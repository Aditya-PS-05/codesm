"""Credential storage for providers"""

import json
from pathlib import Path
from typing import Optional


class CredentialStore:
    """Simple file-based credential storage"""

    def __init__(self):
        self.config_dir = Path.home() / ".config" / "codesm"
        self.credentials_file = self.config_dir / "credentials.json"
        self._ensure_config_dir()

    def _ensure_config_dir(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if self.credentials_file.exists():
            try:
                return json.loads(self.credentials_file.read_text())
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save(self, data: dict):
        self.credentials_file.write_text(json.dumps(data, indent=2))
        self.credentials_file.chmod(0o600)

    def get(self, provider: str) -> Optional[dict]:
        """Get credentials for a provider"""
        data = self._load()
        return data.get(provider)

    def set(self, provider: str, credentials: dict):
        """Set credentials for a provider"""
        data = self._load()
        data[provider] = credentials
        self._save(data)

    def delete(self, provider: str):
        """Delete credentials for a provider"""
        data = self._load()
        if provider in data:
            del data[provider]
            self._save(data)

    def get_api_key(self, provider: str) -> Optional[str]:
        """Get API key for a provider"""
        creds = self.get(provider)
        if creds:
            return creds.get("api_key")
        return None

    def is_authenticated(self, provider: str) -> bool:
        """Check if provider is authenticated"""
        creds = self.get(provider)
        return creds is not None and ("api_key" in creds or "access_token" in creds)

    def get_preferred_model(self) -> Optional[str]:
        """Get the user's preferred model"""
        data = self._load()
        return data.get("_preferences", {}).get("model")

    def set_preferred_model(self, model: str):
        """Set the user's preferred model"""
        data = self._load()
        if "_preferences" not in data:
            data["_preferences"] = {}
        data["_preferences"]["model"] = model
        self._save(data)

    def get_preferred_theme(self) -> Optional[str]:
        """Get the user's preferred theme"""
        data = self._load()
        return data.get("_preferences", {}).get("theme")

    def set_preferred_theme(self, theme: str):
        """Set the user's preferred theme"""
        data = self._load()
        if "_preferences" not in data:
            data["_preferences"] = {}
        data["_preferences"]["theme"] = theme
        self._save(data)
