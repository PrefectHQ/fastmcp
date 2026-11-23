# property-based tests

Hypothesis properties and state machines that compare fastmcp against a model
or a reference implementation. A property here is only trusted after planted
bugs (mutants) show it can fail.

## layout

| file | what it holds |
|---|---|
| `conftest.py` | hypothesis profiles, the `pbt` and `integration` markers, per-test timeouts |
| `strategies.py` | URI templates in fastmcp's RFC 6570 subset, with values |
| `schema_strategies.py` | JSON Schemas (with `$defs`, `$ref` cycles, positional arrays, defaults) and schema-shaped instances |
| `test_uri_template.py` | `match_uri_template` inverts `expand_uri_template` (roundtrip, reexpand) |
| `test_resource_read.py` | the same round trip through a FastMCP server and an in-memory `Client` |
| `test_json_schema.py` | `dereference_refs` / `compress_schema` keep what validates; `json_schema_to_type_adapter` accepts what `jsonschema` accepts |
| `test_components_machine.py` | state machine over tools, prompts and resources on a parent and a mounted child: add, remove, versions, visibility, listing, pagination, calls; the client talks to the parent directly or through a `ProxyProvider` front server |
| `test_tool_transform.py` | a transformed tool (stacked `Tool.from_tool` layers or a deferred `ToolTransformConfig`) lists and forwards arguments as the transformation docs describe |
| `test_openapi.py` | an OpenAPI tool sends the method, path, query, headers and body its spec describes, and returns the response |
| `test_oauth_proxy_machine.py` | state machine over the OAuth proxy against a fake upstream: codes are single use, refresh tokens are client-bound, revoked tokens stay dead |
| `test_auth_url_guards.py` | the URL guards in front of the OAuth proxy: an IPv6 spelling never unblocks a blocked IPv4, a validated URL dials where it was validated, a redirect pattern match lands on the pattern's host by every parser, an exact pattern matches only itself, client redirects carry each parameter once, CIMD scopes never widen the server's |
| `test_middleware.py` | `AuthMiddleware` lists only what a caller can call and names every scope a denial still needs; `ResponseCachingMiddleware` answers like no cache for every caller, version and argument; both rate limiters admit no more than their limit under a fake clock |
| `mutants.py` | planted bugs and their catch rate across seeds (not collected by pytest) |
| `FINDINGS.md` | every finding, written before it is fixed: property, minimal case, reachability, cross-SDK case table |

## profiles

`HYPOTHESIS_PROFILE` selects one; nothing else does.

| profile | examples | randomness | database | timeout | for |
|---|---|---|---|---|---|
| `ci` (default) | 50 | derandomized | off | 120s per test | the normal suite; bounded and cannot flake |
| `campaign` | 5000, or `HYPOTHESIS_MAX_EXAMPLES` | random | `.hypothesis/examples` | none | bug hunting |
| `mutants` | 200, or `HYPOTHESIS_MAX_EXAMPLES` | random | off | none | what `mutants.py` runs each seed under |

The explain phase is off in every profile: it line-traces each failure through
the server and client and takes minutes.

## running

Every test here also carries the `integration` marker, so CI runs them in the
integration job (Linux, one Python) rather than the 5s unit matrix.

```bash
uv run pytest tests/pbt
```

```bash
HYPOTHESIS_PROFILE=campaign HYPOTHESIS_MAX_EXAMPLES=20000 uv run pytest tests/pbt --hypothesis-show-statistics
```

A time-boxed hunt gives every property the same number of seconds instead of
the same number of examples, so a run costs the same however fast each
property is. Each test reruns with a fresh seed until its budget is spent,
replaying the example database first on every pass:

```bash
HYPOTHESIS_PROFILE=campaign PBT_BUDGET_SECONDS=60 uv run pytest tests/pbt -m pbt -n auto
```

That is what the `PBT campaign` workflow runs: nightly at 120 seconds per
property, with `.hypothesis/examples` kept in the Actions cache between runs,
and at 20 seconds on pull requests that change this directory. A failing run
uploads the database and the JUnit report; the failure output carries the
`@reproduce_failure` blob. Tests marked `pbt_smoke` check the harness itself
and run once.

```bash
uv run python -m tests.pbt.mutants uri_template
```

`mutants.py` takes target names (`uri_template`, `resource_read`,
`schema_fixes`, `components`, `auth_guards`, `middleware`, …), applies each exact string swap to the source,
counts how many of ten seeds fail, and restores the file. A mutant whose
anchor is not in the tree (one that undoes a fix not yet merged) is skipped;
if every requested mutant is skipped the run exits non-zero, since nothing was
measured.

## known findings

Open findings are skipped by name so a run still stops on anything new. The
JSON Schema ones (`F1`, `F2`, `F3`, `F5`) are skipped only while a probe still
reproduces them, so the same harness checks a fix as soon as it lands; a
mutant that undoes a fix sets `PBT_ASSUME_FIXED` so the probe does not mistake
it for the open bug. Each name points to an entry in `FINDINGS.md`.
