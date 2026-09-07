"""Experimental adapter for Claude Science's local API (tested with 0.1.27)."""

import asyncio
from contextlib import asynccontextmanager, suppress
import json
from pathlib import PurePosixPath
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from codesm.provider.base import StreamChunk
from codesm.storage.images import IMAGE_FORMATS, MAX_IMAGE_BYTES, save_image
from .backends import approve, tool_call_id

POLL_INTERVAL = 0.5
TERMINAL = {"completed", "success", "failed", "cancelled", "replaced"}
PERMISSIONS = {
    "network", "host", "host_delete", "artifact_delete", "mcp_tool",
    "customize_mutation", "local_exec", "remote_exec", "remote_read",
    "credential_access", "byoc_submit", "byoc_kernel", "infer_submit",
}
SETUP = "Start Claude Science with `claude-science serve --detached`, sign in in its browser, then continue in codesm."


def resource_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
        raise RuntimeError("Claude Science returned an invalid session or project ID.")
    return value


def csrf(client):
    return next((c.value for c in client.cookies.jar if c.name == "operon_csrf"), "")


async def request(client, method, path, **kwargs):
    """Only retry a confirmed CSRF rejection, never an ambiguous submission."""
    response = await client.request(method, path, headers={"x-operon-csrf": csrf(client)}, **kwargs)
    try:
        stale = response.status_code == 403 and response.json().get("code") == "csrf_stale"
    except ValueError:
        stale = False
    if stale:
        refresh = await client.get("/api/csrf")
        refresh.raise_for_status()
        response = await client.request(method, path, headers={"x-operon-csrf": csrf(client)}, **kwargs)
    if response.status_code in {401, 403}:
        raise RuntimeError("Claude Science authentication expired. " + SETUP)
    if not response.is_success:
        try:
            detail = str(response.json().get("detail", "Request failed"))[:500]
        except ValueError:
            detail = "Request failed"
        raise RuntimeError(f"Claude Science HTTP {response.status_code}: {detail}")
    return response.json()


@asynccontextmanager
async def connect(directory):
    # Use the native one-time browser login flow, not stored OAuth credentials.
    process = await asyncio.create_subprocess_exec(
        "claude-science", "url", cwd=directory, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 15)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode:
        raise RuntimeError(SETUP)
    try:
        url = urlsplit(output.decode().strip())
        nonce = parse_qs(url.query).get("nonce", [])
        if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}
                or not url.port or url.username or url.password or url.path not in {"", "/"}
                or len(nonce) != 1 or not nonce[0] or url.fragment):
            raise ValueError("Expected a local Science login URL")
        base = f"{url.scheme}://{url.netloc}"
    except (ValueError, UnicodeError):
        raise RuntimeError("Claude Science returned an unsupported login URL. " + SETUP) from None
    async with httpx.AsyncClient(base_url=base, headers={"Origin": base}, trust_env=False, timeout=15) as client:
        try:
            auth = await client.post("/api/auth/nonce", data={"nonce": nonce[0], "dest": "/"})
            if auth.status_code not in {200, 302, 303} or not any(c.name == "operon_auth" for c in client.cookies.jar):
                raise ValueError("Local login failed")
            refresh = await client.get("/api/csrf")
            refresh.raise_for_status()
            status = await request(client, "GET", "/api/auth/status")
            if not status.get("authenticated"):
                raise ValueError("Sign-in required")
        except (httpx.HTTPError, ValueError, RuntimeError):
            # Login URLs, cookies, and nonces must not enter transcripts or logs.
            raise RuntimeError("Could not authenticate with the local Claude Science app. " + SETUP) from None
        yield client


async def messages(client, frame_id, start=0):
    """Read the persisted transcript, including pages beyond the default limit."""
    while True:
        page = await request(client, "GET", f"/api/frames/{frame_id}/messages", params={"from": start, "limit": 200})
        records, total = page.get("messages"), page.get("total")
        if not isinstance(records, list) or not isinstance(total, int) or page.get("from") != start:
            raise RuntimeError("Unsupported Claude Science transcript format; the local API may have changed.")
        for record in records:
            if not isinstance(record.get("_uuid"), str):
                raise RuntimeError("Claude Science transcript is missing message IDs.")
            yield record
        start += len(records)
        if start >= total:
            return
        if not records:
            raise RuntimeError("Claude Science returned an incomplete transcript page.")


