"""End-to-end lifecycle checks with deterministic provider streams."""
import asyncio
from contextlib import aclosing
from pathlib import Path

import pytest

from codesm.agent.agent import Agent
from codesm.config.config import Config, AgentConfig
from codesm.provider.base import Provider, StreamChunk
from codesm.session.session import Session


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_live_child_events_cleanup_and_resume_usage(monkeypatch, tmp_path, cancel):
    monkeypatch.setattr("codesm.agent.agent.load_mcp_config", lambda path=None: {})
    release = asyncio.Event()
    providers = []

    class Fake(Provider):
        provider_id = "openai"
        model = "fixture"

        def __init__(self):
            self.calls = 0
            self.closed = False
            providers.append(self)

        async def _stream(self, system, messages, tools=None):
            from codesm.agent.execution import current_context
            self.calls += 1
            if current_context.get().get("subagent"):
                await release.wait()
                yield StreamChunk(type="text", content="Evidence: target.py:1")
            elif self.calls == 1:
                yield StreamChunk(type="tool_call", id="delegate", name="task", args={
                    "subagent_type": "planner", "prompt": "Find a safe plan", "description": "Plan"})
            else:
                yield StreamChunk(type="text", content="Done")
            yield StreamChunk(type="usage", metadata={"input_tokens": 12, "output_tokens": 5})

        async def close(self):
            self.closed = True

    monkeypatch.setattr("codesm.agent.agent.get_provider", lambda *args: Fake())
    monkeypatch.setattr("codesm.agent.subagent.get_provider", lambda *args: Fake())
    config = Config(model="openai/fixture", agents={"main": AgentConfig(max_output_tokens=32)})
    agent = Agent(tmp_path, config=config)
    seen = []
    async with aclosing(agent.chat("Plan the fix")) as stream:
        async for chunk in stream:
            seen.append(chunk.type)
            if chunk.type == "subagent_start":
                # A start event arrives while the child is still waiting on its provider.
                assert not release.is_set()
                assert not agent.session.agent_runs[chunk.subagent_id]["result"]
                if cancel:
                    break
                release.set()
    assert all(p.closed for p in providers[1:])
    record = next(iter(agent.session.agent_runs.values()))
    assert record["status"] == ("cancelled" if cancel else "completed")
    if not cancel:
        assert record["result"] == "Evidence: target.py:1"
        assert "subagent_done" in seen
        assert agent.budget.get_session_stats().total_requests == 3
    saved = Session.load(agent.session.id)
    resumed = Agent(tmp_path, config=config, session=saved)
    assert resumed.budget.get_session_stats().total_requests == agent.budget.get_session_stats().total_requests
    assert resumed.budget.get_session_stats().total_cost == agent.budget.get_session_stats().total_cost
    agent.new_session()
    assert agent.budget.get_session_stats().total_requests == 0
    await agent.cleanup()
    await resumed.cleanup()


@pytest.mark.asyncio
async def test_single_mode_blocks_hidden_batch_delegation(tmp_path):
    from codesm.agent.execution import current_context
    from codesm.tool.registry import ToolRegistry, DELEGATION_TOOLS
    registry = ToolRegistry()
    context = {"config": Config(delegation="single"), "tools": registry, "cwd": tmp_path}
    token = current_context.set(context)
    try:
        assert {s["name"] for s in registry.get_schemas()}.isdisjoint(DELEGATION_TOOLS)
        result = await registry.execute("batch", {"tool_calls": [
            {"tool": "task", "parameters": {"prompt": "escaped"}},
        ]}, context)
        assert "single-agent mode" in result
    finally:
        current_context.reset(token)


