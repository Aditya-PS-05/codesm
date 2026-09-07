"""Local Science protocol checks without credentials, models, or a running daemon."""

import asyncio
import hashlib
import io
import json
import os
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from PIL import Image

from codesm.agent.agent import Agent
from codesm.agent import claude_science as science
from codesm.config import Config
from codesm.session.session import Session


@pytest.fixture
def peer(tmp_path, monkeypatch):
    """Real CLI discovery/login, with a deterministic peer at the HTTP boundary."""
    binary = tmp_path / "claude-science"
    binary.write_text(f"#!{sys.executable}\nimport os\nprint(os.environ['SCIENCE_TEST_URL'])\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("SCIENCE_TEST_URL", "http://127.0.0.1:38492/?nonce=private-test-nonce")
    monkeypatch.setattr(science, "POLL_INTERVAL", 0)
    state = SimpleNamespace(requests=[], frames={}, submissions=[], responses=[], executed=[],
                            disconnect=False, stale=False, child=False, compact=False, unresolved=False, polls=0,
                            image=None)

    def message(role, key, content, **fields):
        return {"role": role, "_uuid": key, "content": content, **fields}

    def handle(req):
        path = req.url.path
        state.requests.append(req)
        if path == "/api/auth/nonce":
            assert parse_qs(req.content.decode())["nonce"] == ["private-test-nonce"]
            return httpx.Response(302, headers={"set-cookie": "operon_auth=private-cookie; Path=/", "location": "/"})
        assert "operon_auth=private-cookie" in req.headers.get("cookie", "")
        if path == "/api/csrf":
            return httpx.Response(200, json={}, headers={"set-cookie": "operon_csrf=test-csrf; Path=/"})
        if path == "/api/auth/status":
            return httpx.Response(200, json={"authenticated": True})
        if req.method == "POST":
            assert req.headers["origin"] == "http://127.0.0.1:38492"
            assert req.headers["x-operon-csrf"] == "test-csrf"
        if "/cell-images/" in path or path == "/api/artifacts/versions/plot-version":
            assert state.image is not None
            return httpx.Response(200, content=state.image, headers={"content-type": "image/png"})
        if path == "/api/projects":
            return httpx.Response(201, json={"project_id": "project-one"})
        if path == "/api/frames":
            fid = "frame-" + str(len(state.frames) + 1)
            state.frames[fid] = {"id": fid, "root_frame_id": fid, "status": "completed",
                "completed_at": "old-completion", "messages": [
                    message("user", fid + "old-user", [{"type": "text", "text": "Old request"}]),
                    message("assistant", fid + "old-assistant", [{"type": "text", "text": "Do not replay me"}])],
                "output_data": {}, "turn": 0}
            return httpx.Response(201, json={"root_frame_id": fid})
        fid = path.split("/")[3].removesuffix("-child")
        frame = state.frames[fid]
        records = frame["messages"]
        turn = frame["turn"]
        tool = f"{fid}-tool-{turn}"
        ask = f"{fid}-ask-{turn}"
        target = fid + "-child" if state.child else fid
        if path.endswith("/messages"):
            start = int(req.url.params["from"])
            # Smaller pages force real pagination, even for a short conversation.
            return httpx.Response(200, json={"from": start, "total": len(records), "messages": records[start:start + 2]})
        if path.endswith("/message"):
            if state.stale:
                state.stale = False
                return httpx.Response(403, json={"code": "csrf_stale"})
            body = json.loads(req.content)
            state.submissions.append(body)
            frame.update(turn=turn + 1, pending_text=body["input_data"]["request"], stale_snapshot=True)
            return httpx.Response(200, json={"frame_id": fid, "root_frame_id": fid, "status": "accepted"})
        if path.endswith("/resolve-input"):
            reply = json.loads(req.content)["responses"][0]
            state.responses.append(reply)
            assert "scope" not in reply
            if state.unresolved:
                return httpx.Response(200, json={"status": "partial", "remaining_tool_ids": [reply["tool_id"]]})
            if reply["tool_id"] == tool:
                if reply["approved"]:
                    state.executed.append(tool)
                next(r for r in records if r["_uuid"] == tool + "-result")["content"] = [{
                    "type": "tool_result", "tool_use_id": tool,
                    "content": "Executed" if reply["approved"] else "Denied", "is_error": not reply["approved"]}]
                if state.image:
                    # Science adds these to a tool's already-seen assistant record.
                    next(r for r in records if r["_uuid"] == tool)["_cell_images"] = {tool: [{
                        "sha256": hashlib.sha256(state.image).hexdigest(), "filename": "response.png",
                        "content_type": "image/png", "width": 320, "height": 180}]}
                records.append(message("assistant", ask, [{"type": "tool_use", "id": ask, "name": "ask_user", "input": {}}]))
                records.append(message("user", ask + "-result", [{"type": "tool_result", "tool_use_id": ask,
                    "content": '{"status":"awaiting_user_response"}'}]))
                frame["pending"] = {"kind": "ask", "requestId": ask, "tool_id": ask,
                    "frameId": target, "mode": "parked", "questions": [{"question": "Which marker?", "options": [{"label": "OK"}]}]}
                frame["status"] = "awaiting_user_response"
            else:
                assert reply["tool_id"] == ask
                next(r for r in records if r["_uuid"] == ask + "-result")["content"][0]["content"] = "Answer received"
                records.extend([
                    message("assistant", ask + "-final", [{"type": "thinking", "thinking": "private reasoning", "signature": "opaque"},
                        {"type": "text", "text": "Finished."}], _tokens={"input": 17, "output": 4, "cache_read": 12}),
                ])
                if state.image:
                    records[-1]["_artifact_refs"] = {"response.png": {
                        "artifact_id": "plot-artifact", "version_id": "plot-version"}}
                frame.pop("pending", None)
                frame.update(status="completed", completed_at=f"completion-{turn}")
            return httpx.Response(200, json={"status": "accepted", "remaining_tool_ids": []})
        if path.endswith("/cancel"):
            frame["status"] = "cancelled"
            return httpx.Response(200, json={})
        if state.disconnect and frame.get("pending_text"):
            state.disconnect = False
            raise httpx.ConnectError("local connection closed", request=req)
        state.polls += 1
        assert state.polls < 128, "Science's polling loop did not make progress"
        if frame.pop("stale_snapshot", False):
            pass  # A completed snapshot from the previous turn must not finish this turn.
        elif text := frame.pop("pending_text", None):
            records.extend([
                message("user", tool + "-notice", [{"type": "text", "text": "private harness notice"}], _harness_notice=True),
                message("user", tool + "-request", [{"type": "text", "text": text}]),
                message("assistant", tool, [{"type": "text", "text": "Working."},
                    {"type": "tool_use", "id": tool, "name": "python", "input": {"code": "print('marker')"}}], _tokens={"input": 11, "output": 3}),
                message("user", tool + "-result", []),
            ])
            if state.compact:
                del records[:2]
            frame["pending"] = {"kind": "local_exec", "requestId": tool, "tool_id": tool,
                "frameId": target, "mode": "live", "code": "print('marker')"}
            frame["status"] = "processing"
        data = {k: v for k, v in frame.items() if k not in {"messages", "pending_text", "pending"}}
        data["message_count"] = len(records)
        if pending := frame.get("pending"):
            if state.child:
                data["status"] = "processing"
                data["children"] = [{"id": target, "root_frame_id": fid, "status": frame["status"],
                    "output_data": {"pending_input_requests": [pending]}}]
            else:
                data["output_data"] = {"pending_input_requests": [pending]}
        return httpx.Response(200, json=data)

    original = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs["trust_env"] is False
        return original(**kwargs, transport=httpx.MockTransport(handle))
    monkeypatch.setattr(science.httpx, "AsyncClient", client)
    return state


async def test_science_figures_arrive_once_and_survive_resume_and_fork(tmp_path, monkeypatch, peer):
    from codesm.agent.backends import handoff_prompt
    from codesm.storage.images import image_path
    stream = io.BytesIO()
    Image.new("RGB", (320, 180), "navy").save(stream, format="PNG")
    peer.image = stream.getvalue()

    async def approve(*args):
        return True
    async def answer(question):
        return "OK"
    monkeypatch.setattr(science, "approve", approve)
    agent = Agent(tmp_path, backend="claude-science", config=Config(), ask_user=answer)
    chunks = [chunk async for chunk in agent.chat("Plot the measured response")]
    figures = [chunk for chunk in chunks if chunk.type == "image"]
    assert len(figures) == 1  # Same PNG in a cell and an artifact reference.
    assert figures[0].content == "response.png"
    assert chunks.index(figures[0]) < next(i for i, chunk in enumerate(chunks) if "Finished." in chunk.content)
    path = image_path(figures[0].metadata)
    assert path.read_bytes() == peer.image and path.stat().st_mode & 0o777 == 0o600

    saved = Session.load(agent.session.id)
    assert sum("image" in message for message in saved.get_messages_for_display()) == 1
    assert "Saved image:" in str(saved.get_messages())
    assert all("image" not in message for message in saved.get_messages())
    assert "private-cookie" not in str(saved.messages) and "nonce=" not in str(saved.messages)
    saved.add_message("user", "Continue with Codex")
    assert str(path) in handoff_prompt(saved, "Continue with Codex", {})
    fork = saved.fork()
    fork_path = image_path(next(message["image"] for message in fork.messages if "image" in message))
    assert fork_path != path and fork_path.read_bytes() == peer.image
    saved.clear()
    assert not path.exists() and fork_path.exists()
    fork.delete()
    assert not fork_path.exists()


@pytest.mark.parametrize("allowed,child,compact", [(True, False, False), (False, False, True), (True, True, False)])
async def test_science_history_approval_usage_and_same_session(tmp_path, monkeypatch, peer, allowed, child, compact):
    peer.child, peer.compact, peer.stale = child, compact, True
    async def approve(*args):
        assert args[1:3] == ("claude-science", "local_exec")
        return allowed
    async def answer(question):
        assert question["question"] == "Which marker?"
        return "OK"
    monkeypatch.setattr(science, "approve", approve)
    agent = Agent(tmp_path, backend="claude-science", config=Config(), ask_user=answer)
    first = [c async for c in agent.chat("Solve the shared task")]
    assert "".join(c.content for c in first if c.type == "text") == "Working.\n\nFinished."
    assert bool(peer.executed) == allowed
    assert sum(r["input_tokens"] for r in agent.session.usage_records) == 28  # Cache is already included.
    assert all(not r["cost_known"] for r in agent.session.usage_records)
    saved = Session.load(agent.session.id)
    assert saved.backend_sessions["claude-science"]["id"] == "frame-1"
    assert "private reasoning" not in str(saved.messages) and "private harness" not in str(saved.messages)
    assert "awaiting_user_response" not in str(saved.messages)
    assert sum(m.get("content") == "Answer received" for m in saved.messages if m.get("role") == "tool") == 1
    resumed = Agent(tmp_path, session=saved, config=Config(), ask_user=answer)
    assert resumed.budget._tokens == 0 and resumed.budget._requests == 0
    await asyncio.wait_for(collect(resumed, "Continue with the same Science context"), 20)
    assert len(peer.frames) == 1
    assert peer.submissions[-1]["input_data"]["request"].endswith("Continue with the same Science context")
    assert len(peer.responses) == 4
    assert not any("private-test-nonce" in str(r.url) for r in peer.requests)
    assert "private-cookie" not in str(resumed.session.backend_sessions)


async def collect(agent, prompt):
    return [c async for c in agent.chat(prompt)]


async def test_science_cancellation_restarts_with_saved_history(tmp_path, monkeypatch, peer):
    waiting = asyncio.Event()
    async def pause(*args):
        waiting.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(science, "approve", pause)
    agent = Agent(tmp_path, backend="claude-science", config=Config())
    task = asyncio.create_task(collect(agent, "Remember the original task"))
    await asyncio.wait_for(waiting.wait(), 20)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 20)
    saved = Session.load(agent.session.id)
    assert saved.run_state["status"] == "interrupted"
    assert peer.frames["frame-1"]["status"] == "cancelled"
    assert saved.backend_sessions["claude-science"]["previous_id"] == "frame-1"
    assert not saved.backend_sessions["claude-science"].get("id")
    assert any(m.get("content") == "Working." for m in saved.messages)
    assert not any("stop" in str(r.url) for r in peer.requests)
    async def deny(*args):
        return False
    monkeypatch.setattr(science, "approve", deny)
    resumed = Agent(tmp_path, session=saved, config=Config())
    await asyncio.wait_for(collect(resumed, "Continue after interruption"), 20)
    assert "Remember the original task" in peer.submissions[-1]["input_data"]["request"]
    assert "Working." in peer.submissions[-1]["input_data"]["request"]
    assert len(peer.frames) == 2
    assert sum(r.url.path == "/api/projects" for r in peer.requests) == 1


