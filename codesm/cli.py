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
  eval         Run a coding-model eval task from a YAML file and print JSON report
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

Run a coding-model eval task and print a JSON report:

  $ codesm eval benchmarks/add-docstring.yaml --pretty

Run the same task against a specific model and save the JSON:

  $ codesm eval benchmarks/add-docstring.yaml \
      --model anthropic/claude-sonnet-4-20250514 \
      --output reports/add-docstring.json

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
    help_flag: bool = typer.Option(
        False,
        "--help",
        callback=help_callback,
        is_eager=True,
        help="Show this message and exit.",
    ),
):
    """AI coding agent"""
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
    dangerously_skip_permissions: bool = typer.Option(
        False,
        "--dangerously-skip-permissions",
        help="Bypass every permission check: interactive prompts, path guards, and command blocks. Use only in a sandbox or throwaway workspace.",
    ),
):
    """Start the codesm agent"""
    from codesm.tui.app import CodesmApp
    from codesm.auth.credentials import CredentialStore

    if dangerously_skip_permissions:
        from codesm.permission import set_bypass_all
        set_bypass_all(True)
        typer.echo(
            "WARNING: --dangerously-skip-permissions is active. "
            "All permission checks are bypassed for this session.",
            err=True,
        )

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
    dangerously_skip_permissions: bool = typer.Option(
        False,
        "--dangerously-skip-permissions",
        help="Bypass every permission check: interactive prompts, path guards, and command blocks. Use only in a sandbox or throwaway workspace.",
    ),
):
    """Send a single message (non-interactive)"""
    import asyncio
    from codesm.agent.agent import Agent
    from codesm.auth.credentials import CredentialStore

    if dangerously_skip_permissions:
        from codesm.permission import set_bypass_all
        set_bypass_all(True)
        typer.echo(
            "WARNING: --dangerously-skip-permissions is active. "
            "All permission checks are bypassed for this session.",
            err=True,
        )

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


@app.command("trace-viewer")
def trace_viewer(
    host: str = typer.Option("127.0.0.1", "--host", "-H"),
    port: int = typer.Option(8765, "--port", "-p"),
):
    """Launch the web based session event log viewer.

    Reads JSONL event logs from ~/.local/share/codesm/events/ and
    renders a per session timeline with failure mode counts. Useful
    for inspecting real runs during an interview or a post mortem.
    """
    from codesm.server.trace_viewer import run
    typer.echo(f"Trace viewer at http://{host}:{port}/ (Ctrl+C to stop)")
    run(host=host, port=port)


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


@app.command("eval")
def eval_cmd(
    task_file: Path = typer.Argument(..., help="Path to a YAML eval task file"),
    model: str = typer.Option(None, "--model", "-m", help="Override the task's model (provider/model)"),
    directory: Path = typer.Option(None, "--dir", "-d", help="Override the task's working directory"),
    output: Path = typer.Option(None, "--output", "-o", help="Write full JSON report to this path"),
    pretty: bool = typer.Option(False, "--pretty", help="Print a human readable summary before the JSON"),
    all_providers: bool = typer.Option(
        False,
        "--all-providers",
        help="Run the task against Anthropic, OpenAI, and OpenRouter with default models and print a side by side comparison",
    ),
    providers: str = typer.Option(
        None,
        "--providers",
        help="Comma separated model list to compare instead of the default set (implies --all-providers)",
    ),
):
    """Run a coding-model eval task and print a structured JSON report.

    The task file is YAML with name, prompt, setup, assertion fields.
    The report captures provider, tokens, tool calls, iterations, context
    compaction, permission denials, tool errors, wall clock, and verdict.

    Pass --all-providers to run the same task against Anthropic, OpenAI,
    and OpenRouter and get a side by side table of tokens, tool calls,
    time, and pass/fail verdict.
    """
    import asyncio
    import json
    from codesm.eval import (
        DEFAULT_PROVIDER_MODELS,
        format_comparison_table,
        load_task,
        run_comparison,
        run_task,
    )

    try:
        task = load_task(task_file)
    except (FileNotFoundError, ValueError) as e:
        typer.echo(f"Error loading task file: {e}", err=True)
        raise typer.Exit(2)

    # Comparison mode: --all-providers or --providers
    if all_providers or providers:
        if providers:
            models = [m.strip() for m in providers.split(",") if m.strip()]
        else:
            models = list(DEFAULT_PROVIDER_MODELS)

        async def run_compare():
            return await run_comparison(
                task,
                task_file=task_file,
                models=models,
                directory_override=directory,
            )

        result = asyncio.run(run_compare())

        if pretty:
            typer.echo(format_comparison_table(result))
            typer.echo("")

        payload = json.dumps(result.to_dict(), indent=2, default=str)

        if output:
            output.write_text(payload)
            typer.echo(f"Report written to {output}")
        else:
            typer.echo(payload)

        raise typer.Exit(0 if result.all_passed else 1)

    # Single model mode
    async def run():
        return await run_task(
            task,
            task_file=task_file,
            model_override=model,
            directory_override=directory,
        )

    report = asyncio.run(run())

    if pretty:
        verdict = "PASS" if report.passed else "FAIL"
        typer.echo(f":: {verdict}  {report.task_name}  ({report.wall_clock_ms}ms)")
        typer.echo(f"   model: {report.model}")
        typer.echo(f"   iterations: {report.iterations}  tool_calls: {sum(report.tool_calls.values())}")
        if report.compaction_events:
            typer.echo(f"   compactions: {len(report.compaction_events)} (dropped {report.compaction_tokens_dropped} tokens)")
        if report.tool_errors:
            typer.echo(f"   tool_errors: {len(report.tool_errors)}")
        if report.permission_denials:
            typer.echo(f"   permission_denials: {report.permission_denials}")
        if report.malformed_tool_calls:
            typer.echo(f"   malformed_tool_calls: {report.malformed_tool_calls}")
        if report.mark_uncertain_count:
            sev = report.mark_uncertain_by_severity
            typer.echo(
                f"   mark_uncertain: {report.mark_uncertain_count} "
                f"(low={sev['low']} med={sev['medium']} high={sev['high']})"
            )
        for a in report.assertions:
            mark = "OK" if a.passed else "FAIL"
            typer.echo(f"   [{mark}] {a.command}")
        if report.error:
            typer.echo(f"   error: {report.error}")
        typer.echo("")

    payload = json.dumps(report.to_dict(), indent=2, default=str)

    if output:
        output.write_text(payload)
        typer.echo(f"Report written to {output}")
    else:
        typer.echo(payload)

    raise typer.Exit(0 if report.passed else 1)


