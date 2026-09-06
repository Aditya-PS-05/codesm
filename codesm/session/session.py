"""Session management - tracks conversation state"""

import asyncio
import json
import logging
import sqlite3
from copy import deepcopy
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

from codesm.storage.storage import Storage
from codesm.session.title import create_default_title, is_default_title, generate_title_sync, generate_title_async
from codesm.undo_history import UndoHistory
from codesm.index import ProjectIndexer

if TYPE_CHECKING:
    from codesm.snapshot import Snapshot


@dataclass
class Session:
    """Represents a conversation session with the agent"""
    
    id: str
    directory: Path
    title: str = field(default_factory=create_default_title)
    messages: list[dict] = field(default_factory=list)
    debug_state: dict = field(default_factory=dict)
    agent_runs: dict = field(default_factory=dict)
    usage_records: list[dict] = field(default_factory=list)
    context_messages: list[dict] = field(default_factory=list)
    context_message_count: int = 0
    last_model: str = ""
    backend: str = "native"
    backend_sessions: dict = field(default_factory=dict)
    backend_models: dict = field(default_factory=dict)
    run_state: dict = field(default_factory=dict)
    pending_response: dict = field(default_factory=dict)
    file_state: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    topics: Optional[dict] = field(default=None, repr=False)  # TopicInfo as dict
    # Branching support
    parent_id: Optional[str] = field(default=None)  # ID of parent session
    branch_point: Optional[int] = field(default=None)  # Message index where branched
    branch_name: Optional[str] = field(default=None)  # User-friendly branch label
    _title_generated: bool = field(default=False, repr=False)
    _snapshot: Optional["Snapshot"] = field(default=None, repr=False)
    _current_snapshot_hash: Optional[str] = field(default=None, repr=False)
    _undo_history: Optional[UndoHistory] = field(default=None, repr=False)
    
    @classmethod
    def create(cls, directory: Path, is_child: bool = False) -> "Session":
        """Create a new session"""
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        resolved_dir = Path(directory).resolve()
        session = cls(
            id=session_id,
            directory=resolved_dir,
            title=create_default_title(is_child),
        )
        session.save()

        return session
    
    @classmethod
    def load(cls, session_id: str) -> "Session | None":
        """Load a session from storage"""
        data = Storage.read(["session", session_id])
        if not data:
            return None
        title = data.get("title", "New Session")
        messages = data.get("messages", [])
        if data.get("pending_response", {}).get("content"):
            messages.append(data["pending_response"])
        run_state = data.get("run_state", {})
        if run_state.get("status") == "running":
            run_state = {**run_state, "status": "interrupted"}
        # If session has messages, title was already generated
        has_user_message = any(m.get("role") == "user" for m in messages)
        runs = data.get("agent_runs", {})
        for run in runs.values():
            if run.get("status") in ("waiting", "running"):
                run["status"] = "interrupted"
        return cls(
            id=data["id"],
            directory=Path(data["directory"]),
            title=title,
            messages=messages,
            debug_state=data.get("debug_state", {}),
            agent_runs=runs,
            usage_records=data.get("usage_records", []),
            context_messages=data.get("context_messages", []),
            context_message_count=data.get("context_message_count", 0),
            last_model=data.get("last_model", ""),
            backend=data.get("backend", "native"),
            backend_sessions=data.get("backend_sessions", {}),
            backend_models=data.get("backend_models", {}),
            run_state=run_state,
            file_state=data.get("file_state", {}),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            parent_id=data.get("parent_id"),
            branch_point=data.get("branch_point"),
            branch_name=data.get("branch_name"),
            _title_generated=has_user_message,
        )
    
    @classmethod
    def list_sessions(cls, topic_filter: str | None = None) -> list[dict]:
        """List all saved sessions, optionally filtered by topic"""
        from codesm.session.topics import get_topic_index
        
        keys = Storage.list(["session"])
        sessions = []
        topic_index = get_topic_index()
        
        for key in keys:
            data = Storage.read(key)
            if data:
                session_id = data["id"]
                
                # Get topics for this session
                topic_info = topic_index.get_topics(session_id)
                topics_dict = topic_info.to_dict() if topic_info else None
                
                # Apply topic filter if specified
                if topic_filter:
                    if not topic_info:
                        continue
                    if topic_info.primary != topic_filter and topic_filter not in topic_info.secondary:
                        continue
                
                sessions.append({
                    "id": session_id,
                    "title": data.get("title", "New Session"),
                    "updated_at": data.get("updated_at"),
                    "topics": topics_dict,
                })
        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
    
    def save(self):
        """Save session to storage"""
        self.updated_at = datetime.now()
        data = {
            "id": self.id,
            "directory": str(self.directory),
            "title": self.title,
            "messages": self.messages,
            "debug_state": self.debug_state,
            "agent_runs": self.agent_runs,
            "usage_records": self.usage_records,
            "context_messages": self.context_messages,
            "context_message_count": self.context_message_count,
            "last_model": self.last_model,
            "backend": self.backend,
            "backend_sessions": self.backend_sessions,
            "backend_models": self.backend_models,
            "run_state": self.run_state,
            "pending_response": self.pending_response,
            "file_state": self.file_state,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        # Include branching fields if set
        if self.parent_id:
            data["parent_id"] = self.parent_id
        if self.branch_point is not None:
            data["branch_point"] = self.branch_point
        if self.branch_name:
            data["branch_name"] = self.branch_name
        Storage.write(["session", self.id], data)
        try:
            from codesm.memory.history import HistoryStore
            HistoryStore().sync_session(data)
        except (OSError, sqlite3.Error) as error:
            logging.getLogger(__name__).warning("History index unavailable; session JSON is saved: %s", error)

    def save_context(self, messages: list[dict]):
        """Checkpoint compact working context without discarding the original transcript."""
        self.context_messages = deepcopy(messages)
        self.context_message_count = len(self.messages)
        self.save()

    def commit_pending_response(self):
        """Retain visible partial text before an interrupted turn can be resumed."""
        partial = self.pending_response
        self.pending_response = {}
        if partial.get("content"):
            self.add_message(**partial)
    
    def add_message(self, role: str, content: str | None = None, **kwargs):
        """Add a message to the session, preserving all metadata"""
        msg = {"role": role}
        if content is not None:
            msg["content"] = content
        # Preserve tool_call_id, name, tool_calls, etc.
        msg.update(kwargs)
        self.messages.append(msg)
        
        # Auto-generate title from first user message if still default
        if role == "user" and content and not self._title_generated:
            if is_default_title(self.title):
                self.title = generate_title_sync(content)
            self._title_generated = True
        
        self.save()
        
    def get_messages(self) -> list[dict]:
        """Return valid model history, repairing incomplete tool groups after a crash."""
        result = []
        pending = {}
        completed = {}
        deferred = []

        def flush():
            for call_id in pending:
                result.append(completed.get(call_id, {
                    "role": "tool", "tool_call_id": call_id,
                    "content": "Interrupted: the outcome of this call is unknown. Inspect current state before retrying; it may already have changed files or external state.",
                }))
            pending.clear()
            completed.clear()
            result.extend(deferred)
            deferred.clear()

        source = self.messages
        if self.context_messages and 0 <= self.context_message_count <= len(self.messages):
            source = self.context_messages + self.messages[self.context_message_count:]
        for m in source:
            role = m.get("role")
            if role == "backend_input":
                # Native tools may still be running when the user answers a question.
                # Finish their tool group before injecting the answer as user context.
                deferred.append({"role": "user", "content": m.get("content", "")})
                continue
            if role == "tool":
                if m.get("tool_call_id") in pending:
                    completed[m["tool_call_id"]] = m
                continue
            if role == "system":
                flush()
                result.append(m)
                continue
            if role not in ("user", "assistant"):
                continue
            if role == "assistant" and m.get("backend") and pending and result[-1].get("backend"):
                # External agents interleave commentary, questions, and parallel tools.
                # Group their calls/results for API replay without changing the archive.
                calls = m.get("tool_calls", [])
                result[-1]["tool_calls"].extend(deepcopy(calls))
                pending.update({call["id"]: call for call in calls})
                if m.get("content"):
                    deferred.append({key: value for key, value in m.items() if key != "tool_calls"})
                continue
            flush()
            result.append(deepcopy(m) if m.get("backend") and m.get("tool_calls") else m)
            pending.update({tc["id"]: tc for tc in m.get("tool_calls", [])})
        flush()
        return result
    
    def get_messages_for_display(self) -> list[dict]:
        """Get messages formatted for display (user/assistant/tool_display)"""
        return [
            ({**m, "role": "user"} if m.get("role") == "backend_input" else m) for m in self.messages
            if m.get("role") in ("user", "assistant", "tool_display", "backend_input") and m.get("content")
        ]
    
    def set_title(self, title: str):
        """Update session title"""
        self.title = title
        self.save()
    
    async def _auto_index_topics(self):
        """Auto-index topics for this session (runs in background)"""
        try:
            from codesm.session.topics import get_topic_index
            index = get_topic_index()
            info = await index.index_session(self.id)
            self.topics = info.to_dict()
        except Exception:
            pass  # Non-critical, don't fail the session
    
    async def index_topics(self, force: bool = False):
        """Manually index topics for this session"""
        from codesm.session.topics import get_topic_index
        index = get_topic_index()
        info = await index.index_session(self.id, force=force)
        self.topics = info.to_dict()
        return info
    
    async def generate_title_from_message(self, message: str):
        """Generate a title from the first user message using LLM.
        
        Uses Claude Haiku via OpenRouter for fast title generation.
        Only generates if title is still the default.
        """
        if self._title_generated or not is_default_title(self.title):
            return
        
        try:
            title = await generate_title_async(message)
            if title and title != self.title:
                self.title = title
                self._title_generated = True
                self.save()
        except Exception:
            # Fallback already handled in generate_title_async
            pass
    
    def clear(self):
        """Clear all messages"""
        self.messages = []
        self.title = create_default_title()
        self._title_generated = False
        self.debug_state = {}
        self.agent_runs = {}
        self.context_messages = []
        self.context_message_count = 0
        self.run_state = {}
        self.pending_response = {}
        self.file_state = {}
        self.backend_sessions = {}
        from codesm.memory.history import HistoryStore
        HistoryStore().delete_session(self.id)
        Storage.delete(["todo", self.id])
        (Storage.BASE_DIR / "events" / f"{self.id}.jsonl").unlink(missing_ok=True)
        self.save()

    def delete(self):
        """Delete this session from storage"""
        self.delete_by_id(self.id)
    
    def fork(self, at_message: Optional[int] = None, branch_name: Optional[str] = None) -> "Session":
        """Fork this session to explore an alternative path.
        
        Args:
            at_message: Message index to fork from (default: current end)
            branch_name: Optional label for this branch
            
        Returns:
            New Session that is a child of this one
        """
        fork_point = at_message if at_message is not None else len(self.messages)
        
        # Create new session ID
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        
        # Copy messages up to fork point
        forked_messages = deepcopy(self.messages[:fork_point])
        
        # Generate branch name if not provided
        if not branch_name:
            branch_count = len(self.list_branches()) + 1
            branch_name = f"Branch {branch_count}"
        
        forked = Session(
            id=session_id,
            directory=self.directory,
            title=f"{self.title} ({branch_name})",
            messages=forked_messages,
            parent_id=self.id,
            branch_point=fork_point,
            branch_name=branch_name,
            _title_generated=True,  # Preserve parent's title
            last_model=self.last_model,
            backend=self.backend,
            backend_models=deepcopy(self.backend_models),
            debug_state=deepcopy(self.debug_state) if fork_point == len(self.messages) else {},
            agent_runs=deepcopy(self.agent_runs) if fork_point == len(self.messages) else {},
            context_messages=deepcopy(self.context_messages) if fork_point == len(self.messages) else [],
            context_message_count=self.context_message_count if fork_point == len(self.messages) else 0,
            file_state=deepcopy(self.file_state) if fork_point == len(self.messages) else {},
        )
        forked.save()
        if fork_point == len(self.messages):
            todos = Storage.read(["todo", self.id]) or []
            Storage.write(["todo", forked.id], [{**todo, "session_id": forked.id} for todo in todos])
        return forked
    
    def list_branches(self) -> list[dict]:
        """List all sessions that were forked from this one."""
        keys = Storage.list(["session"])
        branches = []
        for key in keys:
            data = Storage.read(key)
            if data and data.get("parent_id") == self.id:
                branches.append({
                    "id": data["id"],
                    "title": data.get("title", "Branch"),
                    "branch_name": data.get("branch_name"),
                    "branch_point": data.get("branch_point"),
                    "created_at": data.get("created_at"),
                })
        return sorted(branches, key=lambda x: x.get("created_at", ""))
    
    def get_parent(self) -> Optional["Session"]:
        """Get the parent session if this is a branch."""
        if not self.parent_id:
            return None
        return Session.load(self.parent_id)
    
    def is_branch(self) -> bool:
        """Check if this session is a branch of another."""
        return self.parent_id is not None

    @classmethod
    def delete_by_id(cls, session_id: str) -> bool:
        """Delete a session by ID"""
        try:
            from codesm.memory.history import HistoryStore
            HistoryStore().delete_session(session_id)
            Storage.delete(["session", session_id])
            Storage.delete(["todo", session_id])
            (Storage.BASE_DIR / "events" / f"{session_id}.jsonl").unlink(missing_ok=True)
            return True
        except Exception:
            return False
    
    def get_snapshot(self) -> "Snapshot":
        """Get or create the snapshot tracker for this session"""
        if self._snapshot is None:
            from codesm.snapshot import Snapshot
            self._snapshot = Snapshot(self.directory, project_id=self.id)
        return self._snapshot
    
    async def track_snapshot(self) -> Optional[str]:
        """Take a snapshot of current file state"""
        snapshot = self.get_snapshot()
        hash_val = await snapshot.track()
        if hash_val:
            self._current_snapshot_hash = hash_val
        return hash_val
    
    async def get_file_changes(self, from_hash: Optional[str] = None) -> dict:
        """Get files changed since a snapshot"""
        if from_hash is None:
            from_hash = self._current_snapshot_hash
        if not from_hash:
            return {"hash": "", "files": []}
        
        snapshot = self.get_snapshot()
        patch = await snapshot.patch(from_hash)
        return {"hash": patch.hash, "files": patch.files}
    
    def add_message_with_patch(self, role: str, content: str | None = None, 
                                patch: Optional[dict] = None, **kwargs):
        """Add a message and optionally record file patches"""
        msg = {"role": role}
        if content is not None:
            msg["content"] = content
        if patch and patch.get("files"):
            msg["_patches"] = [patch]
        msg.update(kwargs)
        self.messages.append(msg)
        
        if role == "user" and content and not self._title_generated:
            if is_default_title(self.title):
                self.title = generate_title_sync(content)
            self._title_generated = True
        
        self.save()
    
    def get_undo_history(self) -> UndoHistory:
        """Get or create the undo history for this session"""
        if self._undo_history is None:
            self._undo_history = UndoHistory()
        return self._undo_history
    
    async def extract_memories(self):
        """Extract and save memories from this session"""
        from codesm.memory import MemoryExtractor
        extractor = MemoryExtractor()
        return await extractor.extract_from_session(self.id)
