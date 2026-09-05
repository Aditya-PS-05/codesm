"""Session checkpoints, interrupted streams, and continuation forks survive restart."""

from copy import deepcopy
import asyncio
import json

import pytest

from codesm.memory.history import HistoryStore
from codesm.session.session import Session
from codesm.session.todo import TodoList
from codesm.storage.storage import Storage


def tool_turn(*ids):
    return {"role": "assistant", "content": "Check app.py and update the fix.", "tool_calls": [
        {"id": call_id, "type": "function", "function": {
            "name": "read", "arguments": json.dumps({"path": "app.py"})}} for call_id in ids]}


def test_checkpoint_keeps_raw_archive_and_appends_repaired_tool_groups_after_restart(tmp_path):
    session = Session.create(tmp_path)
    session.add_message(role="user", content="OriginalArchiveNeedle " * 5000)
    session.add_message(role="assistant", content="Earlier findings")
    original = deepcopy(session.messages)
    checkpoint = [{"role": "system", "content": "Saved summary and remaining work."},
        {"role": "user", "content": "Continue fixing app.py."}]
    session.save_context(checkpoint)
    checkpoint[0]["content"] = "Caller changed its temporary list"
    assert session.context_message_count == 2
    assert session.context_messages[0]["content"] == "Saved summary and remaining work."
    assert session.messages == original
    session.add_message(**tool_turn("read_a", "read_b"))
    session.add_message(role="tool", tool_call_id="read_b", content="Read B completed")
    session.add_message(role="user", content="Switch provider and continue.")
    saved = deepcopy(session.messages)

    resumed = Session.load(session.id)
    history = resumed.get_messages()
    assert history[:2] == session.context_messages
    assert [message["role"] for message in history] == ["system", "user", "assistant", "tool", "tool", "user"]
    assert history[3]["tool_call_id"] == "read_a" and "outcome of this call is unknown" in history[3]["content"]
    assert history[4] == {"role": "tool", "tool_call_id": "read_b", "content": "Read B completed"}
    assert history[-1]["content"] == "Switch provider and continue."
    assert resumed.messages == saved
    assert HistoryStore().search(tmp_path, "OriginalArchiveNeedle", session.id)
    resumed.save()
    assert Session.load(session.id).get_messages() == history


def test_tool_result_after_checkpoint_matches_call_inside_checkpoint(tmp_path):
    session = Session.create(tmp_path)
    session.add_message(role="user", content="Inspect app.py")
    session.add_message(**tool_turn("call_pending"))
    session.save_context(session.get_messages()[:-1])  # Checkpoint while the tool has no result yet.
    session.add_message(role="tool", tool_call_id="call_pending", content="Finished reading app.py")
    history = Session.load(session.id).get_messages()
    assert history == session.messages
    assert len([message for message in history if message["role"] == "tool"]) == 1


def test_crash_recovers_partial_response_once_and_marks_running_work_interrupted(tmp_path):
    session = Session.create(tmp_path)
    session.add_message(role="user", content="Finish the remaining tests")
    session.save_context([{"role": "system", "content": "Working checkpoint"}])
    partial = {"role": "assistant", "content": "The next failing test is", "_interrupted": True,
        "model": "openai/fixture"}
    session.pending_response = partial
    session.run_state = {"status": "running", "request": "Finish the remaining tests"}
    session.agent_runs = {"child": {"status": "running"}, "queued": {"status": "waiting"},
        "finished": {"status": "completed"}}
    session.last_model = "openai/fixture"
    session.file_state = {"app.py": {"sha256": "saved-file-digest"}}
    session.save()

    resumed = Session.load(session.id)
    assert resumed.pending_response == {}
    assert resumed.messages == session.messages + [partial]
    assert resumed.get_messages() == session.context_messages + [partial]
    assert resumed.run_state == {"status": "interrupted", "request": "Finish the remaining tests"}
    assert {run["status"] for run in resumed.agent_runs.values()} == {"interrupted", "completed"}
    assert resumed.last_model == session.last_model and resumed.file_state == session.file_state
    resumed.add_message(role="user", content="Continue using Anthropic")
    reloaded = Session.load(session.id)
    assert reloaded.messages.count(partial) == 1
    assert reloaded.get_messages()[-2:] == [partial, {"role": "user", "content": "Continue using Anthropic"}]
    assert Storage.read(["session", session.id])["pending_response"] == {}


