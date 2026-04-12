"""Diagnostics tool using LSP"""

from pathlib import Path
from typing import Optional
from .base import Tool
from codesm.util.citations import file_link_with_path


class DiagnosticsTool(Tool):
    name = "diagnostics"
    description = "Get code diagnostics (errors, warnings) from language servers for a file or project."
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional absolute path to file. If not provided, returns all diagnostics.",
                },
                "severity": {
                    "type": "string",
                    "enum": ["error", "warning", "all"],
                    "description": "Filter by severity. Defaults to 'all'.",
                },
            },
            "required": [],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        from codesm import lsp
        
        file_path: Optional[str] = args.get("path")
        severity_filter: str = args.get("severity", "all")
        
        # If a specific file is requested, touch it to get fresh diagnostics
        if file_path:
            path = Path(file_path)
            if not path.exists():
                return f"Error: File not found: {file_path}"
            diagnostics = await lsp.touch_file(str(path))
        else:
            diagnostics = lsp.diagnostics()
        
        # Filter by severity
        if severity_filter == "error":
            diagnostics = [d for d in diagnostics if d.severity == "error"]
        elif severity_filter == "warning":
            diagnostics = [d for d in diagnostics if d.severity in ("error", "warning")]
        
        if not diagnostics:
            if file_path:
                return f"No diagnostics for {file_path}"
            return "No diagnostics found"
        
        return format_diagnostics(diagnostics)


def format_diagnostics(diagnostics: list) -> str:
    """Format diagnostics for display with clickable file links."""
    lines = []
    for d in diagnostics:
        severity_label = {"error": "[error]", "warning": "[warn]", "info": "[info]", "hint": "[hint]"}.get(d.severity, "[note]")
        source_str = f" [{d.source}]" if d.source else ""
        file_link = file_link_with_path(d.path, d.line)
        lines.append(f"{severity_label} {file_link}:{d.column}{source_str}")
        lines.append(f"   {d.message}")
    return "\n".join(lines)
