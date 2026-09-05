"""Regression checks for shared execution, recovery, and cancellation."""

import asyncio
import shlex
from types import SimpleNamespace

import pytest

from codesm.agent.loop import ReActLoop
from codesm.provider.base import StreamChunk
from codesm.session.session import Session
from codesm.tool.registry import ToolRegistry


def registry_with(**callbacks):
    registry = ToolRegistry()
    registry._tools = {
        name: SimpleNamespace(name=name, description=name, execute=fn, get_parameters_schema=lambda: {})
        for name, fn in callbacks.items()
    }
    return registry


@pytest.mark.asyncio
async def test_read_batches_overlap_but_writes_are_barriers():
    active = 0
    peak = 0
    order = []

    async def read(args, context):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        order.append(args["n"])
        active -= 1
        return "read"

    async def write(args, context):
        assert active == 0
        order.append("write")
        return "written"

    registry = registry_with(read=read, write=write)
    results = await registry.execute_parallel([
        ("a", "read", {"n": 1}), ("b", "read", {"n": 2}),
        ("c", "write", {"path": "fixture.txt"}), ("d", "read", {"n": 3}),
    ], {})
    assert peak == 2
    assert order == [1, 2, "write", 3]
    assert [r[0] for r in results] == ["a", "b", "c", "d"]


@pytest.mark.asyncio
async def test_restricted_batch_cannot_recreate_unrestricted_tools(tmp_path):
    from codesm.tool.batch import BatchTool
    registry = ToolRegistry()
    context = {"tools": registry, "read_only": True, "cwd": tmp_path}
    result = await BatchTool().execute({"tool_calls": [{
        "tool": "bash", "parameters": {"command": "touch escaped"},
    }]}, context)
    assert "Permission denied" in result
    assert not (tmp_path / "escaped").exists()


@pytest.mark.asyncio
async def test_child_inherits_constraints_and_session_policy(monkeypatch, tmp_path):
    from codesm.agent.subagent import SubAgent, get_subagent_config
    captured = {}

    class Provider:
        async def stream(self, system, messages, tools):
            from codesm.agent.execution import current_context
            captured.update(system=system, context=current_context.get(), tools=tools)
            yield StreamChunk(type="text", content="Found it")

    monkeypatch.setattr("codesm.agent.subagent.get_provider", lambda model: Provider())
    parent = {"session_id": "parent", "rules": "Never edit protected.py",
              "task_constraints": "Preserve compatibility", "budget": object()}
    agent = SubAgent(get_subagent_config("librarian"), tmp_path, "fake", ToolRegistry(), parent)
    await agent.run("Find the implementation")
    assert "Never edit protected.py" in captured["system"]
    assert "Preserve compatibility" in captured["system"]
    assert captured["context"]["session_id"] == "parent"
    assert captured["context"]["budget"] is parent["budget"]
    assert {t["name"] for t in captured["tools"]}.isdisjoint({"bash", "write", "mcp_execute", "orchestrate"})


@pytest.mark.asyncio
async def test_tool_history_survives_reload_and_repairs_interruption(tmp_path):
    session = Session(id="history", directory=tmp_path)
    session.add_message("user", "Read a file")

    class Provider:
        calls = 0

        async def stream(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield StreamChunk(type="tool_call", id="read-1", name="read", args={})
            else:
                yield StreamChunk(type="text", content="Done")

    async def read(args, context):
        return "source"

    async for _ in ReActLoop().execute(Provider(), "", session.get_messages(), registry_with(read=read), {"session": session}):
        pass
    loaded = Session.load(session.id)
    assert [m["role"] for m in loaded.get_messages()] == ["user", "assistant", "tool", "assistant"]
    loaded.add_message("assistant", "", tool_calls=[{"id": "interrupted", "function": {"name": "write", "arguments": "{}"}}])
    loaded.add_message("user", "Continue")
    history = Session.load(session.id).get_messages()
    assert history[-2]["tool_call_id"] == "interrupted"
    assert "outcome" in history[-2]["content"]
    assert history[-1]["role"] == "user"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_shell_timeout_and_cancel_kill_descendants(tmp_path, cancel):
    from codesm.tool.bash import BashTool
    marker = tmp_path / "escaped"
    command = f"(sleep 0.3; printf late > {shlex.quote(str(marker))}) & wait"
    task = asyncio.create_task(BashTool().execute({"command": command, "timeout": 10 if cancel else 0.05}, {"cwd": tmp_path}))
    if cancel:
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert "timed out" in await task
    await asyncio.sleep(0.4)
    assert not marker.exists()

@pytest.mark.asyncio
async def test_compaction_summary_reaches_provider_system_prompt():
    captured = {}
    class Provider:
        async def stream(self, **kwargs):
            captured.update(kwargs)
            yield StreamChunk(type="text", content="done")
    messages = [{"role":"system","content":"Keep the failing command","_context_summary":True},
                {"role":"user","content":"Continue"}]
    async for _ in ReActLoop().execute(Provider(), "Rules", messages, registry_with(), {}):
        pass
    assert "Keep the failing command" in captured["system"]
    assert [m["role"] for m in captured["messages"]] == ["user"]


@pytest.mark.asyncio
async def test_child_write_uses_workspace_and_parent_preview_session(tmp_path, monkeypatch):
    from codesm.diff_preview import DiffPreviewResponse
    calls = []
    async def preview(session_id, *args, **kwargs):
        calls.append(session_id)
        return DiffPreviewResponse.APPLY
    monkeypatch.setattr("codesm.diff_preview.request_diff_preview", preview)
    (tmp_path / "child-file.txt").write_text("before")
    result = await ToolRegistry().execute("write", {"path":"child-file.txt","content":"written"},
        {"cwd":tmp_path,"session_id":"parent"})
    assert (tmp_path / "child-file.txt").read_text() == "written", result
    assert calls == ["parent"]

@pytest.mark.asyncio
async def test_handoff_receives_history_and_propagates_session_switch():
    seen = {}
    async def handoff(args, context):
        seen["messages"] = context["messages"]
        context.update(_handoff_follow=True, _handoff_session_id="next")
        return "Handoff prepared"
    class Provider:
        async def stream(self, **kwargs):
            yield StreamChunk(type="tool_call", id="handoff-1", name="handoff", args={})
    context = {}
    chunks = [chunk async for chunk in ReActLoop().execute(Provider(), "", [{"role":"user","content":"Keep the failure evidence"}], registry_with(handoff=handoff), context)]
    assert seen["messages"][0]["content"] == "Keep the failure evidence"
    assert chunks[-1].type == "handoff" and chunks[-1].new_session_id == "next"
    assert context["completion_status"] == "handed_off"
