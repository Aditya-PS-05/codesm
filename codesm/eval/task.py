"""Eval task file loader.

Task files are YAML with this shape:

    name: "add-docstring"
    description: "Add a docstring to the hello() function in /tmp/test.py"
    setup:
      - |
        cat > /tmp/test.py <<'EOF'
        def hello():
            return 'world'
        EOF
    prompt: "Add a docstring to the hello() function in /tmp/test.py"
    assertion:
      - grep '\"\"\"' /tmp/test.py
    model: "anthropic/claude-sonnet-4-20250514"  # optional
    directory: "/tmp"  # optional working directory
    max_iterations: 20  # optional ReAct loop cap
    timeout: 300  # optional wall clock cap in seconds
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class EvalTask:
    """Parsed eval task.

    A task has three shell hooks plus one agent prompt:

      setup     shell commands run before the agent, in order
      prompt    the user message handed to the agent
      assertion shell commands run after the agent; all must exit 0 to pass
    """

    name: str
    description: str
    prompt: str
    setup: list[str] = field(default_factory=list)
    assertion: list[str] = field(default_factory=list)
    model: Optional[str] = None
    directory: Optional[str] = None
    max_iterations: int = 20
    timeout: int = 300


def load_task(path: Path) -> EvalTask:
    """Load a task file from disk.

    Raises FileNotFoundError if the path does not exist and ValueError if
    the file is not valid YAML or is missing required fields.
    """
    import yaml

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Task file not found: {path}")

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Task file {path} must be a YAML mapping, got {type(data).__name__}")

    missing = [k for k in ("name", "prompt") if k not in data]
    if missing:
        raise ValueError(f"Task file {path} missing required fields: {missing}")

    setup = data.get("setup") or []
    if isinstance(setup, str):
        setup = [setup]

    assertion = data.get("assertion") or []
    if isinstance(assertion, str):
        assertion = [assertion]

    return EvalTask(
        name=str(data["name"]),
        description=str(data.get("description", "")),
        prompt=str(data["prompt"]),
        setup=[str(s) for s in setup],
        assertion=[str(a) for a in assertion],
        model=data.get("model"),
        directory=data.get("directory"),
        max_iterations=int(data.get("max_iterations", 20)),
        timeout=int(data.get("timeout", 300)),
    )
