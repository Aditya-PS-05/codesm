"""Permission system for tool execution.

This module provides a permission system that requires user confirmation
before executing potentially dangerous operations like git commands.
"""

from .permission import (
    Permission,
    PermissionRequest,
    PermissionResponse,
    PermissionDeniedError,
    PathBlockedError,
    CommandBlockedError,
    ask_permission,
    respond_permission,
    get_pending_permissions,
    get_permission_manager,
    requires_permission,
    is_command_blocked,
    is_path_allowed,
    check_path_permission,
    check_command_permission,
    GIT_COMMANDS_REQUIRING_PERMISSION,
    DANGEROUS_COMMANDS,
    DEFAULT_BLOCKED_COMMANDS,
    DEFAULT_GUARDED_PATHS,
)

__all__ = [
    "Permission",
    "PermissionRequest",
    "PermissionResponse",
    "PermissionDeniedError",
    "PathBlockedError",
    "CommandBlockedError",
    "ask_permission",
    "respond_permission",
    "get_pending_permissions",
    "get_permission_manager",
    "requires_permission",
    "is_command_blocked",
    "is_path_allowed",
    "check_path_permission",
    "check_command_permission",
    "GIT_COMMANDS_REQUIRING_PERMISSION",
    "DANGEROUS_COMMANDS",
    "DEFAULT_BLOCKED_COMMANDS",
    "DEFAULT_GUARDED_PATHS",
]