@pytest.mark.asyncio
async def test_parallel_coders_share_one_writer_and_fail_fast_cancels(monkeypatch, tmp_path):
    from codesm.tool.registry import ToolRegistry
    from codesm.agent.subagent import SubAgent, get_subagent_config
    active = peak = 0

    class Fake:
        async def stream(self, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(.01)
                yield StreamChunk(type="text", content="done")
            finally:
                active -= 1

    monkeypatch.setattr("codesm.agent.subagent.get_provider", lambda model: Fake())
    registry = ToolRegistry()
    workers = [SubAgent(get_subagent_config("coder"), tmp_path, "fake", registry) for _ in range(2)]
    await asyncio.gather(*(worker.run("fix") for worker in workers))
    assert peak == 1 and active == 0

    stopped = asyncio.Event()
    async def run(self, prompt):
        if prompt == "fail":
            await asyncio.sleep(.01)
            raise ValueError("fixture failure")
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(SubAgent, "run", run)
    result = await asyncio.wait_for(registry.execute("parallel_tasks", {
        "tasks": [{"prompt": p, "subagent_type": "planner", "description": p} for p in ("wait", "fail")],
        "fail_fast": True,
    }, {"cwd": tmp_path, "tools": registry}), 1)
    assert result.startswith("Error:") and stopped.is_set()


def test_failed_widget_does_not_render_success():
    from codesm.tui.tools import SubAgentTreeWidget
    widget = SubAgentTreeWidget("Plan", subagent_type="planner", model="openai/fixture")
    widget.complete("Connection lost", status="failed")
    rendered = widget.render().plain
    assert "failed" in rendered and "Connection lost" in rendered and "cost unknown" in rendered
    assert "✓" not in rendered
    widget.toggle_collapse()
    assert "openai/fixture" in widget.render().plain and "failed" in widget.render().plain

@pytest.mark.asyncio
async def test_terminal_renders_live_agent_events_and_usage(monkeypatch, tmp_path):
    from codesm.tui.app import CodesmApp
    from codesm.tui.tools import SubAgentTreeWidget
    from textual.widgets import Static, Input
    monkeypatch.setattr("codesm.agent.agent.load_mcp_config", lambda path=None: {})
    monkeypatch.setattr(CodesmApp, "_init_lsp", lambda self: None)
    monkeypatch.setattr(CodesmApp, "_init_file_watcher", lambda self: None)
    class Fake(Provider):
        model = "fixture"
        provider_id = "openai"
        def __init__(self):
            self.calls = 0
        async def _stream(self, system, messages, tools=None):
            from codesm.agent.execution import current_context
            self.calls += 1
            if not current_context.get().get("subagent") and self.calls == 1:
                yield StreamChunk(type="tool_call", id="plan", name="task", args={
                    "subagent_type":"planner", "prompt":"Plan", "description":"Plan"})
            else:
                yield StreamChunk(type="text", content="Inspected the task")
            yield StreamChunk(type="usage", metadata={"input_tokens":10,"output_tokens":5})
    monkeypatch.setattr("codesm.agent.agent.get_provider", lambda *args: Fake())
    monkeypatch.setattr("codesm.agent.subagent.get_provider", lambda *args: Fake())
    app = CodesmApp(tmp_path, "openai/fixture")
    async with app.run_test(size=(120,40)) as pilot:
        app._switch_to_chat()
        await app._process_chat("Plan the change")
        await pilot.pause()
        worker = app.query_one(SubAgentTreeWidget)
        assert worker.status == "completed" and "Inspected the task" in worker.render().plain
        assert app.agent.budget.get_session_stats().total_requests == 3
        assert not app.query_one("#chat-message-input", Input).disabled
        assert "context left" in str(app.query_one("#footer-context", Static).render()).lower()

@pytest.mark.asyncio
async def test_failed_pipeline_dependencies_are_skipped(tmp_path, monkeypatch):
    from codesm.agent.orchestrator import SubAgentOrchestrator, OrchestrationPlan, SubAgentStatus
    from codesm.tool.registry import ToolRegistry
    orchestrator = SubAgentOrchestrator(tmp_path, ToolRegistry(), "openai/fixture")
    first = orchestrator.create_task("coder", "Fail")
    second = orchestrator.create_task("coder", "Must not run")
    visited = []
    async def fail(task):
        visited.append(task.id)
        task.status = SubAgentStatus.FAILED
        raise RuntimeError("failed prerequisite")
    monkeypatch.setattr(orchestrator, "spawn", fail)
    await orchestrator.execute_plan(OrchestrationPlan.sequential([first,second]))
    assert visited == [first.id]
    assert second.status == SubAgentStatus.CANCELLED and "dependency" in second.error
