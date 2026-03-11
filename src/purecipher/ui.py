"""Server-rendered UI for the PureCipher registry."""

from __future__ import annotations

import html
import json
from typing import Any
from urllib.parse import quote, urlencode

SAMPLE_MANIFEST_JSON = json.dumps(
    {
        "tool_name": "weather-lookup",
        "version": "1.0.0",
        "author": "acme",
        "description": "Fetch current weather for a city.",
        "permissions": ["network_access"],
        "data_flows": [
            {
                "source": "input.city",
                "destination": "output.forecast",
                "classification": "public",
                "description": "City name is sent to the weather provider.",
            }
        ],
        "resource_access": [
            {
                "resource_pattern": "https://api.weather.example/*",
                "access_type": "read",
                "description": "Call weather provider endpoint.",
                "classification": "public",
            }
        ],
        "tags": ["weather", "api"],
    },
    indent=2,
)

BASE_STYLES = """
:root {
  --bg: #f5f1e8;
  --panel: #fffdf8;
  --ink: #17212b;
  --muted: #5f6b76;
  --line: #d8d2c8;
  --accent: #165f55;
  --accent-soft: #edf5f3;
  --accent-strong: #0f4e46;
  --danger: #8f3f31;
  --danger-soft: #fbeae6;
  --success: #0f6e48;
  --success-soft: #ebf6ef;
}

html {
  scroll-behavior: smooth;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background:
    radial-gradient(circle at top left, rgba(22, 95, 85, 0.08), transparent 24%),
    linear-gradient(180deg, #f8f5ee 0%, var(--bg) 100%);
  color: var(--ink);
  font-family: "Avenir Next", "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

a {
  color: var(--accent);
  text-decoration-thickness: 1.5px;
  text-underline-offset: 0.18em;
}

a:hover {
  color: var(--accent-strong);
}

::selection {
  background: rgba(22, 95, 85, 0.16);
}

code,
pre {
  font-family: "SFMono-Regular", Menlo, Consolas, monospace;
}

.shell {
  max-width: 1240px;
  margin: 0 auto;
  padding: 28px 20px 40px;
}

.topbar,
.auth-panel {
  background: rgba(255, 253, 248, 0.9);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: 0 10px 30px rgba(23, 33, 43, 0.06);
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 16px 18px;
  margin-bottom: 18px;
  position: sticky;
  top: 12px;
  z-index: 20;
  backdrop-filter: blur(14px);
}

.brand-lockup,
.session-strip {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 14px;
  background: linear-gradient(145deg, #165f55, #1d7e72);
  color: #fff;
  font-size: 0.82rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.brand-name {
  font-family: "Iowan Old Style", Georgia, serif;
  font-size: 1.05rem;
  font-weight: 700;
}

.brand-note {
  color: var(--muted);
  font-size: 0.84rem;
}

.nav-links {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.nav-link {
  display: inline-flex;
  align-items: center;
  padding: 9px 12px;
  border-radius: 999px;
  border: 1px solid transparent;
  color: var(--muted);
  text-decoration: none;
  white-space: nowrap;
  transition: background 160ms ease, border-color 160ms ease, color 160ms ease;
}

.nav-link:hover,
.nav-link.is-current {
  border-color: var(--line);
  background: #fff;
  color: var(--ink);
}

.session-strip {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.session-meta {
  text-align: right;
}

.session-meta strong {
  display: block;
}

.role-pill {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 0.74rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.hero,
.panel,
.catalog-item,
.notice,
.metric,
.detail-box,
.recipe,
.listing-chip {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: 0 10px 30px rgba(23, 33, 43, 0.06);
}

.hero,
.panel {
  padding: 24px;
}

.hero {
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(circle at top right, rgba(22, 95, 85, 0.11), transparent 28%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.88), rgba(255, 253, 248, 0.96));
}

.hero::after {
  content: "";
  position: absolute;
  inset: auto -12% -42% auto;
  width: 320px;
  height: 320px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(22, 95, 85, 0.08), transparent 64%);
  pointer-events: none;
}

.eyebrow {
  margin-bottom: 10px;
  color: var(--accent);
  font-size: 0.76rem;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

h1,
h2,
h3 {
  margin: 0;
  font-family: "Iowan Old Style", Georgia, serif;
}

h1 {
  font-size: clamp(2rem, 4vw, 3.3rem);
  line-height: 0.98;
}

h2 {
  font-size: clamp(1.5rem, 2.4vw, 2rem);
}

h3 {
  font-size: 1.05rem;
}

.subtle,
.catalog-meta,
.catalog-tags,
.hero-copy,
.detail-note,
.footer-links {
  color: var(--muted);
}

.hero-copy {
  max-width: 760px;
  margin: 12px 0 0;
  line-height: 1.6;
}

.hero-actions,
.footer-links,
.action-row,
.chip-row,
.jump-links {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.hero-actions,
.footer-links,
.jump-links {
  margin-top: 18px;
}

.action-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink);
  text-decoration: none;
  transition:
    transform 160ms ease,
    border-color 160ms ease,
    box-shadow 160ms ease,
    background 160ms ease;
}

.action-link:hover,
button:hover {
  transform: translateY(-1px);
  border-color: rgba(22, 95, 85, 0.34);
  box-shadow: 0 10px 24px rgba(23, 33, 43, 0.12);
}

.jump-link {
  background: rgba(255, 255, 255, 0.78);
}

.micro-note {
  color: var(--muted);
  font-size: 0.84rem;
  line-height: 1.5;
}

.metrics,
.section-grid,
.detail-grid,
.install-grid,
.submit-grid,
.hero-cluster {
  display: grid;
  gap: 12px;
}

.metrics {
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  margin-top: 18px;
}

.hero-cluster {
  grid-template-columns: minmax(0, 1fr) minmax(300px, 0.76fr);
  align-items: start;
  gap: 18px;
}

.metric,
.detail-box {
  padding: 14px;
}

.metric .label,
.detail-box .label {
  color: var(--muted);
  font-size: 0.76rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.metric .value,
.detail-box .value {
  margin-top: 8px;
  font-size: 1.2rem;
  font-weight: 700;
}

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
  gap: 18px;
  margin-top: 18px;
}

.layout > .panel:last-child {
  position: sticky;
  top: 98px;
  align-self: start;
}

.page-stack {
  display: grid;
  gap: 18px;
  margin-top: 18px;
}

.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

form.search-form,
.submit-grid {
  display: grid;
  gap: 10px;
}

form.search-form {
  grid-template-columns: minmax(0, 1fr) 220px auto;
  margin-bottom: 14px;
}

.submit-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.field-stack {
  display: grid;
  gap: 6px;
}

.field-label {
  color: var(--muted);
  font-size: 0.74rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.results-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 16px;
}

.results-summary {
  color: var(--muted);
}

.auth-panel {
  padding: 18px;
}

.auth-grid,
.publisher-directory,
.role-grid {
  display: grid;
  gap: 12px;
}

.auth-grid,
.role-grid {
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}

.publisher-directory {
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
}

input,
select,
textarea,
button {
  width: 100%;
  font: inherit;
}

input,
select,
textarea {
  padding: 11px 12px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #fff;
  color: var(--ink);
}

input:focus,
select:focus,
textarea:focus {
  outline: none;
  border-color: rgba(22, 95, 85, 0.55);
  box-shadow: 0 0 0 4px rgba(22, 95, 85, 0.12);
}

textarea {
  min-height: 240px;
  resize: vertical;
  font-size: 0.88rem;
  line-height: 1.5;
}

button {
  padding: 11px 14px;
  border: 0;
  border-radius: 999px;
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  transition:
    transform 160ms ease,
    box-shadow 160ms ease,
    background 160ms ease;
}

.catalog {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.catalog-item {
  display: block;
  padding: 16px;
  color: inherit;
  text-decoration: none;
  transition:
    transform 180ms ease,
    border-color 180ms ease,
    box-shadow 180ms ease;
}

.catalog-item:hover,
.moderation-card:hover,
.publisher-card:hover,
.recipe:hover {
  transform: translateY(-2px);
  border-color: rgba(22, 95, 85, 0.38);
  box-shadow: 0 14px 32px rgba(23, 33, 43, 0.1);
}

.catalog-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.catalog-actions {
  margin-top: 12px;
}

.catalog-description {
  margin: 10px 0;
  line-height: 1.55;
}

.pill,
.listing-chip {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 700;
}

.pill {
  background: var(--accent-soft);
  color: var(--accent);
}

.listing-chip {
  background: #fff;
  box-shadow: none;
}

.detail-stack {
  display: grid;
  gap: 10px;
}

.detail-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.issue-list,
.bullet-list {
  margin: 10px 0 0 18px;
  padding: 0;
}

.notice {
  padding: 12px 14px;
}

.notice-success {
  background: var(--success-soft);
  color: var(--success);
  border-color: rgba(15, 110, 72, 0.2);
}

.notice-error {
  background: var(--danger-soft);
  color: var(--danger);
  border-color: rgba(143, 63, 49, 0.2);
}

.empty {
  padding: 24px;
  border: 1px dashed var(--line);
  border-radius: 16px;
  color: var(--muted);
  text-align: center;
}

.section-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.section-card {
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.72);
}

.section-card p,
.detail-note {
  line-height: 1.6;
}

.definition-grid {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: 8px 12px;
  margin-top: 12px;
}

.definition-grid dt {
  color: var(--muted);
  font-weight: 700;
}

.definition-grid dd {
  margin: 0;
}

.code-block {
  overflow-x: auto;
  margin: 12px 0 0;
  padding: 14px;
  border-radius: 14px;
  background: #0f1720;
  color: #f8fafc;
  font-size: 0.84rem;
  line-height: 1.5;
}

.code-block::-webkit-scrollbar {
  height: 10px;
}

.code-block::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
}

.recipe {
  padding: 16px;
}

.recipe-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.install-grid {
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
}

.publisher-grid,
.queue-section-grid {
  display: grid;
  gap: 12px;
}

.dashboard-stack,
.publisher-highlight-grid,
.pulse-grid {
  display: grid;
  gap: 12px;
}

.pulse-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.mini-card {
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.78);
}

.mini-card .label {
  color: var(--muted);
  font-size: 0.74rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.mini-card .value {
  margin-top: 8px;
  font-size: 1.08rem;
  font-weight: 700;
}

.publisher-highlight-grid {
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}

.publisher-card {
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.76);
  transition:
    transform 180ms ease,
    border-color 180ms ease,
    box-shadow 180ms ease;
}

.compact-list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.compact-item {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.72);
}

.moderation-card {
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.72);
  transition:
    transform 180ms ease,
    border-color 180ms ease,
    box-shadow 180ms ease;
}

.moderation-form {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.moderation-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.moderation-actions button {
  width: auto;
  min-width: 120px;
}

.manifest-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

@media (max-width: 920px) {
  .topbar {
    flex-direction: column;
    align-items: flex-start;
    position: static;
  }

  .session-strip,
  .session-meta {
    justify-content: flex-start;
    text-align: left;
  }

  .hero-cluster,
  .layout,
  .section-grid,
  .manifest-columns,
  form.search-form,
  .submit-grid,
  .detail-grid {
    grid-template-columns: 1fr;
  }

  .layout > .panel:last-child {
    position: static;
  }

  .catalog {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .shell {
    padding: 18px 14px 28px;
  }

  .hero,
  .panel {
    padding: 18px;
  }

  .nav-links {
    width: 100%;
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-bottom: 2px;
    scrollbar-width: none;
  }

  .nav-links::-webkit-scrollbar {
    display: none;
  }

  .panel-head,
  .catalog-row,
  .compact-item,
  .recipe-head,
  .results-bar {
    flex-direction: column;
    align-items: flex-start;
  }

  .publisher-directory,
  .publisher-highlight-grid,
  .install-grid {
    grid-template-columns: 1fr;
  }

  .pulse-grid {
    grid-template-columns: 1fr;
  }

  .moderation-actions button,
  .action-link {
    width: 100%;
    justify-content: center;
  }

  .hero-actions,
  .footer-links,
  .action-row,
  .jump-links {
    display: grid;
    grid-template-columns: 1fr;
  }

  textarea {
    min-height: 200px;
  }
}

@media (max-width: 480px) {
  h1 {
    font-size: 1.8rem;
  }

  h2 {
    font-size: 1.35rem;
  }

  .metrics {
    grid-template-columns: 1fr;
  }

  .definition-grid {
    grid-template-columns: 1fr;
  }
}
"""


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _percent(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{round(value * 100)}%"
    return "n/a"


def _slug(value: str) -> str:
    return quote(value, safe="")


def _catalog_href(
    *,
    registry_prefix: str,
    query: str,
    min_certification: str,
) -> str:
    params = {}
    if query:
        params["q"] = query
    if min_certification:
        params["min_certification"] = min_certification
    if not params:
        return registry_prefix
    return f"{registry_prefix}?{urlencode(params)}"


def _tool_href(
    *,
    registry_prefix: str,
    tool_name: str,
    query: str,
    min_certification: str,
) -> str:
    params = {}
    if query:
        params["q"] = query
    if min_certification:
        params["min_certification"] = min_certification
    suffix = f"?{urlencode(params)}" if params else ""
    return f"{registry_prefix}/listings/{_slug(tool_name)}{suffix}"


def _publisher_href(*, registry_prefix: str, publisher_id: str) -> str:
    return f"{registry_prefix}/publishers/{_slug(publisher_id)}"


def _publisher_index_href(*, registry_prefix: str) -> str:
    return f"{registry_prefix}/publishers?view=html"


def _login_href(*, registry_prefix: str, next_path: str = "") -> str:
    if not next_path:
        return f"{registry_prefix}/login"
    return f"{registry_prefix}/login?{urlencode({'next': next_path})}"


def _logout_href(*, registry_prefix: str, next_path: str = "") -> str:
    if not next_path:
        return f"{registry_prefix}/logout"
    return f"{registry_prefix}/logout?{urlencode({'next': next_path})}"


def _pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def _render_topbar(
    *,
    registry_prefix: str,
    auth_enabled: bool,
    session: dict[str, Any] | None,
    current_page: str,
    current_path: str,
) -> str:
    nav_items = [
        ("catalog", "Catalog", registry_prefix),
        (
            "publishers",
            "Publishers",
            _publisher_index_href(registry_prefix=registry_prefix),
        ),
        ("review", "Review", f"{registry_prefix}/review"),
    ]
    nav_html = "".join(
        f'<a class="nav-link{" is-current" if page == current_page else ""}" href="{_escape(href)}">{_escape(label)}</a>'
        for page, label, href in nav_items
    )

    if not auth_enabled:
        session_html = (
            '<div class="session-strip">'
            '<span class="role-pill">Public Registry</span>'
            '<div class="session-meta"><strong>Open access</strong>'
            '<span class="brand-note">Catalog browsing and submission are public.</span></div>'
            "</div>"
        )
    elif session is None:
        session_html = (
            '<div class="session-strip">'
            '<span class="role-pill">Auth Enabled</span>'
            '<a class="action-link" href="'
            f'{_escape(_login_href(registry_prefix=registry_prefix, next_path=current_path))}">Sign in</a>'
            "</div>"
        )
    else:
        session_html = (
            '<div class="session-strip">'
            f'<span class="role-pill">{_escape(session.get("role", "viewer"))}</span>'
            '<div class="session-meta">'
            f"<strong>{_escape(session.get('display_name') or session.get('username') or 'Authenticated user')}</strong>"
            f'<span class="brand-note">{_escape(session.get("username") or "")}</span>'
            "</div>"
            f'<a class="action-link" href="{_escape(_logout_href(registry_prefix=registry_prefix, next_path=current_path))}">Sign out</a>'
            "</div>"
        )

    return f"""
    <header class="topbar">
      <div class="brand-lockup">
        <div class="brand-mark">PC</div>
        <div>
          <div class="brand-name">PureCipher Secured MCP Registry</div>
          <div class="brand-note">Verified catalog, install recipes, and moderated publishing.</div>
        </div>
      </div>
      <nav class="nav-links">{nav_html}</nav>
      {session_html}
    </header>
    """


def _render_chip_row(values: list[str]) -> str:
    if not values:
        return '<div class="detail-note">None declared.</div>'
    return "".join(
        f'<span class="listing-chip">{_escape(value)}</span>' for value in values
    )


def _render_bullet_list(values: list[str]) -> str:
    if not values:
        return '<div class="detail-note">None declared.</div>'
    items = "".join(f"<li>{_escape(value)}</li>" for value in values)
    return f'<ul class="bullet-list">{items}</ul>'


def _render_verification_issues(verification: dict[str, Any]) -> str:
    issues = verification.get("issues") or []
    if not issues:
        return "<li>No verification issues reported.</li>"
    return "".join(f"<li>{_escape(issue)}</li>" for issue in issues)


def _render_data_flows(manifest: dict[str, Any]) -> str:
    flows = manifest.get("data_flows") or []
    if not flows:
        return '<div class="detail-note">No data flows declared.</div>'
    items = []
    for flow in flows:
        items.append(
            '<div class="detail-box">'
            f'<div class="label">{_escape(flow.get("classification", "internal"))}</div>'
            f'<div class="value">{_escape(flow.get("source", "unknown"))} -> {_escape(flow.get("destination", "unknown"))}</div>'
            f'<div class="detail-note">{_escape(flow.get("description", "No description provided."))}</div>'
            "</div>"
        )
    return "".join(items)


def _render_resource_access(manifest: dict[str, Any]) -> str:
    access_items = manifest.get("resource_access") or []
    if not access_items:
        return '<div class="detail-note">No resource access declared.</div>'
    items = []
    for access in access_items:
        items.append(
            '<div class="detail-box">'
            f'<div class="label">{_escape(access.get("access_type", "read"))}</div>'
            f'<div class="value">{_escape(access.get("resource_pattern", "unknown"))}</div>'
            f'<div class="detail-note">{_escape(access.get("description", "No description provided."))}</div>'
            "</div>"
        )
    return "".join(items)


def _render_catalog(
    *,
    registry_prefix: str,
    catalog: dict[str, Any],
    query: str,
    min_certification: str,
) -> str:
    tools = list(catalog.get("tools", []))
    if not tools:
        return '<div class="empty">No verified tools match the current filter.</div>'

    items: list[str] = []
    for tool in tools:
        href = _tool_href(
            registry_prefix=registry_prefix,
            tool_name=str(tool.get("tool_name", "")),
            query=query,
            min_certification=min_certification,
        )
        publisher_id = str(tool.get("publisher_id") or "")
        author = _escape(tool.get("author") or "unknown author")
        author_html = (
            f'<a href="{_escape(_publisher_href(registry_prefix=registry_prefix, publisher_id=publisher_id))}">{author}</a>'
            if publisher_id
            else author
        )
        tags = ", ".join(sorted(tool.get("tags", []))) or "no tags"
        categories = ", ".join(sorted(tool.get("categories", []))) or "uncategorized"
        items.append(
            f"""
            <a class="catalog-item" href="{_escape(href)}">
              <div class="catalog-row">
                <strong>{_escape(tool.get("display_name") or tool.get("tool_name"))}</strong>
                <span class="pill">{_escape(tool.get("certification_level") or "uncertified")}</span>
              </div>
              <div class="catalog-meta">
                {author_html} ·
                v{_escape(tool.get("version") or "0.0.0")} ·
                trust {_percent((tool.get("trust_score") or {}).get("overall"))}
              </div>
              <p class="catalog-description">{_escape(tool.get("description") or "No description provided.")}</p>
              <div class="catalog-tags">Categories: {_escape(categories)}</div>
              <div class="catalog-tags">Tags: {_escape(tags)}</div>
              <div class="catalog-actions">
                <span class="action-link">Open Listing</span>
              </div>
            </a>
            """
        )
    return "\n".join(items)


def _render_detail_preview(
    *,
    detail: dict[str, Any] | None,
    registry_prefix: str,
    query: str,
    min_certification: str,
) -> str:
    if not detail:
        return (
            '<div class="empty">Open a verified listing to inspect attestation, '
            "trust, and install recipes.</div>"
        )

    trust = detail.get("trust_score") or {}
    verification = detail.get("verification") or {}
    tool_name = str(detail.get("tool_name") or "")
    listing_href = _tool_href(
        registry_prefix=registry_prefix,
        tool_name=tool_name,
        query=query,
        min_certification=min_certification,
    )
    install_href = f"{registry_prefix}/install/{_slug(tool_name)}"
    return f"""
    <div class="detail-stack">
      <div class="detail-box">
        <div class="label">Selected Listing</div>
        <div class="value">{_escape(detail.get("display_name") or tool_name)}</div>
        <div class="detail-note">{_escape(detail.get("description") or "No description provided.")}</div>
      </div>
      <div class="detail-grid">
        <div class="detail-box">
          <div class="label">Certification</div>
          <div class="value">{_escape(detail.get("certification_level") or "uncertified")}</div>
        </div>
        <div class="detail-box">
          <div class="label">Trust</div>
          <div class="value">{_percent(trust.get("overall"))}</div>
        </div>
        <div class="detail-box">
          <div class="label">Verification</div>
          <div class="value">{"valid" if verification.get("valid") else "needs review"}</div>
        </div>
        <div class="detail-box">
          <div class="label">Installs</div>
          <div class="value">{_escape(detail.get("active_installs") or 0)}</div>
        </div>
      </div>
      <div class="action-row">
        <a class="action-link" href="{_escape(listing_href)}">Open full listing</a>
        <a class="action-link" href="{_escape(install_href)}">Install recipes JSON</a>
      </div>
    </div>
    """


def _render_submission_notice(
    *,
    submission_title: str | None,
    submission_body: str | None,
    submission_is_error: bool,
) -> str:
    if not submission_title:
        return (
            '<div class="notice">Submit a manifest to publish a verified listing.</div>'
        )

    tone = "notice-error" if submission_is_error else "notice-success"
    body = f"<div>{_escape(submission_body)}</div>" if submission_body else ""
    return f'<div class="notice {tone}"><strong>{_escape(submission_title)}</strong>{body}</div>'


def _render_optional_notice(
    *,
    notice_title: str | None,
    notice_body: str | None,
    notice_is_error: bool,
) -> str:
    if not notice_title:
        return ""
    tone = "notice-error" if notice_is_error else "notice-success"
    body = f"<div>{_escape(notice_body)}</div>" if notice_body else ""
    return f'<div class="notice {tone}"><strong>{_escape(notice_title)}</strong>{body}</div>'


def _render_dashboard_snapshot(
    *,
    registry_prefix: str,
    health: dict[str, Any],
    queue: dict[str, Any],
    detail: dict[str, Any] | None,
    query: str,
    min_certification: str,
) -> str:
    counts = queue.get("counts") or {}
    pending_items = list((queue.get("sections") or {}).get("pending_review") or [])[:3]
    suspended_items = list((queue.get("sections") or {}).get("suspended") or [])[:3]

    def _render_compact_items(items: list[dict[str, Any]], empty_label: str) -> str:
        if not items:
            return f'<div class="detail-note">{_escape(empty_label)}</div>'
        return "".join(
            f"""
            <div class="compact-item">
              <a href="{
                _escape(
                    _tool_href(
                        registry_prefix=registry_prefix,
                        tool_name=str(item.get("tool_name", "")),
                        query=query,
                        min_certification=min_certification,
                    )
                )
            }">{_escape(item.get("display_name") or item.get("tool_name"))}</a>
              <span class="pill">{_escape(item.get("status") or "unknown")}</span>
            </div>
            """
            for item in items
        )

    return f"""
    <div class="dashboard-stack">
      <div class="pulse-grid">
        <div class="mini-card">
          <div class="label">Published</div>
          <div class="value">{
        _escape(counts.get("published", health.get("verified_tools", 0)))
    }</div>
        </div>
        <div class="mini-card">
          <div class="label">Pending Review</div>
          <div class="value">{
        _escape(counts.get("pending_review", health.get("pending_review", 0)))
    }</div>
        </div>
        <div class="mini-card">
          <div class="label">Suspended</div>
          <div class="value">{_escape(counts.get("suspended", 0))}</div>
        </div>
        <div class="mini-card">
          <div class="label">Moderation</div>
          <div class="value">{
        _escape("enabled" if queue.get("require_moderation") else "open publish")
    }</div>
        </div>
      </div>
      <div class="section-card">
        <h3>Queue Snapshot</h3>
        <div class="detail-note">Pending items and suspended listings visible at a glance.</div>
        <div class="detail-note" style="margin-top: 12px;">Pending review</div>
        <div class="compact-list">
          {_render_compact_items(pending_items, "No pending submissions right now.")}
        </div>
        <div class="detail-note" style="margin-top: 14px;">Suspended</div>
        <div class="compact-list">
          {_render_compact_items(suspended_items, "No suspended listings right now.")}
        </div>
        <div class="action-row" style="margin-top: 12px;">
          <a class="action-link" href="{
        _escape(f"{registry_prefix}/review")
    }">Open moderation queue</a>
          <a class="action-link" href="{
        _escape(f"{registry_prefix}/review/submissions")
    }">Queue JSON</a>
        </div>
      </div>
      <div class="section-card">
        <h3>Focused Listing</h3>
        <div class="detail-note">Use this panel to jump from search results into a full listing review.</div>
        {
        _render_detail_preview(
            detail=detail,
            registry_prefix=registry_prefix,
            query=query,
            min_certification=min_certification,
        )
    }
      </div>
    </div>
    """


def _render_featured_publishers(
    *,
    registry_prefix: str,
    publishers: dict[str, Any],
) -> str:
    items = list(publishers.get("publishers") or [])
    if not items:
        return '<div class="empty">No publisher profiles are available yet.</div>'

    return "".join(
        f"""
        <article class="publisher-card">
          <div class="catalog-row">
            <strong><a href="{_escape(_publisher_href(registry_prefix=registry_prefix, publisher_id=str(item.get("publisher_id", ""))))}">{_escape(item.get("display_name") or item.get("publisher_id"))}</a></strong>
            <span class="pill">{_escape(item.get("listing_count", 0))} listings</span>
          </div>
          <div class="catalog-meta">
            trust {_percent(item.get("average_trust"))} ·
            updated {_escape(item.get("latest_activity") or "n/a")}
          </div>
          <div class="detail-note" style="margin-top: 10px;">Categories: {_escape(", ".join(item.get("categories") or []) or "none")}</div>
          <div class="detail-note">Tags: {_escape(", ".join(item.get("tags") or []) or "none")}</div>
          <div class="action-row" style="margin-top: 12px;">
            <a class="action-link" href="{_escape(_publisher_href(registry_prefix=registry_prefix, publisher_id=str(item.get("publisher_id", ""))))}">Open publisher</a>
          </div>
        </article>
        """
        for item in items
    )


def create_registry_ui_html(
    *,
    server_name: str,
    registry_prefix: str = "/registry",
    health: dict[str, Any],
    catalog: dict[str, Any],
    publishers: dict[str, Any],
    queue: dict[str, Any],
    auth_enabled: bool,
    session: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
    query: str = "",
    min_certification: str = "",
    manifest_text: str = SAMPLE_MANIFEST_JSON,
    display_name: str = "Weather Lookup",
    categories: str = "network,utility",
    requested_level: str = "basic",
    page_notice_title: str | None = None,
    page_notice_body: str | None = None,
    page_notice_is_error: bool = False,
    submission_title: str | None = None,
    submission_body: str | None = None,
    submission_is_error: bool = False,
) -> str:
    """Render the registry catalog and submission UI."""

    page_title = "PureCipher Secured MCP Registry"
    min_level_options = "".join(
        f'<option value="{value}"{" selected" if value == min_certification else ""}>{label}</option>'
        for value, label in (
            ("", "Minimum certification"),
            ("basic", "Basic"),
            ("standard", "Standard"),
            ("strict", "Strict"),
        )
    )
    requested_level_options = "".join(
        f'<option value="{value}"{" selected" if value == requested_level else ""}>{label}</option>'
        for value, label in (
            ("basic", "Basic"),
            ("standard", "Standard"),
            ("strict", "Strict"),
            ("self_attested", "Self Attested"),
        )
    )
    page_notice_html = _render_optional_notice(
        notice_title=page_notice_title,
        notice_body=page_notice_body,
        notice_is_error=page_notice_is_error,
    )
    can_submit = bool(session and session.get("can_submit"))
    show_submit_form = not auth_enabled or can_submit
    result_summary_bits = [f"{_escape(catalog.get('count', 0))} listings"]
    if query:
        result_summary_bits.append(f'query "{_escape(query)}"')
    if min_certification:
        result_summary_bits.append(f"min {_escape(min_certification)}")
    result_summary = " · ".join(result_summary_bits)
    if show_submit_form:
        submission_panel = f"""
        <form method="post" action="{_escape(registry_prefix)}" style="margin-top: 14px;">
          <div class="submit-grid">
            <label class="field-stack">
              <span class="field-label">Display Name</span>
              <input name="display_name" value="{_escape(display_name)}" placeholder="Display name" />
            </label>
            <label class="field-stack">
              <span class="field-label">Categories</span>
              <input name="categories" value="{_escape(categories)}" placeholder="Categories (comma separated)" />
            </label>
            <label class="field-stack">
              <span class="field-label">Requested Level</span>
              <select name="requested_level">{requested_level_options}</select>
            </label>
          </div>
          <div class="micro-note" style="margin-top: 10px;">
            Submit a manifest with runtime metadata if you want install recipes and client config blocks to render on the listing page.
          </div>
          <textarea name="manifest" style="margin-top: 10px;">{_escape(manifest_text)}</textarea>
          <button type="submit" style="margin-top: 12px;">Submit To Registry</button>
        </form>
        """
    elif session is None:
        submission_panel = f"""
        <div class="auth-panel" style="margin-top: 14px;">
          <h3>Sign in to publish</h3>
          <p class="detail-note" style="margin-top: 10px;">
            JWT auth is enabled for this registry. Use a publisher, reviewer, or admin account before submitting a manifest.
          </p>
          <div class="action-row" style="margin-top: 12px;">
            <a class="action-link" href="{_escape(_login_href(registry_prefix=registry_prefix, next_path=f"{registry_prefix}#submit"))}">Open login</a>
          </div>
        </div>
        """
    else:
        submission_panel = f"""
        <div class="auth-panel" style="margin-top: 14px;">
          <h3>Submission blocked for current role</h3>
          <p class="detail-note" style="margin-top: 10px;">
            Signed in as {_escape(session.get("role") or "viewer")}. Publisher, reviewer, or admin role required to submit new listings.
          </p>
          <div class="action-row" style="margin-top: 12px;">
            <a class="action-link" href="{_escape(_logout_href(registry_prefix=registry_prefix, next_path=f"{registry_prefix}#submit"))}">Switch account</a>
          </div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{page_title}</title>
    <style>{BASE_STYLES}</style>
  </head>
  <body>
    <main class="shell">
      {
        _render_topbar(
            registry_prefix=registry_prefix,
            auth_enabled=auth_enabled,
            session=session,
            current_page="catalog",
            current_path=registry_prefix,
        )
    }
      <section class="hero">
        <div class="eyebrow">PureCipher Registry</div>
        <div class="hero-cluster">
          <div>
            <h1>{page_title}</h1>
            <p class="subtle" style="margin-top: 8px;">Server: {
        _escape(server_name)
    }</p>
            <p class="hero-copy">
              Verified registry for SecureMCP tools. Search the catalog, open each listing as a dedicated page,
              inspect trust and attestation data, and move from publisher identity to install-ready listings without leaving the browser.
            </p>
            <div class="jump-links">
              <a class="action-link jump-link" href="#catalog">Explore catalog</a>
              <a class="action-link jump-link" href="#publishers">Browse publishers</a>
              <a class="action-link jump-link" href="#submit">Submit manifest</a>
            </div>
          </div>
          <div class="metrics">
            <div class="metric">
              <div class="label">Status</div>
              <div class="value">{_escape(health.get("status", "unknown"))}</div>
            </div>
            <div class="metric">
              <div class="label">Verified Tools</div>
              <div class="value">{_escape(health.get("verified_tools", 0))}</div>
            </div>
            <div class="metric">
              <div class="label">Minimum Level</div>
              <div class="value">{
        _escape(health.get("minimum_certification", "n/a"))
    }</div>
            </div>
            <div class="metric">
              <div class="label">Pending Review</div>
              <div class="value">{_escape(health.get("pending_review", 0))}</div>
            </div>
          </div>
        </div>
        <div class="footer-links">
          <a href="{_escape(registry_prefix)}">Catalog home</a>
          <a href="{
        _escape(_publisher_index_href(registry_prefix=registry_prefix))
    }">Publisher directory</a>
          <a href="{_escape(f"{registry_prefix}/review")}">Review queue</a>
          <a href="{_escape(f"{registry_prefix}/session")}">Session API</a>
          <a href="{_escape(f"{registry_prefix}/tools")}">Catalog API</a>
          <a href="/security/health">/security/health</a>
        </div>
      </section>

      {page_notice_html}

      <section class="layout">
        <section class="panel" id="catalog">
          <div class="panel-head">
            <div>
              <h2>Verified Catalog</h2>
              <div class="subtle">Search by tool name, author, description, or tags.</div>
            </div>
          </div>
          <form class="search-form" method="get" action="{_escape(registry_prefix)}">
            <label class="field-stack">
              <span class="field-label">Search</span>
              <input name="q" value="{
        _escape(query)
    }" placeholder="Search verified tools" />
            </label>
            <label class="field-stack">
              <span class="field-label">Minimum Certification</span>
              <select name="min_certification">{min_level_options}</select>
            </label>
            <button type="submit">Apply Filters</button>
          </form>
          <div class="results-bar">
            <div class="results-summary">{result_summary}</div>
            <div class="micro-note">Open any card to inspect attestation, trust, and install recipes.</div>
          </div>
          <div class="catalog">
            {
        _render_catalog(
            registry_prefix=registry_prefix,
            catalog=catalog,
            query=query,
            min_certification=min_certification,
        )
    }
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div>
              <h2>Registry Dashboard</h2>
              <div class="subtle">Moderation state, queue pulse, and focused listing review.</div>
            </div>
          </div>
          {
        _render_dashboard_snapshot(
            registry_prefix=registry_prefix,
            health=health,
            queue=queue,
            detail=detail,
            query=query,
            min_certification=min_certification,
        )
    }
        </section>
      </section>

      <section class="panel" id="publishers" style="margin-top: 18px;">
        <div class="panel-head">
          <div>
            <h2>Featured Publishers</h2>
            <div class="subtle">The registry identities currently driving the most visible catalog coverage.</div>
          </div>
          <a class="action-link" href="{
        _escape(_publisher_index_href(registry_prefix=registry_prefix))
    }">Open publisher directory</a>
        </div>
        <div class="publisher-highlight-grid">
          {
        _render_featured_publishers(
            registry_prefix=registry_prefix,
            publishers=publishers,
        )
    }
        </div>
      </section>

      <section class="panel" id="submit" style="margin-top: 18px;">
        <div class="panel-head">
          <div>
            <h2>Submit Manifest</h2>
            <div class="subtle">Browser submission posts to the registry UI. The JSON API remains under /registry/submit.</div>
          </div>
        </div>
        {
        _render_submission_notice(
            submission_title=submission_title,
            submission_body=submission_body,
            submission_is_error=submission_is_error,
        )
    }
        {submission_panel}
      </section>
    </main>
  </body>
</html>"""


def create_listing_detail_html(
    *,
    server_name: str,
    registry_prefix: str,
    detail: dict[str, Any],
    install_recipes: list[dict[str, Any]],
    auth_enabled: bool,
    session: dict[str, Any] | None = None,
    query: str = "",
    min_certification: str = "",
) -> str:
    """Render a dedicated listing page with install and attestation detail."""

    page_title = "PureCipher Secured MCP Registry"
    tool_name = str(detail.get("tool_name") or "")
    display_name = str(detail.get("display_name") or tool_name)
    publisher_id = str(detail.get("publisher_id") or "")
    publisher_name = str(detail.get("author") or "unknown")
    manifest = detail.get("manifest") or {}
    attestation = detail.get("attestation") or {}
    trust = detail.get("trust_score") or {}
    verification = detail.get("verification") or {}
    categories = sorted(detail.get("categories") or [])
    tags = sorted(detail.get("tags") or [])
    back_href = _catalog_href(
        registry_prefix=registry_prefix,
        query=query,
        min_certification=min_certification,
    )
    publisher_href = _publisher_href(
        registry_prefix=registry_prefix,
        publisher_id=publisher_id,
    )
    install_href = f"{registry_prefix}/install/{_slug(tool_name)}"
    detail_api_href = f"{registry_prefix}/tools/{_slug(tool_name)}"

    recipe_html = (
        "".join(
            f"""
            <article class="recipe">
              <div class="recipe-head">
                <h3>{_escape(recipe.get("title", "Install Recipe"))}</h3>
                <span class="pill">{_escape(recipe.get("format", "text"))}</span>
              </div>
              <p class="detail-note">{_escape(recipe.get("description", ""))}</p>
              <pre class="code-block">{_escape(recipe.get("content", ""))}</pre>
            </article>
            """
            for recipe in install_recipes
        )
        if install_recipes
        else '<div class="empty">No install recipe metadata has been published for this listing yet.</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{display_name} · {page_title}</title>
    <style>{BASE_STYLES}</style>
  </head>
  <body>
    <main class="shell">
      {
        _render_topbar(
            registry_prefix=registry_prefix,
            auth_enabled=auth_enabled,
            session=session,
            current_page="catalog",
            current_path=_tool_href(
                registry_prefix=registry_prefix,
                tool_name=tool_name,
                query=query,
                min_certification=min_certification,
            ),
        )
    }
      <section class="hero">
        <div class="eyebrow">Verified Listing</div>
        <h1>{_escape(display_name)}</h1>
        <p class="subtle" style="margin-top: 8px;">Server: {
        _escape(server_name)
    } · Publisher: <a href="{_escape(publisher_href)}">{_escape(publisher_name)}</a></p>
        <p class="hero-copy">{
        _escape(detail.get("description") or "No description provided.")
    }</p>
        <div class="jump-links">
          <span class="listing-chip">{
        _escape(detail.get("certification_level") or "uncertified")
    }</span>
          <span class="listing-chip">trust {_percent(trust.get("overall"))}</span>
          <span class="listing-chip">v{_escape(detail.get("version") or "0.0.0")}</span>
        </div>
        <div class="hero-actions">
          <a class="action-link" href="{_escape(back_href)}">Back to catalog</a>
          <a class="action-link" href="{_escape(publisher_href)}">Publisher profile</a>
          <a class="action-link" href="{_escape(detail_api_href)}">Listing JSON</a>
          <a class="action-link" href="{_escape(install_href)}">Install recipes JSON</a>
        </div>
        <div class="metrics">
          <div class="metric">
            <div class="label">Certification</div>
            <div class="value">{
        _escape(detail.get("certification_level") or "uncertified")
    }</div>
          </div>
          <div class="metric">
            <div class="label">Trust</div>
            <div class="value">{_percent(trust.get("overall"))}</div>
          </div>
          <div class="metric">
            <div class="label">Installs</div>
            <div class="value">{_escape(detail.get("active_installs") or 0)}</div>
          </div>
          <div class="metric">
            <div class="label">Verification</div>
            <div class="value">{
        "valid" if verification.get("valid") else "needs review"
    }</div>
          </div>
        </div>
      </section>

      <section class="page-stack">
        <section class="panel">
          <div class="panel-head">
            <div>
              <h2>Overview</h2>
              <div class="subtle">Publisher identity, discovery metadata, and published links.</div>
            </div>
          </div>
          <div class="section-grid">
            <div class="section-card">
              <h3>Listing Metadata</h3>
              <dl class="definition-grid">
                <dt>Tool Name</dt>
                <dd>{_escape(tool_name)}</dd>
                <dt>Version</dt>
                <dd>{_escape(detail.get("version") or "0.0.0")}</dd>
                <dt>Publisher</dt>
                <dd><a href="{_escape(publisher_href)}">{
        _escape(publisher_name)
    }</a></dd>
                <dt>License</dt>
                <dd>{_escape(detail.get("license") or "unlisted")}</dd>
                <dt>Source</dt>
                <dd>{
        f'<a href="{_escape(detail["source_url"])}">{_escape(detail["source_url"])}</a>'
        if detail.get("source_url")
        else "unlisted"
    }</dd>
                <dt>Homepage</dt>
                <dd>{
        f'<a href="{_escape(detail["homepage_url"])}">{_escape(detail["homepage_url"])}</a>'
        if detail.get("homepage_url")
        else "unlisted"
    }</dd>
              </dl>
            </div>
            <div class="section-card">
              <h3>Discovery</h3>
              <div class="label">Categories</div>
              <div class="chip-row" style="margin-top: 10px;">{
        _render_chip_row(categories)
    }</div>
              <div class="label" style="margin-top: 16px;">Tags</div>
              <div class="chip-row" style="margin-top: 10px;">{
        _render_chip_row(tags)
    }</div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div>
              <h2>Security Profile</h2>
              <div class="subtle">Manifest-declared permissions, data flows, and resource access.</div>
            </div>
          </div>
          <div class="section-grid">
            <div class="section-card">
              <h3>Permissions</h3>
              {_render_bullet_list(sorted(manifest.get("permissions") or []))}
              <dl class="definition-grid">
                <dt>Idempotent</dt>
                <dd>{_escape(manifest.get("idempotent", False))}</dd>
                <dt>Deterministic</dt>
                <dd>{_escape(manifest.get("deterministic", False))}</dd>
                <dt>Consent</dt>
                <dd>{_escape(manifest.get("requires_consent", False))}</dd>
                <dt>Max Runtime</dt>
                <dd>{_escape(manifest.get("max_execution_time_seconds", "n/a"))}s</dd>
              </dl>
            </div>
            <div class="section-card">
              <h3>Data Flows</h3>
              <div class="detail-stack">{_render_data_flows(manifest)}</div>
            </div>
          </div>
          <div class="section-card" style="margin-top: 12px;">
            <h3>Resource Access</h3>
            <div class="detail-stack">{_render_resource_access(manifest)}</div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div>
              <h2>Attestation</h2>
              <div class="subtle">Registry verification state and signed certification claims.</div>
            </div>
          </div>
          <div class="section-grid">
            <div class="section-card">
              <h3>Verification State</h3>
              <dl class="definition-grid">
                <dt>Issuer</dt>
                <dd>{_escape(attestation.get("issuer_id") or "unknown")}</dd>
                <dt>Status</dt>
                <dd>{_escape(attestation.get("status") or "unknown")}</dd>
                <dt>Issued</dt>
                <dd>{_escape(attestation.get("issued_at") or "n/a")}</dd>
                <dt>Expires</dt>
                <dd>{_escape(attestation.get("expires_at") or "n/a")}</dd>
                <dt>Digest</dt>
                <dd><code>{
        _escape(attestation.get("manifest_digest") or "n/a")
    }</code></dd>
              </dl>
            </div>
            <div class="section-card">
              <h3>Verification Notes</h3>
              <ul class="issue-list">{_render_verification_issues(verification)}</ul>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div>
              <h2>Install Recipes</h2>
              <div class="subtle">Publisher-provided connection metadata rendered into copyable snippets.</div>
            </div>
          </div>
          <div class="install-grid">{recipe_html}</div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div>
              <h2>Manifest & Attestation JSON</h2>
              <div class="subtle">Raw registry records for independent inspection.</div>
            </div>
          </div>
          <div class="manifest-columns">
            <div>
              <h3>Manifest</h3>
              <pre class="code-block">{_escape(_pretty_json(manifest))}</pre>
            </div>
            <div>
              <h3>Attestation</h3>
              <pre class="code-block">{_escape(_pretty_json(attestation))}</pre>
            </div>
          </div>
        </section>
      </section>
    </main>
  </body>
</html>"""


def _render_review_item(item: dict[str, Any], *, registry_prefix: str) -> str:
    tool_href = _tool_href(
        registry_prefix=registry_prefix,
        tool_name=str(item.get("tool_name", "")),
        query="",
        min_certification="",
    )
    publisher_href = _publisher_href(
        registry_prefix=registry_prefix,
        publisher_id=str(item.get("publisher_id", "")),
    )
    listing_id = _escape(item.get("listing_id", ""))
    actions = list(item.get("available_actions") or [])
    if not actions:
        action_buttons = (
            '<div class="detail-note">No actions available for this state.</div>'
        )
    else:
        action_buttons = "".join(
            f'<button type="submit" formaction="{_escape(f"{registry_prefix}/review/{listing_id}/{action}")}">{_escape(action.replace("-", " ").title())}</button>'
            for action in actions
        )

    return f"""
    <article class="moderation-card">
      <div class="catalog-row">
        <strong><a href="{_escape(tool_href)}">{_escape(item.get("display_name") or item.get("tool_name"))}</a></strong>
        <span class="pill">{_escape(item.get("status") or "unknown")}</span>
      </div>
      <div class="catalog-meta">
        <a href="{_escape(publisher_href)}">{_escape(item.get("author") or "unknown author")}</a> ·
        v{_escape(item.get("version") or "0.0.0")} ·
        trust {_percent(item.get("trust_score"))}
      </div>
      <div class="detail-note" style="margin-top: 10px;">
        Certification: {_escape(item.get("certification_level") or "uncertified")} ·
        Updated: {_escape(item.get("updated_at") or "n/a")}
      </div>
      <form class="moderation-form" method="post" action="{_escape(f"{registry_prefix}/review/{listing_id}/{actions[0] if actions else 'approve'}")}">
        <input name="moderator_id" value="purecipher-admin" placeholder="Moderator id" />
        <input name="reason" placeholder="Reason for this decision" />
        <div class="moderation-actions">{action_buttons}</div>
      </form>
    </article>
    """


def _render_review_section(
    *,
    title: str,
    subtitle: str,
    items: list[dict[str, Any]],
    registry_prefix: str,
) -> str:
    rendered_items = (
        "".join(
            _render_review_item(item, registry_prefix=registry_prefix) for item in items
        )
        if items
        else '<div class="empty">No listings in this section.</div>'
    )
    return f"""
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>{_escape(title)}</h2>
          <div class="subtle">{_escape(subtitle)}</div>
        </div>
      </div>
      <div class="queue-section-grid">{rendered_items}</div>
    </section>
    """


def create_publisher_index_html(
    *,
    server_name: str,
    registry_prefix: str,
    publishers: dict[str, Any],
    auth_enabled: bool,
    session: dict[str, Any] | None = None,
) -> str:
    """Render the publisher directory page."""

    page_title = "PureCipher Secured MCP Registry"
    items = list(publishers.get("publishers") or [])
    publisher_cards = (
        "".join(
            f"""
            <article class="publisher-card">
              <div class="catalog-row">
                <strong><a href="{_escape(_publisher_href(registry_prefix=registry_prefix, publisher_id=str(item.get("publisher_id", ""))))}">{_escape(item.get("display_name") or item.get("publisher_id"))}</a></strong>
                <span class="pill">{_escape(item.get("listing_count", 0))} listings</span>
              </div>
              <div class="catalog-meta">
                trust {_percent(item.get("average_trust"))} ·
                updated {_escape(item.get("latest_activity") or "n/a")}
              </div>
              <div class="detail-note" style="margin-top: 10px;">Categories: {_escape(", ".join(item.get("categories") or []) or "none")}</div>
              <div class="detail-note">Tags: {_escape(", ".join(item.get("tags") or []) or "none")}</div>
              <div class="action-row" style="margin-top: 12px;">
                <a class="action-link" href="{_escape(_publisher_href(registry_prefix=registry_prefix, publisher_id=str(item.get("publisher_id", ""))))}">Open publisher</a>
              </div>
            </article>
            """
            for item in items
        )
        if items
        else '<div class="empty">No publisher profiles are available yet.</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Publishers · {page_title}</title>
    <style>{BASE_STYLES}</style>
  </head>
  <body>
    <main class="shell">
      {
        _render_topbar(
            registry_prefix=registry_prefix,
            auth_enabled=auth_enabled,
            session=session,
            current_page="publishers",
            current_path=_publisher_index_href(registry_prefix=registry_prefix),
        )
    }
      <section class="hero">
        <div class="eyebrow">Publisher Directory</div>
        <h1>Registry Publishers</h1>
        <p class="subtle" style="margin-top: 8px;">Server: {
        _escape(server_name)
    } · Profiles: {_escape(publishers.get("count", 0))}</p>
        <p class="hero-copy">
          Public publisher identities derived from the verified catalog. Use this page to move laterally across registry publishers,
          then drill into each publisher profile for listing-level detail.
        </p>
        <div class="hero-actions">
          <a class="action-link" href="{_escape(registry_prefix)}">Back to catalog</a>
          <a class="action-link" href="{
        _escape(f"{registry_prefix}/publishers")
    }">Publisher API</a>
        </div>
      </section>

      <section class="panel" style="margin-top: 18px;">
        <div class="panel-head">
          <div>
            <h2>Featured Publisher Set</h2>
            <div class="subtle">Registry-wide view of publisher trust, coverage, and current listing volume.</div>
          </div>
        </div>
        <div class="publisher-directory">{publisher_cards}</div>
      </section>
    </main>
  </body>
</html>"""


def create_publisher_profile_html(
    *,
    server_name: str,
    registry_prefix: str,
    profile: dict[str, Any],
    auth_enabled: bool,
    session: dict[str, Any] | None = None,
) -> str:
    """Render a public publisher profile page."""

    page_title = "PureCipher Secured MCP Registry"
    listings = list(profile.get("listings") or [])
    summary = profile
    catalog = {"tools": listings}
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{_escape(summary.get("display_name") or "Publisher")} · {page_title}</title>
    <style>{BASE_STYLES}</style>
  </head>
  <body>
    <main class="shell">
      {
        _render_topbar(
            registry_prefix=registry_prefix,
            auth_enabled=auth_enabled,
            session=session,
            current_page="publishers",
            current_path=_publisher_href(
                registry_prefix=registry_prefix,
                publisher_id=str(summary.get("publisher_id") or ""),
            ),
        )
    }
      <section class="hero">
        <div class="eyebrow">Publisher Profile</div>
        <h1>{_escape(summary.get("display_name") or "Unknown Publisher")}</h1>
        <p class="subtle" style="margin-top: 8px;">Server: {
        _escape(server_name)
    } · Publisher ID: {_escape(summary.get("publisher_id") or "unknown")}</p>
        <p class="hero-copy">
          Public profile for a PureCipher publisher. Listings shown here are the currently published registry entries
          discoverable through the verified catalog.
        </p>
        <div class="hero-actions">
          <a class="action-link" href="{_escape(registry_prefix)}">Back to catalog</a>
          <a class="action-link" href="{
        _escape(_publisher_index_href(registry_prefix=registry_prefix))
    }">Publisher directory</a>
          <a class="action-link" href="{
        _escape(f"{registry_prefix}/publishers")
    }">Publisher index JSON</a>
        </div>
        <div class="jump-links">
          <span class="listing-chip">{
        _escape(summary.get("listing_count", 0))
    } listings</span>
          <span class="listing-chip">trust {
        _percent(summary.get("average_trust"))
    }</span>
          <span class="listing-chip">{
        _escape(summary.get("publisher_id") or "unknown")
    }</span>
        </div>
        <div class="metrics">
          <div class="metric">
            <div class="label">Published Listings</div>
            <div class="value">{_escape(summary.get("listing_count", 0))}</div>
          </div>
          <div class="metric">
            <div class="label">Average Trust</div>
            <div class="value">{_percent(summary.get("average_trust"))}</div>
          </div>
          <div class="metric">
            <div class="label">Categories</div>
            <div class="value">{_escape(len(summary.get("categories") or []))}</div>
          </div>
          <div class="metric">
            <div class="label">Latest Activity</div>
            <div class="value">{_escape(summary.get("latest_activity") or "n/a")}</div>
          </div>
        </div>
      </section>

      <section class="page-stack">
        <section class="panel">
          <div class="panel-head">
            <div>
              <h2>Publisher Snapshot</h2>
              <div class="subtle">Top-level profile metadata derived from published listings.</div>
            </div>
          </div>
          <div class="section-grid">
            <div class="section-card">
              <h3>Categories</h3>
              <div class="chip-row" style="margin-top: 10px;">{
        _render_chip_row(list(summary.get("categories") or []))
    }</div>
            </div>
            <div class="section-card">
              <h3>Tags</h3>
              <div class="chip-row" style="margin-top: 10px;">{
        _render_chip_row(list(summary.get("tags") or []))
    }</div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div>
              <h2>Published Listings</h2>
              <div class="subtle">Registry entries currently visible in the public catalog.</div>
            </div>
          </div>
          <div class="publisher-grid">
            {
        _render_catalog(
            registry_prefix=registry_prefix,
            catalog=catalog,
            query="",
            min_certification="",
        )
    }
          </div>
        </section>
      </section>
    </main>
  </body>