async def test_science_disconnect_cancels_accepted_work(tmp_path, peer):
    peer.disconnect = True
    agent = Agent(tmp_path, backend="claude-science", config=Config())
    with pytest.raises(httpx.ConnectError):
        await collect(agent, "Start a task")
    assert peer.frames["frame-1"]["status"] == "cancelled"
    assert Session.load(agent.session.id).run_state["status"] == "failed"


@pytest.mark.parametrize("url", ["https://example.com/?nonce=private-test-nonce", "http://127.0.0.1:80@evil.test/?nonce=x",
                                  "http://127.0.0.1:38492/?nonce=x&nonce=y", "not a URL"])
async def test_science_refuses_remote_or_invalid_login_urls(tmp_path, monkeypatch, peer, url):
    monkeypatch.setenv("SCIENCE_TEST_URL", url)
    with pytest.raises(RuntimeError, match="unsupported login URL") as error:
        async with science.connect(tmp_path):
            pytest.fail("Should not authenticate")
    assert not peer.requests and "nonce=" not in str(error.value)


async def test_science_read_only_rejected_before_connecting(tmp_path, peer):
    agent = Agent(tmp_path, backend="claude-science", config=Config(read_only=True))
    with pytest.raises(ValueError, match="read-only"):
        await collect(agent, "Only inspect")
    assert not peer.requests


