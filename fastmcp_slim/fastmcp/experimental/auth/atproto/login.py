"""The handle sign-in page shown between consent and the user's PDS."""

from __future__ import annotations

import html
import secrets
from urllib.parse import urlparse

from fastmcp.utilities.ui import BUTTON_STYLES, create_logo, create_page

ERROR_MESSAGES: dict[str, str] = {
    "invalid_identifier": "Enter a handle like you.bsky.social, or a DID.",
    "handle_not_found": "Couldn't find that handle.",
    "identity_unavailable": "Couldn't look up that account. Try again in a moment.",
    "server_unavailable": "Your account's server couldn't start sign-in. Try again in a moment.",
    "denied": "Sign-in was cancelled.",
    "expired": "That sign-in expired. Try again.",
    "invalid_grant": "That sign-in expired. Try again.",
    "not_allowed": "That account isn't allowed to use this server.",
}

LOGIN_STYLES = """
    .container { max-width: 26rem; text-align: left; padding: 2.25rem 2rem; }
    .logo { margin: 0 auto 1.25rem; width: 48px; }
    h1 { font-size: 1.35rem; font-weight: 600; text-align: center; margin-bottom: 0.35rem; }
    .subtitle { color: #6b7280; text-align: center; margin-bottom: 1.75rem; font-size: 0.95rem; }
    form { display: flex; flex-direction: column; gap: 0.75rem; }
    label { font-size: 0.875rem; font-weight: 500; color: #374151; }
    .field { position: relative; }
    input[type="search"] {
        width: 100%; font: inherit; font-size: 1rem; padding: 0.7rem 0.85rem;
        border: 1px solid #d1d5db; border-radius: 0.6rem; background: #fff; color: inherit;
        -webkit-appearance: none; appearance: none;
    }
    input[type="search"]::-webkit-search-cancel-button { display: none; }
    input[type="search"]:focus { outline: 2px solid #2563eb; outline-offset: 1px; border-color: transparent; }
    .hint { font-size: 0.8rem; color: #6b7280; }
    .error-text { font-size: 0.875rem; color: #b91c1c; background: #fef2f2;
        border: 1px solid #fecaca; border-radius: 0.5rem; padding: 0.6rem 0.75rem; }
    .suggestions { position: absolute; left: 0; right: 0; top: calc(100% + 4px); z-index: 10;
        background: #fff; border: 1px solid #e5e7eb; border-radius: 0.6rem; list-style: none;
        box-shadow: 0 8px 20px rgba(0,0,0,0.08); overflow: hidden; }
    .suggestions[hidden] { display: none; }
    .suggestions li { display: flex; align-items: center; gap: 0.6rem; min-height: 44px;
        padding: 0.4rem 0.75rem; cursor: pointer; }
    .suggestions li[aria-selected="true"], .suggestions li:hover { background: #f3f4f6; }
    .suggestions img, .suggestions .avatar { width: 24px; height: 24px; border-radius: 50%;
        flex: none; background: #e5e7eb; object-fit: cover; }
    .suggestions .handle { color: #0a0a0a; font-size: 0.925rem; }
    .suggestions .name { color: #6b7280; font-size: 0.8rem; overflow: hidden;
        text-overflow: ellipsis; white-space: nowrap; }
    .suggestions .text { display: flex; flex-direction: column; min-width: 0; }
    button.btn-primary { width: 100%; margin-top: 0.25rem; }
    button.btn-primary:disabled { opacity: 0.7; cursor: default; transform: none; }
"""

