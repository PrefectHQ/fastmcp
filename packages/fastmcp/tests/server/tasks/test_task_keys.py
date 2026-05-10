"""Tests for ``fastmcp.server.tasks.keys`` — the encoding boundary that
separates authenticated and anonymous task keyspaces.

Cross-scope isolation depends on these encodings being unambiguous and
round-trippable, so the tests cover: tag dispatch (``auth``/``anon``),
the ``None`` ⇄ anonymous round trip, encoding of values that contain the
``:`` delimiter, error paths for malformed keys, and the parity between
the Docket-key prefix and the Redis-key prefix.
"""

import pytest

from fastmcp.server.tasks.keys import (
    build_task_key,
    get_client_task_id_from_key,
    parse_task_key,
    task_redis_prefix,
)

ROUND_TRIP_CASES = [
    ("client-a", "task-1", "tool", "my_tool"),
    (None, "task-1", "tool", "my_tool"),
    ("client-a", "task-1", "resource", "file://data.txt"),
    (None, "task-1", "resource", "file://data.txt"),
    ("client-a", "task-1", "template", "users://{id}"),
    ("client-a", "task-1", "prompt", "greet@1.0.0"),
    # Scope contains the inner separator used by get_task_scope (client_id|sub).
    ("client|sub-42", "task-1", "tool", "my_tool"),
    # Adversarial: scope is literally the anon tag — must not collide.
    ("anon", "task-1", "tool", "my_tool"),
    # Adversarial: scope is literally the legacy "_" sentinel.
    ("_", "task-1", "tool", "my_tool"),
    # Scope contains every delimiter we care about.
    ("a:b/c d%e|f", "task-1", "tool", "my_tool"),
    # Component identifier with colons, slashes, percent, spaces.
    ("client-a", "task-1", "resource", "https://x/y?z=1&q=a b"),
    # UUID-shaped task id (the realistic case).
    ("client-a", "0c3e9b14-3a3f-4b3a-9b1a-1d8d6e6e0c11", "tool", "t"),
]


@pytest.mark.parametrize(
    ("scope", "task_id", "task_type", "identifier"), ROUND_TRIP_CASES
)
def test_round_trip_preserves_all_fields(
    scope: str | None, task_id: str, task_type: str, identifier: str
):
    key = build_task_key(scope, task_id, task_type, identifier)
    parsed = parse_task_key(key)
    assert parsed == {
        "task_scope": scope,
        "client_task_id": task_id,
        "task_type": task_type,
        "component_identifier": identifier,
    }


@pytest.mark.parametrize(
    ("scope", "task_id", "task_type", "identifier"), ROUND_TRIP_CASES
)
def test_get_client_task_id_round_trip(
    scope: str | None, task_id: str, task_type: str, identifier: str
):
    key = build_task_key(scope, task_id, task_type, identifier)
    assert get_client_task_id_from_key(key) == task_id


def test_authenticated_key_uses_auth_tag():
    key = build_task_key("client-a", "task-1", "tool", "my_tool")
    assert key.startswith("auth:")
    assert key == "auth:client-a:task-1:tool:my_tool"


def test_anonymous_key_uses_anon_tag():
    key = build_task_key(None, "task-1", "tool", "my_tool")
    assert key.startswith("anon:")
    assert key == "anon:task-1:tool:my_tool"


def test_anonymous_and_literal_anon_scope_have_disjoint_keyspaces():
    """A real anonymous task and a (hostile) authenticated task whose scope
    literally equals "anon" must not collide."""
    anon_key = build_task_key(None, "task-1", "tool", "x")
    impostor_key = build_task_key("anon", "task-1", "tool", "x")
    assert anon_key != impostor_key
    assert parse_task_key(anon_key)["task_scope"] is None
    assert parse_task_key(impostor_key)["task_scope"] == "anon"


def test_legacy_underscore_scope_is_just_a_string_now():
    """Belt-and-suspenders: a client_id of "_" no longer aliases anonymous."""
    underscore_key = build_task_key("_", "task-1", "tool", "x")
    anon_key = build_task_key(None, "task-1", "tool", "x")
    assert underscore_key != anon_key
    assert parse_task_key(underscore_key)["task_scope"] == "_"


def test_component_identifier_with_colons_is_recovered():
    key = build_task_key("client-a", "task-1", "resource", "file://data:special.txt")
    assert parse_task_key(key)["component_identifier"] == "file://data:special.txt"


def test_scope_with_colons_is_recovered():
    key = build_task_key("a:b:c", "task-1", "tool", "t")
    parsed = parse_task_key(key)
    assert parsed["task_scope"] == "a:b:c"
    assert parsed["client_task_id"] == "task-1"


def test_scope_pipe_separator_is_preserved():
    """``get_task_scope`` composes ``client_id|sub`` — the ``|`` must survive."""
    key = build_task_key("client-a|user-42", "task-1", "tool", "t")
    assert parse_task_key(key)["task_scope"] == "client-a|user-42"


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "client-a:task-1:tool:my_tool",  # legacy untagged format
        "weird:client-a:task-1:tool:my_tool",  # unknown tag
        "auth:client-a:task-1:tool",  # missing identifier
        "auth:client-a",  # truncated
        "anon:task-1:tool",  # truncated anon
        "anon",  # tag only
        "auth",  # tag only
        ":task-1:tool:t",  # empty tag
    ],
)
def test_parse_rejects_malformed_keys(bad_key: str):
    with pytest.raises(ValueError):
        parse_task_key(bad_key)


def test_redis_prefix_authenticated():
    assert task_redis_prefix("client-a") == "fastmcp:task:auth:client-a"


def test_redis_prefix_anonymous():
    assert task_redis_prefix(None) == "fastmcp:task:anon"


def test_redis_prefix_disjoint_for_anon_vs_literal_anon_scope():
    assert task_redis_prefix(None) != task_redis_prefix("anon")


def test_redis_prefix_disjoint_for_anon_vs_literal_underscore_scope():
    assert task_redis_prefix(None) != task_redis_prefix("_")


def test_redis_prefix_encodes_special_characters():
    # Colons, slashes, pipes in the scope must not break the prefix shape.
    prefix = task_redis_prefix("client:a/b|sub")
    assert prefix.startswith("fastmcp:task:auth:")
    # Exactly four ":" delimiters: fastmcp / task / auth / encoded-scope.
    assert prefix.count(":") == 3


def test_docket_and_redis_prefixes_agree_on_partition():
    """The Docket key tag and the Redis prefix tag must always match — that is
    the load-bearing invariant for cross-scope isolation."""
    auth_docket = build_task_key("client-a", "task-1", "tool", "x")
    auth_redis = task_redis_prefix("client-a")
    assert auth_docket.split(":", 1)[0] == "auth"
    assert ":auth:" in auth_redis

    anon_docket = build_task_key(None, "task-1", "tool", "x")
    anon_redis = task_redis_prefix(None)
    assert anon_docket.split(":", 1)[0] == "anon"
    assert anon_redis.endswith(":anon")
