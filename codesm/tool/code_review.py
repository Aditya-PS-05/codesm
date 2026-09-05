"""Code Review Tool - Reviews code changes with actionable feedback"""

import os
import logging
import subprocess
from pathlib import Path
from typing import Optional

import httpx

from .base import Tool

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REVIEW_MODEL = "anthropic/claude-sonnet-4-20250514"

REVIEW_SYSTEM_PROMPT = """You are an expert code reviewer. Analyze the provided code diff and identify:

1. **Bugs & Errors**: Logic errors, null/undefined issues, race conditions, off-by-one errors
2. **Security Issues**: Injection attacks, hardcoded secrets, insecure patterns
3. **Performance Issues**: N+1 queries, unnecessary allocations, inefficient algorithms
4. **Code Quality**: Missing error handling, poor naming, code duplication

For each issue, provide:
- **severity**: critical | warning | suggestion
- **file**: filepath
- **line**: line number (from the diff, use + line numbers)
- **description**: clear explanation
- **fix**: concrete fix suggestion

Be concise and actionable. Focus on real bugs, not style nitpicks.

Format your response as:
## Issues Found

### [severity] file:line
**Issue**: description
**Fix**: fix suggestion

---

## Summary
One paragraph summary of the review."""


class CodeReviewTool(Tool):
    name = "code_review"
    description = "Review code changes (staged, branch diff, or specific files) for bugs and issues."
    
    def __init__(self, parent_tools=None):
        super().__init__()
        self._client: Optional[httpx.AsyncClient] = None
        self._parent_tools = parent_tools
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "Review mode: 'staged' for staged changes, 'branch:NAME' to compare with branch, 'commit:SHA' for specific commit, 'files:path1,path2' for specific files",
                    "default": "staged",
                },
                "base_branch": {
                    "type": "string",
                    "description": "Base branch for comparison (default: main or master)",
                },
            },
            "required": [],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        mode = args.get("mode", "staged")
        base_branch = args.get("base_branch")
        cwd = context.get("cwd", Path.cwd())
        
        try:
            diff = self._get_diff(mode, base_branch, cwd)
        except Exception as e:
            return f"Error getting diff: {e}"
        
        if not diff.strip():
            return "No changes to review."
        
        try:
            review = await self._review_diff(diff)
            return review
        except Exception as e:
            logger.error(f"Review failed: {e}")
            return f"Review failed: {e}"
    
    def _get_diff(self, mode: str, base_branch: Optional[str], cwd: Path) -> str:
        """Get diff based on mode"""
        if mode == "staged":
            result = subprocess.run(
                ["git", "diff", "--cached"],
                capture_output=True, text=True, cwd=cwd
            )
            if not result.stdout.strip():
                result = subprocess.run(
                    ["git", "diff"],
                    capture_output=True, text=True, cwd=cwd
                )
            return result.stdout
        
        elif mode.startswith("branch:"):
            branch = mode.split(":", 1)[1]
            result = subprocess.run(
                ["git", "diff", f"{branch}...HEAD"],
                capture_output=True, text=True, cwd=cwd
            )
            return result.stdout
        
        elif mode.startswith("commit:"):
            commit = mode.split(":", 1)[1]
            result = subprocess.run(
                ["git", "show", commit, "--format="],
                capture_output=True, text=True, cwd=cwd
            )
            return result.stdout
        
        elif mode.startswith("files:"):
            files = mode.split(":", 1)[1].split(",")
            diffs = []
            for f in files:
                path = Path(f.strip())
                if path.exists():
                    result = subprocess.run(
                        ["git", "diff", "--", str(path)],
                        capture_output=True, text=True, cwd=cwd
                    )
                    if result.stdout:
                        diffs.append(result.stdout)
            return "\n".join(diffs)
        
        else:
            base = base_branch or "main"
            result = subprocess.run(
                ["git", "diff", f"{base}...HEAD"],
                capture_output=True, text=True, cwd=cwd
            )
            return result.stdout
    
    async def _review_diff(self, diff: str) -> str:
        """Send diff to LLM for review"""
        if len(diff) > 50000:
            diff = diff[:50000] + "\n... (truncated)"
        
        
        from codesm.provider.base import complete
        return await complete(REVIEW_SYSTEM_PROMPT, f"Review this diff:\n\n```diff\n{diff}\n```")
