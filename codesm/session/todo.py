"""Todo tracking for agent tasks"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import uuid4

from codesm.storage.storage import Storage


TodoStatus = Literal["pending", "in_progress", "done", "cancelled"]


@dataclass
class TodoItem:
    """A single todo item"""
    
    id: str
    session_id: str
    content: str
    status: TodoStatus = "pending"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    priority: int = 0  # Higher = more important
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "content": self.content,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "priority": self.priority,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "TodoItem":
        return cls(
            id=data["id"],
            session_id=data["session_id"],
            content=data["content"],
            status=data.get("status", "pending"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            priority=data.get("priority", 0),
        )


class TodoList:
    """Manages todos for a session"""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._todos: list[TodoItem] = []
        self._load()
    
    def _storage_key(self) -> list[str]:
        return ["todo", self.session_id]
    
    def _load(self):
        """Load todos from storage"""
        data = Storage.read(self._storage_key())
        if data and isinstance(data, list):
            self._todos = [TodoItem.from_dict(item) for item in data]
        else:
            self._todos = []
    
    def _save(self):
        """Save todos to storage"""
        Storage.write(self._storage_key(), [t.to_dict() for t in self._todos])
    
    def add(self, content: str, priority: int = 0) -> TodoItem:
        """Add a new todo"""
        todo = TodoItem(
            id=f"todo_{uuid4().hex[:8]}",
            session_id=self.session_id,
            content=content,
            priority=priority,
        )
        self._todos.append(todo)
        self._save()
        return todo
    
    def get(self, todo_id: str) -> TodoItem | None:
        """Get a todo by ID"""
        for todo in self._todos:
            if todo.id == todo_id:
                return todo
        return None
    
    def update_status(self, todo_id: str, status: TodoStatus) -> TodoItem | None:
        """Update a todo's status"""
        todo = self.get(todo_id)
        if todo:
            todo.status = status
            todo.updated_at = datetime.now()
            if status == "done":
                todo.completed_at = datetime.now()
            self._save()
        return todo
    
    def update_content(self, todo_id: str, content: str) -> TodoItem | None:
        """Update a todo's content"""
        todo = self.get(todo_id)
        if todo:
            todo.content = content
            todo.updated_at = datetime.now()
            self._save()
        return todo
    
    def delete(self, todo_id: str) -> bool:
        """Delete a todo"""
        for i, todo in enumerate(self._todos):
            if todo.id == todo_id:
                self._todos.pop(i)
                self._save()
                return True
        return False
    
    def list(self, status: TodoStatus | None = None) -> list[TodoItem]:
        """List todos, optionally filtered by status"""
        todos = self._todos
        if status:
            todos = [t for t in todos if t.status == status]
        # Sort by priority (high first) then by created_at (oldest first)
        return sorted(todos, key=lambda t: (-t.priority, t.created_at))
    
    def pending(self) -> list[TodoItem]:
        """Get pending todos"""
        return self.list(status="pending")
    
    def in_progress(self) -> list[TodoItem]:
        """Get in-progress todos"""
        return self.list(status="in_progress")
    
    def done(self) -> list[TodoItem]:
        """Get completed todos"""
        return self.list(status="done")
    
    def clear_done(self) -> int:
        """Remove all completed todos, return count removed"""
        original_count = len(self._todos)
        self._todos = [t for t in self._todos if t.status != "done"]
        self._save()
        return original_count - len(self._todos)
    
    def format_list(self, include_done: bool = False) -> str:
        """Format todos as a readable list"""
        todos = self.list()
        if not include_done:
            todos = [t for t in todos if t.status != "done"]
        
        if not todos:
            return "No todos."
        
        lines = []
        status_icons = {
            "pending": "[ ]",
            "in_progress": "[~]",
            "done": "[x]",
            "cancelled": "[-]",
        }
        
        for todo in todos:
            icon = status_icons.get(todo.status, "[ ]")
            priority_str = f" (P{todo.priority})" if todo.priority > 0 else ""
            lines.append(f"{icon} {todo.id}: {todo.content}{priority_str}")
        
        return "\n".join(lines)
    
    def summary(self) -> dict:
        """Get summary stats"""
        total = len(self._todos)
        pending = len([t for t in self._todos if t.status == "pending"])
        in_progress = len([t for t in self._todos if t.status == "in_progress"])
        done = len([t for t in self._todos if t.status == "done"])
        cancelled = len([t for t in self._todos if t.status == "cancelled"])
        return {
            "total": total,
            "pending": pending,
            "in_progress": in_progress,
            "done": done,
            "cancelled": cancelled,
        }
