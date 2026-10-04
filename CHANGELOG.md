# Changelog

## 0.1.0 (2026-10-03)

First release.

- `lkimpact run`: daily snapshot of Looker metadata through the API (explores and fields, dashboard
  tiles and filters, Looks, merged queries, Looker CA agents, content validator, Looker version,
  deployed LookML commits, Google release notes), compared with the previous run.
- Impact per content item: breaking, numbers may change, AI agents may answer differently, cosmetic;
  each change attributed to a LookML deploy or a Looker upgrade.
- Reports: `impact.md`, `impact.html`, `impact.json`, and a Slack summary.
- `lkimpact init`: `impact.yaml` and a daily GitHub Actions workflow.
- `lkimpact compare` for any two snapshots; `lkimpact demo` offline.
- Optional Conversational Analytics API data agents.
