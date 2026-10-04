"""``lkimpact``: init once, then ``lkimpact run`` every day."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

from looker_impact import __version__
from looker_impact.config import TEMPLATE, ConfigError, load_config
from looker_impact.diff import SEVERITY
from looker_impact.impact import Report
from looker_impact.looker import LookerError
from looker_impact.report import headline, slack_payload, write_reports

WORKFLOW = Path(__file__).with_name("workflow.yml")


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _finish(report: Report, out_dir: Path, fail_on: str, slack: bool) -> int:
    for p in write_reports(report, out_dir):
        print(f"wrote {p}")
    for line in headline(report):
        print(f"  {line}")
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if slack and webhook:
        from looker_impact.run import post_slack, report_link

        try:
            post_slack(webhook, slack_payload(report, report_link()))
            print("posted the summary to Slack")
        except httpx.HTTPError as exc:
            _log(f"warning: Slack post failed: {exc}")
    worst = report.worst()
    if fail_on != "never" and worst and SEVERITY[worst] >= SEVERITY[fail_on]:
        _log(f"failing: impact at or above '{fail_on}' ({worst})")
        return 1
    return 0


def cmd_init(a: argparse.Namespace) -> int:
    root = Path(a.dir)
    targets = {
        root / "impact.yaml": TEMPLATE,
        root / ".github/workflows/looker-impact.yml": WORKFLOW.read_text(encoding="utf-8"),
    }
    for path, text in targets.items():
        if path.exists() and not a.force:
            print(f"kept {path} (exists; --force to overwrite)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    print(
        "\nnext:\n"
        "  1. add repo secrets LOOKER_BASE_URL, LOOKER_CLIENT_ID, LOOKER_CLIENT_SECRET, SLACK_WEBHOOK_URL\n"
        "  2. commit and push; the workflow runs daily (or now: Actions -> looker-impact -> Run)\n"
        "  locally: export the same variables and run `lkimpact run`"
    )
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    from looker_impact.looker import LookerClient
    from looker_impact.run import run

    cfg = load_config(Path(a.config) if a.config else None)
    client = LookerClient.from_env(timeout=cfg.timeout_seconds)
    try:
        report, snap = run(cfg, client, log=_log)
    finally:
        client.close()
    print(f"saved snapshot {snap}")
    return _finish(
        report, Path(a.out) if a.out else cfg.path(cfg.report_dir), a.fail_on, not a.no_slack
    )


def cmd_compare(a: argparse.Namespace) -> int:
    from looker_impact.run import compare

    report = compare(Path(a.old), Path(a.new))
    return _finish(report, Path(a.out), a.fail_on, slack=False)


def cmd_demo(a: argparse.Namespace) -> int:
    from looker_impact.demo import run_demo

    report = run_demo(Path(a.out), log=_log)
    return _finish(report, Path(a.out) / "reports", "never", slack=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="lkimpact",
        description="Daily Looker impact report: what changed (LookML deploys, Looker upgrades, "
        "Google release notes) and which dashboards, Looks, explores and CA agents it affects.",
    )
    ap.add_argument("--version", action="version", version=f"lkimpact {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="write impact.yaml and a daily GitHub Actions workflow")
    p.add_argument("dir", nargs="?", default=".")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    fail = {
        "choices": ["breaking", "results", "ai_context", "cosmetic", "never"],
        "default": "never",
        "help": "exit 1 when impact reaches this severity (default: never)",
    }
    p = sub.add_parser("run", help="snapshot Looker, compare with the last run, write the report")
    p.add_argument("-c", "--config", help="impact.yaml (default: ./impact.yaml if present)")
    p.add_argument("-o", "--out", help="report directory (default: report.dir)")
    p.add_argument("--no-slack", action="store_true", help="do not post to SLACK_WEBHOOK_URL")
    p.add_argument("--fail-on", **fail)  # type: ignore[arg-type]
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("compare", help="report the difference between two saved snapshots")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("-o", "--out", default="reports")
    p.add_argument("--fail-on", **fail)  # type: ignore[arg-type]
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("demo", help="offline demo against a fake Looker instance (two days)")
    p.add_argument("-o", "--out", default="lkimpact-demo")
    p.set_defaults(fn=cmd_demo)

    a = ap.parse_args(argv)
    try:
        return int(a.fn(a))
    except (ConfigError, LookerError) as exc:
        _log(f"lkimpact: {exc}")
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
