import pytest
from starlette import status
from starlette.testclient import TestClient

from fastmcp import FastMCP
from fastmcp.contrib.component_manager import set_up_component_manager
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair


class TestComponentManagementRoutes:
    """Test the component management routes for tools, resources, and prompts."""

    @pytest.fixture
    def mounted_mcp(self):
        """Create a FastMCP server with a mounted sub-server and a tool, resource, and prompt on the sub-server."""
        mounted_mcp = FastMCP("SubServer")

        @mounted_mcp.tool()
        def mounted_tool() -> str:
            """Test tool for tool management routes."""
            return "mounted_tool_result"

        @mounted_mcp.resource("data://mounted_resource")
        def mounted_resource() -> str:
            """Test resource for tool management routes."""
            return "mounted_resource_result"

        # Add a test resource
        @mounted_mcp.resource("data://mounted_resource/{id}")
        def test_template(id: str) -> dict:
            """Test template for tool management routes."""
            return {"id": id, "value": "data"}

        @mounted_mcp.prompt()
        def mounted_prompt() -> str:
            """Test prompt for tool management routes."""
            return "mounted_prompt_result"

        return mounted_mcp

    @pytest.fixture
    def mcp(self, mounted_mcp):
        """Create a FastMCP server with test tools, resources, and prompts."""
        mcp = FastMCP("TestServer")
        mcp.mount(mounted_mcp, namespace="sub")
        set_up_component_manager(server=mcp)

        # Add a test tool
        @mcp.tool
        def test_tool() -> str:
            """Test tool for tool management routes."""
            return "test_tool_result"

        # Add a test resource
        @mcp.resource("data://test_resource")
        def test_resource() -> str:
            """Test resource for tool management routes."""
            return "test_resource_result"

        # Add a test resource
        @mcp.resource("data://test_resource/{id}")
        def test_template(id: str) -> dict:
            """Test template for tool management routes."""
            return {"id": id, "value": "data"}

        # Add a test prompt
        @mcp.prompt
        def test_prompt() -> str:
            """Test prompt for tool management routes."""
            return "test_prompt_result"

        return mcp

    @pytest.fixture
    def client(self, mcp):
        """Create a test client for the FastMCP server."""
        return TestClient(mcp.http_app())

    async def test_enable_tool_route(self, client, mcp):
        """Test enabling a tool via the HTTP route."""
        # First disable the tool
        mcp.disable(keys=["tool:test_tool"])
        tools = await mcp.get_tools()
        assert not any(t.name == "test_tool" for t in tools)

        # Enable the tool via the HTTP route
        response = client.post("/tools/test_tool/enable")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Enabled tool: test_tool"}

        # Verify the tool is enabled
        tools = await mcp.get_tools()
        assert any(t.name == "test_tool" for t in tools)

    async def test_disable_tool_route(self, client, mcp):
        """Test disabling a tool via the HTTP route."""
        # First ensure the tool is enabled
        tools = await mcp.get_tools()
        assert any(t.name == "test_tool" for t in tools)

        # Disable the tool via the HTTP route
        response = client.post("/tools/test_tool/disable")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Disabled tool: test_tool"}

        # Verify the tool is disabled
        tools = await mcp.get_tools()
        assert not any(t.name == "test_tool" for t in tools)

    async def test_enable_resource_route(self, client, mcp):
        """Test enabling a resource via the HTTP route."""
        # First disable the resource
        mcp.disable(keys=["resource:data://test_resource"])
        resources = await mcp.get_resources()
        assert not any(str(r.uri) == "data://test_resource" for r in resources)

        # Enable the resource via the HTTP route
        response = client.post("/resources/data://test_resource/enable")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Enabled resource: data://test_resource"}

        # Verify the resource is enabled
        resources = await mcp.get_resources()
        assert any(str(r.uri) == "data://test_resource" for r in resources)

    async def test_disable_resource_route(self, client, mcp):
        """Test disabling a resource via the HTTP route."""
        # First ensure the resource is enabled
        resources = await mcp.get_resources()
        assert any(str(r.uri) == "data://test_resource" for r in resources)

        # Disable the resource via the HTTP route
        response = client.post("/resources/data://test_resource/disable")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Disabled resource: data://test_resource"}

        # Verify the resource is disabled
        resources = await mcp.get_resources()
        assert not any(str(r.uri) == "data://test_resource" for r in resources)

    async def test_enable_template_route(self, client, mcp):
        """Test enabling a resource on a mounted server via the parent server's HTTP route."""
        key = "data://test_resource/{id}"
        mcp.disable(keys=["template:data://test_resource/{id}"])
        templates = await mcp.get_resource_templates()
        assert not any(t.uri_template == key for t in templates)
        response = client.post("/resources/data://test_resource/{id}/enable")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "message": "Enabled resource: data://test_resource/{id}"
        }
        templates = await mcp.get_resource_templates()
        assert any(t.uri_template == key for t in templates)

    async def test_disable_template_route(self, client, mcp):
        """Test disabling a resource on a mounted server via the parent server's HTTP route."""
        key = "data://test_resource/{id}"
        templates = await mcp.get_resource_templates()
        assert any(t.uri_template == key for t in templates)
        response = client.post("/resources/data://test_resource/{id}/disable")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "message": "Disabled resource: data://test_resource/{id}"
        }
        templates = await mcp.get_resource_templates()
        assert not any(t.uri_template == key for t in templates)

    async def test_enable_prompt_route(self, client, mcp):
        """Test enabling a prompt via the HTTP route."""
        # First disable the prompt
        mcp.disable(keys=["prompt:test_prompt"])
        prompts = await mcp.get_prompts()
        assert not any(p.name == "test_prompt" for p in prompts)

        # Enable the prompt via the HTTP route
        response = client.post("/prompts/test_prompt/enable")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Enabled prompt: test_prompt"}

        # Verify the prompt is enabled
        prompts = await mcp.get_prompts()
        assert any(p.name == "test_prompt" for p in prompts)

    async def test_disable_prompt_route(self, client, mcp):
        """Test disabling a prompt via the HTTP route."""
        # First ensure the prompt is enabled
        prompts = await mcp.get_prompts()
        assert any(p.name == "test_prompt" for p in prompts)

        # Disable the prompt via the HTTP route
        response = client.post("/prompts/test_prompt/disable")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Disabled prompt: test_prompt"}

        # Verify the prompt is disabled
        prompts = await mcp.get_prompts()
        assert not any(p.name == "test_prompt" for p in prompts)

    async def test_enable_tool_route_on_mounted_server(self, client, mounted_mcp):
        """Test enabling a tool on a mounted server via the parent server's HTTP route."""
        # Disable the tool on the sub-server
        mounted_mcp.disable(keys=["tool:mounted_tool"])
        tools = await mounted_mcp.get_tools()
        assert not any(t.name == "mounted_tool" for t in tools)
        # Enable via parent
        response = client.post("/tools/sub_mounted_tool/enable")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Enabled tool: sub_mounted_tool"}
        # Confirm enabled on sub-server
        tools = await mounted_mcp.get_tools()
        assert any(t.name == "mounted_tool" for t in tools)

    async def test_disable_tool_route_on_mounted_server(self, client, mounted_mcp):
        """Test disabling a tool on a mounted server via the parent server's HTTP route."""
        # Ensure the tool is enabled on the sub-server
        tools = await mounted_mcp.get_tools()
        assert any(t.name == "mounted_tool" for t in tools)
        # Disable via parent
        response = client.post("/tools/sub_mounted_tool/disable")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Disabled tool: sub_mounted_tool"}
        # Confirm disabled on sub-server
        tools = await mounted_mcp.get_tools()
        assert not any(t.name == "mounted_tool" for t in tools)

    async def test_enable_resource_route_on_mounted_server(self, client, mounted_mcp):
        """Test enabling a resource on a mounted server via the parent server's HTTP route."""
        mounted_mcp.disable(keys=["resource:data://mounted_resource"])
        resources = await mounted_mcp.get_resources()
        assert not any(str(r.uri) == "data://mounted_resource" for r in resources)
        response = client.post("/resources/data://sub/mounted_resource/enable")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "message": "Enabled resource: data://sub/mounted_resource"
        }
        resources = await mounted_mcp.get_resources()
        assert any(str(r.uri) == "data://mounted_resource" for r in resources)

    async def test_disable_resource_route_on_mounted_server(self, client, mounted_mcp):
        """Test disabling a resource on a mounted server via the parent server's HTTP route."""
        resources = await mounted_mcp.get_resources()
        assert any(str(r.uri) == "data://mounted_resource" for r in resources)
        response = client.post("/resources/data://sub/mounted_resource/disable")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "message": "Disabled resource: data://sub/mounted_resource"
        }
        resources = await mounted_mcp.get_resources()
        assert not any(str(r.uri) == "data://mounted_resource" for r in resources)

    async def test_enable_template_route_on_mounted_server(self, client, mounted_mcp):
        """Test enabling a resource on a mounted server via the parent server's HTTP route."""
        key = "data://mounted_resource/{id}"
        mounted_mcp.disable(keys=["template:data://mounted_resource/{id}"])
        templates = await mounted_mcp.get_resource_templates()
        assert not any(t.uri_template == key for t in templates)
        response = client.post("/resources/data://sub/mounted_resource/{id}/enable")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "message": "Enabled resource: data://sub/mounted_resource/{id}"
        }
        templates = await mounted_mcp.get_resource_templates()
        assert any(t.uri_template == key for t in templates)

    async def test_disable_template_route_on_mounted_server(self, client, mounted_mcp):
        """Test disabling a resource on a mounted server via the parent server's HTTP route."""
        key = "data://mounted_resource/{id}"
        templates = await mounted_mcp.get_resource_templates()
        assert any(t.uri_template == key for t in templates)
        response = client.post("/resources/data://sub/mounted_resource/{id}/disable")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "message": "Disabled resource: data://sub/mounted_resource/{id}"
        }
        templates = await mounted_mcp.get_resource_templates()
        assert not any(t.uri_template == key for t in templates)

    async def test_enable_prompt_route_on_mounted_server(self, client, mounted_mcp):
        """Test enabling a prompt on a mounted server via the parent server's HTTP route."""
        mounted_mcp.disable(keys=["prompt:mounted_prompt"])
        prompts = await mounted_mcp.get_prompts()
        assert not any(p.name == "mounted_prompt" for p in prompts)
        response = client.post("/prompts/sub_mounted_prompt/enable")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Enabled prompt: sub_mounted_prompt"}
        prompts = await mounted_mcp.get_prompts()
        assert any(p.name == "mounted_prompt" for p in prompts)

    async def test_disable_prompt_route_on_mounted_server(self, client, mounted_mcp):
        """Test disabling a prompt on a mounted server via the parent server's HTTP route."""
        prompts = await mounted_mcp.get_prompts()
        assert any(p.name == "mounted_prompt" for p in prompts)
        response = client.post("/prompts/sub_mounted_prompt/disable")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Disabled prompt: sub_mounted_prompt"}
        prompts = await mounted_mcp.get_prompts()
        assert not any(p.name == "mounted_prompt" for p in prompts)

    def test_enable_nonexistent_tool(self, client):
        """Test enabling a non-existent tool returns 404."""
        response = client.post("/tools/nonexistent_tool/enable")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.text == "Unknown tool: 'nonexistent_tool'"

    def test_disable_nonexistent_tool(self, client):
        """Test disabling a non-existent tool returns 404."""
        response = client.post("/tools/nonexistent_tool/disable")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.text == "Unknown tool: 'nonexistent_tool'"

    def test_enable_nonexistent_resource(self, client):
        """Test enabling a non-existent resource returns 404."""
        response = client.post("/resources/nonexistent://resource/enable")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.text == "Unknown resource: 'nonexistent://resource'"

    def test_disable_nonexistent_resource(self, client):
        """Test disabling a non-existent resource returns 404."""
        response = client.post("/resources/nonexistent://resource/disable")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.text == "Unknown resource: 'nonexistent://resource'"

    def test_enable_nonexistent_prompt(self, client):
        """Test enabling a non-existent prompt returns 404."""
        response = client.post("/prompts/nonexistent_prompt/enable")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.text == "Unknown prompt: 'nonexistent_prompt'"

    def test_disable_nonexistent_prompt(self, client):
        """Test disabling a non-existent prompt returns 404."""
        response = client.post("/prompts/nonexistent_prompt/disable")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.text == "Unknown prompt: 'nonexistent_prompt'"


