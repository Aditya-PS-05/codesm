"""Refactoring Suggestions - Proactive code improvement recommendations"""

import os
import re
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REFACTOR_MODEL = "anthropic/claude-sonnet-4-20250514"


class RefactorCategory(str, Enum):
    STRUCTURE = "structure"           # Code organization, modularity
    SIMPLIFICATION = "simplification"  # Reduce complexity, remove duplication
    PERFORMANCE = "performance"        # Algorithmic improvements
    READABILITY = "readability"        # Naming, documentation, clarity
    PATTERNS = "patterns"             # Design patterns, idioms
    MODERNIZATION = "modernization"   # Use newer language features
    TESTABILITY = "testability"       # Make code easier to test
    SAFETY = "safety"                 # Type safety, error handling


@dataclass
class RefactorSuggestion:
    """A single refactoring suggestion"""
    category: RefactorCategory
    priority: str  # "high", "medium", "low"
    file: str
    start_line: Optional[int]
    end_line: Optional[int]
    title: str
    description: str
    before_snippet: Optional[str] = None
    after_snippet: Optional[str] = None
    effort: str = "low"  # "low", "medium", "high"
    impact: str = "medium"  # "low", "medium", "high"


@dataclass
class RefactorAnalysis:
    """Result of refactoring analysis"""
    suggestions: list[RefactorSuggestion] = field(default_factory=list)
    files_analyzed: list[str] = field(default_factory=list)
    summary: str = ""
    metrics: dict = field(default_factory=dict)
    
    @property
    def high_priority_count(self) -> int:
        return sum(1 for s in self.suggestions if s.priority == "high")
    
    @property
    def quick_wins(self) -> list[RefactorSuggestion]:
        """Low effort, high impact suggestions"""
        return [s for s in self.suggestions 
                if s.effort == "low" and s.impact in ("high", "medium")]
    
    def format_for_display(self) -> str:
        """Format for TUI/CLI display"""
        if not self.suggestions:
            return f"✓ No refactoring suggestions for {len(self.files_analyzed)} file(s)"
        
        lines = [f"## Refactoring Suggestions ({len(self.suggestions)} total)"]
        lines.append("")
        
        # Group by category
        by_category: dict[RefactorCategory, list[RefactorSuggestion]] = {}
        for s in self.suggestions:
            by_category.setdefault(s.category, []).append(s)
        
        for category, suggestions in sorted(by_category.items(), key=lambda x: x[0].value):
            lines.append(f"### {category.value.title()} ({len(suggestions)})")
            lines.append("")
            
            for s in sorted(suggestions, key=lambda x: (
                {"high": 0, "medium": 1, "low": 2}.get(x.priority, 3)
            )):
                priority_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(s.priority, "⚪")
                loc = f"{s.file}:{s.start_line}" if s.start_line else s.file
                lines.append(f"{priority_icon} **{s.title}**")
                lines.append(f"   📍 `{loc}`")
                lines.append(f"   {s.description}")
                
                if s.before_snippet and s.after_snippet:
                    lines.append("   ```")
                    lines.append(f"   # Before: {s.before_snippet[:60]}...")
                    lines.append(f"   # After:  {s.after_snippet[:60]}...")
                    lines.append("   ```")
                
                effort_icon = {"low": "⚡", "medium": "⏱️", "high": "🔧"}.get(s.effort, "")
                lines.append(f"   Effort: {effort_icon} {s.effort} | Impact: {s.impact}")
                lines.append("")
        
        # Quick wins section
        quick_wins = self.quick_wins
        if quick_wins:
            lines.append("---")
            lines.append(f"### ⚡ Quick Wins ({len(quick_wins)} low-effort improvements)")
            for s in quick_wins[:5]:
                lines.append(f"- {s.title} in `{s.file}`")
        
        if self.summary:
            lines.append("")
            lines.append(f"**Summary**: {self.summary}")
        
        return "\n".join(lines)


