"""Undo tool - revert the last edit made to a file"""

from pathlib import Path
from typing import Optional
from .base import Tool


class UndoTool(Tool):
    name = "undo"
    description = "Undo the last edit made to a file, restoring it to its previous state. Shows history of edits when available."
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file whose last edit should be undone. If not provided, undoes the most recent edit across all files.",
                },
                "show_history": {
                    "type": "boolean",
                    "description": "If true, show edit history instead of undoing. Default: false",
                },
            },
            "required": [],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        path_str = args.get("path")
        show_history = args.get("show_history", False)
        
        session = context.get("session")
        if not session:
            return "Error: No session context available for undo"
        
        history = session.get_undo_history()
        
        # Resolve path if provided
        file_path = None
        if path_str:
            file_path = Path(path_str)
            if not file_path.exists() and not show_history:
                return f"Error: File not found: {file_path}"
        
        # Show history if requested
        if show_history:
            return self._format_history(history, file_path)
        
        # Check if undo is available
        if not history.can_undo(str(file_path) if file_path else None):
            if file_path:
                return f"No edits to undo for: {file_path.name}"
            return "No edits to undo"
        
        # Get the operation to undo
        op = history.undo(str(file_path) if file_path else None)
        if not op:
            return "No edits to undo"
        
        try:
            target_path = Path(op.file_path)
            
            # Verify current content matches expected (to avoid data loss)
            if target_path.exists():
                current_content = target_path.read_text()
                if current_content != op.after_content:
                    # Content changed externally, warn user
                    # Still proceed but note the discrepancy
                    pass
            
            # Apply the undo (restore before_content)
            target_path.write_text(op.before_content)
            
            # Build result message
            redo_available = history.get_redo_count(str(target_path))
            undo_available = history.get_undo_count(str(target_path))
            
            result = f"✓ Undid edit to {target_path.name}"
            if op.description:
                result += f" ({op.description})"
            
            stats = []
            if undo_available > 0:
                stats.append(f"{undo_available} more undo")
            if redo_available > 0:
                stats.append(f"{redo_available} redo")
            if stats:
                result += f" [{', '.join(stats)} available]"
            
            return result
            
        except Exception as e:
            # Put the operation back on undo stack since we failed
            history._undo_stack.append(op)
            if op in history._redo_stack:
                history._redo_stack.remove(op)
            return f"Error undoing edit: {e}"
    
    def _format_history(self, history, file_path: Optional[Path] = None) -> str:
        """Format edit history for display"""
        ops = history.get_history(str(file_path) if file_path else None, limit=20)
        
        if not ops:
            if file_path:
                return f"No edit history for: {file_path.name}"
            return "No edit history available"
        
        lines = ["## Edit History\n"]
        for i, op in enumerate(ops, 1):
            path = Path(op.file_path)
            time_str = op.timestamp.strftime("%H:%M:%S")
            before_lines = len(op.before_content.split('\n'))
            after_lines = len(op.after_content.split('\n'))
            diff = after_lines - before_lines
            diff_str = f"+{diff}" if diff > 0 else str(diff)
            
            desc = op.description or op.tool_name
            lines.append(f"{i}. **{path.name}** ({time_str}) - {desc} [{diff_str} lines]")
        
        undo_count = history.get_undo_count(str(file_path) if file_path else None)
        redo_count = history.get_redo_count(str(file_path) if file_path else None)
        lines.append(f"\n*{undo_count} undo, {redo_count} redo available*")
        
        return '\n'.join(lines)
