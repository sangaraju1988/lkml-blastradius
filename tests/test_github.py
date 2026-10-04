from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from lkml_blastradius.cli import main
from lkml_blastradius.github import comment
from lkml_blastradius.report import MARKER

ROOT = Path(__file__).resolve().parents[1]


class FakeGitHub:
    def __init__(self) -> None:
        self.comments: dict[int, dict[int, str]] = {7: {1: "unrelated"}}
        self.next_id = 100

    def handler(self, req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/repos/acme/lookml/commits/abc123/pulls":
            return httpx.Response(
                200, json=[{"number": 7, "state": "open"}, {"number": 3, "state": "closed"}]
            )
        if p == "/repos/acme/lookml/issues/7/comments" and req.method == "GET":
            body = [{"id": i, "body": b} for i, b in self.comments[7].items()]
            return httpx.Response(200, json=body)
        if p == "/repos/acme/lookml/issues/7/comments" and req.method == "POST":
            self.next_id += 1
            self.comments[7][self.next_id] = json.loads(req.content)["body"]
            return httpx.Response(201, json={"id": self.next_id})
        if p.startswith("/repos/acme/lookml/issues/comments/") and req.method == "PATCH":
            self.comments[7][int(p.rsplit("/", 1)[1])] = json.loads(req.content)["body"]
            return httpx.Response(200, json={})
        return httpx.Response(404)


def test_comment_is_created_once_then_updated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"ref": "refs/heads/feature/x"}))  # a push event
    for k, v in {
        "GITHUB_TOKEN": "t",
        "GITHUB_REPOSITORY": "acme/lookml",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_SHA": "abc123",
    }.items():
        monkeypatch.setenv(k, v)
    gh = FakeGitHub()
    t = httpx.MockTransport(gh.handler)
    assert comment(f"{MARKER}\n# Blast radius: first", transport=t) == ["#7 created"]
    assert comment(f"{MARKER}\n# Blast radius: second", transport=t) == ["#7 updated"]
    bodies = list(gh.comments[7].values())
    assert bodies == ["unrelated", f"{MARKER}\n# Blast radius: second"]


def test_init_push_writes_a_loop_safe_workflow(tmp_path: Path) -> None:
    assert main(["init", str(tmp_path), "--push", "finance_project"]) == 0
    wf = yaml.safe_load((tmp_path / ".github/workflows/lkml-blastradius.yml").read_text())
    on = wf[True]  # YAML 1.1 reads the `on` key as True
    assert on["push"]["branches-ignore"] == ["lkblast-*"]
    assert wf["concurrency"]["group"] == "lkml-blastradius-finance_project"
    assert wf["permissions"]["pull-requests"] == "write"
    step = wf["jobs"]["blast-radius"]["steps"][0]
    assert step["uses"] == "sangaraju1988/lkml-blastradius@v0"
    assert step["with"]["project"] == "finance_project"
    assert main(["init", str(tmp_path), "--push", "bad name!"]) == 2


def test_action_inputs_match_the_cli() -> None:
    action = yaml.safe_load((ROOT / "action.yml").read_text())
    assert action["runs"]["using"] == "composite"
    assert {
        "project",
        "looker-base-url",
        "looker-client-id",
        "looker-client-secret",
        "fail-on",
        "setup-python",
        "ca-bundle",
    } <= set(action["inputs"])
    script = "\n".join(s.get("run", "") for s in action["runs"]["steps"])
    for flag in ("--project", "--ref", "--label", "--models", "--no-content-validator"):
        assert flag in script
