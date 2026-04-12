"""Eval report data model.

Captures everything a Human Data team would want to see about a single run:
provider, tokens, tool use shape, loop depth, context compaction, permission
denials, tool errors, wall clock, and the final assertion verdict.
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class CompactionEvent:
    """One context compaction firing inside the ReAct loop."""
    iteration: int
    tokens_before: int
    tokens_after: int

    @property
    def tokens_dropped(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)


@dataclass
class ToolErrorEvent:
    """A tool call that raised or returned an error string."""
    iteration: int
    tool: str
    message: str
    recovered: bool = False


@dataclass
class AssertionResult:
    """Outcome of one assertion shell command."""
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


@dataclass
class EvalReport:
    """Structured report for one eval run.

    Every field here should be serialisable to JSON so the full report can be
    written to disk with a single ``json.dumps``.
    """

    # Task identity
    task_name: str = ""
    task_description: str = ""
    task_file: str = ""

    # Agent configuration
    provider: str = ""
    model: str = ""

    # Token counts (best effort — some providers do not expose them)
    tokens_in: int = 0
    tokens_out: int = 0

    # ReAct loop shape
    iterations: int = 0
    max_iterations_hit: bool = False

    # Tool call shape
    tool_calls: dict[str, int] = field(default_factory=dict)
    tool_errors: list[ToolErrorEvent] = field(default_factory=list)

    # Context compaction
    compaction_events: list[CompactionEvent] = field(default_factory=list)

    # Permission system
    permission_denials: int = 0

    # Timing
    wall_clock_ms: int = 0
    setup_ms: int = 0
    agent_ms: int = 0
    assertion_ms: int = 0

    # Outcome
    setup_ok: bool = True
    agent_ok: bool = True
    assertions: list[AssertionResult] = field(default_factory=list)
    error: Optional[str] = None

    # Agent surface for debugging
    final_response: str = ""

    @property
    def assertions_passed(self) -> bool:
        """True if every assertion exited 0. Empty assertion list counts as passed."""
        return all(a.passed for a in self.assertions)

    @property
    def passed(self) -> bool:
        """Overall pass/fail verdict."""
        return self.setup_ok and self.agent_ok and self.assertions_passed and self.error is None

    @property
    def compaction_tokens_dropped(self) -> int:
        return sum(c.tokens_dropped for c in self.compaction_events)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for json.dumps."""
        d = asdict(self)
        # Expose the derived fields the JSON consumer will want
        d["verdict"] = "pass" if self.passed else "fail"
        d["assertions_passed"] = self.assertions_passed
        d["compaction_tokens_dropped"] = self.compaction_tokens_dropped
        return d
