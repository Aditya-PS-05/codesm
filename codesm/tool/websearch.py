"""Web search tool using Exa AI"""

import httpx
from .base import Tool


class WebSearchTool(Tool):
    name = "websearch"
    description = """Search the web using DuckDuckGo or Exa AI.
- Use this tool to find up-to-date information, documentation, or solve errors.
- Automatically selects the best provider (Exa if configured, else DuckDuckGo).
- Returns a list of results with titles, URLs, and snippets."""

    API_URL = "https://api.exa.ai/search"  # Fixed actual API endpoint
    DEFAULT_NUM_RESULTS = 5

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of search results to return (default: 8)",
                },
                "type": {
                    "type": "string",
                    "enum": ["auto", "fast", "deep"],
                    "description": "Search type - 'auto': balanced (default), 'fast': quick, 'deep': comprehensive",
                },
            },
            "required": ["query"],
        }

    async def execute(self, args: dict, context: dict) -> str:
        query = args["query"]
        num_results = args.get("num_results", self.DEFAULT_NUM_RESULTS)
        search_type = args.get("type", "auto")
        
        # Determine provider: simple heuristic or config could be used here
        # For now, we default to DuckDuckGo as it's free and reliable for general queries
        # If Exa key is present in environment, we could prefer it for deep research
        import os
        exa_key = os.environ.get("EXA_API_KEY")
        
        if exa_key:
            return await self._search_exa(query, num_results, search_type, exa_key)
        else:
            return await self._search_ddg(query, num_results)

    async def _search_ddg(self, query: str, num_results: int) -> str:
        """Search using DuckDuckGo (via duckduckgo_search package)"""
        try:
            # Try async version first (avail in newer versions)
            try:
                from duckduckgo_search import AsyncDDGS
                async with AsyncDDGS() as ddgs:
                     results = await ddgs.text(query, max_results=num_results)
            except ImportError:
                # Fallback to sync version
                from duckduckgo_search import DDGS
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=num_results))
            
            if not results:
                return "No results found on DuckDuckGo."
                
            formatted = []
            for i, r in enumerate(results, 1):
                title = r.get("title", "No Title")
                link = r.get("href", "#")
                snippet = r.get("body", "")
                formatted.append(f"{i}. [{title}]({link})\n   {snippet}\n")
                
            return "\n".join(formatted)
            
        except ImportError:
            return "Error: duckduckgo-search package not installed. Please install it."
        except Exception as e:
            return f"Error searching DuckDuckGo: {str(e)}"

    async def _search_exa(self, query: str, num_results: int, search_type: str, api_key: str) -> str:
        """Search using Exa AI"""
        request_body = {
            "query": query,
            "numResults": num_results,
            "type": search_type,
            # "livecrawl": "fallback", # Optional depending on API version
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.API_URL,
                    json=request_body,
                    headers={
                        "x-api-key": api_key,
                        "content-type": "application/json",
                    },
                )
                response.raise_for_status()
                data = response.json()
                
                results = data.get("results", [])
                if not results:
                    return "No results found via Exa."
                    
                formatted = []
                for i, r in enumerate(results, 1):
                    title = r.get("title", "No Title")
                    url = r.get("url", "#")
                    text = r.get("text", "")
                    # Truncate text if too long
                    if len(text) > 300:
                        text = text[:300] + "..."
                    formatted.append(f"{i}. [{title}]({url})\n   {text}\n")
                    
                return "\n".join(formatted)

        except httpx.TimeoutException:
            return f"Error: Exa search timed out for '{query}'"
        except httpx.HTTPStatusError as e:
            return f"Error: Exa search failed ({e.response.status_code})"
        except Exception as e:
            # Fallback to DDG on failure?
            return await self._search_ddg(query, num_results)
