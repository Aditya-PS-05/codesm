"""Per-session structured event log for failure mode analysis.

The ReAct loop calls into an EventLogger to record signals that matter
for coding model evaluation: context compaction, tool errors, permission
denials, malformed tool calls, and max iteration cutoffs. Each event is
written to a per-session JSONL file so you can replay or aggregate
across real runs, and optionally also appended to an in-memory list so
the eval runner can read the same events without touching the filesystem.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_EVENTS_DIR = Path.home() / ".local" / "share" / "codesm" / "events"


class EventLogger:
    """Append only structured event log for one session."""

    def __init__(
        self,
        session_id: str,
        events_dir: Optional[Path] = None,
        memory_sink: Optional[list[dict]] = None,
        enabled: bool = True,
    ):
        self.session_id = session_id
        self.enabled = enabled
        self.memory_sink = memory_sink

        if events_dir is None:
            events_dir = DEFAULT_EVENTS_DIR
        self.events_dir = Path(events_dir)
        self.path = self.events_dir / f"{session_id}.jsonl"

        if self.enabled:
            try:
                self.events_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.warning(f"Could not create events dir {self.events_dir}: {e}")
                self.enabled = False

    def emit(self, event_type: str, **fields: Any) -> dict:
        """Record one structured event."""
        event = {
            "ts": datetime.now().isoformat(),
            "session_id": self.session_id,
            "type": event_type,
        }
        event.update(fields)

        if self.memory_sink is not None:
            self.memory_sink.append(event)

        if not self.enabled:
            return event

        try:
            with open(self.path, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except OSError as e:
            logger.warning(f"Could not write event to {self.path}: {e}")

        return event

    def iteration_start(self, n: int) -> dict:
        return self.emit("iteration_start", n=n)

    def compaction(
        self, iteration: int, tokens_before: int, tokens_after: int
    ) -> dict:
        return self.emit(
            "compaction",
            iteration=iteration,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            tokens_dropped=max(0, tokens_before - tokens_after),
        )

    def tool_error(self, iteration: int, tool: str, message: str) -> dict:
        return self.emit(
            "tool_error",
            iteration=iteration,
            tool=tool,
            message=message[:500],
        )

    def permission_denied(
        self, iteration: int, tool: str, message: str
    ) -> dict:
        return self.emit(
            "permission_denied",
            iteration=iteration,
            tool=tool,
            message=message[:300],
        )

    def malformed_tool_call(
        self,
        iteration: int,
        tool: str,
        reason: str,
        raw: str = "",
    ) -> dict:
        return self.emit(
            "malformed_tool_call",
            iteration=iteration,
            tool=tool,
            reason=reason,
            raw=raw[:300],
        )

    def max_iterations(self, n: int) -> dict:
        return self.emit("max_iterations", n=n)

    @classmethod
    def read(
        cls, session_id: str, events_dir: Optional[Path] = None
    ) -> list[dict]:
        """Load all events for a session from the JSONL file."""
        if events_dir is None:
            events_dir = DEFAULT_EVENTS_DIR
        path = Path(events_dir) / f"{session_id}.jsonl"
        if not path.exists():
            return []
        events: list[dict] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return events
