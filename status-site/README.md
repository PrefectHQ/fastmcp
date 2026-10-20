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
