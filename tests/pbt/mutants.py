"""Plant known bugs and count how many seeds catch each.

    uv run python -m tests.pbt.mutants [target ...]

Each mutant is an exact string swap in a source file, restored afterwards.
A mutant caught by few seeds is a generator problem, not a case-count problem.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = "fastmcp_slim/fastmcp/resources/template.py"
SCHEMA = "fastmcp_slim/fastmcp/utilities/json_schema.py"
SCHEMA_TYPE = "fastmcp_slim/fastmcp/utilities/json_schema_type.py"
MACHINE = "tests/pbt/test_components_machine.py"
TRANSFORM = "fastmcp_slim/fastmcp/tools/tool_transform.py"
DIRECTOR = "fastmcp_slim/fastmcp/utilities/openapi/director.py"
OAUTH = "fastmcp_slim/fastmcp/server/auth/oauth_proxy/proxy.py"
SSRF = "fastmcp_slim/fastmcp/server/auth/ssrf.py"
REDIRECT = "fastmcp_slim/fastmcp/server/auth/redirect_validation.py"
CIMD = "fastmcp_slim/fastmcp/server/auth/cimd.py"
GUARDS = "tests/pbt/test_auth_url_guards.py"
AUTHZ = "fastmcp_slim/fastmcp/server/middleware/authorization.py"
CACHING = "fastmcp_slim/fastmcp/server/middleware/caching.py"
RATE = "fastmcp_slim/fastmcp/server/middleware/rate_limiting.py"
MW = "tests/pbt/test_middleware.py"

MUTANTS: dict[str, list[dict[str, str]]] = {
    "uri_template": [
        {
            "name": "simple value keeps slash",
            "file": TEMPLATE,
            "old": 'quote(value_str, safe="/" if wildcard else "")',
            "new": 'quote(value_str, safe="/")',
            "test": "tests/pbt/test_uri_template.py",
        },
        {
            "name": "path values not unquoted",
            "file": TEMPLATE,
            "old": "{k: unquote(v) for k, v in match.groupdict().items()}",
            "new": "{k: v for k, v in match.groupdict().items()}",
            "test": "tests/pbt/test_uri_template.py",
        },
        {
            "name": "blank query values dropped",
            "file": TEMPLATE,
            "old": "parse_qs(query_string, keep_blank_values=True)",
            "new": "parse_qs(query_string)",
            "test": "tests/pbt/test_uri_template.py",
        },
        {
            "name": "every query block opens with ?",
            "file": TEMPLATE,
            "old": 'separator = "&" if query_started else "?"',
            "new": 'separator = "?"',
            "test": "tests/pbt/test_uri_template.py",
        },
    ],
    "resource_read": [
        {
            "name": "template drops its list query params",
            "file": TEMPLATE,
            "old": 'list_params=self._list_query_params if "?" in uri else (),',
            "new": "list_params=(),",
            "test": "tests/pbt/test_resource_read.py",
        },
        {
            "name": "literals match only as written",
            "file": TEMPLATE,
            "old": 'out.append(f"(?:{re.escape(ch)}|(?i:{re.escape(encoded)}))")',
            "new": "out.append(re.escape(ch))",
            "test": "tests/pbt/test_resource_read.py",
        },
    ],
    "schema_fixes": [
        {
            "name": "F1 undone: nested non-object default merged",
            "fixes": "F1",
            "file": SCHEMA_TYPE,
            "old": "            if not isinstance(nested_default, Mapping):\n"
            "                nested_default = None\n",
            "new": "",
            "test": "tests/pbt/test_json_schema.py::test_adapter_accepts_valid_instances",
        },
        {
            "name": "F1 undone: root non-object default merged",
            "fixes": "F1",
            "file": SCHEMA_TYPE,
            "old": 'elif isinstance(schema.get("default"), Mapping):',
            "new": 'elif "default" in schema:',
            "test": "tests/pbt/test_json_schema.py::test_adapter_accepts_valid_instances",
        },
        {
            "name": "F3 undone: positions read from the closure",
            "fixes": "F3",
            "file": SCHEMA_TYPE,
            "old": "positions_type, tail_type = get_args(source)\n"
            "        resolved_prefix = get_args(positions_type)",
            "new": "resolved_prefix, tail_type = prefix_types, item_type",
            "test": "tests/pbt/test_json_schema.py::test_adapter_accepts_valid_instances",
        },
        {
            "name": "F5 undone: placeholders inherit validate_default",
            "fixes": "F5",
            "file": SCHEMA_TYPE,
            "old": "default=None,\n                    validate_default=False,",
            "new": "default=None,",
            "test": "tests/pbt/test_json_schema.py::test_adapter_accepts_valid_instances",
        },
        {
            "name": "F2 undone: a broken merged default is never dropped",
            "fixes": "F2",
            "file": SCHEMA_TYPE,
            "old": "            if not broken:\n                raise\n",
            "new": "            raise\n",
            "test": "tests/pbt/test_json_schema.py::test_adapter_accepts_valid_instances",
        },
    ],
    "components": [
        {
            "name": "pagination skips an item",
            "file": "fastmcp_slim/fastmcp/utilities/pagination.py",
            "old": "next_cursor = CursorState(offset=end).encode()",
            "new": "next_cursor = CursorState(offset=end + 1).encode()",
            "test": MACHINE,
        },
        {
            "name": "F4 undone: unversioned never matches a requested version",
            "file": "fastmcp_slim/fastmcp/utilities/versions.py",
            "old": "        if version is None:\n            return match_none\n",
            "new": "        if version is None:\n            return False\n",
            "test": MACHINE,
        },
        {
            "name": "lowest version served",
            "file": "fastmcp_slim/fastmcp/utilities/versions.py",
            "old": "highest: C = max(versions, key=version_sort_key)",
            "new": "highest: C = min(versions, key=version_sort_key)",
            "test": MACHINE,
        },
        {
            "name": "first visibility rule wins",
            "file": "fastmcp_slim/fastmcp/server/transforms/visibility.py",
            "old": 'new_internal = {**old_internal, "visibility": self._enabled}',
            "new": 'new_internal = {"visibility": self._enabled, **old_internal}',
            "test": MACHINE,
        },
        {
            "name": "removing one tool version removes its siblings",
            "file": "fastmcp_slim/fastmcp/server/providers/local_provider/local_provider.py",
            "old": 'key = f"{Tool.make_key(name)}@{version}"',
            "new": 'key = f"{Tool.make_key(name)}@{version}"\n'
            "            for k in [k for k, c in self._components.items() if isinstance(c, Tool) and c.name == name and k != key]:\n"
            "                self._remove_component(k)",
            "test": MACHINE,
        },
    ],
    "tool_transform": [
        {
            "name": "hidden argument loses its default",
            "file": TRANSFORM,
            "old": "                    parent_args[old_name] = transform.default\n",
            "new": "                    pass\n",
            "test": "tests/pbt/test_tool_transform.py",
        },
        {
            "name": "missing required arguments accepted",
            "file": TRANSFORM,
            "old": "            missing_args = new_required - provided_args",
            "new": "            missing_args = set()",
            "test": "tests/pbt/test_tool_transform.py",
        },
        {
            "name": "renamed argument reaches the parent under its new name",
            "file": TRANSFORM,
            "old": "                old_name = new_to_old.get(new_name, new_name)",
            "new": "                old_name = new_name",
            "test": "tests/pbt/test_tool_transform.py",
        },
        {
            "name": "unknown arguments accepted",
            "file": TRANSFORM,
            "old": "            if unknown_args:",
            "new": "            if False:",
            "test": "tests/pbt/test_tool_transform.py",
        },
    ],
    "openapi": [
        {
            "name": "non-form arrays explode by default",
            "file": DIRECTOR,
            "old": "                    explode = False\n",
            "new": "                    explode = True\n",
            "test": "tests/pbt/test_openapi.py",
        },
        {
            "name": "pipeDelimited joins with commas",
            "file": DIRECTOR,
            "old": '        "pipeDelimited": "|",',
            "new": '        "pipeDelimited": ",",',
            "test": "tests/pbt/test_openapi.py",
        },
        {
            "name": "booleans sent as True/False",
            "file": DIRECTOR,
            "old": '        return "true" if value else "false"',
            "new": "        return str(value)",
            "test": "tests/pbt/test_openapi.py",
        },
        {
            "name": "slash left unquoted in path values",
            "file": DIRECTOR,
            "old": '        return quote(serialized, safe="")',
            "new": '        return quote(serialized, safe="/")',
            "test": "tests/pbt/test_openapi.py",
        },
    ],
    "oauth_proxy": [
        {
            "name": "authorization code reusable",
            "file": OAUTH,
            "old": "        if not await self._code_store.delete(key=authorization_code.code):",
            "new": "        if not (await self._code_store.delete(key=authorization_code.code) or True):",
            "test": "tests/pbt/test_oauth_proxy_machine.py",
        },
        {
            "name": "refresh token usable by another client",
            "file": OAUTH,
            "old": "        if metadata.client_id != client.client_id:",
            "new": "        if False:",
            "test": "tests/pbt/test_oauth_proxy_machine.py",
        },
        {
            "name": "revoked access token's mapping kept (undoes F10)",
            "file": OAUTH,
            "old": "            await self._jti_mapping_store.delete(key=token.fastmcp_jti)",
            "new": "            pass",
            "test": "tests/pbt/test_oauth_proxy_machine.py",
            "fixes": "F10",
        },
        {
            "name": "revoked refresh token leaves its grant's access tokens live",
            "file": OAUTH,
            "old": "        await self._upstream_token_store.delete(key=jti_mapping.upstream_token_id)",
            "new": "        pass",
            "test": "tests/pbt/test_oauth_proxy_machine.py",
        },
        {
            "name": "revoked refresh token kept",
            "file": OAUTH,
            "old": (
                "            await self._refresh_token_store.delete(key=_hash_token(token.token))\n"
                "            await self._revoke_refresh_grant(token)\n"
            ),
            "new": "            pass\n",
            "test": "tests/pbt/test_oauth_proxy_machine.py",
        },
    ],
    "auth_guards": [
        {
            "name": "IPv6 wrappers not unwrapped",
            "file": SSRF,
            "old": "    if isinstance(ip, ipaddress.IPv6Address):\n        if any(",
            "new": "    if False:\n        if any(",
            "test": f"{GUARDS}::test_ipv6_never_unblocks_a_blocked_ipv4",
        },
        {
            "name": "port 0 rewritten to 443",
            "file": SSRF,
            "old": "    if port is None:\n        port = 443\n    elif port == 0:",
            "new": "    if not port:\n        port = 443\n    elif False:",
            "test": f"{GUARDS}::test_pinned_request_goes_where_validation_looked",
        },
        {
            "name": "ISATAP not unwrapped",
            "file": SSRF,
            "old": "    if packed[8:12] in ISATAP_INTERFACE_IDS:",
            "new": "    if False:",
            "test": f"{GUARDS}::test_ipv6_never_unblocks_a_blocked_ipv4",
        },
        {
            "name": "port read outside the guard (#5773 undone)",
            "file": SSRF,
            "old": (
                "        port = parsed.port\n"
                "    except (ValueError, AttributeError) as e:\n"
                '        raise SSRFError(f"Invalid URL: {e}") from e\n'
            ),
            "new": (
                "        pass\n"
                "    except (ValueError, AttributeError) as e:\n"
                '        raise SSRFError(f"Invalid URL: {e}") from e\n'
                "    port = parsed.port\n"
            ),
            "test": f"{GUARDS}::test_validate_url_fails_only_with_ssrf_error",
        },
        {
            "name": "userinfo allowed in redirect URIs",
            "file": REDIRECT,
            "old": "    if uri_parsed.username is not None or uri_parsed.password is not None:",
            "new": "    if False:",
            "test": f"{GUARDS}::test_a_match_lands_on_the_patterns_host",
        },
        {
            "name": "backslash allowed in the raw authority",
            "file": REDIRECT,
            "old": '    if "\\\\" in raw_parsed.netloc:',
            "new": "    if False:",
            "test": f"{GUARDS}::test_a_match_lands_on_the_patterns_host",
        },
        {
            "name": "subdomain wildcard matches any suffix",
            "file": REDIRECT,
            "old": "        return uri_host.endswith(suffix) and uri_host != pattern_host[2:]",
            "new": "        return uri_host.endswith(pattern_host[2:])",
            "test": f"{GUARDS}::test_a_match_lands_on_the_patterns_host",
        },
        {
            "name": "exact pattern ignores the query (#6000 undone)",
            "file": REDIRECT,
            "old": '    if pattern_parsed.query or "*" not in pattern:',
            "new": "    if pattern_parsed.query:",
            "test": f"{GUARDS}::test_an_exact_pattern_matches_only_itself",
        },
        {
            "name": "repeated iss kept",
            "file": REDIRECT,
            "old": '    return _drop_repeated_query_param(replace_query_param(url, "iss", iss), "iss")',
            "new": '    return replace_query_param(url, "iss", iss)',
            "test": f"{GUARDS}::test_client_redirect_carries_each_parameter_once",
        },
        {
            "name": "document scope not intersected",
            "file": CIMD,
            "old": '        filtered = " ".join(s for s in scope.split(" ") if s and s in allowed)',
            "new": '        filtered = " ".join(s for s in scope.split(" ") if s)',
            "test": f"{GUARDS}::test_document_scopes_never_widen_the_servers",
        },
        {
            "name": "negative freshness kept",
            "file": CIMD,
            "old": "        freshness_lifetime = max(0.0, expires_at - now)",
            "new": "        freshness_lifetime = expires_at - now",
            "test": f"{GUARDS}::test_cache_headers_never_break_a_fetch",
        },
    ],
    "middleware": [
        {
            "name": "tool listing not filtered",
            "file": AUTHZ,
            "old": (
                "                if await run_auth_checks(self.auth, ctx):\n"
                "                    authorized_tools.append(tool)"
            ),
            "new": "                if True:\n                    authorized_tools.append(tool)",
            "test": f"{MW}::test_listing_and_calling_agree",
        },
        {
            "name": "prompt listing not filtered",
            "file": AUTHZ,
            "old": (
                "                if await run_auth_checks(self.auth, ctx):\n"
                "                    authorized_prompts.append(prompt)"
            ),
            "new": "                if True:\n                    authorized_prompts.append(prompt)",
            "test": f"{MW}::test_listing_and_calling_agree",
        },
        {
            "name": "tool denial names only its own layer's scopes",
            "file": AUTHZ,
            "old": (
                "                missing = self._chain_shortfall(missing, ctx, fastmcp.fastmcp)\n"
                "                raise InsufficientScopeError(\n"
                "                    missing,\n"
                "                    message=(\n"
                "                        f\"Authorization failed for tool '{tool_name}': \"\n"
            ),
            "new": (
                "                raise InsufficientScopeError(\n"
                "                    missing,\n"
                "                    message=(\n"
                "                        f\"Authorization failed for tool '{tool_name}': \"\n"
            ),
            "test": f"{MW}::test_a_denial_names_every_scope_the_chain_still_needs",
        },
        {
            "name": "resource denial names only its own layer's scopes",
            "file": AUTHZ,
            "old": (
                "                missing = self._chain_shortfall(missing, ctx, fastmcp.fastmcp)\n"
                "                raise InsufficientScopeError(\n"
                "                    missing,\n"
                "                    message=(\n"
                "                        f\"Authorization failed for resource '{uri}': \"\n"
            ),
            "new": (
                "                raise InsufficientScopeError(\n"
                "                    missing,\n"
                "                    message=(\n"
                "                        f\"Authorization failed for resource '{uri}': \"\n"
            ),
            "test": f"{MW}::test_a_denial_names_every_scope_the_chain_still_needs",
        },
        {
            "name": "prompt denial names only its own layer's scopes",
            "file": AUTHZ,
            "old": (
                "                missing = self._chain_shortfall(missing, ctx, fastmcp.fastmcp)\n"
                "                raise InsufficientScopeError(\n"
                "                    missing,\n"
                "                    message=(\n"
                "                        f\"Authorization failed for prompt '{prompt_name}': \"\n"
            ),
            "new": (
                "                raise InsufficientScopeError(\n"
                "                    missing,\n"
                "                    message=(\n"
                "                        f\"Authorization failed for prompt '{prompt_name}': \"\n"
            ),
            "test": f"{MW}::test_a_denial_names_every_scope_the_chain_still_needs",
        },
        {
            "name": "cache ignores the requested version",
            "file": CACHING,
            "old": "    return f\"__version__:{_get_arguments_str({'version': version})}\"",
            "new": "    return DEFAULT_VERSION_CACHE_KEY",
            "test": f"{MW}::test_a_cached_server_answers_like_an_uncached_one",
        },
        {
            "name": "cache shared across callers",
            "file": CACHING,
            "old": "    return _hash_cache_key(token.token)",
            "new": "    return ANONYMOUS_AUTH_KEY",
            "test": f"{MW}::test_a_cached_server_answers_like_an_uncached_one",
        },
        {
            "name": "cache ignores tool arguments",
            "file": CACHING,
            "old": (
                '        f"{auth_key}:{modern_protocol}:{_get_component_version_cache_key(msg)}:{msg.name}:"\n'
                '        f"{_get_arguments_str(msg.arguments)}"'
            ),
            "new": '        f"{auth_key}:{modern_protocol}:{_get_component_version_cache_key(msg)}:{msg.name}:"',
            "test": f"{MW}::test_a_cached_server_answers_like_an_uncached_one",
        },
        {
            "name": "bucket refills past capacity",
            "file": RATE,
            "old": "            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)",
            "new": "            self.tokens = self.tokens + elapsed * self.refill_rate",
            "test": f"{MW}::test_token_bucket_admits_no_more_than_burst_plus_rate",
        },
        {
            "name": "idle buckets pruned before they refill",
            "file": RATE,
            "old": "            idle_timeout = self.burst_capacity / self.max_requests_per_second",
            "new": "            idle_timeout = self.burst_capacity / self.max_requests_per_second / 4",
            "test": f"{MW}::test_token_bucket_admits_no_more_than_burst_plus_rate",
        },
        {
            "name": "sliding window admits one extra",
            "file": RATE,
            "old": "            if len(self.requests) < self.max_requests:",
            "new": "            if len(self.requests) <= self.max_requests:",
            "test": f"{MW}::test_sliding_window_admits_no_more_than_its_max",
        },
    ],
}


def run(test: str, seed: int, extra_env: dict[str, str] | None = None) -> bool:
    env = {**os.environ, "HYPOTHESIS_PROFILE": "mutants", **(extra_env or {})}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "-x", "-p", "no:cacheprovider",
         f"--hypothesis-seed={seed}"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )  # fmt: skip
    return result.returncode == 0


def main(targets: list[str], seeds: int = 10) -> int:
    missed = 0
    ran = 0
    for target in targets or list(MUTANTS):
        for m in MUTANTS[target]:
            path = ROOT / m["file"]
            original = path.read_text()
            if original.count(m["old"]) != 1:
                # Mutants that undo a fix apply only to a tree with the fix.
                print(f"{target}: {m['name']}: skipped, anchor not in this tree")
                continue
            if not run(m["test"], 0):
                print(f"{target}: unmutated code fails; fix that first")
                return 1
            try:
                path.write_text(original.replace(m["old"], m["new"]))
                env = {"PBT_ASSUME_FIXED": m["fixes"]} if "fixes" in m else None
                caught = sum(not run(m["test"], seed, env) for seed in range(seeds))
            finally:
                path.write_text(original)
            ran += 1
            missed += caught < seeds
            print(f"{target}: {m['name']}: caught {caught}/{seeds}")
    if not ran:
        print("no mutant applied to this tree; nothing was measured")
        return 1
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
