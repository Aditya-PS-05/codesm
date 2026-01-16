"""Patch tool - Apply multi-file patches with context-aware changes"""

import os
import difflib
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from .base import Tool


@dataclass
class UpdateChunk:
    """A chunk of changes within an update hunk"""
    old_lines: list[str]
    new_lines: list[str]
    context: Optional[str] = None
    is_end_of_file: bool = False


@dataclass
class Hunk:
    """Represents a single file operation in a patch"""
    pass


@dataclass
class AddHunk(Hunk):
    path: str
    contents: str


@dataclass
class DeleteHunk(Hunk):
    path: str


@dataclass
class UpdateHunk(Hunk):
    path: str
    chunks: list[UpdateChunk]
    move_path: Optional[str] = None


class PatchParser:
    """Parser for the patch format"""
    
    BEGIN_MARKER = "*** Begin Patch"
    END_MARKER = "*** End Patch"
    
    @classmethod
    def parse(cls, patch_text: str) -> list[Hunk]:
        """Parse patch text into hunks"""
        lines = patch_text.split("\n")
        hunks: list[Hunk] = []
        
        # Find markers
        begin_idx = -1
        end_idx = -1
        
        for i, line in enumerate(lines):
            if line.strip() == cls.BEGIN_MARKER:
                begin_idx = i
            elif line.strip() == cls.END_MARKER:
                end_idx = i
                break
        
        if begin_idx == -1 or end_idx == -1 or begin_idx >= end_idx:
            raise ValueError("Invalid patch format: missing Begin/End markers")
        
        i = begin_idx + 1
        
        while i < end_idx:
            line = lines[i]
            
            if line.startswith("*** Add File:"):
                file_path = line.split(":", 1)[1].strip()
                content, i = cls._parse_add_content(lines, i + 1, end_idx)
                hunks.append(AddHunk(path=file_path, contents=content))
                
            elif line.startswith("*** Delete File:"):
                file_path = line.split(":", 1)[1].strip()
                hunks.append(DeleteHunk(path=file_path))
                i += 1
                
            elif line.startswith("*** Update File:"):
                file_path = line.split(":", 1)[1].strip()
                move_path = None
                i += 1
                
                # Check for move directive
                if i < end_idx and lines[i].startswith("*** Move to:"):
                    move_path = lines[i].split(":", 1)[1].strip()
                    i += 1
                
                chunks, i = cls._parse_update_chunks(lines, i, end_idx)
                hunks.append(UpdateHunk(path=file_path, chunks=chunks, move_path=move_path))
            else:
                i += 1
        
        return hunks
    
    @classmethod
    def _parse_add_content(cls, lines: list[str], start: int, end: int) -> tuple[str, int]:
        """Parse content for an Add File hunk"""
        content_lines = []
        i = start
        
        while i < end and not lines[i].startswith("***"):
            line = lines[i]
            if line.startswith("+"):
                content_lines.append(line[1:])
            i += 1
        
        return "\n".join(content_lines), i
    
    @classmethod
    def _parse_update_chunks(cls, lines: list[str], start: int, end: int) -> tuple[list[UpdateChunk], int]:
        """Parse chunks for an Update File hunk"""
        chunks = []
        i = start
        
        while i < end and not lines[i].startswith("***"):
            if lines[i].startswith("@@"):
                context = lines[i][2:].strip() or None
                i += 1
                
                old_lines = []
                new_lines = []
                is_eof = False
                
                while i < end and not lines[i].startswith("@@") and not lines[i].startswith("***"):
                    line = lines[i]
                    
                    if line == "*** End of File":
                        is_eof = True
                        i += 1
                        break
                    
                    if line.startswith(" "):
                        # Context line - appears in both
                        content = line[1:]
                        old_lines.append(content)
                        new_lines.append(content)
                    elif line.startswith("-"):
                        # Remove line
                        old_lines.append(line[1:])
                    elif line.startswith("+"):
                        # Add line
                        new_lines.append(line[1:])
                    
                    i += 1
                
                chunks.append(UpdateChunk(
                    old_lines=old_lines,
                    new_lines=new_lines,
                    context=context,
                    is_end_of_file=is_eof
                ))
            else:
                i += 1
        
        return chunks, i


