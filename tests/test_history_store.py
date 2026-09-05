"""Durable local recall without provider calls or cross-project reads."""

from copy import deepcopy
import json

import pytest

from codesm.memory.history import HistoryStore
from codesm.storage.storage import Storage


def snapshot(directory, session_id="session_fixture", messages=None, **state):
    return {"id": session_id, "directory": str(directory), "title": "Work in progress",
        "messages": messages or [], **state}


def read_all(store, project, record_id, limit=12000):
    parts = []
    offset = 0
    while True:
        page = store.read(project, record_id, offset=offset, limit=limit)
        assert page is not None and len(page["text"]) <= min(limit, 24000)
        parts.append(page["text"])
        if page["next_offset"] is None:
            return json.loads("".join(parts))
        assert page["next_offset"] > offset
        offset = page["next_offset"]


def test_full_tool_arguments_results_and_events_remain_searchable_and_pageable(tmp_path, monkeypatch):
    store = HistoryStore()
    content = "source line with unicode λ\n" * 4000 + "CompleteTailNeedle"
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "call_edit", "type": "function",
            "function": {"name": "write", "arguments": json.dumps({"content": content})}}]},
        {"role": "tool", "tool_call_id": "call_edit", "content": content},
    ]
    data = snapshot(tmp_path, messages=messages)
    store.sync_session(data)
    event = {"type": "tool_result", "output": content, "run_id": "run_fixture"}
    store.record_event(data["id"], event)
    hits = store.search(tmp_path, "CompleteTailNeedle")
    assert {hit["kind"] for hit in hits} == {"message", "event"}
    assert len(hits) == 3
    for hit in hits:
        expected = event if hit["kind"] == "event" else messages[int(hit["source"].split(":")[1])]
        assert read_all(store, tmp_path, hit["id"], limit=7013) == expected
    first = hits[0]["id"]
    assert store.read(tmp_path, first, offset=-10, limit=100)["text"] == store.read(
        tmp_path, first, offset=0, limit=100)["text"]
    assert len(store.read(tmp_path, first, limit=100000)["text"]) == 24000
    with monkeypatch.context() as patch:
        patch.setattr("codesm.memory.history.json.loads", lambda *_: pytest.fail("page read decoded the full payload"))
        assert store.read(tmp_path, first, offset=30000, limit=100)["next_offset"] == 30100


def test_recall_is_scoped_to_exact_project_and_optional_session(tmp_path):
    store = HistoryStore()
    message = [{"role": "user", "content": "SharedSearchNeedle"}]
    for session_id, project in (("a", tmp_path), ("b", tmp_path),
            ("nested", tmp_path / "nested"), ("other", tmp_path.parent / "other-project")):
        store.sync_session(snapshot(project, session_id, message))
    hits = store.search(tmp_path, "SharedSearchNeedle")
    assert {hit["session_id"] for hit in hits} == {"a", "b"}
    assert {hit["session_id"] for hit in store.search(tmp_path, "SharedSearchNeedle", "b")} == {"b"}
    assert store.search(tmp_path, "SharedSearchNeedle", "nested") == []
    assert store.read(tmp_path / "nested", hits[0]["id"]) is None
    assert store.read(tmp_path, 999999) is None
    assert len(store.search(tmp_path, "SharedSearchNeedle", limit=1)) == 1


@pytest.mark.parametrize("query", [
    '"PunctuationNeedle"', "src/PunctuationNeedle.py", "PunctuationNeedle::method()",
    "PunctuationNeedle OR ) NEAR( ' --", "PunctuationNeedle + C++",
])
def test_search_treats_fts_punctuation_as_text(tmp_path, query):
    store = HistoryStore()
    store.sync_session(snapshot(tmp_path, messages=[{"role": "user", "content": "PunctuationNeedle"}]))
    assert store.search(tmp_path, query)[0]["source"] == "message:0"
    assert store.search(tmp_path, ' + -* ":() ') == []


@pytest.mark.parametrize("content", [
    '  {"content":"first line\\nNestedSearchNeedle"}',
    '[' * 500 + '"NestedSearchNeedle"' + ']' * 500,
])
def test_index_accepts_whitespace_and_deep_json_tool_output(tmp_path, content):
    store = HistoryStore()
    message = {"role": "tool", "tool_call_id": "call_read", "content": content}
    store.sync_session(snapshot(tmp_path, messages=[message]))
    hit = store.search(tmp_path, "NestedSearchNeedle")[0]
    assert read_all(store, tmp_path, hit["id"]) == message


def test_append_sync_is_idempotent_updates_state_and_handles_shorter_history(tmp_path):
    store = HistoryStore()
    data = snapshot(tmp_path, messages=[{"role": "user", "content": "FirstMessageNeedle"}],
        title="OldTitleNeedle")
    store.sync_session(data)
    original = store.search(tmp_path, "FirstMessageNeedle")[0]["id"]
    store.sync_session(data)
    assert [hit["id"] for hit in store.search(tmp_path, "FirstMessageNeedle")] == [original]
    data["messages"].append({"role": "assistant", "content": "SecondMessageNeedle"})
    data["title"] = "NewTitleNeedle"
    store.sync_session(data)
    assert store.search(tmp_path, "FirstMessageNeedle")[0]["id"] == original
    assert len(store.search(tmp_path, "SecondMessageNeedle")) == 1
    assert store.search(tmp_path, "OldTitleNeedle") == []
    assert len(store.search(tmp_path, "NewTitleNeedle")) == 1
    data["messages"].pop()
    store.sync_session(data)
    assert store.search(tmp_path, "SecondMessageNeedle") == []
    assert len(store.search(tmp_path, "FirstMessageNeedle")) == 1


