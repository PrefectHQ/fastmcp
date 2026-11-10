"""Docket worker settings for FastMCP background tasks.

Moved out of ``fastmcp.settings`` during the SEP-1686 -> SEP-2663 migration.
The ``FASTMCP_DOCKET_*`` environment prefix is unchanged so existing
deployments keep working. ``TasksExtension`` reads this configuration (its
constructor overrides the env defaults).
"""

from __future__ import annotations

import inspect
import os
from datetime import timedelta
from typing import Annotated

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load the same dotenv source as core FastMCP settings, so a deployment that
# puts FASTMCP_DOCKET_* in `.env` (or a FASTMCP_ENV_FILE) configures the backend
# rather than silently falling back to memory://.
_ENV_FILE = os.getenv("FASTMCP_ENV_FILE", ".env")


class DocketSettings(BaseSettings):
    """Docket worker configuration."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_DOCKET_",
        env_file=_ENV_FILE,
        extra="ignore",
    )

    name: Annotated[
        str,
        Field(
            description=inspect.cleandoc(
                """
                Name for the Docket queue. All servers/workers sharing the same name
                and backend URL will share a task queue.
                """
            ),
        ),
    ] = "fastmcp"

    url: Annotated[
        str,
        Field(
            description=inspect.cleandoc(
                """
                URL for the Docket backend. Supports:
                - memory:// - In-memory backend (single process only)
                - redis://host:port/db - Redis/Valkey backend (distributed, multi-process)

                Example: redis://localhost:6379/0

                Default is memory:// for single-process scenarios. Use Redis or Valkey
                when coordinating tasks across multiple processes (e.g., additional
                workers via the fastmcp tasks CLI).
                """
            ),
        ),
    ] = "memory://"

    worker_name: Annotated[
        str | None,
        Field(
            description=inspect.cleandoc(
                """
                Name for the Docket worker. If None, Docket will auto-generate
                a unique worker name.
                """
            ),
        ),
    ] = None

    concurrency: Annotated[
        int,
        Field(
            description=inspect.cleandoc(
                """
                Maximum number of tasks the worker can process concurrently.
                """
            ),
        ),
    ] = 10

    redelivery_timeout: Annotated[
        timedelta,
        Field(
            description=inspect.cleandoc(
                """
                Task redelivery timeout. If a worker doesn't complete
                a task within this time, the task will be redelivered to another
                worker.
                """
            ),
        ),
    ] = timedelta(seconds=300)

    reconnection_delay: Annotated[
        timedelta,
        Field(
            description=inspect.cleandoc(
                """
                Delay between reconnection attempts when the worker
                loses connection to the Docket backend.
                """
            ),
        ),
    ] = timedelta(seconds=5)

    minimum_check_interval: Annotated[
        timedelta,
        Field(
            description=inspect.cleandoc(
                """
                How frequently the worker polls for new tasks. Lower
                values reduce latency for task pickup at the cost of
                more CPU usage. The default of 50ms is a good balance;
                increase for high-volume production deployments where
                tasks are long-running.
                """
            ),
        ),
    ] = timedelta(milliseconds=50)


docket_settings = DocketSettings()


class TasksSettings(BaseSettings):
    """Settings for the task engine itself, as opposed to its Docket backend."""

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_TASKS_",
        env_file=_ENV_FILE,
        extra="ignore",
    )

    encryption_key: Annotated[
        SecretStr | None,
        Field(
            description=inspect.cleandoc(
                """
                Key used to encrypt task context snapshots at rest. The snapshot
                carries the submitting caller's access token and HTTP headers,
                and it is written to the Docket backend for the task's TTL.
                Every server and worker sharing a task queue must set the same
                key; a worker that cannot decrypt a snapshot fails the task
                rather than running it as an anonymous caller. When unset, the
                snapshot is stored as plaintext JSON. The Fernet key is derived
                from this value with PBKDF2, so any non-empty string works, but
                use at least 32 random characters.
                """
            ),
        ),
    ] = None


tasks_settings = TasksSettings()


class TasksClientSettings(BaseSettings):
    """Client-side settings for driving background tasks.

    Moved here from core ``fastmcp.settings`` during the SEP-1686 -> SEP-2663
    migration: the entire client task-driving path now lives in
    ``fastmcp-tasks``, so its one tunable does too.
    """

    model_config = SettingsConfigDict(
        env_prefix="FASTMCP_TASKS_CLIENT_",
        env_file=_ENV_FILE,
        extra="ignore",
    )

    poll_interval: Annotated[
        float,
        Field(
            description=inspect.cleandoc(
                """
                Ceiling, in seconds, for the fallback poll backoff while the client
                waits on a background task. Applies only when the server does not
                advertise its own pollIntervalMs: in that case the client starts
                polling fast (~20ms) and doubles up to this ceiling, so quick tasks
                resolve promptly while long-running tasks don't hammer the server.
                When the server advertises a pollIntervalMs, that interval is honored
                exactly and this setting is ignored. Must be positive.
                """
            ),
            gt=0,
        ),
    ] = 0.5


client_settings = TasksClientSettings()
