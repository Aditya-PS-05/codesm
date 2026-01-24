"""Audit logging system for tracking agent actions."""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AuditAction(str, Enum):
    """Types of auditable actions."""
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_EDIT = "file_edit"
    BASH_EXECUTE = "bash_execute"
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESPONSE = "permission_response"
    SESSION_CREATE = "session_create"
    SESSION_FORK = "session_fork"
    GIT_OPERATION = "git_operation"


@dataclass
class AuditEntry:
    """A single audit log entry."""
    timestamp: str
    action: str
    tool: Optional[str] = None
    session_id: Optional[str] = None
    details: dict = field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    
    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class AuditLog:
    """Manages audit logging for agent actions."""
    
    _instance: Optional["AuditLog"] = None
    
    def __init__(self, log_path: Optional[Path] = None, enabled: bool = True):
        self.enabled = enabled
        self._entries: list[AuditEntry] = []
        self._max_memory_entries = 1000  # Keep last 1000 in memory
        
        if log_path:
            self.log_path = log_path
        else:
            # Default path
            data_dir = Path.home() / ".local" / "share" / "codesm"
            data_dir.mkdir(parents=True, exist_ok=True)
            self.log_path = data_dir / "audit.jsonl"
    
    @classmethod
    def get_instance(cls) -> "AuditLog":
        """Get the singleton audit log instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def configure(cls, log_path: Optional[Path] = None, enabled: bool = True):
        """Configure the singleton instance."""
        cls._instance = cls(log_path=log_path, enabled=enabled)
    
    def log(
        self,
        action: AuditAction,
        tool: Optional[str] = None,
        session_id: Optional[str] = None,
        details: Optional[dict] = None,
        success: bool = True,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> AuditEntry:
        """Log an audit entry."""
        entry = AuditEntry(
            timestamp=datetime.now().isoformat(),
            action=action.value,
            tool=tool,
            session_id=session_id,
            details=details or {},
            success=success,
            error=error,
            duration_ms=duration_ms,
        )
        
        if not self.enabled:
            return entry
        
        # Add to memory buffer
        self._entries.append(entry)
        if len(self._entries) > self._max_memory_entries:
            self._entries = self._entries[-self._max_memory_entries:]
        
        # Write to file
        try:
            with open(self.log_path, "a") as f:
                f.write(entry.to_json() + "\n")
        except Exception as e:
            logger.warning(f"Failed to write audit log: {e}")
        
        return entry
    
    def log_tool_call(
        self,
        tool: str,
        args: dict,
        session_id: Optional[str] = None,
    ) -> AuditEntry:
        """Log a tool call."""
        # Sanitize sensitive data
        sanitized_args = self._sanitize_args(args)
        return self.log(
            action=AuditAction.TOOL_CALL,
            tool=tool,
            session_id=session_id,
            details={"args": sanitized_args},
        )
    
    def log_tool_result(
        self,
        tool: str,
        success: bool,
        result_preview: Optional[str] = None,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> AuditEntry:
        """Log a tool result."""
        details = {}
        if result_preview:
            # Truncate long results
            details["result_preview"] = result_preview[:500] if len(result_preview) > 500 else result_preview
        return self.log(
            action=AuditAction.TOOL_RESULT,
            tool=tool,
            session_id=session_id,
            details=details,
            success=success,
            error=error,
            duration_ms=duration_ms,
        )
    
    def log_file_operation(
        self,
        action: AuditAction,
        path: str,
        session_id: Optional[str] = None,
        details: Optional[dict] = None,
        success: bool = True,
        error: Optional[str] = None,
    ) -> AuditEntry:
        """Log a file operation (read/write/edit)."""
        op_details = {"path": path}
        if details:
            op_details.update(details)
        return self.log(
            action=action,
            session_id=session_id,
            details=op_details,
            success=success,
            error=error,
        )
    
    def log_bash(
        self,
        command: str,
        exit_code: Optional[int] = None,
        session_id: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> AuditEntry:
        """Log a bash command execution."""
        return self.log(
            action=AuditAction.BASH_EXECUTE,
            tool="bash",
            session_id=session_id,
            details={"command": command, "exit_code": exit_code},
            success=exit_code == 0 if exit_code is not None else True,
            duration_ms=duration_ms,
        )
    
    def log_permission(
        self,
        request_type: str,
        command: str,
        response: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AuditEntry:
        """Log a permission request/response."""
        action = AuditAction.PERMISSION_RESPONSE if response else AuditAction.PERMISSION_REQUEST
        return self.log(
            action=action,
            session_id=session_id,
            details={
                "type": request_type,
                "command": command,
                "response": response,
            },
        )
    
    def get_recent(self, count: int = 50, session_id: Optional[str] = None) -> list[AuditEntry]:
        """Get recent audit entries from memory."""
        entries = self._entries
        if session_id:
            entries = [e for e in entries if e.session_id == session_id]
        return entries[-count:]
    
    def get_session_history(self, session_id: str) -> list[AuditEntry]:
        """Get all audit entries for a session."""
        return [e for e in self._entries if e.session_id == session_id]
    
    def search(
        self,
        action: Optional[AuditAction] = None,
        tool: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Search audit entries with filters."""
        results = []
        for entry in reversed(self._entries):
            if action and entry.action != action.value:
                continue
            if tool and entry.tool != tool:
                continue
            if since:
                entry_time = datetime.fromisoformat(entry.timestamp)
                if entry_time < since:
                    break
            results.append(entry)
            if len(results) >= limit:
                break
        return results
    
    def format_for_display(self, entries: list[AuditEntry], verbose: bool = False) -> str:
        """Format audit entries for TUI display."""
        lines = []
        for entry in entries:
            time_str = entry.timestamp.split("T")[1].split(".")[0]  # HH:MM:SS
            status = "✓" if entry.success else "✗"
            
            if entry.tool:
                line = f"{time_str} {status} [{entry.tool}] {entry.action}"
            else:
                line = f"{time_str} {status} {entry.action}"
            
            if verbose and entry.details:
                detail_str = ", ".join(f"{k}={v}" for k, v in list(entry.details.items())[:3])
                if len(detail_str) > 60:
                    detail_str = detail_str[:57] + "..."
                line += f" ({detail_str})"
            
            if entry.error:
                line += f" ERROR: {entry.error[:30]}"
            
            lines.append(line)
        
        return "\n".join(lines)
    
    def _sanitize_args(self, args: dict) -> dict:
        """Remove sensitive data from args before logging."""
        sanitized = {}
        sensitive_keys = {"password", "api_key", "token", "secret", "credential"}
        
        for key, value in args.items():
            if any(s in key.lower() for s in sensitive_keys):
                sanitized[key] = "[REDACTED]"
            elif isinstance(value, str) and len(value) > 1000:
                sanitized[key] = value[:500] + f"... [{len(value)} chars]"
            else:
                sanitized[key] = value
        
        return sanitized
    
    def clear_memory(self):
        """Clear in-memory entries (file remains)."""
        self._entries = []
    
    def get_stats(self, session_id: Optional[str] = None) -> dict:
        """Get statistics about logged actions."""
        entries = self._entries
        if session_id:
            entries = [e for e in entries if e.session_id == session_id]
        
        stats = {
            "total_entries": len(entries),
            "successful": sum(1 for e in entries if e.success),
            "failed": sum(1 for e in entries if not e.success),
            "by_action": {},
            "by_tool": {},
        }
        
        for entry in entries:
            stats["by_action"][entry.action] = stats["by_action"].get(entry.action, 0) + 1
            if entry.tool:
                stats["by_tool"][entry.tool] = stats["by_tool"].get(entry.tool, 0) + 1
        
        return stats


# Convenience functions
def get_audit_log() -> AuditLog:
    """Get the global audit log instance."""
    return AuditLog.get_instance()


def audit_tool_call(tool: str, args: dict, session_id: Optional[str] = None) -> AuditEntry:
    """Log a tool call."""
    return get_audit_log().log_tool_call(tool, args, session_id)


def audit_tool_result(
    tool: str,
    success: bool,
    result_preview: Optional[str] = None,
    error: Optional[str] = None,
    duration_ms: Optional[int] = None,
    session_id: Optional[str] = None,
) -> AuditEntry:
    """Log a tool result."""
    return get_audit_log().log_tool_result(tool, success, result_preview, error, duration_ms, session_id)
