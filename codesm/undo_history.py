"""Undo/Redo history system for file edits with full history tracking"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Union
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
    transaction_id: Optional[str] = None  # Link to parent transaction if part of one
    
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
            "transaction_id": self.transaction_id,
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
            transaction_id=data.get("transaction_id"),
        )


@dataclass
class TransactionGroup:
    """Represents a group of file edits that were applied atomically"""
    id: str
    edits: list[EditOperation] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    description: str = ""
    snapshot_hash: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "edits": [e.to_dict() for e in self.edits],
            "timestamp": self.timestamp.isoformat(),
            "description": self.description,
            "snapshot_hash": self.snapshot_hash,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "TransactionGroup":
        return cls(
            id=data["id"],
            edits=[EditOperation.from_dict(e) for e in data.get("edits", [])],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            description=data.get("description", ""),
            snapshot_hash=data.get("snapshot_hash"),
        )
    
    @property
    def file_paths(self) -> list[str]:
        """Get list of all file paths in this transaction"""
        return [e.file_path for e in self.edits]


class UndoHistory:
    """Manages undo/redo history for file edits within a session"""
    
    # Type alias for stack entries (can be single edit or transaction group)
    HistoryEntry = Union[EditOperation, TransactionGroup]
    
    def __init__(self):
        # Stack of operations (newest last) - can contain EditOperation or TransactionGroup
        self._undo_stack: list[Union[EditOperation, TransactionGroup]] = []
        # Stack of undone operations (for redo)
        self._redo_stack: list[Union[EditOperation, TransactionGroup]] = []
        # Counter for generating unique IDs
        self._op_counter = 0
        # Transaction groups by ID for lookup
        self._transactions: dict[str, TransactionGroup] = {}
    
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
        transaction_id: Optional[str] = None,
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
            transaction_id=transaction_id,
        )
        self._undo_stack.append(op)
        # Clear redo stack when new edit is made
        self._redo_stack.clear()
        return op
    
    def record_transaction(
        self,
        transaction_id: str,
        edits: list[dict],
        description: str = "",
        snapshot_hash: Optional[str] = None,
    ) -> TransactionGroup:
        """Record a multi-file transaction as a single undoable unit
        
        Args:
            transaction_id: Unique ID for the transaction
            edits: List of dicts with file_path, before_content, after_content, operation
            description: Human-readable description
            snapshot_hash: Snapshot hash for file state before transaction
            
        Returns:
            TransactionGroup that can be undone atomically
        """
        ops = []
        for edit in edits:
            op = EditOperation(
                id=self._generate_id(),
                file_path=str(edit["file_path"]),
                before_content=edit.get("before_content", ""),
                after_content=edit.get("after_content", ""),
                tool_name=edit.get("operation", "edit"),
                description=edit.get("description", ""),
                snapshot_hash=snapshot_hash,
                transaction_id=transaction_id,
            )
            ops.append(op)
        
        txn = TransactionGroup(
            id=transaction_id,
            edits=ops,
            description=description,
            snapshot_hash=snapshot_hash,
        )
        
        self._transactions[transaction_id] = txn
        self._undo_stack.append(txn)
        # Clear redo stack when new edit is made
        self._redo_stack.clear()
        return txn
    
    def can_undo(self, file_path: Optional[str] = None) -> bool:
        """Check if undo is available, optionally for a specific file"""
        if file_path:
            file_path_str = str(file_path)
            for entry in self._undo_stack:
                if isinstance(entry, TransactionGroup):
                    if file_path_str in entry.file_paths:
                        return True
                elif entry.file_path == file_path_str:
                    return True
            return False
        return len(self._undo_stack) > 0
    
    def can_redo(self, file_path: Optional[str] = None) -> bool:
        """Check if redo is available, optionally for a specific file"""
        if file_path:
            file_path_str = str(file_path)
            for entry in self._redo_stack:
                if isinstance(entry, TransactionGroup):
                    if file_path_str in entry.file_paths:
                        return True
                elif entry.file_path == file_path_str:
                    return True
            return False
        return len(self._redo_stack) > 0
    
    def undo(self, file_path: Optional[str] = None) -> Optional[Union[EditOperation, TransactionGroup]]:
        """Get the next undo operation (does not apply it)
        
        If file_path is provided, returns the most recent edit/transaction for that file.
        Otherwise returns the most recent entry overall.
        
        Returns either an EditOperation or TransactionGroup.
        """
        if not self._undo_stack:
            return None
        
        if file_path:
            # Find most recent edit for this file
            file_path_str = str(file_path)
            for i in range(len(self._undo_stack) - 1, -1, -1):
                entry = self._undo_stack[i]
                if isinstance(entry, TransactionGroup):
                    if file_path_str in entry.file_paths:
                        op = self._undo_stack.pop(i)
                        self._redo_stack.append(op)
                        return op
                elif entry.file_path == file_path_str:
                    op = self._undo_stack.pop(i)
                    self._redo_stack.append(op)
                    return op
            return None
        else:
            # Pop most recent entry
            op = self._undo_stack.pop()
            self._redo_stack.append(op)
            return op
    
    def redo(self, file_path: Optional[str] = None) -> Optional[Union[EditOperation, TransactionGroup]]:
        """Get the next redo operation (does not apply it)
        
        If file_path is provided, returns the most recent undone edit/transaction for that file.
        Otherwise returns the most recent undone entry overall.
        
        Returns either an EditOperation or TransactionGroup.
        """
        if not self._redo_stack:
            return None
        
        if file_path:
            # Find most recent undone edit for this file
            file_path_str = str(file_path)
            for i in range(len(self._redo_stack) - 1, -1, -1):
                entry = self._redo_stack[i]
                if isinstance(entry, TransactionGroup):
                    if file_path_str in entry.file_paths:
                        op = self._redo_stack.pop(i)
                        self._undo_stack.append(op)
                        return op
                elif entry.file_path == file_path_str:
                    op = self._redo_stack.pop(i)
                    self._undo_stack.append(op)
                    return op
            return None
        else:
            # Pop most recent undone edit
            op = self._redo_stack.pop()
            self._undo_stack.append(op)
            return op
    
    def get_history(self, file_path: Optional[str] = None, limit: int = 10) -> list[Union[EditOperation, TransactionGroup]]:
        """Get edit history, optionally filtered by file path
        
        Returns list of EditOperation and TransactionGroup entries.
        """
        if file_path:
            file_path_str = str(file_path)
            filtered = []
            for entry in self._undo_stack:
                if isinstance(entry, TransactionGroup):
                    if file_path_str in entry.file_paths:
                        filtered.append(entry)
                elif entry.file_path == file_path_str:
                    filtered.append(entry)
            return list(reversed(filtered[-limit:]))
        return list(reversed(self._undo_stack[-limit:]))
    
    def get_file_history(self, file_path: str) -> list[EditOperation]:
        """Get complete history for a specific file in chronological order
        
        Expands TransactionGroups to return individual EditOperations.
        """
        file_path_str = str(file_path)
        result = []
        for entry in self._undo_stack:
            if isinstance(entry, TransactionGroup):
                for edit in entry.edits:
                    if edit.file_path == file_path_str:
                        result.append(edit)
            elif entry.file_path == file_path_str:
                result.append(entry)
        return result
    
    def get_undo_count(self, file_path: Optional[str] = None) -> int:
        """Get number of available undo operations"""
        if file_path:
            file_path_str = str(file_path)
            count = 0
            for entry in self._undo_stack:
                if isinstance(entry, TransactionGroup):
                    if file_path_str in entry.file_paths:
                        count += 1
                elif entry.file_path == file_path_str:
                    count += 1
            return count
        return len(self._undo_stack)
    
    def get_redo_count(self, file_path: Optional[str] = None) -> int:
        """Get number of available redo operations"""
        if file_path:
            file_path_str = str(file_path)
            count = 0
            for entry in self._redo_stack:
                if isinstance(entry, TransactionGroup):
                    if file_path_str in entry.file_paths:
                        count += 1
                elif entry.file_path == file_path_str:
                    count += 1
            return count
        return len(self._redo_stack)
    
    def get_transaction(self, transaction_id: str) -> Optional[TransactionGroup]:
        """Get a transaction by ID"""
        return self._transactions.get(transaction_id)
    
    def clear(self):
        """Clear all history"""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._transactions.clear()
    
    def _serialize_entry(self, entry: Union[EditOperation, TransactionGroup]) -> dict:
        """Serialize a stack entry to dict"""
        data = entry.to_dict()
        data["_type"] = "transaction" if isinstance(entry, TransactionGroup) else "edit"
        return data
    
    def _deserialize_entry(self, data: dict) -> Union[EditOperation, TransactionGroup]:
        """Deserialize a stack entry from dict"""
        entry_type = data.get("_type", "edit")
        if entry_type == "transaction":
            return TransactionGroup.from_dict(data)
        return EditOperation.from_dict(data)
    
    def to_dict(self) -> dict:
        """Serialize history to dict for persistence"""
        return {
            "undo_stack": [self._serialize_entry(e) for e in self._undo_stack],
            "redo_stack": [self._serialize_entry(e) for e in self._redo_stack],
            "op_counter": self._op_counter,
            "transactions": {k: v.to_dict() for k, v in self._transactions.items()},
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "UndoHistory":
        """Deserialize history from dict"""
        history = cls()
        
        # Handle legacy format (all EditOperations)
        undo_stack_data = data.get("undo_stack", [])
        for entry_data in undo_stack_data:
            history._undo_stack.append(history._deserialize_entry(entry_data))
        
        redo_stack_data = data.get("redo_stack", [])
        for entry_data in redo_stack_data:
            history._redo_stack.append(history._deserialize_entry(entry_data))
        
        history._op_counter = data.get("op_counter", 0)
        
        # Load transactions
        for txn_id, txn_data in data.get("transactions", {}).items():
            history._transactions[txn_id] = TransactionGroup.from_dict(txn_data)
        
        return history
