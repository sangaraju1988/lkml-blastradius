# lkml-blastradius

**The blast radius of every Looker change.** Which dashboards, Looks, explores and Conversational
Analytics agents a change hits, and how badly. It works in two ways:

- **On every push** to a LookML repo, as a GitHub Action: what this commit *would* do to production
  content, checked in Looker development mode before it is deployed. The result is a job summary and
  one PR comment that is updated on each push.
- **Every morning**: what actually changed in production (LookML deploys, Looker upgrades, Google
  releases) since yesterday.

```bash
pip install lkml-blastradius
lkblast init --push finance_project   # on-every-push workflow for one LookML repo
lkblast init                          # daily workflow + blastradius.yaml
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
lkblast demo    # two days on a fake Looker (fictional Harborline Supply Co.)
open lkblast-demo/reports/blastradius.html
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

## On every push (for all teams)

Each team adds one workflow file to its LookML repo. `lkblast init --push <project>` writes it:

```yaml
on:
  push:
    branches-ignore: ["lkblast-*"]   # lkblast's temporary branches (see below)
permissions: {contents: read, pull-requests: write}
concurrency: {group: lkml-blastradius-finance_project, cancel-in-progress: false}
jobs:
  blast-radius:
    runs-on: ${{ vars.LKBLAST_RUNS_ON || 'ubuntu-latest' }}
    steps:
      - uses: sangaraju1988/lkml-blastradius@v0
        with:
          project: finance_project
          looker-base-url: ${{ secrets.LOOKER_BASE_URL }}
          looker-client-id: ${{ secrets.LOOKER_CLIENT_ID }}
          looker-client-secret: ${{ secrets.LOOKER_CLIENT_SECRET }}
          fail-on: never        # or `breaking` to block merges that break production content
