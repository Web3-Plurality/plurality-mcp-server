from mcp.server.fastmcp import FastMCP
from plurality_mcp_server.auth import JWTAuthMiddleware
from plurality_mcp_server.tools import register_tools
from plurality_mcp_server.oauth import mount_oauth_routes

# ── MCP app ──
mcp_app = FastMCP(name="mcp", stateless_http=True, json_response=True)
register_tools(mcp_app)

# ── ASGI app (Starlette under the hood) ──
mcp_server = mcp_app.streamable_http_app()
mount_oauth_routes(mcp_server)

# ── Middleware (pure ASGI — no BaseHTTPMiddleware) ──
# JWT auth middleware validates Hydra tokens locally via JWKS
mcp_server = JWTAuthMiddleware(mcp_server)
# CORS — handled entirely by Traefik (see ory-hydra/dynamic.yml).
# DO NOT enable app-level CORSMiddleware — duplicate headers cause browser rejections.
