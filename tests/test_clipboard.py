"""Clipboard backend failures must reach the fallback, not report a false copy."""

import subprocess
from types import SimpleNamespace

import pytest

from codesm.tui.clipboard import copy_text, copy_to_system_clipboard


@pytest.mark.parametrize("xclip_status", [0, 1])
def test_copy_checks_backend_exit_status_without_capturing_owner_pipes(monkeypatch, xclip_status):
    attempted = []
    text = "Selected 界🙂\nsecond line"

    def run(command, **kwargs):
        attempted.append(command)
        assert kwargs["input"] == text.encode("utf-8")
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 2
        if command[0] == "wl-copy":
            raise subprocess.TimeoutExpired(command, 2)
        if command[0] in {"xsel", "pbcopy"}:
            raise FileNotFoundError(command[0])
        return subprocess.CompletedProcess(command, xclip_status)

    monkeypatch.setattr(subprocess, "run", run)
    assert copy_to_system_clipboard(text) is (xclip_status == 0)
    assert attempted[0] == ["wl-copy", "--type", "text/plain;charset=utf-8", "--"]
    assert [command[0] for command in attempted] == (
        ["wl-copy", "xsel", "xclip"] + (["pbcopy"] if xclip_status else [])
    )


def test_unconfirmed_terminal_copy_does_not_report_desktop_success(monkeypatch):
    copied, notifications = [], []
    app = SimpleNamespace(
        copy_to_clipboard=copied.append,
        notify=lambda message, **kwargs: notifications.append(message),
    )
    monkeypatch.setattr("codesm.tui.clipboard.copy_to_system_clipboard", lambda text: False)
    copy_text(app, "selected text")
    assert copied == ["selected text"]
    assert notifications == ["Sent copy request to terminal"]
