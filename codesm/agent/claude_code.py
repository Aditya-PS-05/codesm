"""Claude Code's supported SDK transport, using the user's installed CLI."""

import json
import shutil

from codesm.provider.base import StreamChunk
from .backends import approve, tool_call_id


async def stream(*, directory, prompt, state, save, model, read_only, session_id, ask_user, max_turns, instructions=""):
    from claude_agent_sdk import (
        ClaudeAgentOptions, ClaudeSDKClient, AssistantMessage, UserMessage,
        SystemMessage, StreamEvent, TextBlock, ToolUseBlock, ToolResultBlock,
        ResultMessage, PermissionResultAllow, PermissionResultDeny, HookMatcher,
    )

    async def permission(tool, inputs, context):
        if tool == "AskUserQuestion":
            answers = {}
            for question in inputs.get("questions", []):
                answer = await ask_user(question) if ask_user else None
                if answer is None:
                    return PermissionResultDeny(message="No answer provided. Ask the user in the conversation.")
                answers[question["question"]] = answer
            return PermissionResultAllow(updated_input={**inputs, "answers": answers})
        allowed = await approve(session_id, "claude-code", tool, inputs)
        return PermissionResultAllow() if allowed else PermissionResultDeny(message="The user denied this action.")

    async def before_tool(data, tool_use_id, context):
        if read_only and data.get("tool_name") not in {"Read", "Glob", "Grep"}:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                    "permissionDecision": "deny", "permissionDecisionReason": "This task is read-only."}}
        return {"continue_": True}

    options = ClaudeAgentOptions(
        cwd=str(directory), cli_path=shutil.which("claude"), resume=state.get("id"), model=model,
        system_prompt={"type": "preset", "preset": "claude_code", "append": instructions},
        setting_sources=["user", "project", "local"], permission_mode="default",
        can_use_tool=permission, hooks={"PreToolUse": [HookMatcher(hooks=[before_tool])]},
        include_partial_messages=True, max_turns=max_turns,
        tools=["Read", "Glob", "Grep"] if read_only else None,
        strict_mcp_config=read_only,
    )
    tool_names, streamed_ids = {}, set()
    current_message, streamed_text, any_text = "", False, False

    def remember(native_id):
        if native_id and state.get("id") != native_id:
            state["id"] = native_id
            save()

    def call_id(value):
        return tool_call_id("claude-code", state.get("id", session_id), value)

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for event in client.receive_response():
            if isinstance(event, SystemMessage):
                if event.subtype == "init" and not event.data.get("parent_tool_use_id"):
                    remember(event.data.get("session_id"))
            elif isinstance(event, StreamEvent):
                if event.parent_tool_use_id:
                    continue
                remember(event.session_id)
                wire = event.event
                if wire.get("type") == "message_start":
                    current_message = wire.get("message", {}).get("id", "")
                    streamed_text = False
                delta = wire.get("delta", {})
                if delta.get("type") == "text_delta":
                    streamed_text = any_text = True
                    streamed_ids.add(current_message)
                    yield StreamChunk(type="text", content=delta.get("text", ""))
            elif isinstance(event, (AssistantMessage, UserMessage)):
                if isinstance(event, AssistantMessage):
                    if not event.parent_tool_use_id:
                        remember(getattr(event, "session_id", None))
                    if event.error:
                        raise RuntimeError(f"Claude Code: {event.error}. Check authentication with `claude auth status`.")
                if isinstance(event.content, str):
                    continue  # User-message echoes must not become assistant output.
                for block in event.content:
                    if isinstance(block, TextBlock) and isinstance(event, AssistantMessage) and not event.parent_tool_use_id:
                        message_id = getattr(event, "message_id", None)
                        already_streamed = message_id in streamed_ids if message_id else streamed_text
                        if not already_streamed:
                            any_text = True
                            yield StreamChunk(type="text", content=block.text)
                    elif isinstance(block, ToolUseBlock):
                        name = {"Bash": "bash", "Read": "read", "Edit": "edit", "Write": "write",
                                "Glob": "glob", "Grep": "grep"}.get(block.name, block.name)
                        tool_names[block.id] = name
                        args = dict(block.input)
                        if "file_path" in args:
                            args["path"] = args["file_path"]
                        yield StreamChunk(type="tool_call", name=name, id=call_id(block.id), args=args)
                    elif isinstance(block, ToolResultBlock):
                        content = block.content if isinstance(block.content, str) else json.dumps(block.content, ensure_ascii=False)
                        if block.is_error:
                            content = "Error: " + content
                        yield StreamChunk(type="tool_result", name=tool_names.get(block.tool_use_id, "tool"),
                                          id=call_id(block.tool_use_id), content=content)
            elif isinstance(event, ResultMessage):
                remember(event.session_id)
                usage = event.usage or {}
                yield StreamChunk(type="usage", metadata={
                    "input_tokens": sum(usage.get(k, 0) or 0 for k in (
                        "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
                    "output_tokens": usage.get("output_tokens", 0) or 0,
                    "api_equivalent_cost_usd": event.total_cost_usd,
                })
                if event.is_error:
                    reason = "; ".join(getattr(event, "errors", None) or []) or event.result or event.subtype
                    raise RuntimeError(f"Claude Code: {reason}")
                if not any_text and event.result:
                    yield StreamChunk(type="text", content=event.result)
                yield StreamChunk(type="run_status", content="completed")
                return
