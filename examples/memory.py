# /// script
# dependencies = ["pydantic-ai-slim[openai]", "asyncpg", "numpy", "pgvector", "fastmcp"]
# ///

# uv pip install 'pydantic-ai-slim[openai]' asyncpg numpy pgvector fastmcp

"""
Recursive memory system inspired by the human brain's clustering of memories.
Uses OpenAI's 'text-embedding-3-small' model and pgvector for efficient similarity search.
"""

import asyncio
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Self

import asyncpg
import numpy as np
from openai import AsyncOpenAI
from pgvector.asyncpg import register_vector
from pydantic import BaseModel, Field
from pydantic_ai import Agent

import fastmcp
from fastmcp import FastMCP

MAX_DEPTH = 5
SIMILARITY_THRESHOLD = 0.7
DECAY_FACTOR = 0.99
REINFORCEMENT_FACTOR = 1.1

DEFAULT_LLM_MODEL = "openai:gpt-4o"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

# Dependencies are configured in memory.fastmcp.json
mcp = FastMCP("memory")

DB_DSN = "postgresql://postgres:postgres@localhost:54320/memory_db"
# reset memory by deleting the profile directory
PROFILE_DIR = (
    fastmcp.settings.home / os.environ.get("USER", "anon") / "memory"
).resolve()
PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    a_array = np.array(a, dtype=np.float64)
    b_array = np.array(b, dtype=np.float64)
    return np.dot(a_array, b_array) / (
        np.linalg.norm(a_array) * np.linalg.norm(b_array)
    )


async def do_ai(
    user_prompt: str,
    system_prompt: str,
    result_type: type | Annotated,
    deps=None,
) -> Any:
    agent = Agent(
        DEFAULT_LLM_MODEL,
        system_prompt=system_prompt,
        result_type=result_type,
    )
    result = await agent.run(user_prompt, deps=deps)
    return result.data


@dataclass
class Deps:
    openai: AsyncOpenAI
    pool: asyncpg.Pool


async def get_db_pool() -> asyncpg.Pool:
    async def init(conn):
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        await register_vector(conn)

    pool = await asyncpg.create_pool(DB_DSN, init=init)
    return pool


class MemoryNode(BaseModel):
    id: int | None = None
    content: str
    summary: str = ""
    importance: float = 1.0
    access_count: int = 0
    timestamp: float = Field(
        default_factory=lambda: datetime.now(timezone.utc).timestamp()
    )
    embedding: list[float]

    @classmethod
    async def from_content(cls, content: str, deps: Deps):
        embedding = await get_embedding(content, deps)
        return cls(content=content, embedding=embedding)

    async def save(self, deps: Deps):
        async with deps.pool.acquire() as conn:
            if self.id is None:
                result = await conn.fetchrow(
                    """
                    INSERT INTO memories (content, summary, importance, access_count, timestamp, embedding)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    self.content,
                    self.summary,
                    self.importance,
                    self.access_count,
                    self.timestamp,
                    self.embedding,
                )
                self.id = result["id"]
            else:
                await conn.execute(
                    """
                    UPDATE memories
                    SET content = $1, summary = $2, importance = $3,
                        access_count = $4, timestamp = $5, embedding = $6
                    WHERE id = $7
                    """,
                    self.content,
                    self.summary,
                    self.importance,
                    self.access_count,
                    self.timestamp,
                    self.embedding,
                    self.id,
                )

    async def merge_with(self, other: Self, deps: Deps):
        self.content = await do_ai(
            f"{self.content}\n\n{other.content}",
            "Combine the following two texts into a single, coherent text.",
            str,
            deps,
        )
        self.importance += other.importance
        self.access_count += other.access_count
        self.embedding = [
            (a + b) / 2 for a, b in zip(self.embedding, other.embedding, strict=True)
        ]
        self.summary = await do_ai(
            self.content, "Summarize the following text concisely.", str, deps
        )
        await self.save(deps)
        # Delete the merged node from the database
        if other.id is not None:
            await delete_memory(other.id, deps)

    def get_effective_importance(self):
        return self.importance * (1 + math.log(self.access_count + 1))


async def get_embedding(text: str, deps: Deps) -> list[float]:
    embedding_response = await deps.openai.embeddings.create(
        input=text,
        model=DEFAULT_EMBEDDING_MODEL,
    )
    return embedding_response.data[0].embedding


async def delete_memory(memory_id: int, deps: Deps):
    async with deps.pool.acquire() as conn:
        await conn.execute("DELETE FROM memories WHERE id = $1", memory_id)


async def add_memory(content: str, deps: Deps):
    new_memory = await MemoryNode.from_content(content, deps)
    await new_memory.save(deps)

    similar_memories = await find_similar_memories(new_memory.embedding, deps)
    for memory in similar_memories:
        if memory.id != new_memory.id:
            await new_memory.merge_with(memory, deps)

    await update_importance(new_memory.embedding, deps)

    await prune_memories(deps)

    return f"Remembered: {content}"


async def find_similar_memories(embedding: list[float], deps: Deps) -> list[MemoryNode]:
    async with deps.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, content, summary, importance, access_count, timestamp, embedding
            FROM memories
            ORDER BY embedding <-> $1
            LIMIT 5
            """,
            embedding,
        )
    memories = [
        MemoryNode(
            id=row["id"],
            content=row["content"],
            summary=row["summary"],
            importance=row["importance"],
            access_count=row["access_count"],
            timestamp=row["timestamp"],
            embedding=row["embedding"],
        )
        for row in rows
    ]
    return memories


