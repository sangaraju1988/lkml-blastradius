"""Data agents built with the Conversational Analytics API (optional).

Confirmed in the v1 REST reference: ``GET {endpoint}/projects/{p}/locations/{l}/dataAgents``
(``pageSize``, ``pageToken``) returns ``dataAgents[]``, ``nextPageToken``. A DataAgent carries
``dataAnalyticsAgent.{publishedContext, stagingContext}``; a Context has
``datasourceReferences.looker.exploreReferences[{lookerInstanceUri, lookmlModel, explore}]``,
``lookerGoldenQueries[{naturalLanguageQuestions, lookerQuery{model, explore, fields, filters[{field,
value}], sorts}}]`` and ``systemInstruction``. Proto JSON is camelCase; snake_case is accepted too.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from looker_impact.collect import DOTTED, query_ref
from looker_impact.model import ContentItem


class CAError(Exception):
    pass


def _pick(d: dict[str, Any], camel: str) -> Any:
    snake = "".join(f"_{c.lower()}" if c.isupper() else c for c in camel)
    return d.get(camel, d.get(snake))


def agent_item(agent: dict[str, Any], instance: str = "") -> ContentItem:
    daa = _pick(agent, "dataAnalyticsAgent") or {}
    ctx = _pick(daa, "publishedContext") or _pick(daa, "stagingContext") or {}
    refs = _pick(_pick(ctx, "datasourceReferences") or {}, "looker") or {}
    explores = []
    for r in _pick(refs, "exploreReferences") or []:
        uri = str(_pick(r, "lookerInstanceUri") or "")
        if instance and uri and uri.rstrip("/") != instance.rstrip("/"):
            continue  # an agent on another Looker instance
        explores.append(f"{_pick(r, 'lookmlModel')}::{_pick(r, 'explore')}")
    queries = []
    for g in _pick(ctx, "lookerGoldenQueries") or []:
        ref = query_ref(_pick(g, "lookerQuery") or {})
        if ref is not None:
            queries.append(ref)
    name = str(agent.get("name", ""))
    return ContentItem(
        kind="ca_agent",
        id=name,
        title=str(_pick(agent, "displayName") or name.rsplit("/", 1)[-1]),
        queries=queries,
        explores=sorted(set(explores)),
        mentions=sorted(set(DOTTED.findall(str(_pick(ctx, "systemInstruction") or "")))),
    )


def list_agents(
    endpoint: str,
    project: str,
    location: str,
    token: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    url = f"{endpoint.rstrip('/')}/projects/{project}/locations/{location}/dataAgents"
    out: list[dict[str, Any]] = []
    token_param: str | None = None
    with httpx.Client(
        timeout=60, transport=transport, headers={"Authorization": f"Bearer {token}"}
    ) as http:
        while True:
            params: dict[str, Any] = {"pageSize": 100}
            if token_param:
                params["pageToken"] = token_param
            resp = http.get(url, params=params)
            if resp.status_code >= 400:
                raise CAError(f"list data agents in {location}: HTTP {resp.status_code}")
            data = resp.json()
            out += data.get("dataAgents") or []
            token_param = data.get("nextPageToken")
            if not token_param:
                return out


def ca_agents(
    endpoint: str,
    locations: list[str],
    instance: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[ContentItem]:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN", "")
    if not project or not token:
        raise CAError("ca_api needs GOOGLE_CLOUD_PROJECT and GOOGLE_OAUTH_ACCESS_TOKEN")
    items = []
    for loc in locations:
        for a in list_agents(endpoint, project, loc, token, transport=transport):
            item = agent_item(a, instance)
            if item.explores or item.queries:
                items.append(item)
    return items
