"""ReAct loop implementation for agent execution"""

import logging
from typing import AsyncIterator
from dataclasses import dataclass
import json
import time

from codesm.provider.base import StreamChunk
from codesm.tool.registry import ToolRegistry
from codesm.session.context import ContextManager
from codesm.session.summarize import summarize_messages

logger = logging.getLogger(__name__)


@dataclass
class ReActLoop:
    """Implements the ReAct (Reasoning + Acting) loop"""
    
    max_iterations: int = 0  # 0 = unlimited
    
    async def execute(self, provider, system_prompt, messages, tools, context):
        from codesm.agent.execution import current_context, emit
        import asyncio
        token = current_context.set(context)
        context["completion_status"] = "running"
        try:
            from contextlib import aclosing
            async with aclosing(self._execute(provider, system_prompt, messages, tools, context)) as stream:
                async for chunk in stream:
                    yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            context["completion_status"] = "cancelled"
            raise
        except Exception:
            context["completion_status"] = "failed"
            raise
        finally:
            emit(context, "run_status", status=context["completion_status"])
            current_context.reset(token)

    async def _execute(
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

        def _emit(event_type: str, **fields):
            """Emit a failure mode event to the logger or the in-memory sink."""
            from codesm.agent.execution import emit
            emit(context, event_type, **fields)

        # Get or create ContextManager for compaction
        from copy import copy
        context_manager = copy(context.get("context_manager"))
        if context_manager is None:
            context_manager = ContextManager(max_tokens=context.get("context_tokens", 128000))
        # Include the system prompt and tool schemas in the effective context window.
        overhead = context_manager.estimator.estimate_text(system_prompt + json.dumps(tools.get_schemas()))
        output_limit = getattr(provider, "options", {}).get("max_output_tokens", 8192)
        if context.get("context_tokens") and overhead + output_limit >= context["context_tokens"]:
            raise ValueError("Context window cannot fit instructions, tool schemas, and output allowance; reduce enabled tools or max_output_tokens, or configure a larger context_tokens")
        context_manager.max_tokens = max(1024, context_manager.max_tokens - overhead)
        context_manager.autocompact_buffer_tokens = min(context_manager.max_tokens // 2,
            max(context_manager.autocompact_buffer_tokens, output_limit))

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
                if session is not None:
                    session.save_context(current_messages)
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
            response_metadata = {}
            tool_calls = []
            pending_tool_call = None
            
            from contextlib import aclosing
            last_checkpoint = 0.0
            try:
                async with aclosing(provider.stream(
                    system=system_prompt + "\n\n" + "\n\n".join(m.get("content", "") for m in current_messages if m.get("role") == "system"),
                    messages=[m for m in current_messages if m.get("role") != "system"],
                    tools=tools.get_schemas(),
                )) as stream:
                    async for chunk in stream:
                        if chunk.type == "text":
                            response_text += chunk.content
                            if session is not None:
                                session.pending_response = {"role": "assistant", "content": response_text,
                                                            "_interrupted": True, "model": context.get("model", "")}
                                if time.monotonic() - last_checkpoint >= 1:
                                    session.save()
                                    last_checkpoint = time.monotonic()
                            yield chunk
                        elif chunk.type == "response_items":
                            response_metadata.update(chunk.metadata)
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
            except BaseException as error:
                if session is not None:
                    session.commit_pending_response()
                    session.save()
                _emit("provider_interrupted", model=context.get("model", ""), error=str(error))
                raise

            # Add any pending tool call
            if pending_tool_call and pending_tool_call not in tool_calls:
                tool_calls.append(pending_tool_call)
            
            # Persist final text here, including child sessions when present.
            if not tool_calls:
                debug = context.get("debug_state") if not context.get("subagent") else None
                context["completion_status"] = ("verified" if debug.get("status") == "verified" else "unverified") if debug else "completed"
                if session is not None and response_text:
                    session.pending_response = {}
                    session.add_message(role="assistant", content=response_text, **response_metadata)
                yield StreamChunk(type="run_status", content=context["completion_status"], metadata={"status": context["completion_status"]})
                return
            
            ids = [call.id for call in tool_calls]
            if any(not call_id for call_id in ids) or len(set(ids)) != len(ids):
                if session is not None:
                    session.commit_pending_response()
                _emit("malformed_tool_call", iteration=iteration, reason="missing_or_duplicate_call_id")
                raise ValueError("Provider returned missing or duplicate tool call IDs; no tools were executed")
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
            assistant_msg.update(response_metadata)
            current_messages.append(assistant_msg)
            if session is not None:
                session.pending_response = {}
                session.add_message(**assistant_msg)
            
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
                        args = raw_args  # The registry rejects invalid objects without executing a tool.
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
            
            def persist_result(call_id, name, result):
                _emit("tool_result", tool=name, call_id=call_id)
                if isinstance(result, str):
                    lowered = result.strip().lower()
                    kind = "permission_denied" if lowered.startswith("permission denied") else "tool_error" if lowered.startswith("error") else None
                    if kind:
                        _emit(kind, iteration=iteration, tool=name, message=result[:500])
                if session is not None:
                    session.add_message(role="tool", tool_call_id=call_id, name=name, content=result)

            batch_context = {**context, "on_tool_result": persist_result, "messages": current_messages}
            results = await tools.execute_parallel(parsed_calls, batch_context)
            for skipped in tool_calls[MAX_PARALLEL_CALLS:]:
                result = "Error: Per-turn tool limit exceeded; this call was not executed."
                persist_result(skipped.id, skipped.name, result)
                results.append((skipped.id, skipped.name, result))

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
                if batch_context.get("_handoff_follow") and batch_context.get("_handoff_session_id"):
                    handoff_session_id = batch_context.pop("_handoff_session_id")
                    batch_context.pop("_handoff_follow", None)
                    context["completion_status"] = "handed_off"
                    yield StreamChunk(
                        type="handoff",
                        content=f"Switching to session {handoff_session_id}",
                        new_session_id=handoff_session_id,
                    )
                    return  # Stop the loop after handoff
        
        if self.max_iterations > 0 and iteration >= self.max_iterations:
            context["completion_status"] = "stopped"
            _emit("max_iterations", n=iteration)
            yield StreamChunk(
                type="text",
                content="\n\n[Maximum iterations reached - stopping]",
            )
            yield StreamChunk(type="run_status", content="stopped", metadata={"status": "stopped"})
