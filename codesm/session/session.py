"""Session management - tracks conversation state"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from codesm.storage.storage import Storage
from codesm.session.title import create_default_title, is_default_title, generate_title_sync


@dataclass
class Session:
    """Represents a conversation session with the agent"""
    
    id: str
    directory: Path
    title: str = field(default_factory=create_default_title)
    messages: list[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    _title_generated: bool = field(default=False, repr=False)
    
    @classmethod
    def create(cls, directory: Path, is_child: bool = False) -> "Session":
        """Create a new session"""
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        session = cls(
            id=session_id,
            directory=Path(directory).resolve(),
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
        # If session has messages, title was already generated
        has_user_message = any(m.get("role") == "user" for m in messages)
        return cls(
            id=data["id"],
            directory=Path(data["directory"]),
            title=title,
            messages=messages,
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            _title_generated=has_user_message,
        )
    
    @classmethod
    def list_sessions(cls) -> list[dict]:
        """List all saved sessions"""
        keys = Storage.list(["session"])
        sessions = []
        for key in keys:
            data = Storage.read(key)
            if data:
                sessions.append({
                    "id": data["id"],
                    "title": data.get("title", "New Session"),
                    "updated_at": data.get("updated_at"),
                })
        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
    
    def save(self):
        """Save session to storage"""
        self.updated_at = datetime.now()
        Storage.write(["session", self.id], {
            "id": self.id,
            "directory": str(self.directory),
            "title": self.title,
            "messages": self.messages,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        })
    
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
        """Get all messages for LLM context (preserves full structure)"""
        return list(self.messages)
    
    def get_messages_for_display(self) -> list[dict]:
        """Get messages formatted for display (user/assistant only)"""
        return [
            m for m in self.messages 
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
    
    def set_title(self, title: str):
        """Update session title"""
        self.title = title
        self.save()
    
    def clear(self):
        """Clear all messages"""
        self.messages = []
        self.save()
