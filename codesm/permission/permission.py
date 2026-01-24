"""Permission system for requiring user confirmation before sensitive operations."""

import asyncio
import fnmatch
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


class PermissionResponse(str, Enum):
    """User's response to a permission request."""
    ALLOW_ONCE = "once"
    ALLOW_ALWAYS = "always"
    DENY = "deny"


@dataclass
class PermissionRequest:
    """A pending permission request waiting for user response."""
    id: str
    type: str
    command: str
    title: str
    description: str
    session_id: str
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "command": self.command,
            "title": self.title,
            "description": self.description,
            "session_id": self.session_id,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


class PermissionDeniedError(Exception):
    """Raised when a permission request is denied by the user."""
    def __init__(self, request: PermissionRequest, message: Optional[str] = None):
        self.request = request
        self.message = message or f"Permission denied for: {request.title}"
        super().__init__(self.message)


class PathBlockedError(Exception):
    """Raised when a path is blocked by security rules."""
    def __init__(self, path: str, reason: str = ""):
        self.path = path
        self.reason = reason or f"Path blocked by security policy: {path}"
        super().__init__(self.reason)


class CommandBlockedError(Exception):
    """Raised when a command is blocked by security rules."""
    def __init__(self, command: str, reason: str = ""):
        self.command = command
        self.reason = reason or f"Command blocked by security policy: {command}"
        super().__init__(self.reason)


# Git commands that require user confirmation
GIT_COMMANDS_REQUIRING_PERMISSION = [
    "commit", "push", "merge", "rebase", "reset", "checkout",
    "stash", "cherry-pick", "revert", "tag", "branch -d", 
    "branch -D", "clean", "pull", "fetch",
]

# Dangerous commands requiring permission
DANGEROUS_COMMANDS = [
    "rm -rf", "rm -r", "rmdir", "sudo", "chmod", "chown",
    "dd", "mkfs", "fdisk", "> /dev/", "curl | sh", "curl | bash",
]

# Default blocked commands (catastrophic)
DEFAULT_BLOCKED_COMMANDS = [
    "rm -rf /",
    "rm -rf /*",
    "sudo rm -rf /",
    ":(){ :|:& };:",  # Fork bomb
    "dd if=/dev/zero of=/dev/sda",
    "> /dev/sda",
]

# Default guarded paths
DEFAULT_GUARDED_PATHS = [
    "~/.ssh/*",
    "~/.gnupg/*", 
    "~/.aws/*",
    "~/.config/codesm/credentials*",
    "/etc/*",
    "/usr/*",
    "/bin/*",
    "/sbin/*",
]


class Permission:
    """Manages permission requests and user responses."""
    
    def __init__(self):
        self._pending: dict[str, dict[str, tuple[PermissionRequest, asyncio.Future]]] = {}
        self._approved: dict[str, dict[str, bool]] = {}
        self._on_request: Optional[Callable[[PermissionRequest], None]] = None
    
    def set_request_callback(self, callback: Callable[[PermissionRequest], None]):
        self._on_request = callback
    
    def is_approved(self, session_id: str, pattern: str) -> bool:
        session_approved = self._approved.get(session_id, {})
        if pattern in session_approved:
            return True
        for approved_pattern in session_approved:
            if approved_pattern == "*":
                return True
            if approved_pattern.endswith("*") and pattern.startswith(approved_pattern[:-1]):
                return True
        return False
    
    async def ask(
        self,
        session_id: str,
        type: str,
        command: str,
        title: str,
        description: str,
        metadata: Optional[dict] = None,
    ) -> None:
        import logging
        logger = logging.getLogger(__name__)

        pattern = f"{type}:{command}"
        logger.info(f"Permission.ask called: session={session_id}, type={type}, pattern={pattern}")
        logger.info(f"Approved sessions: {list(self._approved.keys())}")

        if self.is_approved(session_id, type):
            logger.info(f"Permission auto-approved for type: {type}")
            return
        if self.is_approved(session_id, pattern):
            logger.info(f"Permission auto-approved for pattern: {pattern}")
            return

        logger.info(f"Permission not auto-approved, showing modal...")
        
        request = PermissionRequest(
            id=str(uuid.uuid4()),
            type=type,
            command=command,
            title=title,
            description=description,
            session_id=session_id,
            metadata=metadata or {},
        )
        
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        
        if session_id not in self._pending:
            self._pending[session_id] = {}
        self._pending[session_id][request.id] = (request, future)
        
        if self._on_request:
            self._on_request(request)
        
        try:
            response = await future
            if response == PermissionResponse.DENY:
                raise PermissionDeniedError(request)
        except asyncio.CancelledError:
            raise PermissionDeniedError(request, "Permission request cancelled")
        finally:
            if session_id in self._pending and request.id in self._pending[session_id]:
                del self._pending[session_id][request.id]
    
    def respond(self, session_id: str, request_id: str, response: PermissionResponse) -> bool:
        if session_id not in self._pending or request_id not in self._pending[session_id]:
            return False
        
        request, future = self._pending[session_id][request_id]
        
        if not future.done():
            future.set_result(response)
        
        if response == PermissionResponse.ALLOW_ALWAYS:
            if session_id not in self._approved:
                self._approved[session_id] = {}
            self._approved[session_id][request.type] = True
        
        return True
    
    def get_pending(self, session_id: Optional[str] = None) -> list[PermissionRequest]:
        result = []
        sessions = [session_id] if session_id else list(self._pending.keys())
        for sid in sessions:
            if sid in self._pending:
                for request, _ in self._pending[sid].values():
                    result.append(request)
        return sorted(result, key=lambda r: r.created_at)


