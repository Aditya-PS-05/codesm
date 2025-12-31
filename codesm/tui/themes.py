"""Theme system for codesm TUI - OpenCode inspired themes"""

from textual.theme import Theme

# OpenCode-style monochromatic dark theme with blue/cyan accents
CODESM_DARK = Theme(
    name="codesm-dark",
    primary="#3d4550",
    secondary="#5dd9c1",  # Cyan accent for borders
    accent="#5dd9c1",
    foreground="#d4d4d4",
    background="#0d1117",  # Near-black background
    surface="#161b22",  # Slightly lighter for panels
    panel="#21262d",  # For sidebar and elevated elements
    success="#3fb950",
    warning="#d29922",
    error="#f85149",
    dark=True,
    variables={
        "modal-bg": "#161b22",
        "highlight": "#5dd9c1",
        "muted": "#8b949e",
        "text-muted": "#8b949e",
        "input-bg": "#0d1117",
        "border-color": "#30363d",
    },
)

CODESM_LIGHT = Theme(
    name="codesm-light",
    primary="#57606a",
    secondary="#0969da",
    accent="#0969da",
    foreground="#24292f",
    background="#ffffff",
    surface="#f6f8fa",
    panel="#eaeef2",
    success="#1a7f37",
    warning="#9a6700",
    error="#cf222e",
    dark=False,
    variables={
        "modal-bg": "#ffffff",
        "highlight": "#0969da",
        "muted": "#57606a",
        "text-muted": "#57606a",
        "input-bg": "#ffffff",
        "border-color": "#d0d7de",
    },
)

CODESM_OCEAN = Theme(
    name="codesm-ocean",
    primary="#4c566a",
    secondary="#88c0d0",
    accent="#88c0d0",
    foreground="#d8dee9",
    background="#2e3440",
    surface="#3b4252",
    panel="#434c5e",
    success="#a3be8c",
    warning="#ebcb8b",
    error="#bf616a",
    dark=True,
    variables={
        "modal-bg": "#3b4252",
        "highlight": "#88c0d0",
        "muted": "#4c566a",
        "text-muted": "#4c566a",
        "input-bg": "#2e3440",
        "border-color": "#4c566a",
    },
)

CODESM_DRACULA = Theme(
    name="codesm-dracula",
    primary="#6272a4",
    secondary="#8be9fd",
    accent="#8be9fd",
    foreground="#f8f8f2",
    background="#1e1f29",
    surface="#282a36",
    panel="#21222c",
    success="#50fa7b",
    warning="#ffb86c",
    error="#ff5555",
    dark=True,
    variables={
        "modal-bg": "#2d2f3d",
        "highlight": "#8be9fd",
        "muted": "#6272a4",
        "text-muted": "#6272a4",
        "input-bg": "#282a36",
        "border-color": "#6272a4",
    },
)

THEMES = {
    "dark": CODESM_DARK,
    "light": CODESM_LIGHT,
    "ocean": CODESM_OCEAN,
    "dracula": CODESM_DRACULA,
}

THEME_NAMES = list(THEMES.keys())


def get_next_theme(current: str) -> str:
    """Get the next theme in the rotation"""
    try:
        idx = THEME_NAMES.index(current)
        return THEME_NAMES[(idx + 1) % len(THEME_NAMES)]
    except ValueError:
        return THEME_NAMES[0]
