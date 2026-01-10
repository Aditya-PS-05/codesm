"""LSP client implementation using JSON-RPC over stdin/stdout"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field

from .servers import ServerConfig

logger = logging.getLogger(__name__)


@dataclass
class Diagnostic:
    path: str
    line: int
    column: int
    message: str
    severity: str  # "error", "warning", "info", "hint"
    source: Optional[str] = None


@dataclass
class LSPClient:
    config: ServerConfig
    root_path: str
    process: Optional[asyncio.subprocess.Process] = None
    _request_id: int = 0
    _pending: dict[int, asyncio.Future] = field(default_factory=dict)
    _diagnostics: dict[str, list[Diagnostic]] = field(default_factory=dict)
    _reader_task: Optional[asyncio.Task] = None
    _initialized: bool = False

    async def start(self) -> bool:
        """Start the language server process."""
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.config.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root_path,
            )
            self._reader_task = asyncio.create_task(self._read_messages())
            return True
        except FileNotFoundError:
            logger.warning(f"LSP server not found: {self.config.command[0]}")
            return False
        except Exception as e:
            logger.error(f"Failed to start LSP server: {e}")
            return False

    async def initialize(self) -> bool:
        """Send initialize request to the server."""
        if not self.process:
            return False

        # Convert to absolute path before creating URI
        root_path_abs = Path(self.root_path).resolve()
        root_uri = root_path_abs.as_uri()

        result = await self._request("initialize", {
            "processId": None,
            "rootUri": root_uri,
            "rootPath": str(root_path_abs),
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {
                        "relatedInformation": True,
                    },
                    "synchronization": {
                        "didOpen": True,
                        "didChange": True,
                        "didClose": True,
                    },
                },
            },
            "workspaceFolders": [
                {"uri": root_uri, "name": root_path_abs.name}
            ],
        })
        
        if result is not None:
            await self._notify("initialized", {})
            self._initialized = True
            return True
        return False

    async def did_open(self, path: str, text: Optional[str] = None) -> None:
        """Notify the server that a file was opened."""
        if not self._initialized:
            return

        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = Path(self.root_path).resolve() / path
        else:
            file_path = file_path.resolve()

        if text is None:
            try:
                text = file_path.read_text()
            except Exception as e:
                logger.warning(f"Failed to read file {path}: {e}")
                return

        language_id = self._get_language_id(str(file_path))

        await self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": file_path.as_uri(),
                "languageId": language_id,
                "version": 1,
                "text": text,
            }
        })

    async def did_change(self, path: str, text: str) -> None:
        """Notify the server that a file changed."""
        if not self._initialized:
            return

        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = Path(self.root_path).resolve() / path
        else:
            file_path = file_path.resolve()

        await self._notify("textDocument/didChange", {
            "textDocument": {
                "uri": file_path.as_uri(),
                "version": 2,
            },
            "contentChanges": [{"text": text}],
        })

    def get_diagnostics(self, path: Optional[str] = None) -> list[Diagnostic]:
        """Get diagnostics, optionally filtered by path."""
        if path:
            return self._diagnostics.get(path, [])
        
        all_diags = []
        for diags in self._diagnostics.values():
            all_diags.extend(diags)
        return all_diags

    async def shutdown(self) -> None:
        """Shutdown the language server."""
        if not self.process:
            return

        try:
            if self._initialized:
                await self._request("shutdown", None, timeout=5.0)
                await self._notify("exit", None)
        except Exception:
            pass

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self.process:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self.process.kill()

        self._initialized = False

    async def _request(
        self, method: str, params: Any, timeout: float = 30.0
    ) -> Optional[Any]:
        """Send a request and wait for response."""
        if not self.process or not self.process.stdin:
            return None

        self._request_id += 1
        request_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        self._send_message(message)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            logger.warning(f"LSP request timed out: {method}")
            return None

    async def _notify(self, method: str, params: Any) -> None:
        """Send a notification (no response expected)."""
        if not self.process or not self.process.stdin:
            return

        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self._send_message(message)

    def _send_message(self, message: dict) -> None:
        """Send a JSON-RPC message."""
        if not self.process or not self.process.stdin:
            return

        content = json.dumps(message)
        header = f"Content-Length: {len(content)}\r\n\r\n"
        
        try:
            self.process.stdin.write(header.encode() + content.encode())
        except Exception as e:
            logger.error(f"Failed to send LSP message: {e}")

    async def _read_messages(self) -> None:
        """Read and handle messages from the server."""
        if not self.process or not self.process.stdout:
            return

        try:
            while True:
                header = await self._read_header()
                if header is None:
                    break

                content_length = self._parse_content_length(header)
                if content_length is None:
                    continue

                content = await self.process.stdout.read(content_length)
                if not content:
                    break

                try:
                    message = json.loads(content.decode())
                    await self._handle_message(message)
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON from LSP server: {e}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"LSP reader error: {e}")

    async def _read_header(self) -> Optional[bytes]:
        """Read the header section of a message."""
        if not self.process or not self.process.stdout:
            return None

        header = b""
        while True:
            line = await self.process.stdout.readline()
            if not line:
                return None
            header += line
            if header.endswith(b"\r\n\r\n"):
                return header

    def _parse_content_length(self, header: bytes) -> Optional[int]:
        """Parse Content-Length from header."""
        for line in header.decode().split("\r\n"):
            if line.lower().startswith("content-length:"):
                try:
                    return int(line.split(":", 1)[1].strip())
                except ValueError:
                    return None
        return None

    async def _handle_message(self, message: dict) -> None:
        """Handle an incoming message."""
        if "id" in message and "result" in message:
            # Response
            request_id = message["id"]
            if request_id in self._pending:
                self._pending[request_id].set_result(message.get("result"))
                del self._pending[request_id]

        elif "id" in message and "error" in message:
            # Error response
            request_id = message["id"]
            if request_id in self._pending:
                error = message["error"]
                logger.warning(f"LSP error: {error.get('message', error)}")
                self._pending[request_id].set_result(None)
                del self._pending[request_id]

        elif "method" in message:
            # Notification or request from server
            method = message["method"]
            params = message.get("params", {})

            if method == "textDocument/publishDiagnostics":
                self._handle_diagnostics(params)
            elif method == "window/logMessage":
                level = params.get("type", 4)
                msg = params.get("message", "")
                if level <= 2:
                    logger.warning(f"LSP: {msg}")

    def _handle_diagnostics(self, params: dict) -> None:
        """Handle publishDiagnostics notification."""
        uri = params.get("uri", "")
        diagnostics = params.get("diagnostics", [])

        # Convert URI to path
        if uri.startswith("file://"):
            path = uri[7:]
        else:
            path = uri

        severity_map = {1: "error", 2: "warning", 3: "info", 4: "hint"}

        self._diagnostics[path] = [
            Diagnostic(
                path=path,
                line=d.get("range", {}).get("start", {}).get("line", 0) + 1,
                column=d.get("range", {}).get("start", {}).get("character", 0) + 1,
                message=d.get("message", ""),
                severity=severity_map.get(d.get("severity", 4), "hint"),
                source=d.get("source"),
            )
            for d in diagnostics
        ]

    def _get_language_id(self, path: str) -> str:
        """Get the language ID for a file path."""
        ext_map = {
            ".py": "python",
            ".pyi": "python",
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".js": "javascript",
            ".jsx": "javascriptreact",
            ".rs": "rust",
            ".go": "go",
        }
        for ext, lang in ext_map.items():
            if path.endswith(ext):
                return lang
        return "plaintext"
