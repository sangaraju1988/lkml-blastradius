"""An offline fake Looker instance (fictional Harborline Supply Co.) for the demo and tests.

Day 1 is the baseline. Before day 2:
* ``core_project`` (imported by finance) deploys a new ``orders.net_revenue`` SQL (also nets out
  discounts) and a new ``orders.gross_revenue`` description;
* ``finance_project`` renames ``customers.region`` to ``customers.sales_region`` and relabels
  ``orders.order_count``;
* Looker is upgraded 26.14.2 -> 26.16.1, and Google publishes (sample) release notes.

The push demo (``lkblast demo --push``) checks the same LookML change as a pushed commit on a
feature branch, through development mode, before it reaches production.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from lkml_blastradius.config import Config
from lkml_blastradius.impact import Report
from lkml_blastradius.looker import LookerClient
from lkml_blastradius.run import run

BASE_URL = "https://harborline.looker.example"
CORE = "imported_projects/core_project/views/orders.view.lkml"


def _f(
    name: str,
    view: str,
    type_: str,
    sql: str = "",
    label: str = "",
    desc: str = "",
    source: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "view": view,
        "type": type_,
        "sql": sql,
        "label": label or name,
        "description": desc,
        "hidden": False,
        "synonyms": [],
        "tags": [],
        "source_file": source,
    }


def day1_explores() -> dict[tuple[str, str], dict[str, Any]]:
    orders_dims = [
        _f("orders.order_id", "orders", "number", "${TABLE}.order_id", source=CORE),
        _f("orders.created_date", "orders", "date_date", "${TABLE}.created_at", source=CORE),
        _f("orders.created_month", "orders", "date_month", "${TABLE}.created_at", source=CORE),
        _f("orders.status", "orders", "string", "${TABLE}.status", source=CORE),
        _f(
            "customers.region",
            "customers",
            "string",
            "${TABLE}.region",
            "Region",
            source="views/customers.view.lkml",
        ),
        _f(
            "customers.segment",
            "customers",
            "string",
            "${TABLE}.segment",
            "Segment",
            source="views/customers.view.lkml",
        ),
    ]
    orders_measures = [
        _f(
            "orders.gross_revenue",
            "orders",
            "sum",
            "${TABLE}.gross_amount",
            "Gross revenue",
            "Revenue before refunds and discounts.",
            CORE,
        ),
        _f(
            "orders.net_revenue",
            "orders",
            "sum",
            "${TABLE}.gross_amount - ${TABLE}.refund_amount",
            "Net revenue",
            "Revenue after refunds.",
            CORE,
        ),
        _f(
            "orders.refund_amount",
            "orders",
            "sum",
            "${TABLE}.refund_amount",
            "Refunds",
            source=CORE,
        ),
        _f("orders.order_count", "orders", "count", "", "Orders", source=CORE),
    ]
    invoices = [
        _f("invoices.invoice_id", "invoices", "number", "${TABLE}.invoice_id"),
        _f("invoices.amount_due", "invoices", "sum", "${TABLE}.amount_due", "Amount due"),
        _f("invoices.days_sales_outstanding", "invoices", "average", "${TABLE}.dso", "DSO"),
    ]
    shipments = [
        _f("shipments.carrier", "shipments", "string", "${TABLE}.carrier", "Carrier"),
        _f(
            "shipments.on_time_rate",
            "shipments",
            "average",
            "${TABLE}.on_time::int",
            "On-time rate",
        ),
    ]

    def ex(
        name: str,
        project: str,
        view: str,
        dims: list[Any],
        measures: list[Any],
        joins: list[Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "label": name.title(),
            "project_name": project,
            "view_name": view,
            "sql_table_name": f"harborline.{view}",
            "dialect_name": "bigquery_standard_sql",
            "joins": joins or [],
            "fields": {"dimensions": dims, "measures": measures, "filters": [], "parameters": []},
            "errors": [],
        }

    join = [
        {
            "name": "customers",
            "type": "left_outer",
            "relationship": "many_to_one",
            "sql_on": "${orders.customer_id} = ${customers.customer_id}",
        }
    ]
    return {
        ("finance", "orders"): ex(
            "orders", "finance_project", "orders", orders_dims, orders_measures, join
        ),
        ("finance", "invoices"): ex(
            "invoices", "finance_project", "invoices", invoices[:1], invoices[1:]
        ),
        ("logistics", "shipments"): ex(
            "shipments", "logistics_project", "shipments", shipments[:1], shipments[1:]
        ),
    }


def _q(
    qid: int,
    model: str,
    explore: str,
    fields: list[str],
    filters: dict[str, str] | None = None,
    sorts: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": str(qid),
        "model": model,
        "view": explore,
        "fields": fields,
        "filters": filters or {},
        "sorts": sorts or [],
        "dynamic_fields": None,
    }


QUERIES = {
    "101": _q(
        101,
        "finance",
        "orders",
        ["orders.created_month", "orders.net_revenue"],
        sorts=["orders.created_month desc"],
    ),
    "102": _q(102, "finance", "orders", ["customers.region", "orders.gross_revenue"]),
    "103": _q(
        103,
        "finance",
        "orders",
        ["orders.status", "orders.order_count"],
        {"orders.status": "-cancelled"},
    ),
    "104": _q(104, "logistics", "shipments", ["shipments.carrier", "shipments.on_time_rate"]),
    "110": _q(110, "finance", "invoices", ["invoices.amount_due"]),
    "111": _q(111, "finance", "orders", ["orders.created_month", "orders.net_revenue"]),
    "201": _q(
        201,
        "finance",
        "orders",
        ["orders.created_date", "orders.refund_amount"],
        {"orders.status": "refunded"},
    ),
    "202": _q(202, "finance", "invoices", ["invoices.invoice_id", "invoices.amount_due"]),
}


def _tile(eid: str, title: str, qid: str) -> dict[str, Any]:
    return {"id": eid, "title": title, "type": "vis", "query": QUERIES[qid], "result_maker": None}


DASHBOARDS = {
    "1": {
        "id": "1",
        "title": "Executive revenue",
        "folder": {"name": "Finance"},
        "dashboard_elements": [
            _tile("11", "Net revenue by month", "101"),
            _tile("12", "Revenue by region", "102"),
            _tile("13", "Order volume", "103"),
            {"id": "14", "type": "text", "title_text": "Notes"},
        ],
        "dashboard_filters": [
            {
                "id": "f1",
                "name": "region",
                "title": "Region",
                "model": "finance",
                "explore": "orders",
                "dimension": "customers.region",
            }
        ],
    },
    "2": {
        "id": "2",
        "title": "Carrier performance",
        "folder": {"name": "Logistics"},
        "dashboard_elements": [_tile("21", "On-time rate by carrier", "104")],
        "dashboard_filters": [],
    },
    "3": {
        "id": "3",
        "title": "Collections",
        "folder": {"name": "Finance"},
        "dashboard_elements": [
            {
                "id": "31",
                "title": "Amount due vs net revenue",
                "type": "vis",
                "query": None,
                "merge_result_id": "m1",
                "result_maker": {"merge_result_id": "m1"},
            }
        ],
        "dashboard_filters": [],
    },
}
LOOKS = {
    "7": {
        "id": "7",
        "title": "Refund watch",
        "folder": {"name": "Finance"},
        "query": QUERIES["201"],
    },
    "8": {
        "id": "8",
        "title": "Open invoices",
        "folder": {"name": "Finance"},
        "query": QUERIES["202"],
    },
}
AGENTS = [
    {
        "id": "a1",
        "name": "Finance assistant",
        "sources": [
            {"model": "finance", "explore": "orders"},
            {"model": "finance", "explore": "invoices"},
        ],
        "golden_queries": [
            {
                "id": 1,
                "questions": ["What was net revenue last month?"],
                "is_active": True,
                "model": "finance",
                "explore": "orders",
                "fields": ["orders.created_month", "orders.net_revenue"],
                "filters": {},
                "sorts": [],
            }
        ],
        "context": {"instructions": "Revenue means orders.net_revenue unless the user says gross."},
    },
    {
        "id": "a2",
        "name": "Logistics helper",
        "sources": [{"model": "logistics", "explore": "shipments"}],
        "golden_queries": [],
        "context": {"instructions": "Answer questions about carriers."},
    },
]

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Looker - Release notes (demo sample)</title>
<entry><title>{day}</title><updated>{day}T00:00:00-07:00</updated>
<link rel="alternate" href="https://docs.cloud.google.com/looker/docs/release-notes"/>
<content type="html"><![CDATA[<h3>Feature</h3><p>(Demo sample) Looker 26.16 lets Conversational
Analytics agents use field synonyms from LookML when choosing fields.</p>
<h3>Deprecated</h3><p>(Demo sample) Legacy dashboard tile settings are deprecated and will no
longer be editable in a future release.</p>]]></content></entry></feed>"""