TYPEAHEAD_SCRIPT = """
(() => {
  const form = document.getElementById("login");
  const input = document.getElementById("identifier");
  const button = form.querySelector("button");
  const list = document.getElementById("suggestions");
  const endpoint = list.dataset.endpoint;
  let controller = null, timer = null, actors = [], active = -1;

  if (new URLSearchParams(location.search).has("error")) {
    const url = new URL(location.href);
    url.searchParams.delete("error");
    history.replaceState(null, "", url);
  }

  form.addEventListener("submit", (event) => {
    if (button.disabled) { event.preventDefault(); return; }
    button.disabled = true;
    button.textContent = "redirecting...";
  });

  if (!endpoint) return;

  const close = () => { list.hidden = true; active = -1; input.setAttribute("aria-expanded", "false"); };
  const choose = (actor) => { input.value = actor.handle; close(); form.requestSubmit(); };
  const render = () => {
    list.replaceChildren(...actors.map((actor, index) => {
      const row = document.createElement("li");
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(index === active));
      if (actor.avatar) {
        const img = document.createElement("img");
        img.src = actor.avatar; img.alt = ""; img.loading = "lazy";
        row.append(img);
      } else {
        const placeholder = document.createElement("span");
        placeholder.className = "avatar";
        row.append(placeholder);
      }
      const text = document.createElement("span");
      text.className = "text";
      const handle = document.createElement("span");
      handle.className = "handle"; handle.textContent = "@" + actor.handle;
      text.append(handle);
      if (actor.displayName) {
        const name = document.createElement("span");
        name.className = "name"; name.textContent = actor.displayName;
        text.append(name);
      }
      row.append(text);
      row.addEventListener("mousedown", (event) => { event.preventDefault(); choose(actor); });
      return row;
    }));
    list.hidden = actors.length === 0;
    input.setAttribute("aria-expanded", String(actors.length > 0));
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const query = input.value.trim().replace(/^@/, "");
    if (query.length < 2 || query.startsWith("did:")) { actors = []; close(); return; }
    timer = setTimeout(async () => {
      controller?.abort();
      controller = new AbortController();
      try {
        const url = new URL(endpoint);
        url.searchParams.set("q", query);
        url.searchParams.set("limit", "6");
        const response = await fetch(url, { signal: controller.signal, headers: { "X-Client": "fastmcp" } });
        if (!response.ok) return;
        const data = await response.json();
        actors = (data.actors || []).filter((actor) => actor.handle && actor.handle !== "handle.invalid");
        active = -1;
        render();
      } catch (error) {
        if (error.name !== "AbortError") { actors = []; close(); }
      }
    }, 120);
  });

  input.addEventListener("keydown", (event) => {
    if (list.hidden || actors.length === 0) return;
    if (event.key === "ArrowDown") { active = (active + 1) % actors.length; render(); event.preventDefault(); }
    else if (event.key === "ArrowUp") { active = (active - 1 + actors.length) % actors.length; render(); event.preventDefault(); }
    else if (event.key === "Enter" && active >= 0) { event.preventDefault(); choose(actors[active]); }
    else if (event.key === "Escape") { close(); }
  });
  input.addEventListener("blur", close);
})();
"""


def create_login_html(
    *,
    txn_id: str,
    action_url: str,
    client_name: str | None,
    server_name: str | None,
    server_icon_url: str | None,
    identifier: str = "",
    error: str | None = None,
    typeahead_url: str | None = None,
) -> str:
    nonce = secrets.token_urlsafe(16)
    connect_src = "'none'"
    if typeahead_url:
        parsed = urlparse(typeahead_url)
        connect_src = f"{parsed.scheme}://{parsed.netloc}"
    csp = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; "
        f"script-src 'nonce-{nonce}'; connect-src {connect_src}; base-uri 'none'"
    )

    server_display = html.escape(server_name or "this server")
    subtitle = (
        f"to connect {html.escape(client_name)} to {server_display}"
        if client_name
        else f"to continue to {server_display}"
    )
    error_html = ""
    if error:
        message = ERROR_MESSAGES.get(error, "Sign-in failed. Try again.")
        error_html = f'<p class="error-text" role="alert">{html.escape(message)}</p>'

    content = f"""
        <div class="container">
            {create_logo(icon_url=server_icon_url, alt_text=server_name or "FastMCP")}
            <h1>Sign in with your handle</h1>
            <p class="subtitle">{subtitle}</p>
            <form id="login" method="post" action="{html.escape(action_url)}">
                <input type="hidden" name="txn_id" value="{html.escape(txn_id)}">
                <label for="identifier">Your handle or DID</label>
                <div class="field">
                    <input id="identifier" name="identifier" type="search" required autofocus
                        value="{html.escape(identifier)}" placeholder="you.bsky.social"
                        autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false"
                        data-1p-ignore data-lpignore="true" data-bwignore
                        role="combobox" aria-autocomplete="list" aria-expanded="false"
                        aria-controls="suggestions" enterkeyhint="go">
                    <ul id="suggestions" class="suggestions" role="listbox" hidden
                        data-endpoint="{html.escape(typeahead_url or "")}"></ul>
                </div>
                <p class="hint">You'll confirm on your account's own server.</p>
                {error_html}
                <button type="submit" class="btn-primary">Continue</button>
            </form>
        </div>
        <script nonce="{nonce}">{TYPEAHEAD_SCRIPT}</script>
    """
    return create_page(
        content=content,
        title="Sign in",
        additional_styles=BUTTON_STYLES + LOGIN_STYLES,
        csp_policy=csp,
    )
