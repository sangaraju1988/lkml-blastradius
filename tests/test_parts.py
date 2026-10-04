from __future__ import annotations

import json
from datetime import date

import httpx

from lkml_blastradius.ca_api import agent_item, list_agents
from lkml_blastradius.collect import parse_validation, query_ref
from lkml_blastradius.releases import new_since, parse_atom, parse_devsite_html


def test_query_ref_collects_every_field_reference() -> None:
    q = {
        "model": "finance",
        "view": "orders",
        "fields": ["orders.status", "orders.total", "calc_margin"],
        "pivots": ["orders.channel"],
        "filters": {"customers.region": "West"},
        "sorts": ["orders.total desc 0"],
        "filter_expression": "${orders.discount} > 0",
        "dynamic_fields": json.dumps(
            [
                {
                    "table_calculation": "calc_margin",
                    "expression": "${orders.total} - ${orders.cost}",
                },
                {
                    "measure": "unique_customers",
                    "based_on": "customers.customer_id",
                    "type": "count_distinct",
                },
            ]
        ),
    }
    ref = query_ref(q)
    assert ref is not None and ref.explore_key == "finance::orders"
    assert ref.fields == sorted(
        [
            "orders.status",
            "orders.total",
            "orders.channel",
            "customers.region",
            "orders.discount",
            "orders.cost",
            "customers.customer_id",
        ]
    )
    assert query_ref({"fields": ["a.b"]}) is None


def test_ca_api_agent_item_reads_published_context_and_filters_instance() -> None:
    agent = {
        "name": "projects/p/locations/global/dataAgents/fin",
        "displayName": "Finance (CA API)",
        "dataAnalyticsAgent": {
            "publishedContext": {
                "systemInstruction": "Use orders.net_revenue for revenue.",
                "datasourceReferences": {
                    "looker": {
                        "exploreReferences": [
                            {
                                "lookerInstanceUri": "https://harborline.looker.example",
                                "lookmlModel": "finance",
                                "explore": "orders",
                            },
                            {
                                "lookerInstanceUri": "https://other.looker.example",
                                "lookmlModel": "x",
                                "explore": "y",
                            },
                        ]
                    }
                },
                "lookerGoldenQueries": [
                    {
                        "naturalLanguageQuestions": ["net revenue?"],
                        "lookerQuery": {
                            "model": "finance",
                            "explore": "orders",
                            "fields": ["orders.net_revenue"],
                            "filters": [{"field": "orders.status", "value": "complete"}],
                        },
                    }
                ],
            }
        },
    }
    item = agent_item(agent, "https://harborline.looker.example/")
    assert item.kind == "ca_agent" and item.title == "Finance (CA API)"
    assert item.explores == ["finance::orders"]
    assert item.queries[0].fields == ["orders.net_revenue", "orders.status"]
    assert item.mentions == ["orders.net_revenue"]
    snake = agent_item(
        {
            "name": "a",
            "data_analytics_agent": {
                "staging_context": {
                    "datasource_references": {
                        "looker": {"explore_references": [{"lookml_model": "m", "explore": "e"}]}
                    }
                }
            },
        }
    )
    assert snake.explores == ["m::e"]


def test_ca_api_list_follows_pages() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/projects/p/locations/global/dataAgents"
        if req.url.params.get("pageToken") == "n2":
            return httpx.Response(200, json={"dataAgents": [{"name": "b"}]})
        return httpx.Response(200, json={"dataAgents": [{"name": "a"}], "nextPageToken": "n2"})

    got = list_agents(
        "https://ca.example/v1", "p", "global", "tok", transport=httpx.MockTransport(handler)
    )
    assert [a["name"] for a in got] == ["a", "b"]


def test_parse_validation_kinds() -> None:
    data = {
        "content_with_errors": [
            {
                "look": {"id": "7", "title": "Refund watch"},
                "errors": [
                    {
                        "message": "Unknown field",
                        "field_name": "orders.x",
                        "model_name": "finance",
                        "explore_name": "orders",
                    }
                ],
            },
            {"scheduled_plan": {"id": "5"}, "id": "e9", "errors": [{"message": "bad"}]},
        ]
    }
    errs = parse_validation(data, "https://l.example")
    assert [(e.content_kind, e.url) for e in errs] == [
        ("look", "https://l.example/looks/7"),
        ("scheduled_plan", ""),
    ]


ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>September 30, 2026</title><updated>2026-09-30T00:00:00-07:00</updated>
<link rel="alternate" href="https://docs.example/rn#September_30_2026"/>
<content type="html"><![CDATA[<strong>Looker (original) only changes</strong>
<h3>Feature</h3><p>One.</p><p>Two &amp; more.</p><h3>Breaking</h3><p>Three.</p>]]></content>
</entry></feed>"""

DEVSITE = """<h2 id="september-3-2026" data-text="September 3, 2026" tabindex="-1">September 3, 2026</h2>
<p><b>Conversational Analytics API updates:</b></p>
<div id="x" class="devsite-release-note">
<span class="devsite-label devsite-label-release-feature">Feature</span>
<div><p>Agent observability.</p></div></div>
<h2 id="june-23-2026" data-text="June 23, 2026" tabindex="-1">June 23, 2026</h2>
<div class="devsite-release-note"><span class="devsite-label devsite-label-release-changed">Changed</span>
<div><p>Sources are cited.</p></div></div>"""


def test_release_parsers_and_new_since() -> None:
    a = parse_atom(ATOM, "Looker")
    assert [(n.date, n.kind, n.text) for n in a] == [
        ("2026-09-30", "Feature", "One."),
        ("2026-09-30", "Feature", "Two & more."),
        ("2026-09-30", "Breaking", "Three."),
    ]
    h = parse_devsite_html(DEVSITE, "CA API", "https://docs.example/ca")
    assert [(n.date, n.kind, n.url) for n in h] == [
        ("2026-09-03", "Feature", "https://docs.example/ca#september-3-2026"),
        ("2026-06-23", "Changed", "https://docs.example/ca#june-23-2026"),
    ]
    fresh = new_since(a + h, seen={a[0].id}, since=None, lookback_start=date(2026, 9, 1))
    assert [n.text for n in fresh] == ["Two & more.", "Three.", "Agent observability."]
