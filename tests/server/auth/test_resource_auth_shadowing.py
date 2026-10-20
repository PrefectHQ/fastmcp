from collections.abc import Sequence

import pytest
from mcp import McpError
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from fastmcp import Client, FastMCP
from fastmcp.resources import Resource
from fastmcp.server.auth import AccessToken, require_scopes
from fastmcp.server.providers.proxy import ProxyProvider
from fastmcp.server.transforms import GetResourceNext, Transform
from fastmcp.utilities.versions import VersionSpec


class ProtectConcreteResources(Transform):
    async def list_resources(self, resources: Sequence[Resource]) -> Sequence[Resource]:
        return [
            resource.model_copy(update={"auth": require_scopes("admin")})
            for resource in resources
        ]

    async def get_resource(
        self,
        uri: str,
        call_next: GetResourceNext,
        *,
        version: VersionSpec | None = None,
    ) -> Resource | None:
        resource = await call_next(uri, version=version)
        if resource is not None:
            return resource.model_copy(update={"auth": require_scopes("admin")})
        return None


@pytest.mark.parametrize(
    "placement", ["direct", "local", "server", "provider", "mounted"]
)
@pytest.mark.parametrize("allowed", [False, True])
async def test_concrete_resource_auth_shadows_template(
    placement: str, allowed: bool
) -> None:
    backend = FastMCP()

    @backend.resource(
        "secret://data", auth=require_scopes("admin") if placement == "direct" else None
    )
    def secret() -> str:
        return "SECRET"

    @backend.resource("secret://{name}")
    def public(name: str) -> str:
        return f"PUBLIC {name}"

    server = backend
    uri = "secret://data"
    other_uri = "secret://other"
    if placement == "local":
        backend.add_transform(ProtectConcreteResources())
    elif placement not in {"direct", "local"}:
        gateway = FastMCP()
        provider = ProxyProvider(lambda: Client(backend))
        if placement == "provider":
            provider.add_transform(ProtectConcreteResources())
        else:
            gateway.add_transform(ProtectConcreteResources())
        gateway.add_provider(provider)
        server = gateway
        if placement == "mounted":
            server = FastMCP()
            server.mount(gateway, namespace="child")
            uri = "secret://child/data"
            other_uri = "secret://child/other"

    token = AccessToken(
        token="test", client_id="test", scopes=["admin"] if allowed else []
    )
    context_token = auth_context_var.set(AuthenticatedUser(token))
    try:
        assert (await server.get_resource(uri) is not None) is allowed
        async with Client(server) as client:
            assert len(await client.list_resources()) == int(allowed)
            if allowed:
                result = await client.read_resource(uri)
                assert result[0].text == "SECRET"
            else:
                with pytest.raises(
                    McpError, match="Unknown resource|Resource not found"
                ):
                    await client.read_resource(uri)
            result = await client.read_resource(other_uri)
            assert result[0].text == "PUBLIC other"
    finally:
        auth_context_var.reset(context_token)