class TestAuthComponentManagementRoutes:
    """Test the component management routes with authentication for tools, resources, and prompts."""

    def setup_method(self):
        """Set up test fixtures."""
        # Generate a key pair and create an auth provider
        key_pair = RSAKeyPair.generate()
        self.auth = JWTVerifier(
            public_key=key_pair.public_key,
            issuer="https://dev.example.com",
            audience="my-dev-server",
        )
        self.mcp = FastMCP("TestServerWithAuth", auth=self.auth)
        set_up_component_manager(
            server=self.mcp, required_scopes=["tool:write", "tool:read"]
        )
        self.token = key_pair.create_token(
            subject="dev-user",
            issuer="https://dev.example.com",
            audience="my-dev-server",
            scopes=["tool:write", "tool:read"],
        )
        self.token_without_scopes = key_pair.create_token(
            subject="dev-user",
            issuer="https://dev.example.com",
            audience="my-dev-server",
            scopes=["tool:read"],
        )

        # Add test components
        @self.mcp.tool
        def test_tool() -> str:
            """Test tool for auth testing."""
            return "test_tool_result"

        @self.mcp.resource("data://test_resource")
        def test_resource() -> str:
            """Test resource for auth testing."""
            return "test_resource_result"

        @self.mcp.prompt
        def test_prompt() -> str:
            """Test prompt for auth testing."""
            return "test_prompt_result"

        # Create test client
        self.client = TestClient(self.mcp.http_app())

    async def test_unauthorized_enable_tool(self):
        """Test that unauthenticated requests to enable a tool are rejected."""
        self.mcp.disable(keys=["tool:test_tool"])
        tools = await self.mcp.get_tools()
        assert not any(t.name == "test_tool" for t in tools)

        response = self.client.post("/tools/test_tool/enable")
        assert response.status_code == 401
        tools = await self.mcp.get_tools()
        assert not any(t.name == "test_tool" for t in tools)

    async def test_authorized_enable_tool(self):
        """Test that authenticated requests to enable a tool are allowed."""
        self.mcp.disable(keys=["tool:test_tool"])
        tools = await self.mcp.get_tools()
        assert not any(t.name == "test_tool" for t in tools)

        response = self.client.post(
            "/tools/test_tool/enable", headers={"Authorization": "Bearer " + self.token}
        )
        assert response.status_code == 200
        assert response.json() == {"message": "Enabled tool: test_tool"}
        tools = await self.mcp.get_tools()
        assert any(t.name == "test_tool" for t in tools)

    async def test_unauthorized_disable_tool(self):
        """Test that unauthenticated requests to disable a tool are rejected."""
        tools = await self.mcp.get_tools()
        assert any(t.name == "test_tool" for t in tools)

        response = self.client.post("/tools/test_tool/disable")
        assert response.status_code == 401
        tools = await self.mcp.get_tools()
        assert any(t.name == "test_tool" for t in tools)

    async def test_authorized_disable_tool(self):
        """Test that authenticated requests to disable a tool are allowed."""
        tools = await self.mcp.get_tools()
        assert any(t.name == "test_tool" for t in tools)

        response = self.client.post(
            "/tools/test_tool/disable",
            headers={"Authorization": "Bearer " + self.token},
        )
        assert response.status_code == 200
        assert response.json() == {"message": "Disabled tool: test_tool"}
        tools = await self.mcp.get_tools()
        assert not any(t.name == "test_tool" for t in tools)

    async def test_forbidden_enable_tool(self):
        """Test that requests with insufficient scopes are rejected."""
        self.mcp.disable(keys=["tool:test_tool"])
        tools = await self.mcp.get_tools()
        assert not any(t.name == "test_tool" for t in tools)

        response = self.client.post(
            "/tools/test_tool/enable",
            headers={"Authorization": "Bearer " + self.token_without_scopes},
        )
        assert response.status_code == 403
        tools = await self.mcp.get_tools()
        assert not any(t.name == "test_tool" for t in tools)

    async def test_authorized_enable_resource(self):
        """Test that authenticated requests to enable a resource are allowed."""
        self.mcp.disable(keys=["resource:data://test_resource"])
        resources = await self.mcp.get_resources()
        assert not any(str(r.uri) == "data://test_resource" for r in resources)

        response = self.client.post(
            "/resources/data://test_resource/enable",
            headers={"Authorization": "Bearer " + self.token},
        )
        assert response.status_code == 200
        assert response.json() == {"message": "Enabled resource: data://test_resource"}
        resources = await self.mcp.get_resources()
        assert any(str(r.uri) == "data://test_resource" for r in resources)

    async def test_unauthorized_disable_resource(self):
        """Test that unauthenticated requests to disable a resource are rejected."""
        resources = await self.mcp.get_resources()
        assert any(str(r.uri) == "data://test_resource" for r in resources)

        response = self.client.post("/resources/data://test_resource/disable")
        assert response.status_code == 401
        resources = await self.mcp.get_resources()
        assert any(str(r.uri) == "data://test_resource" for r in resources)

    async def test_forbidden_enable_resource(self):
        """Test that requests with insufficient scopes are rejected."""
        self.mcp.disable(keys=["resource:data://test_resource"])
        resources = await self.mcp.get_resources()
        assert not any(str(r.uri) == "data://test_resource" for r in resources)

        response = self.client.post(
            "/resources/data://test_resource/disable",
            headers={"Authorization": "Bearer " + self.token_without_scopes},
        )
        assert response.status_code == 403
        resources = await self.mcp.get_resources()
        assert not any(str(r.uri) == "data://test_resource" for r in resources)

    async def test_authorized_disable_resource(self):
        """Test that authenticated requests to disable a resource are allowed."""
        resources = await self.mcp.get_resources()
        assert any(str(r.uri) == "data://test_resource" for r in resources)

        response = self.client.post(
            "/resources/data://test_resource/disable",
            headers={"Authorization": "Bearer " + self.token},
        )
        assert response.status_code == 200
        assert response.json() == {"message": "Disabled resource: data://test_resource"}
        resources = await self.mcp.get_resources()
        assert not any(str(r.uri) == "data://test_resource" for r in resources)

    async def test_unauthorized_enable_prompt(self):
        """Test that unauthenticated requests to enable a prompt are rejected."""
        self.mcp.disable(keys=["prompt:test_prompt"])
        prompts = await self.mcp.get_prompts()
        assert not any(p.name == "test_prompt" for p in prompts)

        response = self.client.post("/prompts/test_prompt/enable")
        assert response.status_code == 401
        prompts = await self.mcp.get_prompts()
        assert not any(p.name == "test_prompt" for p in prompts)

    async def test_authorized_enable_prompt(self):
        """Test that authenticated requests to enable a prompt are allowed."""
        self.mcp.disable(keys=["prompt:test_prompt"])
        prompts = await self.mcp.get_prompts()
        assert not any(p.name == "test_prompt" for p in prompts)

        response = self.client.post(
            "/prompts/test_prompt/enable",
            headers={"Authorization": "Bearer " + self.token},
        )
        assert response.status_code == 200
        assert response.json() == {"message": "Enabled prompt: test_prompt"}
        prompts = await self.mcp.get_prompts()
        assert any(p.name == "test_prompt" for p in prompts)

    async def test_unauthorized_disable_prompt(self):
        """Test that unauthenticated requests to disable a prompt are rejected."""
        prompts = await self.mcp.get_prompts()
        assert any(p.name == "test_prompt" for p in prompts)

        response = self.client.post("/prompts/test_prompt/disable")
        assert response.status_code == 401
        prompts = await self.mcp.get_prompts()
        assert any(p.name == "test_prompt" for p in prompts)

    async def test_forbidden_disable_prompt(self):
        """Test that requests with insufficient scopes are rejected."""
        prompts = await self.mcp.get_prompts()
        assert any(p.name == "test_prompt" for p in prompts)

        response = self.client.post(
            "/prompts/test_prompt/disable",
            headers={"Authorization": "Bearer " + self.token_without_scopes},
        )
        assert response.status_code == 403
        prompts = await self.mcp.get_prompts()
        assert any(p.name == "test_prompt" for p in prompts)

    async def test_authorized_disable_prompt(self):
        """Test that authenticated requests to disable a prompt are allowed."""
        prompts = await self.mcp.get_prompts()
        assert any(p.name == "test_prompt" for p in prompts)

        response = self.client.post(
            "/prompts/test_prompt/disable",
            headers={"Authorization": "Bearer " + self.token},
        )
        assert response.status_code == 200
        assert response.json() == {"message": "Disabled prompt: test_prompt"}
        prompts = await self.mcp.get_prompts()
        assert not any(p.name == "test_prompt" for p in prompts)