SAMPLE_CA_PAGE = """<html><body>
<h2 id="{anchor}" data-text="{pretty}" tabindex="-1">{pretty}</h2>
<div class="devsite-release-note"><span class="devsite-label devsite-label-release-changed">Changed</span>
<div><p>(Demo sample) Golden queries now take precedence over glossary terms for data agents
that query BigQuery and Looker.</p></div></div></body></html>"""


REGION_ERRORS: list[dict[str, Any]] = [
    {
        "dashboard": {"id": "1", "title": "Executive revenue"},
        "dashboard_element": {"id": "12", "dashboard_id": "1", "title": "Revenue by region"},
        "errors": [
            {
                "message": 'Unknown field "customers.region"',
                "field_name": "customers.region",
                "model_name": "finance",
                "explore_name": "orders",
            }
        ],
    },
    {
        "dashboard": {"id": "1", "title": "Executive revenue"},
        "dashboard_filter": {"id": "f1", "dashboard_id": "1", "name": "region", "title": "Region"},
        "errors": [
            {
                "message": 'Unknown field "customers.region"',
                "field_name": "customers.region",
                "model_name": "finance",
                "explore_name": "orders",
            }
        ],
    },
]


def day2_lookml(explores: dict[tuple[str, str], dict[str, Any]]) -> None:
    """The LookML change of the demo: new net_revenue SQL, region renamed, a label and a
    description changed (all in finance::orders)."""
    orders = explores[("finance", "orders")]
    dims, measures = orders["fields"]["dimensions"], orders["fields"]["measures"]
    for m in measures:
        if m["name"] == "orders.net_revenue":
            m["sql"] = "${TABLE}.gross_amount - ${TABLE}.refund_amount - ${TABLE}.discount_amount"
            m["description"] = "Revenue after refunds and discounts."
        if m["name"] == "orders.gross_revenue":
            m["description"] = "Revenue before refunds, discounts and tax."
        if m["name"] == "orders.order_count":
            m["label"] = "Order count"
    for d in dims:
        if d["name"] == "customers.region":
            d["name"], d["label"] = "customers.sales_region", "Sales region"