async def update_importance(user_embedding: list[float], deps: Deps):
    async with deps.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, importance, access_count, embedding FROM memories"
        )
        for row in rows:
            memory_embedding = row["embedding"]
            similarity = cosine_similarity(user_embedding, memory_embedding)
            if similarity > SIMILARITY_THRESHOLD:
                new_importance = row["importance"] * REINFORCEMENT_FACTOR
                new_access_count = row["access_count"] + 1
            else:
                new_importance = row["importance"] * DECAY_FACTOR
                new_access_count = row["access_count"]
            await conn.execute(
                """
                UPDATE memories
                SET importance = $1, access_count = $2
                WHERE id = $3
                """,
                new_importance,
                new_access_count,
                row["id"],
            )


async def prune_memories(deps: Deps):
    async with deps.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, importance, access_count
            FROM memories
            ORDER BY importance DESC
            OFFSET $1
            """,
            MAX_DEPTH,
        )
        for row in rows:
            await conn.execute("DELETE FROM memories WHERE id = $1", row["id"])


async def display_memory_tree(deps: Deps) -> str:
    async with deps.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT content, summary, importance, access_count
            FROM memories
            ORDER BY importance DESC
            LIMIT $1
            """,
            MAX_DEPTH,
        )
    result = ""
    for row in rows:
        effective_importance = row["importance"] * (
            1 + math.log(row["access_count"] + 1)
        )
        summary = row["summary"] or row["content"]
        result += f"- {summary} (Importance: {effective_importance:.2f})\n"
    return result


@mcp.tool
async def remember(
    contents: Annotated[
        list[str], Field(description="List of observations or memories to store")
    ],
):
    deps = Deps(openai=AsyncOpenAI(), pool=await get_db_pool())
    try:
        return "\n".join(
            await asyncio.gather(*[add_memory(content, deps) for content in contents])
        )
    finally:
        await deps.pool.close()


@mcp.tool
async def read_profile() -> str:
    deps = Deps(openai=AsyncOpenAI(), pool=await get_db_pool())
    profile = await display_memory_tree(deps)
    await deps.pool.close()
    return profile


async def initialize_database():
    pool = await asyncpg.create_pool(
        "postgresql://postgres:postgres@localhost:54320/postgres"
    )
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                SELECT pg_terminate_backend(pg_stat_activity.pid)
                FROM pg_stat_activity
                WHERE pg_stat_activity.datname = 'memory_db'
                AND pid <> pg_backend_pid();
            """)
            await conn.execute("DROP DATABASE IF EXISTS memory_db;")
            await conn.execute("CREATE DATABASE memory_db;")
    finally:
        await pool.close()

    pool = await asyncpg.create_pool(DB_DSN)
    try:
        async with pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

            await register_vector(conn)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id SERIAL PRIMARY KEY,
                    content TEXT NOT NULL,
                    summary TEXT,
                    importance REAL NOT NULL,
                    access_count INT NOT NULL,
                    timestamp DOUBLE PRECISION NOT NULL,
                    embedding vector(1536) NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memories_embedding ON memories USING hnsw (embedding vector_l2_ops);
            """)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(initialize_database())
