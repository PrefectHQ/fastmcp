# property-test findings

Every finding is written here before anything is fixed. Each entry: the
property, the minimal shrunk example, the reproduce blob, how often it is hit,
whether a normal user can reach it, and the case against it.

Run a property: `uv run pytest tests/pbt -m pbt --hypothesis-show-statistics`.
Hunt: `HYPOTHESIS_PROFILE=campaign HYPOTHESIS_MAX_EXAMPLES=20000 uv run pytest tests/pbt`.
Mutant catch rates: `uv run python -m tests.pbt.mutants`.

## summary (campaigns of 2026-11-18)

| property | cases | mutants caught | result |
|---|---|---|---|
| uri_template roundtrip, reexpand | 20k each | 4/4 at 10/10 seeds | clean (K1) |
| resource_read through Client | 10k | 2/2 at 10/10 | clean (K2, K3) |
| json_schema dereference, compress | 10k each | none planted | clean |
| json_schema adapter accepts valid | 10k | `schema_fixes`, 4 undo mutants at 10/10 (below) | F1, F2, F3, F5 |
| component machine (tools, prompts, resources, mount, versions, visibility, pagination) | 3k histories | 4/4 at 10/10 | F4 |

F1, F3 and F5 are fixed on `main` (#6552, #6553, #6572), so their probes
report fixed and the harness checks those shapes again. F2 is fixed by keeping
the merge and falling back to the provided object when the merged one is
invalid. F4 is the documented behaviour: the docs now describe the fallback.

Findings, most to least serious: F1 (crash on a non-object default for an
object property, reachable end to end), F4 (explicit version silently runs an
unversioned component, contradicting the versioning docs), F3 (recursive
`prefixItems` tail makes output validation always fail; foreign schemas only).
F2 (found on the fixed tree) turns the object-default merge into a real
rejection when typed `additionalProperties` validate the merged keys; its fix
is a hydration design decision.

## fixes and the mutants that undo them

| finding | fix | undo mutant(s) on a tree with the fixes merged, 300 examples × 10 seeds |
|---|---|---|
| F1 | #6552 | nested guard removed: 10/10; root guard removed: 10/10 |
| F3 | #6553 | positions read from the closure again: 10/10 |
| F5 | #6572 | placeholders inherit `validate_default` again: 10/10 (3/10 before drawing the shape directly) |

Getting there took three harness fixes, each one a reason the first
measurement (1/10, 2/10, 1/10) was low: random JSON instances rarely reached
nested properties (schema-shaped instances), the shapes were rare in drawn
schemas (drawn directly: a recursive def reached from the root, an object
property with an object or non-object default), and the "skip while it
reproduces" probe treated an undo mutant as the open bug and skipped exactly
the schemas that would catch it (`PBT_ASSUME_FIXED`, set per mutant).

## known divergences (registered in the harness, not bugs)

### K1. a non-exploded list query param holding `[""]` reads back as `[]`

- property: uri_template roundtrip / reexpand
- minimal: template `res://x/{v0*}{?v1}`, `v1` a list param, `v1=[""]` →
  uri `res://x/a?v1=` → match `{"v1": []}`
- blob: `@reproduce_failure('6.170.15', b'AEEAQQFBAAFBAABBAUEBQQIBQQAAAAA=')`
- frequency: first failure in a 20k campaign, within a few hundred cases
- reachable: a client reading `...?tags=` from a template whose function takes
  `tags: list[str]` gets `[]`.
- against: `match_uri_template` maps an empty raw value to `[]` on purpose
  (`if raw else []`), and RFC 6570 cannot tell `[]` and `[""]` apart once
  expanded: both are an empty value. Expansion of `[]` omits the key, so the
  only lossy case is a one-item list of the empty string. Kept as a known
  divergence, not a bug.

### K2. a simple value of `.` or `..` expands to a dot segment the wire removes

- property: resource_read roundtrip (expand, then read through Client)
- minimal: template `res://x/{a}`, `a="."` → `expand_uri_template` gives
  `res://x/.` → the request's `AnyUrl` normalizes it to `res://x/` →
  `Resource not found: 'res://x/'`. `res://x/{a}/{b}` with `a=".."` arrives
  as `res://x/b`; a wildcard `a="a/./b"` reaches the function as `"a/b"`.
- frequency: 0.8% of cases (`wire: dot-segment-removed`)
- reachable: only with a value that is exactly `.` or `..` in a path segment.
  The default `ResourceSecurity` already refuses `..`.
- against: RFC 6570 leaves `.` unencoded, so the expansion is to spec, and the
  normalization is RFC 3986 behaviour in the SDK's `AnyUrl`, not fastmcp code.
  Normalization runs before matching, so it cannot smuggle a traversal past the
  screen. Recorded, not a bug.

### K3. literal `:` or `@` in the authority is rewritten or rejected by `AnyUrl`

- property: resource_read roundtrip
- minimal: `data://{h}:{p}` with `p="a"` → client raises
  `Provided resource URI is invalid: 'data://a:a'` (non-numeric port).
  `data://{u}@{h}@{z}` → the wire re-encodes the first `@` to `%40`, and the
  template's literal `@` matches only as written → not found.
  `data://{v0*}://{v1}` → `data://a://a` becomes `data://a//a`.
- frequency: 2.4% rewritten, 2.2% not a URL
- reachable: `redis://{host}:{port}` with a numeric port works; only
  non-numeric "ports" and two literal `@` in one authority fail. A single
  `db://{user}@{host}/...` works, including a user value containing `@`.
- against: these URIs are invalid or ambiguous under RFC 3986; the template
  author put structure where the URI grammar does not allow it. Arguably
  fastmcp could warn at registration, but the read failing is correct.
  Recorded, not a bug.

## findings

### F1. an object property with a non-object `default` crashes output validation

- property: json_schema `adapter_accepts_valid_instances` — an instance
  `jsonschema` accepts is accepted by `json_schema_to_type_adapter`
- minimal: schema
  `{"type": "object", "properties": {"a": {"type": "object", "default": null}}}`,
  instance `{"a": {}}` → `TypeError: 'NoneType' object is not iterable` from
  `_merge_defaults` (`dict(schema["default"])`,
  fastmcp_slim/fastmcp/utilities/json_schema_type.py:1574). With
  `"default": true` and instance `{"a": {"a": null}}` the other branch
  (`dict(parent_default)`) fails the same way; `"default": "x"` fails as a
  pydantic `ValueError` ("dictionary update sequence").
- blobs: `@reproduce_failure('6.170.15', b'AXicc2RwRIOMjI4MDI5sjhDMwOjIAhRwZGJgYAAAdP0EqQ==')`,
  `b'AXicc2RwRIOMjI4MDI5sjhDMCOSyAEUcmUAEAxAAAJNfBS4='`
- frequency: 3 distinct shrunk failures in the first 2k-case campaign
- reachable: yes, end to end. A FastMCP tool with
  `output_schema={"type": "object", "properties": {"meta": {"type": "object", "default": None}}}`
  returning `{"meta": {}}`: `client.call_tool` logs
  `Error parsing structured content: 'NoneType' object is not iterable` and
  returns `data=None` although `structured_content` is valid. Only
  `"type": "object"` exactly triggers it; `["object", "null"]` and
  `anyOf` nullable shapes (what pydantic emits for `dict | None = None`) do not.
- against: the schema's default does not satisfy its own `type`, so the
  schema is sloppy. But JSON Schema makes `default` an annotation with no
  validation force, the instance is valid, and the failure is an uncaught
  `TypeError` in a merge helper, not a validation verdict. Hand-written and
  non-pydantic (e.g. zod-generated) schemas do carry `"default": null`. Kept.

### F2. an object default is merged into a provided `{}`, which can make a valid instance invalid

- property: json_schema `adapter_accepts_valid_instances`, found on
  a tree with the F1 and F3 fixes merged once object properties with object defaults were drawn
  directly; already on main (`83f99fdb6`), not caused by either fix. First
  seen as a data-fidelity oddity while minimizing F1.
- minimal: `{"type":"object","properties":{"a":{"type":"object","properties":{},"additionalProperties":{"type":"string"},"default":{"a":null}}}}`,
  instance `{"a":{}}` → `a.a Input should be a valid string [input_value=None]`.
  jsonschema accepts it. Without typed `additionalProperties` the same merge
  does not fail but still changes the data: `{"a":{}}` with
  `default: {"k":"x"}` hydrates to `a.k == "x"`.
- mechanism: `_merge_defaults` treats an empty provided object as "no data"
  (`if not data:`) and copies the property's object default into it
  (`json_schema_type.py:1568-1574`); the typed-extras model then validates the
  merged-in keys (`validate_default`, `:1414`, the same flag as F5).
- reachable: a tool output schema with an object property that has an object
  `default` and typed `additionalProperties`, when the server returns `{}` for
  it: `data=None`. In the milder form, `.data` carries keys the server never
  sent.
- cross-SDK: no reference merges `default` into data (see the F1 table: python
  `jsonschema`, TS Ajv without `useDefaults`, zig; Go's `ApplyDefaults` fills
  missing child keys but leaves a present `{}` alone). This is fastmcp-only.
- against: the merge is deliberate (`test_default_priority`,
  `test_object_default_still_merges_into_provided_object` in #6552 keep it),
  so changing it is a behaviour change for hydration, not just a crash fix.
  The rejection case is narrow (an invalid object default plus typed extras).
  Decision (nate, 2026-11-19): keep the merge, but when the merged object
  fails validation, validate the provided object instead. Valid defaults
  hydrate exactly as before; `{}` is never rejected because of a default.

### F3. a recursive `$ref` through `prefixItems` builds an adapter that rejects everything

- property: json_schema `adapter_accepts_valid_instances`
- minimal (hand-reduced from the shrunk case):
  ```json
  {"type": "object",
   "properties": {"r": {"type": "array", "items": {"$ref": "#/$defs/N"}}},
   "$defs": {"N": {"type": "object", "properties": {"kids": {
      "type": "array", "prefixItems": [{"type": "string"}],
      "items": {"$ref": "#/$defs/N"}}}}}}
  ```
  `json_schema_to_type_adapter(schema).validate_python({})` raises
  `PydanticUserError: TypeAdapter[Annotated[list[Any], GetPydanticSchema(...)]]
  is not fully defined`. The same with `{"r": []}`. Dropping `prefixItems`
  makes it work, so the positional-array type built by
  `_create_prefix_items_type` does not take part in recursive-type
  resolution (`_resolve_recursive_types`). A cycle through a prefix position
  (`"prefixItems": [{"type": "string"}, {"$ref": "#/$defs/A"}]`) fails the
  same way; the #6553 branch accepts both (checked 2026-11-19).
- blob: shrunk case from the 10k campaign
  (`{'type': 'array', 'prefixItems': [{'type': 'string'}], 'items': {...'$ref': '#/$defs/A'}}`);
  reproduce from `.hypothesis/examples` with the campaign profile
- frequency: 2 failing of ~1.1k generated before shrinking, then 55 of 311 in
  shrink: it is the one distinct failure the 10k run found after F1 was
  registered
- reachable: a tool whose output schema has a positional array with a
  recursive *tail*: `prefixItems` plus `items: {"$ref": <ancestor>}`. That is
  what zod's `z.tuple([...]).rest(Node)` or a hand-written schema emits.
  Pydantic's own recursive tuples do not trigger it: `tuple[str, Node]`
  (the `$ref` inside `prefixItems`, no tail) round-trips fine end to end, and
  `tuple[str, *tuple[Node, ...]]` is not supported by pydantic at all. When it
  triggers, the client logs `Error parsing structured content` and returns
  `data=None` for every call, regardless of the payload.
- against: needs a recursive tail after a prefix, which Python servers built
  on pydantic will not produce; it is a proxy/foreign-server problem. Still,
  the failure doesn't depend on the data at all, so one such tool never gets
  `.data`. Kept, lower severity than F1.

### F4. an explicit version on an unversioned component runs the unversioned one

- property: component machine `call` — "an explicit version runs exactly that
  version, or fails" (docs/servers/versioning.mdx, Requesting Specific
  Versions: "If the requested version doesn't exist, the server raises a
  `NotFoundError` ... rather than silently falling back")
- minimal: `state.add(kind='tool', owner='parent', version=None)` for `t0`,
  then `client.call_tool("t0", {}, version="1")` returns the unversioned
  tool's result instead of failing. Same for `get_prompt(..., version="9")`
  and `read_resource(..., version="9")`.
- frequency: 11–20% of machine cases per kind once a starting state is drawn
- reachable: yes, by any client that pins a version against a server whose
  component is unversioned (e.g. after the server drops versioning).
- against: the versioning docs also say "Unversioned components are exempt
  from version filtering by default" — that sentence is about `VersionFilter`,
  but the implementation may apply the same exemption to explicit requests on
  purpose. If so the docs' promise in "Requesting Specific Versions" is the
  thing that is wrong. Either way the two statements disagree. Kept, as a
  doc-or-behaviour mismatch for the maintainers to decide.

### F5. an unpinned `prefixItems` array inside an object with `additionalProperties` rejects short arrays

- property: json_schema `adapter_accepts_valid_instances`, found on
  a tree with the F1 and F3 fixes merged once the generator was weighted
  toward positional arrays; it is already on main (`83f99fdb6`), not caused
  by either fix
- minimal (hand-reduced):
  `{"type":"object","properties":{"x":{"type":"object","properties":{"a":{"type":"array","prefixItems":[{"type":"string"}]}},"additionalProperties":{"type":"string"}}}}`,
  instance `{"x":{"a":[]}}` → `x.a.0 Input should be a valid string
  [input_value=None]`. jsonschema (2020-12) accepts it. Without
  `additionalProperties` on `x`, or with the array at the root's own
  properties, it validates.
- blob: hypothesis seed 0 under `HYPOTHESIS_PROFILE=mutants` on
  that tree, shrunk to
  `{'a': {'type': 'object', 'properties': {'a': {'anyOf': [...prefixItems...]}}, 'additionalProperties': {...}}}`
  with `{'a': {'a': []}}`
- reachable: yes. A tool with output schema
  `{"type":"object","properties":{"x":{"type":"object","properties":{"pair":{"type":"array","prefixItems":[{"type":"string"},{"type":"integer"}]}},"additionalProperties":{"type":"string"}}}}`
  returning `{"x":{"pair":["a"]}}` gives `data=None` with a validation error
  logged. Pydantic never emits unpinned `prefixItems`, so this needs a
  foreign or hand-written schema, like F3.
- mechanism: `_create_prefix_items_type` pads missing prefix positions with
  `with_default_schema(..., default=None)` placeholders and slices them off
  after validation (`json_schema_type.py:862-870`, `:838`). The placeholders
  set no `validate_default` of their own, so they inherit the enclosing
  model's config. `_build_pydantic_model` builds an object with typed
  `additionalProperties` with `ConfigDict(extra="allow",
  validate_default=extra_value_type is not None)` (`:1414`, from #5779), so
  the `None` placeholders are validated against the position types. Plain
  `additionalProperties: true` or none goes through the dataclass path with
  `validate_default` off and works. Same root cause makes the invalid control
  `{"x":{"pair":[1]}}` report the wrong position (`x.pair.1`, input None).
  Confirmed with pydantic-core alone: the same tuple schema accepts `()` under
  `validate_default=False` and rejects it under `True`.
- case table (instances A `{"x":{"pair":[]}}`, B `["a"]`, C `["a",1]`,
  D `["a",1,true]`, ctl `[1]`, schema with `pair: prefixItems [string, integer]`
  under `x` with `additionalProperties: {"type":"string"}`):

  | implementation | A | B | C | D | ctl |
  |---|---|---|---|---|---|
  | python-sdk@77001c9 (jsonschema 2020-12) | valid | valid | valid | valid | invalid at `pair/0` |
  | typescript-sdk@5ee54ee3 (Ajv2020) | valid | valid | valid | valid | invalid at `pair/0` |
  | go-sdk@a6e937f (jsonschema-go) | valid | valid | valid | valid | invalid at `prefixItems/0` |
  | rust-sdk@165f8fd | no output validation | | | | |
  | zig mcp@2085db9 | valid | valid | valid | valid | invalid at `/x/pair/0` |
  | fastmcp main | **rejected** (`pair.0`, None) | **rejected** (`pair.1`, None) | ok | ok | rejected at the wrong position |

  Every reference agrees the short arrays are valid. Fix direction (not
  applied): pass `validate_default=False` on each placeholder's
  `with_default_schema`, so the tuple no longer depends on the enclosing
  model's config.
- against: needs a short array, an unpinned prefix and `additionalProperties`
  on the enclosing object, so it is narrow. But the instance is plainly valid
  and the failure discards the whole result. Kept; not fixed (only F1 and F3
  were authorized).

## model corrections (harness was wrong, not fastmcp)

- a mounted child's own `disable()` cannot be overridden by the parent's
  `enable()`: `FastMCPProvider` lists through the child's `list_tools()`, which
  already filtered. The visibility docs' "the server has final say" is about
  provider-level transforms on one server, not mounted servers. The model now
  treats the child's hide as final.

## case tables against other MCP implementations

Checkouts: python-sdk@77001c9 (installed `mcp` 2.3.0 has the same lines),
typescript-sdk@5ee54ee3, go-sdk@a6e937f (jsonschema-go v0.4.3),
rust-sdk@165f8fd, zig mcp (tangled.org/zzstoatzz.io/mcp)@2085db9,
fastmcp@db5f1e50c. F1 and F3 were run through each reference's own validator
(jsonschema, Ajv 8.20 with the TS SDK's options, jsonschema-go, the zig
validator under `zig test`).

### F1: `{"type":"object","properties":{"meta":{"type":"object","default":null}}}`, instance `{"meta":{}}`

| implementation | client validates output? | `default` | instance |
|---|---|---|---|
| python-sdk | yes, `jsonschema` Draft 2020-12, report only (`src/mcp/client/session.py:1118-1181`) | annotation | valid |
| typescript-sdk | yes, Ajv2020 / cfworker, no `useDefaults` (`packages/client/src/client/client.ts:2543-2551`, `core-internal/src/validators/ajvProvider.ts:30-38`) | annotation | valid |
| go-sdk | no client validation (`mcp/client.go:1236-1266`); server-side typed tools run `ApplyDefaults` (fills missing keys only) then `Validate` | fills missing keys; never merges a parent default into a present `{}` | valid |
| rust-sdk | no output validation (`crates/rmcp/src/model.rs:4393-4399`) | n/a | n/a |
| zig mcp | yes, hand-rolled 2020-12, read-only (`src/client.zig:1063-1073`, `src/json_schema.zig:64-72`) | annotation | valid |
| fastmcp before fix | yes, schema → pydantic type; `_merge_defaults` calls `dict(None)` (`json_schema_type.py:1574`) | merged into data | **TypeError, `data=None`** |
| fastmcp after fix (`185303b28`) | same | non-object default ignored | valid, `meta == {}` |

The fix matches every reference. Not covered by any reference: fastmcp
still merges an *object* default into a provided `{}` (F2). No SDK does this;
Go comes closest and still leaves a present `{}` alone. The fix keeps the
merge unchanged (mirror test) as existing behaviour, not because a reference
supports it.

### F3: recursive `$ref` through an unpinned `prefixItems` array

Schema: `N = {"type":"object","properties":{"kids":{"type":"array","prefixItems":[{"type":"string"}],"items":{"$ref":"#/$defs/N"}}}}`
under an object root. Instances: `{"r":[]}`, `{"r":[{"kids":["a",{"kids":[]}]}]}`
valid; `kids:["a","b"]` and `kids:[1]` invalid.

| implementation | draft | valid instances | invalid controls |
|---|---|---|---|
| python-sdk | 2020-12 when no `$schema` (`session.py:1175-1179`) | accepted | rejected at the right position |
| typescript-sdk | Ajv2020 when no `$schema`; classic Ajv for a draft-07 `$schema` (`ajvProvider.ts:45-47,114-127`) | accepted | rejected |
| go-sdk | jsonschema-go, 2020-12 by default (`resolve.go:49-60`, `validate.go:318-362`); server side only | accepted | rejected |
| rust-sdk | no output validation | n/a | n/a |
| zig mcp | always 2020-12, recursion bounded at depth 64 (`json_schema.zig:30-33,486-499`) | accepted | rejected |
| fastmcp before fix | pydantic conversion; ForwardRef hidden in a closure (`json_schema_type.py:813-914`) | **rejected: class not fully defined** | rejected |
| fastmcp after fix (`685dfca4f`) | same | accepted | rejected |

Every validating reference agrees with the fix direction. Correction to the
entry above: the trigger is any recursive `$ref` inside an *unpinned*
positional array, in a prefix position as well as the tail; only arrays whose
`minItems == maxItems == len(prefixItems)` (pydantic's own tuples) avoided it.

### F4: explicit version requested for an unversioned component

| implementation | component versions? | unknown tool / prompt / resource |
|---|---|---|
| MCP spec 2025-11-25, 2026-07-28 | none; `CallToolRequestParams` is `name` + `arguments`; the only `version` is `Implementation.version` (`spec.types.2026-07-28.ts:1857-1866`) | -32602 Invalid params (resources were -32002 through 2025-11-25) |
| python-sdk | none | tool: `isError` result "Unknown tool"; prompt: `INVALID_PARAMS` (`tool_manager.py:83-85`, `server.py:437-455`) |
| typescript-sdk | none | `ProtocolError(InvalidParams, "Tool X not found")` (`packages/server/src/server/mcp.ts:333-339`) |
| go-sdk | none | `CodeInvalidParams "unknown tool"`; `ResourceNotFoundError` (`mcp/server.go:1066-1072,1138`) |
| rust-sdk | none | `invalid_params("tool not found")` (`router/tool.rs:566-571`) |
| zig mcp | none | handler's choice; docs recommend `invalid_params` (`src/server.zig:140`) |
| fastmcp | yes, a fastmcp extension in `_meta.fastmcp.version` | `VersionSpec.matches(None, match_none=True)` is `True` (`utilities/versions.py:55-71`, docstring `:31-35`), so `eq="1"` matches an unversioned tool (`local_provider.py:340-358`); asserted on purpose by `tests/server/versioning/test_filtering.py:646-672` |

No reference has component versions, so none can settle F4. The code, its
docstring and three tests intend the exemption; only
`docs/servers/versioning.mdx:210` promises a NotFound. The choice is between
fixing that sentence and flipping explicit `eq` requests to
`match_none=False` (and those tests). Held for nate's decision. Note: if
version-not-found becomes an error it will surface as fastmcp's `isError`
"Unknown tool" result, not the -32602 the spec and the TS/Go/Rust SDKs use.
