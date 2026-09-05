"""Resume saved work after a provider limit without another repository scan."""

from contextlib import aclosing
from copy import deepcopy
import json

import pytest

from codesm.agent.agent import Agent
from codesm.config.config import AgentConfig, Config
from codesm.memory.continuation import continuation_context
from codesm.memory.history import HistoryStore
from codesm.provider.base import Provider, StreamChunk
from codesm.session.session import Session
from codesm.session.todo import TodoList
from codesm.storage.storage import Storage
from codesm.tool.recall import RecallTool


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_limit_then_provider_switch_keeps_evidence_partial_text_and_tasks(tmp_path, monkeypatch, restart):
    (tmp_path / "app.py").write_text("AUTH_TOKEN_RENEWAL = 'already investigated'\n")
    monkeypatch.setattr("codesm.agent.agent.load_mcp_config", lambda *_: {})
    requests = []

    class Fake(Provider):
        def __init__(self, model):
            self.provider_id, self.model = model.split("/")
            self.calls = 0

        async def _stream(self, system, messages, tools=None):
            self.calls += 1
            requests.append((self.provider_id, system, deepcopy(messages)))
            if self.provider_id == "openai":
                if self.calls == 1:
                    yield StreamChunk(type="tool_call", id="inspect", name="read", args={"path": "app.py"})
                else:
                    yield StreamChunk(type="text", content="Found the refresh path. Next fix the expiry check.")
                    raise RuntimeError("Fixture provider quota exhausted")
            else:
                assert "AUTH_TOKEN_RENEWAL" in json.dumps(messages)
                assert any(m.get("_interrupted") and "Next fix" in m.get("content", "") for m in messages)
                assert "Add regression for expired token" in system
                assert "unchanged by size/mtime" in system
                assert "Fixture provider quota exhausted" in system
                assert any(tool["name"] == "recall" for tool in tools)
                yield StreamChunk(type="text", content="Continuing from the saved evidence.")

    monkeypatch.setattr("codesm.agent.agent.get_provider", lambda model, *args: Fake(model))
    config = Config(model="openai/fixture", agents={"main": AgentConfig(max_output_tokens=32)})
    agent = Agent(tmp_path, config=config)
    TodoList(agent.session.id).add("Add regression for expired token")
    with pytest.raises(RuntimeError, match="quota exhausted"):
        async for _ in agent.chat("Investigate AUTH_TOKEN_RENEWAL"):
            pass
    session_id = agent.session.id
    assert not agent._chat_active
    assert Session.load(session_id).pending_response == {}
    if restart:
        await agent.cleanup()
        agent = Agent(tmp_path / "different-cwd", model="anthropic/fixture", config=config,
                      session=Session.load(session_id))
        assert agent.directory == tmp_path
    else:
        agent.model = "anthropic/fixture"
    async for _ in agent.chat("Continue"):
        pass
    assert agent.session.id == session_id
    assert [provider for provider, _, _ in requests] == ["openai", "openai", "anthropic"]
    assert agent.session.run_state["status"] == "completed"
    assert agent.session.last_model == "anthropic/fixture"
    hits = HistoryStore().search(tmp_path, "AUTH_TOKEN_RENEWAL", session_id)
    assert hits
    tool = RecallTool()
    result = json.loads(await tool.execute({"record_id": hits[0]["id"]}, {"cwd": tmp_path}))
    assert "AUTH_TOKEN_RENEWAL" in result["text"]
    assert "No record" in await tool.execute({"record_id": hits[0]["id"]}, {"cwd": tmp_path / "other"})
    await agent.cleanup()


@pytest.mark.asyncio
async def test_compaction_is_checkpointed_before_provider_failure(tmp_path, monkeypatch):
    from codesm.agent.loop import ReActLoop
    from codesm.session.context import ContextManager
    from codesm.tool.registry import ToolRegistry
    session = Session.create(tmp_path)
    session.add_message("user", "Preserve the full OriginalEvidenceNeedle")
    session.add_message("assistant", "Older analysis")
    session.add_message("user", "Continue")
    original = deepcopy(session.messages)
    checkpoint = [{"role": "system", "content": "Goal and completed work preserved", "_context_summary": True},
                  {"role": "user", "content": "Continue"}]

    async def compact(self, messages, summarizer=None):
        return checkpoint

    monkeypatch.setattr(ContextManager, "should_compact", lambda *_: True)
    monkeypatch.setattr(ContextManager, "compact_messages_async", compact)

    class Limited:
        async def stream(self, **kwargs):
            assert "Goal and completed work preserved" in kwargs["system"]
            raise RuntimeError("Fixture rate limit after compaction")
            yield

    with pytest.raises(RuntimeError, match="rate limit"):
        async for _ in ReActLoop().execute(Limited(), "rules", session.get_messages(), ToolRegistry(),
                                            {"session": session, "model": "openai/fixture"}):
            pass
    loaded = Session.load(session.id)
    assert loaded.messages == original
    assert loaded.get_messages() == checkpoint
    assert HistoryStore().search(tmp_path, "OriginalEvidenceNeedle")


def test_continuation_checks_only_known_files_and_keeps_notes_bounded(tmp_path):
    from codesm.memory.models import MemoryItem
    from codesm.memory.store import MemoryStore
    from codesm.util.project_id import get_project_id
    file = tmp_path / "known.py"
    file.write_text("before")
    stat = file.stat()
    session = Session.create(tmp_path)
    session.file_state[str(file)] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
    MemoryStore().upsert(MemoryItem(id="known", type="fact", text="The project uses local fixtures.",
                                  project_id=get_project_id(tmp_path)))
    assert "unchanged by size/mtime" in continuation_context(session, "continue")
    file.write_text("external change")
    context = continuation_context(session, "continue")
    assert "CHANGED; verify" in context and "local fixtures" in context
    assert len(continuation_context(session, "continue", max_chars=200)) <= 200


@pytest.mark.asyncio
async def test_recall_rejects_invalid_inputs(tmp_path):
    tool = RecallTool()
    for args in ({"record_id": True}, {"record_id": 1, "offset": -1},
                 {"query": "x", "limit": True}, {"query": "x", "session_id": []}):
        assert (await tool.execute(args, {"cwd": tmp_path})).startswith("Error:")


@pytest.mark.asyncio
async def test_text_before_malformed_tool_call_survives_immediate_switch_and_restart(tmp_path):
    from codesm.agent.loop import ReActLoop
    from codesm.tool.registry import ToolRegistry

    class Malformed:
        async def stream(self, **kwargs):
            yield StreamChunk(type="text", content="Important partial finding before invalid tool call")
            yield StreamChunk(type="tool_call", id="", name="read", args={"path": "app.py"})

    session = Session.create(tmp_path)
    with pytest.raises(ValueError, match="missing or duplicate"):
        async for _ in ReActLoop().execute(Malformed(), "rules", [], ToolRegistry(), {"session": session}):
            pass
    assert session.pending_response == {}
    for saved in (session, Session.load(session.id)):
        assert saved.get_messages()[-1]["content"] == "Important partial finding before invalid tool call"
        assert saved.get_messages()[-1]["_interrupted"]
