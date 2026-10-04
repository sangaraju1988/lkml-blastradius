"""Render a Report as Markdown (job summary), self-contained HTML, JSON and a Slack message."""

from __future__ import annotations

import html
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from looker_impact import __version__
from looker_impact.diff import Change
from looker_impact.impact import Impact, Report

TITLES = {
    "breaking": "Breaking",
    "results": "Numbers may change",
    "ai_context": "AI agents may answer differently",
    "cosmetic": "Cosmetic",
}
KINDS = {
    "dashboard_tile": "dashboard tile",
    "dashboard_filter": "dashboard filter",
    "look": "Look",
    "looker_agent": "Looker CA agent",
    "ca_agent": "CA API agent",
    "dashboard": "dashboard",
}
MD_ROWS = 100


def _day(ts: str | None) -> str:
    return (ts or "")[:16].replace("T", " ")


def _name(i: Impact) -> str:
    return f"{i.item.parent} / {i.item.title}" if i.item.parent else i.item.title


def headline(r: Report) -> list[str]:
    bits: list[str] = []
    sev = r.by_severity()
    counts = [f"{len(v)} {TITLES[k].lower()}" for k, v in sev.items() if v]
    if r.baseline:
        bits.append("baseline saved (first run: changes are reported from the next run on)")
    else:
        bits.append(
            ", ".join(counts) if counts else "no content affected by semantic-layer changes"
        )
    if r.validator_ran and not r.baseline:
        bits.append(f"{len(r.new_errors)} new content error(s), {r.open_errors} open")
    elif r.validator_ran:
        bits.append(f"{r.open_errors} open content error(s)")
    if r.upgraded:
        bits.append(f"Looker upgraded {r.version[0]} → {r.version[1]}")
    if r.deploys:
        bits.append(f"LookML deployed in {', '.join(sorted(r.deploys))}")
    att = sum(1 for x in r.releases if x.attention)
    if r.releases:
        bits.append(f"{len(r.releases)} new Google release note(s), {att} need attention")
    return bits


# --- Markdown ------------------------------------------------------------------------------


def _md_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def _md_link(text: str, url: str) -> str:
    return f"[{_md_cell(text)}]({url})" if url else _md_cell(text)


def _md_param(c: Change) -> list[str]:
    out = []
    for p in c.params:
        if "\n" in p.old or "\n" in p.new or len(p.old) + len(p.new) > 80:
            out.append(f"  - `{p.param}`\n\n    ```\n    - {p.old}\n    + {p.new}\n    ```")
        else:
            out.append(f"  - `{p.param}`: `{p.old or '∅'}` → `{p.new or '∅'}`")
    return out


