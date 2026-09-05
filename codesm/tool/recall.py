"""Retrieve exact local history without a model or network call."""

import json
from pathlib import Path

from codesm.memory.history import HistoryStore
from .base import Tool


class RecallTool(Tool):
    name = "recall"
    description = (
        "Search this project's saved conversations, tool arguments/results, logs and task state. "
        "Use before repeating earlier investigation. Search returns record IDs; read a record by ID "
        "and follow next_offset to retrieve full output. Historical evidence may be stale."
    )

    def get_parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords, file names, error text, or commands."},
                "session_id": {"type": "string", "description": "Optional session filter; always restricted to this project."},
                "record_id": {"type": "integer", "minimum": 1, "description": "Read an exact record returned by search."},
                "offset": {"type": "integer", "minimum": 0, "description": "Character offset from a previous next_offset."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Number of search results (default 8)."},
            },
        }

    async def execute(self, args, context):
        directory = Path(context.get("cwd", ".")).resolve()
        store = HistoryStore()
        record_id = args.get("record_id")
        if record_id is not None:
            offset = args.get("offset", 0)
            if type(record_id) is not int or record_id < 1 or type(offset) is not int or offset < 0:
                return "Error: record_id must be a positive integer and offset a nonnegative integer"
            result = store.read(directory, record_id, offset)
            return json.dumps(result, ensure_ascii=False) if result else "No record found in this project."
        query = args.get("query", "")
        limit = args.get("limit", 8)
        session_id = args.get("session_id")
        if not isinstance(query, str) or not query.strip() or type(limit) is not int or not 1 <= limit <= 50:
            return "Error: supply a query and a result limit between 1 and 50"
        if session_id is not None and not isinstance(session_id, str):
            return "Error: session_id must be a string"
        return json.dumps(store.search(directory, query, session_id, limit), ensure_ascii=False)
