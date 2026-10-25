# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 4.x     | :white_check_mark: |
| 3.x     | :white_check_mark: |
| 2.x     | :x:                |
| 1.x     | :x:                |
| 0.x     | :x:                |

## Certificate authority incident: October 2026

The [eMudhra intermediate CA compromise](https://emudhra.we-have-a-problem.com/) can let an attacker intercept HTTPS connections when the application still trusts an affected certificate chain. This is an upstream trust-store incident; updating FastMCP alone does not protect every deployment.

FastMCP 3 uses certifi by default. FastMCP 4 uses the operating system's trust store through HTTPX2; some server dependencies and optional integrations still use certifi. Update certifi to **2026.10.19 or later** wherever it is installed, and update the CA stores in your operating system, containers, and custom TLS configuration. Rebuild affected images and restart processes after changing their trust stores. Both the direct emSign chains and the Sectigo/AAA cross-signed chains must be distrusted. Confirm that your distribution has shipped this change; installing its latest CA package is insufficient if the fix is still pending. The incident response page provides distribution status and interim guidance.

Update **uv to 0.13.4 or later** and **pip to 26.2.2 or later** where used. Update existing virtual environments too. Their bundled certificates are separate from the application's certifi package; pip can also trust system certificates, so updating pip does not replace the OS update. Supplying pip with `--cert` or `REQUESTS_CA_BUNDLE` adds trust and does not remove its bundled roots. See the [Python security team's guidance](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576) for other affected tools.

Verify from the actual deployed runtime that the [incident test chains](https://emudhra.we-have-a-problem.com/#check) fail certificate verification and your normal upstream connections still work. A timeout is not evidence of distrust. Do not disable certificate verification to restore connectivity.

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
