"""Edit file tool"""

from pathlib import Path
from .base import Tool
from codesm.util.citations import file_link_with_path


class EditTool(Tool):
    name = "edit"
    description = "Edit a file by replacing old content with new content."

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file",
                },
                "old_content": {
                    "type": "string",
                    "description": "Exact content to replace (must match exactly)",
                },
                "new_content": {
                    "type": "string",
                    "description": "New content to insert",
                },
            },
            "required": ["path", "old_content", "new_content"],
        }

    async def execute(self, args: dict, context: dict) -> str:
        path = Path(args["path"])
        old_content = args["old_content"]
        new_content = args["new_content"]

        if not path.exists():
            return f"Error: File not found: {path}"

        try:
            content = path.read_text()

            if old_content not in content:
                return f"Error: Could not find the specified content to replace"

            session = context.get("session")
            pre_edit_hash = None
            if session:
                pre_edit_hash = await session.track_snapshot()

            updated = content.replace(old_content, new_content, 1)

            # Calculate lines added/removed/modified
            old_lines = old_content.split('\n')
            new_lines = new_content.split('\n')
            lines_added = len(new_lines) - len(old_lines) if len(new_lines) > len(old_lines) else 0
            lines_removed = len(old_lines) - len(new_lines) if len(old_lines) > len(new_lines) else 0
            lines_modified = min(len(old_lines), len(new_lines))

            # Write the file
            path.write_text(updated)

            # Record edit in undo history
            if session:
                history = session.get_undo_history()
                history.record_edit(
                    file_path=str(path),
                    before_content=content,
                    after_content=updated,
                    tool_name="edit",
                    description=f"edit {path.name}",
                    snapshot_hash=pre_edit_hash,
                )

            # Generate styled diff for display
            diff_output = self._generate_styled_diff(path, old_content, new_content, content)

            # Format result with stats like Amp: +added ~modified -removed
            stats_parts = []
            if lines_added > 0:
                stats_parts.append(f"+{lines_added}")
            if lines_modified > 0:
                stats_parts.append(f"~{lines_modified}")
            if lines_removed > 0:
                stats_parts.append(f"-{lines_removed}")
            stats = " ".join(stats_parts) if stats_parts else "+0 -0"

            file_link = file_link_with_path(path, start_line + 1 if 'start_line' in dir() else None)
            result = f"**Edit** {file_link} {stats} Diff:\n\n{diff_output}"

            # Get LSP diagnostics for the edited file
            diagnostics_output = await self._get_diagnostics(str(path))
            if diagnostics_output:
                result += f"\n\n{diagnostics_output}"

            if session and pre_edit_hash:
                patch = await session.get_file_changes(pre_edit_hash)
                if patch.get("files"):
                    context["_last_patch"] = patch

            return result
        except Exception as e:
            return f"Error editing file: {e}"
    
    def _generate_styled_diff(
        self, path: Path, old_content: str, new_content: str, full_content: str
    ) -> str:
        """Generate a unified diff in standard format for syntax highlighting."""
        import difflib
        
        file_lines = full_content.split('\n')
        old_lines = old_content.split('\n')
        new_lines = new_content.split('\n')

        # Find where the old content starts in the file
        start_line = 0
        for i in range(len(file_lines)):
            if i + len(old_lines) <= len(file_lines):
                match_lines = file_lines[i:i + len(old_lines)]
                if '\n'.join(match_lines) == old_content:
                    start_line = i
                    break

        # Build proper unified diff format
        diff_lines = []
        context_before = 2
        context_after = 2

        # Context lines before (no prefix, just indented)
        for i in range(max(0, start_line - context_before), start_line):
            line_num = i + 1
            diff_lines.append(f"  {line_num:3d}    {file_lines[i]}")

        # Use sequence matching for proper diff
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        
        old_idx = start_line + 1
        new_idx = start_line + 1

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                for line in old_lines[i1:i2]:
                    diff_lines.append(f"  {old_idx:3d}    {line}")
                    old_idx += 1
                    new_idx += 1
            elif tag == 'replace':
                for line in old_lines[i1:i2]:
                    diff_lines.append(f"- {old_idx:3d}    {line}")
                    old_idx += 1
                for line in new_lines[j1:j2]:
                    diff_lines.append(f"+ {new_idx:3d}    {line}")
                    new_idx += 1
            elif tag == 'delete':
                for line in old_lines[i1:i2]:
                    diff_lines.append(f"- {old_idx:3d}    {line}")
                    old_idx += 1
            elif tag == 'insert':
                for line in new_lines[j1:j2]:
                    diff_lines.append(f"+ {new_idx:3d}    {line}")
                    new_idx += 1

        # Context lines after
        end_line = start_line + len(old_lines)
        for i in range(end_line, min(len(file_lines), end_line + context_after)):
            line_num = i + 1
            diff_lines.append(f"  {line_num:3d}    {file_lines[i]}")

        return "```diff\n" + '\n'.join(diff_lines) + "\n```"

    def _generate_unified_diff(
        self, path: Path, old_content: str, new_content: str, full_content: str
    ) -> str:
        """Generate a unified diff showing line numbers and changes (legacy)."""
        file_lines = full_content.split('\n')
        old_lines = old_content.split('\n')
        new_lines = new_content.split('\n')

        start_line = 0
        for i in range(len(file_lines)):
            if i + len(old_lines) <= len(file_lines):
                match_lines = file_lines[i:i + len(old_lines)]
                if '\n'.join(match_lines) == old_content:
                    start_line = i
                    break

        diff_lines = []
        context_before = 2
        context_after = 2

        for i in range(max(0, start_line - context_before), start_line):
            line_num = i + 1
            diff_lines.append(f"{line_num:4d}     {file_lines[i]}")

        for i, line in enumerate(old_lines):
            line_num = start_line + i + 1
            diff_lines.append(f"{line_num:4d} -   {line}")

        for i, line in enumerate(new_lines):
            line_num = start_line + i + 1
            diff_lines.append(f"{line_num:4d} +   {line}")

        end_line = start_line + len(old_lines)
        for i in range(end_line, min(len(file_lines), end_line + context_after)):
            line_num = i + 1
            diff_lines.append(f"{line_num:4d}     {file_lines[i]}")

        return '\n'.join(diff_lines)

    def _generate_diff_display(
        self, path: Path, old_content: str, new_content: str, full_content: str
    ) -> str:
        """Generate a unified diff display for the permission request."""
        # Find the line numbers where the change occurs
        lines_before = full_content.split('\n')
        old_lines = old_content.split('\n')
        new_lines = new_content.split('\n')

        # Find the starting line number
        start_line = 0
        for i, line in enumerate(lines_before):
            if old_content.startswith(line):
                # Check if this is the start of our old content
                check_lines = '\n'.join(lines_before[i:i+len(old_lines)])
                if old_content in check_lines or check_lines.startswith(old_content.split('\n')[0]):
                    start_line = i + 1  # 1-indexed
                    break

        # Detect file extension for syntax highlighting
        extension = path.suffix.lstrip('.')
        lang_map = {
            'py': 'python', 'js': 'javascript', 'ts': 'typescript',
            'tsx': 'tsx', 'jsx': 'jsx', 'rs': 'rust', 'go': 'go',
            'java': 'java', 'c': 'c', 'cpp': 'cpp', 'rb': 'ruby',
            'php': 'php', 'swift': 'swift', 'kt': 'kotlin',
        }
        lang = lang_map.get(extension, extension or 'text')

        # Build the diff display with colors
        diff_lines = [f"**Edit file** {path}"]
        diff_lines.append("")
        diff_lines.append(f"```diff")

        # Add removed lines (red, with - prefix)
        for i, line in enumerate(old_lines):
            line_num = start_line + i
            diff_lines.append(f"{line_num} - {line}")

        # Add added lines (green, with + prefix)
        for i, line in enumerate(new_lines):
            line_num = start_line + i
            diff_lines.append(f"{line_num} + {line}")

        diff_lines.append("```")

        return '\n'.join(diff_lines)

    def _format_edit_display(self, path: Path, old_content: str, new_content: str) -> str:
        """Format the edit for display in chat with syntax highlighting."""
        # Detect file extension for syntax highlighting
        extension = path.suffix.lstrip('.')

        # Map common extensions to language identifiers
        lang_map = {
            'py': 'python',
            'js': 'javascript',
            'ts': 'typescript',
            'tsx': 'tsx',
            'jsx': 'jsx',
            'rs': 'rust',
            'go': 'go',
            'java': 'java',
            'c': 'c',
            'cpp': 'cpp',
            'cc': 'cpp',
            'h': 'c',
            'hpp': 'cpp',
            'cs': 'csharp',
            'rb': 'ruby',
            'php': 'php',
            'swift': 'swift',
            'kt': 'kotlin',
            'md': 'markdown',
            'json': 'json',
            'yaml': 'yaml',
            'yml': 'yaml',
            'toml': 'toml',
            'sh': 'bash',
            'bash': 'bash',
            'css': 'css',
            'html': 'html',
            'xml': 'xml',
            'sql': 'sql',
        }

        lang = lang_map.get(extension, '')

        # Create the formatted output
        result = f"Edited `{path}`\n\n"

        # Show the old code (what was removed)
        result += f"**Before:**\n```{lang}\n{old_content}\n```\n\n"

        # Show the new code (what was added)
        result += f"**After:**\n```{lang}\n{new_content}\n```"

        return result

    async def _get_diagnostics(self, path: str) -> str:
        """Get diagnostics for a file after editing."""
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
