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


def start_server(port: int = 4096, directory: Path = Path(".")):
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
