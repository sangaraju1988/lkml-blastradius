# Changelog

## 0.1.0 (2026-10-03)

First release.

- `lkblast run`: daily snapshot of Looker metadata through the API (explores and fields, dashboard
  tiles and filters, Looks, merged queries, Looker CA agents, content validator, Looker version,
  deployed LookML commits, Google release notes), compared with the previous run.
- Impact per content item: breaking, numbers may change, AI agents may answer differently, cosmetic;
  each change attributed to a LookML deploy or a Looker upgrade.
- Reports: `blastradius.md`, `blastradius.html`, `blastradius.json`, and a Slack summary.
- `lkblast init`: `blastradius.yaml` and a daily GitHub Actions workflow.
- `lkblast compare` for any two snapshots; `lkblast demo` offline.
- Optional Conversational Analytics API data agents.
