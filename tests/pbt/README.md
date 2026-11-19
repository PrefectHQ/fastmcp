# property-based tests

Hypothesis properties and state machines that compare fastmcp against a model
or a reference implementation. A property here is only trusted after planted
bugs (mutants) show it can fail.

## layout

| file | what it holds |
|---|---|
| `conftest.py` | hypothesis profiles, the `pbt` marker, per-test timeouts, the reach-check fixture |
| `strategies.py` | URI templates in fastmcp's RFC 6570 subset, with values |
| `schema_strategies.py` | JSON Schemas (with `$defs`, `$ref` cycles, positional arrays, defaults) and schema-shaped instances |
| `test_uri_template.py` | `match_uri_template` inverts `expand_uri_template` (roundtrip, reexpand) |
| `test_resource_read.py` | the same round trip through a FastMCP server and an in-memory `Client` |
| `test_json_schema.py` | `dereference_refs` / `compress_schema` keep what validates; `json_schema_to_type_adapter` accepts what `jsonschema` accepts |
| `test_components_machine.py` | state machine over tools, prompts and resources on a parent and a mounted child: add, remove, versions, visibility, listing, pagination, calls |
| `reach.py` | reach checks: the fraction of cases that hit each interaction a property needs |
| `mutants.py` | planted bugs and their catch rate across seeds (not collected by pytest) |
| `FINDINGS.md` | every finding, written before it is fixed: property, minimal case, reachability, cross-SDK case table |

## profiles

`HYPOTHESIS_PROFILE` selects one; nothing else does.

| profile | examples | randomness | database | timeout | for |
|---|---|---|---|---|---|
| `ci` (default) | 50 | derandomized | off | 120s per test | the normal suite; bounded and cannot flake |
| `campaign` | 5000, or `HYPOTHESIS_MAX_EXAMPLES` | random | `.hypothesis/examples` | none | bug hunting; fails a test whose declared reach is under 0.2% |
| `mutants` | 200, or `HYPOTHESIS_MAX_EXAMPLES` | random | off | none | what `mutants.py` runs each seed under |

The explain phase is off in every profile: it line-traces each failure through
the server and client and takes minutes.

## running

```bash
uv run pytest tests/pbt
```

```bash
HYPOTHESIS_PROFILE=campaign HYPOTHESIS_MAX_EXAMPLES=20000 uv run pytest tests/pbt --hypothesis-show-statistics
```

```bash
uv run python -m tests.pbt.mutants schema_fixes
```

`mutants.py` takes target names (`uri_template`, `resource_read`,
`schema_fixes`, `components`), applies each exact string swap to the source,
counts how many of ten seeds fail, and restores the file. A mutant whose
anchor is not in the tree (one that undoes a fix not yet merged) is skipped.

## known findings

Open findings are skipped by name so a run still stops on anything new. The
JSON Schema ones (`F1`, `F2`, `F3`, `F5`) are skipped only while a probe still
reproduces them, so the same harness checks a fix as soon as it lands; a
mutant that undoes a fix sets `PBT_ASSUME_FIXED` so the probe does not mistake
it for the open bug. Each name points to an entry in `FINDINGS.md`.