class PatchApplier:
    """Applies parsed hunks to the filesystem"""
    
    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root)
    
    def apply(self, hunks: list[Hunk]) -> dict:
        """Apply all hunks and return results"""
        results = {
            "added": [],
            "updated": [],
            "deleted": [],
            "moved": [],
            "errors": [],
            "diffs": {}
        }
        
        for hunk in hunks:
            try:
                if isinstance(hunk, AddHunk):
                    self._apply_add(hunk, results)
                elif isinstance(hunk, DeleteHunk):
                    self._apply_delete(hunk, results)
                elif isinstance(hunk, UpdateHunk):
                    self._apply_update(hunk, results)
            except Exception as e:
                results["errors"].append(f"{hunk.path}: {e}")
        
        return results
    
    def _resolve_path(self, path: str) -> Path:
        """Resolve a path relative to workspace root"""
        if os.path.isabs(path):
            return Path(path)
        return self.workspace_root / path
    
    def _apply_add(self, hunk: AddHunk, results: dict):
        """Apply an add file hunk"""
        file_path = self._resolve_path(hunk.path)
        
        # Create parent directories
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write content
        file_path.write_text(hunk.contents)
        results["added"].append(str(hunk.path))
        
        # Generate diff
        diff = self._generate_diff("", hunk.contents, str(hunk.path))
        results["diffs"][hunk.path] = diff
    
    def _apply_delete(self, hunk: DeleteHunk, results: dict):
        """Apply a delete file hunk"""
        file_path = self._resolve_path(hunk.path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {hunk.path}")
        
        old_content = file_path.read_text()
        file_path.unlink()
        results["deleted"].append(str(hunk.path))
        
        # Generate diff
        diff = self._generate_diff(old_content, "", str(hunk.path))
        results["diffs"][hunk.path] = diff
    
    def _apply_update(self, hunk: UpdateHunk, results: dict):
        """Apply an update file hunk"""
        file_path = self._resolve_path(hunk.path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {hunk.path}")
        
        old_content = file_path.read_text()
        new_content = self._derive_new_content(old_content, hunk.chunks, hunk.path)
        
        # Generate diff before writing
        diff = self._generate_diff(old_content, new_content, str(hunk.path))
        
        if hunk.move_path:
            # Move operation
            new_path = self._resolve_path(hunk.move_path)
            new_path.parent.mkdir(parents=True, exist_ok=True)
            new_path.write_text(new_content)
            file_path.unlink()
            results["moved"].append(f"{hunk.path} -> {hunk.move_path}")
            results["diffs"][hunk.move_path] = diff
        else:
            # Regular update
            file_path.write_text(new_content)
            results["updated"].append(str(hunk.path))
            results["diffs"][hunk.path] = diff
    
    def _derive_new_content(self, content: str, chunks: list[UpdateChunk], path: str) -> str:
        """Apply chunks to derive new file content"""
        lines = content.split("\n")
        replacements: list[tuple[int, int, list[str]]] = []
        line_index = 0
        
        for chunk in chunks:
            pattern = chunk.old_lines
            new_segment = chunk.new_lines
            
            if not pattern:
                # Pure insertion - insert at current position
                replacements.append((line_index, 0, new_segment))
                continue
            
            # Find the pattern in the file
            found = self._seek_sequence(lines, pattern, line_index)
            
            # If not found from current position, search from beginning
            if found == -1 and line_index > 0:
                found = self._seek_sequence(lines, pattern, 0)
            
            if found == -1:
                raise ValueError(
                    f"Failed to find expected lines in {path}:\n" + 
                    "\n".join(pattern[:5]) + ("..." if len(pattern) > 5 else "")
                )
            
            replacements.append((found, len(pattern), new_segment))
            line_index = found + len(pattern)
        
        # Sort and apply replacements in reverse order
        replacements.sort(key=lambda x: x[0], reverse=True)
        
        result = lines.copy()
        for start_idx, old_len, new_segment in replacements:
            result[start_idx:start_idx + old_len] = new_segment
        
        return "\n".join(result)
    
    def _seek_sequence(self, lines: list[str], pattern: list[str], start: int) -> int:
        """Find pattern sequence in lines starting from start index"""
        if not pattern:
            return -1
        
        for i in range(start, len(lines) - len(pattern) + 1):
            if lines[i:i + len(pattern)] == pattern:
                return i
        
        return -1
    
    def _generate_diff(self, old: str, new: str, path: str) -> str:
        """Generate unified diff between old and new content"""
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)
        
        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}"
        )
        
        return "".join(diff)


