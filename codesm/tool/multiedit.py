"""Multi-edit tool for batch file edits"""

from pathlib import Path
from .base import Tool
from .edit import EditTool


class MultiEditTool(Tool):
    name = "multiedit"
    description = "Make multiple edits to a single file in one atomic operation."

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to modify",
                },
                "edits": {
                    "type": "array",
                    "description": "Array of edit operations to perform sequentially",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_content": {
                                "type": "string",
                                "description": "Exact content to replace (must match exactly)",
                            },
                            "new_content": {
                                "type": "string",
                                "description": "New content to insert",
                            },
                            "replace_all": {
                                "type": "boolean",
                                "description": "Replace all occurrences (default: false)",
                                "default": False,
                            },
                        },
                        "required": ["old_content", "new_content"],
                    },
                },
            },
            "required": ["path", "edits"],
        }

    async def execute(self, args: dict, context: dict) -> str:
        path = Path(args["path"])
        edits = args["edits"]

        if not path.exists():
            return f"Error: File not found: {path}"

        if not edits:
            return "Error: No edits provided"

        try:
            # Read original content
            original_content = path.read_text()
            content = original_content

            # Get session and snapshot for undo tracking
            session = context.get("session")
            pre_edit_hash = None
            if session:
                pre_edit_hash = await session.track_snapshot()

            # Validate all edits first (dry run)
            validation_errors = []
            test_content = content
            for i, edit in enumerate(edits):
                old_content = edit["old_content"]
                new_content = edit["new_content"]

                if old_content == new_content:
                    validation_errors.append(
                        f"Edit {i + 1}: old_content and new_content are identical"
                    )
                    continue

                if old_content not in test_content:
                    validation_errors.append(
                        f"Edit {i + 1}: Could not find old_content in file"
                    )
                    continue

                # Apply edit to test content for next iteration
                replace_all = edit.get("replace_all", False)
                if replace_all:
                    test_content = test_content.replace(old_content, new_content)
                else:
                    test_content = test_content.replace(old_content, new_content, 1)

            if validation_errors:
                return "Validation failed (no changes applied):\n" + "\n".join(
                    validation_errors
                )

            # All edits valid, apply them for real
            results = []
            total_added = 0
            total_removed = 0
            total_modified = 0

            for i, edit in enumerate(edits):
                old_content = edit["old_content"]
                new_content = edit["new_content"]
                replace_all = edit.get("replace_all", False)

                # Count occurrences
                occurrences = content.count(old_content)
                if replace_all:
                    content = content.replace(old_content, new_content)
                    applied_count = occurrences
                else:
                    content = content.replace(old_content, new_content, 1)
                    applied_count = 1

                # Calculate line stats
                old_lines = old_content.count("\n") + 1
                new_lines = new_content.count("\n") + 1
                
                if new_lines > old_lines:
                    total_added += (new_lines - old_lines) * applied_count
                elif old_lines > new_lines:
                    total_removed += (old_lines - new_lines) * applied_count
                total_modified += min(old_lines, new_lines) * applied_count

                results.append(
                    f"  {i + 1}. Replaced {applied_count} occurrence(s)"
                )

            # Write the final content
            path.write_text(content)

            # Record multiedit in undo history
            if session:
                history = session.get_undo_history()
                history.record_edit(
                    file_path=str(path),
                    before_content=original_content,
                    after_content=content,
                    tool_name="multiedit",
                    description=f"multiedit {path.name} ({len(edits)} edits)",
                    snapshot_hash=pre_edit_hash,
                )

            # Build stats string
            stats_parts = []
            if total_added > 0:
                stats_parts.append(f"+{total_added}")
            if total_modified > 0:
                stats_parts.append(f"~{total_modified}")
            if total_removed > 0:
                stats_parts.append(f"-{total_removed}")
            stats = " ".join(stats_parts) if stats_parts else "~0"

            # Generate combined diff
            diff_output = self._generate_combined_diff(path, original_content, content)

            result = f"**MultiEdit** {path.name} ({len(edits)} edits) {stats}\n"
            result += "\n".join(results)
            result += f"\n\n{diff_output}"

            # Get diagnostics
            diagnostics_output = await self._get_diagnostics(str(path))
            if diagnostics_output:
                result += f"\n\n{diagnostics_output}"

            return result

        except Exception as e:
            return f"Error performing multi-edit: {e}"

    def _generate_combined_diff(
        self, path: Path, old_content: str, new_content: str
    ) -> str:
        """Generate a unified diff showing all changes."""
        import difflib

        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=str(path),
            tofile=str(path),
            lineterm="",
        )

        diff_text = "".join(diff)
        if not diff_text:
            return "```diff\n(no changes)\n```"

        return f"```diff\n{diff_text}\n```"

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
