# Health Endpoint

## Problem

Production deployments need health checks for:
- Load balancers (AWS ALB, nginx, etc.)
- Kubernetes liveness/readiness probes
- Monitoring systems (Datadog, Prometheus, etc.)
- Uptime monitoring services

Currently users must implement health endpoints themselves or rely on generic HTTP probes that don't understand server state.

## Solution

Built-in `/health` endpoint that returns server status and optional custom health checks.

## API

### Basic Usage

```python
from fastmcp import FastMCP

# Enable health endpoint (defaults to /health)
mcp = FastMCP("server", health_endpoint=True)
```

### With Configuration

```python
from fastmcp.server.health import HealthConfig, HealthCheck

async def check_database() -> tuple[str, bool]:
    """Custom health check."""
    try:
        await db.execute("SELECT 1")
        return "database", True
    except Exception:
        return "database", False

async def check_cache() -> tuple[str, bool]:
    try:
        await cache.ping()
        return "cache", True
    except Exception:
        return "cache", False

mcp = FastMCP(
    "server",
    health_endpoint=HealthConfig(
        path="/health",
        checks=[check_database, check_cache],
        include_version=True,
        include_uptime=True,
    )
)
```

### Readiness Endpoint

```python
# Separate liveness (is server running?) and readiness (can it serve traffic?)
mcp = FastMCP(
    "server",
    health_endpoint=HealthConfig(
        liveness_path="/health",
        readiness_path="/ready",
        readiness_checks=[check_database, check_cache],
    )
)
```

## Response Format

### Healthy Response (200 OK)

```json
{
  "status": "healthy",
  "version": "3.0.0",
  "uptime_seconds": 3600.5,
  "timestamp": "2025-01-19T12:00:00Z",
  "checks": {
    "database": "ok",
    "cache": "ok"
  }
}
```

### Unhealthy Response (503 Service Unavailable)

```json
{
  "status": "unhealthy",
  "version": "3.0.0",
  "uptime_seconds": 3600.5,
  "timestamp": "2025-01-19T12:00:00Z",
  "checks": {
    "database": "failed",
    "cache": "ok"
  }
}
```

### Minimal Response

If `checks=[]`, `include_version=False`, `include_uptime=False`:

```json
{
  "status": "healthy"
}
```

## Implementation

### Location

- `src/fastmcp/server/health.py` - Core implementation
- `src/fastmcp/server/server.py` - Integration with FastMCP

### HealthConfig

```python
from dataclasses import dataclass
from collections.abc import Callable, Awaitable

HealthCheckFn = Callable[[], Awaitable[tuple[str, bool]]]

@dataclass
class HealthConfig:
    """Configuration for health endpoints."""

    # Liveness endpoint (is server alive?)
    liveness_path: str = "/health"

    # Readiness endpoint (can server handle requests?)
    readiness_path: str | None = None

    # Health checks (name, passed/failed)
    checks: list[HealthCheckFn] = field(default_factory=list)

    # Checks only for readiness (not liveness)
    readiness_checks: list[HealthCheckFn] = field(default_factory=list)

    # Include server version in response
    include_version: bool = True

    # Include uptime in response
    include_uptime: bool = True

    # Timeout for each check
    check_timeout: float = 5.0
```

### Health Endpoint Handler

```python
class HealthHandler:
    def __init__(self, config: HealthConfig, server: FastMCP):
        self.config = config
        self.server = server
        self.start_time = time.time()

    async def liveness(self, request: Request) -> Response:
        """Liveness check - is the server running?"""
        result = {
            "status": "healthy",
        }

        if self.config.include_version:
            result["version"] = fastmcp.__version__

        if self.config.include_uptime:
            result["uptime_seconds"] = time.time() - self.start_time

        if self.config.checks:
            check_results = await self._run_checks(self.config.checks)
            result["checks"] = check_results

            if any(status == "failed" for status in check_results.values()):
                result["status"] = "unhealthy"
                return JSONResponse(result, status_code=503)

        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        return JSONResponse(result)

    async def readiness(self, request: Request) -> Response:
        """Readiness check - can the server handle requests?"""
        # Liveness checks
        liveness_checks = await self._run_checks(self.config.checks)

        # Readiness-specific checks
        readiness_checks = await self._run_checks(self.config.readiness_checks)

        all_checks = {**liveness_checks, **readiness_checks}

        result = {
            "status": "ready" if all(s == "ok" for s in all_checks.values()) else "not_ready",
            "checks": all_checks,
        }

        if self.config.include_version:
            result["version"] = fastmcp.__version__

        if self.config.include_uptime:
            result["uptime_seconds"] = time.time() - self.start_time

        result["timestamp"] = datetime.now(timezone.utc).isoformat()

        status_code = 200 if result["status"] == "ready" else 503
        return JSONResponse(result, status_code=status_code)

    async def _run_checks(self, checks: list[HealthCheckFn]) -> dict[str, str]:
        """Run health checks with timeout."""
        results = {}

        for check in checks:
            try:
                async with asyncio.timeout(self.config.check_timeout):
                    name, passed = await check()
                    results[name] = "ok" if passed else "failed"
            except asyncio.TimeoutError:
                results[name] = "timeout"
            except Exception:
                results[name] = "error"

        return results
```

