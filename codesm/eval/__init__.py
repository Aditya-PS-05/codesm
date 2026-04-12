"""Eval harness for codesm.

Runs a task file against the agent and produces a structured JSON report
capturing provider, token usage, tool calls, iterations, context compaction,
permission denials, tool errors, wall clock time, and assertion verdict.
"""

from codesm.eval.task import EvalTask, load_task
from codesm.eval.metrics import EvalReport
from codesm.eval.runner import run_task

__all__ = ["EvalTask", "load_task", "EvalReport", "run_task"]
