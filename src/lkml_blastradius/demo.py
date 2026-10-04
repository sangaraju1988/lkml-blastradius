"""An offline fake Looker instance (fictional Harborline Supply Co.) for the demo and tests.

Day 1 is the baseline. Before day 2:
* ``core_project`` (imported by finance) deploys a new ``orders.net_revenue`` SQL (also nets out
  discounts) and a new ``orders.gross_revenue`` description;
* ``finance_project`` renames ``customers.region`` to ``customers.sales_region`` and relabels
  ``orders.order_count``;
* Looker is upgraded 26.14.2 -> 26.16.1, and Google publishes (sample) release notes.
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


class FakeLooker:
    """Serves the Looker API endpoints lkml-blastradius uses, plus the release-note pages."""

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

    def apply_day2(self) -> None:
        orders = self.explores[("finance", "orders")]
        dims, measures = orders["fields"]["dimensions"], orders["fields"]["measures"]
        for m in measures:
            if m["name"] == "orders.net_revenue":
                m["sql"] = (
                    "${TABLE}.gross_amount - ${TABLE}.refund_amount - ${TABLE}.discount_amount"
                )
                m["description"] = "Revenue after refunds and discounts."
            if m["name"] == "orders.gross_revenue":
                m["description"] = "Revenue before refunds, discounts and tax."
            if m["name"] == "orders.order_count":
                m["label"] = "Order count"
        for d in dims:
            if d["name"] == "customers.region":
                d["name"], d["label"] = "customers.sales_region", "Sales region"
        self.refs["core_project"] = "e81d9f4"
        self.refs["finance_project"] = "77aa0b2"
        self.version = "26.16.1"
        self.validation = [
            {
                "dashboard": {"id": "1", "title": "Executive revenue"},
                "dashboard_element": {
                    "id": "12",
                    "dashboard_id": "1",
                    "title": "Revenue by region",
                },
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
                "dashboard_filter": {
                    "id": "f1",
                    "dashboard_id": "1",
                    "name": "region",
                    "title": "Region",
                },
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
            return {"access_token": "fake-token", "token_type": "Bearer", "expires_in": 3600}
        if req.headers.get("Authorization") != "Bearer fake-token":
            return httpx.Response(401)
        offset, limit = (
            int(req.url.params.get("offset", 0)),
            int(req.url.params.get("limit", 10**6)),
        )
        routes: list[tuple[str, Callable[..., Any]]] = [
            (r"/versions", lambda: {"looker_release_version": self.version}),
            (r"/projects", lambda: [{"id": p} for p in self.refs]),
            (
                r"/projects/([^/]+)/git_branch",
                lambda p: {"name": "production", "ref": self.refs[p]},
            ),
            (r"/lookml_models", self._models),
            (r"/lookml_models/([^/]+)/explores/([^/]+)", lambda m, e: self.explores[(m, e)]),
            (
                r"/dashboards/search",
                lambda: [{"id": k} for k in self.dashboards][offset : offset + limit],
            ),
            (r"/dashboards/([^/]+)", lambda d: self.dashboards[d]),
            (r"/looks/search", lambda: [{"id": k} for k in self.looks][offset : offset + limit]),
            (r"/looks/([^/]+)", lambda lid: self.looks[lid]),
            (r"/queries/([^/]+)", lambda q: self.queries[q]),
            (
                r"/merge_queries/([^/]+)",
                lambda _m: {
                    "id": "m1",
                    "source_queries": [
                        {"query_id": "110", "name": "Invoices"},
                        {"query_id": "111", "name": "Orders"},
                    ],
                },
            ),
            (r"/agents/search", lambda: self.agents[offset : offset + limit]),
            (r"/content_validation", lambda: {"content_with_errors": self.validation}),
        ]
        for pattern, fn in routes:
            m = re.fullmatch(pattern, p)
            if m:
                try:
                    return fn(*m.groups())
                except KeyError:
                    return httpx.Response(404)
        return httpx.Response(404)

    def _models(self) -> list[dict[str, Any]]:
        models: dict[str, dict[str, Any]] = {}
        for (m, e), data in self.explores.items():
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