async def test_science_leaves_an_already_active_frame_alone(tmp_path, peer):
    peer.frames["frame-1"] = {"id": "frame-1", "status": "processing", "messages": [], "turn": 0}
    agent = Agent(tmp_path, backend="claude-science", config=Config())
    agent.session.backend_sessions["claude-science"] = {"id": "frame-1", "project_id": "project-one"}
    with pytest.raises(RuntimeError, match="already active"):
        await collect(agent, "Continue")
    assert not peer.submissions
    assert not any(r.url.path.endswith("/cancel") for r in peer.requests)


async def test_science_unknown_interaction_is_not_approved(tmp_path, monkeypatch, peer):
    async def unexpected(*args):
        pytest.fail("Unknown interactions must not inherit permission bypass")
    monkeypatch.setattr(science, "approve", unexpected)
    async with science.connect(tmp_path) as client:
        with pytest.raises(RuntimeError, match="unsupported"):
            await science.answer(client, "frame-1", {"kind": "future_permission"}, "session", None)
    assert not any(r.url.path.endswith("/resolve-input") for r in peer.requests)


async def test_science_unresolved_approval_stops_instead_of_hanging(tmp_path, monkeypatch, peer):
    peer.unresolved = True
    async def deny(*args):
        return False
    monkeypatch.setattr(science, "approve", deny)
    agent = Agent(tmp_path, backend="claude-science", config=Config())
    with pytest.raises(RuntimeError, match="unresolved"):
        await collect(agent, "Run the task")
    assert peer.frames["frame-1"]["status"] == "cancelled"