class PatchTool(Tool):
    name = "patch"
    description = "Apply a patch to modify multiple files."
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "patch_text": {
                    "type": "string",
                    "description": "The full patch text describing all changes to make",
                },
            },
            "required": ["patch_text"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        patch_text = args.get("patch_text", "")
        
        if not patch_text:
            return "Error: patch_text is required"
        
        # Get workspace root from context
        workspace_root = context.get("workspace_dir", os.getcwd())
        
        try:
            # Parse the patch
            hunks = PatchParser.parse(patch_text)
            
            if not hunks:
                return "Error: No file changes found in patch"
            
            # Apply the patch
            applier = PatchApplier(workspace_root)
            results = applier.apply(hunks)
            
            # Format output
            return self._format_results(results)
            
        except ValueError as e:
            return f"Error parsing patch: {e}"
        except Exception as e:
            return f"Error applying patch: {e}"
    
    def _format_results(self, results: dict) -> str:
        """Format the patch results for display"""
        total_files = (
            len(results["added"]) + 
            len(results["updated"]) + 
            len(results["deleted"]) + 
            len(results["moved"])
        )
        
        # Build stats
        stats_parts = []
        if results["added"]:
            stats_parts.append(f"+{len(results['added'])} added")
        if results["updated"]:
            stats_parts.append(f"~{len(results['updated'])} updated")
        if results["deleted"]:
            stats_parts.append(f"-{len(results['deleted'])} deleted")
        if results["moved"]:
            stats_parts.append(f"→{len(results['moved'])} moved")
        stats = ", ".join(stats_parts) if stats_parts else "no changes"
        
        # Header line
        header = f"**Patch** {total_files} file(s) ({stats})"
        
        # File list
        file_lines = []
        for path in results["added"]:
            file_lines.append(f"  + {path}")
        for path in results["updated"]:
            file_lines.append(f"  ~ {path}")
        for path in results["deleted"]:
            file_lines.append(f"  - {path}")
        for move in results["moved"]:
            file_lines.append(f"  → {move}")
        
        # Build diff output (compact, like other tools)
        diff_parts = []
        for file_path, diff in results["diffs"].items():
            if diff and diff.strip():
                # Parse and format diff nicely
                formatted = self._format_diff_compact(diff, file_path)
                if formatted:
                    diff_parts.append(formatted)
        
        # Combine output
        output = header
        if file_lines:
            output += "\n" + "\n".join(file_lines)
        
        if diff_parts:
            output += "\n\n" + "\n\n".join(diff_parts)
        
        # Errors at the end
        if results["errors"]:
            output += "\n\n⚠️ Errors:\n" + "\n".join(f"  {e}" for e in results["errors"])
        
        return output
    
    def _format_diff_compact(self, diff: str, file_path: str) -> str:
        """Format diff in compact style matching edit tool output"""
        lines = diff.split('\n')
        
        # Skip header lines (---, +++, @@)
        diff_lines = []
        line_num = 1
        
        for line in lines:
            # Skip unified diff headers
            if line.startswith('---') or line.startswith('+++') or line.startswith('@@'):
                continue
            if not line:
                continue
                
            if line.startswith('-'):
                diff_lines.append(f"- {line_num:3d}    {line[1:]}")
                line_num += 1
            elif line.startswith('+'):
                diff_lines.append(f"+ {line_num:3d}    {line[1:]}")
                line_num += 1
            elif line.startswith(' '):
                diff_lines.append(f"  {line_num:3d}    {line[1:]}")
                line_num += 1
        
        if not diff_lines:
            return ""
        
        # Truncate if too long
        if len(diff_lines) > 25:
            diff_lines = diff_lines[:25]
            diff_lines.append(f"  ...    (truncated)")
        
        return f"```diff\n" + "\n".join(diff_lines) + "\n```"
