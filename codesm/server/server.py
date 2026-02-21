"""HTTP API server"""

from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="codesm", version="0.1.0")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    model: str = "anthropic/claude-sonnet-4-20250514"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(request: ChatRequest, directory: str = "."):
    from codesm.agent.agent import Agent
    
    agent = Agent(directory=Path(directory), model=request.model)
    
    async def stream():
        async for chunk in agent.chat(request.message):
            # chunk is a StreamChunk object, extract the content
            if hasattr(chunk, 'content'):
                yield chunk.content
            else:
                yield str(chunk)
    
    return StreamingResponse(stream(), media_type="text/plain")


@app.get("/session/{session_id}")
async def view_session(session_id: str):
    """"View a session as HTML"""
    from codesm.session.session import Session
    from fastapi.responses import HTMLResponse
    import markdown
    
    session = Session.load(session_id)
    if not session:
        return HTMLResponse("Session not found", status_code=404)
        
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{session.title} - codesm</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }}
            pre {{ background: #f4f4f4; padding: 15px; border-radius: 5px; overflow-x: auto; }}
            code {{ font-family: 'Menlo', 'Monaco', 'Courier New', monospace; font-size: 0.9em; }}
            .message {{ margin-bottom: 20px; padding: 15px; border-radius: 8px; }}
            .user {{ background: #e3f2fd; border-left: 4px solid #2196f3; }}
            .assistant {{ background: #f5f5f5; border-left: 4px solid #757575; }}
            .tool {{ background: #fff8e1; border-left: 4px solid #ffc107; font-size: 0.9em; }}
            .meta {{ font-size: 0.8em; color: #666; margin-bottom: 5px; }}
            h1 {{ border-bottom: 1px solid #eee; padding-bottom: 10px; }}
        </style>
    </head>
    <body>
        <h1>{session.title}</h1>
        <div class="meta">Created: {session.created_at} | ID: {session.id}</div>
        
        <div id="messages">
    """
    
    for msg in session.get_messages_for_display():
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        
        if role == "user":
            html += f'<div class="message user"><div class="meta">User</div>{markdown.markdown(content)}</div>'
        elif role == "assistant":
            html += f'<div class="message assistant"><div class="meta">codesm</div>{markdown.markdown(content)}</div>'
        elif role == "tool_display":
            tool_name = msg.get("tool_name", "tool")
            html += f'<div class="message tool"><div class="meta">Tool: {tool_name}</div><pre>{content}</pre></div>'
            
    html += """
        </div>
    </body>
    </html>
    """
    
    return HTMLResponse(html)


def start_server(port: int = 4096, directory: Path = Path(".")):
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
