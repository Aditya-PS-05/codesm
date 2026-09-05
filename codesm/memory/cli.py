"""CLI commands for memory management"""

import typer
from pathlib import Path
from typing import Optional
from rich.console import Console

from .store import MemoryStore
from codesm.util.project_id import get_project_id

console = Console()
memory_app = typer.Typer(help="Manage cross-session memory")


@memory_app.command("list")
def list_memories(
    project: bool = typer.Option(False, "--project", "-p", help="Show project memory only"),
    global_only: bool = typer.Option(False, "--global", "-g", help="Show global memory only"),
):
    """List stored memories"""
    store = MemoryStore()
    
    if global_only:
        items = store.list(None)
        console.print("[bold]Global memories:[/bold]")
    elif project:
        project_id = get_project_id(Path.cwd())
        items = store.list(project_id)
        console.print(f"[bold]Project memories ({project_id}):[/bold]")
    else:
        global_items = store.list(None)
        project_id = get_project_id(Path.cwd())
        project_items = store.list(project_id)
        items = global_items + project_items
        console.print("[bold]All memories:[/bold]")
    
    if not items:
        console.print("  [dim](no memories)[/dim]")
        return
    
    for item in items:
        scope = "global" if item.project_id is None else "project"
        text_preview = item.text[:60] + "..." if len(item.text) > 60 else item.text
        console.print(f"  [cyan][{item.id[:8]}][/cyan] ({item.type}, {scope}): {text_preview}")


@memory_app.command("forget")
def forget_memory(
    memory_id: str = typer.Argument(..., help="Memory ID to delete"),
):
    """Delete a specific memory by ID"""
    store = MemoryStore()
    store.delete(memory_id, None)
    project_id = get_project_id(Path.cwd())
    store.delete(memory_id, project_id)
    console.print(f"[green]Deleted memory {memory_id}[/green]")


@memory_app.command("clear")
def clear_memories(
    project: bool = typer.Option(False, "--project", "-p", help="Clear project memory"),
    global_only: bool = typer.Option(False, "--global", "-g", help="Clear global memory"),
    all_memories: bool = typer.Option(False, "--all", "-a", help="Clear all memory"),
):
    """Clear stored memories"""
    store = MemoryStore()
    
    if all_memories or (not project and not global_only):
        for item in store.list(None):
            store.delete(item.id, None)
        project_id = get_project_id(Path.cwd())
        for item in store.list(project_id):
            store.delete(item.id, project_id)
        console.print("[green]Cleared all memories[/green]")
    elif global_only:
        for item in store.list(None):
            store.delete(item.id, None)
        console.print("[green]Cleared global memories[/green]")
    elif project:
        project_id = get_project_id(Path.cwd())
        for item in store.list(project_id):
            store.delete(item.id, project_id)
        console.print("[green]Cleared project memories[/green]")


@memory_app.command("add")
def add_memory(
    text: str = typer.Argument(..., help="Memory text to store"),
    memory_type: str = typer.Option("fact", "--type", "-t", help="Memory type: preference, fact, pattern, solution"),
    global_memory: bool = typer.Option(False, "--global", "-g", help="Store as global memory"),
):
    """Add a memory manually"""
    from .models import MemoryItem
    import uuid
    
    store = MemoryStore()
    project_id = None if global_memory else get_project_id(Path.cwd())
    
    item = MemoryItem(
        id=str(uuid.uuid4())[:8],
        type=memory_type,  # type: ignore
        text=text,
        project_id=project_id,
    )
    store.upsert(item)
    
    scope = "global" if global_memory else "project"
    console.print(f"[green]Added {memory_type} memory ({scope}): {text[:50]}...[/green]")


@memory_app.command("search")
def search_history(
    query: str = typer.Argument(..., help="Keywords, error text, file name, or command"),
    directory: Path = typer.Option(Path("."), "--directory", "-d", help="Project directory"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="Restrict to a session"),
    limit: int = typer.Option(8, "--limit", min=1, max=50),
):
    """Search local conversations, tool results, and execution logs; no API calls."""
    from .history import HistoryStore
    history = HistoryStore()
    history.import_project(directory)
    console.print_json(data=history.search(directory, query, session, limit))


@memory_app.command("read")
def read_history(
    record_id: int = typer.Argument(..., min=1, help="Record ID returned by memory search"),
    directory: Path = typer.Option(Path("."), "--directory", "-d"),
    offset: int = typer.Option(0, "--offset", min=0, help="Continue from next_offset in a previous page"),
):
    """Read an exact archived record, including long tool output, one page at a time."""
    from .history import HistoryStore
    result = HistoryStore().read(directory, record_id, offset)
    if result is None:
        console.print("No record found in this project.")
        raise typer.Exit(1)
    console.print_json(data=result)


@memory_app.command("reindex")
def reindex_history(
    directory: Path = typer.Option(Path("."), "--directory", "-d"),
):
    """Refresh the search index from this project's session JSON and execution logs."""
    from .history import HistoryStore
    HistoryStore().import_project(directory, force=True)
    console.print("Local session and event history indexed.")
