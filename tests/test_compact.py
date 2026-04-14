"""Tests for compaction: summarizer formatting and context manager"""


class TestFormatCompactSummary:
    def test_strips_analysis_block(self):
        from codesm.session.summarize import format_compact_summary

        raw = (
            "<analysis>scratchpad thinking that should be dropped</analysis>\n"
            "<summary>1. Primary Request:\n   build a thing</summary>"
        )
        out = format_compact_summary(raw)
        assert "scratchpad" not in out
        assert "Primary Request" in out
        assert "build a thing" in out

    def test_strips_summary_wrapper(self):
        from codesm.session.summarize import format_compact_summary

        raw = "<summary>hello world</summary>"
        out = format_compact_summary(raw)
        assert out == "hello world"
        assert "<summary>" not in out

    def test_handles_missing_tags_as_passthrough(self):
        from codesm.session.summarize import format_compact_summary

        raw = "just a plain summary with no tags"
        out = format_compact_summary(raw)
        assert out == raw

    def test_handles_empty_input(self):
        from codesm.session.summarize import format_compact_summary

        assert format_compact_summary("") == ""
        assert format_compact_summary(None) == ""  # type: ignore[arg-type]

    def test_collapses_extra_blank_lines(self):
        from codesm.session.summarize import format_compact_summary

        raw = "<summary>line1\n\n\n\nline2</summary>"
        out = format_compact_summary(raw)
        assert out == "line1\n\nline2"

    def test_create_summary_message_strips_analysis(self):
        from codesm.session.summarize import create_summary_message

        raw = "<analysis>drop me</analysis><summary>keep me</summary>"
        msg = create_summary_message(raw)
        assert msg["role"] == "system"
        assert msg["_context_summary"] is True
        assert "drop me" not in msg["content"]
        assert "keep me" in msg["content"]

    def test_summary_prompt_has_nine_sections_and_no_tools_gate(self):
        from codesm.session.summarize import SUMMARY_SYSTEM_PROMPT

        assert "TEXT ONLY" in SUMMARY_SYSTEM_PROMPT
        assert "REMINDER" in SUMMARY_SYSTEM_PROMPT
        for section in [
            "1. Primary Request and Intent",
            "2. Key Technical Concepts",
            "3. Files and Code Sections",
            "4. Errors and Fixes",
            "5. Problem Solving",
            "6. All User Messages",
            "7. Pending Tasks",
            "8. Current Work",
            "9. Optional Next Step",
        ]:
            assert section in SUMMARY_SYSTEM_PROMPT, f"missing: {section}"


def _assistant_with_tool_call(call_id: str, name: str = "Read") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
        ],
    }


def _tool_result(call_id: str, content: str = "ok") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