REFACTOR_SYSTEM_PROMPT = """You are an expert software architect specializing in code refactoring and improvement.

Analyze the provided code and suggest refactoring opportunities. Focus on:

1. **Structure**: Module organization, separation of concerns, dependency management
2. **Simplification**: Reduce cyclomatic complexity, eliminate duplication, simplify logic
3. **Performance**: Algorithmic improvements, caching opportunities, lazy evaluation
4. **Readability**: Better naming, clearer abstractions, documentation
5. **Patterns**: Apply appropriate design patterns, use language idioms
6. **Modernization**: Use newer language features, updated APIs
7. **Testability**: Dependency injection, pure functions, smaller units
8. **Safety**: Better error handling, type safety, input validation

For each suggestion provide:
- Category: structure|simplification|performance|readability|patterns|modernization|testability|safety
- Priority: high (should fix) | medium (should consider) | low (nice to have)
- Location: file path and line range
- Title: Short, actionable title
- Description: Clear explanation of why and how
- Before/After: Code snippets showing the change (if applicable)
- Effort: low (< 30 min) | medium (1-4 hours) | high (> 4 hours)
- Impact: low | medium | high

Be practical and actionable. Avoid trivial style changes. Focus on changes that provide real value.

Respond in this format:

SUGGESTIONS:
---
category: <category>
priority: <priority>
file: <filepath>
lines: <start>-<end> or null
title: <short title>
description: <explanation>
before: <code snippet or null>
after: <code snippet or null>
effort: <effort>
impact: <impact>
---

METRICS:
complexity_score: <1-10>
maintainability_score: <1-10>
test_coverage_estimate: <percentage>

SUMMARY: <one paragraph summary>"""


