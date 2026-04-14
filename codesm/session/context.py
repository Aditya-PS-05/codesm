"""Context management and compaction for long conversations"""

from __future__ import annotations

import json
from typing import Callable, Any

# Try to use tiktoken for accurate token counting
try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
    _ENCODING = tiktoken.get_encoding("cl100k_base")
except ImportError:
    _TIKTOKEN_AVAILABLE = False
    _ENCODING = None


class TokenEstimator:
    """Estimates token counts for messages"""

    def __init__(self):
        self.use_tiktoken = _TIKTOKEN_AVAILABLE
        self._encoding = _ENCODING

    def estimate_text(self, text: str) -> int:
        """Estimate tokens for a text string"""
        if not text:
            return 0
        if self.use_tiktoken and self._encoding:
            return len(self._encoding.encode(text))
        # Heuristic fallback: words * 1.3
        return int(len(text.split()) * 1.3)

    def estimate_message(self, msg: dict) -> int:
        """Estimate tokens for a single message dict"""
        if not msg:
            return 0

        tokens = 6  # Base overhead per message (role, formatting, etc.)

        # Handle content
        content = msg.get("content")
        if content:
            if isinstance(content, str):
                tokens += self.estimate_text(content)
            elif isinstance(content, list):
                # Multi-part content (e.g., with images)
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            tokens += self.estimate_text(part.get("text", ""))
                        elif part.get("type") == "image_url":
                            tokens += 85  # Base image token estimate
                    elif isinstance(part, str):
                        tokens += self.estimate_text(part)

        # Handle tool_calls
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                if isinstance(tc, dict):
                    func = tc.get("function", {})
                    tokens += self.estimate_text(func.get("name", ""))
                    args = func.get("arguments", "")
                    if isinstance(args, str):
                        tokens += self.estimate_text(args)
                    else:
                        tokens += self.estimate_text(json.dumps(args))
                    tokens += 10  # Overhead for tool_call structure

        # Handle tool_call_id (tool responses)
        if msg.get("tool_call_id"):
            tokens += 10

        # Handle name field
        if msg.get("name"):
            tokens += self.estimate_text(msg.get("name", ""))

        return tokens

    def estimate_messages(self, messages: list[dict]) -> int:
        """Estimate total tokens for a list of messages"""
        if not messages:
            return 0
        return sum(self.estimate_message(msg) for msg in messages)


#: Tokens held back from the context window to leave headroom for the
#: summarizer's own output. A conversation that grows into this buffer zone
#: triggers autocompact. 13k matches Claude Code's AUTOCOMPACT_BUFFER_TOKENS
#: — generous enough that a large summary fits even on tight windows.
AUTOCOMPACT_BUFFER_TOKENS = 13_000

#: Max retries for the summarizer before the circuit breaker trips. Each
#: retry drops an additional 20% of the oldest middle-section messages, so
#: three attempts shed up to ~50% of the middle before giving up.
MAX_SUMMARIZER_RETRIES = 3