class TestSelectRecentMessagesPairIntegrity:
    """Pair integrity: a kept tool-result must have its owning assistant kept,
    and a kept assistant's tool_calls must all have matching kept results.
    Otherwise the next API call 400s on orphaned tool_use/tool_result blocks.
    """

    def test_pulls_in_owning_assistant_across_budget_cut(self):
        """Budget cut between assistant tool_call and its tool_result: the
        Pass 2 pull-in must drag the assistant into the recent set."""
        from codesm.session.context import ContextManager

        cm = ContextManager(max_tokens=10_000)
        messages = [
            {"role": "user", "content": "old filler " * 200},
            _assistant_with_tool_call("call_A"),
            _tool_result("call_A", "big result " * 200),
            {"role": "user", "content": "latest short message"},
        ]
        # Tight budget: only fits the last two messages naturally.
        _, recent = cm._select_recent_messages(messages, budget_tokens=80)

        has_tool = any(m.get("role") == "tool" for m in recent)
        has_owner = any(
            m.get("role") == "assistant"
            and any(tc.get("id") == "call_A" for tc in (m.get("tool_calls") or []))
            for m in recent
        )
        if has_tool:
            assert has_owner, "tool_result kept without its owning assistant"

    def test_filters_orphan_tool_calls_from_kept_assistant(self):
        """Assistant has two tool_calls; only one tool_result is in the
        budget window. Pass 3 must filter the orphan tool_call."""
        from codesm.session.context import ContextManager

        cm = ContextManager(max_tokens=10_000)
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_A",
                        "type": "function",
                        "function": {"name": "Read", "arguments": "{}"},
                    },
                    {
                        "id": "call_B",
                        "type": "function",
                        "function": {"name": "Read", "arguments": "{}"},
                    },
                ],
            },
            _tool_result("call_A", "small"),
            {"role": "tool", "tool_call_id": "call_B", "content": "X" * 10_000},
            {"role": "user", "content": "next"},
        ]
        _, recent = cm._select_recent_messages(messages, budget_tokens=60)

        assistants = [m for m in recent if m.get("role") == "assistant"]
        for a in assistants:
            for tc in a.get("tool_calls") or []:
                # every surviving tool_call must have a matching result kept
                matching = any(
                    r.get("role") == "tool" and r.get("tool_call_id") == tc.get("id")
                    for r in recent
                )
                assert matching, f"orphan tool_call {tc.get('id')} left in recent"

    def test_consistent_window_unchanged(self):
        """No orphans in the backward-walk window -> behavior matches the
        pre-fix version (everything in order, no mutation)."""
        from codesm.session.context import ContextManager

        cm = ContextManager(max_tokens=10_000)
        messages = [
            {"role": "user", "content": "hi"},
            _assistant_with_tool_call("call_1"),
            _tool_result("call_1", "result"),
            {"role": "assistant", "content": "done"},
        ]
        middle, recent = cm._select_recent_messages(messages, budget_tokens=10_000)
        assert middle == []
        assert recent == messages


def _assistant_calling(tool_name: str, args: dict, call_id: str = "c1") -> dict:
    import json as _json
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": tool_name, "arguments": _json.dumps(args)},
            }
        ],
    }


class TestTouchedFilesExtraction:
    def test_extracts_path_arg_from_read_write_edit(self):
        from codesm.session.context import ContextManager

        cm = ContextManager()
        middle = [
            _assistant_calling("read", {"path": "/repo/a.py"}, "c1"),
            _tool_result("c1"),
            _assistant_calling("edit", {"path": "/repo/b.py"}, "c2"),
            _tool_result("c2"),
            _assistant_calling("write", {"path": "/repo/c.py"}, "c3"),
            _tool_result("c3"),
        ]
        touched = cm._extract_touched_files(middle)
        assert touched == ["/repo/a.py", "/repo/b.py", "/repo/c.py"]

    def test_dedupes_preserving_first_seen_order(self):
        from codesm.session.context import ContextManager

        cm = ContextManager()
        middle = [
            _assistant_calling("read", {"path": "/repo/a.py"}, "c1"),
            _tool_result("c1"),
            _assistant_calling("read", {"path": "/repo/b.py"}, "c2"),
            _tool_result("c2"),
            _assistant_calling("read", {"path": "/repo/a.py"}, "c3"),
            _tool_result("c3"),
        ]
        touched = cm._extract_touched_files(middle)
        assert touched == ["/repo/a.py", "/repo/b.py"]

    def test_skips_cwd_sentinel_and_empty(self):
        """grep/ls with path='.' are noise — not useful hints."""
        from codesm.session.context import ContextManager

        cm = ContextManager()
        middle = [
            _assistant_calling("grep", {"path": ".", "pattern": "foo"}, "c1"),
            _tool_result("c1"),
            _assistant_calling("ls", {"path": ""}, "c2"),
            _tool_result("c2"),
            _assistant_calling("read", {"path": "/repo/real.py"}, "c3"),
            _tool_result("c3"),
        ]
        touched = cm._extract_touched_files(middle)
        assert touched == ["/repo/real.py"]

    def test_skips_malformed_json_arguments(self):
        """Malformed tool_calls shouldn't crash extraction."""
        from codesm.session.context import ContextManager

        cm = ContextManager()
        middle = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{not json"},
                    }
                ],
            },
            _assistant_calling("read", {"path": "/repo/ok.py"}, "c2"),
        ]
        touched = cm._extract_touched_files(middle)
        assert touched == ["/repo/ok.py"]

    def test_handles_dict_arguments_not_just_json_strings(self):
        """Some code paths keep arguments as a dict rather than a JSON string."""
        from codesm.session.context import ContextManager

        cm = ContextManager()
        middle = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "read",
                            "arguments": {"path": "/repo/dict.py"},
                        },
                    }
                ],
            },
        ]
        touched = cm._extract_touched_files(middle)
        assert touched == ["/repo/dict.py"]


