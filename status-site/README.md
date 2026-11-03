# Public backlog dashboard

Published at <https://prefecthq.github.io/fastmcp/> from the `status` branch.
The landing page reads `backlog.json`; automation health remains at
`operations.html` and reads `status.json`. Both use raw GitHub data in production
so the data can refresh independently of the Pages deployment.

`backlog-status.yml` runs every 30 minutes, retaining the previous snapshot
series and refreshing creation/latest-closure outcomes from public GitHub
records. The build job has read-only access; the publisher only copies its
artifact. Both status publishers serialize writes through the same concurrency
group. `maintenance-status.yml` publishes the static site alongside its existing
maintenance snapshot twice daily, or on manual dispatch.

The original half-hourly collector's history was imported with:

```bash
python scripts/backlog_status.py --import-history history.jsonl --output backlog.json
```

Keep that generated JSON on the `status` branch, not in source control on `main`.
To rebuild, pass the published file with `--previous` so observed history is
never replaced by reconstructed counts. A missing previous history fails the
scheduled run instead of silently resetting the series.

Dates and daily buckets use America/Chicago. Observed samples are authoritative
for their collection times. Reconstructed rows cannot recover past close/reopen
cycles. Daily closures use each currently closed item's latest closure timestamp
and reason; they are not a complete event log. Openings and outcomes are counted
from individual records, never inferred from differences in open counts.

For a local preview, copy the public `backlog.json` into this folder and run
`python -m http.server 8765 --directory status-site` from the repository root.
Do not commit the copied data. Charts support touch, pointer inspection, and
arrow keys; Enter opens the selected activity day's linked items.

## Automation view

The operations page is a static dashboard over the public `status` branch's `status.json`. Serve this directory with any static host, including GitHub Pages or Wisp. It needs no credentials or build step.

For local preview, build a snapshot with `uv run scripts/maintenance_status.py .local/status-preview`, then run `uv run python -m http.server 4174 --directory .local/status-preview` from the repository root. The collector copies the dashboard assets alongside the data.

The browser checks for a new snapshot every five minutes. This does not change the collector's twice-daily publication schedule. A snapshot older than 24 hours displays a warning. Public contributor information stays factual; private triage judgments are not loaded.

`scripts/rank_issues.py` prints the private ranking; `--public` uses the dashboard's scoring profile. Public scores exclude author reputation, cross-repository activity, and contributor-quality assessments before sorting; the JSON exporter allowlists issue fields and explanations. Never publish the private CLI's JSON output.

The status workflow ranks the newest 500 open issues. Coverage, collection time, and model-assessment coverage appear above the list; this is not a claim to have ranked the entire backlog. On trusted main runs the optional `TYPESAFE_API_KEY` enables Jev. Only first-attempt scheduled runs on main can make paid calls, capped at ten issue assessments per run (twenty per UTC day). PRs, manual dispatches, and reruns receive no judge secret and make zero model calls. Missing credentials also select zero calls. Unchanged public assessments are reused from the previous status snapshot using a hash of model, prompt version, and input; cache misses beyond the cap use facts and labels. SDK retries are disabled, failures spend their reserved slot, and publisher runs are serialized. Browser refreshes never call a model. Missing model results remain explicit. A score is a prioritization hint, not a review verdict or permission to merge.

To build both views locally:

```sh
uv run scripts/rank_issues.py --public --judge none --limit 500 --json .local/attention.json
uv run scripts/maintenance_status.py .local/status-preview .local/attention.json
```

The public issue list supports kind and unanswered filters, expandable score contributions, and linked fixes. Comment histories that are incomplete do not count as unanswered.

Format flags and unconfirmed duplicate suggestions do not count as dismissal.
Labels do not establish that a maintainer has replied. Information-request labels
remain ranking hints; their presence does not prove that the reporter still owes
a response. Check the discussion and later edits before acting on them.

On GitHub Pages, the browser reads JSON directly from the public `status` branch: bot commits do not trigger a Pages rebuild. Other hosts and local previews use adjacent `status.json`. Publishing the UI itself still requires a Pages deployment.

## Public triage leads

The existing half-hourly workflow publishes `triage.json` (`fastmcp-public-triage/1`)
from a separate read-only job. Its independent public inventory read supplies age
buckets and the five oldest
open, non-draft, non-held contributor PR leads (by non-maintainer user accounts).
Non-draft does not imply ready: contributors may still owe revisions. Both JSON and the
page are public, reproducible from this repository, and independent of personal
infrastructure, models, private reviewer ledgers, and Prefect Cloud credentials.

```sh
python scripts/public_triage.py --output triage.json
```

Run in a checkout with `gh` authenticated for read access. The report records the
source commit, collection start/end, inventory size, selection bounds, exact PR
head/base and activity collection status. Conversation comments and submitted
reviews use all API pages; inline threads and other timeline events are excluded.
Failed reads stay unavailable, never “unanswered.” Maintainer account activity
can be agent-assisted and is not acknowledgement or approval. Triage-app review
presence is separated from an authenticated EGM marker matching the PR number,
snapshot head, submitted-review commit and separately collected target-branch tip.
`base_sha` is the PR metadata's reported base; `baseline_sha` is the target tip
read from the branch endpoint. A missing target tip makes revision matching
unknown, not false. Valid public marker bindings retain the review ID and body
hash, not the body. None of these observations proves tests or independent
verification. Reads are not atomic: recheck heads, holds, discussion,
CI, compatibility and supported behavior before any action. Missing from this
bounded oldest-first queue does not mean handled. No closure/merge is authorized.

Age buckets describe inventory at collection, including draft PRs; they are not
historical reconstructions. Empty successful collection is explicitly zero, while
GitHub inventory failure fails the job. The page flags collections over two hours
old and handles unavailable triage data independently of the existing chart.
Private evidence, author assessments and human authorizations must stay out of
this public JSON. This is review prioritization, not a measure of causal model
benefit or shipped fixes. A smaller backlog remains subject to correctness,
performance, contributor credit and downstream compatibility checks.

Refresh waits for both datasets; superseded successes and errors cannot replace
a newer refresh. Check this behavior with `node --test tests/test_status_refresh.cjs`.

## Collection failures and publication independence

PR-detail reads retry once for timeouts, invalid JSON, HTTP 429 and HTTP 5xx.
Authentication/permission/not-found errors are not blindly retried. Public
`collection_diagnostics` contain only a stage, fixed error category, numeric HTTP
status when available and attempt count. Raw stdout, stderr, response bodies and
credentials are never diagnostics. A successful PR observation survives a failed
discussion or target-tip read. `lead_collection_status` is partial when any
returned lead has a gap; it does not claim coverage of all PRs or all activity.

The first PR build retained five unavailable detail reads without their causes;
that artifact cannot establish why they failed. This diagnostics contract makes
future gaps inspectable without inventing an outage diagnosis or a no-response
verdict. A successful retry is not evidence that historical failures had the
same cause.

Backlog collection and publication do not depend on the optional triage job.
Both publishers and the maintenance publisher serialize writes to the existing
status branch with `queue: max`, so one waiting publisher cannot displace another. The independent
reads can observe different queue states; compare their collection timestamps
rather than treating different totals as an inconsistency. A failed triage
build leaves its previous snapshot to age visibly while the backlog can still
advance. The public marker lookup reads GitHub only; it neither queries private
Cloud artifacts nor acknowledges, authorizes or executes workflow decisions.
