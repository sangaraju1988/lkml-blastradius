from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from looker_impact.cli import main
from looker_impact.config import Config, load_config
from looker_impact.demo import FakeLooker, run_demo
from looker_impact.impact import Report
from looker_impact.model import load
from looker_impact.run import prune, run, snapshot_files

DAY1 = datetime(2026, 10, 1, 6, tzinfo=UTC)
DAY2 = datetime(2026, 10, 2, 6, tzinfo=UTC)


def _two_days(tmp_path: Path, change: object = None) -> tuple[Report, FakeLooker]:
    cfg = Config(root=tmp_path, workers=2)
    fake = FakeLooker()
    run(cfg, fake.client(), now=DAY1, http_transport=fake.transport())
    if change is None:
        fake.apply_day2()
    else:
        change(fake)  # type: ignore[operator]
    report, _ = run(cfg, fake.client(), now=DAY2, http_transport=fake.transport())
    return report, fake


def _sev(report: Report) -> dict[str, str]:
    return {f"{i.item.parent}/{i.item.title}".lstrip("/"): i.severity for i in report.impacts}


def test_demo_attributes_every_impact(tmp_path: Path) -> None:
    report = run_demo(tmp_path)
    assert _sev(report) == {
        "Executive revenue/Revenue by region": "breaking",
        "Executive revenue/Region": "breaking",
        "Executive revenue/Net revenue by month": "results",
        "Collections/Amount due vs net revenue": "results",  # merged query
        "Finance assistant": "results",  # golden query uses net_revenue
        "Executive revenue/Order volume": "cosmetic",
    }
    # Refund watch (Look) and Logistics helper (agent) use nothing that changed.
    assert report.worst() == "breaking"
    assert report.upgraded and report.version == ("26.14.2", "26.16.1")
    assert set(report.deploys) == {"core_project", "finance_project"}
    by_name = {c.name: c for c in report.changes}
    # net_revenue lives in the imported core_project: attributed there, not to finance_project
    assert by_name["orders.net_revenue"].cause.startswith("LookML deploy in core_project")
    assert by_name["customers.region"].cause.startswith("LookML deploy in finance_project")
    assert [v.field for v in report.new_errors] == ["customers.region", "customers.region"]
    assert any(r.attention and r.note.kind == "Deprecated" for r in report.releases)
    agent = next(i for i in report.impacts if i.item.title == "Finance assistant")
    assert agent.reasons[0].startswith("orders.net_revenue: sql")  # worst reason first


def test_first_run_is_a_baseline(tmp_path: Path) -> None:
    fake = FakeLooker()
    report, path = run(
        Config(root=tmp_path), fake.client(), now=DAY1, http_transport=fake.transport()
    )
    assert report.baseline and not report.impacts and not report.changes
    snap = load(path)
    assert len(snap.explores) == 3 and snap.projects["core_project"] == "9b7e310"
    kinds = {c.kind for c in snap.content}
    assert kinds == {"dashboard_tile", "dashboard_filter", "look", "looker_agent"}


def test_upgrade_without_deploy_is_attributed_to_looker(tmp_path: Path) -> None:
    def change(fake: FakeLooker) -> None:
        fake.version = "26.16.1"
        orders = fake.explores[("logistics", "shipments")]
        orders["fields"]["measures"][0]["type"] = "sum"  # behaviour change shipped by Google

    report, _ = _two_days(tmp_path, change)
    assert _sev(report) == {
        "Carrier performance/On-time rate by carrier": "results",
        "Logistics helper": "results",
    }
    assert (
        report.changes[0].cause
        == "Looker upgrade 26.14.2 → 26.16.1 (no LookML deploy in logistics_project)"
    )


def test_join_change_hits_only_content_using_the_joined_view(tmp_path: Path) -> None:
    def change(fake: FakeLooker) -> None:
        fake.explores[("finance", "orders")]["joins"][0]["relationship"] = "one_to_many"

    report, _ = _two_days(tmp_path, change)
    sev = _sev(report)
    assert sev["Executive revenue/Revenue by region"] == "results"
    assert sev["Executive revenue/Region"] == "results"
    assert "Executive revenue/Net revenue by month" not in sev  # uses no customers.* field
    assert sev["Finance assistant"] == "results"  # agents can use any field of the explore


