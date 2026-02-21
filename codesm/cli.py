"""CLI entry point for codesm"""

import typer
import logging
import sys
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("codesm.log"),
        logging.StreamHandler(),
    ]
)

VERSION = "0.1.0"

HELP_TEXT = """Codesm CLI

Usage: codesm [options] [command]

Commands:

  run          Start the codesm agent (interactive TUI)
  chat         Send a single message (non-interactive)
  serve        Start HTTP API server
  init         Initialize project with AGENTS.md
  mcp          Manage MCP servers
    list       List all configured MCP servers
    test       Test connection to MCP servers
    init       Create an example MCP configuration file

Options:

  --help
      Show this message and exit.
  -V, --version
      Print the version number and exit.

Environment variables:

  ANTHROPIC_API_KEY      API key for Anthropic Claude models
  OPENAI_API_KEY         API key for OpenAI models  
  CODESM_MODEL           Default model to use (e.g., anthropic/claude-sonnet-4-20250514)
  CODESM_LOG_LEVEL       Set log level (error, warn, info, debug)
  CODESM_CONFIG          Path to config file (default: ~/.config/codesm/config.json)

Examples:

Start an interactive session:

  $ codesm run

Start an interactive session in a specific directory:

  $ codesm run /path/to/project

Send a single message (non-interactive):

  $ codesm chat "explain this codebase"

Send a message with a specific model:

  $ codesm chat "fix the bug" --model anthropic/claude-sonnet-4-20250514

Initialize project with AGENTS.md:

  $ codesm init

  This scans your project and generates an AGENTS.md file with detected:
  - Language and frameworks
  - Build, test, and lint commands  
  - Code style guidelines

Start the HTTP API server:

  $ codesm serve --port 4096

List configured MCP servers:

  $ codesm mcp list

Test MCP server connections:

  $ codesm mcp test

Configuration:

Codesm can be configured using files in the following locations:

  Project config:
    ./mcp-servers.json          MCP server definitions
    ./.codesm/mcp.json          Alternative MCP config location
    ./AGENTS.md                 Project-specific agent instructions

  User config:
    ~/.config/codesm/config.json    User preferences
    ~/.config/codesm/mcp.json       User MCP servers
    ~/.config/codesm/AGENTS.md      Global agent instructions

AGENTS.md:

  Codesm automatically loads AGENTS.md files to customize agent behavior.
  Supported files (in priority order):
    - AGENTS.md
    - AGENT.md
    - CLAUDE.md
    - CONTEXT.md
    - .cursorrules
    - .github/copilot-instructions.md

  Run 'codesm init' to generate an AGENTS.md for your project.

Memory commands:

  codesm memory list       List stored memories
  codesm memory add        Add a memory manually  
  codesm memory forget     Delete a specific memory
  codesm memory clear      Clear stored memories

Index commands:

  codesm index build       Build codebase index for fast semantic search
  codesm index status      Show index status
  codesm index search      Search the indexed codebase
  codesm index clear       Clear the index
"""


def version_callback(value: bool):
    if value:
        print(f"codesm {VERSION}")
        raise typer.Exit()


def help_callback(ctx: typer.Context, value: bool):
    if value:
        print(HELP_TEXT)
        raise typer.Exit()


app = typer.Typer(
    name="codesm",
    help="AI coding agent",
    add_completion=False,
    invoke_without_command=True,
)

# Add memory and index subcommands
from codesm.memory.cli import memory_app
from codesm.index.cli import index_app
app.add_typer(memory_app, name="memory")
app.add_typer(index_app, name="index")


@app.command()
def login(
    provider: str = typer.Option("anthropic", "--provider", "-p", help="Provider (anthropic, openai)"),
):
    """Store API credentials"""
    from codesm.auth.credentials import CredentialStore
    from rich.prompt import Prompt
    
    store = CredentialStore()
    
    # Prompt for key if not provided
    key = Prompt.ask(f"Enter API key for [green]{provider}[/green]", password=True)
    
    if key:
        store.set(provider, {"api_key": key})
        print(f"Credentials saved for {provider}")
    else:
        print("No key provided")


@app.command()
def logout(
    provider: str = typer.Option("anthropic", "--provider", "-p", help="Provider to remove"),
):
    """Remove API credentials"""
    from codesm.auth.credentials import CredentialStore
    import typer
    
    store = CredentialStore()
    
    if not store.get(provider):
        print(f"No credentials found for {provider}")
        return
        
    if typer.confirm(f"Remove credentials for {provider}?"):
        store.delete(provider)
        print(f"Credentials removed for {provider}")


