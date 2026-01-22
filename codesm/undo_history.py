"""Undo/Redo history system for file edits with full history tracking"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import copy


@dataclass
class EditOperation:
    """Represents a single file edit operation"""
    id: str
    file_path: str
    before_content: str
    after_content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tool_name: str = "edit"  # edit, write, multiedit
    description: str = ""
    snapshot_hash: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "before_content": self.before_content,
            "after_content": self.after_content,
            "timestamp": self.timestamp.isoformat(),
            "tool_name": self.tool_name,
            "description": self.description,
            "snapshot_hash": self.snapshot_hash,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "EditOperation":
        return cls(
            id=data["id"],
            file_path=data["file_path"],
            before_content=data["before_content"],
            after_content=data["after_content"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            tool_name=data.get("tool_name", "edit"),
            description=data.get("description", ""),
            snapshot_hash=data.get("snapshot_hash"),
        )


class UndoHistory:
    """Manages undo/redo history for file edits within a session"""
    
    def __init__(self):
        # Stack of operations (newest last)
        self._undo_stack: list[EditOperation] = []
        # Stack of undone operations (for redo)
        self._redo_stack: list[EditOperation] = []
        # Counter for generating unique IDs
        self._op_counter = 0
    
    def _generate_id(self) -> str:
        self._op_counter += 1
        return f"op_{self._op_counter}_{datetime.now().strftime('%H%M%S%f')}"
    
    def record_edit(
        self,
        file_path: str,
        before_content: str,
        after_content: str,
        tool_name: str = "edit",
        description: str = "",
        snapshot_hash: Optional[str] = None,
    ) -> EditOperation:
        """Record a file edit operation for undo tracking"""
        op = EditOperation(
            id=self._generate_id(),
            file_path=str(file_path),
            before_content=before_content,
            after_content=after_content,
            tool_name=tool_name,
            description=description,
            snapshot_hash=snapshot_hash,
        )
        self._undo_stack.append(op)
        # Clear redo stack when new edit is made
        self._redo_stack.clear()
        return op
    
    def can_undo(self, file_path: Optional[str] = None) -> bool:
        """Check if undo is available, optionally for a specific file"""
        if file_path:
            return any(op.file_path == str(file_path) for op in self._undo_stack)
        return len(self._undo_stack) > 0
    
    def can_redo(self, file_path: Optional[str] = None) -> bool:
        """Check if redo is available, optionally for a specific file"""
        if file_path:
            return any(op.file_path == str(file_path) for op in self._redo_stack)
        return len(self._redo_stack) > 0
    
    def undo(self, file_path: Optional[str] = None) -> Optional[EditOperation]:
        """Get the next undo operation (does not apply it)
        
        If file_path is provided, returns the most recent edit for that file.
        Otherwise returns the most recent edit overall.
        """
        if not self._undo_stack:
            return None
        
        if file_path:
            # Find most recent edit for this file
            file_path_str = str(file_path)
            for i in range(len(self._undo_stack) - 1, -1, -1):
                if self._undo_stack[i].file_path == file_path_str:
                    op = self._undo_stack.pop(i)
                    self._redo_stack.append(op)
                    return op
            return None
        else:
            # Pop most recent edit
            op = self._undo_stack.pop()
            self._redo_stack.append(op)
            return op
    
    def redo(self, file_path: Optional[str] = None) -> Optional[EditOperation]:
        """Get the next redo operation (does not apply it)
        
        If file_path is provided, returns the most recent undone edit for that file.
        Otherwise returns the most recent undone edit overall.
        """
        if not self._redo_stack:
            return None
        
        if file_path:
            # Find most recent undone edit for this file
            file_path_str = str(file_path)
            for i in range(len(self._redo_stack) - 1, -1, -1):
                if self._redo_stack[i].file_path == file_path_str:
                    op = self._redo_stack.pop(i)
                    self._undo_stack.append(op)
                    return op
            return None
        else:
            # Pop most recent undone edit
            op = self._redo_stack.pop()
            self._undo_stack.append(op)
            return op
    
    def get_history(self, file_path: Optional[str] = None, limit: int = 10) -> list[EditOperation]:
        """Get edit history, optionally filtered by file path"""
        ops = self._undo_stack
        if file_path:
            file_path_str = str(file_path)
            ops = [op for op in ops if op.file_path == file_path_str]
        return list(reversed(ops[-limit:]))
    
    def get_file_history(self, file_path: str) -> list[EditOperation]:
        """Get complete history for a specific file in chronological order"""
        file_path_str = str(file_path)
        return [op for op in self._undo_stack if op.file_path == file_path_str]
    
    def get_undo_count(self, file_path: Optional[str] = None) -> int:
        """Get number of available undo operations"""
        if file_path:
            file_path_str = str(file_path)
            return sum(1 for op in self._undo_stack if op.file_path == file_path_str)
        return len(self._undo_stack)
    
    def get_redo_count(self, file_path: Optional[str] = None) -> int:
        """Get number of available redo operations"""
        if file_path:
            file_path_str = str(file_path)
            return sum(1 for op in self._redo_stack if op.file_path == file_path_str)
        return len(self._redo_stack)
    
    def clear(self):
        """Clear all history"""
        self._undo_stack.clear()
        self._redo_stack.clear()
    
    def to_dict(self) -> dict:
        """Serialize history to dict for persistence"""
        return {
            "undo_stack": [op.to_dict() for op in self._undo_stack],
            "redo_stack": [op.to_dict() for op in self._redo_stack],
            "op_counter": self._op_counter,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "UndoHistory":
        """Deserialize history from dict"""
        history = cls()
        history._undo_stack = [EditOperation.from_dict(op) for op in data.get("undo_stack", [])]
        history._redo_stack = [EditOperation.from_dict(op) for op in data.get("redo_stack", [])]
        history._op_counter = data.get("op_counter", 0)
        return history
