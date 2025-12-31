"""CLI entry point for codesm"""

import typer
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("codesm.log"),
        logging.StreamHandler(),
    ]
)

app = typer.Typer(
    name="codesm",
    help="AI coding agent",
    add_completion=False,
)


@app.command()
def run(
    directory: Path = typer.Argument(
        Path("."),
        help="Directory to run in",
    ),
    model: str = typer.Option(
        None,
        "--model", "-m",
        help="Model to use (provider/model)",
    ),
    session: str = typer.Option(
        None,
        "--session", "-s",
        help="Session ID to load (for continuing previous conversations)",
    ),
):
    """Start the codesm agent"""
    from codesm.tui.app import CodesmApp
    from codesm.auth.credentials import CredentialStore

    # Use preferred model from config if no model specified
    if model is None:
        store = CredentialStore()
        model = store.get_preferred_model() or "anthropic/claude-sonnet-4-20250514"

    app = CodesmApp(directory=directory, model=model, session_id=session)
    app.run()


@app.command()
def chat(
    message: str = typer.Argument(..., help="Message to send"),
    directory: Path = typer.Option(Path("."), "--dir", "-d"),
    model: str = typer.Option(None, "--model", "-m"),
):
    """Send a single message (non-interactive)"""
    import asyncio
    from codesm.agent.agent import Agent
    from codesm.auth.credentials import CredentialStore

    # Use preferred model from config if no model specified
    if model is None:
        store = CredentialStore()
        model = store.get_preferred_model() or "anthropic/claude-sonnet-4-20250514"

    async def run_chat():
        agent = Agent(directory=directory, model=model)
        async for chunk in agent.chat(message):
            # chunk is a StreamChunk object, extract the content
            if hasattr(chunk, 'content'):
                print(chunk.content, end="", flush=True)
            else:
                print(chunk, end="", flush=True)
        print()

    asyncio.run(run_chat())


@app.command()
def serve(
    port: int = typer.Option(4096, "--port", "-p"),
    directory: Path = typer.Option(Path("."), "--dir", "-d"),
):
    """Start HTTP API server"""
    from codesm.server.server import start_server
    start_server(port=port, directory=directory)


def main():
    app()


if __name__ == "__main__":
    main()
