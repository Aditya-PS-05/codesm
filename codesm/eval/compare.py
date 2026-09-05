"""Provider comparison for eval tasks.

Runs the same task against multiple provider + model combinations and
produces a side by side comparison. This is the shape of output a Human
Data team looks at when picking which model to train or benchmark against:
tokens, tool use, time, and verdict per provider.
"""

from dataclasses import dataclass, field
from collections import defaultdict
import math
import statistics
from pathlib import Path
from typing import Optional

from codesm.eval.metrics import EvalReport
from codesm.eval.runner import run_task
from codesm.eval.task import EvalTask


# Default model per provider for the --all-providers flag. Chosen to span
# the three API surfaces most relevant to a coding-model eval: Anthropic's
# flagship sonnet, OpenAI's flagship GPT-4o, and Google Gemini 2.5 Pro via
# OpenRouter. Override with --providers if you want different models.
DEFAULT_PROVIDER_MODELS: list[str] = [
    "anthropic/claude-sonnet-4-20250514",
    "openai/gpt-4o",
    "openrouter/google/gemini-2.5-pro-preview",
]


@dataclass
class ComparisonResult:
    """One eval task run across many provider + model pairs."""
    task_name: str
    task_description: str
    task_file: str
    runs: list[EvalReport] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.runs if r.passed)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.runs) if self.runs else False

    def to_dict(self) -> dict:
        return {
            "task_name": self.task_name,
            "task_description": self.task_description,
            "task_file": self.task_file,
            "passed_count": self.passed_count,
            "total": len(self.runs),
            "runs": [r.to_dict() for r in self.runs],
            "summary": self.summarize(),
        }

    def summarize(self) -> list[dict]:
        groups = defaultdict(list)
        for run in self.runs:
            groups[(run.model, run.variant)].append(run)
        summary = []
        for (model, variant), runs in groups.items():
            durations = sorted(r.wall_clock_ms for r in runs)
            successes = sum(r.passed for r in runs)
            priced = all(r.cost_usd is not None for r in runs)
            total_cost = sum(r.cost_usd for r in runs) if priced else None
            summary.append({"model": model, "variant": variant, "runs": len(runs),
                "passed": successes, "pass_rate": successes / len(runs),
                "p50_ms": statistics.median(durations),
                "p95_ms": durations[math.ceil(len(runs) * .95) - 1],
                "total_cost_usd": total_cost,
                "cost_per_success_usd": total_cost / successes if priced and successes else None,
                "requests": sum(r.requests for r in runs),
                "estimated_requests": sum(r.estimated_requests for r in runs),
                "human_corrections": None})
        return summary


async def run_comparison(
    task: EvalTask,
    task_file: Optional[Path] = None,
    models: Optional[list[str]] = None,
    directory_override: Optional[Path] = None,
    variants: list[str] | None = None,
    repeat: int = 1,
    config=None,
) -> ComparisonResult:
    """Run a task against multiple models sequentially and collect reports.

    Runs are sequential on purpose: each task's setup and assertion shell
    commands often touch the same filesystem paths, so parallel runs would
    race on the workspace. Serial runs give clean per-provider metrics.
    """
    if repeat < 1:
        raise ValueError("repeat must be positive")
    variants = variants or [getattr(config, "delegation", "adaptive")]
    if any(v not in ("single", "specialists", "adaptive") for v in variants):
        raise ValueError("variants must be single, specialists, or adaptive")
    models = models or [None]
    result = ComparisonResult(task.name, task.description, str(task_file or ""))
    for repetition in range(1, repeat + 1):
        for model in models:
            for variant in variants:
                report = await run_task(task, task_file=task_file, model_override=model,
                    directory_override=directory_override, variant=variant, config=config)
                report.repetition = repetition
                result.runs.append(report)

    return result


def format_comparison_table(result: ComparisonResult) -> str:
    """Render a plain text side by side comparison table.

    Columns: Provider, Model, Verdict, Iter, Tools, Tokens In/Out,
             Wall (ms), Error.
    """
    if not result.runs:
        return "No runs to display."

    headers = ["Variant", "Model", "Verdict", "Iter", "Tools", "Tok In", "Tok Out", "Wall(ms)", "Notes"]
    rows: list[list[str]] = []

    for r in result.runs:
        tool_total = sum(r.tool_calls.values())
        verdict = "PASS" if r.passed else "FAIL"

        notes_parts: list[str] = []
        if r.compaction_events:
            notes_parts.append(f"compact x{len(r.compaction_events)}")
        if r.tool_errors:
            notes_parts.append(f"tool_err x{len(r.tool_errors)}")
        if r.permission_denials:
            notes_parts.append(f"perm_deny x{r.permission_denials}")
        if r.max_iterations_hit:
            notes_parts.append("max_iter")
        if r.error:
            # Strip newlines and tabs so the table stays one row per run.
            flat_err = " ".join(r.error.split())
            notes_parts.append(flat_err[:40])
        notes = ", ".join(notes_parts) if notes_parts else "-"

        rows.append([
            r.variant,
            _shorten(r.model, 36),
            verdict,
            str(r.iterations),
            str(tool_total),
            str(r.tokens_in),
            str(r.tokens_out),
            str(r.wall_clock_ms),
            _shorten(notes, 40),
        ])

    # Compute column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)) + " |"

    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    lines = [sep, fmt_row(headers), sep]
    for row in rows:
        lines.append(fmt_row(row))
    lines.append(sep)

    # Footer
    lines.append(f"  {result.passed_count}/{len(result.runs)} passed  ::  task: {result.task_name}")
    return "\n".join(lines)


def _shorten(s: str, n: int) -> str:
    """Truncate a string to n characters, adding an ellipsis if needed."""
    if len(s) <= n:
        return s
    if n <= 3:
        return s[:n]
    return s[: n - 3] + "..."