def render_markdown(r: Report) -> str:
    L = [f"# Looker impact report: {_day(r.head_at)} UTC", ""]
    since = f"compared with {_day(r.base_at)} UTC" if r.base_at else "first run"
    L.append(f"`{r.instance}` · {since} · Looker {r.version[1] or '?'}")
    L += ["", *[f"- **{b}**" for b in headline(r)], ""]

    if r.impacts:
        L.append("## Impacted content")
        for sev, items in r.by_severity().items():
            if not items:
                continue
            L += [
                "",
                f"### {TITLES[sev]} ({len(items)})",
                "",
                "| Content | Type | What changed | Cause |",
                "|---|---|---|---|",
            ]
            for i in items[:MD_ROWS]:
                L.append(
                    f"| {_md_link(_name(i), i.item.url)} | {KINDS.get(i.item.kind, i.item.kind)} "
                    f"| {_md_cell('; '.join(i.reasons[:3]))}{' …' if len(i.reasons) > 3 else ''} "
                    f"| {_md_cell('; '.join(i.causes))} |"
                )
            if len(items) > MD_ROWS:
                L.append(f"\n…and {len(items) - MD_ROWS} more (see report.html)")
        L.append("")

    if r.new_errors or r.resolved_errors:
        L += ["## Content validator", ""]
        if r.new_errors:
            L += [
                f"**New errors ({len(r.new_errors)})**",
                "",
                "| Content | Error | Explore |",
                "|---|---|---|",
            ]
            for v in r.new_errors[:MD_ROWS]:
                L.append(
                    f"| {_md_link(v.title, v.url)} | {_md_cell(v.message)} | {v.model}::{v.explore} |"
                )
        if r.resolved_errors:
            L += [
                "",
                f"**Resolved ({len(r.resolved_errors)})**: "
                + ", ".join(_md_cell(v.title) for v in r.resolved_errors[:20]),
            ]
        L.append("")

    if r.changes:
        L += [f"## LookML changes ({len(r.changes)})", ""]
        current = ""
        for c in r.changes[: MD_ROWS * 2]:
            if c.explore_key != current:
                current = c.explore_key
                L += ["", f"#### `{current}`", ""]
            L.append(f"- **{c.describe()}** ({c.category}) · {c.cause}")
            L += _md_param(c)
        L.append("")

    if r.releases:
        L += [
            "## New Google release notes",
            "",
            "| | Date | Source | Kind | Note | Relevant to |",
            "|---|---|---|---|---|---|",
        ]
        for x in sorted(r.releases, key=lambda x: (not x.attention, x.note.date), reverse=False)[
            :50
        ]:
            text = x.note.text[:220] + ("…" if len(x.note.text) > 220 else "")
            L.append(
                f"| {'⚠️' if x.attention else ''} | {x.note.date} | {x.note.source} | {x.note.kind} "
                f"| {_md_link(text, x.note.url)} | {', '.join(x.areas)} |"
            )
        L.append("")

    if r.warnings:
        L += [
            "<details><summary>Warnings</summary>",
            "",
            *[f"- {w}" for w in r.warnings],
            "",
            "</details>",
            "",
        ]
    L.append(f"<sub>looker-impact {__version__}</sub>")
    return "\n".join(L) + "\n"


# --- HTML ----------------------------------------------------------------------------------

CSS = """
:root{--bg:#fbfaf8;--fg:#1d1d1b;--muted:#6b6862;--line:#e6e2dc;--card:#fff;
--breaking:#b42318;--results:#b54708;--ai_context:#5b4bd1;--cosmetic:#667085;--ok:#067647}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe8;--muted:#a19e98;
--line:#2e2d2a;--card:#1e1e1c;--breaking:#f97066;--results:#fdb022;--ai_context:#a49bf5;
--cosmetic:#98a2b3;--ok:#47cd89}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:40px 0 12px}
.meta{color:var(--muted);font-size:13px}code{font:12.5px ui-monospace,Menlo,monospace}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:24px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.tile b{display:block;font-size:26px;line-height:1.1}.tile span{color:var(--muted);font-size:13px}
.notice{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--ai_context);
border-radius:8px;padding:10px 14px;margin:8px 0}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
tr:last-child td{border-bottom:0}a{color:inherit}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600;
color:var(--c);border:1px solid var(--c);white-space:nowrap}
.diff{margin:4px 0 0;padding:6px 8px;border-radius:6px;background:var(--bg);white-space:pre-wrap;
word-break:break-word;font:12px ui-monospace,Menlo,monospace}
.del{color:var(--breaking)}.add{color:var(--ok)}
input{width:100%;max-width:360px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;
background:var(--card);color:var(--fg);margin:0 0 12px;font:inherit}
.muted{color:var(--muted)}
"""

FILTER_JS = """
document.querySelectorAll('input[data-filter]').forEach(function(inp){
  inp.addEventListener('input',function(){
    var q=inp.value.toLowerCase();
    document.querySelectorAll('#'+inp.dataset.filter+' tbody tr').forEach(function(tr){
      tr.style.display=tr.textContent.toLowerCase().indexOf(q)>=0?'':'none';});});});
"""


def _e(s: object) -> str:
    return html.escape(str(s))


def _a(text: str, url: str) -> str:
    return f'<a href="{_e(url)}">{_e(text)}</a>' if url else _e(text)


