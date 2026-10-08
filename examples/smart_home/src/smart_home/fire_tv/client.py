"""One Fire TV connection, opened on first use and reopened when the TV drops off."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from androidtv.setup_async import setup as setup_android_tv
from pydantic_settings import BaseSettings, SettingsConfigDict

from fastmcp import Context, FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.exceptions import ToolError
from fastmcp.server.lifespan import lifespan


class FireTVClient(Protocol):
    available: bool
    device_properties: dict[str, str]
    installed_apps: list[str] | None

    async def update(
        self, get_running_apps: bool = True, lazy: bool = True
    ) -> tuple[str | None, str | None, list[str] | None, str | None]: ...

    async def home(self) -> None: ...

    async def launch_app(self, app: str) -> None: ...

    async def adb_shell(self, command: str) -> str | None: ...

    async def adb_connect(self, log_errors: bool = True) -> bool: ...

    async def adb_close(self) -> None: ...


class FireTVSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    fire_tv_host: str | None = None
    fire_tv_port: int = 5555
    fire_tv_adb_key: Path | None = None
    fire_tv_adb_server_ip: str | None = None
    fire_tv_adb_server_port: int = 5037


class FireTVConnection:
    def __init__(self, settings: FireTVSettings) -> None:
        self._settings = settings
        self._client: FireTVClient | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> FireTVClient:
        async with self._lock:
            host = self._settings.fire_tv_host
            if self._client is None:
                try:
                    self._client = await setup_android_tv(
                        host=host,
                        port=self._settings.fire_tv_port,
                        adbkey=str(self._settings.fire_tv_adb_key or ""),
                        adb_server_ip=self._settings.fire_tv_adb_server_ip or "",
                        adb_server_port=self._settings.fire_tv_adb_server_port,
                        device_class="firetv",
                    )
                except Exception as e:
                    raise ToolError(f"Fire TV at {host} is not reachable") from e
            if not self._client.available:
                await self._client.adb_connect(log_errors=False)
            if not self._client.available:
                raise ToolError(f"Fire TV at {host} is not reachable; is it asleep?")
            return self._client

    async def close(self) -> None:
        async with self._lock:
            if self._client is not None:
                await self._client.adb_close()


@lifespan
async def fire_tv_lifespan(
    server: FastMCP,
) -> AsyncIterator[dict[str, FireTVConnection | None]]:
    settings = FireTVSettings()
    if settings.fire_tv_host is None:
        yield {"fire_tv": None}
        return
    connection = FireTVConnection(settings)
    try:
        yield {"fire_tv": connection}
    finally:
        await connection.close()


@asynccontextmanager
async def get_fire_tv(
    ctx: Context = CurrentContext(),
) -> AsyncIterator[FireTVClient]:
    connection = ctx.lifespan_context["fire_tv"]
    if connection is None:
        raise ToolError("Fire TV is not configured; set FIRE_TV_HOST")
    yield await connection.get()
