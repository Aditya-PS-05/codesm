"""Backend handoffs, saved work, permissions, and the actual pipe transport."""

import asyncio
from contextlib import aclosing
import json
import os
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

from codesm.agent.agent import Agent
from codesm.agent.backends import checkout_lock, handoff_prompt
from codesm.config import Config
from codesm.provider.base import StreamChunk
from codesm.session.session import Session


@pytest.fixture
def installed_backends(monkeypatch):
    monkeypatch.setattr("codesm.agent.backends.availability", lambda backend: None)
    monkeypatch.setattr("codesm.agent.agent.get_provider", lambda *args: SimpleNamespace())


async def collect(agent, message):
    return [chunk async for chunk in agent.chat(message)]


@pytest.mark.parametrize("destination", ["codex", "claude-science"])
async def test_switch_resume_fork_and_native_history(tmp_path, monkeypatch, installed_backends, destination):
    calls = []

    def runner(backend):
        async def stream(**kwargs):
            calls.append((backend, kwargs["prompt"], dict(kwargs["state"])))
            kwargs["state"]["id"] = f"{backend}-session"
            kwargs["save"]()
            yield StreamChunk(type="text", content=f"{backend} inspected the task.")
            yield StreamChunk(type="tool_call", id=f"{backend}-{len(calls)}", name="bash", args={"command": "pytest"})
            yield StreamChunk(type="tool_result", id=f"{backend}-{len(calls)}", name="bash", content=f"{backend}: test evidence")
            yield StreamChunk(type="text", content=f"{backend}: next step")
            yield StreamChunk(type="run_status", content="completed")
        return stream

    monkeypatch.setattr("codesm.agent.claude_code.stream", runner("claude"))
    monkeypatch.setattr("codesm.agent.codex_backend.stream", runner("codex"))
    monkeypatch.setattr("codesm.agent.claude_science.stream", runner("claude-science"))
    agent = Agent(tmp_path, backend="claude-code", config=Config(model="openai/fixture"))
    await collect(agent, "Fix the unique regression without changing the API")
    agent.backend = destination
    await collect(agent, "Continue in Codex")
    assert "unique regression" in calls[-1][1] and "claude: test evidence" in calls[-1][1]
    saved = Session.load(agent.session.id)
    assert saved.backend == destination
    assert saved.backend_sessions["claude-code"]["id"] == "claude-session"
    resumed = Agent(tmp_path, session=saved, config=Config(model="openai/fixture"))
    resumed.backend = "claude-code"
    await collect(resumed, "Continue back in Claude")
    assert calls[-1][2]["id"] == "claude-session"
    assert f"{destination}: test evidence" in calls[-1][1]
    assert '"content": "claude: test evidence"' not in calls[-1][1]
    await collect(resumed, "One more follow-up")
    assert calls[-1][1] == "One more follow-up"
    resumed.backend = "native"
    assert any(m.get("role") == "tool" and "test evidence" in m.get("content", "") for m in resumed.session.get_messages())
    fork = resumed.session.fork()
    assert not fork.backend_sessions and fork.messages == resumed.session.messages
    resumed.session.clear()
    assert not Session.load(resumed.session.id).backend_sessions


async def test_interrupt_keeps_partial_text_and_releases_checkout(tmp_path, monkeypatch, installed_backends):
    entered = asyncio.Event()

    async def stream(**kwargs):
        kwargs["state"]["id"] = "interrupted-native-session"
        kwargs["save"]()
        yield StreamChunk(type="text", content="Partial diagnosis")
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("codesm.agent.codex_backend.stream", stream)
    agent = Agent(tmp_path, backend="codex", config=Config())
    task = asyncio.create_task(collect(agent, "Diagnose the bug"))
    await entered.wait()
    with pytest.raises(RuntimeError, match="active task"):
        agent.backend = "claude-code"
    with pytest.raises(RuntimeError, match="Another codesm"):
        with checkout_lock(tmp_path):
            pass
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = Session.load(agent.session.id)
    assert saved.messages[-1]["content"] == "Partial diagnosis"
    assert saved.run_state["status"] == "interrupted"
    assert saved.backend_sessions["codex"]["interrupted"]
    with checkout_lock(tmp_path):
        pass


