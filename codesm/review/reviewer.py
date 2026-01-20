"""Code Reviewer - Uses Gemini via OpenRouter for bug detection and code review"""

import os
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Model configuration - uses OpenRouter
REVIEW_MODEL = "google/gemini-2.5-pro-preview"  # Gemini 2.5 Pro via OpenRouter
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

REVIEW_SYSTEM_PROMPT = """You are an expert code reviewer. Your job is to review code changes and identify:

1. **Bugs & Errors**: Logic errors, off-by-one errors, null pointer issues, race conditions, etc.
2. **Security Issues**: SQL injection, XSS, hardcoded secrets, insecure defaults, etc.
3. **Performance Issues**: N+1 queries, unnecessary loops, memory leaks, etc.
4. **Code Quality**: Poor naming, missing error handling, code duplication, etc.
5. **Best Practices**: Missing type hints, documentation, tests, etc.

For each issue found, provide:
- **Severity**: critical, warning, or suggestion
- **Location**: File path and line number if possible
- **Description**: Clear explanation of the issue
- **Fix**: Suggested fix or code snippet

If the code looks good, say so briefly. Be concise and actionable.
Focus on real issues, not style nitpicks unless they affect readability significantly."""


@dataclass
class ReviewIssue:
    """A single issue found during code review"""
    severity: str  # "critical", "warning", "suggestion"
    file: str
    line: Optional[int]
    description: str
    fix: Optional[str] = None


@dataclass
class ReviewResult:
    """Result of a code review"""
    issues: list[ReviewIssue] = field(default_factory=list)
    summary: str = ""
    files_reviewed: list[str] = field(default_factory=list)
    
    @property
    def has_critical(self) -> bool:
        return any(i.severity == "critical" for i in self.issues)
    
    @property
    def has_warnings(self) -> bool:
        return any(i.severity == "warning" for i in self.issues)
    
    def format_for_display(self) -> str:
        """Format review result for TUI display"""
        if not self.issues:
            return f"✓ Code review passed - no issues found in {len(self.files_reviewed)} file(s)"
        
        lines = [f":: Code Review ({len(self.issues)} issue(s) in {len(self.files_reviewed)} file(s))"]
        lines.append("")
        
        # Group by severity
        critical = [i for i in self.issues if i.severity == "critical"]
        warnings = [i for i in self.issues if i.severity == "warning"]
        suggestions = [i for i in self.issues if i.severity == "suggestion"]
        
        if critical:
            lines.append("**Critical Issues:**")
            for issue in critical:
                loc = f"{issue.file}:{issue.line}" if issue.line else issue.file
                lines.append(f"  ✗ [{loc}] {issue.description}")
                if issue.fix:
                    lines.append(f"    → Fix: {issue.fix}")
            lines.append("")
        
        if warnings:
            lines.append("**Warnings:**")
            for issue in warnings:
                loc = f"{issue.file}:{issue.line}" if issue.line else issue.file
                lines.append(f"  ⚠ [{loc}] {issue.description}")
                if issue.fix:
                    lines.append(f"    → Fix: {issue.fix}")
            lines.append("")
        
        if suggestions:
            lines.append("**Suggestions:**")
            for issue in suggestions:
                loc = f"{issue.file}:{issue.line}" if issue.line else issue.file
                lines.append(f"  • [{loc}] {issue.description}")
            lines.append("")
        
        if self.summary:
            lines.append(self.summary)
        
        return "\n".join(lines)


class CodeReviewer:
    """Reviews code changes using Gemini via OpenRouter"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self._client = None
    
    def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if not self.api_key:
            raise ValueError("OpenRouter API key not found. Set OPENROUTER_API_KEY environment variable.")
        
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=120.0,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/codesm",
                    "X-Title": "codesm",
                },
            )
        
        return self._client
    
    async def review_files(self, files: list[dict]) -> ReviewResult:
        """Review a list of file changes.
        
        Args:
            files: List of dicts with 'path', 'content', and optionally 'diff'
        
        Returns:
            ReviewResult with issues found
        """
        if not files:
            return ReviewResult(summary="No files to review")
        
        # Build the review prompt
        prompt_parts = ["Review the following code changes:\n\n"]
        
        for f in files:
            path = f.get("path", "unknown")
            content = f.get("content", "")
            diff = f.get("diff", "")
            
            prompt_parts.append(f"## File: {path}\n")
            if diff:
                prompt_parts.append(f"### Changes (diff):\n```diff\n{diff}\n```\n")
            if content:
                # Only include first 500 lines to avoid token limits
                lines = content.split("\n")[:500]
                prompt_parts.append(f"### Current content:\n```\n{chr(10).join(lines)}\n```\n")
            prompt_parts.append("\n---\n\n")
        
        prompt_parts.append("""
Analyze these changes and respond in this exact format:

ISSUES:
- severity: <critical|warning|suggestion>
  file: <filepath>
  line: <line number or null>
  description: <what's wrong>
  fix: <how to fix it>