def test_full_fork_copies_checkpoint_todos_and_state_without_aliasing(tmp_path):
    source = Session.create(tmp_path)
    source.add_message(role="user", content="Finish the implementation")
    source.add_message(role="assistant", content="Implementation is ready; tests remain")
    source.save_context([{"role": "system", "content": "Run tests next"}])
    source.debug_state = {"status": "unverified", "attempts": ["check one"]}
    source.agent_runs = {"child": {"status": "completed", "result": "Useful finding"}}
    source.file_state = {"app.py": {"sha256": "digest"}}
    source.last_model = "anthropic/fixture"
    source.save()
    todos = TodoList(source.id)
    active = todos.add("Run the tests", priority=3)
    done = todos.add("Write the fix")
    todos.update_status(active.id, "in_progress")
    todos.update_status(done.id, "done")

    forked = source.fork(branch_name="continue")
    loaded = Session.load(forked.id)
    assert loaded.parent_id == source.id and loaded.branch_point == len(source.messages)
    assert loaded.get_messages() == source.get_messages()
    assert loaded.context_message_count == source.context_message_count
    assert loaded.last_model == source.last_model
    assert loaded.debug_state == source.debug_state and loaded.agent_runs == source.agent_runs
    assert loaded.file_state == source.file_state
    copied_todos = TodoList(forked.id)
    assert [todo.to_dict() for todo in copied_todos.list()] == [
        {**todo.to_dict(), "session_id": forked.id} for todo in todos.list()]
    forked.context_messages[0]["content"] = "A different plan"
    forked.debug_state["attempts"].append("fork-only attempt")
    forked.messages[0]["content"] = "A different task"
    copied_todos.update_status(active.id, "done")
    assert source.context_messages[0]["content"] == "Run tests next"
    assert source.debug_state["attempts"] == ["check one"]
    assert source.messages[0]["content"] == "Finish the implementation"
    assert TodoList(source.id).get(active.id).status == "in_progress"


def test_earlier_fork_does_not_inherit_checkpoint_or_tasks_from_the_future(tmp_path):
    source = Session.create(tmp_path)
    source.add_message(role="user", content="Initial request")
    source.add_message(role="assistant", content="Later findings")
    source.save_context([{"role": "system", "content": "Summary including later findings"}])
    source.debug_state = {"status": "verified"}
    source.file_state = {"app.py": {"sha256": "future digest"}}
    source.agent_runs = {"child": {"status": "completed"}}
    TodoList(source.id).add("Task added after the initial message")

    earlier = source.fork(at_message=1, branch_name="earlier")
    assert earlier.get_messages() == source.messages[:1]
    assert earlier.context_messages == [] and earlier.context_message_count == 0
    assert earlier.debug_state == earlier.file_state == earlier.agent_runs == {}
    assert TodoList(earlier.id).list() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [False, True])
async def test_complete_specialist_findings_are_archived_while_preview_stays_bounded(tmp_path, monkeypatch, interrupted):
    from codesm.agent.subagent import SubAgent, get_subagent_config
    from codesm.provider.base import StreamChunk
    from codesm.tool.registry import ToolRegistry

    finding = "earlier finding\n" * 1500 + "MiddleFindingNeedle\n" + "later finding\n" * 1500

    class Fake:
        async def stream(self, **kwargs):
            yield StreamChunk(type="text", content=finding)
            assert Session.load(session.id).agent_runs[worker.id]["result"] == finding
            if interrupted:
                raise ConnectionError("Specialist stream interrupted")

    monkeypatch.setattr("codesm.agent.subagent.get_provider", lambda *_: Fake())
    session = Session.create(tmp_path)
    queue = asyncio.Queue()
    worker = SubAgent(get_subagent_config("planner"), tmp_path, "openai/fixture", ToolRegistry(), {
        "session": session, "agent_runs": session.agent_runs, "save_session": session.save,
        "event_queue": queue, "rules": "",
    })
    if interrupted:
        with pytest.raises(ConnectionError, match="interrupted"):
            _ = [chunk async for chunk in worker.run_streaming("Investigate the whole task")]
    else:
        _ = [chunk async for chunk in worker.run_streaming("Investigate the whole task")]
    saved = Session.load(session.id).agent_runs[worker.id]
    assert saved["result"] == finding
    assert saved["status"] == ("failed" if interrupted else "completed")
    events = [queue.get_nowait() for _ in range(queue.qsize())]
    done = next(event for event in events if event.type == "subagent_done")
    assert done.content == finding[-12000:]
    store = HistoryStore()
    hit = store.search(tmp_path, "MiddleFindingNeedle", session.id)[0]
    assert "MiddleFindingNeedle" in store.read(tmp_path, hit["id"], offset=12000, limit=24000)["text"]


def test_clear_does_not_resurrect_logs_or_todos_when_reindexed(tmp_path):
    session = Session.create(tmp_path)
    session.add_message('user', 'ForgottenEvidenceNeedle')
    TodoList(session.id).add('Forgotten task')
    log = Storage.BASE_DIR / 'events' / f'{session.id}.jsonl'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({'type': 'tool_result', 'text': 'ForgottenEvidenceNeedle'}) + '\n')
    store = HistoryStore()
    store.import_project(tmp_path)
    session.clear()
    store.import_project(tmp_path, force=True)
    assert not store.search(tmp_path, 'ForgottenEvidenceNeedle')
    assert not TodoList(session.id).list()
    assert not log.exists()