def test_handoff_excludes_opaque_data_and_preserves_current_request(tmp_path):
    session = Session.create(tmp_path)
    session.add_message("assistant", "Remember this result", response_items=[{"secret": "opaque-reasoning"}])
    request = "Do the next requested step " * 1000
    session.add_message("user", request)
    prompt = handoff_prompt(session, request, {}, max_chars=100)
    assert "opaque-reasoning" not in prompt
    assert prompt.endswith(request)


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    """An executable protocol peer: real subprocess IO, no model or network."""
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    binary = binary_dir / "codex"
    binary.write_text(f"#!{sys.executable}\n" + r'''
import json, os, sys
from pathlib import Path
log = Path(os.environ["CODESM_FAKE_LOG"])
def emit(event):
    print(json.dumps(event), flush=True)
def reply(event, result):
    emit({"id": event["id"], "result": result})
def notification(method, **params):
    emit({"method": method, "params": params})
for line in sys.stdin:
    event = json.loads(line)
    with log.open("a") as f:
        f.write(json.dumps(event) + "\n")
    method = event.get("method")
    if method == "initialize":
        reply(event, {"userAgent": "fixture"})
    elif method in ("thread/start", "thread/resume"):
        reply(event, {"thread": {"id": "thread-fixture"}})
    elif method == "turn/start":
        reply(event, {"turn": {"id": "turn-fixture"}})
        notification("item/agentMessage/delta", threadId="thread-fixture", turnId="turn-fixture", itemId="message", delta="Reading ")
        notification("item/agentMessage/delta", itemId="message", delta="the code.")
        notification("item/completed", item={"type":"agentMessage","id":"message","text":"Reading the code."})
        notification("item/started", item={"type":"commandExecution","id":"cmd","command":"pytest","cwd":str(Path.cwd())})
        emit({"id": "permission", "method":"item/commandExecution/requestApproval", "params":{"itemId":"cmd","command":"pytest"}})
    elif event.get("id") == "permission":
        allowed = event["result"]["decision"] == "accept"
        if allowed:
            Path("approval-marker").write_text("approved")
        notification("item/completed", item={"type":"commandExecution","id":"cmd","status":"completed" if allowed else "declined", "aggregatedOutput":"tests passed" if allowed else "permission denied", "exitCode":0 if allowed else 1})
        notification("thread/tokenUsage/updated", tokenUsage={"total":{"inputTokens":30,"outputTokens":8}})
        notification("turn/completed", turn={"id":"turn-fixture","status":"completed"})
    elif method == "turn/interrupt":
        reply(event, {})
''')
    binary.chmod(0o755)
    log = tmp_path / "protocol.jsonl"
    monkeypatch.setenv("PATH", str(binary_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("CODESM_FAKE_LOG", str(log))
    return log


@pytest.mark.parametrize("allowed", [True, False])
async def test_codex_pipe_protocol_approval_and_native_resume(tmp_path, fake_codex, monkeypatch, allowed):
    approvals = []

    async def approve(*args):
        approvals.append(args)
        return allowed

    monkeypatch.setattr("codesm.agent.codex_backend.approve", approve)
    agent = Agent(tmp_path, backend="codex", config=Config())
    chunks = await collect(agent, "Run the checks")
    assert "".join(c.content for c in chunks if c.type == "text") == "Reading the code."
    assert bool(approvals) and approvals[0][3]["item"]["command"] == "pytest"
    assert (tmp_path / "approval-marker").exists() == allowed
    assert agent.session.backend_sessions["codex"]["id"] == "thread-fixture"
    assert agent.session.usage_records[0]["input_tokens"] == 30
    assert not agent.session.usage_records[0]["cost_known"]
    await collect(agent, "Continue")
    requests = [json.loads(line) for line in fake_codex.read_text().splitlines()]
    assert len([r for r in requests if r.get("method") == "thread/start"]) == 1
    assert len([r for r in requests if r.get("method") == "thread/resume"]) == 1
    starts = [r for r in requests if r.get("method") == "thread/start"]
    assert starts[0]["params"]["sandbox"] == "workspace-write"
    assert starts[0]["params"]["approvalPolicy"] == "untrusted"
    turns = [r for r in requests if r.get("method") == "turn/start"]
    assert turns[-1]["params"]["input"][0]["text"] == "Continue"


async def test_codex_read_only_denies_escalation(tmp_path, fake_codex, monkeypatch):
    async def unexpected(*args):
        pytest.fail("Read-only execution must not allow permission escalation")
    monkeypatch.setattr("codesm.agent.codex_backend.approve", unexpected)
    agent = Agent(tmp_path, backend="codex", config=Config(read_only=True))
    await collect(agent, "Inspect only")
    assert not (tmp_path / "approval-marker").exists()


async def test_claude_sdk_stream_deduplication_tools_and_permissions(tmp_path, monkeypatch):
    sdk = pytest.importorskip("claude_agent_sdk")
    options = []

    class Client:
        def __init__(self, options):
            self.options = options
        async def __aenter__(self):
            options.append(self.options)
            return self
        async def __aexit__(self, *args):
            pass
        async def query(self, prompt):
            self.prompt = prompt
        async def receive_response(self):
            assert self.options.permission_mode == "default"
            denied = await self.options.can_use_tool("Bash", {"command": "pytest"}, None)
            assert denied.behavior == "deny"
            yield sdk.SystemMessage("init", {"session_id": "claude-native"})
            yield sdk.SystemMessage("init", {"session_id": "child", "parent_tool_use_id": "delegation"})
            yield sdk.StreamEvent("child-event", "child", {"type":"content_block_delta", "delta":{"type":"text_delta","text":"Child analysis"}}, parent_tool_use_id="delegation")
            yield sdk.AssistantMessage([sdk.TextBlock("Child analysis")], "fixture", parent_tool_use_id="delegation", session_id="child")
            assert agent.session.backend_sessions["claude-code"]["id"] == "claude-native"
            yield sdk.StreamEvent("event1", "claude-native", {"type":"message_start", "message":{"id":"m"}})
            yield sdk.StreamEvent("event2", "claude-native", {"type":"content_block_delta", "delta":{"type":"text_delta","text":"Diagnosis"}})
            yield sdk.AssistantMessage([sdk.TextBlock("Diagnosis"), sdk.ToolUseBlock("call1", "Read", {"file_path":"app.py"})], "fixture", message_id="m")
            yield sdk.UserMessage([sdk.ToolResultBlock("call1", "def f(): pass")])
            yield sdk.ResultMessage("success", 100, 90, False, 1, "claude-native", result="Diagnosis", usage={"input_tokens":12,"output_tokens":3})

    async def deny(*args):
        return False
    monkeypatch.setattr(sdk, "ClaudeSDKClient", Client)
    monkeypatch.setattr("codesm.agent.claude_code.approve", deny)
    monkeypatch.setattr("codesm.agent.backends.availability", lambda backend: None)
    agent = Agent(tmp_path, backend="claude-code", config=Config())
    chunks = await collect(agent, "Find the bug")
    assert "".join(c.content for c in chunks if c.type == "text") == "Diagnosis"
    assert any(m.get("role") == "tool" and m["content"] == "def f(): pass" for m in agent.session.get_messages())
    assert options[0].setting_sources == ["user", "project", "local"]
    assert options[0].system_prompt["preset"] == "claude_code"
    await collect(agent, "Follow up")
    assert options[-1].resume == "claude-native"


async def test_claude_real_sdk_pipe_permission_and_cancellation(tmp_path, monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    binary = tmp_path / "claude"
    binary.write_text(f"#!{sys.executable}\n" + r'''
import json, os, sys
from pathlib import Path
if "--version" in sys.argv or "-v" in sys.argv:
    print("99.0.0 (test peer)")
    sys.exit(0)
Path("claude-pid").write_text(str(os.getpid()))
Path("claude-args").write_text(json.dumps(sys.argv))
def emit(event):
    print(json.dumps(event), flush=True)
for line in sys.stdin:
    event = json.loads(line)
    if event["type"] == "control_request":
        emit({"type":"control_response", "response":{"subtype":"success", "request_id":event["request_id"], "response":{}}})
    elif event["type"] == "user":
        emit({"type":"system", "subtype":"init", "session_id":"claude-pipe"})
        emit({"type":"assistant", "message":{"model":"fixture", "content":[{"type":"text", "text":"Partial diagnosis"}]}})
        emit({"type":"control_request", "request_id":"gate", "request":{"subtype":"can_use_tool", "tool_name":"Bash", "input":{"command":"pytest"}}})
    elif event["type"] == "control_response":
        Path("claude-permission").write_text(event["response"]["response"]["behavior"])
        emit({"type":"result", "subtype":"success", "duration_ms":1, "duration_api_ms":1, "is_error":False, "num_turns":1, "session_id":"claude-pipe"})
''')
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))

    async def deny(*args):
        return False
    monkeypatch.setattr("codesm.agent.claude_code.approve", deny)
    agent = Agent(tmp_path, backend="claude-code", config=Config())
    await asyncio.wait_for(collect(agent, "Inspect the bug"), 10)
    assert (tmp_path / "claude-permission").read_text() == "deny"
    assert agent.session.run_state["status"] == "completed"

    waiting = asyncio.Event()
    async def wait_for_permission(*args):
        waiting.set()
        await asyncio.Event().wait()
    monkeypatch.setattr("codesm.agent.claude_code.approve", wait_for_permission)
    task = asyncio.create_task(collect(agent, "Continue"))
    try:
        await asyncio.wait_for(waiting.wait(), 10)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
    saved = Session.load(agent.session.id)
    assert saved.run_state["status"] == "interrupted"
    assert saved.messages[-1]["content"] == "Partial diagnosis"
    assert saved.backend_sessions["claude-code"]["id"] == "claude-pipe"
    args = json.loads((tmp_path / "claude-args").read_text())
    assert "--resume=claude-pipe" in args
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "claude-pid").read_text()), 0)


