"""Bash command tool"""

import asyncio
import time
from pathlib import Path
from .base import Tool


class BashTool(Tool):
    name = "bash"
    description = "Execute shell commands. Use for builds, git, tests, etc."
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory (optional)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 120)",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview command without executing (default: false)",
                    "default": False,
                },
            },
            "required": ["command"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        start_time = time.time()
        
        command = args["command"]
        cwd = args.get("cwd") or context.get("cwd", ".")
        timeout = args.get("timeout", 120)
        dry_run = args.get("dry_run", False)
        
        # Check global dry_run mode
        if context.get("dry_run", False):
            dry_run = True
        
        session_id = context.get("session_id", "default")
        
        # Audit log the command
        try:
            from codesm.audit import audit_tool_call
            audit_tool_call("bash", {"command": command, "cwd": str(cwd)}, session_id)
        except ImportError:
            pass
        
        # Check command blocklist first (always blocked)
        try:
            from codesm.permission import check_command_permission, CommandBlockedError
            check_command_permission(command)
        except CommandBlockedError as e:
            return f"Error: {e.reason}\n\nThis command is blocked by security policy."
        except ImportError:
            pass
        
        # DRY RUN: Show what would be executed
        if dry_run:
            result = f"**[DRY RUN] Bash Preview**\n\n```bash\n$ {command}\n```\n\n**Working directory:** {cwd}\n**Timeout:** {timeout}s\n\n*Run with dry_run=false to execute*"
            
            try:
                from codesm.audit import audit_tool_result
                audit_tool_result("bash", True, f"dry_run: {command[:50]}", session_id=session_id)
            except:
                pass
            
            return result
        
        # Check if command requires permission
        from codesm.permission import requires_permission, ask_permission, PermissionDeniedError
        
        needs_permission, perm_type, reason = requires_permission(command)
        if needs_permission:
            try:
                await ask_permission(
                    session_id=session_id,
                    type=perm_type,
                    command=command,
                    title=f"Allow {perm_type} command?",
                    description=f"The agent wants to execute:\n\n```\n{command}\n```\n\nReason: {reason}",
                    metadata={"command": command, "cwd": cwd},
                )
            except PermissionDeniedError as e:
                # Audit permission denied
                try:
                    from codesm.audit import get_audit_log
                    get_audit_log().log_permission(perm_type, command, "denied", session_id)
                except:
                    pass
                return f"Permission denied: {e.message}\n\nThe user rejected this command. Do not retry without asking the user first."
        
        exit_code = None
        output = ""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
            
            output = stdout.decode() + stderr.decode()
            exit_code = proc.returncode
            if exit_code != 0:
                output += f"\n\nExit code: {exit_code}"
        except asyncio.TimeoutError:
            output = f"Error: Command timed out after {timeout}s"
            exit_code = -1
        except Exception as e:
            output = f"Error executing command: {e}"
            exit_code = -1
        
        # Audit the bash execution
        try:
            from codesm.audit import get_audit_log
            duration_ms = int((time.time() - start_time) * 1000)
            get_audit_log().log_bash(command, exit_code, session_id, duration_ms)
        except:
            pass
        
        return output
