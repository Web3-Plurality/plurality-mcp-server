import asyncio
import json
import time
import httpx
import jwt as pyjwt
from plurality_mcp_server.config import http_client, HYDRA_ISSUER, MCP_RESOURCE_URL, BACKEND_API_URL, current_token, current_user_id

# ── PAT prefix used by Plurality personal access tokens ──
PAT_PREFIX = "plur_pat_"

# ── JWKS cache for local JWT validation ──
_jwks_cache: dict = {"keys": [], "fetched_at": 0}
_JWKS_CACHE_TTL = 3600  # 1 hour


async def _get_jwks_keys() -> list:
    """Fetch and cache Hydra's JWKS public keys."""
    now = time.time()
    if _jwks_cache["keys"] and (now - _jwks_cache["fetched_at"]) < _JWKS_CACHE_TTL:
        return _jwks_cache["keys"]

    jwks_url = f"{MCP_RESOURCE_URL}/.well-known/jwks.json"
    resp = await http_client.get(jwks_url, timeout=10.0)
    resp.raise_for_status()
    jwks_data = resp.json()
    _jwks_cache["keys"] = jwks_data.get("keys", [])
    _jwks_cache["fetched_at"] = now
    print(f"[JWKS] Refreshed {len(_jwks_cache['keys'])} keys from {jwks_url}", flush=True)
    return _jwks_cache["keys"]


async def prewarm_jwks(max_retries: int = 5, initial_delay: float = 2.0) -> None:
    """Pre-warm JWKS cache on startup with retries for slow network environments."""
    jwks_url = f"{MCP_RESOURCE_URL}/.well-known/jwks.json"
    delay = initial_delay
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[JWKS] Pre-warm attempt {attempt}/{max_retries} from {jwks_url}", flush=True)
            keys = await _get_jwks_keys()
            if keys:
                print(f"[JWKS] Pre-warm successful — {len(keys)} keys cached", flush=True)
                return
        except Exception as e:
            print(f"[JWKS] Pre-warm attempt {attempt} failed: {e}", flush=True)
        if attempt < max_retries:
            print(f"[JWKS] Retrying in {delay:.0f}s...", flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)  # exponential backoff, max 30s
    print("[JWKS] Pre-warm exhausted all retries — first request will trigger fetch", flush=True)


async def verify_jwt(token: str) -> dict:
    """Verify a Hydra JWT token locally using cached JWKS public keys."""
    unverified_header = pyjwt.get_unverified_header(token)
    kid = unverified_header.get("kid")

    keys = await _get_jwks_keys()

    # Find matching key
    matching_key = None
    for key in keys:
        if key.get("kid") == kid:
            matching_key = key
            break

    if not matching_key:
        # Key not found — force refresh cache and retry once
        _jwks_cache["fetched_at"] = 0
        keys = await _get_jwks_keys()
        for key in keys:
            if key.get("kid") == kid:
                matching_key = key
                break

    if not matching_key:
        raise pyjwt.InvalidTokenError(f"No matching key found for kid={kid}")

    # Build public key from JWK and verify
    public_key = pyjwt.algorithms.RSAAlgorithm.from_jwk(matching_key)
    decoded = pyjwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer=HYDRA_ISSUER,
        options={"verify_aud": False},  # MCP tokens may not have audience
    )
    return decoded