Explores = dict[tuple[str, str], dict[str, Any]]


class FakeLooker:
    """Serves the Looker API endpoints lkml-blastradius uses, plus the release-note pages.

    Development mode is modelled per API session: ``PATCH /session`` selects the workspace of
    the token; in "dev" the explores of a project come from the commit its dev branch points at
    (``commits``), and the content validator returns that commit's errors.
    """

    def __init__(self) -> None:
        self.version = "26.14.2"
        self.refs = {
            "finance_project": "4f1c2aa",
            "core_project": "9b7e310",
            "logistics_project": "c02d551",
        }
        self.explores = day1_explores()
        self.queries = copy.deepcopy(QUERIES)
        self.dashboards = copy.deepcopy(DASHBOARDS)
        self.looks = copy.deepcopy(LOOKS)
        self.agents = copy.deepcopy(AGENTS)
        self.validation: list[dict[str, Any]] = []
        self.release_day = "2000-01-01"
        self.fail: set[str] = set()  # paths that return HTTP 500 (for tests)
        # development mode
        self.tokens: dict[str, str] = {}  # token -> workspace
        self.branches = {p: {"dev-api-user": r} for p, r in self.refs.items()}
        self.checkout = dict.fromkeys(self.refs, "dev-api-user")
        self.commits: dict[str, tuple[Explores, list[dict[str, Any]]]] = {}
        self.reject_create_with_ref = False
        self.calls: list[str] = []  # non-GET calls, for tests

    def apply_day2(self) -> None:
        day2_lookml(self.explores)
        self.refs["core_project"] = "e81d9f4"
        self.refs["finance_project"] = "77aa0b2"
        self.version = "26.16.1"
        self.validation = copy.deepcopy(REGION_ERRORS)

    def add_commit(
        self,
        sha: str,
        change: Callable[[Explores], None],
        validation: list[dict[str, Any]] | None = None,
    ) -> None:
        """A commit pushed to a team's repo: production LookML with ``change`` applied."""
        explores = copy.deepcopy(self.explores)
        change(explores)
        self.commits[sha] = (explores, validation if validation is not None else self.validation)

    # --- views of the instance for one session -----------------------------------------------

    def _dev_refs(self) -> dict[str, str]:
        return {p: self.branches[p][b] for p, b in self.checkout.items()}

    def _view(self, workspace: str) -> tuple[Explores, list[dict[str, Any]]]:
        if workspace != "dev":
            return self.explores, self.validation
        out = dict(self.explores)
        validation = self.validation
        for project, ref in self._dev_refs().items():
            if ref in self.commits:
                explores, validation = self.commits[ref]
                out = {k: v for k, v in out.items() if v["project_name"] != project}
                out.update({k: v for k, v in explores.items() if v["project_name"] == project})
        return out, validation

    def _git(self, req: httpx.Request, ws: str, project: str, name: str | None) -> Any:
        if project not in self.branches:
            return httpx.Response(404)
        if req.method == "GET":
            if ws != "dev":
                return {"name": "production", "ref": self.refs[project], "is_production": True}
            b = self.checkout[project]
            return {"name": b, "ref": self.branches[project][b]}
        if ws != "dev":
            return httpx.Response(400, json={"message": "Only allowed in development mode"})
        body = json.loads(req.content or b"{}")
        self.calls.append(f"{req.method} {project} {name or body.get('name', '')}".strip())
        if req.method == "DELETE":
            if name == self.checkout[project] or name not in self.branches[project]:
                return httpx.Response(400, json={"message": "cannot delete branch"})
            del self.branches[project][name]
            self.calls[-1] += " (local+remote)"
            return httpx.Response(204)
        new, ref = body.get("name"), body.get("ref")
        if req.method == "POST":
            if ref and self.reject_create_with_ref:
                return httpx.Response(422, json={"message": "ref not found locally"})
            known = (
                set(self.commits) | set(self.refs.values()) | set(self.branches[project].values())
            )
            if ref and ref not in known:
                return httpx.Response(404, json={"message": f"unknown ref {ref}"})
            head = self.branches[project][self.checkout[project]]
            self.branches[project][new] = ref or head
            self.checkout[project] = new
        else:  # PUT: checkout, and reset when ref is given
            if new not in self.branches[project]:
                return httpx.Response(404)
            self.checkout[project] = new
            if ref:
                self.branches[project][new] = ref
                self.calls[-1] += f" reset->{ref} (force-push)"
        return {
            "name": self.checkout[project],
            "ref": self.branches[project][self.checkout[project]],
        }

    def _route(self, req: httpx.Request) -> Any:
        path = req.url.path
        if path in self.fail:
            return httpx.Response(500)
        if req.url.host == "docs.cloud.google.com":
            day = self.release_day
            if path.endswith(".xml"):
                return httpx.Response(200, text=SAMPLE_FEED.format(day=day))
            d = datetime.fromisoformat(day)
            pretty = f"{d:%B} {d.day}, {d.year}"
            return httpx.Response(200, text=SAMPLE_CA_PAGE.format(anchor=day, pretty=pretty))
        p = path.removeprefix("/api/4.0")
        if p == "/login":
            token = f"fake-token-{len(self.tokens) + 1}"
            self.tokens[token] = "production"  # every new session starts in production
            return {"access_token": token, "token_type": "Bearer", "expires_in": 3600}
        token = req.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in self.tokens:
            return httpx.Response(401)
        ws = self.tokens[token]
        if p == "/session" and req.method == "PATCH":
            self.tokens[token] = json.loads(req.content)["workspace_id"]
            self.calls.append(f"PATCH session {self.tokens[token]}")
            return {"workspace_id": self.tokens[token]}
        m = re.fullmatch(r"/projects/([^/]+)/git_branch(?:/([^/]+))?", p)
        if m:
            return self._git(req, ws, m.group(1), m.group(2))
        if req.method != "GET":
            return httpx.Response(405)
        explores, validation = self._view(ws)
        offset, limit = (
            int(req.url.params.get("offset", 0)),
            int(req.url.params.get("limit", 10**6)),
        )
        merge = {
            "id": "m1",
            "source_queries": [
                {"query_id": "110", "name": "Invoices"},
                {"query_id": "111", "name": "Orders"},
            ],
        }
        routes: list[tuple[str, Callable[..., Any]]] = [
            (r"/versions", lambda: {"looker_release_version": self.version}),
            (r"/projects", lambda: [{"id": p} for p in self.refs]),
            (r"/lookml_models", lambda: self._models(explores)),
            (r"/lookml_models/([^/]+)/explores/([^/]+)", lambda m, e: explores[(m, e)]),
            (
                r"/dashboards/search",
                lambda: [{"id": k} for k in self.dashboards][offset : offset + limit],
            ),
            (r"/dashboards/([^/]+)", lambda d: self.dashboards[d]),
            (r"/looks/search", lambda: [{"id": k} for k in self.looks][offset : offset + limit]),
            (r"/looks/([^/]+)", lambda lid: self.looks[lid]),
            (r"/queries/([^/]+)", lambda q: self.queries[q]),
            (r"/merge_queries/([^/]+)", lambda _m: merge),
            (r"/agents/search", lambda: self.agents[offset : offset + limit]),
            (r"/content_validation", lambda: {"content_with_errors": validation}),
        ]
        for pattern, fn in routes:
            match = re.fullmatch(pattern, p)
            if match:
                try:
                    return fn(*match.groups())
                except KeyError:
                    return httpx.Response(404)
        return httpx.Response(404)

    @staticmethod
    def _models(explores: Explores) -> list[dict[str, Any]]:
        models: dict[str, dict[str, Any]] = {}
        for (m, e), data in explores.items():
            models.setdefault(m, {"name": m, "project_name": data["project_name"], "explores": []})
            models[m]["explores"].append({"name": e})
        return list(models.values())

    def handler(self, req: httpx.Request) -> httpx.Response:
        out = self._route(req)
        return out if isinstance(out, httpx.Response) else httpx.Response(200, text=json.dumps(out))

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def client(self) -> LookerClient:
        return LookerClient(BASE_URL, "id", "secret", transport=self.transport(), retries=0)


