"""Codex app-server over its documented JSON-RPC stdio protocol."""

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import os
import signal

from codesm.provider.base import StreamChunk
from .backends import approve, tool_call_id


class AppServer:
    def __init__(self, process):
        self.process = process
        self.pending = {}
        self.events = asyncio.Queue()
        self.next_id = 0
        self.stderr = bytearray()

    async def send(self, message):
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, 60)
        finally:
            self.pending.pop(request_id, None)

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "method" not in message and message.get("id") in self.pending:
                    future = self.pending[message["id"]]
                    if not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError("Codex: " + str(message["error"].get("message", "request failed"))))
                        else:
                            future.set_result(message.get("result", {}))
                elif "method" in message:
                    self.events.put_nowait(message)
            raise RuntimeError("Codex app-server disconnected. Check `codex login status` and resume the saved session.")
        except Exception as error:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            self.events.put_nowait(error)

    async def drain_stderr(self):
        while chunk := await self.process.stderr.read(4096):
            self.stderr.extend(chunk)
            del self.stderr[:-8192]


@asynccontextmanager
async def connect(directory):
    process = await asyncio.create_subprocess_exec(
        "codex", "app-server", "--listen", "stdio://", cwd=directory,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=16 * 1024 * 1024,
    )
    server = AppServer(process)
    reader = asyncio.create_task(server.read())
    stderr = asyncio.create_task(server.drain_stderr())
    try:
        await server.request("initialize", {"clientInfo": {"name": "codesm", "title": "codesm", "version": "0.1.0"}})
        await server.send({"method": "initialized", "params": {}})
        yield server
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        reader.cancel()
        stderr.cancel()
        await asyncio.gather(reader, stderr, return_exceptions=True)


async def answer_request(server, event, session_id, ask_user, read_only, items):
    method, params = event["method"], event.get("params", {})
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        details = {**params, "item": items.get(params.get("itemId"), {})}
        allowed = not read_only and await approve(session_id, "codex", method, details)
        result = {"decision": "accept" if allowed else "decline"}
    elif method == "item/permissions/requestApproval":
        # Extra permission grants remain denied; command/file approvals work above.
        result = {"permissions": {}, "scope": "turn"}
    elif method == "item/tool/requestUserInput":
        answers = {}
        for question in params.get("questions", []):
            answer = await ask_user(question) if ask_user else None
            if answer is not None:
                answers[question["id"]] = {"answers": [answer]}
        result = {"answers": answers}
    elif method == "mcpServer/elicitation/request":
        result = {"action": "decline", "content": None}
    else:
        await server.send({"id": event["id"], "error": {"code": -32601,
            "message": f"codesm does not support {method}; the action was not approved."}})
        return
    await server.send({"id": event["id"], "result": result})


async def stream(*, directory, prompt, state, save, model, read_only, session_id, ask_user, max_turns, instructions=""):
    async with connect(directory) as server:
        params = {"cwd": str(directory), "approvalPolicy": "never" if read_only else "untrusted",
                  "sandbox": "read-only" if read_only else "workspace-write"}
        if model:
            params["model"] = model
        if instructions:
            params["developerInstructions"] = instructions
        method = "thread/start"
        if state.get("id"):
            method = "thread/resume"
            params["threadId"] = state["id"]
        result = await server.request(method, params)
        state["id"] = result["thread"]["id"]
        save()
        thread_id = state["id"]
        result = await server.request("turn/start", {"threadId": thread_id,
            "input": [{"type": "text", "text": prompt}]})
        turn_id = result["turn"]["id"]
        items, streamed, tool_names = {}, set(), {}
        total_usage = None
        completed = False
        try:
            while True:
                event = await server.events.get()
                if isinstance(event, Exception):
                    raise event
                method, data = event["method"], event.get("params", {})
                if "id" in event:
                    await answer_request(server, event, session_id, ask_user, read_only, items)
                    continue
                if data.get("threadId", thread_id) != thread_id or data.get("turnId", turn_id) != turn_id:
                    continue
                item_id = data.get("itemId", "")
                if method == "item/agentMessage/delta":
                    streamed.add(item_id)
                    yield StreamChunk(type="text", content=data.get("delta", ""))
                elif method in {"item/started", "item/completed"}:
                    item = data.get("item", {})
                    item_id = item.get("id", "")
                    items[item_id] = item
                    kind = item.get("type")
                    call_id = tool_call_id("codex", thread_id, turn_id, item_id)
                    if kind == "agentMessage":
                        if method == "item/completed" and item_id not in streamed:
                            yield StreamChunk(type="text", content=item.get("text", ""))
                    elif kind in {"commandExecution", "fileChange", "mcpToolCall", "webSearch", "collabAgentToolCall", "dynamicToolCall"}:
                        name = {"commandExecution": "bash", "fileChange": "edit", "webSearch": "web_search"}.get(kind, item.get("tool", kind))
                        if method == "item/started":
                            tool_names[item_id] = name
                            yield StreamChunk(type="tool_call", name=name, id=call_id, args=item)
                        else:
                            if item_id not in tool_names:
                                yield StreamChunk(type="tool_call", name=name, id=call_id, args=item)
                            content = item.get("aggregatedOutput") or json.dumps(item, ensure_ascii=False)
                            if kind == "commandExecution":
                                content += f"\nExit code: {item.get('exitCode')}"
                            if item.get("status") in {"failed", "declined"} or item.get("exitCode") not in (None, 0):
                                content = "Error: " + content
                            yield StreamChunk(type="tool_result", name=name, id=call_id, content=content)
                elif method == "thread/tokenUsage/updated":
                    total_usage = data.get("tokenUsage", {}).get("total")
                elif method == "error" and not data.get("willRetry", False):
                    raise RuntimeError("Codex: " + str(data.get("error", {}).get("message", "turn failed")))
                elif method == "turn/completed":
                    turn = data.get("turn", {})
                    if total_usage:
                        previous = state.get("token_usage", {})
                        yield StreamChunk(type="usage", metadata={
                            "input_tokens": max(0, total_usage.get("inputTokens", 0) - previous.get("inputTokens", 0)),
                            "output_tokens": max(0, total_usage.get("outputTokens", 0) - previous.get("outputTokens", 0)),
                        })
                        state["token_usage"] = total_usage
                        save()
                    if turn.get("status") != "completed":
                        raise RuntimeError("Codex: " + str((turn.get("error") or {}).get("message", turn.get("status", "incomplete"))))
                    completed = True
                    yield StreamChunk(type="run_status", content="completed")
                    return
        finally:
            if not completed:
                with suppress(Exception):
                    await asyncio.wait_for(server.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}), 3)
