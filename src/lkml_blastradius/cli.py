"""``lkblast``: init once, then ``lkblast run`` every day."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import httpx

from lkml_blastradius import __version__
from lkml_blastradius.check import CheckError
from lkml_blastradius.config import TEMPLATE, ConfigError, load_config
from lkml_blastradius.diff import SEVERITY
from lkml_blastradius.impact import Report
from lkml_blastradius.looker import LookerError
from lkml_blastradius.report import headline, slack_payload, write_reports

WORKFLOW = Path(__file__).with_name("workflow.yml")
PUSH_WORKFLOW = Path(__file__).with_name("push-workflow.yml")


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _finish(report: Report, out_dir: Path, fail_on: str, slack: bool) -> int:
    for p in write_reports(report, out_dir):
        print(f"wrote {p}")
    for line in headline(report):
        print(f"  {line}")
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if slack and webhook:
        from lkml_blastradius.run import post_slack, report_link

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
    if a.push:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", a.push):
            _log(f"lkblast init: {a.push!r} is not a LookML project name")
            return 2
        text = PUSH_WORKFLOW.read_text(encoding="utf-8").replace("__PROJECT__", a.push)
        targets = {root / ".github/workflows/lkml-blastradius.yml": text}
        hint = (
            "\nnext:\n"
            "  1. secrets LOOKER_BASE_URL, LOOKER_CLIENT_ID, LOOKER_CLIENT_SECRET (org or repo level)\n"
            "  2. if GitHub's runners cannot reach Looker: set variable LKBLAST_RUNS_ON to the label\n"
            "     of a self-hosted runner inside your network\n"
            "  3. commit and push: every push now gets a blast-radius report (and a PR comment)"
        )
    else:
        targets = {
            root / "blastradius.yaml": TEMPLATE,
            root / ".github/workflows/lkml-blastradius.yml": WORKFLOW.read_text(encoding="utf-8"),
        }
        hint = (
            "\nnext:\n"
            "  1. add repo secrets LOOKER_BASE_URL, LOOKER_CLIENT_ID, LOOKER_CLIENT_SECRET, "
            "SLACK_WEBHOOK_URL\n"
            "  2. commit and push; the workflow runs daily (or now: Actions -> lkml-blastradius -> Run)\n"
            "  locally: export the same variables and run `lkblast run`"
        )
    for path, text in targets.items():
        if path.exists() and not a.force:
            print(f"kept {path} (exists; --force to overwrite)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    print(hint)
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    from lkml_blastradius.looker import LookerClient
    from lkml_blastradius.run import run

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


def cmd_check(a: argparse.Namespace) -> int:
    from lkml_blastradius.check import CheckTarget, run_check
    from lkml_blastradius.looker import LookerClient

    ref = a.ref or os.environ.get("GITHUB_SHA", "")
    if not ref:
        _log("lkblast check: give --ref (a commit SHA or branch) or run inside GitHub Actions")
        return 2
    label = a.label or os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME", "")
    cfg = load_config(Path(a.config) if a.config else None)
    if a.no_content_validator:
        cfg.content_validator = False
    target = CheckTarget(
        project=a.project,
        ref=ref,
        label=label,
        extra_models=[m.strip() for m in (a.models or "").split(",") if m.strip()],
    )
    client = LookerClient.from_env(timeout=cfg.timeout_seconds)
    try:
        report = run_check(cfg, client, target, log=_log)
    finally:
        client.close()
    return _finish(report, Path(a.out), a.fail_on, not a.no_slack)


def cmd_comment(a: argparse.Namespace) -> int:
    from lkml_blastradius.github import GitHubError, comment

    try:
        done = comment(Path(a.file).read_text(encoding="utf-8"))
    except (GitHubError, httpx.HTTPError, OSError) as exc:
        _log(f"lkblast comment: {exc}")
        return 1
    print("\n".join(done) if done else "no open pull request for this commit")
    return 0


def cmd_compare(a: argparse.Namespace) -> int:
    from lkml_blastradius.run import compare

    report = compare(Path(a.old), Path(a.new))
    return _finish(report, Path(a.out), a.fail_on, slack=False)


def cmd_demo(a: argparse.Namespace) -> int:
    from lkml_blastradius.demo import run_demo, run_push_demo

    if a.push:
        report, _ = run_push_demo(log=_log)
    else:
        report = run_demo(Path(a.out), log=_log)
    return _finish(report, Path(a.out) / "reports", "never", slack=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="lkblast",
        description="Daily Blast radius report: what changed (LookML deploys, Looker upgrades, "
        "Google release notes) and which dashboards, Looks, explores and CA agents it affects.",
    )
    ap.add_argument("--version", action="version", version=f"lkblast {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser(
        "init", help="write blastradius.yaml and a daily workflow (or, with --push, a per-push one)"
    )
    p.add_argument("dir", nargs="?", default=".")
    p.add_argument(
        "--push", metavar="PROJECT", help="write the on-every-push workflow for this LookML project"
    )
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    fail = {
        "choices": ["breaking", "results", "ai_context", "cosmetic", "never"],
        "default": "never",
        "help": "exit 1 when impact reaches this severity (default: never)",
    }
    p = sub.add_parser("run", help="snapshot Looker, compare with the last run, write the report")
    p.add_argument(
        "-c", "--config", help="blastradius.yaml (default: ./blastradius.yaml if present)"
    )
    p.add_argument("-o", "--out", help="report directory (default: report.dir)")
    p.add_argument("--no-slack", action="store_true", help="do not post to SLACK_WEBHOOK_URL")
    p.add_argument("--fail-on", **fail)  # type: ignore[arg-type]
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser(
        "check", help="blast radius of a pushed commit vs production (uses Looker dev mode)"
    )
    p.add_argument("--project", required=True, help="LookML project name in Looker")
    p.add_argument("--ref", help="commit SHA or branch to check (default: $GITHUB_SHA)")
    p.add_argument("--label", help="branch name shown in the report (default: from GitHub)")
    p.add_argument(
        "--models", help="extra models to check, comma-separated (projects imported by others)"
    )
    p.add_argument("-c", "--config", help="blastradius.yaml (optional)")
    p.add_argument("-o", "--out", default="reports", help="report directory (default: reports)")
    p.add_argument("--no-content-validator", action="store_true", help="skip the content validator")
    p.add_argument("--no-slack", action="store_true", help="do not post to SLACK_WEBHOOK_URL")
    p.add_argument("--fail-on", **fail)  # type: ignore[arg-type]
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("comment", help="post a report as the PR comment (inside GitHub Actions)")
    p.add_argument("file", help="blastradius.md")
    p.set_defaults(fn=cmd_comment)

    p = sub.add_parser("compare", help="report the difference between two saved snapshots")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("-o", "--out", default="reports")
    p.add_argument("--fail-on", **fail)  # type: ignore[arg-type]
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("demo", help="offline demo against a fake Looker instance")
    p.add_argument("-o", "--out", default="lkblast-demo")
    p.add_argument("--push", action="store_true", help="demo `lkblast check` on a pushed commit")
    p.set_defaults(fn=cmd_demo)

    a = ap.parse_args(argv)
    try:
        return int(a.fn(a))
    except (ConfigError, LookerError, CheckError) as exc:
        _log(f"lkblast: {exc}")
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
