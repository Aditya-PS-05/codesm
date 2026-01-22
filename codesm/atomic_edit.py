"""Atomic multi-file edit system with transaction support and rollback"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Any, TypeVar
from enum import Enum
import uuid


class TransactionState(Enum):
    """State of a transaction"""
    PENDING = "pending"
    VALIDATING = "validating"
    APPLYING = "applying"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


@dataclass
class FileEdit:
    """Represents a single file edit within a transaction"""
    path: str
    old_content: str
    new_content: str
    operation: str = "edit"  # edit, create, delete
    applied: bool = False
    error: Optional[str] = None


@dataclass
class TransactionResult:
    """Result of a transaction execution"""
    success: bool
    transaction_id: str
    files_modified: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rolled_back: bool = False


@dataclass
class Transaction:
    """A transaction containing multiple file edits that succeed or fail together"""
    id: str
    edits: list[FileEdit] = field(default_factory=list)
    state: TransactionState = TransactionState.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    snapshot_hash: Optional[str] = None
    description: str = ""
    
    def add_edit(self, path: str, old_content: str, new_content: str, 
                 operation: str = "edit") -> FileEdit:
        """Add an edit to the transaction"""
        edit = FileEdit(
            path=str(path),
            old_content=old_content,
            new_content=new_content,
            operation=operation,
        )
        self.edits.append(edit)
        return edit
    
    def add_create(self, path: str, content: str) -> FileEdit:
        """Add a file creation to the transaction"""
        return self.add_edit(path, "", content, operation="create")
    
    def add_delete(self, path: str, current_content: str) -> FileEdit:
        """Add a file deletion to the transaction"""
        return self.add_edit(path, current_content, "", operation="delete")


class FileLock:
    """Per-file write lock using promise chaining pattern"""
    
    def __init__(self):
        self._locks: dict[str, asyncio.Future] = {}
    
    @asynccontextmanager
    async def acquire(self, path: str):
        """Acquire a lock on a file, waiting for any pending operations"""
        path_str = str(Path(path).resolve())
        
        # Get current lock or create a resolved one
        current_lock = self._locks.get(path_str)
        if current_lock is None:
            current_lock = asyncio.get_event_loop().create_future()
            current_lock.set_result(None)
        
        # Create a new lock for this operation
        new_lock = asyncio.get_event_loop().create_future()
        self._locks[path_str] = new_lock
        
        try:
            # Wait for previous operation to complete
            if not current_lock.done():
                await current_lock
            yield
        finally:
            # Release our lock
            if not new_lock.done():
                new_lock.set_result(None)
            # Clean up if this is still the current lock
            if self._locks.get(path_str) is new_lock:
                del self._locks[path_str]


class AtomicEditManager:
    """Manages atomic multi-file edit transactions"""
    
    _instance: Optional["AtomicEditManager"] = None
    
    def __init__(self):
        self._file_lock = FileLock()
        self._active_transactions: dict[str, Transaction] = {}
    
    @classmethod
    def get_instance(cls) -> "AtomicEditManager":
        """Get singleton instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def create_transaction(self, description: str = "") -> Transaction:
        """Create a new transaction"""
        txn_id = f"txn_{uuid.uuid4().hex[:12]}"
        txn = Transaction(id=txn_id, description=description)
        self._active_transactions[txn_id] = txn
        return txn
    
    async def validate_transaction(self, txn: Transaction) -> list[str]:
        """Validate all edits in a transaction without applying them
        
        Returns list of validation errors (empty if valid)
        """
        errors = []
        txn.state = TransactionState.VALIDATING
        
        for edit in txn.edits:
            path = Path(edit.path)
            
            if edit.operation == "create":
                if path.exists():
                    errors.append(f"{edit.path}: File already exists")
                # Check parent directory
                if not path.parent.exists():
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                    except Exception as e:
                        errors.append(f"{edit.path}: Cannot create parent directory: {e}")
                        
            elif edit.operation == "delete":
                if not path.exists():
                    errors.append(f"{edit.path}: File not found for deletion")
                elif path.read_text() != edit.old_content:
                    errors.append(f"{edit.path}: File content changed since transaction started")
                    
            elif edit.operation == "edit":
                if not path.exists():
                    errors.append(f"{edit.path}: File not found")
                elif path.read_text() != edit.old_content:
                    errors.append(f"{edit.path}: File content changed since transaction started")
        
        return errors
    
    async def commit_transaction(
        self, 
        txn: Transaction,
        session: Optional[Any] = None,
    ) -> TransactionResult:
        """Commit a transaction atomically
        
        All files succeed or all are rolled back on failure.
        """
        result = TransactionResult(
            success=False,
            transaction_id=txn.id,
        )
        
        # Validate first
        validation_errors = await self.validate_transaction(txn)
        if validation_errors:
            txn.state = TransactionState.FAILED
            result.errors = validation_errors
            return result
        
        # Take snapshot before applying if session provided
        if session:
            txn.snapshot_hash = await session.track_snapshot()
        
        txn.state = TransactionState.APPLYING
        applied_edits: list[FileEdit] = []
        
        try:
            # Acquire locks for all files
            async with self._acquire_all_locks([e.path for e in txn.edits]):
                for edit in txn.edits:
                    try:
                        await self._apply_edit(edit)
                        edit.applied = True
                        applied_edits.append(edit)
                        
                        # Track in result
                        if edit.operation == "create":
                            result.files_created.append(edit.path)
                        elif edit.operation == "delete":
                            result.files_deleted.append(edit.path)
                        else:
                            result.files_modified.append(edit.path)
                            
                    except Exception as e:
                        edit.error = str(e)
                        result.errors.append(f"{edit.path}: {e}")
                        raise  # Trigger rollback
                
                # All edits succeeded
                txn.state = TransactionState.COMMITTED
                result.success = True
                
                # Record in undo history as a group
                if session:
                    await self._record_transaction_history(txn, session)
                    
        except Exception as e:
            # Rollback all applied edits
            txn.state = TransactionState.ROLLED_BACK
            result.rolled_back = True
            
            for edit in reversed(applied_edits):
                try:
                    await self._rollback_edit(edit)
                except Exception as rollback_error:
                    result.errors.append(f"Rollback failed for {edit.path}: {rollback_error}")
        
        finally:
            # Clean up transaction
            if txn.id in self._active_transactions:
                del self._active_transactions[txn.id]
        
        return result
    
    @asynccontextmanager
    async def _acquire_all_locks(self, paths: list[str]):
        """Acquire locks for multiple files in sorted order to prevent deadlocks"""
        sorted_paths = sorted(set(paths))
        
        async def acquire_recursive(remaining: list[str]):
            if not remaining:
                yield
                return
            
            path = remaining[0]
            async with self._file_lock.acquire(path):
                async for _ in acquire_recursive(remaining[1:]):
                    yield
        
        async for _ in acquire_recursive(sorted_paths):
            yield
    
    async def _apply_edit(self, edit: FileEdit):
        """Apply a single edit"""
        path = Path(edit.path)
        
        if edit.operation == "create":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(edit.new_content)
            
        elif edit.operation == "delete":
            path.unlink()
            
        elif edit.operation == "edit":
            path.write_text(edit.new_content)
    
    async def _rollback_edit(self, edit: FileEdit):
        """Rollback a single edit"""
        path = Path(edit.path)
        
        if edit.operation == "create":
            # Remove created file
            if path.exists():
                path.unlink()
                
        elif edit.operation == "delete":
            # Restore deleted file
            path.write_text(edit.old_content)
            
        elif edit.operation == "edit":
            # Restore original content
            path.write_text(edit.old_content)
    
    async def _record_transaction_history(self, txn: Transaction, session):
        """Record transaction in undo history as a group"""
        history = session.get_undo_history()
        
        # Record as a transaction group
        history.record_transaction(
            transaction_id=txn.id,
            edits=[
                {
                    "file_path": edit.path,
                    "before_content": edit.old_content,
                    "after_content": edit.new_content,
                    "operation": edit.operation,
                }
                for edit in txn.edits
            ],
            description=txn.description or f"Multi-file edit ({len(txn.edits)} files)",
            snapshot_hash=txn.snapshot_hash,
        )


