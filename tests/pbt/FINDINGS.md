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
| component machine, round 2: mount with or without a namespace (2026-11-19) | 3k histories | not yet measured | F6 (warned, #6613), F7 (#6612), F8 (#6611) |
| component machine through a `ProxyProvider` front server (2026-11-21) | 5-minute campaign | shares the machine's mutants | clean |
| tool transforms: rename, hide, defaults, required, two layers, deferred config (2026-11-21) | 1k cases, 5-minute campaign | 4/4: 10, 9, 8 and 8 of 10 seeds at 200 cases | F9 |
| OpenAPI tool sends the described request (2026-11-21) | 1k cases | 4/4 (explode default 9/10, pipe delimiter 9/10, boolean text 10/10, path quoting 10/10) | clean (K4) |
| OAuth proxy machine: codes, refresh, revocation, concurrent redemption (2026-11-21) | 1k histories | 3/3 at 10/10 seeds (an "already used" check is not planted: the lock and re-read mask it) | F10 |
| elicitation: every requested schema is a spec form; the server gets what the user chose, through a `Client` (2026-11-22) | 1.5k cases each | `elicitation`: 4/4 at 10/10 seeds | F14, F15 |
| OpenAPI path, header, cookie and object query parameters read back by a spec decoder (2026-11-22) | 3k cases | `openapi_params`: 5/5, 10/10 except cookie explode default at 8/10 | F16 |

F1, F3 and F5 are fixed on `main` (#6552, #6553, #6572), so their probes
report fixed and the harness checks those shapes again. F2 is fixed by keeping
the merge and falling back to the provided object when the merged one is
invalid. F4 is the documented behaviour: the docs now describe the fallback.

Findings, most to least serious: F1 (crash on a non-object default for an
object property, reachable end to end), F4 (explicit version ran an
unversioned component while the versioning docs said it would fail; the docs
now match the code), F3 (recursive `prefixItems` tail made output validation
always fail; foreign schemas only). F2 (found on the fixed tree) turned the
object-default merge into a real rejection when typed `additionalProperties`
validate the merged keys; a merged default that breaks the provided object is
now dropped for that property only.

## fixes and the mutants that undo them

| finding | fix | undo mutant(s) on a tree with the fixes merged, 300 examples × 10 seeds |
|---|---|---|
| F1 | #6552 | nested guard removed: 10/10; root guard removed: 10/10 |
| F3 | #6553 | positions read from the closure again: 10/10 |
| F5 | #6572 | placeholders inherit `validate_default` again: 10/10 (3/10 before drawing the shape directly) |
| F2 | this branch | a broken merged default is never dropped: 5/5 (1/5 before drawing a typed `additionalProperties` beside the default) |
| F4 | docs only | unversioned never matches a requested version: 5/5 (the machine now models the fallback) |

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

### K4. a non-ASCII OpenAPI header value fails the call

- property: openapi tool sends the described request
- minimal: header parameter `X-Trace` (string), value `"ü"` → the call
  returns an error before any request is sent.
- reachable: yes, but the refusal is httpx's: header values must be ASCII
  (RFC 9110 recommends it), and httpx raises before sending.
- against: not a fastmcp choice. The harness draws ASCII header values.

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

### F6. versioned and unversioned components under one name, from two servers

- status: documented and warned about (#6613): a listing that sees the mix
  logs one warning per name. The behaviour itself is unchanged.
- property: component machine with `mount(child)` and no namespace. One
  server refuses to mix versioned and unversioned components under one name
  (docs/servers/versioning.mdx); nothing refuses it across a mount.
- minimal: parent `t` unversioned, child `t` version `"1"`. `list_tools()`
  shows only the child's `t@1` (`versions: ["1"]`), the call without a version
  runs `child@1`, and `version="2"` runs the parent's unversioned `t`, which
  the listing never showed.
- blob: `@reproduce_failure('6.170.15', b'AXicY3BkcGSEYlQIEmECEgwgDEaOLAwMcFmYKBMQwfQAAEy6CDI=')`
  (resource shape: parent `res://r0` unversioned, child `res://r0@1` hidden by
  tag; the read without a version fails although the parent's is enabled).
- frequency: 21.7% of 3k histories touch the shape (registered, not asserted).
- reachable: yes, by mounting two servers without a namespace where one
  versions a name and the other does not. The aggregate already warns about
  exact duplicate keys, but not about this.
- against: the composition docs only say "highest matching version" and
  "first registered wins" for equal keys; neither covers a mix, so there is no
  documented answer to violate. The versioning docs now carry a caveat
  (#6582). Kept as an unspecified region, not a bug, until a behaviour is
  chosen.

### F7. equal versions from two servers are listed twice in `versions`

- status: fixed (#6612). Each shared version is listed once.
- property: component machine listing — `meta.fastmcp.versions` lists the
  enabled versions, highest first.
- minimal: parent `t@1`, child `t@1`, no namespace: `versions == ["1", "1"]`.
- blob: `@reproduce_failure('6.170.15', b'AXicY3BkcGSEYiYgwcCAzECBMAIAjL8FXg==')`
- frequency: 7.4% of 3k histories.
- reachable: yes, by any un-namespaced mount where both servers carry the same
  version of a name. Cosmetic: a client building a version picker shows a
  duplicate, and both entries resolve to the parent's.
- against: the duplicate is the honest count of providers, and only the
  parent's is reachable anyway. But `versions` is documented as the versions a
  client can request, and "1" is one version. Kept.

### F8. a hidden component shadows the mounted component listed in its place

- status: fixed (#6611). A call answers with the shown twin when the lookup
  returns a hidden one.
- property: component machine `call` — what a listing shows, a call without a
  version runs.
- minimal: parent `t` (no tags), child `t` tagged `x`, `mount(child)`,
  `parent.enable(tags={"x"}, only=True)`. `list_tools()` shows `t` (the
  child's), and `call_tool("t", {})` raises `Unknown tool: 't'`. The same
  happens with equal versions (`t@1` on both), with `version="1"`, and for
  prompts and resources.
- blob: `@reproduce_failure('6.170.15', b'AXicY3BkcGQEYxCJBYIIFgZGmCyIARZlReKBCCaovCMDAIpfCPk=')`
- frequency: 0.12% of 3k histories; the shape needs two equal keys and a rule
  that hides only the parent's.
- reachable: yes. Mount a server without a namespace that defines a tool the
  parent also defines, then hide the parent's by tag or name: the client sees
  the tool and cannot call it.
- cause: `AggregateProvider._get_highest_version_result` picks one component
  across providers (`max`, first registered on ties) before the server applies
  visibility, and `FastMCP.get_tool`'s fallback (`_highest_available_version`)
  retries by version only, so it skips unversioned candidates and asks for the
  same tied version again. Listing filters first and dedupes after, so the two
  paths disagree.
- against: none found. Listing and calling disagree, and neither doc says a
  hidden component should block one that is shown.

### F9. an invalid deferred tool transform breaks every listing and unrelated calls

- status: fixed (this branch). A config that cannot apply hides its own tool
  and logs why; the harness now checks that every other tool still works.
- property: tool transform matches its model — construction is refused exactly
  when the docs say so.
- minimal:
  ```python
  @server.tool
  def parent(a: int) -> int: ...
  @server.tool
  def other() -> str: ...
  server.add_transform(ToolTransform({"parent": ToolTransformConfig(
      name="t0", arguments={"a": ArgTransformConfig(hide=True)})}))
  ```
  `add_transform` accepts it. Afterwards `list_tools()` and
  `call_tool("other", {})` both return `Internal server error`; the server log
  has `ValueError: Hidden parameter 'a' has no default value`.
- frequency: every drawn deferred layer that hides a required argument or maps
  two arguments to one name: 3.7% of 1k cases, 13% of the deferred ones.
- reachable: yes, from config files and `ToolTransformConfig` alike. One bad
  entry takes down the whole tool surface, not just the transformed tool.
- cause: `Tool.from_tool` validates eagerly, so the same options raise at
  construction there; the deferred path builds the tool lazily on every
  `list_tools`/`get_tool`, so the `ValueError` escapes on each request.
- against: none found. `Tool.from_tool` refuses the same options up front, so
  the two paths disagree.

### F10. OAuth proxy access-token revocation is per grant, and refresh undoes it

- status: fixed (this branch). The revocation route retires the presented
  token's JTI mapping, so a refresh cannot revive it. Revoking still revokes
  the grant's upstream token, which RFC 7009 2.1 allows: the grant's other
  access tokens stop validating until a refresh, and the model now says so.
- property: OAuth proxy machine — a revoked access token stays dead; revoking
  one token leaves other live tokens alone.
- minimal: redeem a code (access token A1, refresh R1), `revoke_token(A1)`,
  refresh with R1 (A2), then `load_access_token(A1)` validates again. Also:
  refresh first (A2), then `revoke_token(A1)` makes A2 invalid.
- frequency: 17% of 1k histories reach a token in a grant with a revoked access token.
- reachable: yes, with any upstream that has a revocation endpoint. A client
  that logs out (revokes its access token) and still holds the refresh token,
  or a second session on the same grant, sees the wrong token live or dead.
- cause: every fastmcp access token of a grant maps through `JTIMapping` to
  one `upstream_token_id`, and `load_access_token` validates the grant's
  current upstream token. Revoking an access token revokes that current
  upstream token upstream and deletes nothing locally; a refresh writes a new
  upstream token into the same record, so every old JTI validates again.
- against: revocation of access tokens is optional under RFC 7009, and the
  revoked token is short-lived. But `revoke_token` reports success, and the
  token comes back.
- related, read from the code, not asserted: `revoke_token` logs upstream
  revocation failures as a warning and still succeeds; RFC 7009 lets the
  server answer 503 instead.

### F14. a choice with one option is sent as `const`, and a multi-select of one option is refused

- status: open. Numbers F11 to F13 belong to the auth-guard and middleware
  branches.
- property: elicitation, every requested schema is a spec form
  (`test_every_requested_schema_is_a_spec_form`).
- minimal: `ctx.elicit("pick", ["a"])` sends
  `{"type": "string", "const": "a"}`; `ctx.elicit("pick", list[Literal["a"]])`
  raises `TypeError` before anything is sent. `[["a"]]` (the shorthand) works.
- frequency: any choice list built from data that happens to hold one item.
- reachable: yes; choices often come from data ("which of your workspaces?"),
  and one item is a common case.
- spec: MCP 2025-11-25 `PrimitiveSchemaDefinition` has no `const`. The python
  SDK's own types read the property as a plain `StringSchema` and drop the
  `const` (run, below); the TypeScript SDK's zod schemas do the same
  (read from `types.js`, not run). The user is shown a free text box, and any
  answer but `a` is then rejected by the server.
- cause: pydantic renders a one-member `Literal` as `const`; the enum
  handling in `ElicitationJsonSchema` only rewrites `enum`.
- fix direction: render a one-member `Literal`/`Enum` as `enum: [value]`, in
  single and multi-select.

### F15. integer choices are sent as an integer `enum`, which spec clients read as a free number

- status: open.
- property: as F14.
- minimal: `ctx.elicit("how many?", Literal[1, 2])` (or an `Enum` with int
  values) sends `{"type": "integer", "enum": [1, 2]}`.
- frequency: any integer `Literal` or int-valued `Enum`.
- reachable: yes, e.g. a rating or a count from a fixed set.
- spec: enum shapes are string-only. The python SDK's types read the property
  as `NumberSchema` and drop `enum` (run); fastmcp's own client honours it,
  so fastmcp to fastmcp works and other clients show a number box.
- note: the python SDK sends the same schema for an integer `Literal`, so this
  is shared, not a fastmcp divergence. Options: send the choices as strings
  and convert back on accept, or refuse integer choices with a clear error.

### F16. a cookie value is sent raw, so `;` sets cookies the call never named

- status: open.
- property: OpenAPI parameters read back as sent
  (`test_each_parameter_reaches_the_backend_as_sent`).
- minimal: a string cookie parameter `session` called with
  `"abc; role=admin"` sends `Cookie: session=abc; role=admin`; the backend
  reads two cookies. Exploded arrays and objects (the default) do the same per
  item. A non-ASCII value fails the call (httpx refuses the header).
- frequency: any scalar or exploded cookie value holding `;`.
- reachable: yes. Tool arguments come from a model, so text the model was shown
  can choose them; this lets it add any cookie to the backend request.
- cause: `_serialize_cookie_params` escapes `%`, `,`, `;` and `=` only for the
  non-exploded forms (`_cookie_joined_item`); scalars and exploded items go
  through `_cookie_scalar_to_str` unescaped.
- spec: RFC 6265 cookie values exclude `;`, `,`, whitespace and non-ASCII;
  OpenAPI 3.1 `form` style (RFC 6570) percent-encodes them.
- fix direction: percent-encode every cookie value and exploded property name
  the way the joined form already does (at least `;`, and non-ASCII).

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

### F6–F8: one name answered by two servers

| implementation | two components under one name? | enable/disable | list vs call |
|---|---|---|---|
| MCP spec 2026-07-28 | no; a name identifies one tool | none | `tools/list` advertises what `tools/call` accepts |
| typescript-sdk@5ee54ee | no; registering a taken name throws (`mcp.ts:1144`) | `RegisteredTool.disable()`; list filters `enabled` (`packages/server/src/server/mcp.ts:300`), call checks the same flag (`:337`) | agree |
| go-sdk@a6e937f | no; `AddTool` replaces by name (`mcp/server.go:315,381`) | none (remove only) | agree |
| fastmcp | yes, across an un-namespaced mount | visibility rules applied after the cross-provider pick | F8: listed but not callable; F6: callable but not listed |

No reference composes servers, so none can say which of two components should
answer. Every reference with enable/disable uses one predicate for listing and
calling, which is the property F8 breaks.

### F9: an invalid transform

No reference SDK has tool transforms; the only comparison is fastmcp's own
eager path, `Tool.from_tool`, which raises `ValueError` at construction for
the same options.

### F10: revoking a proxied access token

| implementation | proxies an upstream AS? | revoked access token after a refresh |
|---|---|---|
| RFC 7009 | n/a | access revocation optional; a revoked token must not validate |
| typescript-sdk@5ee54ee | yes, `ProxyOAuthServerProvider` passes upstream tokens through (`packages/server/src/server/auth/providers/proxyProvider.ts`) | dead: the client holds the upstream token, which the upstream revoked |
| python-sdk@77001c9, go-sdk@a6e937f, rust-sdk@165f8fd | no proxy provider | n/a |
| fastmcp | yes, issues its own JWTs mapped to one upstream record per grant | F10: live again |

### F14 and F15: what the python SDK's own types make of fastmcp's properties

Run through `mcp_types._v2025_11_25.PrimitiveSchemaDefinition` (installed
`mcp` 2.3.0), the type python-sdk's `render_elicitation_schema` validates
against:

| property fastmcp sends | python-sdk reads it as | kept |
|---|---|---|
| `{"type": "string", "const": "a"}` | `StringSchema` | `type` only, the choice is dropped |
| `{"type": "integer", "enum": [1, 2]}` | `NumberSchema` | `type` only, the choices are dropped |
| `{"type": "array", "items": {"type": "string", "enum": ["a"]}}` (what `[["a"]]` sends) | `UntitledMultiSelectEnumSchema` | all |

python-sdk's own server sends the same `const` and integer `enum` for those
types; it refuses `list[Literal["a"]]` and an `Enum` field (`$ref`) outright.
typescript-sdk@1.32.2 zod (`dist/cjs/types.js:1656-1787`): zod objects strip
unknown keys, so `const` and an integer `enum` are dropped the same way (read,
not run).

