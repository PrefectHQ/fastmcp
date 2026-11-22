import asyncio
import shutil
import sys
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from docket import Docket
from redis.asyncio import Redis
from redis.credentials import CredentialProvider
from redis.exceptions import AuthenticationError, ConnectionError

from fastmcp import Client, FastMCP
from fastmcp_tasks import TasksExtension

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]


class FileCredentials(CredentialProvider):
    def __init__(self, path: Path):
        self.path = path

    def get_credentials(self) -> tuple[str, str]:
        return "tasks", self.path.read_text()


class AsyncFileCredentials(FileCredentials):
    def get_credentials(self) -> tuple[str, str]:
        raise AssertionError("Async credentials must be awaited")

    async def get_credentials_async(self) -> tuple[str, str]:
        return "tasks", self.path.read_text()


@pytest.fixture
async def redis_backend(free_port: int, tmp_path: Path) -> AsyncIterator[Redis]:
    executable = shutil.which("redis-server")
    if executable is None:
        pytest.skip("redis-server is required for credential rotation tests")
    with (tmp_path / "redis.log").open("w") as log:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--bind",
            "127.0.0.1",
            "--port",
            str(free_port),
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(tmp_path),
            stdout=log,
            stderr=log,
        )
        async with Redis(host="127.0.0.1", port=free_port) as admin:
            try:
                for _ in range(100):
                    try:
                        await admin.ping()
                        break
                    except ConnectionError:
                        if process.returncode is not None:
                            pytest.fail((tmp_path / "redis.log").read_text())
                        await asyncio.sleep(0.02)
                else:
                    pytest.fail("Redis did not start")
                yield admin
            finally:
                if process.returncode is None:
                    process.terminate()
                await asyncio.wait_for(process.wait(), 5)


async def rotate(admin: Redis, path: Path, password: str) -> None:
    path.write_text(password)
    await admin.execute_command(
        "ACL",
        "SETUSER",
        "tasks",
        "on",
        "resetpass",
        f">{password}",
        "~*",
        "&*",
        "+@all",
    )
    await admin.execute_command("CLIENT", "KILL", "USER", "tasks")


def task_server(
    url: str, provider: CredentialProvider | None, *, clone_extension: bool = False
) -> FastMCP:
    server = FastMCP("rotating-credentials")
    extension = TasksExtension(
        url=url,
        credential_provider=provider,
        reconnection_delay=timedelta(milliseconds=10),
    )
    server.add_extension(extension.clone() if clone_extension else extension)

    @server.tool(task=True)
    async def double(value: int) -> int:
        return value * 2

    return server


@pytest.mark.parametrize("provider_type", [FileCredentials, AsyncFileCredentials])
@pytest.mark.parametrize("clone_extension", [False, True])
async def test_tasks_reconnect_with_rotated_credentials(
    redis_backend: Redis,
    free_port: int,
    tmp_path: Path,
    provider_type: type[FileCredentials],
    clone_extension: bool,
) -> None:
    password = tmp_path / "password"
    await rotate(redis_backend, password, "first")
    url = f"redis://127.0.0.1:{free_port}/0"
    server = task_server(url, provider_type(password), clone_extension=clone_extension)
    async with Client(server) as client:
        result = await client.call_tool("double", {"value": 21})
        assert result.data == 42
        assert type(result.data) is int
        await rotate(redis_backend, password, "second")
        result = await client.call_tool("double", {"value": 22})
        assert result.data == 44
        assert type(result.data) is int


async def test_static_credentials_do_not_refresh(
    redis_backend: Redis,
    free_port: int,
    tmp_path: Path,
) -> None:
    password = tmp_path / "password"
    await rotate(redis_backend, password, "first")
    server = task_server(f"redis://tasks:first@127.0.0.1:{free_port}/0", None)
    async with Client(server) as client:
        assert (await client.call_tool("double", {"value": 21})).data == 42
        await rotate(redis_backend, password, "second")
        assert server.docket is not None
        async with server.docket.redis() as redis:
            with pytest.raises(AuthenticationError):
                await redis.get("after-rotation")


async def test_invalid_provider_credentials_are_rejected(
    redis_backend: Redis,
    free_port: int,
    tmp_path: Path,
) -> None:
    password = tmp_path / "password"
    await rotate(redis_backend, password, "first")
    password.write_text("wrong")
    server = task_server(f"redis://127.0.0.1:{free_port}/0", FileCredentials(password))
    async with server._lifespan_manager():
        assert server.docket is not None
        async with server.docket.redis() as redis:
            with pytest.raises(AuthenticationError):
                await redis.get("invalid-credentials")


@pytest.mark.subprocess_heavy
async def test_worker_cli_uses_the_servers_provider(
    redis_backend: Redis,
    free_port: int,
    tmp_path: Path,
) -> None:
    password = tmp_path / "password"
    await rotate(redis_backend, password, "first")
    url = f"redis://127.0.0.1:{free_port}/0"
    result_path = tmp_path / "result"
    server_path = tmp_path / "server.py"
    server_path.write_text(f"""from pathlib import Path
from datetime import timedelta
from redis.credentials import CredentialProvider
from fastmcp import FastMCP
from fastmcp_tasks import TasksExtension

class Credentials(CredentialProvider):
    async def get_credentials_async(self):
        return "tasks", Path({str(password)!r}).read_text()

mcp = FastMCP("worker-credentials")
mcp.add_extension(TasksExtension(
    url={url!r}, name="worker-credentials", credential_provider=Credentials(),
    reconnection_delay=timedelta(milliseconds=10),
))

@mcp.tool(task=True)
async def record(value: int) -> int:
    Path({str(result_path)!r}).write_text(str(value))
    return value
""")
    with (tmp_path / "worker.log").open("w") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "fastmcp_tasks.worker_cli",
            "worker",
            str(server_path),
            stdout=log,
            stderr=log,
        )
        try:
            async with Docket(
                name="worker-credentials",
                url=url,
                credential_provider=FileCredentials(password),
            ) as docket:

                async def record(value: int) -> int:
                    return value

                docket.register(record, names=["tool:record@"])
                for value in (41, 42):
                    if value == 42:
                        await rotate(redis_backend, password, "second")
                    await docket.add("tool:record@")(value=value)
                    for _ in range(400):
                        if result_path.exists() and result_path.read_text() == str(
                            value
                        ):
                            break
                        if process.returncode is not None:
                            pytest.fail((tmp_path / "worker.log").read_text())
                        await asyncio.sleep(0.02)
                    else:
                        pytest.fail((tmp_path / "worker.log").read_text())
        finally:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 5)
