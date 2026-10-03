from __future__ import annotations

import time

import pytest
from mcp.client.caching import CacheEntry, CacheKey

from fastmcp.client.caching import KeyValueResponseCacheStore
from fastmcp.skills.models import GetSkillResult, ListSkillsResult, Skill

DIGEST = f"sha256:{'a' * 64}"


def make_skill() -> Skill:
    return Skill.model_validate(
        {
            "uri": "skill://review/SKILL.md",
            "frontmatter": {
                "name": "review",
                "description": "Review a change",
            },
            "resources": [
                {
                    "uri": "skill://review/SKILL.md",
                    "digest": DIGEST,
                    "size": 42,
                }
            ],
        }
    )


@pytest.mark.parametrize(
    ("method", "params_key", "result"),
    [
        (
            "skills/list",
            "",
            ListSkillsResult(
                skills=[make_skill()], ttl_ms=60_000, cache_scope="public"
            ),
        ),
        (
            "skills/get",
            "skill://review/SKILL.md",
            GetSkillResult(skill=make_skill(), ttl_ms=60_000, cache_scope="public"),
        ),
    ],
    ids=["list", "get"],
)
async def test_persistent_cache_round_trips_skills_results(
    method: str,
    params_key: str,
    result: ListSkillsResult | GetSkillResult,
) -> None:
    store = KeyValueResponseCacheStore()
    key = CacheKey(method, params_key, "test")
    entry = CacheEntry(
        value=result,
        scope="public",
        expires_at=time.time() + 60,
    )

    await store.set(key, entry)
    cached = await store.get(key)

    assert cached is not None
    assert type(cached.value) is type(result)
    assert cached.value == result