async def test_external_backend_refuses_unenforceable_native_budget(tmp_path, installed_backends):
    agent = Agent(tmp_path, backend="codex", config=Config(budget_usd=1))
    with pytest.raises(ValueError, match="cannot yet be enforced"):
        await collect(agent, "Do work")


async def test_backend_popovers_and_selection(tmp_path, monkeypatch):
    from textual.app import App
    from textual.widgets import Input
    from codesm.tui.backend_modal import BackendInputModal, BackendSelectModal
    monkeypatch.setattr("codesm.tui.backend_modal.availability", lambda backend: None)
    app = App()
    selected = []
    async with app.run_test(size=(80, 24)) as pilot:
        modal = BackendSelectModal("claude-code")
        await app.push_screen(modal, selected.append)
        await pilot.pause()
        assert modal.children[0].region.x == 2
        assert modal.children[0].region.bottom == 20
        await pilot.press("down", "enter")
        assert selected == ["codex"]
        prompt = BackendInputModal("Agent question", "Which test should I run?")
        await app.push_screen(prompt, selected.append)
        await pilot.pause()
        assert prompt.children[0].region.bottom == 20
        prompt.query_one(Input).value = "pytest"
        await pilot.press("enter")
        assert selected[-1] == "pytest"


def test_interleaved_external_tools_and_answers_replay_without_losing_results(tmp_path):
    session = Session.create(tmp_path)
    def call(identifier):
        return {"id": identifier, "type": "function", "function": {"name": "read", "arguments": "{}"}}
    session.add_message("assistant", "", backend="codex", tool_calls=[call("a")])
    session.add_message("backend_input", "Which module?\nauth", backend="codex")
    session.add_message("assistant", "Also inspecting tests", backend="codex")
    session.add_message("assistant", "", backend="codex", tool_calls=[call("b")])
    session.add_message("tool", "result-b", tool_call_id="b", backend="codex")
    session.add_message("tool", "result-a", tool_call_id="a", backend="codex")
    session.add_message("assistant", "Done", backend="codex")
    archived = deepcopy(session.messages)
    messages = session.get_messages()
    assert session.messages == archived
    assert [m["content"] for m in messages if m["role"] == "tool"] == ["result-a", "result-b"]
    assert [call["id"] for call in messages[0]["tool_calls"]] == ["a", "b"]
    assert messages[3] == {"role": "user", "content": "Which module?\nauth"}


