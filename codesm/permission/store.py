"""Persistence for permission rules"""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict

CONFIG_DIR = Path.home() / ".config" / "codesm"
PERMISSIONS_FILE = CONFIG_DIR / "permissions.json"

@dataclass
class PermissionRules:
    allowlist: list[str] = field(default_factory=list)
    blocklist: list[str] = field(default_factory=list)
    guarded_paths: list[str] = field(default_factory=list)

class PermissionStore:
    def __init__(self, path: Path = PERMISSIONS_FILE):
        self.path = path
        self._rules = PermissionRules()
        self.load()

    def load(self):
        if not self.path.exists():
            return
        
        try:
            data = json.loads(self.path.read_text())
            self._rules = PermissionRules(
                allowlist=data.get("allowlist", []),
                blocklist=data.get("blocklist", []),
                guarded_paths=data.get("guarded_paths", [])
            )
        except Exception:
            # Fallback to empty if corrupt
            self._rules = PermissionRules()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(self._rules), indent=2))

    def get_rules(self) -> PermissionRules:
        return self._rules

    def add_allow(self, pattern: str):
        if pattern not in self._rules.allowlist:
            self._rules.allowlist.append(pattern)
            self.save()

    def remove_allow(self, pattern: str):
        if pattern in self._rules.allowlist:
            self._rules.allowlist.remove(pattern)
            self.save()

    def add_block(self, pattern: str):
        if pattern not in self._rules.blocklist:
            self._rules.blocklist.append(pattern)
            self.save()

    def remove_block(self, pattern: str):
        if pattern in self._rules.blocklist:
            self._rules.blocklist.remove(pattern)
            self.save()

# Global store
_store = PermissionStore()

def get_store() -> PermissionStore:
    return _store
