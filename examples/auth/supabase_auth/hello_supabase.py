import base64
import json
import logging
import os

import httpx
from dotenv import load_dotenv

from fastmcp import FastMCP
from fastmcp.server.auth.providers.supabase import SupabaseProvider
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.utilities.logging import get_logger

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = get_logger(__name__)

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_PROJECT_URL"]
BASE_URL = os.environ["BASE_URL"]

logger.info(f"Initializing SupabaseProvider with project_url: {SUPABASE_URL}")
logger.info(f"Server base_url: {BASE_URL}")


class InstrumentedJWTVerifier:
    def __init__(self, base_verifier, project_url):
        self.base_verifier = base_verifier
        self.project_url = project_url

    def _decode_header(self, token: str):
        try:
            header_b64 = token.split(".")[0]
            padding = "=" * (-len(header_b64) % 4)
            decoded = base64.urlsafe_b64decode(header_b64 + padding)
            return json.loads(decoded)
        except Exception as e:
            return {"error": str(e)}

    async def _fetch_jwks(self):
        url = f"{self.project_url}/auth/v1/.well-known/jwks.json"
        async with httpx.AsyncClient() as client:
            res = await client.get(url)
            return res.json()

    async def verify_token(self, token: str) -> dict:
        logger.info("🔍 ===== TOKEN DEBUG START =====")

        header = self._decode_header(token)
        logger.info(f"🧾 JWT HEADER: {header}")

        alg = header.get("alg")
        kid = header.get("kid")

        logger.info(f"🔑 alg = {alg}")
        logger.info(f"🆔 kid = {kid}")

        if alg == "HS256":
            logger.error("🚨 TOKEN IS HS256 → WILL NEVER MATCH JWKS (ROOT CAUSE)")

        jwks = await self._fetch_jwks()
        jwks_kids = [k.get("kid") for k in jwks.get("keys", [])]

        logger.info(f"📦 JWKS kids: {jwks_kids}")

        if kid not in jwks_kids:
            logger.error("🚨 KID NOT FOUND IN JWKS → TOKEN CANNOT BE VERIFIED")

        try:
            payload_part = token.split(".")[1]
            payload = json.loads(
                base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
            )
            logger.info(
                f"📦 JWT PAYLOAD (truncated): {dict(list(payload.items())[:5])}"
            )
        except Exception as e:
            logger.debug(f"Could not decode payload: {e}")

        try:
            result = await self.base_verifier.verify_token(token)
            logger.info(f"✅ TOKEN VERIFIED: {result}")
            return result
        except Exception as e:
            logger.error(f"❌ TOKEN VERIFICATION FAILED: {e}")
            raise
        finally:
            logger.info("🔍 ===== TOKEN DEBUG END =====\n")

    def __getattr__(self, name):
        return getattr(self.base_verifier, name)


class RequestLoggingMiddleware(Middleware):
    async def on_message(self, context: MiddlewareContext, call_next):
        logger.info(f"📥 INCOMING REQUEST: {context.method} from {context.source}")

        if hasattr(context, "fastmcp_context") and context.fastmcp_context:
            ctx = context.fastmcp_context
            if hasattr(ctx, "request_context") and ctx.request_context:
                headers = getattr(ctx.request_context, "headers", {})
                auth_header = headers.get("authorization") if headers else None
                if auth_header:
                    logger.info(
                        f"🔑 AUTH HEADER: {auth_header[:50]}..."
                        if len(auth_header) > 50
                        else f"🔑 AUTH HEADER: {auth_header}"
                    )

        result = await call_next(context)
        logger.info(f"📤 RESPONSE: {context.method} completed")
        return result


auth = SupabaseProvider(
    project_url=SUPABASE_URL,
    base_url=BASE_URL,
)

auth.token_verifier = InstrumentedJWTVerifier(auth.token_verifier, SUPABASE_URL)

logger.info("Creating FastMCP server with authentication")

mcp = FastMCP(
    "Hello Supabase",
    auth=auth,
    middleware=[LoggingMiddleware()],
)

mcp.add_middleware(RequestLoggingMiddleware())


@mcp.tool
def hello_supabase() -> str:
    """Simple authenticated hello world."""
    logger.info("🔧 TOOL CALLED: hello_supabase")
    return "Hello from Supabase-protected MCP server!"


if __name__ == "__main__":
    logger.info("🚀 Starting FastMCP server on port 3000")
    logger.info(f"📍 Server will be available at: {BASE_URL}")
    logger.info("🔐 Authentication endpoints:")
    logger.info(f"   - JWKS: {SUPABASE_URL}/auth/v1/.well-known/jwks.json")
    logger.info(
        f"   - OAuth Metadata: {BASE_URL}/.well-known/oauth-authorization-server"
    )
    mcp.run(transport="http", port=3000)
