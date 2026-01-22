"""Refactor Tool - Proactive code improvement recommendations"""

import logging
from pathlib import Path
from typing import Optional

from .base import Tool

logger = logging.getLogger(__name__)


class RefactorTool(Tool):
    name = "refactor"
    description = """Analyze code and suggest refactoring improvements.

Provides proactive recommendations for:
- **Structure**: Module organization, separation of concerns
- **Simplification**: Reduce complexity, eliminate duplication
- **Performance**: Algorithmic improvements, caching
- **Readability**: Better naming, clearer abstractions
- **Patterns**: Design patterns, language idioms
- **Modernization**: Newer language features, updated APIs
- **Testability**: Dependency injection, pure functions
- **Safety**: Error handling, type safety

Use this tool to:
- Review files before committing to catch improvement opportunities
- Analyze legacy code for modernization
- Find quick wins (low effort, high impact changes)
- Identify technical debt

Examples:
- Analyze a single file: {"path": "src/utils.py"}
- Analyze multiple files: {"paths": ["src/auth.py", "src/user.py"]}
- Focus on performance: {"path": "src/data.py", "focus": ["performance", "simplification"]}
- Analyze a directory: {"directory": "src/api", "max_files": 5}
- Analyze code snippet: {"code": "def foo():\\n    pass", "language": "python"}"""
    
    def __init__(self, parent_tools=None):
        super().__init__()
        self._parent_tools = parent_tools
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Single file path to analyze",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Multiple file paths to analyze together",
                },
                "directory": {
                    "type": "string",
                    "description": "Directory to analyze (will pick relevant files)",
                },
                "code": {
                    "type": "string",
                    "description": "Code snippet to analyze directly",
                },
                "language": {
                    "type": "string",
                    "description": "Language of the code snippet (python, typescript, etc.)",
                    "default": "python",
                },
                "focus": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Categories to focus on: structure, simplification, performance, readability, patterns, modernization, testability, safety",
                },
                "max_files": {
                    "type": "integer",
                    "description": "Maximum files to analyze from directory (default: 10)",
                    "default": 10,
                },
                "patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Glob patterns for directory analysis (default: common source files)",
                },
                "context": {
                    "type": "string",
                    "description": "Additional context about the codebase or what to look for",
                },
                "quick_wins_only": {
                    "type": "boolean",
                    "description": "Only show low-effort, high-impact suggestions",
                    "default": False,
                },
            },
            "oneOf": [
                {"required": ["path"]},
                {"required": ["paths"]},
                {"required": ["directory"]},
                {"required": ["code"]},
            ],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        from ..review.refactor import RefactorAnalyzer, RefactorCategory
        
        cwd = Path(context.get("cwd", Path.cwd()))
        focus = args.get("focus")
        extra_context = args.get("context")
        quick_wins_only = args.get("quick_wins_only", False)
        
        analyzer = RefactorAnalyzer()
        
        try:
            # Determine analysis mode
            if args.get("code"):
                # Analyze code snippet directly
                code = args["code"]
                language = args.get("language", "python")
                result = await analyzer.analyze_function(code, language)
            
            elif args.get("directory"):
                # Analyze directory
                directory = Path(args["directory"])
                if not directory.is_absolute():
                    directory = cwd / directory
                
                if not directory.exists():
                    return f"Error: Directory not found: {directory}"
                
                result = await analyzer.analyze_directory(
                    directory,
                    patterns=args.get("patterns"),
                    max_files=args.get("max_files", 10),
                    focus=focus,
                )
            
            elif args.get("paths"):
                # Analyze multiple files
                files = []
                for p in args["paths"]:
                    path = Path(p)
                    if not path.is_absolute():
                        path = cwd / path
                    
                    if not path.exists():
                        continue
                    
                    try:
                        content = path.read_text()
                        files.append({
                            "path": str(path.relative_to(cwd)) if path.is_relative_to(cwd) else str(path),
                            "content": content,
                        })
                    except Exception as e:
                        logger.warning(f"Could not read {path}: {e}")
                
                if not files:
                    return "Error: No valid files found"
                
                result = await analyzer.analyze_files(files, focus=focus, context=extra_context)
            
            elif args.get("path"):
                # Analyze single file
                path = Path(args["path"])
                if not path.is_absolute():
                    path = cwd / path
                
                if not path.exists():
                    return f"Error: File not found: {path}"
                
                try:
                    content = path.read_text()
                except Exception as e:
                    return f"Error reading file: {e}"
                
                rel_path = str(path.relative_to(cwd)) if path.is_relative_to(cwd) else str(path)
                result = await analyzer.analyze_files(
                    [{"path": rel_path, "content": content}],
                    focus=focus,
                    context=extra_context,
                )
            
            else:
                return "Error: Must provide path, paths, directory, or code"
            
            # Filter to quick wins if requested
            if quick_wins_only and result.suggestions:
                quick_wins = result.quick_wins
                if quick_wins:
                    result.suggestions = quick_wins
                    result.summary = f"Showing {len(quick_wins)} quick wins (low effort, high impact)"
            
            return result.format_for_display()
        
        except Exception as e:
            logger.error(f"Refactoring analysis failed: {e}")
            return f"Error: Refactoring analysis failed: {e}"
        
        finally:
            await analyzer.close()


