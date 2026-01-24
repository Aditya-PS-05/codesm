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
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview changes without applying (default: false)",
                    "default": False,
                },
            },
            "required": ["path", "content"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        import time
        start_time = time.time()
        
        path = Path(args["path"])
        content = args["content"]
        dry_run = args.get("dry_run", False)
        
        # Check global dry_run mode from context
        if context.get("dry_run", False):
            dry_run = True
        
        session = context.get("session")
        session_id = session.id if session else "default"
        
        # Audit log the tool call
        try:
            from codesm.audit import audit_tool_call, audit_tool_result, AuditAction, get_audit_log
            audit_tool_call("write", {"path": str(path), "dry_run": dry_run}, session_id)
        except ImportError:
            pass
        
        # Check path permissions
        try:
            from codesm.permission import check_path_permission, PathBlockedError
            working_dir = Path(context.get("cwd", ".")).resolve()
            check_path_permission(path, working_dir=working_dir)
        except PathBlockedError as e:
            return f"Error: {e.reason}"
        except ImportError:
            pass
        
        try:
            old_content = ""
            is_new_file = not path.exists()
            if not is_new_file:
                old_content = path.read_text()
            
            # Generate diff/preview
            new_lines = content.split('\n')
            if is_new_file:
                # New file - show all lines as added
                diff_lines = []
                for i, line in enumerate(new_lines[:20]):  # Limit preview
                    diff_lines.append(f"+ {i+1:3d}    {line}")
                if len(new_lines) > 20:
                    diff_lines.append(f"  ... ({len(new_lines) - 20} more lines)")
                diff_output = "```diff\n" + '\n'.join(diff_lines) + "\n```"
                stats = f"+{len(new_lines)} lines (new file)"
            else:
                # Existing file - show diff
                diff_output = self._generate_diff(old_content, content)
                old_lines = old_content.split('\n')
                added = len(new_lines) - len(old_lines) if len(new_lines) > len(old_lines) else 0
                removed = len(old_lines) - len(new_lines) if len(old_lines) > len(new_lines) else 0
                stats = f"+{added} -{removed}"
            
            # DRY RUN: Show preview only
            if dry_run:
                file_link = file_link_with_path(path)
                result = f"**[DRY RUN] Write Preview** {file_link} {stats}\n\n{diff_output}\n\n*Run with dry_run=false to apply changes*"
                
                try:
                    duration_ms = int((time.time() - start_time) * 1000)
                    audit_tool_result("write", True, f"dry_run preview: {path.name}", duration_ms=duration_ms, session_id=session_id)
                except:
                    pass
                
                return result
            
            pre_write_hash = None
            if session:
                pre_write_hash = await session.track_snapshot()
            
            # Show diff preview if enabled (for overwrites, not new files)
            if not is_new_file:
                try:
                    from codesm.diff_preview import request_diff_preview, DiffPreviewSkippedError, DiffPreviewCancelledError
                    await request_diff_preview(
                        session_id=session_id,
                        file_path=str(path),
                        old_content=old_content,
                        new_content=content,
                        tool_name="write",
                    )
                except DiffPreviewSkippedError:
                    return f"Write skipped by user: {path.name}"
                except DiffPreviewCancelledError:
                    return f"Write cancelled by user"
                except Exception:
                    pass  # If diff preview fails, proceed anyway
            
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            
            # Audit file operation
            try:
                from codesm.audit import get_audit_log, AuditAction
                get_audit_log().log_file_operation(
                    AuditAction.FILE_WRITE,
                    str(path),
                    session_id=session_id,
                    details={"is_new": is_new_file, "lines": len(new_lines)},
                )
            except:
                pass
            
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
            
            # Format on save
            format_msg = await self._format_file(path, session)
            
            file_link = file_link_with_path(path)
            result = f"**Write** {file_link} {stats}\n\n{diff_output}"
            
            # Add format info if formatted
            if format_msg:
                result += f"\n\n{format_msg}"
            
            diagnostics_output = await self._get_diagnostics(str(path))
            if diagnostics_output:
                result += f"\n\n{diagnostics_output}"
            
            if session and pre_write_hash:
                patch = await session.get_file_changes(pre_write_hash)
                if patch.get("files"):
                    context["_last_patch"] = patch
            
            # Audit success
            try:
                duration_ms = int((time.time() - start_time) * 1000)
                audit_tool_result("write", True, f"wrote {path.name}", duration_ms=duration_ms, session_id=session_id)
            except:
                pass
            
            return result
        except Exception as e:
            # Audit failure
            try:
                duration_ms = int((time.time() - start_time) * 1000)
                audit_tool_result("write", False, error=str(e), duration_ms=duration_ms, session_id=session_id)
            except:
                pass
            return f"Error writing file: {e}"

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