```

Set the three secrets once as **organization secrets**, and every team's workflow picks them up.

What one run does (`lkblast check`):

1. **Production:** reads the project's explores, the dashboards, Looks and CA agents that use them,
   and the content validator errors.
2. **Development mode:** creates a temporary branch `lkblast-<sha>` at the pushed commit in the API
   user's dev workspace. It reads the same explores as Looker compiles them there, so includes,
   extends, refinements and imports are all resolved by Looker itself, and runs the content
   validator against that LookML.
3. **Cleanup, always, even on failure:** checks out the API user's original dev branch again, deletes
   the temporary branch (locally and on the remote), and switches the session back to production.
   The team's own branches are never checked out or reset.
4. **Report:** the difference, mapped to production content, with the same severities as below. It
   goes to the job summary, an artifact (md/html/json) and one PR comment updated in place.

Things to know:

- **The API user needs `develop`** on the project, plus `see_lookml`, `see_user_dashboards` and
  `see_looks`.
- **Temporary branches appear in your git remote** for a few seconds, because Looker pushes branches it
  creates. The generated workflow ignores `lkblast-*` pushes, so they don't trigger it again. Other
  CI that runs on every branch should ignore them too.
- **Runs for one project are queued,** not run in parallel (one API user has one dev checkout per
  project). The `concurrency` group handles this.
- **Projects without models** (imported by others): pass the importing models with `models:`.

Action inputs: `project`, `looker-base-url`, `looker-client-id`, `looker-client-secret`, `models`,
`fail-on`, `content-validator`, `comment`, `slack-webhook-url`, `setup-python`, `ca-bundle`,
`ref`, `label`. Outputs: `worst`, `report-dir`. Try it offline with `lkblast demo --push`.

## When GitHub can't reach Looker (internal Looker, private IP, Looker core in a VPC)

You don't need a network path from github.com into your network. A job runs on a **runner**, and
a runner only makes **outbound** HTTPS connections to GitHub to pick up work. So put a runner where
Looker is reachable:

| Setup | How |
|---|---|
| **Self-hosted runner inside the network** (most common) | A VM or Kubernetes pod in the network or VPC that can reach Looker, registered to an **organization runner group** with a label such as `looker`. Set the org variable `LKBLAST_RUNS_ON=looker`, and every team's workflow runs there without editing the file. On GKE, Actions Runner Controller scales runners automatically. |
| **Looker (Google Cloud core) with private IP / PSC** | Run that self-hosted runner in the same VPC, or in a VPC peered or connected to it. |
| **GitHub-hosted larger runners with private networking** (GitHub Enterprise Cloud) | Azure private networking puts GitHub-hosted runners in your VNet. Alternatively, use static outbound IPs and allowlist them on a Looker that has an IP allowlist. |
| **GitHub Enterprise Server** (on-premises) | Its runners are usually already inside the network. `GITHUB_API_URL` is honored for the PR comment. |

On self-hosted runners:

- `setup-python: "false"` uses the runner's own Python 3.11+, with no download from the internet.
- `HTTPS_PROXY` / `NO_PROXY` are honored for Looker and GitHub calls. `PIP_INDEX_URL` points pip at
  an internal mirror.
- `ca-bundle: /path/to/corp-ca.pem` (or `SSL_CERT_FILE`) handles a Looker with an internal certificate.

## Run it daily (GitHub Actions + Slack)

1. `lkblast init` in any repo (it can be an otherwise empty repo).
2. Add repository secrets: `LOOKER_BASE_URL`, `LOOKER_CLIENT_ID`, `LOOKER_CLIENT_SECRET`, and
   optionally `SLACK_WEBHOOK_URL`.
3. Push. The workflow runs at 06:00 UTC daily (change the cron in `.github/workflows/lkml-blastradius.yml`)
   or on demand from the Actions tab.

Every run writes `reports/blastradius.{md,html,json}`. The Markdown goes to the job summary, the reports are
uploaded as an artifact, and a short summary with a link to the run is posted to Slack. Snapshot history
lives in the Actions cache. Each run refreshes it, so it doesn't expire while the job runs daily.

The first run saves a baseline. Impact is reported from the second run on.

**API user.** Use API credentials of a user that can see all content (an admin, or a role with
`see_lookml`, `see_user_dashboards`, `see_looks`, `access_data` on all models). Content the user
can't see is not analyzed. Reading deployed commits needs `see_lookml`/`develop`; without it, the
report still works but can't tell a LookML deploy from a Looker upgrade.

## Options

`blastradius.yaml` (written by `lkblast init`; every key is optional):

```yaml
looker:   {models: [], workers: 8, timeout_seconds: 120}
content:  {dashboards: true, looks: true, looker_agents: true, content_validator: true}
ca_api:   {enabled: false, locations: [global]}   # + GOOGLE_CLOUD_PROJECT, GOOGLE_OAUTH_ACCESS_TOKEN
releases: {enabled: true, lookback_days: 7}
storage:  {dir: .lkblast, keep: 30}
report:   {dir: reports}
```

```bash
lkblast run --fail-on breaking          # exit 1 when something breaks (for alerting)
lkblast compare OLD.json.gz NEW.json.gz # report between any two saved snapshots
```

To include **CA API data agents**, set `ca_api.enabled: true` and provide `GOOGLE_CLOUD_PROJECT`
and an access token in `GOOGLE_OAUTH_ACCESS_TOKEN`. In Actions, get one with
`google-github-actions/auth` (`token_format: access_token`). Agents that point at another Looker
instance are ignored.

## What it does not do

- The daily mode reports changes after they reach production. Push mode checks LookML before
  deploy, but only the project you push (plus `models:`). Changes in other projects, or in content
  edited in the UI, show up in the daily report.
- It never runs queries and stores no row data. It can't see a number change that comes from the
  warehouse data itself.
- It can't see a Google change that alters behavior but not metadata, such as how an agent picks
  fields. That shows up only as an upgrade line and release notes. Agent answer regression testing
  is what [lookml-agentops](https://github.com/sangaraju1988/lookml-agentops) does.

## API sources

Every Looker endpoint and attribute used is checked against Looker's OpenAPI spec
(`looker-open-source/sdk-codegen`, `spec/Looker.4.0.oas.json`, 4.0.26.12). The list is in the
docstring of [`looker.py`](src/lkml_blastradius/looker.py). `/agents` is marked **beta** by Looker.
CA API fields come from the v1 REST reference. Release-note formats were checked on 2026-10-03
(see [`releases.py`](src/lkml_blastradius/releases.py)). If Google changes the page layout, the run
records a warning and continues.

## Development

```bash
uv sync && uv run pytest && uv run ruff check src tests && uv run mypy
```

Apache-2.0. Not affiliated with or endorsed by Google. Looker is a trademark of Google LLC.
