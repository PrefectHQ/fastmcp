# API-Key-Backed OAuth Example

Make OAuth-only MCP clients work with a service that authenticates by API key.

## The problem

Claude Code can send a static header to a remote MCP server:

```bash
claude mcp add -t http my-server https://example.com/mcp -H "X-API-Key: <key>"
```

Claude Desktop and ChatGPT's connectors cannot. They expose no field for a
custom header — the only authentication mechanism they implement is the MCP
OAuth 2.1 handshake. A service whose entire auth model is "send your API key in
a header" therefore cannot reach those clients at all, even though the
credential the user needs (their API key) is sitting right there.

## The approach

This `APIKeyOAuthProvider` speaks full OAuth so the clients are satisfied, but
replaces the usual username/password login with a consent page that names the
requesting client and asks the user to **paste the API key they already have**.
The OAuth dance is purely a transport for the key — no identity provider, no user
database, no key lookup.

It keeps the SDK's authorization handler (so request validation and PKCE are
unchanged) and reuses the same primitives FastMCP's OAuth proxy is built on:
`derive_jwt_key` turns a configured secret into the token signing key and a
Fernet storage-encryption key, `JWTIssuer` issues *reference tokens* that carry
only a `jti`, and a Fernet-encrypted store holds the transaction, the
authorization code, and the API key. The key is encrypted at rest and never
travels on the wire; tools read it back from the access token claims.

Two integration points are yours to fill in. First, verify the pasted key
against your backend before a token is issued — the hook may be async, so it can
make an HTTP call:

```python
async def validate_api_key(key: str) -> bool:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            API_VERIFY_URL, headers={"Authorization": f"Bearer {key}"}
        )
        return response.is_success
```

Second, read the key inside a tool and use it to construct your client:

```python
@mcp.tool
async def list_files(token: AccessToken = CurrentAccessToken()) -> list[str]:
    api_key = token.claims[API_KEY_CLAIM]
    client = my_service.Client(api_key=api_key)
    return await client.list_files()
```

## Run it

```bash
python server.py
```

In another terminal:

```bash
python client.py
```

A browser opens to the consent page. The demo server accepts any non-empty key
(set `API_VERIFY_URL` to validate against a real backend); enter anything and the
connection completes. `list_files` then runs with the key the server recovered
from your token.

To wire it into a real client, point Claude Desktop / ChatGPT at
`http://127.0.0.1:8000/mcp` as a custom connector.

## Production notes

This is a reference, not a drop-in. Before shipping:

- **The API key is encrypted at rest and never on the wire.** The access token
  is a reference token carrying only a `jti`; the key lives in the Fernet-
  encrypted store keyed by that `jti`. It also never travels in a URL — it is
  submitted in the form POST body and bound to an opaque authorization code.
- **Load `jwt_signing_key` from your secret store.** Both the token signing key
  and the storage encryption key derive from it, so the same secret across
  restarts keeps previously issued tokens valid.
- **The default store is single-host.** It defaults to an on-disk Fernet-
  encrypted file store. For a multi-worker or multi-replica deployment, pass a
  shared `client_storage` (e.g. Redis-backed) so a token issued by one worker
  resolves on another.
- **Registered clients live in process memory.** They are cheaply re-created via
  dynamic client registration; a production server may prefer to persist them.
  Transactions, authorization codes, and keys already live in the shared
  encrypted store.
- **The consent page is deliberately minimal.** It names the client and blocks
  cross-site form submission, which covers the basic phishing case, but it does
  not implement the full consent machinery (cookie-bound "remember" decisions,
  CSP tuning) that `OAuthProxy` provides. Harden it before exposing the server to
  untrusted users.
- **Validate the key at the authorize step** by passing `validate_api_key=` so a
  bogus key is rejected before a token is minted rather than failing later.