SUMMARY: <one sentence summary>

If there are no issues, respond with:
ISSUES: none
SUMMARY: Code looks good, no issues found.
""")
        
        prompt = "".join(prompt_parts)
        
        try:
            client = self._get_client()
            
            response = await client.post(
                OPENROUTER_URL,
                json={
                    "model": REVIEW_MODEL,
                    "messages": [
                        {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 4096,
                },
            )
            
            if response.status_code != 200:
                logger.error(f"OpenRouter API error: {response.status_code} - {response.text}")
                return ReviewResult(
                    summary=f"Review failed: API error {response.status_code}",
                    files_reviewed=[f.get("path", "") for f in files],
                )
            
            data = response.json()
            result_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            return self._parse_review_response(result_text, [f.get("path", "") for f in files])
        
        except Exception as e:
            logger.error(f"Code review failed: {e}")
            return ReviewResult(
                summary=f"Review failed: {e}",
                files_reviewed=[f.get("path", "") for f in files],
            )
    
    async def review_session_changes(self, session) -> ReviewResult:
        """Review all file changes made during a session.
        
        Args:
            session: Session object with messages containing patches
        
        Returns:
            ReviewResult with issues found
        """
        # Collect all edited files from session messages
        edited_files = set()
        file_diffs = {}
        
        for msg in session.messages:
            patches = msg.get("_patches", [])
            for patch in patches:
                for file_info in patch.get("files", []):
                    filepath = file_info.get("path", "")
                    if filepath:
                        edited_files.add(filepath)
                        # Store the diff if available
                        if "diff" in file_info:
                            file_diffs[filepath] = file_info["diff"]
            
            # Also check tool_display messages for edit/write tools
            if msg.get("role") == "tool_display" and msg.get("tool_name") in ["edit", "write"]:
                # Try to extract file path from content
                content = msg.get("content", "")
                if "Edit" in content or "Write" in content:
                    # Parse file path from message like "**Edit** filename.py"
                    import re
                    match = re.search(r'\*\*(?:Edit|Write)\*\*\s+([^\s]+)', content)
                    if match:
                        filepath = match.group(1)
                        if not filepath.startswith("/"):
                            filepath = str(session.directory / filepath)
                        edited_files.add(filepath)
        
        if not edited_files:
            return ReviewResult(summary="No file changes to review")
        
        # Read current content of edited files
        files_to_review = []
        for filepath in edited_files:
            try:
                path = Path(filepath)
                if path.exists() and path.is_file():
                    content = path.read_text()
                    files_to_review.append({
                        "path": str(path),
                        "content": content,
                        "diff": file_diffs.get(str(path), ""),
                    })
            except Exception as e:
                logger.warning(f"Could not read {filepath}: {e}")
        
        if not files_to_review:
            return ReviewResult(summary="No readable files to review")
        
        return await self.review_files(files_to_review)
    
    def _parse_review_response(self, text: str, files: list[str]) -> ReviewResult:
        """Parse the model response into a ReviewResult"""
        issues = []
        summary = ""
        
        lines = text.strip().split("\n")
        current_issue = {}
        in_issues = False
        
        for line in lines:
            line = line.strip()
            
            if line.startswith("ISSUES:"):
                in_issues = True
                if "none" in line.lower():
                    in_issues = False
                continue
            
            if line.startswith("SUMMARY:"):
                in_issues = False
                summary = line.replace("SUMMARY:", "").strip()
                continue
            
            if in_issues and line.startswith("-"):
                # Save previous issue if exists
                if current_issue.get("description"):
                    issues.append(ReviewIssue(
                        severity=current_issue.get("severity", "suggestion"),
                        file=current_issue.get("file", files[0] if files else "unknown"),
                        line=current_issue.get("line"),
                        description=current_issue.get("description", ""),
                        fix=current_issue.get("fix"),
                    ))
                current_issue = {}
            
            if in_issues:
                if "severity:" in line:
                    current_issue["severity"] = line.split("severity:")[-1].strip().lower()
                elif "file:" in line:
                    current_issue["file"] = line.split("file:")[-1].strip()
                elif "line:" in line:
                    line_val = line.split("line:")[-1].strip()
                    if line_val.isdigit():
                        current_issue["line"] = int(line_val)
                elif "description:" in line:
                    current_issue["description"] = line.split("description:")[-1].strip()
                elif "fix:" in line:
                    current_issue["fix"] = line.split("fix:")[-1].strip()
        
        # Don't forget the last issue
        if current_issue.get("description"):
            issues.append(ReviewIssue(
                severity=current_issue.get("severity", "suggestion"),
                file=current_issue.get("file", files[0] if files else "unknown"),
                line=current_issue.get("line"),
                description=current_issue.get("description", ""),
                fix=current_issue.get("fix"),
            ))
        
        return ReviewResult(
            issues=issues,
            summary=summary,
            files_reviewed=files,
        )
    
    async def close(self):
        """Close the HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None
