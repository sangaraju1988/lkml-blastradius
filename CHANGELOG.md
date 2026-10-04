# Changelog

## 0.2.0 (2026-10-04)

- **On every push:** `lkblast check --project P --ref SHA` checks a pushed commit against production
  in Looker development mode, on a temporary `lkblast-*` branch that is always cleaned up. It never
  checks out or resets team branches.
- **GitHub Action** (`uses: sangaraju1988/lkml-blastradius@v0`): job summary, report artifact, one PR
  comment updated in place, optional `fail-on` gate. It works on self-hosted runners (proxy,
  internal CA, no Python download).
- `lkblast init --push PROJECT` writes the per-team workflow; `lkblast comment` posts the PR comment.
- `lkblast demo --push`.
- Fix: a re-login after an expired token no longer uses up a retry, and re-selects the dev workspace.

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
