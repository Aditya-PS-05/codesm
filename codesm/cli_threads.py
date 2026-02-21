"""CLI commands for session management"""

import typer
from rich.console import Console
from rich.table import Table
from pathlib import Path
from codesm.session.session import Session

app = typer.Typer(help="Manage sessions/threads")
console = Console()

@app.command("list")
def list_sessions():
    """List all saved sessions"""
    sessions = Session.list_sessions()
    
    if not sessions:
        console.print("[yellow]No sessions found[/yellow]")
        return

    table = Table(title="Saved Sessions")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", style="green")
    table.add_column("Updated", style="magenta")
    table.add_column("Topics", style="blue")

    for s in sessions:
        topics = ""
        if s.get("topics"):
            t = s["topics"]
            topics = f"{t.get('primary', '')}"
        
        table.add_row(
            s["id"],
            s["title"],
            str(s.get("updated_at", "")),
            topics
        )

    console.print(table)

@app.command("new")
def new_session(
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Working directory for the session")
):
    """Create a new session"""
    session = Session.create(directory)
    console.print(f"[green]Created new session:[/green] {session.id}")
    console.print(f"Run 'codesm threads continue {session.id}' to start")

@app.command("continue")
def continue_session(
    session_id: str = typer.Argument(..., help="Session ID to resume")
):
    """Resume an existing session"""
    from codesm.tui.app import CodesmApp
    from codesm.auth.credentials import CredentialStore
    
    session = Session.load(session_id)
    if not session:
        console.print(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(1)
        
    store = CredentialStore()
    model = store.get_preferred_model() or "anthropic/claude-sonnet-4-20250514"
    
    app = CodesmApp(directory=session.directory, model=model, session_id=session_id)
    app.run()

@app.command("export")
def export_session(
    session_id: str = typer.Argument(..., help="Session ID"),
    format: str = typer.Option("markdown", "--format", "-f", help="Output format (markdown, json)"),
    output: Path = typer.Option(None, "--output", "-o", help="Output file path (default: stdout)"),
):
    """Export a session to Markdown or JSON"""
    session = Session.load(session_id)
    if not session:
        console.print(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(1)
        
    result = ""
    
    if format.lower() == "json":
        import json
        # Filter out internal keys starting with _
        clean_messages = []
        for msg in session.messages:
            clean_msg = {k: v for k, v in msg.items() if not k.startswith("_")}
            clean_messages.append(clean_msg)
            
        data = {
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at.isoformat(),
            "messages": clean_messages
        }
        result = json.dumps(data, indent=2)
        
    else:  # markdown
        result = f"# {session.title}\n\n"
        result += f"*Created: {session.created_at}*\n\n"
        
        for msg in session.get_messages_for_display():
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            
            if role == "user":
                result += f"## User\n\n{content}\n\n"
            elif role == "assistant":
                result += f"## codesm\n\n{content}\n\n"
            elif role == "tool_display":
                result += f"### Tool: {msg.get('tool_name')}\n\n```\n{content}\n```\n\n"
                
    if output:
        output.write_text(result, encoding="utf-8")
        console.print(f"[green]Exported to {output}[/green]")
    else:
        print(result)


@app.command("share")
def share_session(
    session_id: str = typer.Argument(..., help="Session ID"),
    port: int = typer.Option(4096, "--port", "-p", help="Server port"),
):
    """Share a session via local URL"""
    session = Session.load(session_id)
    if not session:
        console.print(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(1)
        
    url = f"http://localhost:{port}/session/{session_id}"
    console.print(f"[green]Session available at:[/green] {url}")
    console.print("[dim]Ensure the server is running with 'codesm serve'[/dim]")
    
    import webbrowser
    if typer.confirm("Open in browser?"):
        webbrowser.open(url)


@app.command("rename")
def rename_session(
    session_id: str = typer.Argument(..., help="Session ID"),
    name: str = typer.Argument(..., help="New name for the session")
):
    """Rename a session"""
    session = Session.load(session_id)
    if not session:
        console.print(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(1)
        
    session.set_title(name)
    console.print(f"[green]Renamed session to:[/green] {name}")

@app.command("delete")
def delete_session(
    session_id: str = typer.Argument(..., help="Session ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Delete without confirmation")
):
    """Delete a session"""
    if not force:
        if not typer.confirm(f"Are you sure you want to delete session {session_id}?"):
            raise typer.Abort()
            
    if Session.delete_by_id(session_id):
        console.print(f"[green]Deleted session {session_id}[/green]")
    else:
        console.print(f"[red]Failed to delete session {session_id}[/red]")