</html>"""


def create_review_queue_html(
    *,
    server_name: str,
    registry_prefix: str,
    queue: dict[str, Any],
    auth_enabled: bool,
    session: dict[str, Any] | None = None,
    notice_title: str | None = None,
    notice_body: str | None = None,
    notice_is_error: bool = False,
) -> str:
    """Render the moderation queue page."""

    sections = queue.get("sections") or {}
    counts = queue.get("counts") or {}
    page_title = "PureCipher Secured MCP Registry"
    notice_html = _render_optional_notice(
        notice_title=notice_title,
        notice_body=notice_body,
        notice_is_error=notice_is_error,
    )
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Moderation Queue · {page_title}</title>
    <style>{BASE_STYLES}</style>
  </head>
  <body>
    <main class="shell">
      {
        _render_topbar(
            registry_prefix=registry_prefix,
            auth_enabled=auth_enabled,
            session=session,
            current_page="review",
            current_path=f"{registry_prefix}/review",
        )
    }
      <section class="hero">
        <div class="eyebrow">Moderation Queue</div>
        <h1>Registry Review Queue</h1>
        <p class="subtle" style="margin-top: 8px;">Server: {
        _escape(server_name)
    } · Require moderation: {_escape(queue.get("require_moderation", False))}</p>
        <p class="hero-copy">
          Admin surface for pending submissions and live listings that may need intervention. Actions here write
          directly into the SecureMCP marketplace moderation log.
        </p>
        <div class="hero-actions">
          <a class="action-link" href="{_escape(registry_prefix)}">Back to catalog</a>
          <a class="action-link" href="{
        _escape(f"{registry_prefix}/review/submissions")
    }">Queue API</a>
        </div>
        <div class="metrics">
          <div class="metric">
            <div class="label">Pending Review</div>
            <div class="value">{_escape(counts.get("pending_review", 0))}</div>
          </div>
          <div class="metric">
            <div class="label">Published</div>
            <div class="value">{_escape(counts.get("published", 0))}</div>
          </div>
          <div class="metric">
            <div class="label">Suspended</div>
            <div class="value">{_escape(counts.get("suspended", 0))}</div>
          </div>
          <div class="metric">
            <div class="label">Generated</div>
            <div class="value">{_escape(queue.get("generated_at") or "n/a")}</div>
          </div>
        </div>
      </section>

      <section class="page-stack">
        {notice_html}
        {
        _render_review_section(
            title="Pending Review",
            subtitle="Approve, reject, or request changes for newly submitted listings.",
            items=list(sections.get("pending_review") or []),
            registry_prefix=registry_prefix,
        )
    }
        {
        _render_review_section(
            title="Published Listings",
            subtitle="Published tools remain manageable here if they need suspension.",
            items=list(sections.get("published") or []),
            registry_prefix=registry_prefix,
        )
    }
        {
        _render_review_section(
            title="Suspended Listings",
            subtitle="Restore suspended listings once they are cleared for publication.",
            items=list(sections.get("suspended") or []),
            registry_prefix=registry_prefix,
        )
    }
      </section>
    </main>
  </body>
</html>"""


