# Plurality MCP Server

An OAuth-secured [Model Context Protocol](https://modelcontextprotocol.io/) server that gives any MCP-compatible AI client (Claude Code, Claude Desktop, Cursor, etc.) read access to a user's Plurality memory — documents, notes, and files stored across memory buckets.

## Architecture

```
MCP Client (Claude Code, Cursor, etc.)
    │
    │  OAuth2 + Streamable HTTP
    ▼
Traefik (:5050)           ← single entrypoint for clients
    ├── /mcp              → MCP Server (:5051)
    ├── /.well-known/*    → MCP Server or Hydra
    ├── /oauth2/*         → Hydra (:4444)
    └── /register         → MCP Server (DCR proxy)
                                │
                                │ Bearer token
                                ▼
                          Backend API (:5000)
                                │
                                ▼
                          AI Service (:8000)
```

**Traefik** is the single entrypoint. It routes OAuth traffic to Hydra and MCP protocol traffic to the MCP server. The MCP server validates JWTs locally via Hydra's JWKS keys, then forwards the Bearer token to the Backend API for data access.

## Tools Exposed

| Tool | Description |
|---|---|
| `get_user_memory_buckets` | List all memory buckets (AI profiles) for the user |
| `list_items_in_memory_bucket` | List stored items in a specific bucket (metadata only) |
| `search_memory` | Semantic search across buckets with relevance scoring |
| `read_context` | Read the full content of a stored item with pagination |

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Docker and Docker Compose
- Running **API Gateway and AI Services**: MCP server calls it as an API gateway for database access, semantic search, and vector queries

## Local Setup

### 1. Install dependencies

```bash
cd plurality-mcp-server
pip install uv
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

Default values work for local development — no changes needed if Backend API runs on `:5000`:

```env
HYDRA_PUBLIC_URL=http://localhost:4444
HYDRA_ISSUER=http://localhost:5050
MCP_RESOURCE_URL=http://localhost:5050
BACKEND_API_URL=http://localhost:5000
```

### 3. Start Docker services (Hydra + Traefik for OAuth)

```bash
cd ory-hydra
docker compose up -d
```

This starts:

| Service | Port | Purpose |
|---|---|---|
| **PostgreSQL** | 5433 | Hydra's database |
| **Hydra** | 4444, 4445 | OAuth2/OIDC provider (public + admin) |
| **Traefik** | 5050 | API gateway / reverse proxy |

Wait for all services to be healthy:

```bash
docker compose ps
```

### 4. Start the MCP server

```bash
uv run uvicorn main:mcp_server --host 0.0.0.0 --port 5051 --reload
```

> **Port 5051**, not 5050. Traefik listens on 5050 and proxies `/mcp` to the MCP server on 5051.

### 5. Verify

Health check (direct):
```bash
curl http://localhost:5051/mcp/health
```

OAuth metadata (via Traefik):
```bash
curl http://localhost:5050/.well-known/oauth-protected-resource
```

Traefik dashboard (for debugging routes): http://localhost:8080

## MCP Client Integration — Production

Production URL: `https://app.plurality.network/mcp`

Dev URL: `https://dev.plurality.network/mcp`

### Claude Desktop / Web

**Easy setup (paid plans — Pro, Max, Team, Enterprise):**

1. Open **Settings → Connectors**
2. Click **Add** → paste `https://app.plurality.network/mcp`
3. Claude opens a browser window for OAuth login — sign in with your Plurality account
4. Once authenticated, the 4 Plurality tools appear in the chat input

**Development mode (free plan — Desktop app only):**

Free-plan users can connect the Desktop app via the [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) bridge by editing the config file directly. This does not work with the web app — only the native Desktop app reads this config.

1. Open the config file:
   - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
   - **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

2. Add the `mcpServers` block:

```json
{
  "mcpServers": {
    "plurality-memory": {
      "command": "npx",
      "args": ["mcp-remote", "https://app.plurality.network/mcp"]
    }
  }
}
```

> **Windows note:** If you get "Connection closed" errors, wrap with `cmd /c`:
> ```json
> { "command": "cmd", "args": ["/c", "npx", "mcp-remote", "https://app.plurality.network/mcp"] }
> ```

3. Fully restart Claude Desktop (quit and reopen, not just close the window).
4. On first use, `mcp-remote` opens your browser for OAuth login. After authenticating, tokens are cached locally.
5. Look for the tools icon in Claude Desktop's chat input — you should see the 4 Plurality tools.

### ChatGPT (requires paid plan)

1. Open **Settings → Connectors → Create**
2. Enter a name (e.g. "Plurality Memory") and paste `https://app.plurality.network/mcp` as the URL
3. Save the connector — ChatGPT discovers the OAuth metadata automatically
4. On first use in a chat, ChatGPT opens a browser window for OAuth login
5. After authenticating, the tools are available in your conversations

> Requires a Plus, Pro, Team, Enterprise, or Edu plan. Developer Mode must be enabled by a workspace admin under **Settings → Admin → Developer Mode**.

### Claude Code

```bash
claude mcp add --transport http plurality-memory https://app.plurality.network/mcp
```

Then authenticate inside Claude Code:

```
> /mcp
```

### Other MCP Clients

Any MCP client that supports streamable HTTP transport and OAuth2 with Dynamic Client Registration (DCR) can connect by pointing to `https://app.plurality.network/mcp`.

---

## MCP Client Integration — Local Development

For local dev, the MCP server runs at `http://localhost:5050/mcp` via Traefik. Since this isn't publicly reachable, some clients need workarounds.

### Claude Code — Terminal

1. Add the server:

```bash
claude mcp add --transport http plurality-memory http://localhost:5050/mcp
```

2. Inside Claude Code, authenticate via the `/mcp` command:

```
> /mcp
```

This triggers the OAuth2 flow — Claude Code discovers Hydra's authorization server from the `/.well-known/oauth-protected-resource` metadata, registers a client via Dynamic Client Registration (DCR), and opens the browser for login/consent.

### Claude Code — VSCode Extension

1. Authenticate first in the **terminal** using the steps above (`claude mcp add` + `/mcp`). OAuth tokens are stored and shared across terminal and VSCode.

2. Add `.mcp.json` to your project root:

```json
{
  "mcpServers": {
    "plurality-memory": {
      "type": "http",
      "url": "http://localhost:5050/mcp"
    }
  }
}
```

> **Note:** The VSCode extension may not trigger the OAuth browser flow automatically. Completing authentication via the terminal first ensures tokens are available for the extension.

### Claude Desktop

Claude Desktop's config file only supports stdio transport, so it can't connect to HTTP servers directly. Use the [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) bridge which translates between stdio and HTTP.

1. Open the config file:
   - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
   - **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

2. Add the `mcpServers` block:

```json
{
  "mcpServers": {
    "plurality-memory": {
      "command": "npx",
      "args": ["mcp-remote", "http://localhost:5050/mcp"]
    }
  }
}
```

> **Windows note:** If you get "Connection closed" errors, wrap with `cmd /c`:
> ```json
> { "command": "cmd", "args": ["/c", "npx", "mcp-remote", "http://localhost:5050/mcp"] }
> ```

3. Fully restart Claude Desktop (quit and reopen, not just close the window).

4. On first use, `mcp-remote` opens your browser for the Hydra OAuth login. After authenticating, tokens are cached locally.

5. Look for the tools icon in Claude Desktop's chat input — you should see the 4 Plurality tools.

### ChatGPT

Not supported for local development. ChatGPT's servers need to reach the OAuth endpoints over the public internet, which isn't possible with `localhost`. Use the production setup with a deployed URL instead.

### MCP Inspector (for debugging)

```bash
npx @modelcontextprotocol/inspector
```

Enter `http://localhost:5050/mcp` as the server URL. The inspector will walk through the OAuth flow and let you call tools interactively.

## OAuth2 Flow

The server uses [Ory Hydra](https://www.ory.sh/hydra/) as the OAuth2/OIDC provider with the following flow:

1. **Client discovers auth server** — fetches `/.well-known/oauth-protected-resource` from Traefik
2. **Client registers** — calls `/register` (Dynamic Client Registration) to get `client_id`/`client_secret`
3. **User authenticates** — browser opens Hydra's login flow, which redirects to the Backend API's login/consent endpoints at `localhost:3000`
4. **Token issued** — Hydra returns a JWT access token (RS256, 15min TTL) with the user's ID as the `sub` claim
5. **Authenticated requests** — client includes `Authorization: Bearer <token>` on all MCP requests
6. **MCP server validates** — JWT signature verified locally against Hydra's JWKS public keys (cached 1 hour)
7. **Backend API access** — MCP server forwards the Bearer token when calling Backend API endpoints

### Token details

| Property | Value |
|---|---|
| Algorithm | RS256 |
| Issuer | `http://localhost:5050` (Traefik entrypoint) |
| Subject | User's database UUID |
| Access token TTL | 15 minutes |
| Refresh token TTL | 720 hours |

## Project Structure

```
plurality-mcp-server/
├── main.py                             # Entry point
├── pyproject.toml                      # Dependencies (managed by uv)
├── .env.example                        # Environment template
├── src/plurality_mcp_server/
│   ├── app.py                          # FastMCP app + middleware stack
│   ├── config.py                       # Env vars, shared HTTP client, context vars
│   ├── auth.py                         # JWT validation via Hydra JWKS
│   ├── oauth.py                        # OAuth metadata + DCR proxy endpoints
│   └── tools.py                        # MCP tool definitions
└── ory-hydra/
    ├── docker-compose.yml              # Hydra + Traefik + PostgreSQL
    ├── hydra.yml                       # Hydra OAuth2/OIDC config
    ├── traefik.yml                     # Traefik static config
    └── dynamic.yml                     # Traefik routing rules
```

## Troubleshooting

**MCP client gets 401 Unauthorized**
- Check that Hydra is running: `curl http://localhost:4444/.well-known/openid-configuration`
- Check that the JWT hasn't expired (15min TTL)
- Verify `HYDRA_ISSUER` matches the issuer in the token's `iss` claim

**MCP client gets 502 Bad Gateway**
- The MCP server isn't running on port 5051
- Check Traefik logs: `docker compose -f ory-hydra/docker-compose.yml logs traefik`

**Tools return "Error: Backend API returned status 401"**
- Backend API needs to accept Hydra JWTs — ensure `jwks-rsa` is installed and the dual auth middleware is deployed

**OAuth flow redirects to localhost:3000 but nothing is there**
- Hydra is configured with `login: http://localhost:3000/login` — this points to the Plurality frontend. Start the frontend or update `hydra.yml` URLs.
