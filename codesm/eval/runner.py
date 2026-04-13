"""Eval runner.

Takes an EvalTask, runs the setup shell commands, invokes the Agent with an
instrumented context dict, runs the assertion shell commands, and returns a
populated EvalReport.

The runner does not modify the Agent or ReAct loop surface directly. It
relies on two conventions added to the context dict:

  context["eval_events"]   list the ReAct loop appends compaction/error
                           events to if present.
  context["eval_usage"]    dict providers may populate with token counts.

Both are backwards compatible: if the key is absent, everything still runs
normally.
"""

import asyncio
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from codesm.eval.metrics import (
    AssertionResult,
    CompactionEvent,
    EvalReport,
    ToolErrorEvent,
)
from codesm.eval.task import EvalTask

logger = logging.getLogger(__name__)


def _run_shell(cmd: str, cwd: Path, timeout: int = 60) -> tuple[int, str, str]:
    """Run one shell command and return (exit_code, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return 124, out, err + f"\n[timeout after {timeout}s]"
    except Exception as e:
        return 1, "", f"[runner error: {e}]"


def _provider_from_model(model: str) -> str:
    """Extract the provider segment from a model id like 'anthropic/claude-...'."""
    if "/" in model:
        return model.split("/", 1)[0]
    return "anthropic"


async def run_task(
    task: EvalTask,
    task_file: Optional[Path] = None,
    model_override: Optional[str] = None,
    directory_override: Optional[Path] = None,
) -> EvalReport:
    """Run one eval task end to end and return a populated EvalReport.

    The caller is responsible for printing or persisting the report.
    """
    from codesm.agent.agent import Agent
    from codesm.auth.credentials import CredentialStore

    report = EvalReport(
        task_name=task.name,
        task_description=task.description,
        task_file=str(task_file) if task_file else "",
    )

    # Resolve working directory
    if directory_override is not None:
        workdir = Path(directory_override).resolve()
    elif task.directory:
        workdir = Path(task.directory).resolve()
    else:
        workdir = Path.cwd()

    if not workdir.exists():
        workdir.mkdir(parents=True, exist_ok=True)

    # Resolve model
    model = model_override or task.model
    if not model:
        store = CredentialStore()
        model = store.get_preferred_model() or "anthropic/claude-sonnet-4-20250514"
    report.model = model
    report.provider = _provider_from_model(model)

    wall_start = time.time()

    # 1. Setup shell commands
    setup_start = time.time()
    for cmd in task.setup:
        code, stdout, stderr = _run_shell(cmd, workdir, timeout=60)
        if code != 0:
            report.setup_ok = False
            report.error = f"Setup failed (exit {code}): {cmd}\nstderr: {stderr.strip()}"
            report.setup_ms = int((time.time() - setup_start) * 1000)
            report.wall_clock_ms = int((time.time() - wall_start) * 1000)
            return report
    report.setup_ms = int((time.time() - setup_start) * 1000)

    # 2. Run the agent with an instrumented context
    eval_events: list[dict] = []
    eval_usage: dict = {}

    agent_start = time.time()
    try:
        agent = Agent(
            directory=workdir,
            model=model,
            max_iterations=task.max_iterations,
        )

        # Inject the two instrumentation hooks so Agent.chat can flow them
        # into the ReAct loop context dict. Agent.__init__ declares these.
        agent._eval_events = eval_events
        agent._eval_usage = eval_usage

        full_response = ""
        tool_counts: dict[str, int] = {}

        async def run_agent():
            nonlocal full_response
            async for chunk in agent.chat(task.prompt):
                # Agent.chat yields StreamChunk; use getattr because the
                # declared return type in agent.py is AsyncIterator[str].
                ctype = getattr(chunk, "type", None)
                ccontent = getattr(chunk, "content", "") or ""
                cname = getattr(chunk, "name", None)
                if ctype == "text" and ccontent:
                    full_response += ccontent
                elif ctype == "tool_call":
                    tool_name = cname or "unknown"
                    tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                # tool_result sniffing removed: the ReAct loop now emits
                # tool_error events directly, drained below.

        try:
            await asyncio.wait_for(run_agent(), timeout=task.timeout)
        except asyncio.TimeoutError:
            report.agent_ok = False
            report.error = f"Agent timed out after {task.timeout}s"

        await agent.cleanup()

        report.final_response = full_response[:4000]
        report.tool_calls = tool_counts

    except Exception as e:
        logger.exception("Agent run failed")
        report.agent_ok = False
        report.error = f"Agent crashed: {e}"
    finally:
        report.agent_ms = int((time.time() - agent_start) * 1000)

    # Drain instrumentation events
    for ev in eval_events:
        kind = ev.get("type")
        if kind == "iteration_start":
            report.iterations = max(report.iterations, int(ev.get("n", 0)))
        elif kind == "compaction":
            report.compaction_events.append(
                CompactionEvent(
                    iteration=int(ev.get("iteration", 0)),
                    tokens_before=int(ev.get("tokens_before", 0)),
                    tokens_after=int(ev.get("tokens_after", 0)),
                )
            )
        elif kind == "tool_error":
            report.tool_errors.append(
                ToolErrorEvent(
                    iteration=int(ev.get("iteration", 0)),
                    tool=str(ev.get("tool", "unknown")),
                    message=str(ev.get("message", ""))[:500],
                    recovered=bool(ev.get("recovered", False)),
                )
            )
        elif kind == "permission_denied":
            report.permission_denials += 1
        elif kind == "malformed_tool_call":
            report.malformed_tool_calls += 1
        elif kind == "mark_uncertain":
            report.mark_uncertain_count += 1
            sev = str(ev.get("severity", "")).lower()
            if sev in report.mark_uncertain_by_severity:
                report.mark_uncertain_by_severity[sev] += 1
        elif kind == "max_iterations":
            report.max_iterations_hit = True

    # Drain usage if the provider wrote any
    report.tokens_in = int(eval_usage.get("tokens_in", 0))
    report.tokens_out = int(eval_usage.get("tokens_out", 0))

    # Materialise the agent's final response as a file in the workdir so
    # shell assertions can grep it. Benchmarks like ambiguous-requirements
    # and adversarial-secret rely on this to observe the model's behavior.
    response_path = workdir / ".codesm-eval-response.txt"
    try:
        response_path.write_text(report.final_response or "")
    except Exception as e:
        logger.warning(f"Could not write eval response artifact: {e}")

    # 3. Run assertion shell commands
    assertion_start = time.time()
    for cmd in task.assertion:
        code, stdout, stderr = _run_shell(cmd, workdir, timeout=60)
        report.assertions.append(
            AssertionResult(
                command=cmd,
                exit_code=code,
                stdout=stdout[:2000],
                stderr=stderr[:2000],
            )
        )
    report.assertion_ms = int((time.time() - assertion_start) * 1000)

    report.wall_clock_ms = int((time.time() - wall_start) * 1000)
    return report