class RefactorApplyTool(Tool):
    """Tool to apply a specific refactoring suggestion"""
    
    name = "refactor_apply"
    description = """Apply a refactoring suggestion to code.

Takes a refactoring suggestion (from the refactor tool) and applies it to the code.
Generates the specific code changes needed.

Use after running 'refactor' to implement the suggested improvements."""
    
    def __init__(self, parent_tools=None):
        super().__init__()
        self._parent_tools = parent_tools
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to refactor",
                },
                "suggestion": {
                    "type": "string",
                    "description": "Description of the refactoring to apply (from refactor tool output)",
                },
                "start_line": {
                    "type": "integer",
                    "description": "Start line of code to refactor",
                },
                "end_line": {
                    "type": "integer",
                    "description": "End line of code to refactor",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, only show what would change without applying",
                    "default": True,
                },
            },
            "required": ["path", "suggestion"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        import os
        import httpx
        
        cwd = Path(context.get("cwd", Path.cwd()))
        path = Path(args["path"])
        if not path.is_absolute():
            path = cwd / path
        
        if not path.exists():
            return f"Error: File not found: {path}"
        
        try:
            content = path.read_text()
        except Exception as e:
            return f"Error reading file: {e}"
        
        suggestion = args["suggestion"]
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        dry_run = args.get("dry_run", True)
        
        # Extract the relevant code section if lines specified
        lines = content.split("\n")
        if start_line and end_line:
            target_code = "\n".join(lines[start_line-1:end_line])
            context_before = "\n".join(lines[max(0, start_line-6):start_line-1])
            context_after = "\n".join(lines[end_line:min(len(lines), end_line+5)])
        else:
            target_code = content
            context_before = ""
            context_after = ""
        
        # Use LLM to generate the refactored code
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return "Error: OPENROUTER_API_KEY not set"
        
        prompt = f"""Apply this refactoring suggestion to the code:

SUGGESTION: {suggestion}

{f'CONTEXT BEFORE:\\n```\\n{context_before}\\n```\\n' if context_before else ''}

CODE TO REFACTOR:
```
{target_code}
```

{f'CONTEXT AFTER:\\n```\\n{context_after}\\n```\\n' if context_after else ''}

Provide the refactored code that implements this suggestion.
Only output the refactored code, no explanations.
Maintain the same indentation and style as the original."""

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "anthropic/claude-sonnet-4-20250514",
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 4096,
                },
            )
            
            if response.status_code != 200:
                return f"Error: API error {response.status_code}"
            
            data = response.json()
            refactored = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        # Clean up the response (remove markdown code blocks if present)
        refactored = refactored.strip()
        if refactored.startswith("```"):
            lines = refactored.split("\n")
            refactored = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        
        if dry_run:
            output = ["## Refactoring Preview (Dry Run)", ""]
            output.append(f"**File**: {path}")
            if start_line and end_line:
                output.append(f"**Lines**: {start_line}-{end_line}")
            output.append(f"**Suggestion**: {suggestion}")
            output.append("")
            output.append("### Original Code:")
            output.append(f"```\n{target_code}\n```")
            output.append("")
            output.append("### Refactored Code:")
            output.append(f"```\n{refactored}\n```")
            output.append("")
            output.append("*Run with dry_run=false to apply these changes*")
            return "\n".join(output)
        
        # Apply the changes
        if start_line and end_line:
            new_lines = lines[:start_line-1] + refactored.split("\n") + lines[end_line:]
            new_content = "\n".join(new_lines)
        else:
            new_content = refactored
        
        try:
            path.write_text(new_content)
            return f"✓ Refactoring applied to {path}\n\nNew code:\n```\n{refactored}\n```"
        except Exception as e:
            return f"Error writing file: {e}"