def test_explore_removed_breaks_all_its_content(tmp_path: Path) -> None:
    def change(fake: FakeLooker) -> None:
        del fake.explores[("finance", "invoices")]

    report, _ = _two_days(tmp_path, change)
    sev = _sev(report)
    assert sev["Open invoices"] == "breaking"
    assert sev["Collections/Amount due vs net revenue"] == "breaking"
    assert sev["Finance assistant"] == "breaking"


def test_description_change_only_affects_agents(tmp_path: Path) -> None:
    def change(fake: FakeLooker) -> None:
        fake.explores[("finance", "orders")]["fields"]["measures"][2]["description"] = (
            "Refunds issued."
        )

    report, _ = _two_days(tmp_path, change)
    assert _sev(report) == {"Finance assistant": "ai_context"}


def test_failed_endpoints_become_warnings(tmp_path: Path) -> None:
    fake = FakeLooker()
    fake.fail = {
        "/api/4.0/lookml_models/logistics/explores/shipments",
        "/api/4.0/content_validation",
    }
    report, path = run(
        Config(root=tmp_path), fake.client(), now=DAY1, http_transport=fake.transport()
    )
    assert any("logistics::shipments" in w for w in report.warnings)
    assert any("content validator" in w for w in report.warnings)
    assert load(path).validation is None
    fake.fail = set()
    fake.apply_day2()
    report, _ = run(Config(root=tmp_path), fake.client(), now=DAY2, http_transport=fake.transport())
    assert report.open_errors == 2 and report.new_errors == []  # no baseline to compare with


def test_snapshots_are_pruned(tmp_path: Path) -> None:
    d = tmp_path / "snaps"
    d.mkdir()
    for i in range(5):
        (d / f"2026100{i}T000000Z.json.gz").write_bytes(b"")
    prune(d, keep=3)
    assert [p.name[:9] for p in snapshot_files(d)] == ["20261002T", "20261003T", "20261004T"]


def test_cli_init_and_compare(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init", str(tmp_path)]) == 0
    assert (tmp_path / ".github/workflows/looker-impact.yml").exists()
    cfg = load_config(tmp_path / "impact.yaml")
    assert cfg.content_validator and not cfg.ca_api and cfg.root == tmp_path.resolve()

    run_demo(tmp_path / "demo")
    old, new = snapshot_files(tmp_path / "demo/.lkimpact/snapshots")
    out = tmp_path / "cmp"
    assert main(["compare", str(old), str(new), "-o", str(out)]) == 0
    assert main(["compare", str(old), str(new), "-o", str(out), "--fail-on", "results"]) == 1
    data = json.loads((out / "impact.json").read_text())
    assert data["worst"] == "breaking"
    html = (out / "impact.html").read_text()
    assert "Revenue by region" in html and "<script>" in html
    assert "2 breaking" in capsys.readouterr().out


def test_cli_run_needs_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for k in ("LOOKER_BASE_URL", "LOOKER_CLIENT_ID", "LOOKER_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert main(["run"]) == 2


def test_slack_post(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from looker_impact import cli
    from looker_impact import run as run_mod

    sent: list[dict[str, str]] = []

    def fake_post(webhook: str, payload: dict[str, str], **_: object) -> None:
        sent.append(payload)

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/looker")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setattr(run_mod, "post_slack", fake_post)
    report = run_demo(tmp_path)
    assert cli._finish(report, tmp_path / "r", "never", slack=True) == 0
    text = sent[0]["text"]
    assert ":red_circle:" in text and "Revenue by region" in text
    assert "https://github.com/acme/looker/actions/runs/42" in text


def test_client_relogs_in_on_401() -> None:
    calls: list[str] = []
    tokens = iter(["t1", "t2"])

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.path.endswith("/login"):
            return httpx.Response(200, json={"access_token": next(tokens)})
        if req.headers["Authorization"] == "Bearer t1":
            return httpx.Response(401)
        return httpx.Response(200, json={"looker_release_version": "26.16.1"})

    from looker_impact.looker import LookerClient

    c = LookerClient("https://x.example", "i", "s", transport=httpx.MockTransport(handler))
    assert c.version() == "26.16.1"
    assert calls == ["/api/4.0/login", "/api/4.0/versions", "/api/4.0/login", "/api/4.0/versions"]
