"""Write file tool"""

from pathlib import Path
from .base import Tool
from codesm.util.citations import file_link_with_path


class WriteTool(Tool):
    name = "write"
    description = "Create or overwrite a file with the given content."
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write",
                },
            },
            "required": ["path", "content"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        path = Path(args["path"])
        content = args["content"]
        
        try:
            session = context.get("session")
            pre_write_hash = None
            if session:
                pre_write_hash = await session.track_snapshot()
            
            old_content = ""
            is_new_file = not path.exists()
            if not is_new_file:
                old_content = path.read_text()
            
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            
            # Record write in undo history
            if session:
                history = session.get_undo_history()
                history.record_edit(
                    file_path=str(path),
                    before_content=old_content,
                    after_content=content,
                    tool_name="write",
                    description=f"write {path.name}" if is_new_file else f"overwrite {path.name}",
                    snapshot_hash=pre_write_hash,
                )
            
            # Generate diff output
            new_lines = content.split('\n')
            if is_new_file:
                # New file - show all lines as added
                diff_lines = []
                for i, line in enumerate(new_lines[:20]):  # Limit preview
                    diff_lines.append(f"+ {i+1:3d}    {line}")
                if len(new_lines) > 20:
                    diff_lines.append(f"  ... ({len(new_lines) - 20} more lines)")
                diff_output = "```diff\n" + '\n'.join(diff_lines) + "\n```"
                file_link = file_link_with_path(path)
                result = f"**Write** {file_link} +{len(new_lines)} lines (new file)\n\n{diff_output}"
            else:
                # Existing file - show diff
                diff_output = self._generate_diff(old_content, content)
                old_lines = old_content.split('\n')
                added = len(new_lines) - len(old_lines) if len(new_lines) > len(old_lines) else 0
                removed = len(old_lines) - len(new_lines) if len(old_lines) > len(new_lines) else 0
                file_link = file_link_with_path(path)
                result = f"**Write** {file_link} +{added} -{removed}\n\n{diff_output}"
            
            diagnostics_output = await self._get_diagnostics(str(path))
            if diagnostics_output:
                result += f"\n\n{diagnostics_output}"
            
            if session and pre_write_hash:
                patch = await session.get_file_changes(pre_write_hash)
                if patch.get("files"):
                    context["_last_patch"] = patch
            
            return result
        except Exception as e:
            return f"Error writing file: {e}"
    
    def _generate_diff(self, old_content: str, new_content: str) -> str:
        """Generate a unified diff between old and new content."""
        import difflib
        
        old_lines = old_content.split('\n')
        new_lines = new_content.split('\n')
        
        diff_lines = []
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        
        line_count = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if line_count > 30:  # Limit output
                diff_lines.append("  ... (diff truncated)")
                break
                
            if tag == 'equal':
                # Show just first/last context lines for equal blocks
                if i2 - i1 > 4:
                    for idx in range(i1, min(i1 + 2, i2)):
                        diff_lines.append(f"  {idx+1:3d}    {old_lines[idx]}")
                    diff_lines.append(f"  ...    ({i2 - i1 - 4} unchanged lines)")
                    for idx in range(max(i1 + 2, i2 - 2), i2):
                        diff_lines.append(f"  {idx+1:3d}    {old_lines[idx]}")
                else:
                    for idx in range(i1, i2):
                        diff_lines.append(f"  {idx+1:3d}    {old_lines[idx]}")
                line_count += min(i2 - i1, 4)
            elif tag == 'replace':
                for idx in range(i1, i2):
                    diff_lines.append(f"- {idx+1:3d}    {old_lines[idx]}")
                    line_count += 1
                for idx in range(j1, j2):
                    diff_lines.append(f"+ {idx+1:3d}    {new_lines[idx]}")
                    line_count += 1
            elif tag == 'delete':
                for idx in range(i1, i2):
                    diff_lines.append(f"- {idx+1:3d}    {old_lines[idx]}")
                    line_count += 1
            elif tag == 'insert':
                for idx in range(j1, j2):
                    diff_lines.append(f"+ {idx+1:3d}    {new_lines[idx]}")
                    line_count += 1
        
        return "```diff\n" + '\n'.join(diff_lines) + "\n```"
    
    async def _get_diagnostics(self, path: str) -> str:
        """Get diagnostics for a file after writing."""
        try:
            from codesm import lsp
            from .diagnostics import format_diagnostics
            
            diagnostics = await lsp.touch_file(path)
            errors = [d for d in diagnostics if d.severity == "error"]
            
            if errors:
                return f"⚠️ Errors detected:\n{format_diagnostics(errors)}"
            return ""
        except Exception:
            return ""
