"""Web fetch tool - fetches content from URLs"""

import httpx
from .base import Tool


class WebFetchTool(Tool):
    name = "webfetch"
    description = """Fetch and read content from a specific URL.
- Use when the user provides a URL to read
- Use to read documentation pages
- Returns content as readable text/markdown
- Supports HTML pages, converting them to readable text"""

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch content from",
                },
                "format": {
                    "type": "string",
                    "enum": ["text", "markdown", "html"],
                    "description": "Output format (default: markdown)",
                },
            },
            "required": ["url"],
        }

    async def execute(self, args: dict, context: dict) -> str:
        url = args["url"]
        format_type = args.get("format", "markdown")

        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://"

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                text = response.text

                # Convert HTML to readable text
                if "html" in content_type and format_type in ("text", "markdown"):
                    text = self._html_to_text(text)

                # Limit response size
                if len(text) > 50000:
                    text = text[:50000] + "\n\n... (content truncated)"

                return text

        except httpx.TimeoutException:
            return f"Error: Request timed out for {url}"
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code} for {url}"
        except Exception as e:
            return f"Error fetching {url}: {str(e)}"

    def _html_to_text(self, html: str) -> str:
        """Convert HTML to readable text using BeautifulSoup"""
        try:
            from bs4 import BeautifulSoup, Comment
            
            soup = BeautifulSoup(html, "html.parser")
            
            # Remove unwanted elements
            for element in soup(["script", "style", "head", "noscript", "iframe", "svg", "meta", "link"]):
                element.decompose()
            
            # Remove comments
            for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
                comment.extract()
                
            # Convert links to markdown format: [text](href)
            for a in soup.find_all("a", href=True):
                text = a.get_text(strip=True)
                if text:
                    href = a["href"]
                    a.replace_with(f"[{text}]({href})")
            
            # Convert headings
            for i in range(1, 7):
                for h in soup.find_all(f"h{i}"):
                    text = h.get_text(strip=True)
                    if text:
                        h.replace_with(f"\n{'#' * i} {text}\n")
            
            # Convert code blocks
            for pre in soup.find_all("pre"):
                code = pre.get_text()
                pre.replace_with(f"\n```\n{code}\n```\n")
            for code in soup.find_all("code"):
                text = code.get_text()
                code.replace_with(f"`{text}`")
                
            # Process text and clean up whitespace from the layout
            text = soup.get_text(separator="\n", strip=True)
            
            # Normalize whitespace: max 2 newlines
            import re
            text = re.sub(r'\n{3,}', '\n\n', text)
            
            return text.strip()
            
        except ImportError:
            # Fallback to regex method if bs4 not available
            return self._regex_html_to_text(html)
        except Exception as e:
            return f"Error parsing HTML: {str(e)}\n\n(Raw content follows)\n\n{html[:1000]}..."

    def _regex_html_to_text(self, html: str) -> str:
        """Simple regex-based fallback for HTML to text conversion"""
        import re

        # Remove script and style elements
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<head[^>]*>.*?</head>', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove tags
        text = re.sub(r'<[^>]+>', ' ', text)
        
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()