def _pill(sev: str) -> str:
    return f'<span class="pill" style="--c:var(--{sev})">{_e(TITLES.get(sev, sev))}</span>'


def _diff_html(c: Change) -> str:
    rows = []
    for p in c.params:
        rows.append(
            f'<div class="diff"><b>{_e(p.param)}</b>\n<span class="del">- {_e(p.old or "∅")}</span>'
            f'\n<span class="add">+ {_e(p.new or "∅")}</span></div>'
        )
    return "".join(rows)


def render_html(r: Report) -> str:
    sev = r.by_severity()
    tiles = [(len(sev[k]), TITLES[k], k) for k in sev]
    tiles.append((len(r.new_errors), "New content errors", "breaking"))
    tiles.append((len(r.changes), "LookML changes", "cosmetic"))
    tiles.append((sum(1 for x in r.releases if x.attention), "Google notes to review", "results"))
    since = f"compared with {_day(r.base_at)} UTC" if r.base_at else "first run (baseline)"
    out = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        f"<title>Looker impact {_e(_day(r.head_at)[:10])}</title><style>{CSS}</style></head><body><main>",
        f"<h1>Looker impact report</h1><div class=meta>{_e(r.instance)} · {_e(_day(r.head_at))} UTC · "
        f"{_e(since)} · Looker {_e(r.version[1] or '?')}</div>",
        '<div class="tiles">',
        *[
            f'<div class="tile"><b style="color:{"var(--" + k + ")" if n else "inherit"}">{n}</b>'
            f"<span>{_e(t)}</span></div>"
            for n, t, k in tiles
        ],
        "</div>",
    ]
    if r.baseline:
        out.append(
            '<div class="notice">First run: today\'s state is saved as the baseline. '
            "Changes and their impact are reported from the next run on.</div>"
        )
    if r.upgraded:
        out.append(
            f'<div class="notice">Looker was upgraded <b>{_e(r.version[0])} → '
            f"{_e(r.version[1])}</b> since the last run.</div>"
        )
    for p, (a, b) in sorted(r.deploys.items()):
        out.append(
            f'<div class="notice">LookML deployed in <b>{_e(p)}</b>: '
            f"<code>{_e(a[:10] or '?')}</code> → <code>{_e(b[:10] or '?')}</code></div>"
        )

    if r.impacts:
        out += [
            f"<h2>Impacted content ({len(r.impacts)})</h2>",
            '<input data-filter="impacts" placeholder="Filter by name, field, folder…">',
            '<div class="wrap"><table id="impacts"><thead><tr><th>Severity</th><th>Content</th>'
            "<th>Type</th><th>What changed</th><th>Cause</th></tr></thead><tbody>",
        ]
        for i in r.impacts:
            folder = f'<div class="muted">{_e(i.item.folder)}</div>' if i.item.folder else ""
            out.append(
                f"<tr><td>{_pill(i.severity)}</td><td>{_a(_name(i), i.item.url)}{folder}</td>"
                f"<td>{_e(KINDS.get(i.item.kind, i.item.kind))}</td>"
                f"<td>{'<br>'.join(_e(x) for x in i.reasons)}</td>"
                f"<td>{'<br>'.join(_e(x) for x in i.causes)}</td></tr>"
            )
        out.append("</tbody></table></div>")

    if r.new_errors or r.resolved_errors:
        out.append(
            f"<h2>Content validator: {len(r.new_errors)} new, {len(r.resolved_errors)} resolved</h2>"
        )
        out.append(
            '<div class="wrap"><table><thead><tr><th></th><th>Content</th><th>Error</th>'
            "<th>Explore</th></tr></thead><tbody>"
        )
        for v in r.new_errors:
            out.append(
                f"<tr><td>{_pill('breaking')}</td><td>{_a(v.title, v.url)}</td>"
                f"<td>{_e(v.message)}</td><td>{_e(v.model)}::{_e(v.explore)}</td></tr>"
            )
        for v in r.resolved_errors:
            out.append(
                f'<tr><td><span class="pill" style="--c:var(--ok)">Resolved</span></td>'
                f"<td>{_a(v.title, v.url)}</td><td>{_e(v.message)}</td>"
                f"<td>{_e(v.model)}::{_e(v.explore)}</td></tr>"
            )
        out.append("</tbody></table></div>")

    if r.changes:
        out += [
            f"<h2>LookML changes ({len(r.changes)})</h2>",
            '<input data-filter="changes" placeholder="Filter by explore, field…">',
            '<div class="wrap"><table id="changes"><thead><tr><th>Category</th><th>Explore</th>'
            "<th>Change</th><th>Cause</th></tr></thead><tbody>",
        ]
        for c in r.changes:
            out.append(
                f"<tr><td>{_pill(c.category)}</td><td><code>{_e(c.explore_key)}</code></td>"
                f"<td><b>{_e(c.describe())}</b>{_diff_html(c)}</td><td>{_e(c.cause)}</td></tr>"
            )
        out.append("</tbody></table></div>")

    if r.releases:
        out += [
            f"<h2>New Google release notes ({len(r.releases)})</h2>",
            '<div class="wrap"><table><thead><tr><th></th><th>Date</th><th>Source</th><th>Kind</th>'
            "<th>Note</th><th>Relevant to</th></tr></thead><tbody>",
        ]
        for x in sorted(r.releases, key=lambda x: (not x.attention, x.note.date)):
            flag = _pill("results").replace("Numbers may change", "Review") if x.attention else ""
            out.append(
                f"<tr><td>{flag}</td><td>{_e(x.note.date)}</td><td>{_e(x.note.source)}</td>"
                f"<td>{_e(x.note.kind)}</td><td>{_a(x.note.text, x.note.url)}</td>"
                f"<td>{_e(', '.join(x.areas))}</td></tr>"
            )
        out.append("</tbody></table></div>")

    counts = ", ".join(f"{n} {KINDS.get(k, k)}s" for k, n in sorted(r.content_counts.items()))
    out.append(
        f'<h2>Coverage</h2><p class="muted">{r.explore_count} explores; {_e(counts or "no content")}.</p>'
    )
    if r.warnings:
        out.append(
            "<h2>Warnings</h2><ul>" + "".join(f"<li>{_e(w)}</li>" for w in r.warnings) + "</ul>"
        )
    out.append(
        f'<p class="muted">looker-impact {__version__} · metadata only, no query results</p>'
    )
    out.append(f"</main><script>{FILTER_JS}</script></body></html>")
    return "\n".join(out)


