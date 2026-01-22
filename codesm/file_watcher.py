"""File watcher for real-time file change detection"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Set
from enum import Enum

logger = logging.getLogger(__name__)


class ChangeType(Enum):
    """Type of file change"""
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass
class FileChange:
    """Represents a file change event"""
    path: Path
    change_type: ChangeType
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def relative_path(self) -> str:
        """Get relative path for display"""
        return self.path.name
    
    def __str__(self) -> str:
        return f"{self.change_type.value}: {self.relative_path}"


@dataclass
class FileState:
    """Tracks the state of a file"""
    path: Path
    mtime: float
    size: int
    exists: bool = True


class FileWatcher:
    """Watches a directory for file changes using polling.
    
    Uses async polling approach that works across all platforms.
    For production, consider using watchdog library for native OS events.
    """
    
    # Default patterns to ignore
    DEFAULT_IGNORE_PATTERNS = {
        ".git",
        ".venv",
        "__pycache__",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "*.pyc",
        "*.pyo",
        ".DS_Store",
        "*.swp",
        "*.swo",
        "*~",
        ".codesm",
    }
    
    # File extensions to watch (empty = watch all)
    DEFAULT_WATCH_EXTENSIONS = {
        ".py", ".js", ".ts", ".tsx", ".jsx",
        ".rs", ".go", ".java", ".c", ".cpp", ".h",
        ".md", ".txt", ".json", ".yaml", ".yml", ".toml",
        ".html", ".css", ".scss", ".sh", ".bash",
    }
    
    def __init__(
        self,
        directory: Path,
        on_change: Optional[Callable[[FileChange], None]] = None,
        poll_interval: float = 1.0,
        ignore_patterns: Optional[Set[str]] = None,
        watch_extensions: Optional[Set[str]] = None,
        max_depth: int = 5,
    ):
        self.directory = Path(directory).resolve()
        self.on_change = on_change
        self.poll_interval = poll_interval
        self.ignore_patterns = ignore_patterns or self.DEFAULT_IGNORE_PATTERNS
        self.watch_extensions = watch_extensions or self.DEFAULT_WATCH_EXTENSIONS
        self.max_depth = max_depth
        
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._file_states: dict[Path, FileState] = {}
        self._change_queue: asyncio.Queue[FileChange] = asyncio.Queue()
        self._callbacks: list[Callable[[FileChange], None]] = []
        
        if on_change:
            self._callbacks.append(on_change)
    
    def add_callback(self, callback: Callable[[FileChange], None]):
        """Add a callback for file changes"""
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable[[FileChange], None]):
        """Remove a callback"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    def _should_ignore(self, path: Path) -> bool:
        """Check if a path should be ignored"""
        name = path.name
        
        # Check ignore patterns
        for pattern in self.ignore_patterns:
            if pattern.startswith("*"):
                # Wildcard pattern
                if name.endswith(pattern[1:]):
                    return True
            elif name == pattern or pattern in str(path):
                return True
        
        return False
    
    def _should_watch(self, path: Path) -> bool:
        """Check if a file should be watched"""
        if not path.is_file():
            return False
        
        if self._should_ignore(path):
            return False
        
        # Check extension
        if self.watch_extensions:
            return path.suffix.lower() in self.watch_extensions
        
        return True
    
    def _get_file_state(self, path: Path) -> Optional[FileState]:
        """Get current state of a file"""
        try:
            stat = path.stat()
            return FileState(
                path=path,
                mtime=stat.st_mtime,
                size=stat.st_size,
                exists=True,
            )
        except (FileNotFoundError, PermissionError):
            return None
    
    def _scan_directory(self) -> dict[Path, FileState]:
        """Scan directory and return current file states"""
        states = {}
        
        def scan_recursive(dir_path: Path, depth: int = 0):
            if depth > self.max_depth:
                return
            
            if self._should_ignore(dir_path):
                return
            
            try:
                for entry in dir_path.iterdir():
                    if entry.is_dir():
                        scan_recursive(entry, depth + 1)
                    elif self._should_watch(entry):
                        state = self._get_file_state(entry)
                        if state:
                            states[entry] = state
            except PermissionError:
                pass
        
        scan_recursive(self.directory)
        return states
    
    def _detect_changes(self, new_states: dict[Path, FileState]) -> list[FileChange]:
        """Compare states and detect changes"""
        changes = []
        
        # Check for new and modified files
        for path, new_state in new_states.items():
            if path not in self._file_states:
                # New file
                changes.append(FileChange(path=path, change_type=ChangeType.CREATED))
            else:
                old_state = self._file_states[path]
                if new_state.mtime != old_state.mtime or new_state.size != old_state.size:
                    # Modified file
                    changes.append(FileChange(path=path, change_type=ChangeType.MODIFIED))
        
        # Check for deleted files
        for path in self._file_states:
            if path not in new_states:
                changes.append(FileChange(path=path, change_type=ChangeType.DELETED))
        
        return changes
    
    async def _poll_loop(self):
        """Main polling loop"""
        # Initial scan
        self._file_states = self._scan_directory()
        logger.info(f"File watcher started: monitoring {len(self._file_states)} files in {self.directory}")
        
        while self._running:
            await asyncio.sleep(self.poll_interval)
            
            if not self._running:
                break
            
            try:
                new_states = self._scan_directory()
                changes = self._detect_changes(new_states)
                
                # Update states
                self._file_states = new_states
                
                # Notify callbacks
                for change in changes:
                    for callback in self._callbacks:
                        try:
                            result = callback(change)
                            # Support async callbacks
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.error(f"Error in file watcher callback: {e}")
                    
                    # Also put in queue for consumers
                    await self._change_queue.put(change)
                    
            except Exception as e:
                logger.error(f"Error in file watcher poll: {e}")
    
    async def start(self):
        """Start watching for file changes"""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"File watcher started for {self.directory}")
    
    async def stop(self):
        """Stop watching for file changes"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("File watcher stopped")
    
    async def get_next_change(self, timeout: Optional[float] = None) -> Optional[FileChange]:
        """Get the next file change from the queue"""
        try:
            if timeout:
                return await asyncio.wait_for(self._change_queue.get(), timeout)
            return await self._change_queue.get()
        except asyncio.TimeoutError:
            return None
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def watched_file_count(self) -> int:
        return len(self._file_states)
    
    def get_watched_files(self) -> list[Path]:
        """Get list of currently watched files"""
        return list(self._file_states.keys())


class FileWatcherManager:
    """Manages file watcher instances"""
    
    _instance: Optional["FileWatcherManager"] = None
    
    def __init__(self):
        self._watchers: dict[Path, FileWatcher] = {}
    
    @classmethod
    def get_instance(cls) -> "FileWatcherManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def get_watcher(
        self,
        directory: Path,
        on_change: Optional[Callable[[FileChange], None]] = None,
        **kwargs
    ) -> FileWatcher:
        """Get or create a file watcher for a directory"""
        directory = Path(directory).resolve()
        
        if directory not in self._watchers:
            self._watchers[directory] = FileWatcher(
                directory=directory,
                on_change=on_change,
                **kwargs
            )
        elif on_change:
            self._watchers[directory].add_callback(on_change)
        
        return self._watchers[directory]
    
    async def stop_all(self):
        """Stop all file watchers"""
        for watcher in self._watchers.values():
            await watcher.stop()
        self._watchers.clear()
