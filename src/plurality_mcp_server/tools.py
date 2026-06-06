from collections import defaultdict
from datetime import datetime
from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from plurality_mcp_server.config import http_client, current_token, BACKEND_API_URL


class ChatMessage(BaseModel):
    """A single message in a conversation."""
    role: Literal["user", "assistant"] = Field(description="The role of the message sender")
    content: str = Field(description="The text content of the message")


def register_tools(mcp_app):
    """Register all MCP tools on the given FastMCP app instance."""

    def _format_bucket_item_line(item: dict, indent: str = "  ") -> str:
        title = item.get("title") or "Untitled"
        ctx_id = item.get("contextId") or "?"
        line = f"{indent}- **{title}** (contextId: {ctx_id})"
        details = []
        if item.get("sourceType"):
            details.append(item["sourceType"])
        if item.get("originalFileName"):
            details.append(item["originalFileName"])
        if details:
            line += f" [{', '.join(details)}]"
        updated = item.get("updatedAt")
        if updated:
            line += f"\n{indent}  updatedAt: {updated}"
        deleted = item.get("deletedAt")
        if deleted:
            line += f"\n{indent}  deletedAt: {deleted}"
        desc = item.get("description")
        if desc:
            line += f"\n{indent}  {desc[:150]}"
        return line

    @mcp_app.tool()
    async def get_user_memory_buckets(include_trashed: bool = False) -> str:
        """
        List memory buckets (AI profiles) for the authenticated user with the
        items inside each one in a single round-trip.

        Default mode shows ACTIVE items per bucket (title, contextId, updatedAt).
        Trash mode (include_trashed=True) shows ONLY trashed items per bucket
        with their deletedAt timestamps — use this when the user asks about
        items they've deleted, then chain into restore_context.

        Use this as the primary discovery tool when the user names an item by
        title: every contextId and updatedAt token a mutation needs is already
        in the output, so you can go straight from here to update_context_metadata
        / delete_context / replace_context_content without a separate
        list_items_in_memory_bucket round trip.

        Returns both owned buckets and buckets shared with the user. Each
        bucket has a `role` ('owner', 'editor', or 'viewer'); shared buckets
        also include the owner's name/email.

        Args:
            include_trashed: When True, return only trashed items under each
                bucket (with deletedAt). When False (default), return only
                active items.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            params = {"includeTrashed": "true"} if include_trashed else None
            resp = await http_client.get(
                f"{BACKEND_API_URL}/mcp/get-profiles-with-items",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            data = resp.json() or {}
            owned = data.get("owned") or []
            shared = data.get("shared") or []

            if not owned and not shared:
                return "No memory buckets found. The user hasn't created any yet."

            item_label = "trashed item" if include_trashed else "item"

            def render_bucket(b: dict, header_extra: str = "") -> str:
                header = f"- **{b.get('name', 'Untitled')}** (id: {b.get('id', '?')}, {b.get('item_count', 0)} {item_label}s)"
                if header_extra:
                    header += f" {header_extra}"
                items = b.get("items") or []
                if not items:
                    return header + ("\n  (no trashed items)" if include_trashed else "\n  (empty)")
                item_lines = "\n".join(_format_bucket_item_line(it) for it in items)
                return header + "\n" + item_lines

            parts = []
            if owned:
                parts.append(
                    f"**Your buckets ({len(owned)}):**\n\n"
                    + "\n\n".join(render_bucket(b) for b in owned)
                )
            if shared:
                rendered = []
                for b in shared:
                    shared_by = b.get("owner_name") or b.get("owner_email") or "unknown"
                    role = b.get("role", "?")
                    rendered.append(
                        render_bucket(b, header_extra=f"[role: {role}, shared by {shared_by}]")
                    )
                parts.append(
                    f"**Shared with you ({len(shared)}):**\n\n" + "\n\n".join(rendered)
                )

            total = len(owned) + len(shared)
            header_mode = "memory bucket(s) (TRASH view)" if include_trashed else "memory bucket(s)"
            return f"Found {total} {header_mode}:\n\n" + "\n\n".join(parts)

        except Exception as e:
            return f"Error fetching memory buckets: {str(e)}"

    @mcp_app.tool()
    async def list_items_in_memory_bucket(
        profile_id: str,
        include_trashed: bool = False,
    ) -> str:
        """
        List stored items (documents, files, notes) inside a specific memory
        bucket.

        Default mode returns ALL non-trashed items with metadata: title,
        description, source type, file name, size, chunk count, edit
        permission — but not the actual content.

        Trash mode (include_trashed=True) returns ONLY trashed items in that
        bucket with their deletedAt timestamps. Use this when the user asks
        about something they deleted, before calling restore_context.

        Args:
            profile_id: The ID of the memory bucket (profile) to list items from.
            include_trashed: When True, return only trashed items (with
                deletedAt). When False (default), return only active items.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            params: dict = {"profileId": profile_id}
            if include_trashed:
                params["includeDeleted"] = "true"
            resp = await http_client.get(
                f"{BACKEND_API_URL}/ai/context",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            data = resp.json()
            contexts = data if isinstance(data, list) else data.get("contexts", data.get("data", []))

            if not contexts:
                label = "trashed items" if include_trashed else "items"
                return f"No {label} found in memory bucket {profile_id}."

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
                can_edit = c.get("canEdit")
                deleted_at = c.get("deletedAt")
                updated_at = c.get("updatedAt")
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
                if can_edit is not None:
                    details.append("editable" if can_edit else "read-only")
                if details:
                    line += f" [{', '.join(details)}]"
                if updated_at and not include_trashed:
                    line += f"\n  updatedAt: {updated_at}"
                if deleted_at:
                    line += f"\n  deletedAt: {deleted_at}"
                if description:
                    line += f"\n  Summary: {description}"
                result_lines.append(line)

            label = "trashed item(s)" if include_trashed else "item(s)"
            return f"Found {len(contexts)} {label} in bucket {profile_id}:\n\n" + "\n".join(result_lines)

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
        Searches across both owned and shared buckets automatically.
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
            updated_at = data.get("updatedAt")
            deleted_at = data.get("deletedAt")

            # Build header
            header_parts = [f"**{title}**"]
            if original_file:
                header_parts.append(f"(file: {original_file})")
            if source_type:
                header_parts.append(f"[{source_type}]")
            header = " ".join(header_parts)

            meta_lines = [
                f"Chunks: {start_chunk}-{(next_chunk - 1) if next_chunk else total_chunks - 1} of {total_chunks}",
            ]
            if description:
                meta_lines.append(f"Description: {description}")
            # Surface updatedAt so mutation tools can pass it back as expected_updated_at.
            if updated_at:
                meta_lines.append(f"updatedAt: {updated_at}")
            if deleted_at:
                meta_lines.append(
                    f"⚠️ This item is in the trash (deletedAt: {deleted_at}). "
                    f"It must be restored via the UI before it can be edited."
                )
            meta = "\n".join(meta_lines)

            result = f"{header}\n{meta}\n\n{content}"

            if next_chunk is not None:
                result += f"\n\n---\n_More content available. Call read_context with start_chunk={next_chunk} to continue._"

            return result

        except Exception as e:
            return f"Error reading context: {str(e)}"

    @mcp_app.tool()
    async def save_memory(
        profile_id: str,
        content: str,
        title: Optional[str] = None,
        source_platform: str = "unknown",
    ) -> str:
        """
        Save text content to a specific memory bucket.

        IMPORTANT — Before calling this tool:
        1. Call get_user_memory_buckets to list available buckets
        2. Ask the user which bucket to save to, or offer to create a new one
        3. If the user wants a new bucket, call create_memory_bucket first

        Args:
            profile_id: The ID of the memory bucket to save to (required).
            content: The text content to save.
            title: Optional custom title for this memory. If not provided, a title
                   is generated automatically from the content.
            source_platform: The name of this MCP client platform
                             (e.g. "claude", "chatgpt", "cursor").
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        # Route by content size — same threshold as the first-party UI and as
        # replace_context_content. ≤ 25 KB goes inline via /add-raw-context;
        # larger payloads go straight to /upload-file (async) so the MCP call
        # doesn't sit on a 20-30s sync embed and risk the client tool-call
        # timeout. The backend's sync ceiling is 500 KB, but we don't want to
        # rely on the 413 fallback below to handle the 25-500 KB band.
        content_bytes = content.encode("utf-8")
        SMALL_ADD_MAX_BYTES = 25_000

        async def upload_as_file(reason: str) -> str:
            ts = datetime.now().strftime("%Y-%m-%d %H-%M")
            filename = f"{title}.txt" if title else f"MCP saved text - {ts}.txt"
            files = {"file": (filename, content_bytes, "text/plain")}
            form = {"profileId": profile_id}
            upload_resp = await http_client.post(
                f"{BACKEND_API_URL}/ai/context/upload-file",
                files=files,
                data=form,
                headers={"Authorization": f"Bearer {token}"},
                timeout=120.0,
            )
            if upload_resp.status_code == 400:
                err = upload_resp.json().get("error", upload_resp.text) if upload_resp.headers.get("content-type", "").startswith("application/json") else upload_resp.text
                return (
                    f"Error: Content is too large even as a file ({err}). "
                    f"Split the content into smaller pieces and retry."
                )
            if upload_resp.status_code != 200:
                return f"Error: Backend API returned status {upload_resp.status_code}: {upload_resp.text}"
            data = upload_resp.json()
            ctx_id = data.get("contextId", "unknown")
            final_title = data.get("title", filename)
            final_desc = data.get("description", "")
            result = (
                f"Memory saved as a file ({reason}).\n\n"
                f"- **Title:** {final_title}\n"
                f"- **Filename:** {filename}\n"
                f"- **Context ID:** {ctx_id}\n"
                f"- **Bucket ID:** {profile_id}\n"
                f"- **Status:** Processing in background; embeddings will be searchable shortly."
            )
            if final_desc:
                result += f"\n- **Description:** {final_desc}"
            return result

        try:
            # Big payload: skip the sync path entirely.
            if len(content_bytes) > SMALL_ADD_MAX_BYTES:
                return await upload_as_file(
                    f"content is {len(content_bytes) // 1024} KB, "
                    f"above the {SMALL_ADD_MAX_BYTES // 1024} KB inline threshold"
                )

            body = {
                "profileId": profile_id,
                "context": content,
                "sourcePlatform": f"mcp-{source_platform}",
            }
            if title:
                body["title"] = title

            resp = await http_client.post(
                f"{BACKEND_API_URL}/ai/context/add-raw-context",
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=120.0,
            )
            if resp.status_code == 413:
                # Belt-and-suspenders: in case the backend's raw-text ceiling
                # is ever tuned below our 25 KB pre-check, still fall back.
                return await upload_as_file("content exceeded the raw-text size limit")
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            data = resp.json()
            title = data.get("title", "Untitled")
            ctx_id = data.get("contextId", "unknown")
            desc = data.get("description", "")
            pid = data.get("profileId", profile_id)

            result = f"Memory saved successfully!\n\n"
            result += f"- **Title:** {title}\n"
            result += f"- **Context ID:** {ctx_id}\n"
            result += f"- **Bucket ID:** {pid}\n"
            if desc:
                result += f"- **Description:** {desc}\n"

            return result

        except Exception as e:
            return f"Error saving memory: {str(e)}"

    @mcp_app.tool()
    async def save_conversation(
        profile_id: str,
        chat_history: List[ChatMessage],
        title: Optional[str] = None,
        source_platform: str = "unknown",
    ) -> str:
        """
        Save a conversation (chat history) to a specific memory bucket.

        IMPORTANT — Before calling this tool:
        1. Call get_user_memory_buckets to list available buckets
        2. Ask the user which bucket to save to, or offer to create a new one
        3. If the user wants a new bucket, call create_memory_bucket first

        Args:
            profile_id: The ID of the memory bucket to save to (required).
            chat_history: List of chat messages, each with 'role' ("user" or "assistant")
                          and 'content' (the message text).
            title: Optional custom title for this conversation. If not provided, a title
                   is generated automatically from the conversation content.
            source_platform: The name of this MCP client platform
                             (e.g. "claude", "chatgpt", "cursor").
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            body = {
                "profileId": profile_id,
                "chatHistory": [msg.model_dump() for msg in chat_history],
                "platform": f"mcp-{source_platform}",
            }
            if title:
                body["title"] = title

            resp = await http_client.post(
                f"{BACKEND_API_URL}/ai/context/add-chat-context",
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
            title = data.get("title", "Untitled")
            ctx_id = data.get("contextId", "unknown")
            desc = data.get("description", "")
            pid = data.get("profileId", profile_id)
            msg_count = len(chat_history)

            result = f"Conversation saved successfully!\n\n"
            result += f"- **Title:** {title}\n"
            result += f"- **Context ID:** {ctx_id}\n"
            result += f"- **Bucket ID:** {pid}\n"
            result += f"- **Messages:** {msg_count}\n"
            if desc:
                result += f"- **Description:** {desc}\n"

            return result

        except Exception as e:
            return f"Error saving conversation: {str(e)}"

    @mcp_app.tool()
    async def create_memory_bucket(
        bucket_name: str,
    ) -> str:
        """
        Create a new memory bucket (AI profile) for organizing saved content.

        Only use this when the user explicitly wants a new bucket.
        Always ask the user for confirmation before creating.

        Args:
            bucket_name: A descriptive name for the new bucket.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            resp = await http_client.post(
                f"{BACKEND_API_URL}/ai/profile",
                json={"profileName": bucket_name},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code not in (200, 201):
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            data = resp.json()
            pid = data.get("id", "unknown")
            name = data.get("profileName", bucket_name)

            return f"Memory bucket created successfully!\n\n- **Name:** {name}\n- **Bucket ID:** {pid}\n\nYou can now use this ID with save_memory or save_conversation."

        except Exception as e:
            return f"Error creating memory bucket: {str(e)}"

    @mcp_app.tool()
    async def update_context_metadata(
        context_id: str,
        expected_updated_at: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> str:
        """
        Update only the title and/or description of a context item.
        Does NOT modify content. Free, instant. To change content, you would need
        to overwrite the item by reading it, modifying the text, and saving the
        result back (no replace tool exists yet in v1).

        BEFORE calling this tool, call read_context(context_id) to fetch the
        current state and updatedAt. Do not rely on data from earlier in this
        conversation — items may have been edited or deleted since. Pass the
        fresh updatedAt value as expected_updated_at. If the item is in the
        trash (deletedAt is set in the read_context output), it must be
        restored via the UI before it can be edited.

        Args:
            context_id: The UUID of the context item to update.
            expected_updated_at: The updatedAt timestamp from the most recent
                read_context call for this item. Used to detect concurrent edits;
                if stale, the call returns a 409 error and you must re-read.
            title: New title (optional). Must be a non-empty string if provided.
            description: New description (optional). Empty string clears it.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        if title is None and description is None:
            return "Error: Provide at least one of title or description to update."

        body: dict = {"expectedUpdatedAt": expected_updated_at}
        if title is not None:
            body["title"] = title
        if description is not None:
            body["description"] = description

        try:
            resp = await http_client.patch(
                f"{BACKEND_API_URL}/ai/context/{context_id}",
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

            if resp.status_code == 409:
                detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                current = detail.get("currentUpdatedAt", "unknown")
                return (
                    f"Conflict: this item was edited by someone else after your last read.\n"
                    f"Current updatedAt is {current}. Call read_context({context_id!r}) again "
                    f"to see the latest state, then retry with the fresh updatedAt."
                )
            if resp.status_code == 403:
                return "Error: You do not have permission to edit this context item."
            if resp.status_code == 404:
                return f"Error: Context {context_id} not found."
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            data = resp.json()
            if data.get("noop"):
                return "No changes applied — title and description already match the provided values."

            ctx = data.get("context", {})
            new_title = ctx.get("title", "")
            new_desc = ctx.get("description", "")
            new_updated = ctx.get("updatedAt", "")

            lines = [f"Updated context {context_id}:"]
            if title is not None:
                lines.append(f"- title: {new_title!r}")
            if description is not None:
                lines.append(f"- description: {new_desc!r}")
            lines.append(f"- updatedAt: {new_updated}")
            return "\n".join(lines)

        except Exception as e:
            return f"Error updating context metadata ({type(e).__name__}): {str(e)}"

    @mcp_app.tool()
    async def delete_context(
        context_id: str,
        expected_updated_at: str,
    ) -> str:
        """
        Soft-delete a context item (move it to trash). Recoverable from the
        trash for 30 days via the UI. After 30 days the item is permanently
        removed along with its vector embeddings.

        Soft-delete means the item disappears from search and list results
        immediately, but the user (or you on their behalf, via restore_context)
        can restore it from trash within the plan-tier window.

        BEFORE calling this tool, call read_context(context_id) to fetch the
        current state and updatedAt. Do not rely on data from earlier in this
        conversation — items may have been edited since. Pass the fresh
        updatedAt as expected_updated_at.

        Args:
            context_id: The UUID of the context item to delete.
            expected_updated_at: The updatedAt timestamp from the most recent
                read_context call for this item. If stale, the call returns
                a 409 error and you must re-read.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            resp = await http_client.request(
                "DELETE",
                f"{BACKEND_API_URL}/ai/context/{context_id}",
                json={"expectedUpdatedAt": expected_updated_at},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

            if resp.status_code == 409:
                detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                current = detail.get("currentUpdatedAt", "unknown")
                return (
                    f"Conflict: this item was edited by someone else after your last read.\n"
                    f"Current updatedAt is {current}. Call read_context({context_id!r}) again to "
                    f"see the latest state, then retry with the fresh updatedAt."
                )
            if resp.status_code == 403:
                return "Error: You do not have permission to delete this context item."
            if resp.status_code == 404:
                return f"Error: Context {context_id} not found (or already deleted)."
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            return (
                f"Context {context_id} moved to trash. The user can restore it "
                f"from the UI — or you can restore it via restore_context — "
                f"within the plan's restore window."
            )

        except Exception as e:
            return f"Error deleting context ({type(e).__name__}): {str(e)}"

    @mcp_app.tool()
    async def restore_context(context_id: str) -> str:
        """
        Restore a previously deleted (trashed) item back to its bucket.

        Works only on items currently in the trash. For active items use the
        metadata or content tools instead.

        Restore eligibility is gated by the bucket owner's plan tier (free
        plans have a 0-day window — only the owner can restore by upgrading;
        plus = 30 days; pro = 90 days). The tool returns a clear "outside
        restore window" message with the required plan if the item is too old.

        Discover trashed items first by calling get_user_memory_buckets
        (include_trashed=True) or list_items_in_memory_bucket
        (include_trashed=True) to grab the contextId.

        Args:
            context_id: The UUID of the trashed context item to restore.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            resp = await http_client.post(
                f"{BACKEND_API_URL}/ai/context/{context_id}/restore",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

            if resp.status_code == 403:
                detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                err = (detail.get("error") or "")
                if err == "OUTSIDE_RESTORE_WINDOW":
                    age = detail.get("ageDays", "?")
                    window = detail.get("windowDays", "?")
                    still = detail.get("stillRecoverableForDays")
                    min_plan = detail.get("minPlanRequired")
                    msg = (
                        f"Cannot restore — this item is {age} days old, but the current plan's "
                        f"restore window is {window} days."
                    )
                    if still and still > 0 and min_plan:
                        msg += f" Upgrading to {min_plan} would still allow recovery for {still} more day(s)."
                    else:
                        msg += " Upgrading the plan would no longer recover it."
                    return msg
                return "Error: You do not have permission to restore this context item."
            if resp.status_code == 404:
                return f"Error: Context {context_id} not found."
            if resp.status_code == 400:
                detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                err = (detail.get("error") or "")
                if "not in the trash" in err.lower():
                    return f"Context {context_id} is not in the trash — nothing to restore."
                return f"Error: {err or 'invalid request'}"
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            data = resp.json()
            title = (data.get("context") or {}).get("title") or "(untitled)"
            return f"Restored \"{title}\" (context {context_id}) from trash."

        except Exception as e:
            return f"Error restoring context ({type(e).__name__}): {str(e)}"

    @mcp_app.tool()
    async def replace_context_content(
        context_id: str,
        content: str,
        expected_updated_at: str,
    ) -> str:
        """
        REPLACE the entire content of a context item with new text.
        This re-embeds all chunks and overwrites the existing content.

        Prior content is automatically saved to version history and is
        recoverable from the UI for 30 days.

        To ADD to existing content (rather than replace), first call
        read_context to get the current content, then call this with
        the combined text (old + new).

        Re-embedding charges credits proportional to the new content size.
        On embedding failure, no credits are charged and the original
        content remains intact.

        BEFORE calling this tool, call read_context(context_id) to fetch
        the current state and updatedAt. Do not rely on data from earlier
        in this conversation — items may have been edited or deleted
        since. Pass the fresh updatedAt value as expected_updated_at.

        Args:
            context_id: The UUID of the context item to update.
            content: The full new content (replaces existing entirely).
            expected_updated_at: The updatedAt timestamp from the most recent
                read_context call for this item. If stale, the call returns
                a 409 error and you must re-read.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        if not content or not content.strip():
            return "Error: content cannot be empty"

        # Route by content size: small edits land on the synchronous /replace-raw-context
        # (returns when embedding completes). Larger payloads upload via the async
        # /file-replace path, which returns 202 immediately and embeds via the worker.
        #
        # 25 KB matches the first-party UI threshold. The backend's sync ceiling
        # is 500 KB, but a sync embed at that size runs 30s+ end-to-end and
        # blows past most MCP client tool-call timeouts. Anything bigger goes
        # async and the worker handles it — the model gets an immediate 202
        # acknowledgement instead of a dropped request.
        content_bytes = content.encode("utf-8")
        SMALL_EDIT_MAX_BYTES = 25_000

        async def file_replace():
            files = {"file": ("memory.txt", content_bytes, "text/plain")}
            data = {"lastSeenUpdatedAt": expected_updated_at}
            return await http_client.post(
                f"{BACKEND_API_URL}/ai/context/{context_id}/file-replace",
                files=files,
                data=data,
                headers={"Authorization": f"Bearer {token}"},
                timeout=180.0,
            )

        try:
            if len(content_bytes) <= SMALL_EDIT_MAX_BYTES:
                resp = await http_client.post(
                    f"{BACKEND_API_URL}/ai/context/{context_id}/replace-raw-context",
                    json={"content": content, "lastSeenUpdatedAt": expected_updated_at},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    timeout=180.0,
                )
                # Belt-and-suspenders: if the backend's raw-text ceiling is ever
                # tuned below our 25 KB pre-check, silently retry via the async
                # path instead of bouncing the call back to the model. Matches
                # the 413 fallback in save_memory.
                if resp.status_code == 413:
                    resp = await file_replace()
            else:
                resp = await file_replace()

            if resp.status_code == 409:
                detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                err = detail.get("error", "")
                if err.startswith("Context is in the trash"):
                    return (
                        f"Cannot replace: context {context_id} is in the trash. "
                        f"Ask the user to restore it from the UI before editing."
                    )
                if err.startswith("Embedding in progress"):
                    return (
                        "A previous save for this item is still being embedded. "
                        "Wait a few seconds and retry."
                    )
                current = detail.get("currentUpdatedAt", "unknown")
                return (
                    f"Conflict: this item was edited by someone else after your last read.\n"
                    f"Current updatedAt is {current}. Call read_context({context_id!r}) again to "
                    f"see the latest state, then retry with the fresh updatedAt."
                )
            if resp.status_code == 403:
                return "Error: You do not have permission to edit this context item."
            if resp.status_code == 404:
                return f"Error: Context {context_id} not found."
            if resp.status_code == 413:
                return "Error: Content exceeds the maximum size limit. Try splitting it into multiple items."
            if resp.status_code == 502:
                return "Error: Embedding service failed. The original content was restored (auto-rollback)."
            if resp.status_code not in (200, 202):
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            data = resp.json()

            # Async path (file-replace, 202): backend returned immediately;
            # worker will embed in the background.
            if resp.status_code == 202:
                new_version_id = data.get("currentVersionId")
                new_title = data.get("title", "")
                file_size = data.get("fileSize", "?")
                return (
                    f"Replacement queued for context {context_id}:\n"
                    f"- title: {new_title!r}\n"
                    f"- size: {file_size} bytes\n"
                    f"- newVersionId: {new_version_id}\n"
                    f"- status: queued (the embedding worker will process this in the background)"
                )

            # Sync path (replace-raw-context, 200)
            ctx = data.get("context", {})
            prior_version_id = data.get("priorVersionId")
            new_version_id = data.get("newVersionId")
            new_title = ctx.get("title", "")
            new_size = ctx.get("contentSize", "?")
            new_updated = ctx.get("updatedAt", "")

            lines = [f"Replaced content for context {context_id}:"]
            lines.append(f"- title: {new_title!r}")
            lines.append(f"- contentSize: {new_size} bytes")
            lines.append(f"- updatedAt: {new_updated}")
            if prior_version_id:
                lines.append(f"- prior version archived as {prior_version_id} (recoverable from UI within your plan's restore window)")
            if new_version_id:
                lines.append(f"- newVersionId: {new_version_id}")
            return "\n".join(lines)

        except Exception as e:
            return f"Error replacing context content ({type(e).__name__}): {str(e)}"

    # ── PHASE 2: VERSION & DISCOVERY READS ──────────────────────────

    @mcp_app.tool()
    async def get_context_versions(context_id: str) -> str:
        """
        List the version history of a saved item.

        Returns each version's id, ordinal versionNum, edit timestamp, who
        edited it, title/description at that point, and whether it can be
        restored right now under the bucket owner's plan (with the upgrade
        plan needed when it's outside the current window).

        Use this when the user asks "what did this look like before", "who
        changed this", or wants to roll back. Pair with read_context_version
        to fetch the content of one snapshot, and restore_context_to_version
        to promote a historical version back to current.

        Args:
            context_id: The UUID of the saved item whose history to fetch.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            resp = await http_client.get(
                f"{BACKEND_API_URL}/ai/context/{context_id}/versions",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 404:
                return f"Error: Context {context_id} not found."
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            data = resp.json() or {}
            versions = data.get("versions") or []
            current_id = data.get("currentVersionId")
            if not versions:
                return f"No version history for context {context_id}."

            lines = [f"Version history for context {context_id} ({len(versions)} entries):"]
            for v in versions:
                vid = v.get("id", "?")
                vnum = v.get("versionNum", "?")
                edited_at = v.get("editedAt", "?")
                eb = (v.get("editedBy") or {})
                who = eb.get("name") or eb.get("email") or "unknown"
                src = v.get("editSource") or ""
                is_current = vid == current_id
                restorable = v.get("isRestorable")
                title = v.get("title") or "(untitled)"
                flags = []
                if is_current:
                    flags.append("CURRENT")
                if not is_current:
                    if restorable:
                        flags.append("restorable")
                    else:
                        age = v.get("ageDays", "?")
                        window = v.get("windowDays", "?")
                        min_plan = v.get("minPlanRequired")
                        tag = f"outside window ({age}d > {window}d"
                        if min_plan:
                            tag += f"; needs {min_plan}"
                        tag += ")"
                        flags.append(tag)
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                line = f"- v{vnum} (id: {vid}){flag_str}: {title}"
                line += f"\n  editedAt: {edited_at} by {who}"
                if src:
                    line += f" via {src}"
                lines.append(line)
            return "\n".join(lines)

        except Exception as e:
            return f"Error fetching versions ({type(e).__name__}): {str(e)}"

    @mcp_app.tool()
    async def read_context_version(context_id: str, version_id: str) -> str:
        """
        Read the full content of a specific historical version of a saved item.

        The version_id comes from get_context_versions. Tombstones and
        content-fetch-failed backfill rows return empty content. The current
        version is also fetchable.

        Args:
            context_id: The UUID of the saved item.
            version_id: The UUID of the specific version snapshot.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            resp = await http_client.get(
                f"{BACKEND_API_URL}/ai/context/{context_id}/versions/{version_id}/content",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 404:
                return f"Error: Version {version_id} not found for context {context_id}."
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            d = resp.json() or {}
            title = d.get("title") or "(untitled)"
            desc = d.get("description") or ""
            edited_at = d.get("editedAt") or "?"
            content = d.get("content") or ""

            header = f"**{title}**"
            if desc:
                header += f"\n_{desc}_"
            header += f"\neditedAt: {edited_at}"
            if not content:
                return f"{header}\n\n(version has no content — likely a tombstone or backfill failure)"
            return f"{header}\n\n{content}"

        except Exception as e:
            return f"Error reading version ({type(e).__name__}): {str(e)}"

    @mcp_app.tool()
    async def get_bucket_activity(bucket_id: str, limit: int = 20) -> str:
        """
        Show recent activity in a memory bucket: items created, edited,
        renamed, moved, deleted, restored — and who did each, with timestamps.

        Use this when the user asks "what's been happening here lately", "did
        anyone touch this bucket", or wants an audit summary. Requires
        viewer+ access on the bucket.

        Args:
            bucket_id: The UUID of the bucket whose activity to fetch.
            limit: Maximum events to return (default 20, capped at 50).
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            effective_limit = max(1, min(50, int(limit) if limit else 20))
            resp = await http_client.get(
                f"{BACKEND_API_URL}/ai/activity/profile/{bucket_id}",
                params={"limit": str(effective_limit)},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 404:
                return f"Error: Bucket {bucket_id} not found or you do not have access."
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            d = resp.json() or {}
            events = d.get("activities") or []
            total = d.get("total", len(events))
            if not events:
                return f"No recent activity in bucket {bucket_id}."

            lines = [f"Activity in bucket {bucket_id} ({len(events)} of {total} shown):"]
            for ev in events:
                when = ev.get("createdAt", "?")
                action = ev.get("action", "?")
                actor = (ev.get("actor") or {})
                who = actor.get("name") or actor.get("email") or "unknown"
                tgt_type = ev.get("targetType") or ""
                tgt_id = ev.get("targetId") or ""
                suffix = ""
                if tgt_type:
                    suffix = f" — {tgt_type}"
                    if tgt_id:
                        suffix += f" {tgt_id}"
                lines.append(f"- {when}: {who} {action}{suffix}")
            return "\n".join(lines)

        except Exception as e:
            return f"Error fetching activity ({type(e).__name__}): {str(e)}"

    @mcp_app.tool()
    async def get_bucket_access(bucket_id: str) -> str:
        """
        List the users who have access to a memory bucket and their role
        (editor or viewer). Owner-only — the call returns a "not the owner"
        404 if the caller doesn't own the bucket.

        Use this when the user asks "who can see this bucket" or "who is it
        shared with". Access grants/revokes themselves aren't exposed as
        tools — direct the user to the studio UI for those.

        Args:
            bucket_id: The UUID of the bucket whose access list to fetch.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            resp = await http_client.get(
                f"{BACKEND_API_URL}/ai/profile/{bucket_id}/access",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 404:
                return f"Error: Bucket {bucket_id} not found, or you are not its owner."
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            d = resp.json() or {}
            shares = d.get("shares") or []
            if not shares:
                return f"Bucket {bucket_id} is not shared with anyone yet."

            lines = [f"Access list for bucket {bucket_id} ({len(shares)} collaborator(s)):"]
            for s in shares:
                user = (s.get("user") or {})
                who = user.get("name") or user.get("email") or user.get("wallet") or "unknown"
                role = s.get("role", "?")
                created = s.get("createdAt", "?")
                lines.append(f"- {who} ({role}) — added {created}")
            return "\n".join(lines)

        except Exception as e:
            return f"Error fetching access list ({type(e).__name__}): {str(e)}"

    # ── PHASE 3: MUTATIONS (CONFIRM BEFORE CALLING) ─────────────────

    @mcp_app.tool()
    async def copy_context(
        context_id: str,
        target_bucket_id: str,
        expected_updated_at: str,
    ) -> str:
        """
        Copy a saved item into a different bucket. The original stays in
        place; a fresh contextId is created in the target. Re-embedding
        happens in the background via the worker queue.

        Always confirm with the user before calling. Briefly state the copy
        ("I'll copy X into bucket Y"), and only proceed after explicit yes.

        BEFORE calling, call read_context(context_id) to fetch the current
        updatedAt — pass it as expected_updated_at. The check prevents
        copying a stale snapshot.

        Args:
            context_id: The UUID of the item to copy.
            target_bucket_id: The UUID of the destination bucket. You need
                editor or owner access on it.
            expected_updated_at: The updatedAt timestamp from your most
                recent read_context call for this item.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            resp = await http_client.post(
                f"{BACKEND_API_URL}/ai/context/{context_id}/copy",
                json={
                    "targetProfileId": target_bucket_id,
                    "expectedUpdatedAt": expected_updated_at,
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

            if resp.status_code == 403:
                return "Error: You do not have permission to copy into the target bucket."
            if resp.status_code == 404:
                return f"Error: Context {context_id} not found."
            if resp.status_code == 409:
                detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                err = (detail.get("error") or "")
                if "trash" in err.lower():
                    return f"Context {context_id} is in the trash. Restore it before copying."
                if "embedding" in err.lower() or "error state" in err.lower():
                    return f"Source memory cannot be copied right now: {err}"
                current = detail.get("currentUpdatedAt", "unknown")
                return (
                    f"Conflict: this item was edited since your last read.\n"
                    f"Current updatedAt is {current}. Call read_context({context_id!r}) again, then retry."
                )
            if resp.status_code == 507:
                return "Error: Target bucket's owner has no storage left. Ask them to free space or upgrade."
            if resp.status_code not in (200, 202):
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            d = resp.json() or {}
            new_id = d.get("newContextId") or d.get("contextId") or "?"
            return (
                f"Copy queued. New context {new_id} created in target bucket {target_bucket_id}. "
                f"It will become searchable once the embedding worker finishes."
            )

        except Exception as e:
            return f"Error copying context ({type(e).__name__}): {str(e)}"

    @mcp_app.tool()
    async def move_context(context_id: str, target_bucket_id: str) -> str:
        """
        Relocate a saved item from its current bucket to a different one.
        The contextId stays the same; only the parent bucket changes.
        Vectors are NOT re-embedded — the move is a single atomic DB update.

        Always confirm with the user before calling. Briefly state the move
        ("I'll move X to bucket Y"), and only proceed after explicit yes.

        Reversible — you can move it back the same way.

        Args:
            context_id: The UUID of the item to move.
            target_bucket_id: The UUID of the destination bucket. You need
                editor or owner access on it.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            resp = await http_client.patch(
                f"{BACKEND_API_URL}/ai/context/{context_id}/move",
                json={"targetProfileId": target_bucket_id},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

            if resp.status_code == 403:
                return "Error: You do not have permission to move this item (need editor on source, editor/owner on target)."
            if resp.status_code == 404:
                return f"Error: Context {context_id} not found."
            if resp.status_code == 409:
                detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                err = (detail.get("error") or "")
                if err == "SOURCE_BUSY_COPYING":
                    return "This memory is currently being copied. Try again in a moment."
                if "trash" in err.lower():
                    return f"Context {context_id} is in the trash. Restore it before moving."
                if "embedding" in err.lower():
                    return "Source memory is still embedding; cannot move right now."
                return f"Move did not complete: {err or 'concurrent change'}. Reload and retry."
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            d = resp.json() or {}
            if d.get("unchanged"):
                return f"Context {context_id} is already in bucket {target_bucket_id} — nothing to move."
            return f"Moved context {context_id} into bucket {target_bucket_id}."

        except Exception as e:
            return f"Error moving context ({type(e).__name__}): {str(e)}"

    @mcp_app.tool()
    async def restore_context_to_version(
        context_id: str,
        version_id: str,
        expected_updated_at: str,
    ) -> str:
        """
        Roll back the content of a saved item to one of its historical
        versions. The current content gets archived (still recoverable
        within the restore window); the chosen version becomes live.
        No re-embedding — historical S3 vectors are kept alive until the
        retention cron evicts them.

        Always confirm with the user before calling. Briefly state what will
        happen ("I'll roll back the content of X to the version from
        <date>"), and only proceed after explicit yes.

        BEFORE calling, call read_context(context_id) to get the current
        updatedAt. Call get_context_versions to find the version_id you want
        to roll back to.

        Args:
            context_id: The UUID of the saved item.
            version_id: The UUID of the historical version to promote.
            expected_updated_at: The updatedAt timestamp from your most
                recent read_context call for this item.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            resp = await http_client.post(
                f"{BACKEND_API_URL}/ai/context/{context_id}/versions/{version_id}/restore",
                json={"lastSeenUpdatedAt": expected_updated_at},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

            if resp.status_code == 403:
                detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                err = (detail.get("error") or "")
                if err == "OUTSIDE_RESTORE_WINDOW":
                    age = detail.get("ageDays", "?")
                    window = detail.get("windowDays", "?")
                    still = detail.get("stillRecoverableForDays")
                    min_plan = detail.get("minPlanRequired")
                    msg = f"Cannot restore — this version is {age} days old; current plan allows {window} days."
                    if still and still > 0 and min_plan:
                        msg += f" Upgrading to {min_plan} would still allow recovery for {still} more day(s)."
                    else:
                        msg += " Upgrading the plan would no longer recover it."
                    return msg
                return "Error: You do not have permission to restore versions of this item."
            if resp.status_code == 404:
                return f"Error: Version {version_id} not found for context {context_id}."
            if resp.status_code == 400:
                detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                err = (detail.get("error") or "").lower()
                if "tombstone" in err or "no content" in err:
                    return f"Error: Version {version_id} cannot be restored (no content / cleanup tombstone)."
                return f"Error: {detail.get('error') or 'invalid request'}."
            if resp.status_code == 409:
                detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                err = (detail.get("error") or "")
                if err == "SOURCE_BUSY_COPYING":
                    return "This memory is currently being copied. Try again in a moment."
                if "trash" in err.lower():
                    return f"Context {context_id} is in the trash. Restore it from trash before reverting versions."
                current = detail.get("currentUpdatedAt", "unknown")
                return (
                    f"Conflict: this item was edited since your last read.\n"
                    f"Current updatedAt is {current}. Call read_context({context_id!r}) again, then retry."
                )
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            return f"Restored context {context_id} to version {version_id}. The prior content is archived to history."

        except Exception as e:
            return f"Error restoring version ({type(e).__name__}): {str(e)}"

    @mcp_app.tool()
    async def rename_bucket(bucket_id: str, new_name: str) -> str:
        """
        Rename a memory bucket. Only the owner can rename; the new name must
        be unique among the owner's buckets.

        Always confirm with the user before calling. Briefly state the change
        ("I'll rename the bucket from X to Y"), and only proceed after
        explicit yes.

        Args:
            bucket_id: The UUID of the bucket to rename.
            new_name: New name (1-200 characters, non-empty after trim).
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        if not new_name or not new_name.strip():
            return "Error: new_name is required and must be non-empty."

        try:
            resp = await http_client.put(
                f"{BACKEND_API_URL}/ai/profile/{bucket_id}",
                json={"profileName": new_name.strip()},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

            if resp.status_code == 404:
                return f"Error: Bucket {bucket_id} not found, or you don't own it (only owners can rename)."
            if resp.status_code == 409:
                return f"Error: A bucket named {new_name.strip()!r} already exists. Pick a different name."
            if resp.status_code == 400:
                detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                return f"Error: {detail.get('error') or 'invalid request'}."
            if resp.status_code != 200:
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            d = resp.json() or {}
            return f"Renamed bucket to {d.get('profileName', new_name.strip())!r} (id: {bucket_id})."

        except Exception as e:
            return f"Error renaming bucket ({type(e).__name__}): {str(e)}"

    @mcp_app.tool()
    async def duplicate_bucket(
        source_bucket_id: str,
        new_name: Optional[str] = None,
    ) -> str:
        """
        Create a fresh bucket that is a full copy of an existing one — every
        item inside is copied in the background via the worker queue. The
        original bucket is untouched.

        Heavy on storage and credits: duplicating an N-MB bucket consumes N
        MB on the owner's plan and may trigger re-embedding charges
        proportional to content size.

        Always confirm with the user before calling. Say roughly: "This
        bucket has K items totaling M MB. Duplicating creates a fresh
        bucket with the same content. Proceed?" — pulling K and M from
        get_user_memory_buckets if you don't already have them. Only call
        after explicit yes.

        Args:
            source_bucket_id: The UUID of the bucket to duplicate.
            new_name: Optional explicit name. If omitted, an auto-suffixed
                name like "<source name> (2)" is derived.
        """
        token = current_token.get()
        if not token:
            return "Error: Not authenticated"

        try:
            body: dict = {}
            if new_name and new_name.strip():
                body["newName"] = new_name.strip()
            resp = await http_client.post(
                f"{BACKEND_API_URL}/ai/profile/{source_bucket_id}/duplicate",
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

            if resp.status_code == 404:
                return f"Error: Bucket {source_bucket_id} not found."
            if resp.status_code == 409:
                detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                err = (detail.get("error") or "")
                if err == "BUCKET_NAME_TAKEN":
                    return f"Error: A bucket named {new_name!r} already exists. Pick a different name or omit it for auto-suffix."
                if err == "NAME_SUFFIX_EXHAUSTED":
                    return "Error: Could not derive a unique auto-name. Provide an explicit new_name."
                return f"Error: {err or 'concurrent change'}."
            if resp.status_code == 507:
                return "Error: Not enough storage on your plan to duplicate. Free space or upgrade."
            if resp.status_code not in (200, 201, 202):
                return f"Error: Backend API returned status {resp.status_code}: {resp.text}"

            d = resp.json() or {}
            new_bid = d.get("newProfileId") or (d.get("profile") or {}).get("id") or "?"
            resolved_name = d.get("newName") or (d.get("profile") or {}).get("profileName") or (new_name.strip() if new_name else "?")
            queued = d.get("queuedCount") or d.get("eligibleCount")
            queued_str = f" Queuing {queued} item(s) to copy in the background." if queued else ""
            return (
                f"Duplication started. New bucket {resolved_name!r} (id: {new_bid}) created.{queued_str} "
                f"Items become searchable as the worker finishes."
            )

        except Exception as e:
            return f"Error duplicating bucket ({type(e).__name__}): {str(e)}"