class ContextManager:
    """Manages conversation context and handles context window limits"""

    def __init__(
        self,
        max_tokens: int = 128000,
        compact_trigger_ratio: float | None = None,
        recent_budget_ratio: float = 0.4,
        summary_budget_tokens: int = 1500,
        min_messages_to_summarize: int = 5,
        autocompact_buffer_tokens: int = AUTOCOMPACT_BUFFER_TOKENS,
    ):
        self.max_tokens = max_tokens
        # compact_trigger_ratio is now a testing override. The primary
        # threshold is buffer-based: compact when headroom drops below
        # autocompact_buffer_tokens. A conservative ratio can still force
        # earlier compaction for tests that need to trip the threshold
        # with small synthetic conversations.
        self.compact_trigger_ratio = compact_trigger_ratio
        self.recent_budget_ratio = recent_budget_ratio
        self.summary_budget_tokens = summary_budget_tokens
        self.min_messages_to_summarize = min_messages_to_summarize
        self.autocompact_buffer_tokens = autocompact_buffer_tokens
        self.estimator = TokenEstimator()

        # Consecutive-failure counter for the summarizer circuit breaker.
        # Reset on any successful compaction. Once it hits
        # MAX_SUMMARIZER_RETRIES we stop trying to summarize and just trim
        # — the summarizer model is clearly unable to handle the middle
        # section for this session and retrying wastes API calls.
        self._consecutive_summarizer_failures = 0

    def _compact_threshold(self) -> int:
        """Token count above which compaction should trigger.

        Buffer-based by default. The optional ratio override lowers the
        threshold further when set — it never raises it. This matches
        Claude Code's CLAUDE_AUTOCOMPACT_PCT_OVERRIDE semantic: env/test
        overrides can make compaction fire earlier but can't delay it
        past the principled buffer-based point.
        """
        buffer_threshold = max(0, self.max_tokens - self.autocompact_buffer_tokens)
        if self.compact_trigger_ratio and self.compact_trigger_ratio > 0:
            ratio_threshold = int(self.max_tokens * self.compact_trigger_ratio)
            return min(buffer_threshold, ratio_threshold)
        return buffer_threshold

    def should_compact(self, messages: list[dict]) -> bool:
        """Check if messages should be compacted based on token estimation"""
        if not messages:
            return False
        estimated_tokens = self.estimator.estimate_messages(messages)
        return estimated_tokens > self._compact_threshold()

    def prune_tool_outputs(
        self,
        messages: list[dict],
        keep_recent: int = 4,
        max_output_chars: int = 4000,
    ) -> list[dict]:
        """
        Prune large tool outputs from older messages.
        
        - Keep the most recent `keep_recent` tool/tool_display messages unmodified
        - For older ones, if content > max_output_chars, replace with "[OUTPUT PRUNED: N chars]"
        - Keep message structure intact (tool_call_id, role, etc.)
        """
        if not messages:
            return []

        result = []
        
        # Find indices of tool response messages (role == "tool")
        tool_indices = [
            i for i, msg in enumerate(messages)
            if msg.get("role") == "tool"
        ]
        
        # Determine which tool messages to keep unmodified
        recent_tool_indices = set(tool_indices[-keep_recent:]) if tool_indices else set()

        for i, msg in enumerate(messages):
            if msg.get("role") != "tool":
                result.append(msg)
                continue

            # Tool message - check if it should be pruned
            if i in recent_tool_indices:
                result.append(msg)
                continue

            # Older tool message - check content size
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > max_output_chars:
                pruned_msg = msg.copy()
                pruned_msg["content"] = f"[OUTPUT PRUNED: {len(content)} chars]"
                result.append(pruned_msg)
            else:
                result.append(msg)

        return result

    def _extract_sections(
        self, messages: list[dict]
    ) -> tuple[list[dict], dict | None, list[dict]]:
        """
        Extract system messages, existing summary, and conversation messages.
        
        Returns:
            (system_messages, existing_summary, conversation_messages)
        """
        system_messages = []
        existing_summary = None
        conversation = []

        for msg in messages:
            if msg.get("role") == "system":
                if msg.get("_context_summary"):
                    existing_summary = msg
                elif msg.get("_touched_files_hint"):
                    # Drop stale hints — a fresh hint is rebuilt from the
                    # new middle section during compaction. Without this
                    # the hints pile up across every compaction generation.
                    continue
                else:
                    system_messages.append(msg)
            else:
                conversation.append(msg)

        return system_messages, existing_summary, conversation

    def _select_recent_messages(
        self, messages: list[dict], budget_tokens: int
    ) -> tuple[list[dict], list[dict]]:
        """
        Walk backward through messages to select recent ones within budget,
        then repair tool_use/tool_result pair integrity.

        A tool-role message's tool_call_id references a tool_call emitted by
        an earlier assistant message. If the backward walk stops between a
        tool result and its owning assistant, the continuing conversation
        will fail at the API boundary ("no tool_use block matching
        tool_use_id"). Symmetric problem: a kept assistant message whose
        tool_calls reference tool results that fell into the middle leaves
        orphan tool_use blocks with no matching tool_result.

        We fix both directions:
          Pass 1 — backward walk by token budget.
          Pass 2 — pull in any assistant message whose tool_calls own a
                   tool_call_id referenced by a kept tool-role message.
          Pass 3 — for each kept assistant, drop tool_calls whose ids
                   don't have matching kept tool-role responses. Filtering
                   tool_calls is safer than dropping the whole turn.

        Returns:
            (middle_messages, recent_messages)
        """
        if not messages:
            return [], []

        # Pass 1: backward walk by token budget.
        recent_indices: set[int] = set()
        tokens_used = 0
        for i in range(len(messages) - 1, -1, -1):
            msg_tokens = self.estimator.estimate_message(messages[i])
            if tokens_used + msg_tokens <= budget_tokens:
                recent_indices.add(i)
                tokens_used += msg_tokens
            else:
                break

        # Pass 2: pull in missing assistant owners.
        needed_call_ids: set[str] = set()
        for i in recent_indices:
            msg = messages[i]
            if msg.get("role") == "tool":
                call_id = msg.get("tool_call_id")
                if call_id:
                    needed_call_ids.add(call_id)

        if needed_call_ids:
            for i in range(len(messages) - 1, -1, -1):
                if i in recent_indices:
                    continue
                msg = messages[i]
                if msg.get("role") != "assistant":
                    continue
                tool_calls = msg.get("tool_calls") or []
                if any(tc.get("id") in needed_call_ids for tc in tool_calls):
                    recent_indices.add(i)

        # Pass 3: rebuild the kept tool_call_id set (the owner pull-in may
        # have added an assistant whose OTHER tool_calls still lack results,
        # but those will be filtered below — they weren't in Pass 1's set).
        kept_tool_call_ids: set[str] = set()
        for i in recent_indices:
            msg = messages[i]
            if msg.get("role") == "tool":
                call_id = msg.get("tool_call_id")
                if call_id:
                    kept_tool_call_ids.add(call_id)

        recent: list[dict] = []
        for i in sorted(recent_indices):
            msg = messages[i]
            tool_calls = msg.get("tool_calls")
            if msg.get("role") == "assistant" and tool_calls:
                filtered = [
                    tc for tc in tool_calls
                    if tc.get("id") in kept_tool_call_ids
                ]
                if len(filtered) != len(tool_calls):
                    msg_copy = dict(msg)
                    if filtered:
                        msg_copy["tool_calls"] = filtered
                    else:
                        msg_copy.pop("tool_calls", None)
                    recent.append(msg_copy)
                    continue
            recent.append(msg)

        middle = [
            messages[i] for i in range(len(messages)) if i not in recent_indices
        ]
        return middle, recent

    def _create_summary_message(self, summary_text: str) -> dict:
        """Create a summary message in the standard format"""
        return {
            "role": "system",
            "content": f"## Previous Conversation Summary\n\n{summary_text}",
            "_context_summary": True,
        }

    # Tool argument keys that reliably point at a file the model has touched.
    # Excludes directory-level search tools (grep/glob path is often ".") and
    # bulk selectors ("files") that aren't per-call identifiers — those produce
    # noisy hints. Ordered by prevalence in the codesm tool set.
    _PATH_ARG_KEYS = ("path", "file_path", "file")

    # Per-file path length cap: prevents a single pathological argument from
    # ballooning the hint message. A 512-char path covers every reasonable
    # absolute path on Linux while rejecting obvious garbage.
    _MAX_PATH_HINT_LENGTH = 512

    def _extract_touched_files(self, messages: list[dict]) -> list[str]:
        """Walk assistant tool_calls and return file paths in first-seen order.

        The point of re-injecting this list after compaction is to stop the
        model from re-Reading files it already knows about but has forgotten
        now that the middle section was summarized away. We only scan the
        *middle* (messages about to be dropped) so that we don't re-hint
        files already visible in the kept recent window.

        Returns paths in first-seen order so the earliest file surfaces
        first in the hint — that roughly mirrors the reading order the
        model saw originally.
        """
        seen: dict[str, None] = {}  # preserves insertion order, dedupes

        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                func = tc.get("function") or {}
                raw_args = func.get("arguments", "")
                args: dict
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        continue
                elif isinstance(raw_args, dict):
                    args = raw_args
                else:
                    continue

                for key in self._PATH_ARG_KEYS:
                    value = args.get(key)
                    if not isinstance(value, str):
                        continue
                    path = value.strip()
                    # Skip cwd sentinels and empty strings — these come from
                    # grep/ls on "the current dir" and aren't useful hints.
                    if not path or path in (".", "./"):
                        continue
                    if len(path) > self._MAX_PATH_HINT_LENGTH:
                        continue
                    if path not in seen:
                        seen[path] = None
                    break  # one path per tool call is enough

        return list(seen.keys())

    def _create_touched_files_message(self, paths: list[str]) -> dict:
        """Build a system message listing files the model touched pre-compact.

        Hint framing (not an imperative) so the model is free to re-read when
        it genuinely needs to, but doesn't re-Read by reflex to rebuild
        mental model after losing the middle section.
        """
        lines = [f"- {p}" for p in paths]
        body = "\n".join(lines)
        return {
            "role": "system",
            "content": (
                "## Files Touched Earlier In This Session\n\n"
                "You previously read, wrote, or edited these files during the "
                "summarized portion of the conversation. Assume you still know "
                "what's in them — don't re-read unless something in the recent "
                "messages suggests they've changed:\n\n"
                f"{body}"
            ),
            "_touched_files_hint": True,
        }

    async def compact_messages_async(
        self,
        messages: list[dict],
        summarizer: Callable[[list[dict]], Any] | None = None,
    ) -> list[dict]:
        """
        Compact messages asynchronously, optionally using an LLM summarizer.
        
        If summarizer is provided, it will be called with the middle section
        and should return a summary string.
        """
        if not messages:
            return []

        if not self.should_compact(messages):
            return messages

        # Prune tool outputs first
        messages = self.prune_tool_outputs(messages)

        # Extract sections
        system_messages, existing_summary, conversation = self._extract_sections(messages)

        # Calculate recent budget
        recent_budget = int(self.max_tokens * self.recent_budget_ratio)

        # Select recent messages
        middle, recent = self._select_recent_messages(conversation, recent_budget)

        # Check if we have enough middle content to summarize
        if len(middle) < self.min_messages_to_summarize:
            # Not enough to summarize, just return pruned messages
            result = system_messages[:]
            if existing_summary:
                result.append(existing_summary)
            result.extend(conversation)
            return result

        # Build result
        result = system_messages[:]

        # Handle summarization with retry ladder + circuit breaker.
        # Middle section may be too big for the summarizer model's own
        # context window. Rather than eating the failure silently, retry
        # with a progressively smaller middle (drop oldest 20% each
        # attempt). After MAX_SUMMARIZER_RETRIES consecutive session-wide
        # failures, the circuit breaker trips and we stop trying entirely
        # — the summarizer is clearly unable to handle this conversation
        # and every retry wastes an API call.
        summary_landed = False
        circuit_broken = (
            self._consecutive_summarizer_failures >= MAX_SUMMARIZER_RETRIES
        )

        if summarizer is not None and not circuit_broken:
            current_middle = list(middle)
            summary_text: str | None = None
            for _attempt in range(MAX_SUMMARIZER_RETRIES):
                if len(current_middle) < self.min_messages_to_summarize:
                    # Nothing left to summarize — further retries would be
                    # pointless. Leave summary_text=None to fall through.
                    break

                summary_context = []
                if existing_summary:
                    summary_context.append(existing_summary)
                summary_context.extend(current_middle)

                try:
                    summary_result = summarizer(summary_context)
                    if hasattr(summary_result, "__await__"):
                        candidate = await summary_result
                    else:
                        candidate = summary_result

                    if candidate:
                        summary_text = candidate
                        break
                except Exception:
                    pass  # Fall through to the shrink-and-retry step.

                # Drop the oldest 20% and try again. Oldest-first because
                # newer messages are more likely to be load-bearing for the
                # next turn, and PTL is driven by total volume rather than
                # location, so trimming the tail would cost more utility.
                drop_count = max(1, len(current_middle) // 5)
                current_middle = current_middle[drop_count:]

            if summary_text:
                result.append(self._create_summary_message(summary_text))
                summary_landed = True
                self._consecutive_summarizer_failures = 0
            else:
                self._consecutive_summarizer_failures += 1
                # Preserve the pre-existing summary if one was in place so
                # we don't lose history just because this attempt failed.
                if existing_summary:
                    result.append(existing_summary)
        else:
            # No summarizer (or circuit breaker tripped) — keep the
            # existing summary if any so re-compaction can still proceed.
            if existing_summary:
                result.append(existing_summary)

        # Re-inject files the model touched in the middle section. The
        # summary covers intent and state at a high level, but model output
        # post-compaction frequently re-Reads the same files to rebuild
        # mental model. Listing them as a hint kills that reflex. Only run
        # when the summary actually landed — otherwise we haven't dropped
        # anything and the hint would be redundant.
        if summary_landed:
            touched = self._extract_touched_files(middle)
            if touched:
                result.append(self._create_touched_files_message(touched))

        # Add recent messages
        result.extend(recent)

        return result

    def compact_messages(self, messages: list[dict]) -> list[dict]:
        """
        Compact messages synchronously (no LLM summary, just prune + select).
        """
        if not messages:
            return []

        if not self.should_compact(messages):
            return messages

        # Prune tool outputs
        messages = self.prune_tool_outputs(messages)

        # Extract sections
        system_messages, existing_summary, conversation = self._extract_sections(messages)

        # Calculate recent budget
        recent_budget = int(self.max_tokens * self.recent_budget_ratio)

        # Select recent messages
        middle, recent = self._select_recent_messages(conversation, recent_budget)

        # Build result
        result = system_messages[:]

        # Keep existing summary
        if existing_summary:
            result.append(existing_summary)

        # Add recent messages
        result.extend(recent)

        return result

    def estimate_tokens(self, messages: list[dict]) -> int:
        """Estimate token count for messages"""
        return self.estimator.estimate_messages(messages)
