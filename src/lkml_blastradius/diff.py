"""What changed in the semantic layer between two snapshots, with old and new values.

Every change gets a category that says what it can do to content:

* ``breaking`` — an explore or field disappeared: queries that use it fail.
* ``results``  — the numbers can change: SQL, type, measure filters, joins, table, always filters.
* ``ai_context`` — what an AI agent sees changed: description, label, synonyms, tags, hidden,
  new fields. Dashboards and Looks are unaffected; Conversational Analytics may answer differently.
* ``cosmetic`` — display only: labels and formats on dashboards and Looks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lkml_blastradius.model import ExploreDef, Snapshot

SEVERITY = {"breaking": 3, "results": 2, "ai_context": 1, "cosmetic": 0}

FIELD_PARAMS: dict[str, str] = {
    "sql": "results",
    "type": "results",
    "filters": "results",
    "kind": "results",
    "view": "results",
    "description": "ai_context",
    "synonyms": "ai_context",
    "tags": "ai_context",
    "hidden": "ai_context",
    "label": "cosmetic",
    "value_format_name": "cosmetic",
}
EXPLORE_PARAMS: dict[str, str] = {
    "sql_table_name": "results",
    "view_name": "results",
    "always_filter": "results",
    "conditionally_filter": "results",
    "access_filters": "results",
    "description": "ai_context",
    "hidden": "ai_context",
    "label": "cosmetic",
}
# `source_file` and `project` are tracked for attribution, not as changes.


@dataclass
class ParamChange:
    param: str
    old: str
    new: str


@dataclass
class Change:
    kind: str  # explore_added|explore_removed|explore_changed|field_*|join_*
    category: str
    model: str
    explore: str
    name: str = ""  # field or join name
    params: list[ParamChange] = field(default_factory=list)
    project: str = ""
    source_file: str = ""
    cause: str = ""  # filled by impact.attribute

    @property
    def explore_key(self) -> str:
        return f"{self.model}::{self.explore}"

    def describe(self) -> str:
        what = {
            "explore_added": "explore added",
            "explore_removed": "explore removed",
            "explore_changed": "explore changed",
            "field_added": "field added",
            "field_removed": "field removed",
            "field_changed": "changed",
            "join_added": "join added",
            "join_removed": "join removed",
            "join_changed": "join changed",
        }[self.kind]
        target = self.name or self.explore_key
        params = f" ({', '.join(p.param for p in self.params)})" if self.params else ""
        return f"{target} {what}{params}"


def _val(v: object) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


def _params(old: object, new: object, rules: dict[str, str]) -> tuple[list[ParamChange], str]:
    changes, top = [], ""
    for p, cat in rules.items():
        a, b = getattr(old, p), getattr(new, p)
        if a != b:
            changes.append(ParamChange(p, _val(a), _val(b)))
            if not top or SEVERITY[cat] > SEVERITY[top]:
                top = cat
    return changes, top


def _field_changes(model: str, a: ExploreDef, b: ExploreDef) -> list[Change]:
    out: list[Change] = []

    def add(
        kind: str,
        cat: str,
        name: str,
        params: list[ParamChange] | None = None,
        source_file: str = "",
    ) -> None:
        out.append(Change(kind, cat, model, b.name, name, params or [], b.project, source_file))

    for name in sorted(set(a.fields) | set(b.fields)):
        fa, fb = a.fields.get(name), b.fields.get(name)
        if fa is None and fb is not None:
            add("field_added", "ai_context", name, source_file=fb.source_file)
        elif fb is None and fa is not None:
            add("field_removed", "breaking", name, source_file=fa.source_file)
        elif fa is not None and fb is not None:
            params, cat = _params(fa, fb, FIELD_PARAMS)
            if params:
                add("field_changed", cat, name, params, fb.source_file)
    for name in sorted(set(a.joins) | set(b.joins)):
        ja, jb = a.joins.get(name), b.joins.get(name)
        if ja == jb:
            continue
        if ja is None:
            add("join_added", "ai_context", name)
        elif jb is None:
            add("join_removed", "breaking", name)
        else:
            keys = sorted(set(ja) | set(jb))
            params = [
                ParamChange(k, ja.get(k, ""), jb.get(k, "")) for k in keys if ja.get(k) != jb.get(k)
            ]
            add("join_changed", "results", name, params)
    return out


def diff_explores(old: Snapshot, new: Snapshot) -> list[Change]:
    out: list[Change] = []
    for key in sorted(set(old.explores) | set(new.explores)):
        a, b = old.explores.get(key), new.explores.get(key)
        if a is None and b is not None:
            out.append(Change("explore_added", "ai_context", b.model, b.name, project=b.project))
        elif b is None and a is not None:
            out.append(Change("explore_removed", "breaking", a.model, a.name, project=a.project))
        elif a is not None and b is not None:
            params, cat = _params(a, b, EXPLORE_PARAMS)
            if params:
                out.append(
                    Change(
                        "explore_changed", cat, b.model, b.name, params=params, project=b.project
                    )
                )
            out += _field_changes(b.model, a, b)
    return out