def image_sources(record, frame_id):
    """Science 0.1.27 uses saved cell figures and versioned artifact references."""
    if record.get("_harness_notice"):
        return
    extensions = {mime: extension for extension, mime in IMAGE_FORMATS.values()}
    cells = record.get("_cell_images")
    if isinstance(cells, dict):
        for figures in cells.values():
            for figure in figures if isinstance(figures, list) else []:
                if not isinstance(figure, dict) or not isinstance(figure.get("filename"), str):
                    continue
                media_type = figure.get("content_type", "image/png")
                extension = extensions.get(media_type) if isinstance(media_type, str) else None
                if not extension:
                    continue
                digest, path = figure.get("sha256"), figure.get("path")
                if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest):
                    url = f"/api/frames/{resource_id(frame_id)}/cell-images/{digest}.{extension}"
                elif isinstance(path, str) and path and len(path) <= 4096 and "\0" not in path:
                    # Match Science's own viewer: downloads stay confined to its workspaces.
                    url = "/api/compute/local/download?" + urlencode({
                        "path": path, "disposition": "inline", "confine": "workspaces"})
                else:
                    continue
                yield url, figure["filename"]
    refs = record.get("_artifact_refs")
    if isinstance(refs, dict):
        suffixes = {"." + extension for extension in extensions.values()} | {".jpeg", ".tif"}
        for filename, ref in refs.items():
            if not isinstance(filename, str) or not isinstance(ref, dict) or PurePosixPath(filename).suffix.lower() not in suffixes:
                continue
            version, artifact = ref.get("version_id"), ref.get("artifact_id")
            identifier = version or artifact
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", identifier):
                continue
            prefix = "/api/artifacts/versions/" if version else "/api/artifacts/"
            yield prefix + identifier, filename


async def download_image(client, url, session_id):
    # URLs come only from image_sources, never from arbitrary Markdown/model URLs.
    async with client.stream("GET", url, follow_redirects=False) as response:
        if not response.is_success:
            raise ValueError(f"Science image request returned HTTP {response.status_code}")
        length = response.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_IMAGE_BYTES:
            raise ValueError("Image exceeds the 20 MiB preview limit")
        data = bytearray()
        async for part in response.aiter_bytes(64 * 1024):
            data.extend(part)
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError("Image exceeds the 20 MiB preview limit")
    return await asyncio.to_thread(save_image, bytes(data), session_id)


async def stream_images(client, record, frame_id, session_id, seen, downloaded, displayed):
    for url, filename in image_sources(record, frame_id):
        key = (record["_uuid"], url)
        if key in seen:
            continue
        seen.add(key)
        if url not in downloaded:
            metadata = {"source_url": str(client.base_url.join(url))}
            try:
                metadata.update(await download_image(client, url, session_id))
            except ValueError as error:
                metadata["error"] = str(error)
            except (httpx.HTTPError, OSError):
                metadata["error"] = "Could not load the image from Science"
            downloaded[url] = metadata
        metadata = downloaded[url]
        identity = metadata.get("path", url)
        if identity in displayed:
            continue
        displayed.add(identity)
        caption = "".join(char for char in filename if char.isprintable())[:200] or "Research figure"
        yield StreamChunk(type="image", content=caption, metadata=dict(metadata))


async def answer(client, frame_id, pending, session_id, ask_user):
    kind = pending.get("kind")
    reply = {"requestId": pending.get("requestId"), "tool_id": pending.get("tool_id")}
    if kind == "ask":
        answers = {}
        for question in pending.get("questions", []):
            value = await ask_user(question) if ask_user else None
            if value is None:
                break
            answers[question["question"]] = value
        allowed = bool(answers) and len(answers) == len(pending.get("questions", []))
        reply.update(approved=allowed, action="answer" if allowed else "cancel", answers=answers if allowed else {})
    elif kind in PERMISSIONS:
        allowed = await approve(session_id, "claude-science", kind, pending)
        # Do not request persistent conversation, project, or global grants.
        reply.update(approved=allowed, action="allow" if allowed else "deny")
    else:
        raise RuntimeError(f"Claude Science needs an unsupported {kind!r} interaction. Review it in the Science browser.")
    result = await request(client, "POST", f"/api/frames/{frame_id}/resolve-input", json={"responses": [reply]})
    if pending.get("tool_id") in (result.get("remaining_tool_ids") or []):
        raise RuntimeError("Claude Science left this request unresolved. Resume it in the Science browser.")


