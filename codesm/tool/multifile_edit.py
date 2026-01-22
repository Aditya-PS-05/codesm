"""Multi-file atomic edit tool - Edit multiple files in a single transaction"""

import difflib
from pathlib import Path
from typing import Optional
from .base import Tool
from codesm.atomic_edit import AtomicEditManager, TransactionResult
from codesm.util.citations import file_link_with_path


class MultiFileEditTool(Tool):
    name = "multifile_edit"
    description = "Edit multiple files atomically - all changes succeed or all are rolled back."
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "description": "Array of file edits to perform atomically",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Absolute path to the file",
                            },
                            "old_content": {
                                "type": "string",
                                "description": "Exact content to replace (must match exactly). Empty for new files.",
                            },
                            "new_content": {
                                "type": "string",
                                "description": "New content to insert. Empty for file deletion.",
                            },
                            "operation": {
                                "type": "string",
                                "enum": ["edit", "create", "delete"],
                                "description": "Type of operation: edit (replace), create (new file), delete",
                                "default": "edit",
                            },
                        },
                        "required": ["path"],
                    },
                    "minItems": 1,
                },
                "description": {
                    "type": "string",
                    "description": "Description of the changes for undo history",
                },
            },
            "required": ["edits"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        edits = args.get("edits", [])
        description = args.get("description", "")
        
        if not edits:
            return "Error: No edits provided"
        
        session = context.get("session")
        
        # Validate and prepare edits
        prepared_edits = []
        validation_errors = []
        
        for i, edit in enumerate(edits):
            path = Path(edit.get("path", ""))
            operation = edit.get("operation", "edit")
            old_content = edit.get("old_content", "")
            new_content = edit.get("new_content", "")
            
            if not path:
                validation_errors.append(f"Edit {i+1}: Missing path")
                continue
            
            # For edit operations, read current content if old_content not provided
            if operation == "edit":
                if not path.exists():
                    validation_errors.append(f"Edit {i+1}: File not found: {path}")
                    continue
                
                current = path.read_text()
                if old_content:
                    if old_content not in current:
                        validation_errors.append(
                            f"Edit {i+1}: Could not find old_content in {path.name}"
                        )
                        continue
                    # Apply the replacement to get new file content
                    new_file_content = current.replace(old_content, new_content, 1)
                    prepared_edits.append({
                        "path": str(path),
                        "old_content": current,
                        "new_content": new_file_content,
                        "operation": "edit",
                        "_display_old": old_content,
                        "_display_new": new_content,
                    })
                else:
                    # Full file replacement
                    prepared_edits.append({
                        "path": str(path),
                        "old_content": current,
                        "new_content": new_content,
                        "operation": "edit",
                    })
                    
            elif operation == "create":
                if path.exists():
                    validation_errors.append(f"Edit {i+1}: File already exists: {path}")
                    continue
                prepared_edits.append({
                    "path": str(path),
                    "old_content": "",
                    "new_content": new_content,
                    "operation": "create",
                })
                
            elif operation == "delete":
                if not path.exists():
                    validation_errors.append(f"Edit {i+1}: File not found: {path}")
                    continue
                current = path.read_text()
                prepared_edits.append({
                    "path": str(path),
                    "old_content": current,
                    "new_content": "",
                    "operation": "delete",
                })
        
        if validation_errors:
            return "Validation failed:\n" + "\n".join(f"  • {e}" for e in validation_errors)
        
        # Show diff preview for all files
        try:
            from codesm.diff_preview import (
                request_diff_preview_multi,
                DiffPreviewSkippedError,
                DiffPreviewCancelledError,
            )
            
            preview_files = []
            for edit in prepared_edits:
                preview_files.append({
                    "path": edit["path"],
                    "old_content": edit.get("_display_old", edit["old_content"]),
                    "new_content": edit.get("_display_new", edit["new_content"]),
                })
            
            session_id = session.id if session else "default"
            await request_diff_preview_multi(
                session_id=session_id,
                files=preview_files,
                tool_name="multifile_edit",
            )
        except DiffPreviewSkippedError:
            return "MultiFileEdit skipped by user"
        except DiffPreviewCancelledError:
            return "MultiFileEdit cancelled by user"
        except (ImportError, AttributeError):
            pass  # Multi-file preview not implemented, proceed anyway
        except Exception:
            pass  # If diff preview fails, proceed anyway
        
        # Execute atomic transaction
        manager = AtomicEditManager.get_instance()
        txn = manager.create_transaction(
            description=description or f"Multi-file edit ({len(prepared_edits)} files)"
        )
        
        for edit in prepared_edits:
            txn.add_edit(
                path=edit["path"],
                old_content=edit["old_content"],
                new_content=edit["new_content"],
                operation=edit["operation"],
            )
        
        result = await manager.commit_transaction(txn, session)
        
        # Format output
        return await self._format_result(result, prepared_edits, session)
    
    async def _format_result(
        self,
        result: TransactionResult,
        prepared_edits: list[dict],
        session,
    ) -> str:
        """Format the transaction result for display"""
        
        if not result.success:
            error_msg = "**MultiFileEdit Failed** (all changes rolled back)\n\n"
            error_msg += "Errors:\n"
            for error in result.errors:
                error_msg += f"  • {error}\n"
            if result.rolled_back:
                error_msg += "\n✓ All partial changes have been rolled back."
            return error_msg
        
        # Build success output
        total = len(result.files_modified) + len(result.files_created) + len(result.files_deleted)
        
        stats_parts = []
        if result.files_created:
            stats_parts.append(f"+{len(result.files_created)} created")
        if result.files_modified:
            stats_parts.append(f"~{len(result.files_modified)} modified")
        if result.files_deleted:
            stats_parts.append(f"-{len(result.files_deleted)} deleted")
        stats = ", ".join(stats_parts) if stats_parts else "no changes"
        
        output = f"**MultiFileEdit** {total} file(s) ({stats})\n\n"
        
        # List files
        for path in result.files_created:
            file_link = file_link_with_path(Path(path))
            output += f"  + {file_link} (created)\n"
        for path in result.files_modified:
            file_link = file_link_with_path(Path(path))
            output += f"  ~ {file_link}\n"
        for path in result.files_deleted:
            output += f"  - {path} (deleted)\n"
        
        # Generate compact diffs for each modified file
        diff_parts = []
        for edit in prepared_edits:
            if edit["operation"] == "delete":
                continue
                
            path = Path(edit["path"])
            old = edit.get("_display_old", edit["old_content"])
            new = edit.get("_display_new", edit["new_content"])
            
            diff = self._generate_compact_diff(path.name, old, new)
            if diff:
                diff_parts.append(f"**{path.name}**\n{diff}")
        
        if diff_parts:
            output += "\n" + "\n\n".join(diff_parts[:5])  # Limit to 5 diffs
            if len(diff_parts) > 5:
                output += f"\n\n... and {len(diff_parts) - 5} more file(s)"
        
        # Format files if formatter available
        format_msgs = []
        for edit in prepared_edits:
            if edit["operation"] != "delete":
                msg = await self._format_file(Path(edit["path"]), session)
                if msg:
                    format_msgs.append(f"{Path(edit['path']).name}: {msg}")
        
        if format_msgs:
            output += "\n\n" + "\n".join(format_msgs)
        
        return output
    
    def _generate_compact_diff(self, filename: str, old: str, new: str) -> str:
        """Generate a compact diff for display"""
        if not old and new:
            # New content
            lines = new.split("\n")[:10]
            diff_lines = [f"+ {i+1:3d}    {line}" for i, line in enumerate(lines)]
            if len(new.split("\n")) > 10:
                diff_lines.append(f"  ...    ({len(new.split(chr(10))) - 10} more lines)")
            return "```diff\n" + "\n".join(diff_lines) + "\n```"
        
        if old and not new:
            # Deleted content
            lines = old.split("\n")[:5]
            diff_lines = [f"- {i+1:3d}    {line}" for i, line in enumerate(lines)]
            if len(old.split("\n")) > 5:
                diff_lines.append(f"  ...    ({len(old.split(chr(10))) - 5} more lines)")
            return "```diff\n" + "\n".join(diff_lines) + "\n```"
        
        # Diff
        old_lines = old.split("\n")
        new_lines = new.split("\n")
        
        diff_lines = []
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        line_count = 0
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if line_count > 15:
                diff_lines.append("  ...    (truncated)")
                break
            
            if tag == "equal":
                # Show context
                if i2 - i1 <= 2:
                    for idx in range(i1, i2):
                        diff_lines.append(f"  {idx+1:3d}    {old_lines[idx]}")
                        line_count += 1
            elif tag == "replace":
                for idx in range(i1, i2):
                    diff_lines.append(f"- {idx+1:3d}    {old_lines[idx]}")
                    line_count += 1
                for idx in range(j1, j2):
                    diff_lines.append(f"+ {idx+1:3d}    {new_lines[idx]}")
                    line_count += 1
            elif tag == "delete":
                for idx in range(i1, i2):
                    diff_lines.append(f"- {idx+1:3d}    {old_lines[idx]}")
                    line_count += 1
            elif tag == "insert":
                for idx in range(j1, j2):
                    diff_lines.append(f"+ {idx+1:3d}    {new_lines[idx]}")
                    line_count += 1
        
        if not diff_lines:
            return ""
        
        return "```diff\n" + "\n".join(diff_lines) + "\n```"
    
    async def _format_file(self, path: Path, session) -> str:
        """Format file if formatter is available and enabled."""
        try:
            from codesm.formatter import format_file_if_enabled
            session_id = session.id if session else None
            result = await format_file_if_enabled(path, session_id)
            
            if result and result.formatted:
                return f"✨ Formatted with {result.formatter}"
            elif result and not result.success and result.error:
                return f"⚠️ Format failed: {result.error}"
            return ""
        except Exception:
            return ""
