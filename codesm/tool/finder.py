"""Finder tool - high-speed codebase retrieval using Gemini Flash"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from codesm.tool.registry import ToolRegistry

logger = logging.getLogger(__name__)

# System prompt for the finder LLM
FINDER_SYSTEM_PROMPT = """You are a lightning-fast code finder. Your job is to analyze search results and provide clear, actionable answers.

# Your Role
You receive raw search results (from grep/glob) and must:
1. Filter out noise and irrelevant matches
2. Rank results by relevance to the query
3. Provide a concise summary

# Output Format
Respond with:
1. A brief answer to the query (1-2 sentences)
2. Top 5 most relevant matches with file:line and brief context
3. If nothing relevant found, suggest better search terms

Be FAST and CONCISE. No lengthy explanations."""


class FinderTool(Tool):
    """High-speed codebase retrieval using Gemini Flash for intelligent search"""
    
    name = "finder"
    description = "Fast LLM-powered codebase search. Finds code by meaning, not just keywords. Uses Gemini Flash for speed."
    
    def __init__(self, parent_tools: "ToolRegistry | None" = None):
        super().__init__()
        self._parent_tools = parent_tools
    
    def set_parent(self, tools: "ToolRegistry"):
        """Set parent context (called by Agent after init)"""
        self._parent_tools = tools
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What you're looking for (e.g., 'authentication logic', 'database connection setup', 'error handling for API calls')",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (defaults to project root)",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "File pattern to filter (e.g., '*.py', '*.ts')",
                },
            },
            "required": ["query"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        """Execute the finder search"""
        from codesm.provider.base import get_provider
        
        query = args.get("query", "")
        if not query:
            return "Error: query is required"
        
        path = args.get("path") or context.get("cwd", ".")
        file_pattern = args.get("file_pattern")
        
        root = Path(path).resolve()
        if not root.exists():
            return f"Error: Path '{path}' does not exist"
        
        # Step 1: Gather raw results using grep and glob
        raw_results = await self._gather_search_results(query, root, file_pattern, context)
        
        if not raw_results.strip():
            return f"No matches found for '{query}'. Try different keywords or check the path."
        
        # Step 2: Use Gemini Flash to intelligently filter and rank results
        try:
            provider = get_provider("finder")  # Uses Gemini Flash via router
            
            user_prompt = f"""Query: {query}

Search results from codebase at {root}:

{raw_results}

Analyze these results and provide:
1. Direct answer: Where is this code located?
2. Top matches with file:line references
3. Any related files that might be relevant"""

            # Collect response
            response_text = ""
            async for chunk in provider.stream(
                system=FINDER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                tools=None,  # No tools needed for summarization
            ):
                if chunk.type == "text":
                    response_text += chunk.content
            
            return response_text
            
        except Exception as e:
            logger.warning(f"Finder LLM failed, returning raw results: {e}")
            # Fallback to raw results if LLM fails
            return f"Search results for '{query}':\n\n{raw_results}"
    
    async def _gather_search_results(
        self, 
        query: str, 
        root: Path, 
        file_pattern: str | None,
        context: dict,
    ) -> str:
        """Gather search results using grep and glob"""
        results = []
        
        # Extract keywords from query for grep
        keywords = self._extract_keywords(query)
        
        if not self._parent_tools:
            # Fallback to basic search
            return self._basic_search(query, root, file_pattern)
        
        # Try grep for each keyword
        grep_tool = self._parent_tools.get("grep")
        if grep_tool and keywords:
            for keyword in keywords[:3]:  # Limit to 3 keywords for speed
                try:
                    grep_args = {
                        "pattern": keyword,
                        "path": str(root),
                        "max_matches": 20,
                    }
                    if file_pattern:
                        grep_args["include"] = file_pattern
                    
                    result = await grep_tool.execute(grep_args, context)
                    if result and "No matches" not in result and "Error" not in result:
                        results.append(f"### Matches for '{keyword}':\n{result}")
                except Exception as e:
                    logger.debug(f"Grep failed for {keyword}: {e}")
        
        # Try glob for file patterns
        glob_tool = self._parent_tools.get("glob")
        if glob_tool:
            try:
                # Create pattern from query keywords
                patterns_to_try = []
                
                if file_pattern:
                    patterns_to_try.append(file_pattern)
                else:
                    # Generate patterns from keywords
                    for keyword in keywords[:2]:
                        patterns_to_try.append(f"**/*{keyword.lower()}*")
                        patterns_to_try.append(f"**/*{keyword.lower()}*.py")
                
                for pattern in patterns_to_try[:3]:
                    glob_args = {
                        "pattern": pattern,
                        "path": str(root),
                    }
                    result = await glob_tool.execute(glob_args, context)
                    if result and "No matches" not in result and "Error" not in result:
                        results.append(f"### Files matching '{pattern}':\n{result}")
            except Exception as e:
                logger.debug(f"Glob failed: {e}")
        
        return "\n\n".join(results) if results else ""
    
    def _extract_keywords(self, query: str) -> list[str]:
        """Extract search keywords from natural language query"""
        # Remove common words
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "must", "shall", "can", "need", "dare",
            "ought", "used", "to", "of", "in", "for", "on", "with", "at", "by",
            "from", "as", "into", "through", "during", "before", "after",
            "above", "below", "between", "under", "again", "further", "then",
            "once", "here", "there", "when", "where", "why", "how", "all",
            "each", "few", "more", "most", "other", "some", "such", "no", "nor",
            "not", "only", "own", "same", "so", "than", "too", "very", "just",
            "and", "but", "if", "or", "because", "until", "while", "although",
            "find", "search", "look", "looking", "show", "get", "what", "which",
            "code", "file", "files", "function", "class", "method", "that", "this",
        }
        
        # Split and filter
        words = query.lower().replace("_", " ").replace("-", " ").split()
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        
        # Also include camelCase/snake_case patterns
        if "_" in query or any(c.isupper() for c in query):
            # Add original casing for code patterns
            code_patterns = [w for w in query.split() if "_" in w or any(c.isupper() for c in w)]
            keywords.extend(code_patterns)
        
        return list(dict.fromkeys(keywords))  # Remove duplicates, preserve order
    
    def _basic_search(self, query: str, root: Path, file_pattern: str | None) -> str:
        """Basic search fallback without parent tools"""
        import subprocess
        
        keywords = self._extract_keywords(query)
        if not keywords:
            keywords = query.split()[:3]
        
        results = []
        
        for keyword in keywords[:3]:
            try:
                cmd = ["grep", "-r", "-n", "-i", "--include=*.py", "--include=*.js", "--include=*.ts", keyword, str(root)]
                if file_pattern:
                    cmd = ["grep", "-r", "-n", "-i", f"--include={file_pattern}", keyword, str(root)]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if result.stdout:
                    lines = result.stdout.strip().split("\n")[:20]
                    results.append(f"### {keyword}:\n" + "\n".join(lines))
            except Exception:
                pass
        
        return "\n\n".join(results)
