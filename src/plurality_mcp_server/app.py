import asyncio
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from plurality_mcp_server.auth import JWTAuthMiddleware, prewarm_jwks
from plurality_mcp_server.tools import register_tools

# ── MCP app ──
# host="0.0.0.0" for container networking; transport_security allows the
# public domain forwarded by the reverse proxy (Traefik).
_mcp_resource_url = os.getenv("MCP_RESOURCE_URL", "http://localhost:5051")
mcp_app = FastMCP(
    name="mcp",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
    port=5051,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "localhost:*",
            "127.0.0.1:*",
            _mcp_resource_url.split("//")[-1],  # e.g. "dev.plurality.network"
        ],
        allowed_origins=[
            "http://localhost:*",
            "http://127.0.0.1:*",
            _mcp_resource_url,                   # e.g. "https://dev.plurality.network"
        ],
    ),
)
register_tools(mcp_app)

# ── ASGI app (Starlette under the hood) ──
mcp_server = mcp_app.streamable_http_app()

# ── Middleware (pure ASGI — no BaseHTTPMiddleware) ──
# JWT auth middleware validates Hydra tokens locally via JWKS
mcp_server = JWTAuthMiddleware(mcp_server)
# CORS — handled entirely by Traefik (see ory-hydra/dynamic.yml).
# DO NOT enable app-level CORSMiddleware — duplicate headers cause browser rejections.


# ── Startup: pre-warm JWKS cache ──
_original_app = mcp_server

async def _app_with_jwks_prewarm(scope, receive, send):
    """Wrapper that pre-warms JWKS on the first ASGI lifespan startup."""
    if scope["type"] == "lifespan":
        # Intercept lifespan to inject JWKS pre-warm
        async def _receive_wrapper():
            message = await receive()
            if message.get("type") == "lifespan.startup":
                asyncio.ensure_future(prewarm_jwks())
            return message
        await _original_app(scope, _receive_wrapper, send)
    else:
        await _original_app(scope, receive, send)

mcp_server = _app_with_jwks_prewarm
