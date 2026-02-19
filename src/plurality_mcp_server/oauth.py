import json
from starlette.routing import Route
from starlette.responses import JSONResponse as StarletteJSONResponse
from starlette.requests import Request
from plurality_mcp_server.config import http_client, HYDRA_PUBLIC_URL, MCP_RESOURCE_URL


# ── OAuth metadata endpoints (served by MCP server, routed by Traefik) ──

async def oauth_protected_resource_metadata(request: Request):
    """OAuth2 Protected Resource Metadata (RFC 9728) — required by MCP spec."""
    return StarletteJSONResponse({
        "resource": MCP_RESOURCE_URL,
        "authorization_servers": [MCP_RESOURCE_URL],
        "scopes_supported": ["openid", "offline_access"],
        "bearer_methods_supported": ["header"],
    })


async def oauth_authorization_server_metadata(request: Request):
    """Fetch authorization server metadata from Hydra and inject registration_endpoint.

    Since Hydra's issuer is set to the Traefik entrypoint (http://localhost:5050),
    all URLs in the metadata already point to :5050. Traefik routes them to Hydra.
    We only need to inject registration_endpoint (Hydra omits it from discovery).
    """
    try:
        resp = await http_client.get(
            f"{HYDRA_PUBLIC_URL}/.well-known/openid-configuration",
            timeout=5.0,
        )
        if resp.status_code != 200:
            return StarletteJSONResponse(
                {"error": f"Hydra returned {resp.status_code}", "body": resp.text},
                status_code=502,
            )
        metadata = resp.json()
        if "registration_endpoint" not in metadata:
            metadata["registration_endpoint"] = f"{MCP_RESOURCE_URL}/register"
        return StarletteJSONResponse(metadata)
    except Exception as e:
        return StarletteJSONResponse(
            {"error": f"Failed to reach Hydra: {str(e)}"},
            status_code=502,
        )


# ── DCR proxy (sanitizes request/response for Hydra ↔ MCP SDK compatibility) ──
# Workaround for: https://github.com/modelcontextprotocol/typescript-sdk/issues/754
#
# Request:  MCP SDK sends null for optional fields → Hydra rejects (expects array or omitted)
# Response: Hydra returns null for optional fields → MCP SDK Zod schema rejects (expects array)
# Fix:      Strip nulls from request, coerce array fields; strip nulls from response.

# Fields that RFC 7591 defines as arrays — coerce non-array values to arrays
_DCR_ARRAY_FIELDS = {
    "contacts", "grant_types", "response_types", "redirect_uris",
    "post_logout_redirect_uris", "request_uris", "allowed_cors_origins", "audience",
}

# Fields that Hydra validates as URLs — strip empty strings (clients like Claude Code send "")
_DCR_URI_FIELDS = {
    "client_uri", "logo_uri", "policy_uri", "tos_uri", "jwks_uri",
    "backchannel_logout_uri", "frontchannel_logout_uri", "sector_identifier_uri",
}


def _sanitize_dcr_request(body_json: dict) -> dict:
    """Sanitize DCR request body before sending to Hydra."""
    sanitized = {}
    for key, value in body_json.items():
        if value is None:
            continue  # Strip null fields — Hydra rejects them for typed fields
        if key in _DCR_URI_FIELDS and value == "":
            continue  # Strip empty URI strings — Hydra rejects invalid URLs
        if key in _DCR_ARRAY_FIELDS and not isinstance(value, list):
            sanitized[key] = [value] if value else []
        else:
            sanitized[key] = value
    return sanitized


def _sanitize_dcr_response(body_json: dict) -> dict:
    """Sanitize DCR response body before returning to MCP client."""
    return {
        k: v for k, v in body_json.items()
        if v is not None and not (k in _DCR_URI_FIELDS and v == "")
    }


async def proxy_dcr_to_hydra(request: Request):
    """Proxy Dynamic Client Registration to Hydra with request/response sanitization."""
    try:
        body = await request.body()
        headers = dict(request.headers)
        headers.pop("host", None)

        if body:
            try:
                body_json = json.loads(body)
                body_json = _sanitize_dcr_request(body_json)
                body = json.dumps(body_json).encode()
                headers["content-length"] = str(len(body))
            except (json.JSONDecodeError, TypeError):
                pass

        resp = await http_client.request(
            method=request.method,
            url=f"{HYDRA_PUBLIC_URL}/oauth2/register",
            content=body,
            headers=headers,
            timeout=10.0,
        )

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            resp_json = resp.json()
            if isinstance(resp_json, dict):
                resp_json = _sanitize_dcr_response(resp_json)
            return StarletteJSONResponse(content=resp_json, status_code=resp.status_code)
        else:
            return StarletteJSONResponse(content={"raw": resp.text}, status_code=resp.status_code)

    except Exception as e:
        return StarletteJSONResponse(
            {"error": f"DCR proxy failed: {str(e)}"},
            status_code=502,
        )


def mount_oauth_routes(starlette_app):
    """Mount all OAuth metadata and DCR proxy routes on the Starlette app."""
    # Per RFC 9728, clients may append the resource path (e.g. /mcp) to well-known URLs
    for path_suffix in ["", "/mcp"]:
        starlette_app.routes.insert(0, Route(
            f"/.well-known/oauth-protected-resource{path_suffix}",
            endpoint=oauth_protected_resource_metadata,
            methods=["GET"],
        ))
        starlette_app.routes.insert(0, Route(
            f"/.well-known/oauth-authorization-server{path_suffix}",
            endpoint=oauth_authorization_server_metadata,
            methods=["GET"],
        ))
        starlette_app.routes.insert(0, Route(
            f"/.well-known/openid-configuration{path_suffix}",
            endpoint=oauth_authorization_server_metadata,
            methods=["GET"],
        ))

    # DCR routes (Traefik forwards /register and /oauth2/register here for sanitization)
    for dcr_path in ["/register", "/oauth2/register"]:
        starlette_app.routes.insert(0, Route(
            dcr_path,
            endpoint=proxy_dcr_to_hydra,
            methods=["GET", "POST"],
        ))