class RefactorAnalyzer:
    """Analyzes code and provides refactoring suggestions"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self._client: Optional[httpx.AsyncClient] = None
    
    def _get_client(self) -> httpx.AsyncClient:
        if not self.api_key:
            raise ValueError("OpenRouter API key not found. Set OPENROUTER_API_KEY.")
        
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=180.0,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/codesm",
                    "X-Title": "codesm-refactor",
                },
            )
        return self._client
    
    async def analyze_files(
        self, 
        files: list[dict], 
        focus: Optional[list[str]] = None,
        context: Optional[str] = None
    ) -> RefactorAnalysis:
        """Analyze files and suggest refactorings.
        
        Args:
            files: List of dicts with 'path' and 'content'
            focus: Optional list of categories to focus on
            context: Optional context about the codebase/project
        
        Returns:
            RefactorAnalysis with suggestions
        """
        if not files:
            return RefactorAnalysis(summary="No files to analyze")
        
        prompt_parts = []
        
        if context:
            prompt_parts.append(f"Context: {context}\n\n")
        
        if focus:
            prompt_parts.append(f"Focus on these categories: {', '.join(focus)}\n\n")
        
        prompt_parts.append("Analyze the following files for refactoring opportunities:\n\n")
        
        for f in files:
            path = f.get("path", "unknown")
            content = f.get("content", "")
            
            # Limit content size
            lines = content.split("\n")
            if len(lines) > 500:
                content = "\n".join(lines[:500]) + "\n# ... (truncated)"
            
            prompt_parts.append(f"## File: {path}\n```\n{content}\n```\n\n")
        
        prompt = "".join(prompt_parts)
        
        try:
            client = self._get_client()
            
            response = await client.post(
                OPENROUTER_URL,
                json={
                    "model": REFACTOR_MODEL,
                    "messages": [
                        {"role": "system", "content": REFACTOR_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 8192,
                },
            )
            
            if response.status_code != 200:
                logger.error(f"API error: {response.status_code} - {response.text}")
                return RefactorAnalysis(
                    summary=f"Analysis failed: API error {response.status_code}",
                    files_analyzed=[f.get("path", "") for f in files],
                )
            
            data = response.json()
            result_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            return self._parse_response(result_text, [f.get("path", "") for f in files])
        
        except Exception as e:
            logger.error(f"Refactor analysis failed: {e}")
            return RefactorAnalysis(
                summary=f"Analysis failed: {e}",
                files_analyzed=[f.get("path", "") for f in files],
            )
    
    async def analyze_directory(
        self,
        directory: Path,
        patterns: Optional[list[str]] = None,
        exclude: Optional[list[str]] = None,
        max_files: int = 10,
        focus: Optional[list[str]] = None,
    ) -> RefactorAnalysis:
        """Analyze files in a directory.
        
        Args:
            directory: Directory to analyze
            patterns: Glob patterns to include (default: common source files)
            exclude: Patterns to exclude
            max_files: Maximum number of files to analyze
            focus: Categories to focus on
        """
        if patterns is None:
            patterns = ["**/*.py", "**/*.ts", "**/*.js", "**/*.go", "**/*.rs"]
        
        if exclude is None:
            exclude = ["**/node_modules/**", "**/.git/**", "**/__pycache__/**", 
                      "**/venv/**", "**/.venv/**", "**/dist/**", "**/build/**"]
        
        files_to_analyze = []
        
        for pattern in patterns:
            for path in directory.glob(pattern):
                if not path.is_file():
                    continue
                
                # Check exclusions
                path_str = str(path)
                if any(Path(path_str).match(ex) for ex in exclude):
                    continue
                
                try:
                    content = path.read_text()
                    files_to_analyze.append({
                        "path": str(path.relative_to(directory)),
                        "content": content,
                    })
                except Exception as e:
                    logger.warning(f"Could not read {path}: {e}")
                
                if len(files_to_analyze) >= max_files:
                    break
            
            if len(files_to_analyze) >= max_files:
                break
        
        if not files_to_analyze:
            return RefactorAnalysis(summary="No files found matching patterns")
        
        return await self.analyze_files(files_to_analyze, focus=focus)
    
    async def analyze_function(
        self,
        code: str,
        language: str = "python",
        function_name: Optional[str] = None,
    ) -> RefactorAnalysis:
        """Analyze a single function or code block."""
        files = [{"path": f"{function_name or 'snippet'}.{language}", "content": code}]
        return await self.analyze_files(files)
    
    def _parse_response(self, text: str, files: list[str]) -> RefactorAnalysis:
        """Parse the model response into RefactorAnalysis"""
        suggestions = []
        summary = ""
        metrics = {}
        
        # Parse suggestions
        suggestion_blocks = re.split(r'^---$', text, flags=re.MULTILINE)
        
        for block in suggestion_blocks:
            block = block.strip()
            if not block or block.startswith("SUGGESTIONS:"):
                continue
            
            # Parse individual suggestion
            suggestion = self._parse_suggestion_block(block, files)
            if suggestion:
                suggestions.append(suggestion)
        
        # Parse metrics
        metrics_match = re.search(
            r'METRICS:\s*\n(.*?)(?=SUMMARY:|$)', 
            text, 
            re.DOTALL | re.IGNORECASE
        )
        if metrics_match:
            metrics_text = metrics_match.group(1)
            for line in metrics_text.strip().split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip().lower().replace(" ", "_")
                    value = value.strip()
                    try:
                        if "." in value or "%" in value:
                            metrics[key] = float(value.replace("%", ""))
                        else:
                            metrics[key] = int(value)
                    except ValueError:
                        metrics[key] = value
        
        # Parse summary
        summary_match = re.search(r'SUMMARY:\s*(.+?)$', text, re.DOTALL | re.IGNORECASE)
        if summary_match:
            summary = summary_match.group(1).strip()
        
        return RefactorAnalysis(
            suggestions=suggestions,
            files_analyzed=files,
            summary=summary,
            metrics=metrics,
        )
    
    def _parse_suggestion_block(
        self, 
        block: str, 
        files: list[str]
    ) -> Optional[RefactorSuggestion]:
        """Parse a single suggestion block"""
        def extract_field(name: str) -> Optional[str]:
            pattern = rf'^{name}:\s*(.+?)(?=\n\w+:|$)'
            match = re.search(pattern, block, re.MULTILINE | re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
            return None
        
        category_str = extract_field("category")
        if not category_str:
            return None
        
        try:
            category = RefactorCategory(category_str.lower())
        except ValueError:
            category = RefactorCategory.STRUCTURE
        
        # Parse line range
        lines_str = extract_field("lines")
        start_line, end_line = None, None
        if lines_str and lines_str.lower() != "null":
            if "-" in lines_str:
                parts = lines_str.split("-")
                try:
                    start_line = int(parts[0].strip())
                    end_line = int(parts[1].strip())
                except ValueError:
                    pass
            else:
                try:
                    start_line = int(lines_str.strip())
                except ValueError:
                    pass
        
        before_code = extract_field("before")
        after_code = extract_field("after")
        
        if before_code and before_code.lower() == "null":
            before_code = None
        if after_code and after_code.lower() == "null":
            after_code = None
        
        return RefactorSuggestion(
            category=category,
            priority=extract_field("priority") or "medium",
            file=extract_field("file") or (files[0] if files else "unknown"),
            start_line=start_line,
            end_line=end_line,
            title=extract_field("title") or "Refactoring suggestion",
            description=extract_field("description") or "",
            before_snippet=before_code,
            after_snippet=after_code,
            effort=extract_field("effort") or "medium",
            impact=extract_field("impact") or "medium",
        )
    
    async def close(self):
        """Close the HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None


# Convenience functions for quick analysis

async def suggest_refactorings(
    path: str | Path,
    focus: Optional[list[str]] = None,
) -> RefactorAnalysis:
    """Quick refactoring analysis for a file or directory."""
    analyzer = RefactorAnalyzer()
    path = Path(path)
    
    try:
        if path.is_dir():
            return await analyzer.analyze_directory(path, focus=focus)
        else:
            content = path.read_text()
            return await analyzer.analyze_files(
                [{"path": str(path), "content": content}],
                focus=focus,
            )
    finally:
        await analyzer.close()


async def quick_refactor_check(code: str, language: str = "python") -> str:
    """Quick refactoring check for a code snippet."""
    analyzer = RefactorAnalyzer()
    try:
        result = await analyzer.analyze_function(code, language)
        return result.format_for_display()
    finally:
        await analyzer.close()