# --- JSON and Slack ------------------------------------------------------------------------


def render_json(r: Report) -> str:
    d: dict[str, Any] = asdict(r)
    d["worst"] = r.worst()
    d["tool_version"] = __version__
    return json.dumps(d, indent=1, sort_keys=True)


def slack_payload(r: Report, link: str = "") -> dict[str, Any]:
    worst = r.worst()
    icon = {
        "breaking": ":red_circle:",
        "results": ":large_orange_circle:",
        "ai_context": ":large_purple_circle:",
        "cosmetic": ":white_circle:",
    }.get(worst or "", ":large_green_circle:")
    lines = [f"{icon} *Looker impact {_day(r.head_at)[:10]}*", *[f"• {b}" for b in headline(r)]]
    for i in r.impacts[:8]:
        name = f"<{i.item.url}|{_name(i)}>" if i.item.url else _name(i)
        lines.append(f"  {TITLES[i.severity]}: {name} ({KINDS.get(i.item.kind, i.item.kind)})")
    if len(r.impacts) > 8:
        lines.append(f"  …and {len(r.impacts) - 8} more")
    if link:
        lines.append(f"<{link}|Full report>")
    return {"text": "\n".join(lines)}


def write_reports(r: Report, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "impact.md": render_markdown(r),
        "impact.html": render_html(r),
        "impact.json": render_json(r),
    }
    paths = []
    for name, text in files.items():
        p = out_dir / name
        p.write_text(text, encoding="utf-8")
        paths.append(p)
    return paths
