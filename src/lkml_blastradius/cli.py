"""``lkblast``: init once, then ``lkblast run`` every day."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

from lkml_blastradius import __version__
from lkml_blastradius.config import TEMPLATE, ConfigError, load_config
from lkml_blastradius.diff import SEVERITY
from lkml_blastradius.impact import Report
from lkml_blastradius.looker import LookerError
from lkml_blastradius.report import headline, slack_payload, write_reports

WORKFLOW = Path(__file__).with_name("workflow.yml")


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _finish(report: Report, out_dir: Path, fail_on: str, slack: bool) -> int:
    for p in write_reports(report, out_dir):
        print(f"wrote {p}")
    for line in headline(report):
        print(f"  {line}")
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if slack and webhook:
        from lkml_blastradius.run import post_slack, report_link

        try:
            post_slack(webhook, slack_payload(report, report_link()))
            print("posted the summary to Slack")
        except httpx.HTTPError as exc:
            _log(f"warning: Slack post failed: {exc}")
    worst = report.worst()
    if fail_on != "never" and worst and SEVERITY[worst] >= SEVERITY[fail_on]:
        _log(f"failing: impact at or above '{fail_on}' ({worst})")
        return 1
    return 0


def cmd_init(a: argparse.Namespace) -> int:
    root = Path(a.dir)
    targets = {
        root / "blastradius.yaml": TEMPLATE,
        root / ".github/workflows/lkml-blastradius.yml": WORKFLOW.read_text(encoding="utf-8"),
    }
    for path, text in targets.items():
        if path.exists() and not a.force:
            print(f"kept {path} (exists; --force to overwrite)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    print(
        "\nnext:\n"
        "  1. add repo secrets LOOKER_BASE_URL, LOOKER_CLIENT_ID, LOOKER_CLIENT_SECRET, SLACK_WEBHOOK_URL\n"
        "  2. commit and push; the workflow runs daily (or now: Actions -> lkml-blastradius -> Run)\n"
        "  locally: export the same variables and run `lkblast run`"
    )
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    from lkml_blastradius.looker import LookerClient
    from lkml_blastradius.run import run

    cfg = load_config(Path(a.config) if a.config else None)
    client = LookerClient.from_env(timeout=cfg.timeout_seconds)
    try:
        report, snap = run(cfg, client, log=_log)
    finally:
        client.close()
    print(f"saved snapshot {snap}")
    return _finish(
        report, Path(a.out) if a.out else cfg.path(cfg.report_dir), a.fail_on, not a.no_slack
    )


def cmd_compare(a: argparse.Namespace) -> int:
    from lkml_blastradius.run import compare

    report = compare(Path(a.old), Path(a.new))
    return _finish(report, Path(a.out), a.fail_on, slack=False)


def cmd_demo(a: argparse.Namespace) -> int:
    from lkml_blastradius.demo import run_demo

    report = run_demo(Path(a.out), log=_log)
    return _finish(report, Path(a.out) / "reports", "never", slack=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="lkblast",
        description="Daily Blast radius report: what changed (LookML deploys, Looker upgrades, "
        "Google release notes) and which dashboards, Looks, explores and CA agents it affects.",
    )
    ap.add_argument("--version", action="version", version=f"lkblast {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="write blastradius.yaml and a daily GitHub Actions workflow")
    p.add_argument("dir", nargs="?", default=".")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    fail = {
        "choices": ["breaking", "results", "ai_context", "cosmetic", "never"],
        "default": "never",
        "help": "exit 1 when impact reaches this severity (default: never)",
    }
    p = sub.add_parser("run", help="snapshot Looker, compare with the last run, write the report")
    p.add_argument(
        "-c", "--config", help="blastradius.yaml (default: ./blastradius.yaml if present)"
    )
    p.add_argument("-o", "--out", help="report directory (default: report.dir)")
    p.add_argument("--no-slack", action="store_true", help="do not post to SLACK_WEBHOOK_URL")
    p.add_argument("--fail-on", **fail)  # type: ignore[arg-type]
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("compare", help="report the difference between two saved snapshots")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("-o", "--out", default="reports")
    p.add_argument("--fail-on", **fail)  # type: ignore[arg-type]
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("demo", help="offline demo against a fake Looker instance (two days)")
    p.add_argument("-o", "--out", default="lkblast-demo")
    p.set_defaults(fn=cmd_demo)

    a = ap.parse_args(argv)
    try:
        return int(a.fn(a))
    except (ConfigError, LookerError) as exc:
        _log(f"lkblast: {exc}")
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