# ── Pure ASGI middleware for JWT validation ──
# IMPORTANT: Must NOT use BaseHTTPMiddleware — it buffers SSE streams,
# which breaks MCP's Streamable HTTP transport (FastMCP Issue #858).
class JWTAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def _send_json_error(self, scope, receive, send, status, body, extra_headers=None):
        """Send a JSON error response directly via ASGI."""
        payload = json.dumps(body).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
        ]
        if extra_headers:
            for k, v in extra_headers.items():
                headers.append((k.encode("latin-1"), v.encode("latin-1")))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": payload})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")

        # Parse headers from ASGI scope (byte tuples)
        headers_dict = {}
        for key, value in scope.get("headers", []):
            headers_dict[key.decode("latin-1").lower()] = value.decode("latin-1")

        # Skip authentication for discovery, health, and DCR endpoints
        if path in ["/health", "/docs", "/openapi.json", "/register"] or \
           path.startswith("/.well-known/") or \
           path.startswith("/oauth2/"):
            await self.app(scope, receive, send)
            return

        # Skip CORS preflight
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        auth_header = headers_dict.get("authorization", "")

        if not auth_header:
            await self._send_json_error(scope, receive, send, 401, {
                "error": "Missing Authorization header",
                "message": "Please provide a Bearer token in the Authorization header",
            }, {"WWW-Authenticate": f'Bearer resource_metadata="{MCP_RESOURCE_URL}/.well-known/oauth-protected-resource"'})
            return

        if not auth_header.startswith("Bearer "):
            await self._send_json_error(scope, receive, send, 401, {
                "error": "Invalid Authorization header format",
                "message": "Authorization header must be in format: Bearer <token>",
            })
            return

        token = auth_header.removeprefix("Bearer ").strip()

        # ── Personal Access Token (PAT) path ──
        # Validates via the backend (which owns the PAT DB). Keeps this server stateless.
        if token.startswith(PAT_PREFIX):
            try:
                verify_resp = await http_client.get(
                    f"{BACKEND_API_URL}/pat/tokens/verify",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=5.0,
                )
            except httpx.RequestError as e:
                await self._send_json_error(scope, receive, send, 502, {
                    "error": "pat_verify_failed",
                    "message": f"Failed to verify PAT with backend: {str(e)}",
                })
                return

            if verify_resp.status_code != 200:
                err_body = {}
                try:
                    err_body = verify_resp.json()
                except Exception:
                    pass
                await self._send_json_error(scope, receive, send, 401, {
                    "error": err_body.get("error", "invalid_pat"),
                    "message": "Invalid or expired API token",
                }, {"WWW-Authenticate": f'Bearer resource_metadata="{MCP_RESOURCE_URL}/.well-known/oauth-protected-resource"'})
                return

            info = verify_resp.json()
            pat_scopes = info.get("scopes", [])
            if "mcp:tools" not in pat_scopes:
                await self._send_json_error(scope, receive, send, 403, {
                    "error": "insufficient_scope",
                    "message": "Token missing required scope: mcp:tools",
                }, {"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="mcp:tools"'})
                return

            user_id = info.get("user_id", "")
            print(f"[AUTH-PAT] {method} {path} | user={user_id} | token=...{token[-8:]}", flush=True)
            current_token.set(token)
            current_user_id.set(user_id)
            await self.app(scope, receive, send)
            return

        try:
            decoded = await verify_jwt(token)
            user_id = decoded.get("sub", "")

            # Verify token has mcp:tools scope (required for MCP resource access)
            token_scopes = decoded.get("scp", [])
            if "mcp:tools" not in token_scopes:
                await self._send_json_error(scope, receive, send, 403, {
                    "error": "insufficient_scope",
                    "message": "Token missing required scope: mcp:tools",
                }, {"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="mcp:tools", resource_metadata="{MCP_RESOURCE_URL}/.well-known/oauth-protected-resource"'})
                return

            print(f"[AUTH] {method} {path} | user={user_id} | token=...{token[-8:]}", flush=True)

            # Set per-request context for tool functions
            current_token.set(token)
            current_user_id.set(user_id)

            await self.app(scope, receive, send)

        except pyjwt.ExpiredSignatureError:
            await self._send_json_error(scope, receive, send, 401, {
                "error": "Token expired",
                "message": "Access token has expired. Please refresh your token.",
            }, {"WWW-Authenticate": f'Bearer error="invalid_token", resource_metadata="{MCP_RESOURCE_URL}/.well-known/oauth-protected-resource"'})
        except pyjwt.InvalidTokenError as e:
            await self._send_json_error(scope, receive, send, 401, {
                "error": "Invalid token",
                "message": f"Token validation failed: {str(e)}",
            }, {"WWW-Authenticate": f'Bearer error="invalid_token", resource_metadata="{MCP_RESOURCE_URL}/.well-known/oauth-protected-resource"'})
        except httpx.RequestError as e:
            await self._send_json_error(scope, receive, send, 502, {
                "error": "JWKS fetch failed",
                "message": f"Failed to fetch JWKS from Hydra: {str(e)}",
            })
        except Exception as e:
            await self._send_json_error(scope, receive, send, 500, {
                "error": "Authentication error",
                "message": f"Unexpected error during authentication: {str(e)}",
            })
