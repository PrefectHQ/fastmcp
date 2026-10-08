# Maintainer dashboard

A static dashboard over the public `status` branch's `status.json`. Serve this directory with any static host, including GitHub Pages or Wisp. It needs no credentials or build step.

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

On GitHub Pages, the browser reads JSON directly from the public `status` branch: bot commits do not trigger a Pages rebuild. Other hosts and local previews use adjacent `status.json`. Publishing the UI itself still requires a Pages deployment.
