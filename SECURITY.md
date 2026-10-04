# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 4.x     | :white_check_mark: |
| 3.x     | :white_check_mark: |
| 2.x     | :x:                |
| 1.x     | :x:                |
| 0.x     | :x:                |

## Reporting a Vulnerability

Please report security vulnerabilities privately using [GitHub's security advisory feature](https://github.com/PrefectHQ/fastmcp/security/advisories/new). Do not open public issues for security concerns.

## Scope

We accept reports for vulnerabilities in FastMCP itself — the library code in this repository.

The following are **out of scope**:

- Vulnerabilities in third-party dependencies or the MCP SDK itself. We'll bump version floors for known CVEs, but the fix belongs upstream.
- Limitations of upstream identity providers that FastMCP cannot control.
- Issues that require the attacker to already have server-side access or control of the MCP server configuration.

### Security Boundaries

MCP Apps tool visibility (`AppConfig.visibility`, including `["app"]`) declares the intended audience and is **not a security boundary**. Hosts use it to filter the model's tool list, but FastMCP cannot distinguish model and app callers on the same MCP connection. Direct calls to app tools remain subject to the server's authentication and authorization checks. Tool names and hashed app-tool identities are routing identifiers, not secrets or access controls.

Security guarantees depend on the protections configured for the deployment. An HTTP server with `auth=None` does not require bearer-token authentication. Setting `require_authorization_consent=False` or `"external"` removes the OAuth proxy's consent and browser-binding protections against confused deputy attacks; `"external"` acknowledges external enforcement and suppresses a warning, but FastMCP does not provide or verify that enforcement. Other options that disable a protection or delegate it externally likewise remove that protection from FastMCP's guarantees.

Reports demonstrating a failure of an enabled authentication or authorization check, or another configured protection, remain in scope. Calling an app-visible tool directly or exercising a deliberately disabled protection does not by itself demonstrate a security bypass.

## Disclosure Process

When we receive a valid report:

1. We triage the report and determine whether it affects FastMCP directly.
2. We develop and test a fix on a private branch.
3. We coordinate CVE assignment through GitHub's advisory process when warranted.
4. We publish the advisory and release a patched version.
5. We credit the reporter in the advisory (unless they prefer otherwise).
