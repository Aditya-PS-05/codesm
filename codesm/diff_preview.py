"""Diff preview system for showing file changes before applying."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Optional
import uuid


class DiffPreviewResponse(str, Enum):
    """Response from diff preview."""
    APPLY = "apply"
    SKIP = "skip"
    CANCEL = "cancel"


@dataclass
class DiffPreviewRequest:
    """A request to preview a diff before applying."""
    id: str
    file_path: str
    old_content: str
    new_content: str
    tool_name: str
    session_id: str
    created_at: datetime = field(default_factory=datetime.now)
    
    @property
    def file_name(self) -> str:
        return Path(self.file_path).name
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "old_content": self.old_content,
            "new_content": self.new_content,
            "tool_name": self.tool_name,
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
        }


class DiffPreviewCancelledError(Exception):
    """Raised when a diff preview is cancelled by the user."""
    def __init__(self, request: DiffPreviewRequest, message: Optional[str] = None):
        self.request = request
        self.message = message or f"Diff preview cancelled for: {request.file_name}"
        super().__init__(self.message)


class DiffPreviewSkippedError(Exception):
    """Raised when a diff preview is skipped by the user."""
    def __init__(self, request: DiffPreviewRequest, message: Optional[str] = None):
        self.request = request
        self.message = message or f"Edit skipped for: {request.file_name}"
        super().__init__(self.message)


class DiffPreview:
    """Manages diff preview requests and responses."""
    
    def __init__(self):
        self._pending: dict[str, dict[str, tuple[DiffPreviewRequest, asyncio.Future]]] = {}
        self._enabled: bool = True  # Global toggle
        self._session_enabled: dict[str, bool] = {}  # Per-session toggle
        self._on_request: Optional[Callable[[DiffPreviewRequest], None]] = None
    
    def set_request_callback(self, callback: Callable[[DiffPreviewRequest], None]):
        """Set callback for when a preview request is made."""
        self._on_request = callback
    
    def is_enabled(self, session_id: Optional[str] = None) -> bool:
        """Check if diff preview is enabled."""
        if not self._enabled:
            return False
        if session_id and session_id in self._session_enabled:
            return self._session_enabled[session_id]
        return self._enabled
    
    def set_enabled(self, enabled: bool, session_id: Optional[str] = None):
        """Enable or disable diff preview."""
        if session_id:
            self._session_enabled[session_id] = enabled
        else:
            self._enabled = enabled
    
    async def preview(
        self,
        session_id: str,
        file_path: str,
        old_content: str,
        new_content: str,
        tool_name: str = "edit",
    ) -> DiffPreviewResponse:
        """Request a diff preview from the user.
        
        Returns:
            DiffPreviewResponse indicating user's choice
            
        Raises:
            DiffPreviewCancelledError: If user cancels all edits
            DiffPreviewSkippedError: If user skips this specific edit
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # If disabled, auto-apply
        if not self.is_enabled(session_id):
            return DiffPreviewResponse.APPLY
        
        # If content is the same, no preview needed
        if old_content == new_content:
            return DiffPreviewResponse.SKIP
        
        request = DiffPreviewRequest(
            id=str(uuid.uuid4()),
            file_path=file_path,
            old_content=old_content,
            new_content=new_content,
            tool_name=tool_name,
            session_id=session_id,
        )
        
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        
        if session_id not in self._pending:
            self._pending[session_id] = {}
        self._pending[session_id][request.id] = (request, future)
        
        logger.info(f"Diff preview requested for: {request.file_name}")
        
        # Trigger callback to show modal
        if self._on_request:
            self._on_request(request)
        
        try:
            response = await future
            
            if response == DiffPreviewResponse.CANCEL:
                raise DiffPreviewCancelledError(request)
            elif response == DiffPreviewResponse.SKIP:
                raise DiffPreviewSkippedError(request)
            
            return response
            
        except asyncio.CancelledError:
            raise DiffPreviewCancelledError(request, "Diff preview was cancelled")
        finally:
            if session_id in self._pending and request.id in self._pending[session_id]:
                del self._pending[session_id][request.id]
    
    def respond(self, session_id: str, request_id: str, response: DiffPreviewResponse) -> bool:
        """Respond to a diff preview request."""
        if session_id not in self._pending or request_id not in self._pending[session_id]:
            return False
        
        request, future = self._pending[session_id][request_id]
        
        if not future.done():
            future.set_result(response)
        
        return True
    
    def get_pending(self, session_id: Optional[str] = None) -> list[DiffPreviewRequest]:
        """Get pending preview requests."""
        result = []
        sessions = [session_id] if session_id else list(self._pending.keys())
        for sid in sessions:
            if sid in self._pending:
                for request, _ in self._pending[sid].values():
                    result.append(request)
        return sorted(result, key=lambda r: r.created_at)


# Global diff preview manager
_diff_preview_manager = DiffPreview()


async def request_diff_preview(
    session_id: str,
    file_path: str,
    old_content: str,
    new_content: str,
    tool_name: str = "edit",
) -> DiffPreviewResponse:
    """Request a diff preview from the user."""
    return await _diff_preview_manager.preview(
        session_id, file_path, old_content, new_content, tool_name
    )


def respond_diff_preview(session_id: str, request_id: str, response: DiffPreviewResponse) -> bool:
    """Respond to a diff preview request."""
    return _diff_preview_manager.respond(session_id, request_id, response)


def get_diff_preview_manager() -> DiffPreview:
    """Get the global diff preview manager."""
    return _diff_preview_manager


def set_diff_preview_enabled(enabled: bool, session_id: Optional[str] = None):
    """Enable or disable diff preview globally or per-session."""
    _diff_preview_manager.set_enabled(enabled, session_id)


def is_diff_preview_enabled(session_id: Optional[str] = None) -> bool:
    """Check if diff preview is enabled."""
    return _diff_preview_manager.is_enabled(session_id)