class TestCompactReinjectsTouchedFiles:
    async def test_hint_is_appended_after_summary(self):
        from codesm.session.context import ContextManager

        # Small max_tokens so compaction triggers; tight recent budget so
        # the middle section is non-empty.
        cm = ContextManager(
            max_tokens=500,
            compact_trigger_ratio=0.1,
            recent_budget_ratio=0.1,
            min_messages_to_summarize=2,
        )

        # Middle has real file touches
        middle_messages = []
        for i, p in enumerate(["/repo/a.py", "/repo/b.py"]):
            middle_messages.append(_assistant_calling("read", {"path": p}, f"c{i}"))
            middle_messages.append(_tool_result(f"c{i}", "content " * 100))

        messages = [
            {"role": "system", "content": "sys prompt"},
            {"role": "user", "content": "early task"},
            *middle_messages,
            {"role": "user", "content": "final"},
        ]

        async def fake_summarizer(_msgs):
            return "<summary>all good</summary>"

        result = await cm.compact_messages_async(messages, summarizer=fake_summarizer)

        hint_msgs = [m for m in result if m.get("_touched_files_hint")]
        assert len(hint_msgs) == 1, "expected exactly one touched-files hint"
        hint = hint_msgs[0]
        assert "/repo/a.py" in hint["content"]
        assert "/repo/b.py" in hint["content"]
        # hint should sit AFTER the summary, BEFORE recent conversation
        summary_idx = next(i for i, m in enumerate(result) if m.get("_context_summary"))
        hint_idx = next(i for i, m in enumerate(result) if m.get("_touched_files_hint"))
        assert hint_idx == summary_idx + 1

    async def test_stale_hint_is_replaced_on_recompact(self):
        """Re-compaction should drop the old hint, not pile on a new one."""
        from codesm.session.context import ContextManager

        cm = ContextManager(
            max_tokens=500,
            compact_trigger_ratio=0.1,
            recent_budget_ratio=0.1,
            min_messages_to_summarize=2,
        )

        stale_hint = {
            "role": "system",
            "content": "## Files Touched Earlier\n- /old/stale.py",
            "_touched_files_hint": True,
        }
        messages = [
            {"role": "system", "content": "sys"},
            stale_hint,
            _assistant_calling("read", {"path": "/repo/new.py"}, "c1"),
            _tool_result("c1", "x " * 100),
            _assistant_calling("read", {"path": "/repo/new2.py"}, "c2"),
            _tool_result("c2", "x " * 100),
            {"role": "user", "content": "go"},
        ]

        async def fake_summarizer(_msgs):
            return "ok"

        result = await cm.compact_messages_async(messages, summarizer=fake_summarizer)
        hints = [m for m in result if m.get("_touched_files_hint")]
        assert len(hints) == 1
        assert "/old/stale.py" not in hints[0]["content"]


