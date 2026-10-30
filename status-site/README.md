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

The dashboard and `just issues` (Textual) share `scripts/rank_issues.py`. `just issues-table` prints the private ranking. Both terminal commands accept `--public` to use the dashboard's scoring profile. Public scores exclude author reputation, cross-repository activity, and contributor-quality assessments before sorting; the JSON exporter allowlists issue fields and explanations. Never publish the private CLI's JSON output.

The status workflow ranks the newest 500 open issues. Coverage, collection time, and model-assessment coverage appear above the list; this is not a claim to have ranked the entire backlog. On trusted main runs the optional `TYPESAFE_API_KEY` enables Jev. Only first-attempt scheduled runs on main can make paid calls, capped at ten issue assessments per run (twenty per UTC day). PRs, manual dispatches, and reruns receive no judge secret and make zero model calls. Missing credentials also select zero calls. Unchanged public assessments are reused from the previous status snapshot using a hash of model, prompt version, and input; cache misses beyond the cap use facts and labels. SDK retries are disabled, failures spend their reserved slot, and publisher runs are serialized. Browser refreshes never call a model. Missing model results remain explicit. A score is a prioritization hint, not a review verdict or permission to merge.

To build both views locally:

```sh
uv run scripts/rank_issues.py --public --judge none --limit 500 --json .local/attention.json
uv run scripts/maintenance_status.py .local/status-preview .local/attention.json
just issues --public --judge none --limit 500
```

The public issue list supports kind and unanswered filters, expandable score contributions, and linked fixes. Comment histories that are incomplete do not count as unanswered. The terminal browser also supports loading additional pages, reranking, and private author context.

Format flags and unconfirmed duplicate suggestions do not count as dismissal.
Labels do not establish that a maintainer has replied. Information-request labels
remain ranking hints; their presence does not prove that the reporter still owes
a response. Check the discussion and later edits before acting on them.

On GitHub Pages, the browser reads JSON directly from the public `status` branch: bot commits do not trigger a Pages rebuild. Other hosts and local previews use adjacent `status.json`. Publishing the UI itself still requires a Pages deployment.
