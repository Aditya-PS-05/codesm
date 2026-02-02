"""Permissions CLI"""

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="permissions", help="Manage security permission rules")
console = Console()

@app.command("list")
def list_rules():
    """List all permission rules"""
    from codesm.permission.store import get_store
    rules = get_store().get_rules()

    if not (rules.allowlist or rules.blocklist or rules.guarded_paths):
        console.print("[yellow]No permission rules defined.[/yellow]")
        return

    if rules.allowlist:
        table = Table(title="Allow List")
        table.add_column("Pattern", style="green")
        for p in rules.allowlist:
            table.add_row(p)
        console.print(table)
        console.print()

    if rules.blocklist:
        table = Table(title="Block List")
        table.add_column("Pattern", style="red")
        for p in rules.blocklist:
            table.add_row(p)
        console.print(table)
        console.print()
    
    if rules.guarded_paths:
        table = Table(title="Guarded Paths")
        table.add_column("Path Pattern", style="yellow")
        for p in rules.guarded_paths:
            table.add_row(p)
        console.print(table)

@app.command("add")
def add_rule(
    type: str = typer.Argument(..., help="Rule type: 'allow', 'block', or 'guard'"),
    pattern: str = typer.Argument(..., help="Command pattern or path glob"),
):
    """Add a permission rule"""
    from codesm.permission.store import get_store
    store = get_store()

    if type == "allow":
        store.add_allow(pattern)
        console.print(f"[green]Added allow rule: {pattern}[/green]")
    elif type == "block":
        store.add_block(pattern)
        console.print(f"[red]Added block rule: {pattern}[/red]")
    elif type == "guard":
        # Guarded paths
        if pattern not in store.get_rules().guarded_paths:
            store.get_rules().guarded_paths.append(pattern)
            store.save()
        console.print(f"[yellow]Added guarded path: {pattern}[/yellow]")
    else:
        console.print(f"[red]Invalid type: {type}. Use 'allow', 'block', or 'guard'.[/red]")

@app.command("remove")
def remove_rule(
    type: str = typer.Argument(..., help="Rule type: 'allow', 'block', or 'guard'"),
    pattern: str = typer.Argument(..., help="Command pattern or path glob"),
):
    """Remove a permission rule"""
    from codesm.permission.store import get_store
    store = get_store()

    if type == "allow":
        store.remove_allow(pattern)
        console.print(f"[green]Removed allow rule: {pattern}[/green]")
    elif type == "block":
        store.remove_block(pattern)
        console.print(f"[red]Removed block rule: {pattern}[/red]")
    elif type == "guard":
        rules = store.get_rules()
        if pattern in rules.guarded_paths:
            rules.guarded_paths.remove(pattern)
            store.save()
        console.print(f"[yellow]Removed guarded path: {pattern}[/yellow]")
    else:
        console.print(f"[red]Invalid type: {type}. Use 'allow', 'block', or 'guard'.[/red]")

@app.command("test")
def test_command(
    command: str = typer.Argument(..., help="Command to test"),
):
    """Test if a command is allowed or blocked"""
    from codesm.permission.permission import is_command_blocked
    from codesm.permission.store import get_store
    
    rules = get_store().get_rules()
    
    blocked, reason = is_command_blocked(
        command, 
        blocklist=rules.blocklist, 
        allowlist=rules.allowlist
    )
    
    if blocked:
        console.print(f"[red]BLOCKED[/red]: {reason}")
    else:
        console.print("[green]ALLOWED[/green]")