class TestBufferTokenThreshold:
    """_compact_threshold uses max_tokens - autocompact_buffer_tokens by
    default; the ratio override can only lower the threshold further."""

    def test_default_threshold_is_buffer_based(self):
        from codesm.session.context import ContextManager, AUTOCOMPACT_BUFFER_TOKENS

        cm = ContextManager(max_tokens=128_000)
        assert cm._compact_threshold() == 128_000 - AUTOCOMPACT_BUFFER_TOKENS

    def test_ratio_override_lowers_threshold(self):
        from codesm.session.context import ContextManager

        cm = ContextManager(max_tokens=128_000, compact_trigger_ratio=0.5)
        # ratio lowers below buffer threshold
        assert cm._compact_threshold() == 64_000

    def test_ratio_override_cannot_raise_threshold(self):
        """A lax ratio can't delay compaction past the principled buffer."""
        from codesm.session.context import ContextManager, AUTOCOMPACT_BUFFER_TOKENS

        cm = ContextManager(max_tokens=128_000, compact_trigger_ratio=0.99)
        # 0.99 * 128_000 = 126_720, but buffer caps at 115_000
        assert cm._compact_threshold() == 128_000 - AUTOCOMPACT_BUFFER_TOKENS

    def test_negative_threshold_clamped_to_zero(self):
        """Very small max_tokens can't produce a negative threshold."""
        from codesm.session.context import ContextManager

        cm = ContextManager(max_tokens=100, autocompact_buffer_tokens=1_000)
        assert cm._compact_threshold() == 0


class TestSummarizerRetryLadder:
    async def test_succeeds_after_drop_oldest_retry(self):
        """First call fails (simulated PTL), second call on the trimmed
        middle succeeds. Expect a summary to land and the failure counter
        to reset."""
        from codesm.session.context import ContextManager

        cm = ContextManager(
            max_tokens=500,
            compact_trigger_ratio=0.1,
            recent_budget_ratio=0.1,
            min_messages_to_summarize=2,
        )

        call_count = {"n": 0}

        async def flaky_summarizer(msgs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("prompt_too_long")
            return "retry success"

        messages = [{"role": "user", "content": "old " * 50} for _ in range(10)]
        messages.append({"role": "user", "content": "tail"})

        result = await cm.compact_messages_async(messages, summarizer=flaky_summarizer)
        assert call_count["n"] >= 2  # initial + at least one retry
        assert any(m.get("_context_summary") for m in result)
        assert cm._consecutive_summarizer_failures == 0

    async def test_all_retries_fail_increments_counter(self):
        from codesm.session.context import ContextManager

        cm = ContextManager(
            max_tokens=500,
            compact_trigger_ratio=0.1,
            recent_budget_ratio=0.1,
            min_messages_to_summarize=2,
        )

        call_count = {"n": 0}

        async def always_fail(msgs):
            call_count["n"] += 1
            raise RuntimeError("prompt_too_long")

        messages = [{"role": "user", "content": "old " * 50} for _ in range(10)]
        messages.append({"role": "user", "content": "tail"})

        result = await cm.compact_messages_async(messages, summarizer=always_fail)
        # No summary landed — no _context_summary in result
        assert not any(m.get("_context_summary") for m in result)
        assert cm._consecutive_summarizer_failures == 1
        assert call_count["n"] >= 1

    async def test_circuit_breaker_trips_after_three_failures(self):
        """Once the counter hits MAX_SUMMARIZER_RETRIES, subsequent
        compactions skip the summarizer entirely — we don't keep burning
        API calls on a session whose middle section the summarizer can't
        handle."""
        from codesm.session.context import ContextManager, MAX_SUMMARIZER_RETRIES

        cm = ContextManager(
            max_tokens=500,
            compact_trigger_ratio=0.1,
            recent_budget_ratio=0.1,
            min_messages_to_summarize=2,
        )
        cm._consecutive_summarizer_failures = MAX_SUMMARIZER_RETRIES

        call_count = {"n": 0}

        async def counting_summarizer(msgs):
            call_count["n"] += 1
            return "would succeed"

        messages = [{"role": "user", "content": "x " * 50} for _ in range(10)]
        messages.append({"role": "user", "content": "tail"})

        await cm.compact_messages_async(messages, summarizer=counting_summarizer)
        assert call_count["n"] == 0, "circuit breaker should skip summarizer"
