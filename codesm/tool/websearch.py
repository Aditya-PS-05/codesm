"""Web search tool using Exa AI"""

import httpx
from .base import Tool


class WebSearchTool(Tool):
    name = "websearch"
    description = """Search the web using Exa AI - performs real-time web searches.
- Provides up-to-date information for current events and recent data
- Use this tool for accessing information beyond knowledge cutoff
- Use for general knowledge questions that aren't about the local codebase"""

    API_URL = "https://mcp.exa.ai/mcp"
    DEFAULT_NUM_RESULTS = 8

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

        request_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "web_search_exa",
                "arguments": {
                    "query": query,
                    "numResults": num_results,
                    "type": search_type,
                    "livecrawl": "fallback",
                },
            },
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.API_URL,
                    json=request_body,
                    headers={
                        "accept": "application/json, text/event-stream",
                        "content-type": "application/json",
                    },
                )
                response.raise_for_status()

                # Parse SSE response
                for line in response.text.split("\n"):
                    if line.startswith("data: "):
                        import json
                        data = json.loads(line[6:])
                        if data.get("result", {}).get("content"):
                            return data["result"]["content"][0]["text"]

                return "No search results found. Please try a different query."

        except httpx.TimeoutException:
            return f"Error: Search request timed out for '{query}'"
        except httpx.HTTPStatusError as e:
            return f"Error: Search failed ({e.response.status_code})"
        except Exception as e:
            return f"Error searching for '{query}': {str(e)}"