# Convenience function for simple atomic multi-file edits
async def atomic_edit(
    edits: list[dict],
    session: Optional[Any] = None,
    description: str = "",
) -> TransactionResult:
    """Execute multiple file edits atomically
    
    Args:
        edits: List of edit dicts with keys:
            - path: File path
            - old_content: Current content (for validation)
            - new_content: New content
            - operation: 'edit', 'create', or 'delete' (default: 'edit')
        session: Optional session for snapshot tracking
        description: Description for undo history
        
    Returns:
        TransactionResult with success/failure info
        
    Example:
        result = await atomic_edit([
            {"path": "file1.py", "old_content": "...", "new_content": "..."},
            {"path": "file2.py", "old_content": "...", "new_content": "..."},
        ])
    """
    manager = AtomicEditManager.get_instance()
    txn = manager.create_transaction(description)
    
    for edit in edits:
        operation = edit.get("operation", "edit")
        txn.add_edit(
            path=edit["path"],
            old_content=edit.get("old_content", ""),
            new_content=edit.get("new_content", ""),
            operation=operation,
        )
    
    return await manager.commit_transaction(txn, session)


@asynccontextmanager
async def transaction(description: str = "", session: Optional[Any] = None):
    """Context manager for building and committing a transaction
    
    Example:
        async with transaction("Update API", session) as txn:
            txn.add_edit("api.py", old_content, new_content)
            txn.add_edit("tests.py", old_test, new_test)
        # Transaction is automatically committed on exit
    """
    manager = AtomicEditManager.get_instance()
    txn = manager.create_transaction(description)
    
    class TransactionContext:
        def __init__(self, txn: Transaction):
            self._txn = txn
            self.result: Optional[TransactionResult] = None
        
        def add_edit(self, path: str, old_content: str, new_content: str):
            self._txn.add_edit(path, old_content, new_content, operation="edit")
        
        def add_create(self, path: str, content: str):
            self._txn.add_create(path, content)
        
        def add_delete(self, path: str, current_content: str):
            self._txn.add_delete(path, current_content)
    
    ctx = TransactionContext(txn)
    
    try:
        yield ctx
        # Commit on successful exit
        ctx.result = await manager.commit_transaction(txn, session)
        if not ctx.result.success:
            raise RuntimeError(f"Transaction failed: {ctx.result.errors}")
    except Exception:
        # Transaction is rolled back on exception
        if txn.state == TransactionState.PENDING:
            txn.state = TransactionState.FAILED
        raise