# MCP subcommands
mcp_app = typer.Typer(help="MCP (Model Context Protocol) management")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("list")
def mcp_list(
    config: Path = typer.Option(None, "--config", "-c", help="Path to MCP config file"),
):
    """List configured MCP servers"""
    from codesm.mcp import load_mcp_config
    from rich.console import Console
    from rich.table import Table

    console = Console()
    servers = load_mcp_config(config)

    if not servers:
        console.print("[yellow]No MCP servers configured[/yellow]")
        console.print("Create a config file with 'codesm mcp init'")
        return

    table = Table(title="MCP Servers")
    table.add_column("Name", style="cyan")
    table.add_column("Command", style="green")
    table.add_column("Transport")

    for name, server in servers.items():
        cmd = f"{server.command} {' '.join(server.args)}"
        if len(cmd) > 50:
            cmd = cmd[:47] + "..."
        table.add_row(name, cmd, server.transport)

    console.print(table)


@mcp_app.command("test")
def mcp_test(
    server_name: str = typer.Argument(None, help="Server name to test (tests all if omitted)"),
    config: Path = typer.Option(None, "--config", "-c", help="Path to MCP config file"),
):
    """Test connection to MCP servers"""
    import asyncio
    from codesm.mcp import load_mcp_config, MCPManager
    from rich.console import Console

    console = Console()
    servers = load_mcp_config(config)

    if not servers:
        console.print("[red]No MCP servers configured[/red]")
        return

    async def test_servers():
        manager = MCPManager()
        
        for name, server_config in servers.items():
            if server_name and name != server_name:
                continue
            manager.add_server(server_config)

        with console.status("Connecting to MCP servers..."):
            results = await manager.connect_all()

        for name, success in results.items():
            if success:
                client = manager._clients.get(name)
                tools = len(client.tools) if client else 0
                resources = len(client.resources) if client else 0
                console.print(f"[green]✓[/green] {name}: {tools} tools, {resources} resources")
            else:
                console.print(f"[red]✗[/red] {name}: connection failed")

        # List discovered tools
        tools = manager.list_all_tools()
        if tools:
            console.print(f"\n[bold]Discovered {len(tools)} tools:[/bold]")
            for tool in tools:
                console.print(f"  • {tool['server']}/{tool['name']}: {tool['description'][:60]}...")

        await manager.disconnect_all()

    asyncio.run(test_servers())


@mcp_app.command("init")
def mcp_init(
    path: Path = typer.Argument(Path("mcp-servers.json"), help="Path to create config file"),
):
    """Create an example MCP configuration file"""
    from codesm.mcp import create_example_config
    from rich.console import Console

    console = Console()
    
    if path.exists():
        if not typer.confirm(f"{path} already exists. Overwrite?"):
            raise typer.Abort()

    create_example_config(path)
    console.print(f"[green]Created example MCP config at {path}[/green]")
    console.print("Edit the file to configure your MCP servers.")


def main():
    app()


if __name__ == "__main__":
    main()
