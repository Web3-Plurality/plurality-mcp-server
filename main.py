from mcp.server.fastmcp import FastMCP
from mcp.types import Resource, TextResourceContents
from duckduckgo_search import DDGS
from langchain_community.document_loaders import WebBaseLoader
import re
import requests
import json
from urllib.parse import urlparse
from typing import Optional, Dict, Any, List
import jwt
from datetime import datetime
from fastapi import Request
from fastapi.responses import JSONResponse
import os
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

mcp_app = FastMCP(name="mcp", stateless_http=True)

# JWT Configuration
JWT_SECRET_KEY = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"

# Database Configuration
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT", "5432"),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD")
}

def get_db_connection():
    """Create and return a database connection"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        raise Exception(f"Database connection failed: {str(e)}")

def verify_token_in_db(userId: str, profileId: str, token_hash: str) -> bool:
    """
    Verify if the given token_hash matches the mcpTokenHash in the UserAiProfile table.

    Args:
        userId: The user ID
        profileId: The profile ID
        token_hash: SHA256 hash of the JWT token

    Returns:
        True if token_hash matches mcpTokenHash, False otherwise
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        print("userId", userId)
        print("profileId", profileId)
        print("token_hash", token_hash)
        # Query to retrieve the mcpTokenHash for the specified user and profile
        query = """
            SELECT "mcpTokenHash" FROM user_ai_profile
            WHERE "userId" = %s AND "id" = %s
        """
        cursor.execute(query, (userId, profileId))
        print("cursor", cursor)
        record = cursor.fetchone()
        print("record", record)
        if record and "mcpTokenHash" in record:
            return str(record["mcpTokenHash"]) == str(token_hash)
        return False
        
    except Exception as e:
        raise Exception(f"Database query failed: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Middleware for JWT authentication
async def authenticate_jwt(request: Request, call_next):
    """
    Middleware to verify JWT token from Authorization header.
    Checks Bearer token, verifies signature and expiration.
    """
    # Skip authentication for health check or docs endpoints (if any)
    if request.url.path in ["/health", "/docs", "/openapi.json"]:
        return await call_next(request)
    
    # Get Authorization header
    auth_header = request.headers.get("Authorization", "")
    
    if not auth_header:
        return JSONResponse(
            status_code=401,
            content={
                "error": "Missing Authorization header",
                "message": "Please provide a Bearer token in the Authorization header"
            }
        )
    
    # Check if it's a Bearer token
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={
                "error": "Invalid Authorization header format",
                "message": "Authorization header must be in format: Bearer <token>"
            }
        )
    
    # Extract token
    token = auth_header.replace("Bearer ", "").strip()
    
    try:
        # Verify and decode JWT token
        payload = jwt.decode(
            token, 
            JWT_SECRET_KEY, 
            algorithms=[JWT_ALGORITHM]
        )
        
        # Check if token has expired (jwt.decode already checks this, but being explicit)
        exp = payload.get("exp")
        if exp and datetime.fromtimestamp(exp) < datetime.utcnow():
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Token expired",
                    "message": "Your token has expired. Please request a new one."
                }
            )

        userId = payload.get("userId")
        profileId = payload.get("profileId")
        
        # Hash the token 
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        
        # Verify token hash exists in database
        try:
            user_verified = verify_token_in_db(userId,profileId,token_hash)
            
            if not user_verified:
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "Token not verified",
                        "message": "Token hash not verified. Token may have been revoked or is invalid."
                    }
                )
            
            # Store user info and profile data in request state for use in tools
            request.state.userId = userId
            request.state.profileId = profileId
            
        except Exception as db_error:
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Database verification failed",
                    "message": f"Failed to verify token in database: {str(db_error)}"
                }
            )
        
        # Proceed with the request
        response = await call_next(request)
        return response
        
    except jwt.ExpiredSignatureError:
        return JSONResponse(
            status_code=401,
            content={
                "error": "Token expired",
                "message": "Your token has expired. Please request a new one."
            }
        )
    except jwt.InvalidTokenError as e:
        return JSONResponse(
            status_code=401,
            content={
                "error": "Invalid token",
                "message": f"Token verification failed: {str(e)}"
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "error": "Authentication error",
                "message": f"Unexpected error during authentication: {str(e)}"
            }
        )


def _is_valid_url(url: str) -> bool:
    """Validate URL format"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False

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



mcp_server = mcp_app.streamable_http_app()
mcp_server.middleware("http")(authenticate_jwt)