"""Pytest configuration and shared fixtures"""

import pytest
import tempfile
import os
from pathlib import Path

# Set test storage directory to avoid polluting user data
os.environ["CODESM_DATA_DIR"] = tempfile.mkdtemp()


@pytest.fixture(autouse=True)
def clean_storage():
    """Clean storage before each test"""
    from codesm.storage.storage import Storage

    # Use a fresh temp dir for each test
    Storage.BASE_DIR = Path(tempfile.mkdtemp())
    yield


@pytest.fixture(autouse=True)
def disable_diff_preview():
    """Without a TUI, the diff preview modal creates a Future that
    never resolves, so any tool that calls request_diff_preview would
    hang forever. Disable it globally for the test session."""
    from codesm.diff_preview import set_diff_preview_enabled
    set_diff_preview_enabled(False)
    yield
    set_diff_preview_enabled(True)


@pytest.fixture(autouse=True)
def isolate_global_rules(monkeypatch):
    """RulesDiscovery hardcodes ~/.claude/CLAUDE.md and similar global
    locations. On a developer machine those files exist and leak into
    tests that expect a clean workspace. Point the global locations at
    a nonexistent path for the duration of each test."""
    from codesm.rules import discovery
    nowhere = Path(tempfile.mkdtemp()) / "no_such_global_rules.md"
    monkeypatch.setattr(discovery, "GLOBAL_LOCATIONS", [nowhere])
