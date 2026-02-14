"""Format on save - auto-format files after edits using language-specific formatters."""

import asyncio
import logging
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class FormatterType(str, Enum):
    """Supported formatter types."""
    BLACK = "black"
    RUFF = "ruff"
    PRETTIER = "prettier"
    GOFMT = "gofmt"
    RUSTFMT = "rustfmt"
    CLANG_FORMAT = "clang-format"
    SHFMT = "shfmt"


@dataclass
class FormatResult:
    """Result of a format operation."""
    success: bool
    formatted: bool  # True if file was changed
    error: Optional[str] = None
    formatter: Optional[str] = None


# Mapping of file extensions to formatters (in priority order)
EXTENSION_FORMATTERS: dict[str, list[FormatterType]] = {
    # Python
    ".py": [FormatterType.RUFF, FormatterType.BLACK],
    ".pyi": [FormatterType.RUFF, FormatterType.BLACK],
    
    # JavaScript/TypeScript
    ".js": [FormatterType.PRETTIER],
    ".jsx": [FormatterType.PRETTIER],
    ".ts": [FormatterType.PRETTIER],
    ".tsx": [FormatterType.PRETTIER],
    ".mjs": [FormatterType.PRETTIER],
    ".cjs": [FormatterType.PRETTIER],
    
    # Web
    ".html": [FormatterType.PRETTIER],
    ".css": [FormatterType.PRETTIER],
    ".scss": [FormatterType.PRETTIER],
    ".less": [FormatterType.PRETTIER],
    ".json": [FormatterType.PRETTIER],
    ".yaml": [FormatterType.PRETTIER],
    ".yml": [FormatterType.PRETTIER],
    ".md": [FormatterType.PRETTIER],
    
    # Go
    ".go": [FormatterType.GOFMT],
    
    # Rust
    ".rs": [FormatterType.RUSTFMT],
    
    # C/C++
    ".c": [FormatterType.CLANG_FORMAT],
    ".cpp": [FormatterType.CLANG_FORMAT],
    ".cc": [FormatterType.CLANG_FORMAT],
    ".h": [FormatterType.CLANG_FORMAT],
    ".hpp": [FormatterType.CLANG_FORMAT],
    
    # Shell
    ".sh": [FormatterType.SHFMT],
    ".bash": [FormatterType.SHFMT],
}


# Formatter command configurations
FORMATTER_COMMANDS: dict[FormatterType, dict] = {
    FormatterType.BLACK: {
        "cmd": ["black", "--quiet", "{file}"],
        "check_cmd": ["black", "--version"],
    },
    FormatterType.RUFF: {
        "cmd": ["ruff", "format", "{file}"],
        "check_cmd": ["ruff", "--version"],
    },
    FormatterType.PRETTIER: {
        "cmd": ["prettier", "--write", "{file}"],
        "check_cmd": ["prettier", "--version"],
    },
    FormatterType.GOFMT: {
        "cmd": ["gofmt", "-w", "{file}"],
        "check_cmd": ["gofmt", "-h"],  # gofmt doesn't have --version
    },
    FormatterType.RUSTFMT: {
        "cmd": ["rustfmt", "{file}"],
        "check_cmd": ["rustfmt", "--version"],
    },
    FormatterType.CLANG_FORMAT: {
        "cmd": ["clang-format", "-i", "{file}"],
        "check_cmd": ["clang-format", "--version"],
    },
    FormatterType.SHFMT: {
        "cmd": ["shfmt", "-w", "{file}"],
        "check_cmd": ["shfmt", "--version"],
    },
}