### Integration with FastMCP

In `server.py`:

```python
class FastMCP:
    def __init__(
        self,
        name: str,
        *,
        health_endpoint: bool | HealthConfig = False,
        **kwargs,
    ):
        self._health_handler: HealthHandler | None = None

        if health_endpoint:
            config = health_endpoint if isinstance(health_endpoint, HealthConfig) else HealthConfig()
            self._health_handler = HealthHandler(config, self)

    async def handle_http_request(self, request: Request) -> Response:
        """Route HTTP requests."""
        path = request.url.path

        # Health endpoints
        if self._health_handler:
            if path == self._health_handler.config.liveness_path:
                return await self._health_handler.liveness(request)

            if self._health_handler.config.readiness_path and path == self._health_handler.config.readiness_path:
                return await self._health_handler.readiness(request)

        # Normal MCP request handling
        return await self._handle_mcp_request(request)
```

## Edge Cases

1. **stdio transport** - Health endpoints only work for HTTP transports. Ignore for stdio.

2. **Startup time** - Readiness checks might fail during startup (DB connecting, etc.). This is correct behavior.

3. **Check timeouts** - Individual checks should timeout independently. Don't let one slow check block others.

4. **Check errors** - Exceptions in checks should be caught and reported as "error" status.

5. **No checks** - If no checks configured, health endpoint just returns `{"status": "healthy"}`.

6. **Concurrent requests** - Health checks might be called concurrently. Ensure checks are safe for concurrent execution.

## Testing

Add `tests/server/test_health.py`:

```python
async def test_basic_health_endpoint():
    mcp = FastMCP("test", health_endpoint=True)

    async with httpx.AsyncClient(app=mcp.get_asgi_app()) as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

async def test_health_checks():
    async def good_check():
        return "good", True

    async def bad_check():
        return "bad", False

    mcp = FastMCP(
        "test",
        health_endpoint=HealthConfig(checks=[good_check, bad_check])
    )

    async with httpx.AsyncClient(app=mcp.get_asgi_app()) as client:
        response = await client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["checks"]["good"] == "ok"
        assert data["checks"]["bad"] == "failed"

async def test_readiness_endpoint():
    async def db_check():
        return "database", True

    mcp = FastMCP(
        "test",
        health_endpoint=HealthConfig(
            readiness_path="/ready",
            readiness_checks=[db_check]
        )
    )

    async with httpx.AsyncClient(app=mcp.get_asgi_app()) as client:
        # Liveness should pass with no checks
        response = await client.get("/health")
        assert response.status_code == 200

        # Readiness should pass with passing checks
        response = await client.get("/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
```

## Documentation

Add to `docs/servers/health.mdx`:

- Why health endpoints matter
- Basic usage
- Custom health checks
- Liveness vs readiness
- Kubernetes integration examples
- AWS ALB integration examples
- Best practices for health checks

## Examples

### Kubernetes Integration

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: fastmcp-server
spec:
  containers:
  - name: server
    image: my-fastmcp-server
    ports:
    - containerPort: 8000
    livenessProbe:
      httpGet:
        path: /health
        port: 8000
      initialDelaySeconds: 10
      periodSeconds: 30
    readinessProbe:
      httpGet:
        path: /ready
        port: 8000
      initialDelaySeconds: 5
      periodSeconds: 10
```

### AWS ALB Target Group

Health check configuration:
- Protocol: HTTP
- Path: `/health`
- Success codes: 200
- Interval: 30 seconds
- Timeout: 5 seconds
- Healthy threshold: 2
- Unhealthy threshold: 3
