from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lkml_blastradius.check import CheckError, CheckTarget, run_check
from lkml_blastradius.cli import main
from lkml_blastradius.config import Config
from lkml_blastradius.demo import PUSH_SHA, FakeLooker, run_push_demo
from lkml_blastradius.looker import LookerClient, LookerError

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def _check(fake: FakeLooker, sha: str = SHA, **kw: Any) -> Any:
    target = CheckTarget("finance_project", sha, "feature/x", **kw)
    return run_check(Config(root=Path.cwd(), workers=2), fake.client(), target)


def _assert_clean(fake: FakeLooker) -> None:
    assert fake.checkout["finance_project"] == "dev-api-user"
    assert set(fake.branches["finance_project"]) == {"dev-api-user"}  # temp branch deleted
    assert fake.branches["finance_project"]["dev-api-user"] == "4f1c2aa"  # never reset
    assert fake.calls[-1] == "PATCH session production"


def test_push_demo_reports_impact_before_production() -> None:
    report, fake = run_push_demo()
    sev = {f"{i.item.parent}/{i.item.title}".lstrip("/"): i.severity for i in report.impacts}
    assert sev == {
        "Executive revenue/Revenue by region": "breaking",
        "Executive revenue/Region": "breaking",
        "Executive revenue/Net revenue by month": "results",
        "Collections/Amount due vs net revenue": "results",
        "Finance assistant": "results",
        "Executive revenue/Order volume": "cosmetic",
    }
    assert report.mode == "check" and not report.baseline
    assert report.subject == f"finance_project @ feature/net-revenue-discounts ({PUSH_SHA[:7]})"
    assert len(report.new_errors) == 2
    assert all(c.cause.startswith("this push: ") for c in report.changes)
    # production LookML is untouched by the check
    assert fake.refs["finance_project"] == "4f1c2aa"
    temp = next(c.split()[2] for c in fake.calls if c.startswith("POST"))
    assert temp.startswith(f"lkblast-{PUSH_SHA[:12]}-")
    assert fake.calls == [
        "PATCH session dev",
        f"POST finance_project {temp}",
        "PUT finance_project dev-api-user",
        f"DELETE finance_project {temp} (local+remote)",
        "PATCH session production",
    ]
    _assert_clean(fake)


def test_unchanged_commit_has_no_blast_radius() -> None:
    fake = FakeLooker()
    fake.add_commit(SHA, lambda _e: None)
    report = _check(fake)
    assert report.impacts == [] and report.changes == [] and report.worst() is None
    _assert_clean(fake)


def test_falls_back_to_resetting_only_the_temporary_branch() -> None:
    fake = FakeLooker()
    fake.add_commit(SHA, lambda e: e[("finance", "orders")]["joins"].clear())
    fake.reject_create_with_ref = True
    report = _check(fake)
    assert any(c.kind == "join_removed" for c in report.changes)
    resets = [c for c in fake.calls if "force-push" in c]
    assert len(resets) == 1 and resets[0].startswith("PUT finance_project lkblast-")
    _assert_clean(fake)


def test_cleanup_runs_when_the_dev_snapshot_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLooker()
    fake.add_commit(SHA, lambda _e: None)
    client = fake.client()
    real = client.models

    def models() -> list[dict[str, Any]]:
        if client.workspace == "dev":
            raise LookerError("GET /lookml_models: HTTP 500")
        return real()

    monkeypatch.setattr(client, "models", models)
    with pytest.raises(LookerError):
        run_check(Config(root=Path.cwd()), client, CheckTarget("finance_project", SHA))
    _assert_clean(fake)


def test_validator_errors_of_other_projects_are_ignored() -> None:
    fake = FakeLooker()
    noise = {
        "look": {"id": "9", "title": "Carrier list"},
        "errors": [{"message": "x", "model_name": "logistics", "explore_name": "shipments"}],
    }
    fake.add_commit(SHA, lambda _e: None, [noise])  # the API user's stale logistics dev branch
    report = _check(fake)
    assert report.new_errors == []


def test_unknown_project_is_a_clear_error() -> None:
    fake = FakeLooker()
    with pytest.raises(CheckError, match="no explores found for project 'nope'"):
        run_check(Config(root=Path.cwd()), fake.client(), CheckTarget("nope", SHA))


def test_relogin_returns_to_the_dev_workspace() -> None:
    fake = FakeLooker()
    fake.add_commit(SHA, lambda e: e.pop(("finance", "invoices")))
    c = fake.client()
    c.set_workspace("dev")
    c.create_branch("finance_project", "lkblast-t", SHA)
    fake.tokens.clear()  # the access token expires
    models = {m["name"]: m for m in c.models()}
    assert [e["name"] for e in models["finance"]["explores"]] == ["orders"]  # still dev
    assert fake.calls.count("PATCH session dev") == 2


def test_client_refuses_to_reset_or_delete_team_branches() -> None:
    c = LookerClient("https://x.example", "i", "s")
    with pytest.raises(LookerError, match="refusing to reset"):
        c.reset_branch("p", "feature/x", SHA)
    with pytest.raises(LookerError, match="refusing to delete"):
        c.delete_branch("p", "main")


def test_cli_check_needs_a_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    assert main(["check", "--project", "finance_project"]) == 2
