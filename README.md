# looker-impact

**Every morning: what changed in Looker, and which dashboards, Looks, explores and
Conversational Analytics agents it affects.**

```bash
pip install looker-impact
lkimpact init          # writes impact.yaml + a daily GitHub Actions workflow
lkimpact run           # snapshot Looker, compare with yesterday, write the report
```

Each run takes a metadata snapshot through the Looker API and compares it with the previous one:

| It tracks | From |
|---|---|
| Every explore and field: SQL, type, label, description, synonyms, joins, `sql_table_name`, always filters | `lookml_models`, `lookml_model_explore` |
| Every dashboard tile and filter, Look, and merged query, with each field it uses (including filters, sorts, pivots, custom fields) | `dashboards`, `looks`, `queries`, `merge_queries` |
| Conversational Analytics agents in Looker: explores, golden queries, fields named in instructions | `agents` (beta API) |
| CA API data agents (optional) | `geminidataanalytics.googleapis.com` `dataAgents` |
| Broken content, whatever the cause | Looker content validator |
| Looker version and the deployed LookML commit of every project, including imported ones | `versions`, `projects/{id}/git_branch` |
| What Google shipped | Looker release-notes feed, CA API release notes |

Then it reports **what changed, old → new, which content it touches, how badly, and why**:

| Severity | Meaning |
|---|---|
| **Breaking** | A field, join or explore that the content uses was removed, or the content validator found a new error |
| **Numbers may change** | SQL, type, measure filters, joins, table or always-filters changed for a field the content uses |
| **AI agents may answer differently** | Descriptions, labels, synonyms, hidden flags or new fields changed in an explore an agent can use. Dashboards ignore these changes, but agents read them. |
| **Cosmetic** | Labels or formats on dashboards and Looks |

Every change is attributed to one of three causes: **a LookML deploy** in a named project (old → new commit),
**a Looker upgrade** (old → new version) with no deploy, or neither. Changes from Google come from
the upgrade line, the matching release notes, and the content validator.

## Try it offline

```bash
lkimpact demo          # two days on a fake Looker (fictional Harborline Supply Co.)
open lkimpact-demo/reports/impact.html
```

```
  2 breaking, 3 numbers may change, 1 cosmetic
  2 new content error(s), 2 open
  Looker upgraded 26.14.2 → 26.16.1
  LookML deployed in core_project, finance_project
  3 new Google release note(s), 2 need attention
```

| Content | Severity | Why | Cause |
|---|---|---|---|
| Executive revenue / Revenue by region (tile) | Breaking | `customers.region` removed | LookML deploy in finance_project |
| Executive revenue / Region (filter) | Breaking | `customers.region` removed | LookML deploy in finance_project |
| Executive revenue / Net revenue by month | Numbers may change | `orders.net_revenue` SQL now also subtracts discounts | LookML deploy in core_project (imported) |
| Collections / Amount due vs net revenue (merged query) | Numbers may change | same | same |
| Finance assistant (Looker CA agent) | Numbers may change | its golden query uses `orders.net_revenue` | same |
| Executive revenue / Order volume | Cosmetic | `orders.order_count` label | same |

The *Refund watch* Look and the *Logistics helper* agent are not listed because nothing they use changed.

## Run it daily (GitHub Actions + Slack)

1. `lkimpact init` in any repo (it can be an otherwise empty repo).
2. Add repository secrets: `LOOKER_BASE_URL`, `LOOKER_CLIENT_ID`, `LOOKER_CLIENT_SECRET`, and
   optionally `SLACK_WEBHOOK_URL`.
3. Push. The workflow runs at 06:00 UTC daily (change the cron in `.github/workflows/looker-impact.yml`)
   or on demand from the Actions tab.

Every run writes `reports/impact.{md,html,json}`. The Markdown goes to the job summary, the reports are
uploaded as an artifact, and a short summary with a link to the run is posted to Slack. Snapshot history
lives in the Actions cache. Each run refreshes it, so it doesn't expire while the job runs daily.

The first run saves a baseline. Impact is reported from the second run on.

**API user.** Use API credentials of a user that can see all content (an admin, or a role with
`see_lookml`, `see_user_dashboards`, `see_looks`, `access_data` on all models). Content the user
can't see is not analyzed. Reading deployed commits needs `see_lookml`/`develop`; without it, the
report still works but can't tell a LookML deploy from a Looker upgrade.

## Options

`impact.yaml` (written by `lkimpact init`; every key is optional):

```yaml
looker:   {models: [], workers: 8, timeout_seconds: 120}
content:  {dashboards: true, looks: true, looker_agents: true, content_validator: true}
ca_api:   {enabled: false, locations: [global]}   # + GOOGLE_CLOUD_PROJECT, GOOGLE_OAUTH_ACCESS_TOKEN
releases: {enabled: true, lookback_days: 7}
storage:  {dir: .lkimpact, keep: 30}
report:   {dir: reports}
```

```bash
lkimpact run --fail-on breaking          # exit 1 when something breaks (for alerting)
lkimpact compare OLD.json.gz NEW.json.gz # report between any two saved snapshots
```

To include **CA API data agents**, set `ca_api.enabled: true` and provide `GOOGLE_CLOUD_PROJECT`
and an access token in `GOOGLE_OAUTH_ACCESS_TOKEN`. In Actions, get one with
`google-github-actions/auth` (`token_format: access_token`). Agents that point at another Looker
instance are ignored.

## What it does not do

- It reports changes **after** they reach production (daily). It doesn't check a LookML branch
  before merge.
- It never runs queries and stores no row data. It can't see a number change that comes from the
  warehouse data itself.
- It can't see a Google change that alters behavior but not metadata, such as how an agent picks
  fields. That shows up only as an upgrade line and release notes. Agent answer regression testing
  is what [lookml-agentops](https://github.com/sangaraju1988/lookml-agentops) does.

## API sources

Every Looker endpoint and attribute used is checked against Looker's OpenAPI spec
(`looker-open-source/sdk-codegen`, `spec/Looker.4.0.oas.json`, 4.0.26.12). The list is in the
docstring of [`looker.py`](src/looker_impact/looker.py). `/agents` is marked **beta** by Looker.
CA API fields come from the v1 REST reference. Release-note formats were checked on 2026-10-03
(see [`releases.py`](src/looker_impact/releases.py)). If Google changes the page layout, the run
records a warning and continues.

## Development

```bash
uv sync && uv run pytest && uv run ruff check src tests && uv run mypy
```

Apache-2.0. Not affiliated with or endorsed by Google. Looker is a trademark of Google LLC.