# Global permission instance
_permission_manager = Permission()


async def ask_permission(
    session_id: str, type: str, command: str, title: str, description: str,
    metadata: Optional[dict] = None,
) -> None:
    await _permission_manager.ask(session_id, type, command, title, description, metadata)


def respond_permission(session_id: str, request_id: str, response: PermissionResponse) -> bool:
    return _permission_manager.respond(session_id, request_id, response)


def get_pending_permissions(session_id: Optional[str] = None) -> list[PermissionRequest]:
    return _permission_manager.get_pending(session_id)


def get_permission_manager() -> Permission:
    return _permission_manager


def requires_permission(command: str) -> tuple[bool, str, str]:
    """Check if a command requires permission. Returns (needs_permission, type, reason)."""
    cmd = command.lower().strip()
    
    # Git commands
    if cmd.startswith("git "):
        git_sub = cmd[4:].split()[0] if len(cmd) > 4 else ""
        for git_cmd in GIT_COMMANDS_REQUIRING_PERMISSION:
            if git_sub == git_cmd or cmd[4:].startswith(git_cmd):
                return (True, "git", f"Git {git_cmd}")
        if "--force" in cmd or "--hard" in cmd:
            return (True, "git", "Git operation with dangerous flag")
    
    # Dangerous commands
    for dangerous in DANGEROUS_COMMANDS:
        if dangerous in cmd:
            return (True, "dangerous", f"Dangerous: {dangerous}")
    
    # GitHub CLI
    if cmd.startswith("gh "):
        gh_modify = ["pr create", "pr merge", "issue create", "release create"]
        for gh_cmd in gh_modify:
            if gh_cmd in cmd:
                return (True, "github", f"GitHub {gh_cmd}")
    
    return (False, "", "")


def is_command_blocked(
    command: str,
    blocklist: Optional[list[str]] = None,
    allowlist: Optional[list[str]] = None,
) -> tuple[bool, str]:
    """Check if a command is blocked by security rules.
    
    Args:
        command: The command to check
        blocklist: Patterns to block (glob-style)
        allowlist: Patterns to allow (if set, only these are allowed)
        
    Returns:
        (is_blocked, reason)
    """
    cmd = command.strip()
    
    # Check default blocked commands first (always blocked)
    for blocked in DEFAULT_BLOCKED_COMMANDS:
        if blocked in cmd or cmd.startswith(blocked):
            return (True, f"Blocked: catastrophic command pattern '{blocked}'")
    
    # Check custom blocklist
    if blocklist:
        for pattern in blocklist:
            if fnmatch.fnmatch(cmd, pattern) or pattern in cmd:
                return (True, f"Blocked by rule: {pattern}")
    
    # If allowlist is set, command must match at least one pattern
    if allowlist:
        for pattern in allowlist:
            if fnmatch.fnmatch(cmd, pattern) or cmd.startswith(pattern.rstrip("*")):
                return (False, "")
        return (True, "Command not in allowlist")
    
    return (False, "")


def is_path_allowed(
    path: str | Path,
    working_dir: Optional[Path] = None,
    guarded_paths: Optional[list[str]] = None,
    allowed_paths: Optional[list[str]] = None,
) -> tuple[bool, str]:
    """Check if a path is allowed for file operations.
    
    Args:
        path: The path to check
        working_dir: Current working directory (paths must be within)
        guarded_paths: Patterns for protected paths
        allowed_paths: If set, only these paths are allowed
        
    Returns:
        (is_allowed, reason)
    """
    path = Path(path).expanduser().resolve()
    path_str = str(path)
    
    # Expand guarded paths and check
    guards = guarded_paths or DEFAULT_GUARDED_PATHS
    for pattern in guards:
        expanded = str(Path(pattern).expanduser())
        if fnmatch.fnmatch(path_str, expanded):
            return (False, f"Path matches guarded pattern: {pattern}")
    
    # If allowed_paths is set, path must match
    if allowed_paths:
        for pattern in allowed_paths:
            expanded = str(Path(pattern).expanduser())
            if fnmatch.fnmatch(path_str, expanded) or path_str.startswith(expanded.rstrip("*")):
                return (True, "")
        return (False, "Path not in allowed list")
    
    # If working_dir is set, path must be within it
    if working_dir:
        working_dir = working_dir.resolve()
        try:
            path.relative_to(working_dir)
            return (True, "")
        except ValueError:
            return (False, f"Path outside working directory: {working_dir}")
    
    return (True, "")


def check_path_permission(
    path: str | Path,
    working_dir: Optional[Path] = None,
    guarded_paths: Optional[list[str]] = None,
    allowed_paths: Optional[list[str]] = None,
) -> None:
    """Check path permission and raise PathBlockedError if blocked."""
    allowed, reason = is_path_allowed(path, working_dir, guarded_paths, allowed_paths)
    if not allowed:
        raise PathBlockedError(str(path), reason)


def check_command_permission(
    command: str,
    blocklist: Optional[list[str]] = None,
    allowlist: Optional[list[str]] = None,
) -> None:
    """Check command permission and raise CommandBlockedError if blocked."""
    blocked, reason = is_command_blocked(command, blocklist, allowlist)
    if blocked:
        raise CommandBlockedError(command, reason)
