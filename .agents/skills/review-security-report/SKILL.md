---
name: review-security-report
description: Review FastMCP vulnerability reports before accepting, rejecting, patching, scoring, or publishing them. Use for security advisories, bug-bounty submissions, OAuth or MCP vulnerability claims, and proposed security fixes.
---

# Review a FastMCP security report

A working proof of concept establishes behavior, not ownership or classification. Identify the
component that violates a promised security boundary before changing code or advisory state.

## Preserve the evidence

Before editing an advisory, export the report, comments, proof of concept, configuration, and
claimed affected versions. Record the reproduced commit and derive affected releases from history.

Keep the investigation read-only until classification. Do not mutate the advisory or prepare a fix
merely because the proof of concept works.

## Reproduce the claim

Use the smallest end-to-end reproduction against a supported release. Record:

- defaults, non-default arguments, environment, and deployment assumptions;
- what the attacker controls, what the victim does, and what authority the attacker gains;
- the check or trust boundary allegedly bypassed; and
- whether the attacker's prerequisites already provide equal or greater authority than the claimed
  impact.

A non-default configuration may still be vulnerable; a default may intentionally delegate a
security decision elsewhere.

## Identify the responsible layer

- **FastMCP vulnerability:** FastMCP violates a promised boundary in a supported configuration.
- **FastMCP bug:** FastMCP behaves incorrectly without creating attacker capability.
- **Upstream vulnerability:** The defect belongs to a standard, dependency, identity provider,
  client, proxy, or platform.
- **Insecure deployment:** The operator omits, disables, or delegates a required control without
  supplying the replacement required by its contract.
- **Expected behavior:** The result follows the documented API contract or protocol.
- **Documentation gap:** The implementation follows its intended contract, but the guidance creates
  a reasonable expectation of protection.

Attribute the violated boundary to its owner. Neither a dangerous configuration nor an available
external mitigation decides ownership by itself. Distinguish failure of a primary control from
failure of defense-in-depth.

## Establish FastMCP's contract

Read the code, released documentation, tests, and history. Determine:

1. What FastMCP promises for the exact configuration, supported API, and affected releases.
2. Whether the proof of concept bypasses, disables, or delegates that protection.
3. Whether the behavior follows the implemented protocol despite any stricter FastMCP promise.

If the intended contract remains unclear, ask the subsystem maintainer before accepting the report
or proposing a fix. Current code alone does not define supported product behavior.

## Separate OAuth proxy boundaries

For OAuth proxy reports, check each layer independently:

- Open DCR lets unknown clients register redirects; a matching attacker callback is not a redirect
  validation bypass.
- FastMCP consent binds the downstream client; ordinary upstream consent binds FastMCP's shared app.
- `require_authorization_consent=False` removes consent; `"external"` delegates equivalent consent
  and transaction binding outside FastMCP.
- PKCE protects the verifier, not a code issued for a malicious client's own challenge.
- Redirect allowlists are optional policy and can break hosted clients or open DCR.

## Review the proposed fix independently

A patch can block the proof of concept and still be wrong. Verify that it:

- fixes the violated boundary at its source and enforces the claimed property;
- does not silently change an intentional default or opt-in contract; and
- preserves supported workflows without assuming one replacement control is the only valid design.

Treat a change to the supported product contract as an enhancement requiring maintainer agreement,
independently of the security report.

## Classification checkpoint

Before any GitHub mutation, state the decisive configuration and affected versions, boundary owner,
realistic impact, and one disposition:

- accept a draft advisory and prepare a private patch;
- request specific missing evidence;
- route as a normal FastMCP bug or upstream issue;
- close as expected behavior or an insecure deployment;
- ask the relevant maintainer to decide the contract.

Separately recommend any documentation or warning clarification warranted by the investigation.

When rejecting a report, acknowledge any valid reproduction and explain the decisive precondition.