class TestComponentManagerWithPath:
    """Test component manager routes when mounted at a custom path."""

    @pytest.fixture
    def mcp_with_path(self):
        mcp = FastMCP("TestServerWithPath")
        set_up_component_manager(server=mcp, path="/test")

        @mcp.tool
        def test_tool() -> str:
            return "test_tool_result"

        @mcp.resource("data://test_resource")
        def test_resource() -> str:
            return "test_resource_result"

        @mcp.prompt
        def test_prompt() -> str:
            return "test_prompt_result"

        return mcp

    @pytest.fixture
    def client_with_path(self, mcp_with_path):
        return TestClient(mcp_with_path.http_app())

    async def test_enable_tool_route_with_path(self, client_with_path, mcp_with_path):
        mcp_with_path.disable(keys=["tool:test_tool"])
        tools = await mcp_with_path.get_tools()
        assert not any(t.name == "test_tool" for t in tools)
        response = client_with_path.post("/test/tools/test_tool/enable")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Enabled tool: test_tool"}
        tools = await mcp_with_path.get_tools()
        assert any(t.name == "test_tool" for t in tools)

    async def test_disable_resource_route_with_path(
        self, client_with_path, mcp_with_path
    ):
        resources = await mcp_with_path.get_resources()
        assert any(str(r.uri) == "data://test_resource" for r in resources)
        response = client_with_path.post("/test/resources/data://test_resource/disable")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Disabled resource: data://test_resource"}
        resources = await mcp_with_path.get_resources()
        assert not any(str(r.uri) == "data://test_resource" for r in resources)

    async def test_enable_prompt_route_with_path(self, client_with_path, mcp_with_path):
        mcp_with_path.disable(keys=["prompt:test_prompt"])
        prompts = await mcp_with_path.get_prompts()
        assert not any(p.name == "test_prompt" for p in prompts)
        response = client_with_path.post("/test/prompts/test_prompt/enable")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Enabled prompt: test_prompt"}
        prompts = await mcp_with_path.get_prompts()
        assert any(p.name == "test_prompt" for p in prompts)


