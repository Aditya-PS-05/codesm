"""Run trusted benchmark hooks and an instrumented agent in a fresh workspace."""

import asyncio
import hashlib
from contextlib import aclosing
import os
from pathlib import Path
import shutil
import signal
import tempfile
import time

from codesm.eval.metrics import AssertionResult, CompactionEvent, EvalReport, ToolErrorEvent
from codesm.eval.task import EvalTask


async def _run_shell(cmd: str, cwd: Path, timeout: int = 60) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_shell(cmd, cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
            return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
        except asyncio.TimeoutError:
            return 124, "", f"Timeout after {timeout}s"
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()


async def run_task(task: EvalTask, task_file: Path | None = None,
                   model_override: str | None = None, directory_override: Path | None = None,
                   variant: str | None = None, config=None) -> EvalReport:
    """Copy an optional fixture directory; never run setup in the source checkout.

    Shell hooks are trusted code, not sandboxed: use relative paths in task files.
    """
    from codesm.config import Config
    source = directory_override or task.directory
    source = Path(source).resolve() if source else None
    settings = (config or Config.load(directory=source or (task_file.parent if task_file else Path.cwd()))).model_copy(deep=True)
    if variant is not None:
        if variant not in ("single", "specialists", "adaptive"):
            raise ValueError(f"Unknown evaluation variant: {variant}")
        settings.delegation = variant
    with tempfile.TemporaryDirectory(prefix="codesm-eval-") as temp:
        workdir = Path(temp) / "workspace"
        if source:
            if not source.is_dir():
                raise ValueError(f"Fixture directory does not exist: {source}")
            shutil.copytree(source, workdir, ignore=shutil.ignore_patterns(
                ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".lavish"))
        else:
            workdir.mkdir()
        return await _run(task, workdir, settings, task_file, model_override)


async def _run(task, workdir, config, task_file, model_override):
    from codesm.agent.agent import Agent
    from codesm.diff_preview import set_diff_preview_enabled
    report = EvalReport(task_name=task.name, task_description=task.description,
                        task_file=str(task_file or ""), variant=config.delegation)
    profile = config.agents.get("main")
    report.model = model_override or task.model or (profile.model if profile else None) or config.model
    report.provider = report.model.split("/", 1)[0]
    wall = time.monotonic()
    started = time.monotonic()
    for command in task.setup:
        code, _, stderr = await _run_shell(command, workdir)
        if code:
            report.setup_ok = False
            report.error = f"Setup failed ({code}): {command}\n{stderr[:2000]}"
            report.setup_ms = int((time.monotonic() - started) * 1000)
            report.wall_clock_ms = int((time.monotonic() - wall) * 1000)
            return report
    report.setup_ms = int((time.monotonic() - started) * 1000)
    protected = {}
    for name in task.protected_files:
        path = (workdir / name).resolve()
        if not path.is_relative_to(workdir) or not path.is_file():
            raise ValueError(f"Protected fixture must be an existing workspace file: {name}")
        protected[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    events, usage = [], {}
    response = []
    agent = None
    started = time.monotonic()
    try:
        agent = Agent(directory=workdir, model=report.model, config=config, max_iterations=task.max_iterations,
                      mcp_config_path=workdir / "mcp-servers.json")
        set_diff_preview_enabled(False, agent.session.id)
        agent._eval_events, agent._eval_usage = events, usage

        async def execute():
            prompt = "/debug " + task.prompt if task.debug else task.prompt
            async with aclosing(agent.chat(prompt)) as stream:
                async for chunk in stream:
                    if chunk.type == "text":
                        response.append(chunk.content)
                    elif chunk.type == "run_status":
                        report.completion_status = chunk.content
        await asyncio.wait_for(execute(), task.timeout)
        report.agent_ok = report.completion_status in ("completed", "verified")
    except asyncio.TimeoutError:
        report.agent_ok = False
        report.completion_status = "cancelled"
        report.error = f"Agent timed out after {task.timeout}s"
    except Exception as error:
        report.agent_ok = False
        report.completion_status = "failed"
        report.error = f"Agent crashed: {error}"
    finally:
        if agent is not None:
            await agent.cleanup()
        report.agent_ms = int((time.monotonic() - started) * 1000)
    report.final_response = "".join(response)
    for event in events:
        kind = event.get("type")
        if kind == "iteration_start":
            report.iterations += 1
        elif kind == "compaction":
            report.compaction_events.append(CompactionEvent(event.get("iteration", 0),
                event.get("tokens_before", 0), event.get("tokens_after", 0)))
        elif kind == "tool_error":
            report.tool_errors.append(ToolErrorEvent(event.get("iteration", 0),
                event.get("tool", "unknown"), event.get("message", "")[:500], event.get("recovered", False)))
        elif kind == "tool_result":
            name = event.get("tool", "unknown")
            report.tool_calls[name] = report.tool_calls.get(name, 0) + 1
        elif kind == "permission_denied":
            report.permission_denials += 1
        elif kind == "malformed_tool_call":
            report.malformed_tool_calls += 1
        elif kind == "mark_uncertain":
            report.mark_uncertain_count += 1
            severity = event.get("severity", "")
            if severity in report.mark_uncertain_by_severity:
                report.mark_uncertain_by_severity[severity] += 1
        elif kind == "max_iterations":
            report.max_iterations_hit = True
        elif kind == "usage":
            report.requests += 1
            report.estimated_requests += int(event.get("estimated", False))
            report.unpriced_requests += int(not event.get("cost_known", False))
            report.cost_usd += event.get("cost", 0)
        elif kind == "subagent_done":
            report.subagents += 1
            status = event.get("status", "unknown")
            report.subagent_statuses[status] = report.subagent_statuses.get(status, 0) + 1
    if report.unpriced_requests or not report.requests:
        report.cost_usd = None
    report.tokens_in, report.tokens_out = usage.get("tokens_in", 0), usage.get("tokens_out", 0)
    # Assertions see the complete response, not a truncated display preview.
    (workdir / ".codesm-eval-response.txt").write_text(report.final_response)
    report.final_response = report.final_response[-12000:]
    started = time.monotonic()
    for name, digest in protected.items():
        path = (workdir / name).resolve()
        intact = path.is_relative_to(workdir) and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
        report.assertions.append(AssertionResult(f"Preserve {name}", 0 if intact else 1))
    for command in task.assertion:
        code, stdout, stderr = await _run_shell(command, workdir)
        report.assertions.append(AssertionResult(command, code, stdout[:2000], stderr[:2000]))
    report.assertion_ms = int((time.monotonic() - started) * 1000)
    report.wall_clock_ms = int((time.monotonic() - wall) * 1000)
    return report
