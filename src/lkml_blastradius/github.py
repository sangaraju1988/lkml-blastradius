"""Post the report as one PR comment, updated in place on every push.

Uses the GitHub REST API with the job's ``GITHUB_TOKEN`` (``pull-requests: write``):
``GET /repos/{repo}/commits/{sha}/pulls`` (open PRs containing a pushed commit),
``GET|POST /repos/{repo}/issues/{n}/comments``, ``PATCH /repos/{repo}/issues/comments/{id}``.
``GITHUB_API_URL`` is honored, so GitHub Enterprise Server works too.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from lkml_blastradius.report import MARKER

MAX_BODY = 65_000  # GitHub's limit is 65,536 characters


class GitHubError(Exception):
    pass


def _client(token: str, transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(
        base_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        timeout=30,
        transport=transport,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def pull_requests(http: httpx.Client, repo: str, event: dict[str, Any], sha: str) -> list[int]:
    if event.get("pull_request"):
        return [int(event["pull_request"]["number"])]
    resp = http.get(f"/repos/{repo}/commits/{sha}/pulls")
    if resp.status_code >= 400:
        raise GitHubError(f"list PRs for {sha[:7]}: HTTP {resp.status_code}")
    return [int(p["number"]) for p in resp.json() if p.get("state") == "open"]


def upsert(http: httpx.Client, repo: str, number: int, body: str) -> str:
    page = 1
    while True:
        resp = http.get(
            f"/repos/{repo}/issues/{number}/comments", params={"per_page": 100, "page": page}
        )
        if resp.status_code >= 400:
            raise GitHubError(f"list comments on #{number}: HTTP {resp.status_code}")
        comments = resp.json()
        for c in comments:
            if MARKER in (c.get("body") or ""):
                r = http.patch(f"/repos/{repo}/issues/comments/{c['id']}", json={"body": body})
                if r.status_code >= 400:
                    raise GitHubError(f"update comment on #{number}: HTTP {r.status_code}")
                return "updated"
        if len(comments) < 100:
            break
        page += 1
    r = http.post(f"/repos/{repo}/issues/{number}/comments", json={"body": body})
    if r.status_code >= 400:
        raise GitHubError(f"comment on #{number}: HTTP {r.status_code}")
    return "created"


def comment(markdown: str, *, transport: httpx.BaseTransport | None = None) -> list[str]:
    env = os.environ
    missing = [
        k for k in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_EVENT_PATH") if not env.get(k)
    ]
    if missing:
        raise GitHubError(f"needs {', '.join(missing)} (run inside GitHub Actions)")
    with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
        event = json.load(fh)
    sha = str(
        (event.get("pull_request") or {}).get("head", {}).get("sha") or env.get("GITHUB_SHA", "")
    )
    if len(markdown) > MAX_BODY:
        markdown = (
            markdown[:MAX_BODY] + "\n\n…truncated: see the full report in the job artifact.\n"
        )
    if MARKER not in markdown:
        markdown = f"{MARKER}\n{markdown}"
    done = []
    with _client(env["GITHUB_TOKEN"], transport) as http:
        for n in pull_requests(http, env["GITHUB_REPOSITORY"], event, sha):
            done.append(f"#{n} {upsert(http, env['GITHUB_REPOSITORY'], n, markdown)}")
    return done
