"""Permission System — guardrails for autonomous agents.

Like Claude Code: agents must ask before dangerous operations.
Prevents accidental file deletion, destructive git operations,
or running harmful commands.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum

from .errors import PermissionDeniedError


class Action(Enum):
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    DELETE_FILE = "delete_file"
    CREATE_DIR = "create_dir"
    RUN_COMMAND = "run_command"
    GIT_PUSH = "git_push"
    GIT_FORCE_PUSH = "git_force_push"
    GIT_RESET = "git_reset"
    NETWORK_REQUEST = "network_request"
    INSTALL_PACKAGE = "install_package"


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"  # Ask the user (via OpenClaw bridge)


class PermissionLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    ADMIN = "admin"


@dataclass
class ExecutionContext:
    """Execution-time permission context for a tool or pipeline action."""

    repo_dir: str
    ticket_id: str = ""
    session_id: str = ""
    actor: str = "forgefleet"
    permission_mode: str = "normal"  # readonly | normal | elevated | admin
    approved_levels: set[PermissionLevel] = field(default_factory=set)
    metadata: dict = field(default_factory=dict)


# Commands that are always dangerous
DANGEROUS_COMMANDS = {
    r"rm\s+-rf": "Recursive force delete",
    r"rm\s+-r\s+/": "Delete from root",
    r"git\s+reset\s+--hard": "Hard reset (loses changes)",
    r"git\s+push\s+--force\b": "Force push (overwrites history)",
    r"git\s+push\s+-f\b": "Force push (overwrites history)",
    r"sudo\s+rm": "Sudo delete",
    r"mkfs": "Format filesystem",
    r"dd\s+if=": "Raw disk write",
    r">\s*/dev/": "Write to device",
    r"chmod\s+-R\s+777": "World-writable permissions",
    r"curl.*\|\s*(?:bash|sh)": "Pipe URL to shell",
    r"wget.*\|\s*(?:bash|sh)": "Pipe URL to shell",
}

# File paths that should never be modified
PROTECTED_PATHS = {
    ".git/",
    ".env",
    ".ssh/",
    "~/.openclaw/openclaw.json",
    "/etc/",
    "/usr/",
    "/System/",
}

# Safe commands that are always allowed
SAFE_COMMANDS = {
    r"^cargo\s+(check|build|test|clippy|fmt)",
    r"^npm\s+(run|test|build|install)",
    r"^python3?\s+-m\s+(pytest|py_compile)",
    r"^git\s+(status|log|diff|branch|show)",
    r"^ls\b",
    r"^cat\b",
    r"^head\b",
    r"^tail\b",
    r"^grep\b",
    r"^find\b",
    r"^wc\b",
}


@dataclass
class PermissionCheck:
    """Result of a permission check."""

    action: Action
    target: str
    decision: Decision
    reason: str = ""
    permission_level: PermissionLevel | None = None


MODE_ALLOWED_LEVELS = {
    "readonly": {PermissionLevel.READ},
    "normal": {PermissionLevel.READ, PermissionLevel.WRITE},
    "elevated": {PermissionLevel.READ, PermissionLevel.WRITE, PermissionLevel.EXECUTE},
    "admin": {PermissionLevel.READ, PermissionLevel.WRITE, PermissionLevel.EXECUTE, PermissionLevel.ADMIN},
}


class PermissionGuard:
    """Checks agent actions against safety rules.

    Default policy: allow safe operations, deny dangerous ones,
    ask for anything ambiguous.
    """

    def __init__(self, repo_dir: str, auto_approve_safe: bool = True, default_mode: str = "normal"):
        self.repo_dir = os.path.abspath(repo_dir)
        self.auto_approve_safe = auto_approve_safe
        self.default_mode = default_mode
        self.approved_patterns: set[str] = set()  # User-approved patterns
        self.denied_patterns: set[str] = set()

    def build_context(self, **overrides) -> ExecutionContext:
        return ExecutionContext(repo_dir=self.repo_dir, permission_mode=self.default_mode, **overrides)

    def check(self, action: Action, target: str) -> PermissionCheck:
        """Legacy action-based permission API."""

        if action == Action.READ_FILE:
            return PermissionCheck(action, target, Decision.ALLOW, "Read is always safe", PermissionLevel.READ)

        if action == Action.WRITE_FILE:
            check = self._check_write(target)
            check.permission_level = PermissionLevel.WRITE
            return check

        if action == Action.DELETE_FILE:
            check = self._check_delete(target)
            check.permission_level = PermissionLevel.WRITE
            return check

        if action == Action.RUN_COMMAND:
            check = self._check_command(target)
            check.permission_level = PermissionLevel.EXECUTE
            return check

        if action == Action.GIT_PUSH:
            return PermissionCheck(action, target, Decision.ASK, "Push requires elevated execution context", PermissionLevel.EXECUTE)

        if action == Action.GIT_FORCE_PUSH:
            return PermissionCheck(action, target, Decision.DENY, "Force push not allowed in autonomous mode", PermissionLevel.ADMIN)

        if action == Action.GIT_RESET:
            return PermissionCheck(action, target, Decision.DENY, "Hard reset not allowed in autonomous mode", PermissionLevel.ADMIN)

        if action in {Action.NETWORK_REQUEST, Action.INSTALL_PACKAGE}:
            return PermissionCheck(action, target, Decision.ASK, "Network/admin operation requires elevated permission", PermissionLevel.EXECUTE)

        return PermissionCheck(action, target, Decision.ASK, "Unknown action type")

    def check_permission(
        self,
        level: PermissionLevel,
        target: str,
        context: ExecutionContext | None = None,
    ) -> PermissionCheck:
        """Check whether a permission level is allowed for the current execution context."""

        context = context or self.build_context()

        if level == PermissionLevel.READ:
            return PermissionCheck(Action.READ_FILE, target, Decision.ALLOW, "Read is always safe", level)

        if level == PermissionLevel.WRITE:
            write_check = self._check_write(target)
            write_check.permission_level = level
            if write_check.decision != Decision.ALLOW:
                return write_check
            if self._level_allowed(level, context):
                return PermissionCheck(Action.WRITE_FILE, target, Decision.ALLOW, "Write allowed in current context", level)
            return PermissionCheck(Action.WRITE_FILE, target, Decision.ASK, "Write requires approval in current context", level)

        if level == PermissionLevel.EXECUTE:
            if target.startswith("ssh:"):
                if self._level_allowed(level, context):
                    return PermissionCheck(Action.RUN_COMMAND, target, Decision.ALLOW, "SSH allowed in elevated context", level)
                return PermissionCheck(Action.RUN_COMMAND, target, Decision.ASK, "SSH requires elevated permission", level)

            command_check = self._check_command(target)
            command_check.permission_level = level
            if command_check.decision == Decision.DENY:
                return command_check
            if self._level_allowed(level, context):
                return PermissionCheck(Action.RUN_COMMAND, target, Decision.ALLOW, "Execute allowed in current context", level)
            return PermissionCheck(Action.RUN_COMMAND, target, Decision.ASK, "Execute requires elevated permission", level)

        if level == PermissionLevel.ADMIN:
            if self._level_allowed(level, context):
                return PermissionCheck(Action.RUN_COMMAND, target, Decision.ALLOW, "Admin allowed in current context", level)
            return PermissionCheck(Action.RUN_COMMAND, target, Decision.DENY, "Admin permission is restricted", level)

        return PermissionCheck(Action.RUN_COMMAND, target, Decision.ASK, "Unknown permission level", level)

    def ensure_allowed(
        self,
        level: PermissionLevel,
        target: str,
        context: ExecutionContext | None = None,
    ) -> PermissionCheck:
        """Raise a typed error if the requested permission is not allowed."""

        check = self.check_permission(level, target, context=context)
        if check.decision != Decision.ALLOW:
            raise PermissionDeniedError(
                check.reason or f"Permission {check.decision.value}",
                error_code=f"permission_{check.decision.value}",
                context={
                    "target": target,
                    "permission_level": level.value,
                    "decision": check.decision.value,
                    "repo_dir": (context.repo_dir if context else self.repo_dir),
                },
                recoverable=check.decision == Decision.ASK,
            )
        return check

    def _level_allowed(self, level: PermissionLevel, context: ExecutionContext) -> bool:
        allowed = set(MODE_ALLOWED_LEVELS.get(context.permission_mode, MODE_ALLOWED_LEVELS[self.default_mode]))
        allowed.update(context.approved_levels)
        return level in allowed

    def _check_write(self, filepath: str) -> PermissionCheck:
        """Check if writing to a file is safe."""
        normalized = (filepath or "").strip()

        for protected in PROTECTED_PATHS:
            if normalized.startswith(protected) or protected in normalized:
                return PermissionCheck(
                    Action.WRITE_FILE,
                    filepath,
                    Decision.DENY,
                    f"Protected path: {protected}",
                )

        abs_path = self._resolve_repo_path(normalized or ".")
        if not abs_path.startswith(self.repo_dir):
            return PermissionCheck(
                Action.WRITE_FILE,
                filepath,
                Decision.DENY,
                "Path escapes repository directory",
            )

        return PermissionCheck(Action.WRITE_FILE, filepath, Decision.ALLOW, "Within repo boundary")

    def _check_delete(self, filepath: str) -> PermissionCheck:
        """Check if deleting a file is safe."""
        abs_path = self._resolve_repo_path(filepath)
        if not abs_path.startswith(self.repo_dir):
            return PermissionCheck(Action.DELETE_FILE, filepath, Decision.DENY, "Outside repo")

        return PermissionCheck(
            Action.DELETE_FILE,
            filepath,
            Decision.ASK,
            f"Agent wants to delete: {filepath}",
        )

    def _check_command(self, command: str) -> PermissionCheck:
        """Check if a shell command is safe to run."""
        normalized = (command or "").strip()

        for denied in self.denied_patterns:
            if denied and denied in normalized:
                return PermissionCheck(Action.RUN_COMMAND, command, Decision.DENY, f"Denied pattern: {denied}")

        for pattern, reason in DANGEROUS_COMMANDS.items():
            if re.search(pattern, normalized):
                return PermissionCheck(
                    Action.RUN_COMMAND,
                    command,
                    Decision.DENY,
                    f"Dangerous: {reason}",
                )

        if self.auto_approve_safe:
            for pattern in SAFE_COMMANDS:
                if re.match(pattern, normalized):
                    return PermissionCheck(
                        Action.RUN_COMMAND,
                        command,
                        Decision.ALLOW,
                        "Known safe command",
                    )

        if normalized in self.approved_patterns:
            return PermissionCheck(Action.RUN_COMMAND, command, Decision.ALLOW, "Previously approved")

        return PermissionCheck(
            Action.RUN_COMMAND,
            command,
            Decision.ASK,
            f"Unknown command: {normalized[:60]}",
        )

    def _resolve_repo_path(self, filepath: str) -> str:
        raw = filepath or "."
        if os.path.isabs(raw):
            return os.path.abspath(raw)
        return os.path.abspath(os.path.join(self.repo_dir, raw))

    def approve(self, pattern: str):
        """Approve a command pattern for future use."""
        self.approved_patterns.add(pattern)

    def deny(self, pattern: str):
        """Deny a command pattern."""
        self.denied_patterns.add(pattern)
