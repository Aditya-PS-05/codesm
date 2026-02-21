"""MCP Management CLI"""

import json
import os
import shlex
import typer
from pathlib import Path
from rich.console import Console
from rich.table import Table

from codesm.mcp.config import load_mcp_config, create_example_config

app = typer.Typer(name="mcp", help="Manage MCP servers and configuration")
console = Console()

CONFIG_PATH = Path.home() / ".config" / "codesm" / "mcp.json"

def _ensure_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        create_example_config(CONFIG_PATH)

def _load_raw_config() -> dict:
    _ensure_config()
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {"mcpServers": {}}

def _save_raw_config(config: dict):
    CONFIG_PATH.write_text(json.dumps(config, indent=2))

@app.command("list")
def list_servers():
    """List configured MCP servers"""
    servers = _load_raw_config().get("mcpServers", {})
    
    if not servers:
        console.print("[yellow]No MCP servers configured.[/yellow]")
        return

    table = Table(title="MCP Servers")
    table.add_column("Name", style="cyan")
    table.add_column("Command", style="blue")
    
    for name, config in servers.items():
        cmd = config.get("command", "")
        args = config.get("args", [])
        args_str = f"{cmd} {' '.join(args)}"
        if len(args_str) > 50:
            args_str = args_str[:47] + "..."
        table.add_row(name, args_str)
        
    console.print(table)

@app.command("doctor")
def check_health():
    """Check connections to configured MCP servers"""
    servers = load_mcp_config(CONFIG_PATH)
    
    if not servers:
        console.print("[yellow]No MCP servers configured.[/yellow]")
        console.print(f"Config file: {CONFIG_PATH}")
        return

    table = Table(title="MCP Server Health")
    table.add_column("Server", style="cyan")
    table.add_column("Command", style="blue")
    table.add_column("Status", style="green")
    
    # We can't fully "ping" properly without starting the client which is async complex
    # For CLI, we verify the executable exists
    
    import shutil
    
    for name, config in servers.items():
        cmd = config.command
        executable = shutil.which(cmd)
        
        status = "[green]OK[/green]" if executable else "[red]Command not found[/red]"
        
        args_str = f"{cmd} {' '.join(config.args)}"
        if len(args_str) > 50:
            args_str = args_str[:47] + "..."
            
        table.add_row(name, args_str, status)
        
    console.print(table)
    console.print(f"\n[dim]Config file: {CONFIG_PATH}[/dim]")

@app.command("add")
def add_server(
    name: str = typer.Argument(..., help="Server name"),
    command: str = typer.Argument(..., help="Command to run"),
    args: str = typer.Option("", help="Arguments (space separated strings)"),
):
    """Add an MCP server configuration"""
    config = _load_raw_config()
    
    # Ensure structure
    if "mcpServers" not in config:
        config["mcpServers"] = {}
        
    args_list = shlex.split(args)
    
    config["mcpServers"][name] = {
        "command": command,
        "args": args_list
    }
    
    _save_raw_config(config)
    console.print(f"[green]Added MCP server: {name}[/green]")

@app.command("remove")
def remove_server(
    name: str = typer.Argument(..., help="Server name to remove"),
):
    """Remove an MCP server configuration"""
    config = _load_raw_config()
    
    if "mcpServers" in config and name in config["mcpServers"]:
        del config["mcpServers"][name]
        _save_raw_config(config)
        console.print(f"[green]Removed MCP server: {name}[/green]")
    else:
        console.print(f"[red]Server not found: {name}[/red]")

# OAuth / Credentials helpers
oauth_app = typer.Typer(name="oauth", help="Manage credentials")
app.add_typer(oauth_app, name="oauth")

@oauth_app.command("login")
def oauth_login(
    server: str = typer.Argument(..., help="Server name (e.g. github)"),
):
    """Configure credentials for a server"""
    config = _load_raw_config()
    
    if "mcpServers" not in config or server not in config["mcpServers"]:
        console.print(f"[red]Server '{server}' not found in config.[/red]")
        return
        
    console.print(f"Configuring credentials for [bold]{server}[/bold]...")
    
    # Heuristic for common servers
    env_keys = []
    if server == "github":
        env_keys = ["GITHUB_PERSONAL_ACCESS_TOKEN"]
    elif server == "slack":
        env_keys = ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"]
    elif server == "postgres":
        env_keys = ["POSTGRES_URL"]
    else:
        # Generic prompt
        key = typer.prompt("Environment variable name (e.g. API_KEY)")
        env_keys = [key]
    
    env_updates = {}
    for key in env_keys:
        value = typer.prompt(f"Enter value for {key}", hide_input=True)
        env_updates[key] = value
        
    # Update config
    if "env" not in config["mcpServers"][server]:
        config["mcpServers"][server]["env"] = {}
        
    config["mcpServers"][server]["env"].update(env_updates)
    
    _save_raw_config(config)
    console.print(f"[green]Credentials saved for {server}[/green]")

@oauth_app.command("logout")
def oauth_logout(
    server: str = typer.Argument(..., help="Server name"),
):
    """Remove credentials for a server"""
    config = _load_raw_config()
    
    if "mcpServers" not in config or server not in config["mcpServers"]:
        console.print(f"[red]Server '{server}' not found.[/red]")
        return
        
    if "env" in config["mcpServers"][server]:
        del config["mcpServers"][server]["env"]
        _save_raw_config(config)
        console.print(f"[green]Credentials removed for {server}[/green]")
    else:
        console.print(f"[yellow]No credentials found for {server}[/yellow]")

@oauth_app.command("status")
def oauth_status():
    """Show authentication status of servers"""
    config = _load_raw_config()
    
    if "mcpServers" not in config:
        console.print("No servers configured.")
        return

    table = Table(title="Credential Status")
    table.add_column("Server", style="cyan")
    table.add_column("Env Vars", style="yellow")
    
    for name, srv in config["mcpServers"].items():
        env = srv.get("env", {})
        keys = list(env.keys())
        status = ", ".join(keys) if keys else "[dim]None[/dim]"
        table.add_row(name, status)
        
    console.print(table)
