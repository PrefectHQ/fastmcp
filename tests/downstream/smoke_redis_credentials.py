"""Run fastmcp-tasks on the rotating Redis credentials real deployments use.

Two providers, each driven through `TasksExtension(credential_provider=...)`
with concurrent task calls while its credentials rotate underneath:

- AWS ElastiCache IAM auth, with the `CredentialProvider` from AWS's
  documentation: botocore presigns a short-lived SigV4 token on demand.
- Azure Managed Redis with Entra ID, with redis-entraid's streaming
  `EntraIdCredentialsProvider` refreshing its token in the background.

A local `redis-server` stands in for both services: each token a provider
mints becomes a password of the `tasks` ACL user, as the service would accept
it. No cloud account is involved. See the README for running it locally.
"""

import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
import time
from datetime import timedelta
from importlib.metadata import version
from urllib.parse import ParseResult, urlencode, urlunparse

import botocore.session
import redis
from _harness import Report
from botocore.model import ServiceId
from botocore.signers import RequestSigner
from cachetools import TTLCache, cached
from redis.asyncio import Redis
from redis.auth.idp import IdentityProviderInterface
from redis.auth.token import SimpleToken, TokenInterface
from redis.auth.token_manager import RetryPolicy, TokenManagerConfig
from redis_entraid.cred_provider import EntraIdCredentialsProvider

import fastmcp
from fastmcp import Client, FastMCP
from fastmcp_tasks import TasksExtension

USER = "tasks"
CALLERS = 8
ROUND_SECONDS = 2


class ElastiCacheIAMProvider(redis.CredentialProvider):
    """AWS's documented provider for ElastiCache IAM auth, with a 1s cache in
    place of its 900s one so tokens rotate during the run, and the botocore
    session kept alive."""

    def __init__(self, user: str, cluster_name: str, region: str = "us-east-1"):
        self.user = user
        self.cluster_name = cluster_name
        self.region = region
        # The signer holds only a weak reference to the session's event
        # emitter, so the session has to outlive __init__.
        self.session = botocore.session.get_session()
        self.request_signer = RequestSigner(
            ServiceId("elasticache"),
            self.region,
            "elasticache",
            "v4",
            self.session.get_credentials(),
            self.session.get_component("event_emitter"),
        )

    @cached(cache=TTLCache(maxsize=128, ttl=1))
    def get_credentials(self) -> tuple[str, str]:
        query_params = {"Action": "connect", "User": self.user}
        url = urlunparse(
            ParseResult(
                scheme="https",
                netloc=self.cluster_name,
                path="/",
                query=urlencode(query_params),
                params="",
                fragment="",
            )
        )
        signed_url = self.request_signer.generate_presigned_url(
            {"method": "GET", "url": url, "body": {}, "headers": {}, "context": {}},
            operation_name="connect",
            expires_in=900,
            region_name=self.region,
        )
        return (self.user, signed_url.removeprefix("https://"))


class AcceptedByLocalRedis(ElastiCacheIAMProvider):
    """Registers each presigned token, as ElastiCache's SigV4 check would accept it."""

    def __init__(self, admin: redis.Redis):
        super().__init__(USER, "smoke-cluster")
        self.admin = admin
        self.minted: set[str] = set()

    def get_credentials(self) -> tuple[str, str]:
        user, token = super().get_credentials()
        if token not in self.minted:
            accept(self.admin, token)
            self.minted.add(token)
        return user, token


class LocalEntraId(IdentityProviderInterface):
    """Stands in for Entra ID: mints a 1s token and registers it with Redis."""

    def __init__(self, admin: redis.Redis):
        self.admin = admin
        self.issued = 0

    def request_token(self, force_refresh: bool = False) -> TokenInterface:
        self.issued += 1
        value = f"entra-token-{self.issued}-{time.monotonic_ns()}"
        accept(self.admin, value)
        now = time.time() * 1000
        return SimpleToken(value, now + 1000, now, {"oid": USER})


def accept(admin: redis.Redis, password: str) -> None:
    admin.execute_command(
        "ACL", "SETUSER", USER, "on", f">{password}", "~*", "&*", "+@all"
    )


