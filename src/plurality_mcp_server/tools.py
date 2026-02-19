from collections import defaultdict
from typing import Optional, List
from plurality_mcp_server.config import http_client, current_token, BACKEND_API_URL


def register_tools(mcp_app):
    """Register all MCP tools on the given FastMCP app instance."""

    @mcp_app.tool()
    async def get_user_memory_buckets() -> str:
        """
        List all memory buckets (AI profiles) for the authenticated user.

        Each bucket is a themed collection of documents, notes, and files.
        Returns bucket IDs, names, and item counts.
        Use this first to discover available memory buckets before browsing
        or searching their contents.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            resp = await http_client.get(
                f"{BACKEND_API_URL}/ai/profile",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            data = resp.json()
            profiles = data if isinstance(data, list) else data.get("profiles", data.get("data", []))

            if not profiles:
                return "No memory buckets found. The user hasn't created any yet."

            result_lines = []
            for p in profiles:
                name = p.get("profileName", p.get("name", "Unnamed"))
                pid = p.get("id", "unknown")
                count = p.get("contextCount", p.get("context_count", 0))
                desc = p.get("description", "")
                line = f"- **{name}** (id: {pid}, {count} items)"
                if desc:
                    line += f"\n  {desc}"
                result_lines.append(line)

            return f"Found {len(profiles)} memory bucket(s):\n\n" + "\n".join(result_lines)

        except Exception as e:
            return f"Error fetching memory buckets: {str(e)}"

    @mcp_app.tool()
    async def list_items_in_memory_bucket(profile_id: str) -> str:
        """
        List all stored items (documents, files, notes) inside a specific memory bucket.

        Returns item metadata including title, description, source type, file name,
        size, and chunk count — but not the actual content.
        Use this to browse what's stored in a bucket before reading or searching
        specific items.

        Args:
            profile_id: The ID of the memory bucket (profile) to list items from.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            resp = await http_client.get(
                f"{BACKEND_API_URL}/ai/context",
                params={"profileId": profile_id},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            data = resp.json()
            contexts = data if isinstance(data, list) else data.get("contexts", data.get("data", []))

            if not contexts:
                return f"No items found in memory bucket {profile_id}."

            result_lines = []
            for c in contexts:
                ctx_id = c.get("contextId", "unknown")
                title = c.get("title", "Untitled")
                description = c.get("description", "")
                source_type = c.get("sourceType", "")
                original_file = c.get("originalFileName", "")
                content_size = c.get("contentSize", 0)
                vector_ids = c.get("vectorIds", [])
                chunk_count = len(vector_ids) if isinstance(vector_ids, list) else 0
                line = f"- **{title}** (contextId: {ctx_id})"
                details = []
                if source_type:
                    details.append(source_type)
                if original_file:
                    details.append(original_file)
                if chunk_count:
                    details.append(f"{chunk_count} chunks")
                if content_size:
                    details.append(f"{content_size} bytes")
                if details:
                    line += f" [{', '.join(details)}]"
                if description:
                    line += f"\n  Summary: {description}"
                result_lines.append(line)

            return f"Found {len(contexts)} item(s) in bucket {profile_id}:\n\n" + "\n".join(result_lines)

        except Exception as e:
            return f"Error listing items: {str(e)}"

    @mcp_app.tool()
    async def search_memory(
        query: str,
        profile_ids: Optional[List[str]] = None,
        k: int = 5,
    ) -> str:
        """
        Search across the user's stored memory using semantic vector similarity.

        Finds relevant content even when the query doesn't exactly match stored text.
        Searches all memory buckets by default, or specify bucket IDs to narrow scope.
        Returns results grouped by memory bucket and context, with content previews
        and relevance scores.
        Use this to discover which contexts contain relevant information,
        then use read_context to get the full content.

        Args:
            query: The search query to find relevant memories.
            profile_ids: Optional list of memory bucket IDs to search within.
                         If not provided, searches across all buckets.
            k: Number of results to return (default: 5).
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            body: dict = {"query": query, "k": k}
            if profile_ids:
                body["profileIds"] = profile_ids

            resp = await http_client.post(
                f"{BACKEND_API_URL}/mcp/semantic-search",
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=120.0,
            )
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            data = resp.json()
            results = data.get("results", [])

            if not results:
                return f"No results found for query: \"{query}\""

            # Group results by profileId → contextId
            buckets: dict = defaultdict(lambda: defaultdict(list))
            for r in results:
                metadata = r.get("metadata", {})
                pid = metadata.get("profileId", "unknown")
                ctx_id = metadata.get("contextId", metadata.get("fileId", "unknown"))
                title = metadata.get("title", "Untitled")
                chunk_index = metadata.get("chunkIndex", "?")
                score = r.get("similarity_score", 0)
                content = r.get("content", "").strip()
                # Truncate long content for preview
                preview = content[:300] + "..." if len(content) > 300 else content

                buckets[pid][(ctx_id, title)].append({
                    "chunk_index": chunk_index,
                    "score": score,
                    "preview": preview,
                })

            # Format grouped output
            output_lines = [f"Search results for \"{query}\" ({len(results)} matches across {len(buckets)} bucket(s)):"]

            for pid, contexts in buckets.items():
                output_lines.append(f"\n## Bucket (profileId: {pid})")
                for (ctx_id, title), chunks in contexts.items():
                    best_score = max(c["score"] for c in chunks)
                    output_lines.append(f"\n### {title} (contextId: {ctx_id}, {len(chunks)} match(es), best score: {best_score:.2f})")
                    for c in sorted(chunks, key=lambda x: x["score"], reverse=True):
                        output_lines.append(f"- [chunk {c['chunk_index']}, score: {c['score']:.2f}]: {c['preview']}")

            return "\n".join(output_lines)

        except Exception as e:
            return f"Error searching memory ({type(e).__name__}): {str(e)}"

    @mcp_app.tool()
    async def read_context(
        context_id: str,
        start_chunk: int = 0,
        limit: int = 0,
    ) -> str:
        """
        Read the full content of a specific stored memory item (document, file, or note).

        Returns the actual text content with pagination support for large documents.
        Use start_chunk and limit to read specific portions — defaults to returning
        all content.
        Use this after finding an item via list_items_in_memory_bucket or search_memory.

        Args:
            context_id: The ID of the context/item to read.
            start_chunk: Chunk index to start reading from (default: 0).
            limit: Maximum number of chunks to return (default: 0 = all chunks).
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            resp = await http_client.get(
                f"{BACKEND_API_URL}/ai/context/{context_id}/content",
                params={
                    "startChunk": start_chunk,
                    "limit": limit,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            data = resp.json()
            title = data.get("title", "Untitled")
            total_chunks = data.get("totalChunks", 0)
            content = data.get("content", "")
            next_chunk = data.get("nextChunk")
            description = data.get("description", "")
            source_type = data.get("sourceType", "")
            original_file = data.get("originalFileName", "")

            # Build header
            header_parts = [f"**{title}**"]
            if original_file:
                header_parts.append(f"(file: {original_file})")
            if source_type:
                header_parts.append(f"[{source_type}]")
            header = " ".join(header_parts)

            meta = f"Chunks: {start_chunk}-{(next_chunk - 1) if next_chunk else total_chunks - 1} of {total_chunks}"
            if description:
                meta += f"\nDescription: {description}"

            result = f"{header}\n{meta}\n\n{content}"

            if next_chunk is not None:
                result += f"\n\n---\n_More content available. Call read_context with start_chunk={next_chunk} to continue._"

            return result

        except Exception as e:
            return f"Error reading context: {str(e)}"
