import httpx
import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import PlainTextResponse
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from fastmcp.server.openapi import (
    FastMCPOpenAPI,
    MCPType,
    RouteMap,
)


class User(BaseModel):
    id: int
    name: str
    active: bool


class UserCreate(BaseModel):
    name: str
    active: bool


@pytest.fixture
def users_db() -> dict[int, User]:
    return {
        1: User(id=1, name="Alice", active=True),
        2: User(id=2, name="Bob", active=True),
        3: User(id=3, name="Charlie", active=False),
    }


# route maps for GET requests
# use these to create components of all types instead of just tools
GET_ROUTE_MAPS = [
    # GET requests with path parameters go to ResourceTemplate
    RouteMap(
        methods=["GET"],
        pattern=r".*\{.*\}.*",
        mcp_type=MCPType.RESOURCE_TEMPLATE,
    ),
    # GET requests without path parameters go to Resource
    RouteMap(methods=["GET"], pattern=r".*", mcp_type=MCPType.RESOURCE),
]


@pytest.fixture
def fastapi_app(users_db: dict[int, User]) -> FastAPI:
    app = FastAPI(title="FastAPI App")

    @app.get("/users", tags=["users", "list"])
    async def get_users() -> list[User]:
        """Get all users."""
        return sorted(users_db.values(), key=lambda x: x.id)

    @app.get("/search", tags=["search"])
    async def search_users(
        name: str | None = None, active: bool | None = None, min_id: int | None = None
    ) -> list[User]:
        """Search users with optional filters."""
        results = list(users_db.values())

        if name is not None:
            results = [u for u in results if name.lower() in u.name.lower()]
        if active is not None:
            results = [u for u in results if u.active == active]
        if min_id is not None:
            results = [u for u in results if u.id >= min_id]

        return sorted(results, key=lambda x: x.id)

    @app.get("/users/{user_id}", tags=["users", "detail"])
    async def get_user(user_id: int) -> User | None:
        """Get a user by ID."""
        return users_db.get(user_id)

    @app.get("/users/{user_id}/{is_active}", tags=["users", "detail"])
    async def get_user_active_state(user_id: int, is_active: bool) -> User | None:
        """Get a user by ID and filter by active state."""
        user = users_db.get(user_id)
        if user is not None and user.active == is_active:
            return user
        return None

    @app.post("/users", tags=["users", "create"])
    async def create_user(user: UserCreate) -> User:
        """Create a new user."""
        user_id = max(users_db.keys()) + 1
        new_user = User(id=user_id, **user.model_dump())
        users_db[user_id] = new_user
        return new_user

    @app.patch("/users/{user_id}/name", tags=["users", "update"])
    async def update_user_name(user_id: int, name: str) -> User:
        """Update a user's name."""
        user = users_db.get(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        user.name = name
        return user

    @app.get("/ping", response_class=PlainTextResponse)
    async def ping() -> str:
        """Ping the server."""
        return "pong"

    @app.get("/ping-bytes")
    async def ping_bytes() -> Response:
        """Ping the server and get a bytes response."""

        return Response(content=b"pong")

    return app


@pytest.fixture
def api_client(fastapi_app: FastAPI) -> AsyncClient:
    """Create a pre-configured httpx client for testing."""
    return AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test")


@pytest.fixture
async def fastmcp_openapi_server(
    fastapi_app: FastAPI, api_client: httpx.AsyncClient
) -> FastMCPOpenAPI:
    openapi_spec = fastapi_app.openapi()

    return FastMCPOpenAPI(
        openapi_spec=openapi_spec,
        client=api_client,
        name="Test App",
        route_maps=GET_ROUTE_MAPS,
    )
