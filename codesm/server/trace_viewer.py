"""Web based trace viewer for Codesm session event logs.

Reads JSONL event files written by the H4 failure mode
instrumentation (`codesm/agent/event_log.py`) from
`~/.local/share/codesm/events/` and renders a per session timeline
and failure mode summary in a single HTML page. Intentionally tiny:
one FastAPI app, two JSON endpoints, one inline HTML page, no
build step, no JavaScript framework.

Run it with::

    codesm trace-viewer
    # or
    python -m codesm.server.trace_viewer

and open http://127.0.0.1:8765/ in a browser.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from codesm.agent.event_log import DEFAULT_EVENTS_DIR, EventLogger


app = FastAPI(title="codesm trace viewer", version="0.1.0")


def _events_dir() -> Path:
    return DEFAULT_EVENTS_DIR


def _list_sessions() -> list[dict[str, Any]]:
    events_dir = _events_dir()
    if not events_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(events_dir.glob("*.jsonl"), reverse=True):
        session_id = path.stem
        try:
            stat = path.stat()
        except OSError:
            continue
        event_count = 0
        first_ts = None
        last_ts = None
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    event_count += 1
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = record.get("ts")
                    if ts:
                        if first_ts is None:
                            first_ts = ts
                        last_ts = ts
        except OSError:
            continue
        rows.append(
            {
                "session_id": session_id,
                "event_count": event_count,
                "size_bytes": stat.st_size,
                "first_event": first_ts,
                "last_event": last_ts,
            }
        )
    return rows


def _summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "iterations": 0,
        "compactions": 0,
        "tokens_dropped": 0,
        "tool_errors": 0,
        "permission_denied": 0,
        "malformed_tool_calls": 0,
        "mark_uncertain": 0,
        "mark_uncertain_by_severity": {"low": 0, "medium": 0, "high": 0},
        "max_iterations_hit": False,
    }
    for ev in events:
        t = ev.get("type")
        if t == "iteration_start":
            n = int(ev.get("n", 0) or 0)
            if n > summary["iterations"]:
                summary["iterations"] = n
        elif t == "compaction":
            summary["compactions"] += 1
            summary["tokens_dropped"] += int(ev.get("tokens_dropped", 0) or 0)
        elif t == "tool_error":
            summary["tool_errors"] += 1
        elif t == "permission_denied":
            summary["permission_denied"] += 1
        elif t == "malformed_tool_call":
            summary["malformed_tool_calls"] += 1
        elif t == "mark_uncertain":
            summary["mark_uncertain"] += 1
            sev = str(ev.get("severity", "")).lower()
            if sev in summary["mark_uncertain_by_severity"]:
                summary["mark_uncertain_by_severity"][sev] += 1
        elif t == "max_iterations":
            summary["max_iterations_hit"] = True
    return summary


@app.get("/api/sessions")
def api_sessions() -> JSONResponse:
    return JSONResponse({"sessions": _list_sessions()})


@app.get("/api/sessions/{session_id}")
def api_session(session_id: str) -> JSONResponse:
    if "/" in session_id or ".." in session_id:
        raise HTTPException(status_code=400, detail="invalid session id")
    events = EventLogger.read(session_id, events_dir=_events_dir())
    if not events:
        raise HTTPException(status_code=404, detail="no events for session")
    return JSONResponse(
        {
            "session_id": session_id,
            "summary": _summarize(events),
            "events": events,
        }
    )


INDEX_HTML = """<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\" />
<title>codesm trace viewer</title>
<style>
  body { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #0f0f13; color: #e6e6e6; margin: 0; }
  header { padding: 14px 20px; border-bottom: 1px solid #2a2a33; background: #16161d; }
  header h1 { font-size: 15px; margin: 0; font-weight: 600; }
  header .hint { font-size: 11px; color: #8a8a96; margin-top: 4px; }
  main { display: grid; grid-template-columns: 320px 1fr; min-height: calc(100vh - 56px); }
  aside { border-right: 1px solid #2a2a33; overflow-y: auto; }
  aside .session { padding: 10px 14px; border-bottom: 1px solid #1e1e26; cursor: pointer; font-size: 12px; }
  aside .session:hover { background: #1c1c26; }
  aside .session.active { background: #24243a; border-left: 2px solid #7a7aff; padding-left: 12px; }
  aside .session .sid { color: #e6e6e6; }
  aside .session .meta { color: #7a7a85; font-size: 10px; margin-top: 3px; }
  section { padding: 18px 24px; overflow-y: auto; }
  section h2 { font-size: 13px; margin: 0 0 10px; color: #b8b8c8; font-weight: 600; }
  .summary { display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 22px; }
  .pill { padding: 6px 12px; border-radius: 4px; font-size: 11px; background: #1e1e2a; border: 1px solid #2a2a33; }
  .pill strong { color: #e6e6e6; font-weight: 600; }
  .pill.danger { border-color: #6b2a2a; color: #ff9a9a; }
  .pill.warn { border-color: #6b4a2a; color: #ffc29a; }
  .event { padding: 8px 12px; margin-bottom: 6px; border-left: 2px solid #2a2a33; background: #14141c; font-size: 11px; }
  .event .type { display: inline-block; padding: 2px 8px; border-radius: 3px; background: #2a2a3a; color: #e6e6e6; font-weight: 600; margin-right: 10px; font-size: 10px; }
  .event .ts { color: #6a6a75; font-size: 10px; }
  .event .fields { color: #a0a0b0; margin-top: 4px; word-break: break-word; }
  .event.compaction { border-left-color: #ffb347; }
  .event.tool_error { border-left-color: #ff6b6b; }
  .event.permission_denied { border-left-color: #ff6b6b; }
  .event.malformed_tool_call { border-left-color: #ff6b6b; }
  .event.mark_uncertain { border-left-color: #9a7aff; }
  .event.max_iterations { border-left-color: #ff6b6b; }
  .event.iteration_start { border-left-color: #4a8aff; }
  .empty { color: #6a6a75; font-size: 11px; padding: 20px; }
</style>
</head>
<body>
<header>
  <h1>codesm trace viewer</h1>
  <div class=\"hint\">Reads session event logs from ~/.local/share/codesm/events/. Click a session on the left.</div>
</header>
<main>
  <aside id=\"sessions\"><div class=\"empty\">Loading sessions...</div></aside>
  <section id=\"detail\"><div class=\"empty\">Select a session to see events.</div></section>
</main>
<script>
const sessionsEl = document.getElementById('sessions');
const detailEl = document.getElementById('detail');
let activeId = null;

async function loadSessions() {
  const r = await fetch('/api/sessions');
  const d = await r.json();
  if (!d.sessions.length) {
    sessionsEl.innerHTML = '<div class=\"empty\">No sessions found. Run the agent first.</div>';
    return;
  }
  sessionsEl.innerHTML = '';
  for (const s of d.sessions) {
    const row = document.createElement('div');
    row.className = 'session' + (activeId === s.session_id ? ' active' : '');
    row.innerHTML = `<div class=\"sid\">${s.session_id}</div><div class=\"meta\">${s.event_count} events &middot; ${(s.size_bytes / 1024).toFixed(1)} KB &middot; ${s.last_event || ''}</div>`;
    row.onclick = () => loadSession(s.session_id);
    sessionsEl.appendChild(row);
  }
}

function fmtPill(label, value, cls) {
  return `<div class=\"pill ${cls || ''}\"><strong>${value}</strong> ${label}</div>`;
}

async function loadSession(sid) {
  activeId = sid;
  for (const el of sessionsEl.querySelectorAll('.session')) {
    el.classList.toggle('active', el.querySelector('.sid').textContent === sid);
  }
  detailEl.innerHTML = '<div class=\"empty\">Loading...</div>';
  const r = await fetch('/api/sessions/' + encodeURIComponent(sid));
  if (!r.ok) {
    detailEl.innerHTML = '<div class=\"empty\">Could not load session.</div>';
    return;
  }
  const d = await r.json();
  const s = d.summary;
  const sev = s.mark_uncertain_by_severity;
  const pills = [
    fmtPill('iterations', s.iterations),
    fmtPill('compactions', s.compactions, s.compactions ? 'warn' : ''),
    fmtPill('tokens dropped', s.tokens_dropped, s.tokens_dropped ? 'warn' : ''),
    fmtPill('tool errors', s.tool_errors, s.tool_errors ? 'danger' : ''),
    fmtPill('permission denials', s.permission_denied, s.permission_denied ? 'danger' : ''),
    fmtPill('malformed calls', s.malformed_tool_calls, s.malformed_tool_calls ? 'danger' : ''),
    fmtPill(`mark_uncertain (L${sev.low}/M${sev.medium}/H${sev.high})`, s.mark_uncertain),
    fmtPill('max iter hit', s.max_iterations_hit ? 'yes' : 'no', s.max_iterations_hit ? 'danger' : ''),
  ].join('');
  const events = d.events.map(ev => {
    const known = ['type', 'ts', 'session_id'];
    const rest = Object.entries(ev).filter(([k]) => !known.includes(k));
    const fields = rest.map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' ');
    return `<div class=\"event ${ev.type}\"><span class=\"type\">${ev.type}</span><span class=\"ts\">${ev.ts || ''}</span><div class=\"fields\">${fields}</div></div>`;
  }).join('');
  detailEl.innerHTML = `<h2>${sid}</h2><div class=\"summary\">${pills}</div><h2>events (${d.events.length})</h2>${events || '<div class=\"empty\">No events.</div>'}`;
}

loadSessions();
setInterval(loadSessions, 5000);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Launch the trace viewer with uvicorn."""
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
