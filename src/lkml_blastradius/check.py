"""``lkblast check``: the blast radius of one pushed commit, before it reaches production.

1. Production session: the project's explores, the content that uses them, content validator.
2. Development session: a temporary branch ``lkblast-<sha>`` is created at the pushed commit in
   the API user's dev workspace; the same explores are read as Looker compiles them there, and the
   content validator runs against that LookML.
3. Always (also on failure): the API user's original dev branch is checked out again, the
   temporary branch is deleted (locally and on the remote) and the session returns to production.

The team's own branches are never checked out or reset. Runs for the same project must not
overlap (one API user has one dev checkout per project): the GitHub Action serializes them.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from lkml_blastradius.collect import Collector
from lkml_blastradius.config import Config
from lkml_blastradius.impact import Report, build_report
from lkml_blastradius.looker import LookerClient, LookerError
from lkml_blastradius.model import Snapshot, ValidationError

Log = Callable[[str], None]


class CheckError(Exception):
    pass


@dataclass
class CheckTarget:
    project: str
    ref: str  # commit SHA (or branch/tag) to check
    label: str = ""  # branch name for the report
    extra_models: list[str] = field(default_factory=list)

    @property
    def subject(self) -> str:
        short = self.ref[:7] if len(self.ref) >= 7 else self.ref
        return (
            f"{self.project} @ {self.label} ({short})"
            if self.label
            else f"{self.project} @ {short}"
        )


def _only(errors: list[ValidationError] | None, models: set[str]) -> list[ValidationError] | None:
    """Validator errors on this project's models (dev mode also uses the API user's dev branches
    of other projects, which must not show up as noise)."""
    if errors is None:
        return None
    return [e for e in errors if not e.model or e.model in models]


def _temp_branch(ref: str) -> str:
    return f"lkblast-{ref[:12].replace('/', '-')}-{secrets.token_hex(3)}"


def _dev_snapshot(
    client: LookerClient,
    col: Collector,
    cfg: Config,
    target: CheckTarget,
    log: Log,
) -> tuple[Snapshot, str]:
    client.set_workspace("dev")
    original = str(client.current_branch(target.project).get("name") or "")
    temp = _temp_branch(target.ref)
    created = False
    try:
        try:
            info = client.create_branch(target.project, temp, target.ref)
            created = True
        except LookerError as exc:
            # Some setups only resolve remote commits on reset: create at HEAD, then reset the
            # temporary branch (and only it) to the commit.
            log(f"create at {target.ref[:12]} failed ({exc}); creating at HEAD and resetting")
            info = client.create_branch(target.project, temp, "")
            created = True
            client.reset_branch(target.project, temp, target.ref)
            info = client.current_branch(target.project)
        log(f"dev mode: {target.project} on temporary branch {temp} at {str(info.get('ref'))[:12]}")
        snap = Snapshot(
            taken_at=datetime.now(UTC).isoformat(timespec="seconds"), instance=client.base_url
        )
        snap.explores, _ = col.explores(target.project, target.extra_models)
        if cfg.content_validator:
            log("content validator on the pushed LookML")
            snap.validation = col.validation()
        return snap, str(info.get("ref") or target.ref)
    finally:
        try:
            if original and original != temp:
                client.checkout_branch(target.project, original)
            if created:
                client.delete_branch(target.project, temp)
        except LookerError as exc:
            log(f"warning: cleanup of {temp} failed: {exc} (delete it in Looker's Git panel)")
        finally:
            client.set_workspace("production")


def run_check(
    cfg: Config, client: LookerClient, target: CheckTarget, *, log: Log = lambda _m: None
) -> Report:
    col = Collector(client, cfg, log)
    prod = Snapshot(
        taken_at=datetime.now(UTC).isoformat(timespec="seconds"), instance=client.base_url
    )
    try:
        prod.looker_version = client.version()
    except LookerError as exc:
        col._warn(f"Looker version: {exc}")
    prod.explores, _ = col.explores(target.project, target.extra_models)
    if not prod.explores and not target.extra_models:
        raise CheckError(
            f"no explores found for project {target.project!r} in production. Check the project "
            "name; for a project without models (imported by others) pass the importing models "
            "with --models."
        )
    try:
        prod_ref = client.deployed_ref(target.project)
    except LookerError as exc:
        col._warn(f"deployed commit of {target.project}: {exc}")
        prod_ref = ""
    models = {e.model for e in prod.explores.values()} | set(target.extra_models)
    if cfg.dashboards:
        prod.content += col.dashboards()
    if cfg.looks:
        prod.content += col.looks()
    if cfg.looker_agents:
        prod.content += col.looker_agents()
    prod.content = [
        c for c in prod.content if any(k.split("::", 1)[0] in models for k in c.explore_keys())
    ]
    if cfg.content_validator:
        log("content validator on production")
        prod.validation = _only(col.validation(), models)

    head, head_ref = _dev_snapshot(client, col, cfg, target, log)
    head.looker_version = prod.looker_version
    head.content = prod.content  # content lives in production; only the LookML differs
    head.validation = _only(head.validation, models)
    head.warnings = col.warnings
    prod.projects = {target.project: prod_ref}
    head.projects = {target.project: head_ref}

    report = build_report(prod, head, cause=f"this push: {target.subject}")
    report.mode = "check"
    report.subject = target.subject
    report.deploys = {}
    return report
