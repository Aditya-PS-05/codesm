"""ReAct loop implementation for agent execution"""

import logging
from typing import AsyncIterator
from dataclasses import dataclass
import json

from codesm.provider.base import StreamChunk
from codesm.tool.registry import ToolRegistry
from codesm.session.context import ContextManager
from codesm.session.summarize import summarize_messages

logger = logging.getLogger(__name__)


@dataclass
class ReActLoop:
    """Implements the ReAct (Reasoning + Acting) loop"""
    
    max_iterations: int = 0  # 0 = unlimited
    
    async def execute(
        self,
        provider,
        system_prompt: str,
        messages: list[dict],
        tools: ToolRegistry,
        context: dict,
    ) -> AsyncIterator[StreamChunk]:
        """Execute the ReAct loop with tool calling"""
        
        iteration = 0
        current_messages = list(messages)  # Copy to avoid mutating original
        session = context.get("session")

        # Optional eval instrumentation sink. When the eval runner attached a
        # list under this key, we append structured events to it. This is
        # backwards compatible: if the key is absent, nothing happens.
        eval_events = context.get("eval_events")
        event_logger = context.get("event_logger")

        def _emit(event_type: str, **fields):
            """Emit a failure mode event to the logger or the in-memory sink."""
            if event_logger is not None:
                event_logger.emit(event_type, **fields)
            if isinstance(eval_events, list):
                record = {"type": event_type}
                record.update(fields)
                eval_events.append(record)

        # Get or create ContextManager for compaction
        context_manager = context.get("context_manager")
        if context_manager is None:
            context_manager = ContextManager()

        while self.max_iterations == 0 or iteration < self.max_iterations:
            iteration += 1

            _emit("iteration_start", n=iteration)

            # Compact context if needed
            if context_manager.should_compact(current_messages):
                tokens_before = context_manager.estimate_tokens(current_messages)

                async def summarizer(msgs):
                    return await summarize_messages(msgs)

                current_messages = await context_manager.compact_messages_async(
                    current_messages,
                    summarizer=summarizer,
                )
                tokens_after = context_manager.estimate_tokens(current_messages)
                logger.info(f"Compacted context from {tokens_before} to {tokens_after} tokens")

                _emit(
                    "compaction",
                    iteration=iteration,
                    tokens_before=tokens_before,
                    tokens_after=tokens_after,
                    tokens_dropped=max(0, tokens_before - tokens_after),
                )
            
            # Get response from LLM
            response_text = ""
            tool_calls = []
            pending_tool_call = None
            
            async for chunk in provider.stream(
                system=system_prompt,
                messages=current_messages,
                tools=tools.get_schemas(),
            ):
                if chunk.type == "text":
                    response_text += chunk.content
                    yield chunk
                elif chunk.type == "tool_call":
                    tool_calls.append(chunk)
                    yield chunk
                elif chunk.type == "tool_call_delta":
                    # Handle streaming tool call arguments
                    if pending_tool_call is None:
                        pending_tool_call = chunk
                    else:
                        # Accumulate args
                        if chunk.args:
                            pending_tool_call.args.update(chunk.args)
            
            # Add any pending tool call
            if pending_tool_call and pending_tool_call not in tool_calls:
                tool_calls.append(pending_tool_call)
            
            # If no tool calls, we're done
            if not tool_calls:
                break
            
            # Add assistant message with tool calls to history
            assistant_msg = {"role": "assistant", "content": response_text or ""}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.args) if isinstance(tc.args, dict) else tc.args,
                        }
                    }
                    for tc in tool_calls
                ]
            current_messages.append(assistant_msg)
            
            # Execute tool calls in parallel (limit to avoid API errors)
            MAX_PARALLEL_CALLS = 64  # API limit is 128, stay well under
            
            parsed_calls = []
            for tool_call in tool_calls[:MAX_PARALLEL_CALLS]:  # Cap the number
                args = tool_call.args
                if isinstance(args, str):
                    raw_args = args
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError as e:
                        args = {}
                        _emit(
                            "malformed_tool_call",
                            iteration=iteration,
                            tool=tool_call.name,
                            reason=f"json_decode_error: {e}",
                            raw=raw_args[:300],
                        )
                if tools.get(tool_call.name) is None:
                    # Unknown tool: the registry will still return an error
                    # string, but we emit a distinct event so evals can
                    # distinguish name hallucination from execution errors.
                    _emit(
                        "malformed_tool_call",
                        iteration=iteration,
                        tool=tool_call.name,
                        reason="unknown_tool_name",
                        raw="",
                    )
                parsed_calls.append((tool_call.id, tool_call.name, args))
            
            if len(tool_calls) > MAX_PARALLEL_CALLS:
                # Log that we're dropping some calls
                dropped = len(tool_calls) - MAX_PARALLEL_CALLS
                parsed_calls.append((
                    f"dropped_{dropped}",
                    "_system",
                    {"message": f"Warning: {dropped} tool calls dropped (max {MAX_PARALLEL_CALLS} per turn)"}
                ))
            
            # Execute all tools in parallel
            results = await tools.execute_parallel(parsed_calls, context)

            # Inspect results for failure mode signals. Tool results are
            # strings; the tool layer encodes permission denials with a
            # "Permission denied:" prefix, path/command blocks with
            # "Error: <reason>", and generic failures by starting the
            # result with "Error". We emit distinct events per category.
            for call_id, name, result in results:
                if not isinstance(result, str):
                    continue
                stripped = result.strip()
                lowered = stripped.lower()
                if lowered.startswith("permission denied"):
                    _emit(
                        "permission_denied",
                        iteration=iteration,
                        tool=name,
                        message=stripped[:300],
                    )
                elif lowered.startswith("error"):
                    _emit(
                        "tool_error",
                        iteration=iteration,
                        tool=name,
                        message=stripped[:500],
                    )

            # Process results in order
            for call_id, name, result in results:
                # Add tool result to messages (for this turn only, not persisted)
                tool_result_msg = {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result,
                }
                current_messages.append(tool_result_msg)
                
                # Yield tool result as a chunk
                yield StreamChunk(
                    type="tool_result",
                    content=result,
                    id=call_id,
                    name=name,
                )
                
                # Check if handoff was triggered
                if context.get("_handoff_follow") and context.get("_handoff_session_id"):
                    handoff_session_id = context.pop("_handoff_session_id")
                    context.pop("_handoff_follow", None)
                    yield StreamChunk(
                        type="handoff",
                        content=f"Switching to session {handoff_session_id}",
                        new_session_id=handoff_session_id,
                    )
                    return  # Stop the loop after handoff
        
        if self.max_iterations > 0 and iteration >= self.max_iterations:
            _emit("max_iterations", n=iteration)
            yield StreamChunk(
                type="text",
                content="\n\n[Maximum iterations reached - stopping]",
            )
