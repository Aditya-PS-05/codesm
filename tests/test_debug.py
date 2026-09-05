import shlex
import sys

import pytest

from codesm.session.session import Session
from codesm.tool.registry import ToolRegistry


@pytest.mark.asyncio
async def test_debug_reproduces_and_verifies_exact_original_command(tmp_path):
    source = tmp_path / "bug.py"
    source.write_text("assert 1 == 2\n")
    session = Session(id="debug", directory=tmp_path)
    tools = ToolRegistry()
    context = {"session": session, "session_id": session.id, "tools": tools, "cwd": tmp_path}
    command = f"{shlex.quote(sys.executable)} bug.py"
    result = await tools.execute("debug", {"action": "reproduce", "command": command}, context)
    assert "reproduced" in result
    assert session.debug_state["reproduction"]["exit_code"] != 0
    await tools.execute("debug", {"action": "hypothesis", "hypothesis": "The expected value is wrong; the fixture defines 1."}, context)
    source.write_text("assert 1 == 1\n")
    assert "Error" in await tools.execute("debug", {"action": "verify", "command": "true"}, context)
    await tools.execute("debug", {"action": "verify"}, context)
    loaded = Session.load(session.id)
    assert loaded.debug_state["status"] == "verified"
    assert loaded.debug_state["verification"]["command"] == command
    assert loaded.debug_state["hypotheses"]
    await tools.execute("bash", {"command": "true"}, context)
    assert session.debug_state["status"] == "unverified"


@pytest.mark.asyncio
async def test_unreproduced_bug_cannot_be_verified_and_attempts_are_bounded(tmp_path):
    tools = ToolRegistry()
    session = Session(id="unverified", directory=tmp_path)
    context = {"session": session, "tools": tools, "cwd": tmp_path}
    await tools.execute("debug", {"action": "reproduce", "command": "true"}, context)
    assert session.debug_state["status"] == "unreproduced"
    assert "No failing reproduction" in await tools.execute("debug", {"action": "verify"}, context)
    session.debug_state = {}
    await tools.execute("debug", {"action": "reproduce", "command": "false"}, context)
    for _ in range(3):
        await tools.execute("debug", {"action": "verify"}, context)
    assert "attempt limit" in await tools.execute("debug", {"action": "verify"}, context)
    assert "attempt limit" in await tools.execute("bash", {"command": "true"}, context)
    assert session.debug_state["status"] == "unverified"

@pytest.mark.asyncio
async def test_workflow_cannot_skip_reproduction_and_raw_rerun_is_recorded(tmp_path):
    from codesm.tool.registry import ToolRegistry
    from codesm.session.session import Session
    registry = ToolRegistry()
    session = Session(id="enforced-debug", directory=tmp_path,
                      debug_state={"status":"unverified","attempts":0,"max_attempts":3,"hypotheses":[]})
    context = {"session":session,"session_id":session.id,"cwd":tmp_path,"tools":registry,
               "debug_state":session.debug_state,"save_session":session.save}
    result = await registry.execute("bash", {"command":"false"}, context)
    assert "capture a failing reproduction" in result
    result = await registry.execute("write", {"path":"target","content":"yes"}, context)
    assert "capture a failing reproduction" in result and not (tmp_path / "target").exists()
    await registry.execute("debug", {"action":"reproduce","command":"test -f target"}, context)
    await registry.execute("write", {"path":"target","content":"yes"}, context)
    await registry.execute("bash", {"command":"test -f target"}, context)
    assert session.debug_state["status"] == "verified" and session.debug_state["attempts"] == 1
