import asyncio

from mcp.server.fastmcp import FastMCP
from plurality_mcp_server.auth import JWTAuthMiddleware, prewarm_jwks
from plurality_mcp_server.tools import register_tools

# ── MCP app ──
# host="0.0.0.0" for container networking; transport security is disabled
# because JWT auth middleware is the real security gate.
mcp_app = FastMCP(
    name="mcp",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
    port=5051,
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
        async def _receive_wrapper():
            message = await receive()
            if message.get("type") == "lifespan.startup":
                asyncio.ensure_future(prewarm_jwks())
            return message
        await _original_app(scope, _receive_wrapper, send)
    elif scope["type"] == "http":
        # Log every incoming HTTP request at the outermost layer
        path = scope.get("path", "?")
        method = scope.get("method", "?")
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        host = headers.get("host", "?")
        has_auth = "authorization" in headers
        auth_preview = headers["authorization"][:30] + "..." if has_auth else "NONE"
        print(f"[REQ] {method} {path} | host={host} | auth={auth_preview}", flush=True)

        # Capture the response status
        async def _send_wrapper(message):
            if message.get("type") == "http.response.start":
                status = message.get("status", "?")
                print(f"[RES] {method} {path} | status={status}", flush=True)
            await send(message)

        await _original_app(scope, receive, _send_wrapper)
    else:
        await _original_app(scope, receive, send)

mcp_server = _app_with_jwks_prewarm