def test_import_legacy_json_and_events_once_and_force_without_duplicates(tmp_path, monkeypatch):
    store = HistoryStore()
    data = snapshot(tmp_path, messages=[{"role": "user", "content": "LegacyMessageNeedle"}])
    Storage.write(["session", data["id"]], data)
    Storage.write(["session", "other"], snapshot(tmp_path / "other", "other", [
        {"role": "user", "content": "LegacyMessageNeedle"}]))
    event = {"type": "tool_error", "message": "LegacyEventNeedle", "ts": "2026-09-05T10:00:00"}
    log = Storage.BASE_DIR / "events" / f"{data['id']}.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps(event) + '\n{"interrupted":\n')
    store.import_project(tmp_path)
    assert len(store.search(tmp_path, "LegacyMessageNeedle")) == 1
    event_hits = store.search(tmp_path, "LegacyEventNeedle")
    assert len(event_hits) == 1
    assert read_all(store, tmp_path, event_hits[0]["id"]) == event
    with monkeypatch.context() as patch:
        patch.setattr(Storage, "list", lambda *_: pytest.fail("already imported project was rescanned"))
        store.import_project(tmp_path)
    store.import_project(tmp_path, force=True)
    assert len(store.search(tmp_path, "LegacyMessageNeedle")) == 1
    assert len(store.search(tmp_path, "LegacyEventNeedle")) == 1


def test_live_event_and_imported_copy_are_indexed_once(tmp_path):
    store = HistoryStore()
    data = snapshot(tmp_path)
    Storage.write(["session", data["id"]], data)
    store.sync_session(data)
    event = {"type": "tool_end", "output": "LiveEventNeedle", "ts": "2026-09-05T10:00:00"}
    store.record_event(data["id"], event)
    log = Storage.BASE_DIR / "events" / f"{data['id']}.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps(event) + "\n")
    store.import_project(tmp_path)
    store.import_project(tmp_path, force=True)
    assert len(store.search(tmp_path, "LiveEventNeedle")) == 1


def test_opaque_replay_is_kept_on_disk_but_not_in_search_or_reads(tmp_path):
    store = HistoryStore()
    message = {"role": "assistant", "content": "VisibleAnswerNeedle",
        "response_provider": "openai", "response_model": "fixture-model",
        "response_prefix": "OpaquePrefixNeedle",
        "response_items": [{"type": "reasoning", "encrypted_content": "OpaqueReasoningNeedle"}],
        "chat_response": {"extra_content": {"signature": "OpaqueSignatureNeedle"}},
        "anthropic_content": [{"type": "thinking", "signature": "OpaqueAnthropicNeedle"}],
        "thinking_blocks": [{"thinking": "OpaqueThinkingNeedle"}]}
    original = deepcopy(message)
    store.sync_session(snapshot(tmp_path, messages=[message]))
    hit = store.search(tmp_path, "VisibleAnswerNeedle")[0]
    assert read_all(store, tmp_path, hit["id"]) == {"role": "assistant", "content": "VisibleAnswerNeedle"}
    assert store.search(tmp_path, "OpaqueReasoningNeedle OpaqueSignatureNeedle OpaqueAnthropicNeedle OpaqueThinkingNeedle") == []
    with store.connect() as db:
        payload = db.execute("SELECT payload FROM records WHERE id=?", (hit["id"],)).fetchone()[0]
    assert json.loads(payload) == original == message
    store.record_event("session_fixture", {"type": "saved_response", "message": message})
    event = next(hit for hit in store.search(tmp_path, "VisibleAnswerNeedle") if hit["kind"] == "event")
    assert read_all(store, tmp_path, event["id"]) == {"type": "saved_response", "message": {
        "role": "assistant", "content": "VisibleAnswerNeedle"}}
    assert store.search(tmp_path, "OpaqueReasoningNeedle OpaqueSignatureNeedle OpaqueAnthropicNeedle OpaqueThinkingNeedle OpaquePrefixNeedle") == []


def test_delete_removes_records_search_entries_and_events_for_only_that_session(tmp_path):
    store = HistoryStore()
    for session_id in ("keep", "remove"):
        store.sync_session(snapshot(tmp_path, session_id, [{"role": "user", "content": "DeleteNeedle"}]))
        store.record_event(session_id, {"type": "tool_end", "output": "DeleteNeedle"})
    removed = store.search(tmp_path, "DeleteNeedle", "remove")
    store.delete_session("remove")
    store.delete_session("remove")
    assert {hit["session_id"] for hit in store.search(tmp_path, "DeleteNeedle")} == {"keep"}
    assert all(store.read(tmp_path, hit["id"]) is None for hit in removed)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM records WHERE session_id='remove'").fetchone()[0] == 0


def test_deleted_record_id_is_never_reused_for_unrelated_evidence(tmp_path):
    store = HistoryStore()
    store.sync_session(snapshot(tmp_path, 'old', [{'role': 'user', 'content': 'OldEvidenceNeedle'}]))
    reference = store.search(tmp_path, 'OldEvidenceNeedle')[0]['id']
    store.delete_session('old')
    store.sync_session(snapshot(tmp_path, 'new', [{'role': 'user', 'content': 'NewEvidenceNeedle'}]))
    assert store.read(tmp_path, reference) is None
    assert store.search(tmp_path, 'NewEvidenceNeedle')[0]['id'] > reference
