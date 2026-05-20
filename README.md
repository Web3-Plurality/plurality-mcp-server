# Plurality MCP Server

**Universal memory for AI agents and tools. Save, organize and search context anywhere.**

[![MCP Registry](https://img.shields.io/badge/MCP_Registry-published-brightgreen)](https://registry.modelcontextprotocol.io/?q=ai-context-flow)
[![Smithery](https://img.shields.io/badge/Smithery-listed-blue)](https://smithery.ai/servers/plurality-network/ai-context-flow)
[![Version](https://img.shields.io/github/v/release/Web3-Plurality/plurality-mcp-server)](https://github.com/Web3-Plurality/plurality-mcp-server/releases)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

<!-- mcp-name: network.plurality/ai-context-flow -->

---

An [Model Context Protocol](https://modelcontextprotocol.io/) server that gives any MCP-compatible AI client persistent memory — documents, notes, conversations, and files stored across organized memory buckets with semantic search. Supports OAuth and Personal Access Tokens (PAT) based authentication.

## Why use this?

- **Memory Studio** — a personal knowledge base and second brain where you organize context into memory buckets, review saved content, and manage what your AI agents know.
- **Save important chats** — directly from within Claude, ChatGPT, Cursor, and other AI tools into the relevant memory bucket
- **Shared persistent memory** across all your agents — Claude Code, Lovable, Cursor, OpenClaw and more all access the same context
- **Semantic search** — find relevant memories using natural language, not just keywords
- **Share buckets with other people** — collaborate by giving others access to your context
- **Works everywhere** — any MCP-compatible client can connect

We also have a [Chrome extension](https://chromewebstore.google.com/detail/ai-context-flow-use-your/cfegfckldnmbdnimjgfamhjnmjpcmgnf) called AI Context Flow that lets you capture and use context on any website. It comes with a built-in chat agent (30+ models) that opens as a sidebar on any page — talk to your memories, answer questions from your stored context, and surface connections across everything you've saved. You can use the MCP Server alongside the extension to get full feature set of the product. 

<div align="center">
  <a href="https://www.youtube.com/watch?v=0bC4-hzUiks">
    <img src="https://img.youtube.com/vi/0bC4-hzUiks/sddefault.jpg" alt="Watch the demo" width="600">
  </a>
  <p><em>▶️ Watch the demo</em></p>
</div>


📖 [Full documentation](https://docs.plurality.network/the-plurality-mcp-server) · 🌐 [Website](https://plurality.network/ai-context-flow) · 🧩 [Chrome Extension](https://chromewebstore.google.com/detail/ai-context-flow-use-your/cfegfckldnmbdnimjgfamhjnmjpcmgnf) · 🧠 [Memory Studio](https://app.plurality.network)

---

## Quick connect (production)

Production URL: `https://app.plurality.network/mcp`

The server supports **OAuth 2.1 with PKCE** (for interactive clients) and **Personal Access Tokens** (for headless agents and CI). Most clients handle the OAuth flow automatically.

Works with Claude Desktop, Claude Code, ChatGPT, Cursor, and any MCP-compatible client.

→ **[Step-by-step setup guides for all supported clients](https://docs.plurality.network/the-plurality-mcp-server/connect-your-agents-via-mcp)**

---

## Tools

| Tool | Description |
|------|-------------|
| `get_user_memory_buckets` | List all memory buckets (organized folders) for the user |
| `list_items_in_memory_bucket` | List stored items in a specific bucket (metadata only) |
| `search_memory` | Semantic search across buckets with relevance scoring |
| `read_context` | Read the full content of a stored item with pagination |
| `save_memory` | Save text content to a specific memory bucket |
| `save_conversation` | Save a conversation (chat history) to a memory bucket |
| `create_memory_bucket` | Create a new memory bucket for organizing saved content |

---

## Authentication

The MCP server accepts two auth methods:

| Method | When to use | Browser required? |
|--------|-------------|-------------------|
| **OAuth 2.1 + PKCE** | Interactive clients: Claude Desktop, Web, Code, ChatGPT | Yes (one-time) |
| **Personal Access Token (PAT)** | Headless agents, CI runners, custom integrations, n8n, LangChain | No |

### Using a Personal Access Token

1. Sign in to the [dashboard](https://app.plurality.network), open **Connect via MCP → Manage tokens**
2. Click **Create token**, give it a name and optional expiry, copy the `plur_pat_…` value
3. Configure your client to send it as a Bearer token:

```
Authorization: Bearer plur_pat_...
```

PATs require a **paid plan**. They auto-revoke at expiry, can be rotated with a configurable grace period (default 7 days), and can be immediately revoked from the dashboard. They are stored hashed and never appear in logs.

### OAuth details

The server uses [Ory Hydra](https://www.ory.sh/hydra/) as the OAuth2/OIDC provider:

| Property | Value |
|----------|-------|
| Algorithm | RS256 |
| Issuer | `https://app.plurality.network` (prod) |
| Scope | `openid offline_access mcp:tools` |
| Access token TTL | 15 minutes |
| Refresh token TTL | 720 hours |
| Discovery | `https://app.plurality.network/.well-known/oauth-authorization-server` |
| Dynamic Client Registration | `https://app.plurality.network/register` |

<details>
<summary><strong>OAuth2 flow (step by step)</strong></summary>

1. **Client discovers auth server** — fetches `/.well-known/oauth-protected-resource` from Traefik
2. **Client registers** — calls `/register` (Dynamic Client Registration) to get `client_id`/`client_secret`. The API Gateway proxies this to Hydra, injecting the `mcp:tools` scope
3. **User authenticates** — browser opens Hydra's login flow, which redirects to the frontend's login/consent pages
4. **Token issued** — Hydra returns a JWT access token (RS256, 15min TTL) with the user's ID as the `sub` claim and `mcp:tools` scope
5. **Authenticated requests** — client includes `Authorization: Bearer <token>` on all MCP requests
6. **MCP server validates** — JWT signature verified locally against Hydra's JWKS public keys (cached 1 hour), `mcp:tools` scope is checked
7. **API Gateway access** — MCP server forwards the Bearer token when calling API Gateway endpoints for data retrieval and storage

</details>

---

## Architecture

```
MCP Client (Claude Code, Cursor, etc.)
    │
    │  OAuth2 + Streamable HTTP
    ▼
Traefik (:5050)           ← single entrypoint for clients
    ├── /mcp              → MCP Server (:5051)
    ├── /.well-known/*    → API Gateway or Hydra
    ├── /oauth2/*         → Hydra (:4444)
    └── /register         → API Gateway (DCR proxy)
                                │
                                │ Bearer token
                                ▼
                          API Gateway (:5000)
                                │
                                ▼
                          Vector Service (:8000)
```

**Traefik** is the single entrypoint. It routes OAuth traffic to Hydra and MCP protocol traffic to the MCP server. The MCP server validates JWTs locally via Hydra's JWKS keys, then forwards the Bearer token to the API Gateway for data access. The API Gateway handles authentication, DCR proxying, and routes vector/search operations to the Vector Service.

---

## Local development setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Docker and Docker Compose
- Running **API Gateway** (plurality-backend-api): Handles authentication, OAuth metadata, DCR proxying, and database access
- Running **Vector Service** (plurality-ai-service): Handles semantic search and vector database operations

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

Default values work for local development — no changes needed if the API Gateway runs on `:5000`:

```
HYDRA_ISSUER=http://localhost:5050
MCP_RESOURCE_URL=http://localhost:5050
BACKEND_API_URL=http://localhost:5000
```

### 3. Start Docker services (Hydra + Traefik)

```bash
cd ory-hydra
docker compose up -d
```

This starts:

| Service | Port | Purpose |
|---------|------|---------|
| **PostgreSQL** | 5433 | Hydra's database |
| **Hydra** | 4444, 4445 | OAuth2/OIDC provider (public + admin) |
| **Traefik** | 5050 | Reverse proxy / routing |

Wait for services to be healthy: `docker compose ps`

### 4. Start the MCP server

```bash
uv run uvicorn main:mcp_server --host 0.0.0.0 --port 5051 --reload
```

> **Port 5051**, not 5050. Traefik listens on 5050 and proxies `/mcp` to the MCP server on 5051.

### 5. Verify

```bash
# Health check (direct)
curl http://localhost:5051/mcp/health

# OAuth metadata (via Traefik)
curl http://localhost:5050/.well-known/oauth-protected-resource

# Traefik dashboard (for debugging routes)
open http://localhost:8080
```

### Local client configuration

<details>
<summary><strong>Claude Code</strong></summary>

```bash
claude mcp add --transport http plurality-memory http://localhost:5050/mcp
```

Then authenticate via `/mcp` inside Claude Code.

</details>

<details>
<summary><strong>Claude Code — VS Code Extension</strong></summary>

Authenticate first in the terminal using the steps above. Then add `.mcp.json` to your project root:

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

> The VS Code extension may not trigger the OAuth browser flow automatically. Complete authentication via the terminal first.

</details>

<details>
<summary><strong>Claude Desktop</strong></summary>

Edit your config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

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

Restart Claude Desktop fully, then authenticate when the browser opens.

</details>

<details>
<summary><strong>MCP Inspector (debugging)</strong></summary>

```bash
npx @modelcontextprotocol/inspector
```

Enter `http://localhost:5050/mcp` as the server URL. The inspector walks through the OAuth flow and lets you call tools interactively.

</details>

> **Note:** ChatGPT is not supported for local development — it requires publicly reachable OAuth endpoints.

---

## Project structure

```
plurality-mcp-server/
├── main.py                             # Entry point
├── pyproject.toml                      # Dependencies (managed by uv)
├── .env.example                        # Environment template
├── src/plurality_mcp_server/
│   ├── app.py                          # FastMCP app + middleware stack
│   ├── config.py                       # Env vars, shared HTTP client, context vars
│   ├── auth.py                         # JWT validation via Hydra JWKS + scope check
│   └── tools.py                        # MCP tool definitions (read + write)
└── ory-hydra/
    ├── docker-compose.yml              # Hydra + Traefik + PostgreSQL
    ├── hydra.yml                       # Hydra OAuth2/OIDC config
    ├── traefik.yml                     # Traefik static config
    └── dynamic.yml                     # Traefik routing rules
```

---

## Troubleshooting

<details>
<summary><strong>MCP client gets 401 Unauthorized</strong></summary>

- Check that Hydra is running: `curl http://localhost:4444/.well-known/openid-configuration`
- Check that the JWT hasn't expired (15min TTL)
- Verify `HYDRA_ISSUER` matches the issuer in the token's `iss` claim
- Ensure the token has the `mcp:tools` scope

</details>

<details>
<summary><strong>MCP client gets 502 Bad Gateway</strong></summary>

- The MCP server isn't running on port 5051
- Check Traefik logs: `docker compose -f ory-hydra/docker-compose.yml logs traefik`

</details>

<details>
<summary><strong>Tools return "Error: Backend API returned status 401"</strong></summary>

- The API Gateway needs to accept Hydra JWTs — ensure `jwks-rsa` is installed and the OAuth auth middleware is deployed

</details>

<details>
<summary><strong>OAuth flow redirects to localhost:3000 but nothing is there</strong></summary>

- Hydra is configured with `login: http://localhost:3000/login` — this points to the Plurality frontend. Start the frontend or update `hydra.yml` URLs.

</details>

<details>
<summary><strong>DCR returns unexpected scope or missing mcp:tools</strong></summary>

- The API Gateway's DCR proxy injects `mcp:tools` into the allowed scopes. Ensure the API Gateway is running and the `/register` route is reachable.

</details>

---

## Links

- 📖 [Documentation](https://docs.plurality.network/the-plurality-mcp-server)
- 🌐 [Website](https://plurality.network)
- 🧩 [Chrome Extension](https://chromewebstore.google.com/detail/ai-context-flow-use-your/cfegfckldnmbdnimjgfamhjnmjpcmgnf)
- 🧠 [Memory Studio](https://app.plurality.network)
- 📦 [MCP Registry](https://registry.modelcontextprotocol.io/?q=ai-context-flow)
- 🔧 [Smithery](https://smithery.ai/servers/plurality-network/ai-context-flow)

---
