# Plurality MCP Server

A Model Context Protocol (MCP) server that provides web search capabilities and URL content extraction to LLMs using DuckDuckGo search and WebBaseLoader.



## Prerequisites

- Python 3.11 or higher
- uv (Ultra-fast Python package installer)

## Installation

1. **Clone or navigate to the project directory:**
   ```bash
   cd plurality-mcp
   ```
2. **Install  uv:**
   ```bash
   pip install uv
   ```
3. **Install dependencies using uv:**
   ```bash
   uv sync
   ```
4. **Activate the virtual environment:**
   
   **On macOS/Linux:**
   ```bash
   source .venv/bin/activate
   ```
   
   **On Windows:**
   ```bash
   .venv\Scripts\activate
   ```

## Running the Server

### Method 1: Using Uvicorn (Recommended)

```bash
uv run uvicorn main:mcp_server  --port 5050 --reload
```

### Run Inspector
```bash
npx @modelcontextprotocol/inspector
```

