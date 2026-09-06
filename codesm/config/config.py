"""Configuration management"""

from pathlib import Path
from pydantic import BaseModel, Field
from typing import Any, Literal
import json
import os


class ProviderConfig(BaseModel):
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    models: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    name: str = ""
    model: str | None = None
    prompt: str | None = None
    tools: dict[str, bool] = Field(default_factory=dict)
    permissions: dict[str, str] = Field(default_factory=dict)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    max_iterations: int = Field(default=25, ge=1, le=200)
    max_output_tokens: int = Field(default=8192, ge=1)
    context_tokens: int = Field(default=128000, ge=2048)


class ModelPrice(BaseModel):
    input: float = Field(ge=0, allow_inf_nan=False)
    output: float = Field(ge=0, allow_inf_nan=False)


class Config(BaseModel):
    backend: Literal["native", "claude-code", "codex", "claude-science"] = "native"
    backend_models: dict[str, str] = Field(default_factory=dict)
    model: str = "anthropic/claude-sonnet-5"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    routing_models: dict[str, str] = Field(default_factory=dict)
    model_prices: dict[str, ModelPrice] = Field(default_factory=dict)
    pin_model: bool = False
    read_only: bool = False
    denied_tools: list[str] = Field(default_factory=list)
    budget_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_requests: int = Field(default=200, ge=1)
    max_task_tokens: int | None = Field(default=None, ge=1)
    delegation: Literal["single", "specialists", "adaptive"] = "adaptive"
    
    @classmethod
    def load(cls, path: Path | None = None, directory: Path | None = None) -> "Config":
        """Load config from file"""
        if path is None and os.environ.get("CODESM_CONFIG"):
            path = Path(os.environ["CODESM_CONFIG"]).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"CODESM_CONFIG does not exist: {path}")
        if path is None:
            # Look for codesm.json in current dir or home
            candidates = [
                (directory or Path.cwd()) / "codesm.json",
                Path.home() / ".config" / "codesm" / "config.json",
            ]
            for p in candidates:
                if p.exists():
                    path = p
                    break
        
        if path and path.exists():
            data = json.loads(path.read_text())
            return cls(**data)
        
        return cls()
    
    def save(self, path: Path):
        """Save config to file"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))