class Formatter:
    """Handles file formatting with auto-detection of formatters."""
    
    def __init__(self):
        self._available_formatters: dict[FormatterType, bool] = {}
        self._enabled = True
        self._session_enabled: dict[str, bool] = {}
    
    def is_enabled(self, session_id: Optional[str] = None) -> bool:
        """Check if format on save is enabled."""
        if not self._enabled:
            return False
        if session_id and session_id in self._session_enabled:
            return self._session_enabled[session_id]
        return self._enabled
    
    def set_enabled(self, enabled: bool, session_id: Optional[str] = None):
        """Enable or disable format on save."""
        if session_id:
            self._session_enabled[session_id] = enabled
        else:
            self._enabled = enabled
    
    async def _check_formatter_available(self, formatter: FormatterType) -> bool:
        """Check if a formatter is available on the system."""
        if formatter in self._available_formatters:
            return self._available_formatters[formatter]
        
        config = FORMATTER_COMMANDS.get(formatter)
        if not config:
            self._available_formatters[formatter] = False
            return False
        
        # Check if command exists
        cmd_name = config["cmd"][0]
        if not shutil.which(cmd_name):
            logger.debug(f"Formatter not found in PATH: {cmd_name}")
            self._available_formatters[formatter] = False
            return False
        
        # Try running check command
        try:
            check_cmd = config.get("check_cmd", [cmd_name, "--version"])
            proc = await asyncio.create_subprocess_exec(
                *check_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            available = proc.returncode == 0
            self._available_formatters[formatter] = available
            return available
        except Exception as e:
            logger.debug(f"Formatter check failed for {formatter.value}: {e}")
            self._available_formatters[formatter] = False
            return False
    
    def get_formatters_for_file(self, file_path: Path) -> list[FormatterType]:
        """Get list of formatters for a file based on extension."""
        ext = file_path.suffix.lower()
        return EXTENSION_FORMATTERS.get(ext, [])
    
    async def find_available_formatter(self, file_path: Path) -> Optional[FormatterType]:
        """Find the first available formatter for a file."""
        formatters = self.get_formatters_for_file(file_path)
        
        for formatter in formatters:
            if await self._check_formatter_available(formatter):
                return formatter
        
        return None
    
    async def format_file(
        self,
        file_path: Path,
        formatter: Optional[FormatterType] = None,
    ) -> FormatResult:
        """Format a file using the appropriate formatter.
        
        Args:
            file_path: Path to the file to format
            formatter: Specific formatter to use, or None to auto-detect
            
        Returns:
            FormatResult with success status and any errors
        """
        file_path = Path(file_path).resolve()
        
        if not file_path.exists():
            return FormatResult(success=False, formatted=False, error="File not found")
        
        # Auto-detect formatter if not specified
        if formatter is None:
            formatter = await self.find_available_formatter(file_path)
        
        if formatter is None:
            # No formatter available - this is not an error, just skip
            return FormatResult(success=True, formatted=False)
        
        # Get original content for comparison
        try:
            original_content = file_path.read_text()
        except Exception as e:
            return FormatResult(success=False, formatted=False, error=str(e))
        
        # Build command
        config = FORMATTER_COMMANDS.get(formatter)
        if not config:
            return FormatResult(success=False, formatted=False, error=f"Unknown formatter: {formatter}")
        
        cmd = [
            arg.replace("{file}", str(file_path))
            for arg in config["cmd"]
        ]
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(file_path.parent if file_path.parent.exists() else Path.cwd()),
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                error_msg = stderr.decode().strip() or stdout.decode().strip()
                return FormatResult(
                    success=False,
                    formatted=False,
                    error=error_msg[:200],  # Truncate long errors
                    formatter=formatter.value,
                )
            
            # Check if file was modified
            try:
                new_content = file_path.read_text()
                formatted = new_content != original_content
            except Exception:
                formatted = False
            
            return FormatResult(
                success=True,
                formatted=formatted,
                formatter=formatter.value,
            )
            
        except FileNotFoundError:
            return FormatResult(
                success=False,
                formatted=False,
                error=f"Formatter not found: {config['cmd'][0]}",
            )
        except Exception as e:
            return FormatResult(
                success=False,
                formatted=False,
                error=str(e),
            )


# Global formatter instance
_formatter = Formatter()


async def format_file(
    file_path: Path,
    formatter: Optional[FormatterType] = None,
) -> FormatResult:
    """Format a file using the appropriate formatter."""
    return await _formatter.format_file(file_path, formatter)


async def format_file_if_enabled(
    file_path: Path,
    session_id: Optional[str] = None,
) -> Optional[FormatResult]:
    """Format a file if format on save is enabled.
    
    Returns None if formatting is disabled, FormatResult otherwise.
    """
    if not _formatter.is_enabled(session_id):
        return None
    return await _formatter.format_file(file_path)


def get_formatter() -> Formatter:
    """Get the global formatter instance."""
    return _formatter


def set_format_enabled(enabled: bool, session_id: Optional[str] = None):
    """Enable or disable format on save."""
    _formatter.set_enabled(enabled, session_id)


def is_format_enabled(session_id: Optional[str] = None) -> bool:
    """Check if format on save is enabled."""
    return _formatter.is_enabled(session_id)
