"""Minimal Looker API 4.0 client.

Every endpoint and attribute used here is checked against Looker's published OpenAPI spec
(looker-open-source/sdk-codegen, spec/Looker.4.0.oas.json, version 4.0.26.12):

* ``POST /login`` (form ``client_id``, ``client_secret``) -> ``access_token``; requests send
  ``Authorization: Bearer <token>`` as the official Python SDK does.
* ``GET /versions`` -> ``looker_release_version``.
* ``GET /projects`` and ``GET /projects/{id}/git_branch`` -> ``ref`` (the deployed commit in the
  production workspace that API sessions use by default).
* ``GET /lookml_models`` -> ``name``, ``project_name``, ``explores[{name}]``.
* ``GET /lookml_models/{model}/explores/{explore}`` -> ``fields{dimensions, measures, filters,
  parameters}[{name, view, type, sql, label, description, hidden, synonyms, tags, ...}]``,
  ``joins[{name, sql_on, type, relationship, ...}]``, ``sql_table_name``, ``always_filter``, ...
* ``GET /dashboards/search``, ``GET /dashboards/{id}`` -> ``dashboard_elements[{query, look,
  result_maker{query, merge_result_id}, merge_result_id}]``, ``dashboard_filters[{model, explore,
  dimension}]``.
* ``GET /looks/search``, ``GET /looks/{id}`` (LookWithQuery) -> ``query``.
* ``GET /merge_queries/{id}`` -> ``source_queries[{query_id}]``; ``GET /queries/{id}``.
* ``GET /agents/search`` (beta) -> ``sources[{model, explore}]``, ``golden_queries[{model, explore,
  fields, filters, sorts, is_active}]``, ``context{instructions}``.
* ``GET /content_validation`` -> ``content_with_errors[{look, dashboard, dashboard_element,
  dashboard_filter, errors[{message, field_name, model_name, explore_name}]}]``. In development
  mode it validates against the dev branch's LookML (Looker docs, "Content Validator").
* Development mode: ``PATCH /session {workspace_id}`` (per API session; a new login starts in
  production), ``GET|POST|PUT /projects/{id}/git_branch``, ``DELETE
  /projects/{id}/git_branch/{name}`` (deletes locally and on the remote). ``PUT`` with ``ref``
  hard-resets and force-pushes, so it is only ever sent for lkblast's own temporary branch.
"""

from __future__ import annotations

import contextlib
import os
import time
from typing import Any
from urllib.parse import quote

import httpx

API = "/api/4.0"


class LookerError(Exception):
    pass


