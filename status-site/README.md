# Maintainer dashboard

A static dashboard over the public `status` branch's `status.json`. Serve this directory with any static host, including GitHub Pages or Wisp. It needs no credentials or build step.

For local preview, build a snapshot with `uv run scripts/maintenance_status.py .local/status-preview`, then run `uv run python -m http.server 4174 --directory .local/status-preview` from the repository root. The collector copies the dashboard assets alongside the data.

The browser checks for a new snapshot every five minutes. This does not change the collector's twice-daily publication schedule. A snapshot older than 24 hours displays a warning. Public contributor information stays factual; private triage judgments are not loaded.
