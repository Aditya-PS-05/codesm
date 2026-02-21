"""CLI commands for tool management"""

import typer
import json
import asyncio
from rich.console import Console
from rich.table import Table
from rich.syntax import Syntax
from pathlib import Path
from codesm.tool.registry import ToolRegistry
from codesm.mcp.manager import MCPManager
from codesm.mcp import load_mcp_config

app = typer.Typer(help="Discover and execute tools")
console = Console()

def get_registry(directory: Path = Path(".")) -> ToolRegistry:
    """Initialize tool registry with MCP if configured"""
    registry = ToolRegistry()
    
    # Try to load MCP config
    config_path = directory / "mcp-servers.json"
    if not config_path.exists():
        # Try other locations
        for candidate in [
            directory / ".mcp" / "servers.json",
            directory / "codesm.json",
            Path.home() / ".config" / "codesm" / "mcp.json"
        ]:
            if candidate.exists():
                config_path = candidate
                break
                
    servers = load_mcp_config(config_path) if config_path.exists() else {}
    
    if servers:
        async def connect_mcp():
            manager = MCPManager()
            for name, config in servers.items():
                manager.add_server(config)
            await manager.connect_all()
            return manager
            
        manager = asyncio.run(connect_mcp())
        registry.set_mcp_manager(manager, workspace_dir=directory)
        
    return registry

@app.command("list")
def list_tools(
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Working directory context")
):
    """List available tools"""
    registry = get_registry(directory)
    schemas = registry.get_schemas()
    
    table = Table(title="Available Tools")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="green")
    
    for tool in schemas:
        desc = tool["description"].split("\n")[0]
        table.add_row(tool["name"], desc)
        
    console.print(table)

@app.command("show")
def show_tool(
    name: str = typer.Argument(..., help="Tool name"),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Working directory context")
):
    """Show tool details and schema"""
    registry = get_registry(directory)
    tool = registry.get(name)
    
    if not tool:
        console.print(f"[red]Tool not found: {name}[/red]")
        return
        
    console.print(f"[bold cyan]{tool.name}[/bold cyan]")
    console.print(f"[italic]{tool.description}[/italic]")
    console.print()
    console.print("[bold]Parameters:[/bold]")
    
    import json
    schema_json = json.dumps(tool.get_parameters_schema(), indent=2)
    console.print(Syntax(schema_json, "json"))

@app.command("use")
def use_tool(
    name: str = typer.Argument(..., help="Tool name"),
    args: str = typer.Argument("{}", help="JSON arguments for the tool"),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Working directory context")
):
    """Execute a tool directly"""
    registry = get_registry(directory)
    tool = registry.get(name)
    
    if not tool:
        console.print(f"[red]Tool not found: {name}[/red]")
        return
        
    try:
        if args.strip():
            tool_args = json.loads(args)
        else:
            tool_args = {}
    except json.JSONDecodeError:
        console.print("[red]Invalid JSON arguments[/red]")
        return
        
    # Context needed for some tools
    context = {
        "cwd": directory.resolve(),
        "workspace_dir": str(directory.resolve())
    }
    
    async def run_tool():
        result = await registry.execute(name, tool_args, context)
        return result
        
    console.print(f"[dim]Executing {name}...[/dim]")
    result = asyncio.run(run_tool())
    console.print(result)