class LookerClient:
    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._id, self._secret = client_id, client_secret
        self._retries = retries
        self._token: str | None = None
        self.workspace = "production"
        self._http = httpx.Client(
            base_url=self.base_url + API, timeout=timeout, transport=transport
        )

    @classmethod
    def from_env(
        cls, *, timeout: float = 120.0, transport: httpx.BaseTransport | None = None
    ) -> LookerClient:
        names = ("LOOKER_BASE_URL", "LOOKER_CLIENT_ID", "LOOKER_CLIENT_SECRET")
        missing = [n for n in names if not os.environ.get(n)]
        if missing:
            raise LookerError(f"set environment variable(s): {', '.join(missing)}")
        return cls(*(os.environ[n] for n in names), timeout=timeout, transport=transport)

    def login(self) -> None:
        resp = self._http.post(
            "/login", data={"client_id": self._id, "client_secret": self._secret}
        )
        if resp.status_code >= 400:
            raise LookerError(f"login failed: HTTP {resp.status_code} (check the API credentials)")
        self._token = str(resp.json()["access_token"])
        if self.workspace != "production":
            # A workspace belongs to one API session; a new token starts in production again.
            self._call("PATCH", "/session", body={"workspace_id": self.workspace})

    def _call(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        if self._token is None:
            self.login()
        relogged = False
        retry_5xx = method != "POST"  # never repeat a create
        attempt = -1
        while attempt < self._retries:
            attempt += 1
            resp = self._http.request(
                method,
                path,
                params=params,
                json=body,
                headers={"Authorization": f"Bearer {self._token}"},
            )
            if resp.status_code == 401 and not relogged:
                relogged = True
                self.login()
                attempt -= 1  # the re-login does not use up a retry
                continue
            retryable = resp.status_code == 429 or (retry_5xx and resp.status_code >= 500)
            if retryable and attempt < self._retries:
                time.sleep(min(2**attempt, 30))
                continue
            if resp.status_code >= 400:
                message = ""
                with contextlib.suppress(ValueError, AttributeError):
                    message = str(resp.json().get("message") or "")
                detail = f": {message}" if message else ""
                raise LookerError(f"{method} {path}: HTTP {resp.status_code}{detail}")
            return resp.json() if resp.content else None
        raise LookerError(f"{method} {path}: retries exhausted")

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._call("GET", path, params)

    def paged(self, path: str, params: dict[str, Any] | None = None, size: int = 500) -> list[Any]:
        out: list[Any] = []
        offset = 0
        while True:
            page = self.get(path, {**(params or {}), "limit": size, "offset": offset})
            if not isinstance(page, list):
                raise LookerError(f"GET {path}: expected a list")
            out += page
            if len(page) < size:
                return out
            offset += size

    # --- endpoints -------------------------------------------------------------------------

    def version(self) -> str:
        return str(self.get("/versions").get("looker_release_version") or "")

    def projects(self) -> list[str]:
        return [str(p["id"]) for p in self.get("/projects", {"fields": "id"})]

    def deployed_ref(self, project: str) -> str:
        return str(self.get(f"/projects/{quote(project, safe='')}/git_branch").get("ref") or "")

    def models(self) -> list[dict[str, Any]]:
        data = self.get("/lookml_models", {"fields": "name,project_name,explores"})
        return list(data)

    def explore(self, model: str, explore: str) -> dict[str, Any]:
        fields = (
            "name,label,description,hidden,project_name,view_name,sql_table_name,dialect_name,"
            "always_filter,conditionally_filter,access_filters,joins,fields,errors"
        )
        return dict(
            self.get(
                f"/lookml_models/{quote(model, safe='')}/explores/{quote(explore, safe='')}",
                {"fields": fields},
            )
        )

    def dashboard_ids(self) -> list[str]:
        rows = self.paged("/dashboards/search", {"fields": "id", "deleted": "false"})
        return [str(r["id"]) for r in rows]

    def dashboard(self, dashboard_id: str) -> dict[str, Any]:
        return dict(self.get(f"/dashboards/{quote(dashboard_id, safe='')}"))

    def look_ids(self) -> list[str]:
        rows = self.paged("/looks/search", {"fields": "id", "deleted": "false"})
        return [str(r["id"]) for r in rows]

    def look(self, look_id: str) -> dict[str, Any]:
        return dict(self.get(f"/looks/{quote(look_id, safe='')}"))

    def query(self, query_id: str) -> dict[str, Any]:
        return dict(self.get(f"/queries/{quote(query_id, safe='')}"))

    def merge_query(self, merge_id: str) -> dict[str, Any]:
        return dict(self.get(f"/merge_queries/{quote(merge_id, safe='')}"))

    def agents(self) -> list[dict[str, Any]]:
        return self.paged("/agents/search", {"deleted": "false"}, size=100)

    def content_validation(self) -> dict[str, Any]:
        return dict(self.get("/content_validation"))

    # --- development mode (lkblast check) ------------------------------------------------------

    def set_workspace(self, workspace: str) -> None:
        """``PATCH /session {workspace_id}``: "dev" reads LookML from the checked-out dev branch."""
        self._call("PATCH", "/session", body={"workspace_id": workspace})
        self.workspace = workspace

    def current_branch(self, project: str) -> dict[str, Any]:
        return dict(self.get(f"/projects/{quote(project, safe='')}/git_branch"))

    def create_branch(self, project: str, name: str, ref: str) -> dict[str, Any]:
        """``POST /projects/{id}/git_branch {name, ref}``: create and check out (dev mode only)."""
        path = f"/projects/{quote(project, safe='')}/git_branch"
        body = {"name": name, "ref": ref} if ref else {"name": name}
        return dict(self._call("POST", path, body=body))

    def checkout_branch(self, project: str, name: str) -> None:
        """``PUT /projects/{id}/git_branch {name}``: checkout only. Never sends ``ref``, which would
        hard-reset and force-push the branch."""
        self._call("PUT", f"/projects/{quote(project, safe='')}/git_branch", body={"name": name})

    def reset_branch(self, project: str, name: str, ref: str) -> None:
        """Hard-reset (and force-push) ``name`` to ``ref``. Only ever called on lkblast's own
        temporary branch."""
        if not name.startswith("lkblast-"):
            raise LookerError(f"refusing to reset branch {name!r}: not an lkblast temporary branch")
        path = f"/projects/{quote(project, safe='')}/git_branch"
        self._call("PUT", path, body={"name": name, "ref": ref})

    def delete_branch(self, project: str, name: str) -> None:
        """``DELETE /projects/{id}/git_branch/{name}``: removes the branch locally and on the remote."""
        if not name.startswith("lkblast-"):
            raise LookerError(
                f"refusing to delete branch {name!r}: not an lkblast temporary branch"
            )
        self._call(
            "DELETE", f"/projects/{quote(project, safe='')}/git_branch/{quote(name, safe='')}"
        )

    def close(self) -> None:
        self._http.close()
