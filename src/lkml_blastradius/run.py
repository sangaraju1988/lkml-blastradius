"""One daily run: snapshot -> compare with the previous snapshot -> report -> keep history."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from lkml_blastradius.ca_api import CAError, ca_agents
from lkml_blastradius.collect import Collector, collect
from lkml_blastradius.config import Config
from lkml_blastradius.impact import Report, build_report
from lkml_blastradius.looker import LookerClient
from lkml_blastradius.model import ContentItem, Snapshot, load, save
from lkml_blastradius.releases import fetch_release_notes

Log = Callable[[str], None]
KEEP_NOTES_DAYS = 120


def snapshot_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.json.gz")) if directory.exists() else []


def snapshot_name(taken_at: str) -> str:
    ts = datetime.fromisoformat(taken_at).astimezone(UTC)
    return ts.strftime("%Y%m%dT%H%M%SZ") + ".json.gz"


def prune(directory: Path, keep: int) -> None:
    for p in snapshot_files(directory)[: -max(keep, 2)]:
        p.unlink()


def run(
    cfg: Config,
    client: LookerClient,
    *,
    log: Log = lambda _m: None,
    now: datetime | None = None,
    http_transport: httpx.BaseTransport | None = None,
) -> tuple[Report, Path]:
    now = now or datetime.now(UTC)
    files = snapshot_files(cfg.snapshot_dir)
    base = load(files[-1]) if files else None
    log(f"previous snapshot: {files[-1].name}" if files else "no previous snapshot: baseline run")

    extra: Callable[[Collector], list[ContentItem]] | None = None
    if cfg.ca_api:

        def extra(col: Collector) -> list[ContentItem]:
            try:
                items = ca_agents(
                    cfg.ca_endpoint, cfg.ca_locations, client.base_url, transport=http_transport
                )
            except (CAError, httpx.HTTPError) as exc:
                col._warn(f"CA API agents: {exc}")
                return []
            log(f"CA API agents: {len(items)}")
            return items

    snap = collect(client, cfg, log=log, extra_content=extra, now=now)
    if cfg.releases:
        notes, warnings = fetch_release_notes(cfg.release_sources, transport=http_transport)
        cutoff = (now - timedelta(days=KEEP_NOTES_DAYS)).date().isoformat()
        snap.releases = [n for n in notes if n.date >= cutoff]
        snap.warnings += warnings
        log(f"release notes: {len(notes)} read")
    report = build_report(base, snap, cfg.lookback_days)
    path = save(snap, cfg.snapshot_dir / snapshot_name(snap.taken_at))
    prune(cfg.snapshot_dir, cfg.keep)
    return report, path


def compare(old: Path, new: Path, lookback_days: int = 7) -> Report:
    a: Snapshot = load(old)
    return build_report(a, load(new), lookback_days)


def report_link() -> str:
    if os.environ.get("LKBLAST_REPORT_URL"):
        return os.environ["LKBLAST_REPORT_URL"]
    env = os.environ
    if env.get("GITHUB_RUN_ID") and env.get("GITHUB_REPOSITORY"):
        server = env.get("GITHUB_SERVER_URL", "https://github.com")
        return f"{server}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"
    return ""


def post_slack(
    webhook: str, payload: dict[str, object], *, transport: httpx.BaseTransport | None = None
) -> None:
    with httpx.Client(timeout=30, transport=transport) as http:
        resp = http.post(webhook, json=payload)
        resp.raise_for_status()