# Import subcommands
from codesm.cli_threads import app as threads_app
from codesm.cli_tools import app as tools_app

app.add_typer(threads_app, name="threads")
app.add_typer(tools_app, name="tools")

@app.callback()
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version", "-V",
        callback=version_callback,
        is_eager=True,
        help="Print the version number and exit.",
    ),
    execute: str = typer.Option(
        None,
        "--execute", "-x",
        help="Execute a prompt non-interactively and exit.",
    ),
    help_flag: bool = typer.Option(
        False,
        "--help",
        callback=help_callback,
        is_eager=True,
        help="Show this message and exit.",
    ),
):
    """AI coding agent"""
    if execute:
        # Non-interactive execution mode
        import asyncio
        from codesm.agent.agent import Agent
        from codesm.auth.credentials import CredentialStore
        
        async def run_execute():
            store = CredentialStore()
            model = store.get_preferred_model() or "anthropic/claude-sonnet-4-20250514"
            
            # Initialize agent
            agent = Agent(directory=Path("."), model=model)
            
            # We want to capture the final answer, not the whole stream
            full_response = ""
            async for chunk in agent.chat(execute):
                if chunk.type == "text":
                    full_response += chunk.content
            
            # Print only the final result
            print(full_response)
            
            await agent.cleanup()
            
        asyncio.run(run_execute())
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        print(HELP_TEXT)
        raise typer.Exit()


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
        # Initialize MCP explicitly if needed, but agent.chat() does it
        try:
            async for chunk in agent.chat(message):
                # chunk is a StreamChunk object, extract the content
                if hasattr(chunk, 'content'):
                    print(chunk.content, end="", flush=True)
                else:
                    print(chunk, end="", flush=True)
            print()
        finally:
            await agent.cleanup()

    asyncio.run(run_chat())


@app.command()
def serve(
    port: int = typer.Option(4096, "--port", "-p"),
    directory: Path = typer.Option(Path("."), "--dir", "-d"),
):
    """Start HTTP API server"""
    from codesm.server.server import start_server
    start_server(port=port, directory=directory)


@app.command()
def lsp():
    """Start Language Server (stdio)"""
    from codesm.lsp.server import start_lsp
    start_lsp()

# Register subcommands
try:
    from codesm.cli_threads import app as threads_app
    app.add_typer(threads_app, name="threads", help="Manage sessions")
    
    from codesm.cli_tools import app as tools_app
    app.add_typer(tools_app, name="tools", help="Explore tools")
    
    from codesm.cli_permissions import app as perms_app
    app.add_typer(perms_app, name="permissions", help="Manage permissions")

    from codesm.cli_mcp import app as mcp_app
    app.add_typer(mcp_app, name="mcp", help="Manage MCP servers")
except ImportError as e:
    pass


@app.command()
def init(
    directory: Path = typer.Argument(
        Path("."),
        help="Directory to initialize",
    ),
    force: bool = typer.Option(
        False,
        "--force", "-f",
        help="Overwrite existing AGENTS.md",
    ),
    edit: bool = typer.Option(
        False,
        "--edit", "-e",
        help="Open AGENTS.md in editor after creation",
    ),
):
    """Initialize project with AGENTS.md"""
    import os
    from codesm.rules.init import init_agents_md, save_agents_md
    from rich.console import Console

    console = Console()
    workspace = directory.resolve()
    
    content, already_exists = init_agents_md(workspace, force=force)
    
    if already_exists and not force:
        console.print(f"[yellow]AGENTS.md already exists at {workspace / 'AGENTS.md'}[/yellow]")
        console.print("Use --force to overwrite.")
        raise typer.Exit(1)
    
    agents_path = save_agents_md(workspace, content)
    console.print(f"[green]✓[/green] Created {agents_path}")
    console.print()
    console.print("[dim]Detected:[/dim]")
    
    from codesm.rules.init import scan_project
    info = scan_project(workspace)
    if info.language:
        console.print(f"  Language: {info.language}")
    if info.frameworks:
        console.print(f"  Frameworks: {', '.join(info.frameworks)}")
    if info.package_manager:
        console.print(f"  Package manager: {info.package_manager}")
    
    console.print()
    console.print("[dim]Edit AGENTS.md to customize agent behavior for your project.[/dim]")
    
    if edit:
        editor = os.environ.get("EDITOR", "vim")
        os.system(f"{editor} {agents_path}")



def main():
    app()


if __name__ == "__main__":
    main()