def create_login_html(
    *,
    server_name: str,
    registry_prefix: str,
    auth_enabled: bool,
    session: dict[str, Any] | None = None,
    next_path: str = "/registry",
    notice_title: str | None = None,
    notice_body: str | None = None,
    notice_is_error: bool = False,
) -> str:
    """Render the login page for registry JWT auth."""

    page_title = "PureCipher Secured MCP Registry"
    notice_html = _render_optional_notice(
        notice_title=notice_title,
        notice_body=notice_body,
        notice_is_error=notice_is_error,
    )

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Sign In · {page_title}</title>
    <style>{BASE_STYLES}</style>
  </head>
  <body>
    <main class="shell">
      {
        _render_topbar(
            registry_prefix=registry_prefix,
            auth_enabled=auth_enabled,
            session=session,
            current_page="login",
            current_path=_login_href(
                registry_prefix=registry_prefix,
                next_path=next_path,
            ),
        )
    }
      <section class="hero">
        <div class="eyebrow">Registry Login</div>
        <h1>Sign in to the registry</h1>
        <p class="subtle" style="margin-top: 8px;">Server: {
        _escape(server_name)
    } · Next: {_escape(next_path)}</p>
        <p class="hero-copy">
          PureCipher uses JWT-backed product auth for browser sessions and API calls. Sign in to publish manifests,
          review moderation queues, or perform admin actions depending on your assigned role.
        </p>
        <div class="jump-links">
          <span class="listing-chip">JWT session cookie</span>
          <span class="listing-chip">role-aware routing</span>
          <span class="listing-chip">publisher / reviewer / admin</span>
        </div>
      </section>

      <section class="page-stack">
        {notice_html}
        <section class="panel">
          <div class="panel-head">
            <div>
              <h2>Account Access</h2>
              <div class="subtle">Publisher can submit, reviewer can process queue items, admin can suspend and restore listings.</div>
            </div>
          </div>
          <div class="auth-grid">
            <form class="auth-panel" method="post" action="{
        _escape(f"{registry_prefix}/login")
    }">
              <input type="hidden" name="next" value="{_escape(next_path)}" />
              <label class="detail-note" for="username">Username</label>
              <input id="username" name="username" placeholder="admin" style="margin-top: 8px;" />
              <label class="detail-note" for="password" style="margin-top: 12px; display: block;">Password</label>
              <input id="password" type="password" name="password" placeholder="••••••••" style="margin-top: 8px;" />
              <button type="submit" style="margin-top: 14px;">Sign In</button>
            </form>
            <div class="auth-panel">
              <h3>Role Model</h3>
              <div class="role-grid" style="margin-top: 12px;">
                <div class="mini-card">
                  <div class="label">Viewer</div>
                  <div class="detail-note" style="margin-top: 8px;">Browse the verified catalog and listing pages.</div>
                </div>
                <div class="mini-card">
                  <div class="label">Publisher</div>
                  <div class="detail-note" style="margin-top: 8px;">Submit new manifests into the registry.</div>
                </div>
                <div class="mini-card">
                  <div class="label">Reviewer</div>
                  <div class="detail-note" style="margin-top: 8px;">Approve, reject, or request changes for pending listings.</div>
                </div>
                <div class="mini-card">
                  <div class="label">Admin</div>
                  <div class="detail-note" style="margin-top: 8px;">Full moderation, including suspension and restoration.</div>
                </div>
              </div>
              <div class="action-row" style="margin-top: 14px;">
                <a class="action-link" href="{
        _escape(registry_prefix)
    }">Back to catalog</a>
                <a class="action-link" href="{
        _escape(f"{registry_prefix}/session")
    }">Session JSON</a>
              </div>
            </div>
          </div>
        </section>
      </section>
    </main>
  </body>
</html>"""


__all__ = [
    "SAMPLE_MANIFEST_JSON",
    "create_listing_detail_html",
    "create_login_html",
    "create_publisher_index_html",
    "create_publisher_profile_html",
    "create_registry_ui_html",
    "create_review_queue_html",
]
