"""One pooled Fire TV connection for the server's lifetime."""

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

    async def adb_close(self) -> None: ...


class FireTVSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    fire_tv_host: str | None = None
    fire_tv_port: int = 5555
    fire_tv_adb_key: Path | None = None
    fire_tv_adb_server_ip: str | None = None
    fire_tv_adb_server_port: int = 5037


@lifespan
async def fire_tv_lifespan(
    server: FastMCP,
) -> AsyncIterator[dict[str, FireTVClient | None]]:
    settings = FireTVSettings()
    if settings.fire_tv_host is None:
        yield {"fire_tv": None}
        return

    fire_tv = await setup_android_tv(
        host=settings.fire_tv_host,
        port=settings.fire_tv_port,
        adbkey=str(settings.fire_tv_adb_key or ""),
        adb_server_ip=settings.fire_tv_adb_server_ip or "",
        adb_server_port=settings.fire_tv_adb_server_port,
        device_class="firetv",
    )
    try:
        if not fire_tv.available:
            raise RuntimeError(f"Fire TV at {settings.fire_tv_host} is unavailable")
        yield {"fire_tv": fire_tv}
    finally:
        await fire_tv.adb_close()


@asynccontextmanager
async def get_fire_tv(
    ctx: Context = CurrentContext(),
) -> AsyncIterator[FireTVClient]:
    fire_tv = ctx.lifespan_context["fire_tv"]
    if fire_tv is None:
        raise ToolError("Fire TV is not configured; set FIRE_TV_HOST")
    yield fire_tv