def normalize(record, frame_id, tools, separate):
    if record.get("_harness_notice"):
        return
    content = record.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        kind = block.get("type")
        if record.get("role") == "assistant" and kind == "text" and block.get("text"):
            yield StreamChunk(type="text", content=("\n\n" if separate else "") + block["text"])
            separate = True
        elif record.get("role") == "assistant" and kind in {"tool_use", "server_tool_use"}:
            tools[block["id"]] = block["name"]
            yield StreamChunk(type="tool_call", name=block["name"], args=block.get("input", {}),
                              id=tool_call_id("claude-science", frame_id, block["id"]))
        elif kind == "tool_result" or (isinstance(kind, str) and kind.endswith("_tool_result") and "tool_use_id" in block):
            tool_id = block["tool_use_id"]
            result = block.get("content", "")
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False)
            yield StreamChunk(type="tool_result", name=tools.get(tool_id, "tool"),
                              id=tool_call_id("claude-science", frame_id, tool_id),
                              content=("Error: " if block.get("is_error") else "") + result)
    if record.get("role") == "assistant" and record.get("_tokens"):
        usage = record["_tokens"]
        yield StreamChunk(type="usage", metadata={"input_tokens": usage.get("input", 0) or 0,
                                                  "output_tokens": usage.get("output", 0) or 0})


def pending_message(record):
    """Science fills empty tool messages and replaces pending answers in place."""
    if record.get("role") == "user" and not record.get("_harness_notice") and not record.get("content"):
        return True
    for block in record.get("content", []) if isinstance(record.get("content"), list) else []:
        if block.get("type") != "tool_result" or not isinstance(block.get("content"), str):
            continue
        with suppress(ValueError):
            value = json.loads(block["content"])
            if isinstance(value, dict) and value.get("status") == "awaiting_user_response":
                return True
    return False