class TestComponentManagerWithPathAuth:
    """Test component manager routes with auth when mounted at a custom path."""

    def setup_method(self):
        # Generate a key pair and create an auth provider
        key_pair = RSAKeyPair.generate()
        self.auth = JWTVerifier(
            public_key=key_pair.public_key,
            issuer="https://dev.example.com",
            audience="my-dev-server",
        )
        self.mcp = FastMCP("TestServerWithPathAuth", auth=self.auth)
        set_up_component_manager(
            server=self.mcp, path="/test", required_scopes=["tool:write", "tool:read"]
        )
        self.token = key_pair.create_token(
            subject="dev-user",
            issuer="https://dev.example.com",
            audience="my-dev-server",
            scopes=["tool:read", "tool:write"],
        )
        self.token_without_scopes = key_pair.create_token(
            subject="dev-user",
            issuer="https://dev.example.com",
            audience="my-dev-server",
            scopes=[],
        )

        @self.mcp.tool
        def test_tool() -> str:
            return "test_tool_result"

        @self.mcp.resource("data://test_resource")
        def test_resource() -> str:
            return "test_resource_result"

        @self.mcp.prompt
        def test_prompt() -> str:
            return "test_prompt_result"

        self.client = TestClient(self.mcp.http_app())

    async def test_unauthorized_enable_tool(self):
        self.mcp.disable(keys=["tool:test_tool"])
        tools = await self.mcp.get_tools()
        assert not any(t.name == "test_tool" for t in tools)
        response = self.client.post("/test/tools/test_tool/enable")
        assert response.status_code == 401
        tools = await self.mcp.get_tools()
        assert not any(t.name == "test_tool" for t in tools)

    async def test_forbidden_enable_tool(self):
        self.mcp.disable(keys=["tool:test_tool"])
        tools = await self.mcp.get_tools()
        assert not any(t.name == "test_tool" for t in tools)
        response = self.client.post(
            "/test/tools/test_tool/enable",
            headers={"Authorization": "Bearer " + self.token_without_scopes},
        )
        assert response.status_code == 403
        tools = await self.mcp.get_tools()
        assert not any(t.name == "test_tool" for t in tools)

    async def test_authorized_enable_tool(self):
        self.mcp.disable(keys=["tool:test_tool"])
        tools = await self.mcp.get_tools()
        assert not any(t.name == "test_tool" for t in tools)
        response = self.client.post(
            "/test/tools/test_tool/enable",
            headers={"Authorization": "Bearer " + self.token},
        )
        assert response.status_code == 200
        assert response.json() == {"message": "Enabled tool: test_tool"}
        tools = await self.mcp.get_tools()
        assert any(t.name == "test_tool" for t in tools)

    async def test_unauthorized_disable_resource(self):
        resources = await self.mcp.get_resources()
        assert any(str(r.uri) == "data://test_resource" for r in resources)
        response = self.client.post("/test/resources/data://test_resource/disable")
        assert response.status_code == 401
        resources = await self.mcp.get_resources()
        assert any(str(r.uri) == "data://test_resource" for r in resources)

    async def test_forbidden_disable_resource(self):
        resources = await self.mcp.get_resources()
        assert any(str(r.uri) == "data://test_resource" for r in resources)
        response = self.client.post(
            "/test/resources/data://test_resource/disable",
            headers={"Authorization": "Bearer " + self.token_without_scopes},
        )
        assert response.status_code == 403
        resources = await self.mcp.get_resources()
        assert any(str(r.uri) == "data://test_resource" for r in resources)

    async def test_authorized_disable_resource(self):
        resources = await self.mcp.get_resources()
        assert any(str(r.uri) == "data://test_resource" for r in resources)
        response = self.client.post(
            "/test/resources/data://test_resource/disable",
            headers={"Authorization": "Bearer " + self.token},
        )
        assert response.status_code == 200
        assert response.json() == {"message": "Disabled resource: data://test_resource"}
        resources = await self.mcp.get_resources()
        assert not any(str(r.uri) == "data://test_resource" for r in resources)

    async def test_unauthorized_enable_prompt(self):
        self.mcp.disable(keys=["prompt:test_prompt"])
        prompts = await self.mcp.get_prompts()
        assert not any(p.name == "test_prompt" for p in prompts)
        response = self.client.post("/test/prompts/test_prompt/enable")
        assert response.status_code == 401
        prompts = await self.mcp.get_prompts()
        assert not any(p.name == "test_prompt" for p in prompts)

    async def test_forbidden_enable_prompt(self):
        self.mcp.disable(keys=["prompt:test_prompt"])
        prompts = await self.mcp.get_prompts()
        assert not any(p.name == "test_prompt" for p in prompts)
        response = self.client.post(
            "/test/prompts/test_prompt/enable",
            headers={"Authorization": "Bearer " + self.token_without_scopes},
        )
        assert response.status_code == 403
        prompts = await self.mcp.get_prompts()
        assert not any(p.name == "test_prompt" for p in prompts)

    async def test_authorized_enable_prompt(self):
        self.mcp.disable(keys=["prompt:test_prompt"])
        prompts = await self.mcp.get_prompts()
        assert not any(p.name == "test_prompt" for p in prompts)
        response = self.client.post(
            "/test/prompts/test_prompt/enable",
            headers={"Authorization": "Bearer " + self.token},
        )
        assert response.status_code == 200
        assert response.json() == {"message": "Enabled prompt: test_prompt"}
        prompts = await self.mcp.get_prompts()
        assert any(p.name == "test_prompt" for p in prompts)
