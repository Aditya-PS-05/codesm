"""Git operations tool with permission prompts for destructive actions."""

import asyncio
from pathlib import Path
from typing import Optional
from .base import Tool


class GitTool(Tool):
    name = "git"
    description = "Git operations: commit, push, branch, status, diff, PR creation. Destructive operations require user permission."
    
    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "diff", "log", "branch", "checkout", "commit", "push", "pull", "pr"],
                    "description": "Git action to perform",
                },
                "message": {
                    "type": "string",
                    "description": "Commit message (for commit action)",
                },
                "branch_name": {
                    "type": "string",
                    "description": "Branch name (for branch/checkout actions)",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files to stage (for commit). If empty, stages all changes.",
                },
                "pr_title": {
                    "type": "string",
                    "description": "PR title (for pr action)",
                },
                "pr_body": {
                    "type": "string",
                    "description": "PR body/description (for pr action)",
                },
                "pr_base": {
                    "type": "string",
                    "description": "Base branch for PR (default: main)",
                },
            },
            "required": ["action"],
        }
    
    async def execute(self, args: dict, context: dict) -> str:
        action = args["action"]
        cwd = Path(context.get("cwd", ".")).resolve()
        session_id = context.get("session_id", "default")
        
        # Route to specific action handlers
        handlers = {
            "status": self._status,
            "diff": self._diff,
            "log": self._log,
            "branch": self._branch,
            "checkout": self._checkout,
            "commit": self._commit,
            "push": self._push,
            "pull": self._pull,
            "pr": self._create_pr,
        }
        
        handler = handlers.get(action)
        if not handler:
            return f"Unknown git action: {action}"
        
        return await handler(args, cwd, session_id)
    
    async def _run_git(self, cmd: str, cwd: Path, timeout: int = 30) -> tuple[str, int]:
        """Run a git command and return (output, exit_code)."""
        try:
            proc = await asyncio.create_subprocess_shell(
                f"git {cmd}",
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode() + stderr.decode()
            return output.strip(), proc.returncode or 0
        except asyncio.TimeoutError:
            return f"Git command timed out after {timeout}s", 1
        except Exception as e:
            return f"Git error: {e}", 1
    
    async def _status(self, args: dict, cwd: Path, session_id: str) -> str:
        """Get git status."""
        output, code = await self._run_git("status --short", cwd)
        if not output:
            return "Working tree clean - no changes."
        
        # Also get branch info
        branch, _ = await self._run_git("branch --show-current", cwd)
        return f"Branch: {branch}\n\nChanges:\n{output}"
    
    async def _diff(self, args: dict, cwd: Path, session_id: str) -> str:
        """Get git diff."""
        output, code = await self._run_git("diff --stat", cwd)
        if not output:
            # Check staged changes
            staged, _ = await self._run_git("diff --cached --stat", cwd)
            if staged:
                return f"Staged changes:\n{staged}"
            return "No changes to show."
        return output
    
    async def _log(self, args: dict, cwd: Path, session_id: str) -> str:
        """Get recent git log."""
        output, code = await self._run_git("log --oneline -10", cwd)
        return output if output else "No commits yet."
    
    async def _branch(self, args: dict, cwd: Path, session_id: str) -> str:
        """Create or list branches."""
        branch_name = args.get("branch_name")
        if branch_name:
            output, code = await self._run_git(f"branch {branch_name}", cwd)
            if code == 0:
                return f"Created branch: {branch_name}"
            return f"Failed to create branch: {output}"
        
        # List branches
        output, code = await self._run_git("branch -a", cwd)
        return output if output else "No branches found."
    
    async def _checkout(self, args: dict, cwd: Path, session_id: str) -> str:
        """Switch branches."""
        branch_name = args.get("branch_name")
        if not branch_name:
            return "Error: branch_name is required for checkout"
        
        # Check for uncommitted changes first
        status, _ = await self._run_git("status --porcelain", cwd)
        if status:
            return f"Cannot checkout: uncommitted changes exist.\n\n{status}\n\nCommit or stash changes first."
        
        output, code = await self._run_git(f"checkout {branch_name}", cwd)
        if code == 0:
            return f"Switched to branch: {branch_name}"
        return f"Failed to checkout: {output}"
    
    async def _commit(self, args: dict, cwd: Path, session_id: str) -> str:
        """Stage and commit changes. Requires permission."""
        from codesm.permission import ask_permission, PermissionDeniedError
        
        message = args.get("message")
        if not message:
            return "Error: message is required for commit"
        
        files = args.get("files", [])
        
        # Check what would be committed
        if files:
            stage_cmd = f"add {' '.join(files)}"
        else:
            stage_cmd = "add -A"
        
        # Get diff preview
        diff_preview, _ = await self._run_git("diff --stat", cwd)
        staged_preview, _ = await self._run_git("diff --cached --stat", cwd)
        
        preview = diff_preview or staged_preview or "No changes to commit."
        
        # Request permission
        try:
            await ask_permission(
                session_id=session_id,
                type="git",
                command=f"git commit -m \"{message}\"",
                title="Allow git commit?",
                description=f"**Commit message:** {message}\n\n**Changes:**\n```\n{preview}\n```",
                metadata={"message": message, "files": files},
            )
        except PermissionDeniedError:
            return "Permission denied: User rejected the commit. Do not retry without asking."
        
        # Stage files
        await self._run_git(stage_cmd, cwd)
        
        # Commit
        output, code = await self._run_git(f'commit -m "{message}"', cwd)
        if code == 0:
            # Get commit hash
            hash_out, _ = await self._run_git("rev-parse --short HEAD", cwd)
            return f"Committed: {hash_out}\n\n{output}"
        return f"Commit failed: {output}"
    
    async def _push(self, args: dict, cwd: Path, session_id: str) -> str:
        """Push to remote. Requires permission."""
        from codesm.permission import ask_permission, PermissionDeniedError
        
        # Get current branch and remote
        branch, _ = await self._run_git("branch --show-current", cwd)
        remote, _ = await self._run_git("remote", cwd)
        remote = remote.split("\n")[0] if remote else "origin"
        
        # Get commits to push
        commits, _ = await self._run_git(f"log {remote}/{branch}..HEAD --oneline", cwd)
        if not commits:
            commits = "(new branch or no unpushed commits)"
        
        # Request permission
        try:
            await ask_permission(
                session_id=session_id,
                type="git",
                command=f"git push {remote} {branch}",
                title="Allow git push?",
                description=f"**Push to:** {remote}/{branch}\n\n**Commits:**\n```\n{commits}\n```",
                metadata={"remote": remote, "branch": branch},
            )
        except PermissionDeniedError:
            return "Permission denied: User rejected the push. Do not retry without asking."
        
        output, code = await self._run_git(f"push {remote} {branch}", cwd, timeout=60)
        if code == 0:
            return f"Pushed to {remote}/{branch}\n\n{output}"
        return f"Push failed: {output}"
    
    async def _pull(self, args: dict, cwd: Path, session_id: str) -> str:
        """Pull from remote. Requires permission."""
        from codesm.permission import ask_permission, PermissionDeniedError
        
        branch, _ = await self._run_git("branch --show-current", cwd)
        remote, _ = await self._run_git("remote", cwd)
        remote = remote.split("\n")[0] if remote else "origin"
        
        try:
            await ask_permission(
                session_id=session_id,
                type="git",
                command=f"git pull {remote} {branch}",
                title="Allow git pull?",
                description=f"Pull latest changes from {remote}/{branch}",
                metadata={"remote": remote, "branch": branch},
            )
        except PermissionDeniedError:
            return "Permission denied: User rejected the pull. Do not retry without asking."
        
        output, code = await self._run_git(f"pull {remote} {branch}", cwd, timeout=60)
        if code == 0:
            return f"Pulled from {remote}/{branch}\n\n{output}"
        return f"Pull failed: {output}"
    
    async def _create_pr(self, args: dict, cwd: Path, session_id: str) -> str:
        """Create a pull request via GitHub CLI. Requires permission."""
        from codesm.permission import ask_permission, PermissionDeniedError
        
        title = args.get("pr_title")
        if not title:
            return "Error: pr_title is required for PR creation"
        
        body = args.get("pr_body", "")
        base = args.get("pr_base", "main")
        
        # Get current branch
        branch, _ = await self._run_git("branch --show-current", cwd)
        
        # Build gh command
        gh_cmd = f'gh pr create --title "{title}" --base {base}'
        if body:
            gh_cmd += f' --body "{body}"'
        
        # Request permission
        try:
            await ask_permission(
                session_id=session_id,
                type="github",
                command=gh_cmd,
                title="Allow PR creation?",
                description=f"**Title:** {title}\n**Branch:** {branch} → {base}\n\n{body[:200] + '...' if len(body) > 200 else body}",
                metadata={"title": title, "base": base, "branch": branch},
            )
        except PermissionDeniedError:
            return "Permission denied: User rejected PR creation. Do not retry without asking."
        
        # Create PR
        try:
            proc = await asyncio.create_subprocess_shell(
                gh_cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode() + stderr.decode()
            
            if proc.returncode == 0:
                return f"PR created successfully!\n\n{output}"
            return f"PR creation failed: {output}"
        except asyncio.TimeoutError:
            return "PR creation timed out"
        except Exception as e:
            return f"Error creating PR: {e}"
