"""Redo tool - redo an undone edit to a file"""

from pathlib import Path
from typing import Optional
from .base import Tool


class RedoTool(Tool):
    name = "redo"
    description = "Redo an undone edit to a file. Use after undo to restore the change."
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file whose last undo should be redone. If not provided, redoes the most recent undo across all files.",
                },
            },
            "required": [],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        path_str = args.get("path")
        
        session = context.get("session")
        if not session:
            return "Error: No session context available for redo"
        
        history = session.get_undo_history()
        
        # Resolve path if provided
        file_path = None
        if path_str:
            file_path = Path(path_str)
        
        # Check if redo is available
        if not history.can_redo(str(file_path) if file_path else None):
            if file_path:
                return f"No edits to redo for: {file_path.name}"
            return "No edits to redo"
        
        # Get the operation to redo
        op = history.redo(str(file_path) if file_path else None)
        if not op:
            return "No edits to redo"
        
        try:
            target_path = Path(op.file_path)
            
            # Apply the redo (restore after_content)
            target_path.write_text(op.after_content)
            
            # Build result message
            redo_available = history.get_redo_count(str(target_path))
            undo_available = history.get_undo_count(str(target_path))
            
            result = f"✓ Redid edit to {target_path.name}"
            if op.description:
                result += f" ({op.description})"
            
            stats = []
            if undo_available > 0:
                stats.append(f"{undo_available} undo")
            if redo_available > 0:
                stats.append(f"{redo_available} more redo")
            if stats:
                result += f" [{', '.join(stats)} available]"
            
            return result
            
        except Exception as e:
            # Put the operation back on redo stack since we failed
            history._redo_stack.append(op)
            if op in history._undo_stack:
                history._undo_stack.remove(op)
            return f"Error redoing edit: {e}"
