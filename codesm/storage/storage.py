"""File-based storage"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class Storage:
    BASE_DIR = Path(os.environ.get("CODESM_DATA_DIR", Path.home() / ".local" / "share" / "codesm")).expanduser()
    
    @classmethod
    def _key_to_path(cls, key: list[str]) -> Path:
        return cls.BASE_DIR / f"{'/'.join(key)}.json"
    
    @classmethod
    def write(cls, key: list[str], data: Any):
        """Write data to storage"""
        path = cls._key_to_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=2, default=str)
        fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    
    @classmethod
    def read(cls, key: list[str]) -> Any | None:
        """Read data from storage"""
        path = cls._key_to_path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    
    @classmethod
    def delete(cls, key: list[str]):
        """Delete data from storage"""
        path = cls._key_to_path(key)
        if path.exists():
            path.unlink()
    
    @classmethod
    def list(cls, prefix: list[str]) -> list[list[str]]:
        """List all keys with given prefix"""
        dir_path = cls.BASE_DIR / "/".join(prefix) if prefix else cls.BASE_DIR
        if not dir_path.exists():
            return []

        keys = []
        for path in dir_path.rglob("*.json"):
            rel = path.relative_to(cls.BASE_DIR)
            key = list(rel.with_suffix("").parts)
            keys.append(key)
        return keys