async def stream(*, directory, prompt, state, save, model, read_only, session_id, ask_user, max_turns, instructions=""):
    if read_only:
        raise ValueError("Claude Science cannot enforce codesm's read-only mode. Use the native backend or Codex for this task.")
    async with connect(directory) as client:
        frame_id = resource_id(state["id"]) if state.get("id") else None
        if frame_id:
            frame = await request(client, "GET", f"/api/frames/{frame_id}")
            if frame.get("status") not in TERMINAL:
                raise RuntimeError("This Science session is already active. Finish or cancel it in the Science browser first.")
            if frame["status"] not in {"completed", "success"}:
                state["previous_id"] = state.pop("id")
                frame_id = None
        if not frame_id:
            if not state.get("project_id"):
                project = await request(client, "POST", "/api/projects", json={
                    "name": f"Codesm: {directory.name}", "description": f"Codesm project at {directory}",
                })
                state["project_id"] = resource_id(project["project_id"])
                save()
            frame = await request(client, "POST", "/api/frames", json={"project_id": resource_id(state["project_id"])})
            frame_id = state["id"] = resource_id(frame["root_frame_id"])
            save()
        seen, tools = set(), {}
        seen_images, downloaded_images, displayed_images = set(), {}, set()
        count, anchor = 0, None
        async for record in messages(client, frame_id):
            seen.add(record["_uuid"])
            seen_images.update((record["_uuid"], url) for url, _ in image_sources(record, frame_id))
            count, anchor = count + 1, record["_uuid"]
            for block in record.get("content", []) if isinstance(record.get("content"), list) else []:
                if block.get("type") in {"tool_use", "server_tool_use"}:
                    tools[block["id"]] = block["name"]
        previous_completion = frame.get("completed_at")
        text = (f"The host project directory is {directory}. Use Science's normal filesystem approval "
                "flow when access is needed; this path is not a sandbox mount.\n\n")
        if instructions:
            text += "Project instructions:\n" + instructions + "\n\n"
        body = {"input_data": {"request": text + prompt}}
        if model:
            body["model"] = model
        done, acknowledged, separated, deferred = False, False, False, False
        answered = set()
        submitted = time.monotonic()
        image_check = submitted
        try:
            await request(client, "POST", f"/api/frames/{frame_id}/message", json=body)
            while True:
                frame = await request(client, "GET", f"/api/frames/{frame_id}", params={"include_children": "true"})
                # ponytail: poll saved messages for ordered, deduplicated output. Add token
                # deltas only when Science exposes stable message/block IDs for them.
                if (frame.get("message_count") != count or frame.get("status") in TERMINAL or deferred
                        or time.monotonic() - image_check >= 1):
                    # Cell figures can arrive on a previously seen message. Recheck a
                    # short tail while running and the complete transcript at turn end.
                    # ponytail: 20-message live tail; use image deltas if Science publishes them.
                    start = 0 if deferred or frame.get("status") in TERMINAL else max(0, count - 20)
                    records = [r async for r in messages(client, frame_id, start)]
                    if start and (len(records) < count - start or records[count - start - 1]["_uuid"] != anchor):
                        records = [r async for r in messages(client, frame_id)]
                        start = 0  # Compaction rewrote the transcript; IDs prevent replay.
                    count = start + len(records)
                    image_check = time.monotonic()
                    anchor = records[-1]["_uuid"] if records else None
                    deferred = False
                    for record in records:
                        if record["_uuid"] not in seen:
                            if pending_message(record):
                                deferred = True
                            else:
                                seen.add(record["_uuid"])
                                acknowledged |= (record.get("role") == "user" and not record.get("_harness_notice")
                                    and any(block.get("type") == "text" and block.get("text") == body["input_data"]["request"]
                                            for block in record.get("content", []) if isinstance(block, dict)))
                                for chunk in normalize(record, frame_id, tools, separated):
                                    if chunk.type == "text":
                                        separated = True
                                    yield chunk
                        async for chunk in stream_images(client, record, frame_id, session_id,
                                                         seen_images, downloaded_images, displayed_images):
                            separated = True
                            yield chunk
                status = frame.get("status")
                completed = acknowledged and not deferred and frame.get("completed_at") != previous_completion
                if status in TERMINAL and (completed or status not in {"completed", "success"}):
                    if status not in {"completed", "success"}:
                        raise RuntimeError("Claude Science: " + str((frame.get("output_data") or {}).get("error") or status))
                    done = True
                    yield StreamChunk(type="run_status", content="completed")
                    return
                if (not acknowledged or status in {"completed", "success"}) and time.monotonic() - submitted > 30:
                    raise RuntimeError("Claude Science did not acknowledge the prompt; the local API may have changed.")
                if status not in TERMINAL | {"processing", "awaiting_user_response", "awaiting_plan_approval"}:
                    raise RuntimeError(f"Unsupported Claude Science status: {status!r}")
                frames = {frame_id: frame}
                queue = list(frame.get("children") or [])
                while queue:
                    child = queue.pop()
                    child_id = resource_id(child.get("id"))
                    if child.get("root_frame_id") != frame_id:
                        raise RuntimeError("Claude Science returned a child from another session.")
                    if child_id not in frames:
                        frames[child_id] = child
                        queue.extend(child.get("children") or [])
                for current in frames.values():
                    if current.get("status") == "awaiting_plan_approval":
                        raise RuntimeError("Science is waiting for plan review. Resume and review the plan in its browser.")
                    pending = (current.get("output_data") or {}).get("pending_input_requests") or []
                    for item in pending:
                        target = resource_id(item.get("frameId") or current["id"])
                        if target not in frames:
                            raise RuntimeError("Claude Science requested approval for another session.")
                        key = (target, item.get("requestId") or item.get("tool_id"))
                        if key in answered or (item.get("mode") == "parked" and frames[target].get("status") != "awaiting_user_response"):
                            continue
                        await answer(client, target, item, session_id, ask_user)
                        answered.add(key)
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            if not done:
                with suppress(Exception):
                    await asyncio.wait_for(request(client, "POST", f"/api/frames/{frame_id}/cancel", json={}), 5)
                    # Never stop the shared daemon. A cancelled frame cannot accept a new
                    # message; the next run starts fresh with codesm's saved visible history.
                    stopped = await request(client, "GET", f"/api/frames/{frame_id}")
                    if stopped.get("status") in {"cancelled", "failed", "replaced"}:
                        state["previous_id"] = state.pop("id", frame_id)
                        state.pop("synced_messages", None)
                        save()