def run_demo(out: Path, log: Callable[[str], None] = lambda _m: None) -> Report:
    cfg = Config(root=out, workers=4)
    for old in cfg.snapshot_dir.glob("*.json.gz") if cfg.snapshot_dir.exists() else []:
        old.unlink()
    fake = FakeLooker()
    fake.release_day = "2026-09-30"
    log("day 1: baseline")
    run(
        cfg,
        fake.client(),
        log=log,
        now=datetime(2026, 10, 1, 6, tzinfo=UTC),
        http_transport=fake.transport(),
    )
    log("day 2: LookML deploys in core_project and finance_project; Looker upgraded to 26.16")
    fake.apply_day2()
    fake.release_day = "2026-10-01"
    report, _ = run(
        cfg,
        fake.client(),
        log=log,
        now=datetime(2026, 10, 2, 6, tzinfo=UTC),
        http_transport=fake.transport(),
    )
    return report


PUSH_SHA = "5d3a9c1e7b2f4a6d8c0e1f2a3b4c5d6e7f8a9b0c"


def run_push_demo(log: Callable[[str], None] = lambda _m: None) -> tuple[Report, FakeLooker]:
    """A team pushes the day-2 LookML change to branch feature/net-revenue-discounts."""
    from lkml_blastradius.check import CheckTarget, run_check

    fake = FakeLooker()
    fake.add_commit(PUSH_SHA, day2_lookml, REGION_ERRORS)
    log("push: finance_project @ feature/net-revenue-discounts")
    target = CheckTarget("finance_project", PUSH_SHA, "feature/net-revenue-discounts")
    report = run_check(Config(root=Path.cwd(), workers=4), fake.client(), target, log=log)
    return report, fake
