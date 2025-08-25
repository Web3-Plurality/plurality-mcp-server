from mcp.server.fastmcp import FastMCP
from mcp.types import Resource, TextResourceContents
from duckduckgo_search import DDGS
from langchain_community.document_loaders import WebBaseLoader
import re
import requests
import json
from urllib.parse import urlparse
from typing import Optional, Dict, Any, List

mcp_app = FastMCP(name="mcp", stateless_http=True)

def _is_valid_url(url: str) -> bool:
    """Validate URL format"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False

@mcp_app.tool()
def web_search(query: str, max_results: int = 5) -> str:
    """
    Search the web for information using DuckDuckGo.
    
    Args:
        query: The search query string
        max_results: Maximum number of results to return (default: 5, max: 10)
    
    Returns:
        Formatted string containing search results with titles, descriptions, and URLs
    """
    # Limit max_results to prevent excessive requests
    max_results = min(max_results, 10)
    
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            if results:
                formatted_results = []
                for result in results:
                    title = result.get('title', 'No title')
                    body = result.get('body', 'No description')
                    url = result.get('href', 'No URL')
                    formatted_results.append(f"Title: {title}\nDescription: {body}\nURL: {url}")
                return f"Web search results for '{query}':\n\n" + "\n\n".join(formatted_results)
            else:
                return f"No web search results found for '{query}'"
    except Exception as e:
        return f"Web search failed: {str(e)}"

@mcp_app.tool()
def extract_url_content(url: str) -> str:
    """
    Extract content from a URL/webpage.
    Use this tool when the user provides a URL and wants to analyze or get information from that webpage.
    
    Args:
        url: The URL to extract content from
        
    Returns:
        String containing the extracted content from the webpage
    """
    try:
        # Validate URL format
        if not _is_valid_url(url):
            return f"Invalid URL format: {url}"
        
        # Load content using WebBaseLoader
        loader = WebBaseLoader(url)
        documents = loader.load()
        
        if documents:
            # Get the main content
            content = documents[0].page_content
            metadata = documents[0].metadata
            
            # Clean and format the content
            content = content.strip()
            
            # Remove excessive whitespace and normalize text
            content = re.sub(r'\n\s*\n', '\n\n', content)  # Replace multiple newlines with double newlines
            content = re.sub(r' +', ' ', content)  # Replace multiple spaces with single space
            
            if len(content) > 8000:  # Limit content size for token limits
                content = content[:8000] + "... [Content truncated due to length]"
            
            title = metadata.get('title', 'No title')
            source = metadata.get('source', url)
            
            return f"URL Content Extracted from: {source}\nTitle: {title}\n\nContent:\n{content}"
        else:
            return f"No content could be extracted from URL: {url}"
            
    except Exception as e:
        return f"Failed to extract content from URL {url}: {str(e)}"

@mcp_app.tool()
def get_context(
    userId: str,
    profileId: str,
    query: str,
    k: int = 5,
    chatHistory: Optional[List[dict]] = [],  # Add chat history support
    contextSummary: Optional[List[str]] = []
) -> str:
    """
    Make an HTTP API call and return the response.
    
    Args:
        userId: The user ID
        profileId: The profile ID
        query: The query to search for
        k: The number of results to return
        chatHistory: The chat history
        contextSummary: The context summary
    
    Returns:
        String containing the API response with status code and data
    """
    try:
        url = "http://127.0.0.1:8000/rag/get-context"
        return call_rag_api(url, userId, profileId, query, k, chatHistory, contextSummary)
    except Exception as e:
        return f"Unexpected error during API call: {str(e)}"





@mcp_app.tool()
def get_optimized_query(
    userId: str,
    profileId: str,
    query: str,
    k: int = 5,
    chatHistory: Optional[List[dict]] = [],  # Add chat history support
    contextSummary: Optional[List[str]] = []
) -> str:
    """
    Make an HTTP API call and return the response.
    
    Args:
        userId: The user ID
        profileId: The profile ID
        query: The query to search for
        k: The number of results to return
        chatHistory: The chat history
        contextSummary: The context summary
    
    Returns:
        String containing the API response with status code and data
    """
    try:
        url = "http://127.0.0.1:8000/rag/rag-query"
        return call_rag_api(url, userId, profileId, query, k, chatHistory, contextSummary)
                
    except Exception as e:
        return f"Unexpected error during API call: {str(e)}"





def call_rag_api(
    url: str,
    userId: str,
    profileId: str,
    query: str,
    k: int = 5,
    chatHistory: Optional[List[dict]] = [],  # Add chat history support
    contextSummary: Optional[List[str]] = []
) -> str:
    try:
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": "dev-key-12345"
        }
        # Parse data if provided
        request_data = {
            "userId": userId,
            "profileId": profileId,
            "query": query,
            "k": k,
            "chatHistory": chatHistory,
            "contextSummary": contextSummary
        }

        
        # Make the API call
        response = requests.request(
            method="POST",
            url=url,
            headers=headers,
            json=request_data if request_data else None,
            timeout=30
        )
        
        # Get response details
        status_code = response.status_code
        response_headers = dict(response.headers)
        
        # Try to parse response as JSON, fallback to text
        try:
            response_data = response.json()
            print(response_data)
            # Check if response is successful
            if status_code == 200 and response_data:
                # Return the API response data directly as a formatted string
                return json.dumps(response_data, indent=2)
            else:
                return f"API call failed with status {status_code}: {json.dumps(response_data, indent=2)}"
                
        except json.JSONDecodeError:
            # If response is not JSON, return as text
            return f"API Response (Status {status_code}):\n{response.text}"
        
    except requests.exceptions.Timeout:
        return f"API call timed out after 30 seconds for URL: {url}"
    except requests.exceptions.ConnectionError:
        return f"Connection error when calling API: {url}"
    except requests.exceptions.RequestException as e:
        return f"API call failed for URL {url}: {str(e)}"
    except Exception as e:
        return f"Unexpected error during API call: {str(e)}"


mcp_server = mcp_app.streamable_http_app()