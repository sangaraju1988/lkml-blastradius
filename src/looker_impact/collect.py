"""Build today's snapshot from the Looker API (plus optional CA API agents and release notes)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, TypeVar

from looker_impact.config import Config
from looker_impact.looker import LookerClient, LookerError
from looker_impact.model import (
    ContentItem,
    ExploreDef,
    FieldDef,
    QueryRef,
    Snapshot,
    ValidationError,
)

T = TypeVar("T")
R = TypeVar("R")
Log = Callable[[str], None]

FIELD_REF = re.compile(r"\$\{\s*([A-Za-z0-9_]+\.[A-Za-z0-9_]+)\s*\}")
DOTTED = re.compile(r"\b([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)\b")
FIELD_SETS = ("dimensions", "measures", "filters", "parameters")
KIND = {
    "dimensions": "dimension",
    "measures": "measure",
    "filters": "filter",
    "parameters": "parameter",
}


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _j(v: Any) -> str:
    return "" if v in (None, [], {}) else json.dumps(v, sort_keys=True)


def explore_def(model: str, project: str, data: dict[str, Any]) -> ExploreDef:
    e = ExploreDef(
        model=model,
        name=_s(data.get("name")),
        project=_s(data.get("project_name")) or project,
        label=_s(data.get("label")),
        description=_s(data.get("description")),
        hidden=bool(data.get("hidden")),
        view_name=_s(data.get("view_name")),
        sql_table_name=_s(data.get("sql_table_name")),
        dialect=_s(data.get("dialect_name")),
        always_filter=_j(data.get("always_filter")),
        conditionally_filter=_j(data.get("conditionally_filter")),
        access_filters=_j(data.get("access_filters")),
        errors=sorted(
            _s(x.get("message") if isinstance(x, dict) else x) for x in data.get("errors") or []
        ),
    )
    for j in data.get("joins") or []:
        e.joins[_s(j.get("name"))] = {
            k: _j(j.get(k)) if isinstance(j.get(k), list | dict) else _s(j.get(k))
            for k in (
                "from",
                "sql_on",
                "sql_foreign_key",
                "foreign_key",
                "type",
                "relationship",
                "sql_table_name",
                "required_joins",
                "outer_only",
            )
            if j.get(k) not in (None, "", [], False)
        }
    fieldsets = data.get("fields") or {}
    for fs in FIELD_SETS:
        for f in fieldsets.get(fs) or []:
            name = _s(f.get("name"))
            if not name:
                continue
            e.fields[name] = FieldDef(
                kind=KIND[fs],
                view=_s(f.get("view")),
                type=_s(f.get("type")),
                sql=_s(f.get("sql")),
                label=_s(f.get("label")),
                description=_s(f.get("description")),
                hidden=bool(f.get("hidden")),
                synonyms=sorted(_s(x) for x in f.get("synonyms") or []),
                tags=sorted(_s(x) for x in f.get("tags") or []),
                value_format_name=_s(f.get("value_format_name")),
                filters=_j(f.get("filters")),
                source_file=_s(f.get("source_file")),
            )
    return e


def query_ref(q: dict[str, Any]) -> QueryRef | None:
    """Every field a query touches: fields, pivots, filters, sorts and custom-field references."""
    model, explore = _s(q.get("model")), _s(q.get("view") or q.get("explore"))
    if not model or not explore:
        return None
    used: set[str] = set()
    used.update(_s(f) for f in q.get("fields") or [])
    used.update(_s(f) for f in q.get("pivots") or [])
    filters = q.get("filters") or {}
    if isinstance(filters, dict):
        used.update(_s(k) for k in filters)
    elif isinstance(filters, list):  # CA API golden queries: [{field, value}]
        used.update(_s(f.get("field")) for f in filters if isinstance(f, dict))
    used.update(_s(s).split()[0] for s in q.get("sorts") or [] if _s(s).strip())
    for text in (_s(q.get("filter_expression")), _s(q.get("dynamic_fields"))):
        used.update(FIELD_REF.findall(text))
    try:
        for dyn in json.loads(_s(q.get("dynamic_fields")) or "[]"):
            if isinstance(dyn, dict) and dyn.get("based_on"):
                used.add(_s(dyn["based_on"]))
    except (ValueError, TypeError):
        pass
    return QueryRef(model=model, explore=explore, fields=sorted(f for f in used if "." in f))


def _pmap(fn: Callable[[T], R], items: Iterable[T], workers: int) -> list[R]:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return list(ex.map(fn, items))


class Collector:
    def __init__(self, client: LookerClient, cfg: Config, log: Log) -> None:
        self.c, self.cfg, self.log = client, cfg, log
        self.warnings: list[str] = []
        self._queries: dict[str, QueryRef | None] = {}

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)
        self.log(f"warning: {msg}")

    def _query_by_id(self, qid: str) -> QueryRef | None:
        if qid not in self._queries:
            self._queries[qid] = query_ref(self.c.query(qid))
        return self._queries[qid]

    def _merge(self, merge_id: str) -> list[QueryRef]:
        mq = self.c.merge_query(merge_id)
        refs = [self._query_by_id(_s(s.get("query_id"))) for s in mq.get("source_queries") or []]
        return [r for r in refs if r is not None]

    # --- LookML ----------------------------------------------------------------------------

    def explores(self) -> tuple[dict[str, ExploreDef], dict[str, str]]:
        pairs: list[tuple[str, str, str]] = []
        projects: dict[str, str] = {}
        for m in self.c.models():
            name = _s(m.get("name"))
            if self.cfg.models and name not in self.cfg.models:
                continue
            for e in m.get("explores") or []:
                pairs.append((name, _s(m.get("project_name")), _s(e.get("name"))))

        def one(p: tuple[str, str, str]) -> ExploreDef | None:
            try:
                return explore_def(p[0], p[1], self.c.explore(p[0], p[2]))
            except LookerError as exc:
                self._warn(f"explore {p[0]}::{p[2]}: {exc}")
                return None

        out = {e.key: e for e in _pmap(one, pairs, self.cfg.workers) if e is not None}
        for e in out.values():
            if e.project:
                projects[e.project] = ""
        self.log(f"explores: {len(out)} in {len({e.model for e in out.values()})} model(s)")
        return out, projects

    def deployed_refs(self, projects: dict[str, str]) -> dict[str, str]:
        """Deployed commit of every project, including imported ones that have no model."""
        names = set(projects)
        try:
            names |= set(self.c.projects())
        except LookerError as exc:
            self._warn(f"project list: {exc}")
        out: dict[str, str] = {}
        for p in sorted(names):
            try:
                out[p] = self.c.deployed_ref(p)
            except LookerError as exc:
                self._warn(f"deployed commit of project {p}: {exc}")
                out[p] = ""
        return out

    # --- content ---------------------------------------------------------------------------

    def _dashboard_items(self, did: str) -> list[ContentItem]:
        d = self.c.dashboard(did)
        title = _s(d.get("title")) or f"dashboard {did}"
        url = f"{self.c.base_url}/dashboards/{did}"
        folder = _s((d.get("folder") or {}).get("name"))
        items: list[ContentItem] = []
        for el in d.get("dashboard_elements") or []:
            queries: list[QueryRef] = []
            rm = el.get("result_maker") or {}
            merge_id = _s(el.get("merge_result_id") or rm.get("merge_result_id"))
            q = el.get("query") or rm.get("query") or (el.get("look") or {}).get("query")
            if q:
                ref = query_ref(q)
                queries += [ref] if ref else []
            elif merge_id:
                queries += self._merge(merge_id)
            elif el.get("query_id"):
                ref = self._query_by_id(_s(el["query_id"]))
                queries += [ref] if ref else []
            if not queries:
                continue  # text and button tiles
            items.append(
                ContentItem(
                    kind="dashboard_tile",
                    id=f"{did}/{_s(el.get('id'))}",
                    title=_s(el.get("title") or el.get("title_text")) or "(untitled tile)",
                    parent=title,
                    url=url,
                    folder=folder,
                    queries=queries,
                )
            )
        for f in d.get("dashboard_filters") or []:
            if f.get("model") and f.get("explore") and f.get("dimension"):
                items.append(
                    ContentItem(
                        kind="dashboard_filter",
                        id=f"{did}/filter/{_s(f.get('id') or f.get('name'))}",
                        title=_s(f.get("title") or f.get("name")),
                        parent=title,
                        url=url,
                        folder=folder,
                        queries=[QueryRef(_s(f["model"]), _s(f["explore"]), [_s(f["dimension"])])],
                    )
                )
        return items

    def _look_item(self, lid: str) -> ContentItem | None:
        look = self.c.look(lid)
        q = look.get("query") or (
            self.c.query(_s(look["query_id"])) if look.get("query_id") else None
        )
        ref = query_ref(q) if q else None
        if ref is None:
            return None
        return ContentItem(
            kind="look",
            id=lid,
            title=_s(look.get("title")) or f"look {lid}",
            url=f"{self.c.base_url}/looks/{lid}",
            folder=_s((look.get("folder") or {}).get("name")),
            queries=[ref],
        )

    def _safe(self, what: str, fn: Callable[[str], R]) -> Callable[[str], R | None]:
        def run(x: str) -> R | None:
            try:
                return fn(x)
            except (LookerError, KeyError, TypeError, ValueError) as exc:
                self._warn(f"{what} {x}: {exc}")
                return None

        return run

    def dashboards(self) -> list[ContentItem]:
        ids = self.c.dashboard_ids()
        res = _pmap(self._safe("dashboard", self._dashboard_items), ids, self.cfg.workers)
        items = [i for r in res if r for i in r]
        self.log(f"dashboards: {len(ids)} ({len(items)} tiles and filters with queries)")
        return items

    def looks(self) -> list[ContentItem]:
        ids = self.c.look_ids()
        res = _pmap(self._safe("look", self._look_item), ids, self.cfg.workers)
        items = [r for r in res if r is not None]
        self.log(f"looks: {len(items)}")
        return items

    def looker_agents(self) -> list[ContentItem]:
        try:
            agents = self.c.agents()
        except LookerError as exc:
            self._warn(f"Looker agents (beta API): {exc}")
            return []
        items = []
        for a in agents:
            sources = [
                f"{_s(s.get('model'))}::{_s(s.get('explore'))}" for s in a.get("sources") or []
            ]
            queries = [
                r
                for g in a.get("golden_queries") or []
                if g.get("is_active", True) and (r := query_ref(g)) is not None
            ]
            text = _s((a.get("context") or {}).get("instructions"))
            items.append(
                ContentItem(
                    kind="looker_agent",
                    id=_s(a.get("id")),
                    title=_s(a.get("name")) or f"agent {a.get('id')}",
                    queries=queries,
                    explores=sorted(set(sources)),
                    mentions=sorted(set(DOTTED.findall(text))),
                )
            )
        self.log(f"Looker CA agents: {len(items)}")
        return items

    def validation(self) -> list[ValidationError] | None:
        try:
            data = self.c.content_validation()
        except LookerError as exc:
            self._warn(f"content validator: {exc}")
            return None
        return parse_validation(data, self.c.base_url)


def parse_validation(data: dict[str, Any], base_url: str) -> list[ValidationError]:
    out: list[ValidationError] = []
    for c in data.get("content_with_errors") or []:
        if c.get("dashboard_element"):
            el, d = c["dashboard_element"], c.get("dashboard") or {}
            kind, cid = "dashboard_tile", f"{_s(el.get('dashboard_id'))}/{_s(el.get('id'))}"
            title = f"{_s(d.get('title'))} / {_s(el.get('title') or el.get('title_text'))}"
            url = f"{base_url}/dashboards/{_s(el.get('dashboard_id'))}"
        elif c.get("dashboard_filter"):
            f, d = c["dashboard_filter"], c.get("dashboard") or {}
            kind, cid = "dashboard_filter", f"{_s(f.get('dashboard_id'))}/filter/{_s(f.get('id'))}"
            title = f"{_s(d.get('title'))} / filter {_s(f.get('title') or f.get('name'))}"
            url = f"{base_url}/dashboards/{_s(f.get('dashboard_id'))}"
        elif c.get("look"):
            lk = c["look"]
            kind, cid, title = "look", _s(lk.get("id")), _s(lk.get("title"))
            url = f"{base_url}/looks/{cid}"
        elif c.get("dashboard"):
            d = c["dashboard"]
            kind, cid, title = "dashboard", _s(d.get("id")), _s(d.get("title"))
            url = f"{base_url}/dashboards/{cid}"
        else:
            other = next(
                (k for k in ("scheduled_plan", "alert", "lookml_dashboard") if c.get(k)), ""
            )
            kind, cid, title, url = other or "content", _s(c.get("id")), other, ""
        for e in c.get("errors") or []:
            out.append(
                ValidationError(
                    content_kind=kind,
                    content_id=cid,
                    title=title,
                    message=_s(e.get("message")),
                    url=url,
                    model=_s(e.get("model_name")),
                    explore=_s(e.get("explore_name")),
                    field=_s(e.get("field_name")),
                )
            )
    return sorted(out, key=lambda v: v.key)


def collect(
    client: LookerClient,
    cfg: Config,
    *,
    log: Log = lambda _m: None,
    extra_content: Callable[[Collector], list[ContentItem]] | None = None,
    now: datetime | None = None,
) -> Snapshot:
    col = Collector(client, cfg, log)
    snap = Snapshot(
        taken_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        instance=client.base_url,
    )
    try:
        snap.looker_version = client.version()
    except LookerError as exc:
        col._warn(f"Looker version: {exc}")
    snap.explores, projects = col.explores()
    snap.projects = col.deployed_refs(projects)
    if cfg.dashboards:
        snap.content += col.dashboards()
    if cfg.looks:
        snap.content += col.looks()
    if cfg.looker_agents:
        snap.content += col.looker_agents()
    if extra_content is not None:
        snap.content += extra_content(col)
    if cfg.content_validator:
        log("running the content validator (can take a few minutes on large instances)")
        snap.validation = col.validation()
    snap.content.sort(key=lambda c: (c.kind, c.id))
    snap.warnings = col.warnings
    return snap