def entra_id_provider(identity: LocalEntraId) -> EntraIdCredentialsProvider:
    return EntraIdCredentialsProvider(
        identity,
        TokenManagerConfig(
            expiration_refresh_ratio=0.5,
            lower_refresh_bound_millis=0,
            token_request_execution_timeout_in_ms=1000,
            retry_policy=RetryPolicy(max_attempts=3, delay_in_ms=10),
        ),
    )


async def run_tasks_through_rotation(
    provider: redis.CredentialProvider, port: int, admin: Redis, rounds: int = 3
) -> None:
    """Rounds of concurrent task calls.  Between rounds the server drops the
    connections after the provider's credentials have moved on, so the next
    round has to reconnect with current ones; a streaming provider also
    refreshes in the background while calls are in flight."""
    server = FastMCP("rotating-credentials")
    server.add_extension(
        TasksExtension(
            url=f"redis://127.0.0.1:{port}/0",
            name=f"smoke-{time.monotonic_ns()}",
            credential_provider=provider,
            reconnection_delay=timedelta(milliseconds=10),
        )
    )

    @server.tool(task=True)
    async def double(value: int) -> int:
        return value * 2

    async with Client(server) as client:

        async def caller(n: int) -> None:
            deadline = time.monotonic() + ROUND_SECONDS
            i = 0
            while time.monotonic() < deadline:
                result = await client.call_tool("double", {"value": n * 1000 + i})
                assert result.data == (n * 1000 + i) * 2, result
                i += 1

        for _ in range(rounds):
            await asyncio.wait_for(
                asyncio.gather(*(caller(n) for n in range(CALLERS))), 30
            )
            await asyncio.sleep(1.1)
            await admin.execute_command("CLIENT", "KILL", "USER", USER)


def start_redis(directory: str) -> tuple[subprocess.Popen[bytes], int]:
    executable = shutil.which("redis-server")
    assert executable, "redis-server must be on PATH"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [executable, "--bind", "127.0.0.1", "--port", str(port)]
        + ["--save", "", "--appendonly", "no", "--dir", directory],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process, port


async def main() -> None:
    # Fake keys for botocore's signer; nothing is sent to AWS.
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "AKIDSMOKETEST")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "smoke-test-secret")
    report = Report(
        "redis credential providers",
        {
            "fastmcp": fastmcp.__version__,
            "fastmcp-tasks": version("fastmcp-tasks"),
            "pydocket": version("pydocket"),
            "redis": version("redis"),
            "botocore": version("botocore"),
            "redis-entraid": version("redis-entraid"),
        },
    )

    async def installed_build_is_under_test() -> None:
        expected = os.environ.get("FASTMCP_EXPECTED_VERSION")
        assert expected in (None, fastmcp.__version__), (
            f"expected {expected}, got {fastmcp.__version__}"
        )

    with tempfile.TemporaryDirectory(prefix="fastmcp-smoke-redis-") as directory:
        process, port = start_redis(directory)
        try:
            sync_admin = redis.Redis(host="127.0.0.1", port=port)
            for _ in range(200):
                try:
                    sync_admin.ping()
                    break
                except redis.ConnectionError:
                    time.sleep(0.02)
            async with Redis(host="127.0.0.1", port=port) as admin:
                with report.transport("ElastiCache IAM (botocore SigV4)"):
                    await report.check(
                        "installed build is under test", installed_build_is_under_test
                    )
                    await report.check(
                        "tasks run while tokens rotate",
                        lambda: run_tasks_through_rotation(
                            AcceptedByLocalRedis(sync_admin), port, admin
                        ),
                    )

                with report.transport("Entra ID (redis-entraid, streaming)"):

                    async def tasks_run_while_tokens_refresh() -> None:
                        identity = LocalEntraId(sync_admin)
                        await run_tasks_through_rotation(
                            entra_id_provider(identity), port, admin
                        )
                        assert identity.issued > 3, (
                            f"only {identity.issued} tokens issued; the provider "
                            "never refreshed during the run"
                        )

                    await report.check(
                        "tasks run while tokens refresh",
                        tasks_run_while_tokens_refresh,
                        known_gap=(
                            "TasksExtension rejects streaming providers until a "
                            "pydocket release includes chrisguidry/docket#579"
                        ),
                    )
            sync_admin.close()
        finally:
            process.terminate()
            process.wait(timeout=5)

    report.finish()


if __name__ == "__main__":
    asyncio.run(main())
