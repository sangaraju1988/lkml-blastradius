"""The daily snapshot: everything a change can break, as plain JSON-able dataclasses.

A snapshot holds metadata only (LookML field definitions, content titles and the fields their
queries use). It never holds query results or row data.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = "lkml-blastradius.snapshot.v1"


@dataclass
class FieldDef:
    kind: str  # dimension | measure | filter | parameter
    view: str = ""
    type: str = ""
    sql: str = ""
    label: str = ""
    description: str = ""
    hidden: bool = False
    synonyms: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    value_format_name: str = ""
    filters: str = ""  # measure filters, JSON-encoded
    source_file: str = ""


@dataclass
class ExploreDef:
    model: str
    name: str
    project: str = ""
    label: str = ""
    description: str = ""
    hidden: bool = False
    view_name: str = ""
    sql_table_name: str = ""
    dialect: str = ""
    always_filter: str = ""  # JSON-encoded
    conditionally_filter: str = ""
    access_filters: str = ""
    joins: dict[str, dict[str, str]] = field(default_factory=dict)
    fields: dict[str, FieldDef] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.model}::{self.name}"


@dataclass
class QueryRef:
    model: str
    explore: str
    fields: list[str] = field(default_factory=list)  # every field the query touches

    @property
    def explore_key(self) -> str:
        return f"{self.model}::{self.explore}"


@dataclass
class ContentItem:
    kind: str  # dashboard_tile | dashboard_filter | look | looker_agent | ca_agent
    id: str
    title: str
    parent: str = ""  # dashboard title for tiles and filters
    url: str = ""
    folder: str = ""
    queries: list[QueryRef] = field(default_factory=list)
    explores: list[str] = field(default_factory=list)  # agents: every field of these can be used
    mentions: list[str] = field(default_factory=list)  # view.field tokens in agent instructions

    def explore_keys(self) -> set[str]:
        return {q.explore_key for q in self.queries} | set(self.explores)

    def fields_on(self, explore_key: str) -> set[str]:
        return {f for q in self.queries if q.explore_key == explore_key for f in q.fields}


@dataclass
class ValidationError:
    content_kind: str
    content_id: str
    title: str
    message: str
    url: str = ""
    model: str = ""
    explore: str = ""
    field: str = ""

    @property
    def key(self) -> str:
        return f"{self.content_kind}:{self.content_id}:{self.field}:{self.message}"


@dataclass
class ReleaseNote:
    id: str
    source: str
    date: str  # YYYY-MM-DD
    kind: str  # Feature | Changed | Deprecated | Breaking | Fixed | Issue | Announcement | ...
    text: str
    url: str = ""


@dataclass
class Snapshot:
    taken_at: str
    instance: str
    looker_version: str = ""
    projects: dict[str, str] = field(default_factory=dict)  # project -> deployed commit
    explores: dict[str, ExploreDef] = field(default_factory=dict)
    content: list[ContentItem] = field(default_factory=list)
    validation: list[ValidationError] | None = None  # None: validator not run
    releases: list[ReleaseNote] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema: str = SCHEMA

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, indent=1)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Snapshot:
        explores = {}
        for k, e in d.get("explores", {}).items():
            fields = {n: FieldDef(**f) for n, f in e.pop("fields", {}).items()}
            explores[k] = ExploreDef(**e, fields=fields)
        content = []
        for c in d.get("content", []):
            queries = [QueryRef(**q) for q in c.pop("queries", [])]
            content.append(ContentItem(**c, queries=queries))
        validation = d.get("validation")
        return cls(
            taken_at=d["taken_at"],
            instance=d.get("instance", ""),
            looker_version=d.get("looker_version", ""),
            projects=d.get("projects", {}),
            explores=explores,
            content=content,
            validation=None if validation is None else [ValidationError(**v) for v in validation],
            releases=[ReleaseNote(**r) for r in d.get("releases", [])],
            warnings=d.get("warnings", []),
            schema=d.get("schema", SCHEMA),
        )


def save(snap: Snapshot, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(snap.to_json())
    return path


def load(path: Path) -> Snapshot:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return Snapshot.from_dict(json.load(fh))