async def test_codex_disconnect_and_cancel_leave_resumable_work(tmp_path, fake_codex, monkeypatch):
    from pathlib import Path
    binary = Path(os.environ["PATH"].split(os.pathsep)[0]) / "codex"
    script = binary.read_text()
    # Emit partial output and exit before the next tool completes.
    binary.write_text(script.replace('notification("item/started", item=', 'sys.exit(2)\n        notification("item/started", item='))
    agent = Agent(tmp_path, backend="codex", config=Config())
    with pytest.raises(RuntimeError, match="disconnected"):
        await collect(agent, "Work on the task")
    saved = Session.load(agent.session.id)
    assert saved.run_state["status"] == "failed"
    assert saved.messages[-1]["content"] == "Reading the code."
    assert saved.backend_sessions["codex"]["id"] == "thread-fixture"
    with checkout_lock(tmp_path):
        pass

    binary.write_text(script)
    waiting = asyncio.Event()
    async def wait_for_permission(*args):
        waiting.set()
        await asyncio.Event().wait()
    monkeypatch.setattr("codesm.agent.codex_backend.approve", wait_for_permission)
    task = asyncio.create_task(collect(agent, "Resume the task"))
    await asyncio.wait_for(waiting.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    requests = [json.loads(line) for line in fake_codex.read_text().splitlines()]
    assert any(r.get("method") == "turn/interrupt" for r in requests)
    assert Session.load(agent.session.id).run_state["status"] == "interrupted"


async def test_tui_backend_turn_routes_permission_and_question(tmp_path, monkeypatch, installed_backends):
    from codesm.tui.app import CodesmApp
    from codesm.tui.backend_modal import BackendInputModal
    from codesm.tui.modals import PermissionModal
    from codesm.agent.backends import approve
    from textual.widgets import Input
    monkeypatch.setattr(CodesmApp, "_init_lsp", lambda self: None)
    monkeypatch.setattr(CodesmApp, "_init_file_watcher", lambda self: None)

    async def stream(**kwargs):
        kwargs["state"]["id"] = "ui-native-session"
        kwargs["save"]()
        yield StreamChunk(type="tool_call", id="ui-tool", name="bash", args={"command":"pytest"})
        allowed = await approve(kwargs["session_id"], "codex", "bash", {"command":"pytest"})
        assert not allowed
        answer = await kwargs["ask_user"]({"question":"Which module?", "header":"Module"})
        assert answer == "auth"
        yield StreamChunk(type="tool_result", id="ui-tool", name="bash", content="Permission denied")
        yield StreamChunk(type="text", content="I will inspect auth.")
        yield StreamChunk(type="run_status", content="completed")

    monkeypatch.setattr("codesm.agent.codex_backend.stream", stream)
    app = CodesmApp(tmp_path, None, backend="codex")
    async with app.run_test(size=(90, 28)) as pilot:
        field = app.query_one("#chat-message-input", Input)
        field.value = "Inspect the task"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, PermissionModal)
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, BackendInputModal)
        app.screen.query_one(Input).value = "auth"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.agent.session.run_state["status"] == "completed"
        assert any(m.get("role") == "user" and "auth" in m.get("content", "") for m in app.agent.session.get_messages())
        assert app.agent.session.backend_sessions["codex"]["id"] == "ui-native-session"
        saved_id = app.agent.session.id
        app._on_backend_selected("claude-code")
        assert app.agent.backend == "claude-code" and app.agent.session.id == saved_id
        app._show_models()
        await pilot.pause()
        app.screen.query_one(Input).value = "sonnet"
        await pilot.press("enter")
        assert app.agent.model == "sonnet"


def test_cli_backend_selection_restores_saved_backend(tmp_path, fake_codex):
    from typer.testing import CliRunner
    from codesm.cli import app
    runner = CliRunner()
    first = runner.invoke(app, ["chat", "Inspect", "--dir", str(tmp_path), "--backend", "codex"])
    assert first.exit_code == 0, first.output
    sessions = Session.list_sessions()
    session_id = sessions[0]["id"]
    second = runner.invoke(app, ["chat", "Continue", "--session", session_id])
    assert second.exit_code == 0, second.output
    assert "backend: codex" in second.output
    assert len(Session.list_sessions()) == 1
    missing = runner.invoke(app, ["run", "--session", "missing", "--backend", "codex"])
    assert missing.exit_code != 0 and "Session not found" in missing.output
