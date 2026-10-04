"""Google release notes: what Google shipped since the last run, tagged by the areas you use.

Sources (checked 2026-10-03):
* Looker: Atom feed ``docs.cloud.google.com/feeds/looker-release-notes.xml``; one ``<entry>`` per
  date, whose HTML content has ``<h3>Feature|Changed|Deprecated|...</h3>`` sections.
* Conversational Analytics API: no feed; the HTML page has ``<h2 id=... data-text="Month D, YYYY">``
  per date and ``devsite-label-release-<kind>`` labels per note.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from datetime import date, datetime

import httpx

from looker_impact.model import ReleaseNote

ATOM = "{http://www.w3.org/2005/Atom}"
ATTENTION_KINDS = {"breaking", "deprecated", "changed", "change", "issue", "removed"}
ATTENTION_WORDS = re.compile(
    r"\b(deprecat\w*|no longer|removed?|breaking|end of support|shut ?down|will stop|migrat\w+)\b",
    re.I,
)


def _text(fragment: str) -> str:
    t = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def _date(s: str) -> str:
    for fmt in ("%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip()[:20].strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return s[:10]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def parse_atom(xml_text: str, source: str) -> list[ReleaseNote]:
    out: list[ReleaseNote] = []
    root = ET.fromstring(xml_text)
    for entry in root.iter(f"{ATOM}entry"):
        updated = (entry.findtext(f"{ATOM}updated") or "")[:10]
        link = entry.find(f"{ATOM}link")
        url = link.get("href", "") if link is not None else ""
        content = entry.findtext(f"{ATOM}content") or ""
        parts = re.split(r"<h3[^>]*>(.*?)</h3>", content, flags=re.S)
        # parts: [preamble, kind1, body1, kind2, body2, ...]
        for i in range(1, len(parts) - 1, 2):
            kind = _text(parts[i]) or "Note"
            for j, para in enumerate(re.split(r"</p>\s*(?=<p)", parts[i + 1])):
                text = _text(para)
                if text:
                    nid = f"{source}:{updated}:{_slug(kind)}:{_slug(text[:40])}:{j}"
                    out.append(ReleaseNote(nid, source, updated, kind, text[:600], url))
    return out


def parse_devsite_html(page: str, source: str, base_url: str) -> list[ReleaseNote]:
    out: list[ReleaseNote] = []
    sections = re.split(
        r'<h2[^>]*id="([^"]+)"[^>]*data-text="([^"]+)"[^>]*>.*?</h2>', page, flags=re.S
    )
    # sections: [preamble, id1, date1, body1, id2, date2, body2, ...]
    for i in range(1, len(sections) - 2, 3):
        anchor, day, body = sections[i], _date(html.unescape(sections[i + 1])), sections[i + 2]
        notes = re.split(r'<span class="devsite-label devsite-label-release-([a-z-]+)">', body)
        for k in range(1, len(notes) - 1, 2):
            kind = notes[k].replace("-", " ").title()
            text = _text(re.sub(r"^[^<]*</span>", "", notes[k + 1]))
            if text:
                nid = f"{source}:{day}:{_slug(kind)}:{_slug(text[:40])}"
                out.append(ReleaseNote(nid, source, day, kind, text[:600], f"{base_url}#{anchor}"))
    return out


def fetch_release_notes(
    sources: Iterable[dict[str, str]],
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[list[ReleaseNote], list[str]]:
    notes: list[ReleaseNote] = []
    warnings: list[str] = []
    with httpx.Client(timeout=30, transport=transport, follow_redirects=True) as http:
        for s in sources:
            try:
                resp = http.get(s["url"])
                resp.raise_for_status()
                if s.get("format") == "atom":
                    notes += parse_atom(resp.text, s["name"])
                else:
                    notes += parse_devsite_html(resp.text, s["name"], s["url"])
            except (httpx.HTTPError, ET.ParseError, KeyError) as exc:
                warnings.append(f"release notes {s.get('name', '?')}: {exc}")
    return notes, warnings


def new_since(
    notes: list[ReleaseNote], seen: set[str], since: date | None, lookback_start: date
) -> list[ReleaseNote]:
    """Notes not seen in the previous snapshot, dated on/after the previous run (or lookback)."""
    start = since or lookback_start
    return sorted(
        (n for n in notes if n.id not in seen and n.date >= start.isoformat()),
        key=lambda n: (n.date, n.source, n.id),
        reverse=True,
    )


def needs_attention(n: ReleaseNote) -> bool:
    return n.kind.lower() in ATTENTION_KINDS or bool(ATTENTION_WORDS.search(n.text))


AREAS: dict[str, re.Pattern[str]] = {
    "agents": re.compile(r"conversational analytics|data agent|\bagents?\b|gemini", re.I),
    "dashboards": re.compile(r"dashboard|tile|visuali[sz]ation", re.I),
    "looks": re.compile(r"\blooks?\b|saved quer", re.I),
    "lookml": re.compile(
        r"lookml|explore|dimension|measure|derived table|\bpdts?\b|refinement|semantic", re.I
    ),
    "api": re.compile(r"\bapi\b|\bsdk\b", re.I),
}


def areas(n: ReleaseNote, dialects: set[str]) -> list[str]:
    hits = [a for a, rx in AREAS.items() if rx.search(n.text)]
    hits += sorted(d for d in dialects if d and re.search(re.escape(d), n.text, re.I))
    return hits
