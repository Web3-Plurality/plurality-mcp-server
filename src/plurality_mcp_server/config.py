import contextvars
import os
import httpx

# ── Environment configuration ──
# MCP_RESOURCE_URL: public-facing URL (Traefik entrypoint) advertised to clients
# HYDRA_ISSUER: expected issuer claim in Hydra JWTs
# BACKEND_API_URL: trusted backend API for data access
MCP_RESOURCE_URL = os.getenv("MCP_RESOURCE_URL", "http://localhost:5050")
HYDRA_ISSUER = os.getenv("HYDRA_ISSUER", "http://localhost:5050")
BACKEND_API_URL = os.getenv("BACKEND_API_URL", "http://localhost:5000")

# ── Shared httpx client (connection pooling, reused across requests) ──
http_client = httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_connections=20))

# ── Per-request context (async-safe via contextvars) ──
# Auth middleware sets these after JWT validation.
# Tool functions read them to forward the JWT to backend API calls.
current_token: contextvars.ContextVar[str] = contextvars.ContextVar('auth_token', default='')
current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar('user_id', default='')
