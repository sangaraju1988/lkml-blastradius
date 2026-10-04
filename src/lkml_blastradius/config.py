"""``blastradius.yaml``. Every key is optional; secrets always come from environment variables."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_RELEASE_SOURCES: list[dict[str, str]] = [
    {
        "name": "Looker",
        "url": "https://docs.cloud.google.com/feeds/looker-release-notes.xml",
        "format": "atom",
    },
    {
        "name": "Conversational Analytics API",
        "url": "https://docs.cloud.google.com/gemini/data-agents/conversational-analytics-api/release-notes",
        "format": "html",
    },
]

TEMPLATE = """\
# lkml-blastradius configuration. Secrets come from environment variables, never from this file:
#   LOOKER_BASE_URL       e.g. https://yourcompany.cloud.looker.com
#   LOOKER_CLIENT_ID / LOOKER_CLIENT_SECRET   API3 credentials of a user that can see all content
#   SLACK_WEBHOOK_URL     optional: post the daily summary to Slack

looker:
  models: []            # only these LookML models (empty = all)
  workers: 8            # parallel API calls
  timeout_seconds: 120

content:
  dashboards: true
  looks: true
  looker_agents: true       # Conversational Analytics agents in Looker (Looker API /agents, beta)
  content_validator: true   # Looker's content validator: broken references, whatever the cause

ca_api:                     # optional: data agents built with the Conversational Analytics API
  enabled: false
  locations: [global]       # project from GOOGLE_CLOUD_PROJECT, token from GOOGLE_OAUTH_ACCESS_TOKEN

releases:
  enabled: true             # Google release notes for Looker and the CA API
  lookback_days: 7          # on the first run

storage:
  dir: .lkblast             # snapshots (gzipped JSON, metadata only)
  keep: 30                  # snapshots to keep

report:
  dir: reports
"""


class ConfigError(Exception):
    pass


@dataclass
class Config:
    root: Path
    models: list[str] = field(default_factory=list)
    workers: int = 8
    timeout_seconds: float = 120.0
    dashboards: bool = True
    looks: bool = True
    looker_agents: bool = True
    content_validator: bool = True
    ca_api: bool = False
    ca_locations: list[str] = field(default_factory=lambda: ["global"])
    ca_endpoint: str = "https://geminidataanalytics.googleapis.com/v1"
    releases: bool = True
    lookback_days: int = 7
    release_sources: list[dict[str, str]] = field(
        default_factory=lambda: [dict(s) for s in DEFAULT_RELEASE_SOURCES]
    )
    storage_dir: str = ".lkblast"
    keep: int = 30
    report_dir: str = "reports"

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.root / p

    @property
    def snapshot_dir(self) -> Path:
        return self.path(self.storage_dir) / "snapshots"


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    sec = raw.get(name) or {}
    if not isinstance(sec, dict):
        raise ConfigError(f"blastradius.yaml: '{name}' must be a mapping")
    return sec


def load_config(path: Path | None) -> Config:
    """Load blastradius.yaml; a missing file means all defaults (rooted at the current directory)."""
    if path is None:
        path = Path("blastradius.yaml")
        if not path.exists():
            return Config(root=Path.cwd())
    if not path.exists():
        raise ConfigError(f"{path} not found (create one with `lkblast init`)")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping")
    lk, content, ca = _section(raw, "looker"), _section(raw, "content"), _section(raw, "ca_api")
    rel, st, rep = _section(raw, "releases"), _section(raw, "storage"), _section(raw, "report")
    cfg = Config(root=path.resolve().parent)
    cfg.models = [str(m) for m in lk.get("models") or []]
    cfg.workers = int(lk.get("workers", cfg.workers))
    cfg.timeout_seconds = float(lk.get("timeout_seconds", cfg.timeout_seconds))
    cfg.dashboards = bool(content.get("dashboards", True))
    cfg.looks = bool(content.get("looks", True))
    cfg.looker_agents = bool(content.get("looker_agents", True))
    cfg.content_validator = bool(content.get("content_validator", True))
    cfg.ca_api = bool(ca.get("enabled", False))
    cfg.ca_locations = [str(x) for x in ca.get("locations") or ["global"]]
    cfg.ca_endpoint = str(ca.get("endpoint", cfg.ca_endpoint))
    cfg.releases = bool(rel.get("enabled", True))
    cfg.lookback_days = int(rel.get("lookback_days", cfg.lookback_days))
    if rel.get("sources"):
        cfg.release_sources = [{str(k): str(v) for k, v in s.items()} for s in rel["sources"]]
    cfg.storage_dir = str(st.get("dir", cfg.storage_dir))
    cfg.keep = int(st.get("keep", cfg.keep))
    cfg.report_dir = str(rep.get("dir", cfg.report_dir))
    return cfg
