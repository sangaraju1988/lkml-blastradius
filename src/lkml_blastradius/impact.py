"""Join today's changes to the content that depends on them, and say where each change came from."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from lkml_blastradius.diff import SEVERITY, Change, diff_explores
from lkml_blastradius.model import ContentItem, ReleaseNote, Snapshot, ValidationError
from lkml_blastradius.releases import areas, needs_attention, new_since

AGENT_KINDS = {"looker_agent", "ca_agent"}
DIALECTS = {
    "bigquery_standard_sql": "BigQuery",
    "bigquery": "BigQuery",
    "snowflake": "Snowflake",
    "redshift": "Redshift",
    "postgres": "PostgreSQL",
    "databricks": "Databricks",
    "mysql": "MySQL",
    "spanner": "Spanner",
    "alloydb_postgres": "AlloyDB",
    "trino": "Trino",
    "presto": "Presto",
    "athena": "Athena",
    "sqlserver": "SQL Server",
    "oracle": "Oracle",
}


@dataclass
class Impact:
    item: ContentItem
    severity: str
    reasons: list[str] = field(default_factory=list)
    causes: list[str] = field(default_factory=list)


@dataclass
class Release:
    note: ReleaseNote
    attention: bool
    areas: list[str]


@dataclass
class Report:
    instance: str
    base_at: str | None
    head_at: str
    version: tuple[str, str]
    deploys: dict[str, tuple[str, str]] = field(default_factory=dict)
    changes: list[Change] = field(default_factory=list)
    impacts: list[Impact] = field(default_factory=list)
    new_errors: list[ValidationError] = field(default_factory=list)
    resolved_errors: list[ValidationError] = field(default_factory=list)
    open_errors: int = 0
    validator_ran: bool = False
    releases: list[Release] = field(default_factory=list)
    content_counts: dict[str, int] = field(default_factory=dict)
    explore_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def baseline(self) -> bool:
        return self.base_at is None

    @property
    def upgraded(self) -> bool:
        return bool(self.version[0] and self.version[1] and self.version[0] != self.version[1])

    def by_severity(self) -> dict[str, list[Impact]]:
        out: dict[str, list[Impact]] = {k: [] for k in SEVERITY}
        for i in self.impacts:
            out[i.severity].append(i)
        return out

    def worst(self) -> str | None:
        if self.new_errors:
            return "breaking"
        if not self.impacts:
            return None
        return max((i.severity for i in self.impacts), key=SEVERITY.__getitem__)


def _owning_project(c: Change) -> str:
    # Fields defined in an imported project report a source file under imported_projects/<name>/.
    parts = c.source_file.split("/")
    if len(parts) > 2 and parts[0] == "imported_projects":
        return parts[1]
    return c.project


def attribute(c: Change, deploys: dict[str, tuple[str, str]], version: tuple[str, str]) -> str:
    project = _owning_project(c)
    if project in deploys:
        a, b = deploys[project]
        return f"LookML deploy in {project} ({a[:7] or '?'} → {b[:7] or '?'})"
    if version[0] and version[1] and version[0] != version[1]:
        return f"Looker upgrade {version[0]} → {version[1]} (no LookML deploy in {project})"
    return f"no LookML deploy or Looker upgrade seen for {project or 'this project'}"


def _short(c: Change) -> str:
    if not c.params:
        return c.describe()
    p = c.params[0]
    more = f" (+{len(c.params) - 1} more)" if len(c.params) > 1 else ""
    old, new = (p.old or "∅")[:80], (p.new or "∅")[:80]
    return f"{c.name or c.explore_key}: {p.param} `{old}` → `{new}`{more}"


def _relevant(item: ContentItem, key: str, c: Change, join_fields: set[str]) -> str | None:
    """The category this change has *for this item*, or None if it does not touch it."""
    agent = item.kind in AGENT_KINDS
    used = item.fields_on(key) | (set(item.mentions) if agent else set())
    whole_explore = agent and key in item.explores
    if c.kind.startswith("explore_"):
        cat = c.category
    elif c.kind.startswith("field_"):
        if c.name in used:
            cat = c.category
        elif whole_explore:
            cat = "ai_context" if c.category in ("breaking", "cosmetic") else c.category
        else:
            return None
    else:  # join_*
        if used & join_fields:
            cat = c.category
        elif whole_explore:
            cat = "ai_context" if c.category == "breaking" else c.category
        else:
            return None
    if agent and cat == "cosmetic":
        return "ai_context"  # labels are part of what an agent reads
    if not agent and cat == "ai_context":
        return None  # dashboards and Looks don't read descriptions or synonyms
    return cat


def impacts_for(base: Snapshot, head: Snapshot, changes: list[Change]) -> list[Impact]:
    by_explore: dict[str, list[Change]] = {}
    for c in changes:
        by_explore.setdefault(c.explore_key, []).append(c)
    join_fields: dict[tuple[str, str], set[str]] = {}
    for c in changes:
        if c.kind.startswith("join_"):
            fields = set()
            for snap in (base, head):
                e = snap.explores.get(c.explore_key)
                if e:
                    fields |= {n for n, f in e.fields.items() if f.view == c.name}
            join_fields[(c.explore_key, c.name)] = fields
    out: list[Impact] = []
    for item in head.content:
        hits: list[tuple[str, Change]] = []
        for key in sorted(item.explore_keys()):
            for c in by_explore.get(key, []):
                cat = _relevant(item, key, c, join_fields.get((key, c.name), set()))
                if cat is not None:
                    hits.append((cat, c))
        if not hits:
            continue
        hits.sort(key=lambda h: -SEVERITY[h[0]])  # stable: the worst reasons come first
        why: list[str] = []
        for _, c in hits:
            if c.cause and c.cause not in why:
                why.append(c.cause)
        out.append(Impact(item, hits[0][0], [_short(c) for _, c in hits], why))
    out.sort(key=lambda i: (-SEVERITY[i.severity], i.item.kind, i.item.parent, i.item.title))
    return out


def releases_for(
    base: Snapshot | None, head: Snapshot, notes: list[ReleaseNote], lookback_days: int
) -> list[Release]:
    seen = {n.id for n in base.releases} if base else set()
    since = datetime.fromisoformat(base.taken_at).date() if base else None
    lookback = datetime.fromisoformat(head.taken_at).date() - timedelta(days=lookback_days)
    dialects = {
        DIALECTS.get(e.dialect, e.dialect.split("_")[0].title())
        for e in head.explores.values()
        if e.dialect
    }
    out = []
    for n in new_since(notes, seen, since, lookback):
        a = areas(n, dialects)
        if _mentions_version(n, head.looker_version):
            a.insert(0, f"Looker {head.looker_version}")
        out.append(Release(n, needs_attention(n), a))
    return out


def _mentions_version(n: ReleaseNote, version: str) -> bool:
    short = ".".join(version.split(".")[:2])  # 26.16.12 -> 26.16
    return bool(version) and f"Looker {short}" in n.text


def build_report(base: Snapshot | None, head: Snapshot, lookback_days: int = 7) -> Report:
    r = Report(
        instance=head.instance,
        base_at=base.taken_at if base else None,
        head_at=head.taken_at,
        version=(base.looker_version if base else "", head.looker_version),
        explore_count=len(head.explores),
        warnings=list(head.warnings),
    )
    for item in head.content:
        r.content_counts[item.kind] = r.content_counts.get(item.kind, 0) + 1
    if head.validation is not None:
        r.validator_ran = True
        r.open_errors = len(head.validation)
        if base is not None and base.validation is not None:  # both runs validated: compare
            before = {v.key for v in base.validation}
            after = {v.key for v in head.validation}
            r.new_errors = [v for v in head.validation if v.key not in before]
            r.resolved_errors = [v for v in base.validation if v.key not in after]
    r.releases = releases_for(base, head, head.releases, lookback_days)
    if base is None:
        return r
    for p in sorted(set(base.projects) | set(head.projects)):
        a, b = base.projects.get(p, ""), head.projects.get(p, "")
        if a != b and (a or b):
            r.deploys[p] = (a, b)
    r.changes = diff_explores(base, head)
    for c in r.changes:
        c.cause = attribute(c, r.deploys, r.version)
    r.impacts = impacts_for(base, head, r.changes)
    return r
