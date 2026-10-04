"""Serve the fake Looker over HTTP so CI can run the GitHub Action end to end.

usage: python scripts/fake_looker_server.py PORT COMMIT_SHA
The pushed commit COMMIT_SHA carries the demo's LookML change (see lkml_blastradius.demo).
"""

from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from lkml_blastradius.demo import REGION_ERRORS, FakeLooker, day2_lookml

fake = FakeLooker()
fake.add_commit(sys.argv[2], day2_lookml, REGION_ERRORS)


class Handler(BaseHTTPRequestHandler):
    def _serve(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        req = httpx.Request(
            self.command,
            f"http://{self.headers['Host']}{self.path}",
            headers=dict(self.headers.items()),
            content=body,
        )
        resp = fake.handler(req)
        self.send_response(resp.status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp.content)))
        self.end_headers()
        self.wfile.write(resp.content)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _serve


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
