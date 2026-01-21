"""Autocomplete/Tab completion for codesm TUI.

Triggered by:
- '@' -> file/directory completion
- '/' -> command completion (handled by command palette)

Features:
- Fuzzy matching
- Arrow key navigation
- Enter/Tab to select
- Escape to dismiss
"""

import os
import asyncio
from pathlib import Path
from typing import Callable
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, Input, OptionList
from textual.widgets.option_list import Option
from textual.screen import ModalScreen
from textual.binding import Binding
from textual import events

try:
    from thefuzz import fuzz
except ImportError:
    fuzz = None


@dataclass
class AutocompleteOption:
    """An autocomplete suggestion."""
    display: str
    value: str
    description: str = ""
    icon: str = ""


class AutocompletePopup(ModalScreen[str | None]):
    """Popup for autocomplete suggestions."""

    BINDINGS = [
        Binding("escape", "dismiss_popup", "Close", show=False),
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select_option", "Select", show=False),
        Binding("tab", "select_option", "Select", show=False),
    ]

    CSS = """
    AutocompletePopup {
        align: left bottom;
    }

    AutocompletePopup > #popup-container {
        width: 60;
        max-height: 12;
        background: $surface;
        border: solid $primary;
        padding: 0;
        margin-left: 4;
        margin-bottom: 4;
    }

    AutocompletePopup #filter-input {
        width: 100%;
        border: none;
        background: $panel;
        height: 1;
        padding: 0 1;
    }

    AutocompletePopup #options-list {
        height: auto;
        max-height: 10;
        background: $surface;
    }

    AutocompletePopup OptionList > .option-list--option-highlighted {
        background: $primary;
    }

    AutocompletePopup #empty-message {
        padding: 1;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(
        self,
        mode: str,  # "@" for files, "/" for commands
        workspace: Path,
        initial_filter: str = "",
        agents: list[str] | None = None,
    ):
        super().__init__()
        self.mode = mode
        self.workspace = workspace
        self.initial_filter = initial_filter
        self.agents = agents or []
        self._options: list[AutocompleteOption] = []
        self._filtered_options: list[AutocompleteOption] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="popup-container"):
            yield Input(
                value=self.initial_filter,
                placeholder="Type to filter..." if self.mode == "@" else "Command...",
                id="filter-input"
            )
            yield OptionList(id="options-list")

    async def on_mount(self):
        """Load initial options."""
        if self.mode == "@":
            self._options = await self._get_file_options()
            # Add agent options
            for agent in self.agents:
                self._options.insert(0, AutocompleteOption(
                    display=f"@{agent}",
                    value=f"@{agent}",
                    description="Subagent",
                    icon="🤖"
                ))
        else:
            self._options = self._get_command_options()
        
        self._apply_filter(self.initial_filter)
        self.query_one("#filter-input", Input).focus()

    async def _get_file_options(self) -> list[AutocompleteOption]:
        """Get file options from workspace."""
        options = []
        
        try:
            # Get files from workspace (limit to avoid performance issues)
            for root, dirs, files in os.walk(self.workspace):
                # Skip hidden and common ignored directories
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in (
                    'node_modules', '__pycache__', '.git', '.venv', 'venv', 
                    'dist', 'build', '.next', 'target'
                )]
                
                rel_root = Path(root).relative_to(self.workspace)
                
                # Add directories
                for d in dirs[:20]:  # Limit
                    rel_path = rel_root / d if str(rel_root) != '.' else Path(d)
                    options.append(AutocompleteOption(
                        display=f"{rel_path}/",
                        value=str(rel_path) + "/",
                        description="Directory",
                        icon="📁"
                    ))
                
                # Add files
                for f in files[:30]:  # Limit
                    if f.startswith('.'):
                        continue
                    rel_path = rel_root / f if str(rel_root) != '.' else Path(f)
                    ext = Path(f).suffix
                    icon = self._get_file_icon(ext)
                    options.append(AutocompleteOption(
                        display=str(rel_path),
                        value=str(rel_path),
                        description=ext or "File",
                        icon=icon
                    ))
                
                if len(options) > 100:
                    break
        except Exception:
            pass
        
        return options

    def _get_file_icon(self, ext: str) -> str:
        """Get icon for file extension."""
        icons = {
            '.py': '🐍',
            '.js': '📜',
            '.ts': '📘',
            '.tsx': '⚛️',
            '.jsx': '⚛️',
            '.json': '📋',
            '.md': '📝',
            '.txt': '📄',
            '.yaml': '⚙️',
            '.yml': '⚙️',
            '.toml': '⚙️',
            '.rs': '🦀',
            '.go': '🐹',
        }
        return icons.get(ext, '📄')

    def _get_command_options(self) -> list[AutocompleteOption]:
        """Get available commands."""
        commands = [
            AutocompleteOption("/new", "/new", "New session"),
            AutocompleteOption("/session", "/session", "Browse sessions"),
            AutocompleteOption("/models", "/models", "Select model"),
            AutocompleteOption("/mode", "/mode", "Switch mode"),
            AutocompleteOption("/smart", "/smart", "Smart mode"),
            AutocompleteOption("/rush", "/rush", "Rush mode"),
            AutocompleteOption("/connect", "/connect", "Connect provider"),
            AutocompleteOption("/theme", "/theme", "Change theme"),
            AutocompleteOption("/init", "/init", "Create AGENTS.md"),
            AutocompleteOption("/status", "/status", "Show status"),
            AutocompleteOption("/cost", "/cost", "Show cost/usage"),
            AutocompleteOption("/help", "/help", "Show help"),
        ]
        return commands

    def _apply_filter(self, query: str):
        """Apply fuzzy filter to options."""
        option_list = self.query_one("#options-list", OptionList)
        option_list.clear_options()
        
        if not query:
            self._filtered_options = self._options[:15]
        else:
            # Fuzzy filter
            scored = []
            query_lower = query.lower()
            
            for opt in self._options:
                display_lower = opt.display.lower()
                
                # Exact prefix match gets highest score
                if display_lower.startswith(query_lower):
                    scored.append((100, opt))
                # Contains match
                elif query_lower in display_lower:
                    scored.append((80, opt))
                # Fuzzy match if thefuzz is available
                elif fuzz:
                    ratio = fuzz.partial_ratio(query_lower, display_lower)
                    if ratio > 50:
                        scored.append((ratio, opt))
                # Simple subsequence match fallback
                else:
                    if self._subsequence_match(query_lower, display_lower):
                        scored.append((60, opt))
            
            scored.sort(key=lambda x: -x[0])
            self._filtered_options = [opt for _, opt in scored[:15]]
        
        # Populate option list
        for opt in self._filtered_options:
            label = f"{opt.icon} {opt.display}" if opt.icon else opt.display
            if opt.description:
                label = f"{label}  [dim]{opt.description}[/dim]"
            option_list.add_option(Option(label, id=opt.value))
        
        if self._filtered_options:
            option_list.highlighted = 0

    def _subsequence_match(self, query: str, target: str) -> bool:
        """Check if query is a subsequence of target."""
        qi = 0
        for char in target:
            if qi < len(query) and char == query[qi]:
                qi += 1
        return qi == len(query)

    def on_input_changed(self, event: Input.Changed):
        """Filter options as user types."""
        if event.input.id == "filter-input":
            self._apply_filter(event.value)

    def on_input_submitted(self, event: Input.Submitted):
        """Handle enter on input."""
        if event.input.id == "filter-input":
            self._select_current()

    def action_dismiss_popup(self):
        """Close without selection."""
        self.dismiss(None)

    def action_move_up(self):
        """Move selection up."""
        option_list = self.query_one("#options-list", OptionList)
        if option_list.highlighted is not None and option_list.highlighted > 0:
            option_list.highlighted -= 1

    def action_move_down(self):
        """Move selection down."""
        option_list = self.query_one("#options-list", OptionList)
        if option_list.highlighted is not None:
            if option_list.highlighted < len(self._filtered_options) - 1:
                option_list.highlighted += 1

    def action_select_option(self):
        """Select current option."""
        self._select_current()

    def _select_current(self):
        """Select the currently highlighted option."""
        option_list = self.query_one("#options-list", OptionList)
        if option_list.highlighted is not None and self._filtered_options:
            idx = option_list.highlighted
            if 0 <= idx < len(self._filtered_options):
                selected = self._filtered_options[idx]
                self.dismiss(selected.value)
                return
        self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        """Handle option click."""
        if event.option.id:
            self.dismiss(event.option.id)


class AutocompleteInput(Input):
    """Input widget with autocomplete support.
    
    Triggers autocomplete popup on '@' or '/' characters.
    """

    def __init__(
        self,
        workspace: Path | None = None,
        on_autocomplete: Callable[[str], None] | None = None,
        agents: list[str] | None = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.workspace = workspace or Path.cwd()
        self.on_autocomplete = on_autocomplete
        self.agents = agents or ["general"]
        self._autocomplete_active = False
        self._trigger_pos = 0

    def _on_key(self, event: events.Key) -> None:
        """Handle key events for autocomplete triggers."""
        # Let parent handle the key first
        super()._on_key(event)
        
        # Check for autocomplete triggers after the character is inserted
        if event.character == "@":
            self._show_autocomplete("@")
        # Note: "/" is handled by command palette in app.py

    def _show_autocomplete(self, mode: str):
        """Show autocomplete popup."""
        if self._autocomplete_active:
            return
        
        self._autocomplete_active = True
        self._trigger_pos = self.cursor_position

        async def show_popup():
            result = await self.app.push_screen_wait(
                AutocompletePopup(
                    mode=mode,
                    workspace=self.workspace,
                    initial_filter="",
                    agents=self.agents,
                )
            )
            self._autocomplete_active = False
            
            if result:
                # Insert the completion, replacing the trigger character
                current = self.value
                before_trigger = current[:self._trigger_pos - 1]  # Before '@'
                after_cursor = current[self.cursor_position:]
                
                # Add space after completion if not already there
                suffix = " " if not after_cursor.startswith(" ") else ""
                
                self.value = before_trigger + result + suffix + after_cursor
                self.cursor_position = len(before_trigger + result + suffix)
                
                if self.on_autocomplete:
                    self.on_autocomplete(result)

        self.app.call_later(lambda: asyncio.create_task(show_popup()))


def create_file_mention(path: str, workspace: Path) -> dict:
    """Create a file mention object for the prompt."""
    full_path = workspace / path
    return {
        "type": "file",
        "path": str(path),
        "full_path": str(full_path),
        "exists": full_path.exists(),
    }
