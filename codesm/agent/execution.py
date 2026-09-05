"""Execution context shared by nested async tool and model calls."""

from contextvars import ContextVar


current_context: ContextVar[dict | None] = ContextVar("codesm_execution", default=None)


def emit(context: dict, event_type: str, **fields):
    fields.setdefault("run_id", context.get("run_id", ""))
    fields.setdefault("subagent_type", context.get("subagent_type", ""))
    logger = context.get("event_logger")
    if logger is not None:
        logger.emit(event_type, **fields)
    sink = context.get("eval_events")
    if sink is not None:
        sink.append({"type": event_type, **fields})
