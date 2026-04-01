"""Tool registry for ForgeFleet built-in and MCP tools."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable

from .errors import ConfigError, NetworkError, ToolExecutionError
from .git_ops import GitOps, GitResult
from .permissions import ExecutionContext, PermissionLevel
from .tool import Tool
from .web_research import WebResearcher
from .. import config


ToolHandler = Callable[[dict[str, Any], ExecutionContext], Any]


@dataclass
class RegisteredTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    permission_level: PermissionLevel = PermissionLevel.READ
    timeout: float = 30.0
    source: str = "builtin"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


class ToolRegistry:
    """Schema-first registry for ForgeFleet tools."""

    def __init__(self):
        self._tools: dict[str, RegisteredTool] = {}

    def register_tool(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: ToolHandler,
        permission_level: PermissionLevel = PermissionLevel.READ,
        timeout: float = 30.0,
        source: str = "builtin",
        metadata: dict[str, Any] | None = None,
    ) -> RegisteredTool:
        if name in self._tools:
            raise ConfigError(
                f"Tool already registered: {name}",
                error_code="duplicate_tool_registration",
                context={"tool_name": name},
                recoverable=False,
            )
        tool = RegisteredTool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            permission_level=permission_level,
            timeout=timeout,
            source=source,
            metadata=metadata or {},
        )
        self._tools[name] = tool
        return tool

    def register_mcp_tool(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: ToolHandler,
        permission_level: PermissionLevel = PermissionLevel.READ,
        timeout: float = 30.0,
        metadata: dict[str, Any] | None = None,
    ) -> RegisteredTool:
        return self.register_tool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            permission_level=permission_level,
            timeout=timeout,
            source="mcp",
            metadata=metadata,
        )

    def get_tool(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolExecutionError(
                f"Unknown tool: {name}",
                error_code="tool_not_found",
                context={"tool_name": name},
                recoverable=False,
            ) from exc

    def list_tools(self) -> list[RegisteredTool]:
        return list(self._tools.values())

    def discover(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "permission_level": tool.permission_level.value,
                "timeout": tool.timeout,
                "source": tool.source,
                "metadata": tool.metadata,
            }
            for tool in self.list_tools()
        ]

    def to_openai_schemas(self) -> list[dict[str, Any]]:
        return [tool.to_openai_schema() for tool in self.list_tools()]

    def as_legacy_tools(self, executor, context_factory: Callable[[], ExecutionContext]) -> list[Tool]:
        legacy_tools: list[Tool] = []

        for registered in self.list_tools():
            def make_runner(tool_name: str):
                def runner(**kwargs):
                    result = executor.execute(tool_name, kwargs, context=context_factory())
                    return result.to_legacy_string()
                return runner

            legacy_tools.append(
                Tool(
                    name=registered.name,
                    description=registered.description,
                    parameters=registered.input_schema,
                    func=make_runner(registered.name),
                )
            )
        return legacy_tools

    def register_builtin_tools(self, repo_dir: str, git_ops: GitOps | None = None):
        repo_dir = os.path.abspath(repo_dir)
        git_ops = git_ops or GitOps(repo_dir)
        researcher = WebResearcher()

        allowed_commands = {
            "git", "python", "python3", "python3.11", "python3.12", "pytest",
            "pip", "pip3", "uv", "ruff", "mypy", "npm", "pnpm", "yarn",
            "node", "npx", "cargo", "go", "make", "just", "ls", "cat",
            "grep", "sed", "find", "echo", "bash", "sh",
        }
        blocked_shell_tokens = ("&&", "||", ";", "|", "`", "$(")

        def resolve_repo_path(path_value: str, allow_missing: bool = False) -> str:
            rel = (path_value or ".").strip() or "."
            candidate = os.path.abspath(os.path.join(repo_dir, rel))
            if os.path.commonpath([repo_dir, candidate]) != repo_dir:
                raise ToolExecutionError(
                    f"Path escapes repository root: {path_value}",
                    error_code="path_escape",
                    context={"repo_dir": repo_dir, "path": path_value},
                    recoverable=False,
                )
            if not allow_missing and not os.path.exists(candidate):
                raise ToolExecutionError(
                    f"Not found: {path_value}",
                    error_code="path_not_found",
                    context={"repo_dir": repo_dir, "path": path_value},
                    recoverable=True,
                )
            return candidate

        def render_git_result(result: GitResult) -> dict[str, Any]:
            return {
                "success": result.success,
                "command": result.command,
                "output": result.output,
                "error": result.error,
            }

        def read_file(args: dict[str, Any], _context: ExecutionContext):
            filepath = str(args.get("filepath", ""))
            max_chars = int(args.get("max_chars", 4000) or 4000)
            file_path = resolve_repo_path(filepath)
            if not os.path.isfile(file_path):
                raise ToolExecutionError(
                    f"Not a file: {filepath}",
                    error_code="not_a_file",
                    context={"filepath": filepath},
                    recoverable=True,
                )
            with open(file_path, encoding="utf-8", errors="ignore") as handle:
                content = handle.read()
            return {
                "filepath": os.path.relpath(file_path, repo_dir),
                "content": content[:max_chars],
                "truncated": len(content) > max_chars,
                "size": len(content),
            }

        def list_files(args: dict[str, Any], _context: ExecutionContext):
            directory = str(args.get("directory", ".") or ".")
            pattern = str(args.get("pattern", "") or "")
            limit = int(args.get("limit", 50) or 50)
            full = resolve_repo_path(directory)
            if not os.path.isdir(full):
                raise ToolExecutionError(
                    f"Not a directory: {directory}",
                    error_code="not_a_directory",
                    context={"directory": directory},
                    recoverable=True,
                )
            exclude = {"target", "node_modules", ".git", "dist", ".next", "__pycache__"}
            files = []
            for root, dirs, fnames in os.walk(full):
                dirs[:] = [d for d in dirs if d not in exclude]
                for filename in fnames:
                    if pattern and not filename.endswith(pattern):
                        continue
                    files.append(os.path.relpath(os.path.join(root, filename), repo_dir))
                    if len(files) >= limit:
                        break
                if len(files) >= limit:
                    break
            return {"directory": directory, "files": files, "count": len(files)}

        def write_file(args: dict[str, Any], _context: ExecutionContext):
            filepath = str(args.get("filepath", ""))
            content = str(args.get("content", ""))
            file_path = resolve_repo_path(filepath, allow_missing=True)
            parent = os.path.dirname(file_path)
            if os.path.commonpath([repo_dir, parent]) != repo_dir:
                raise ToolExecutionError(
                    f"Rejected path: {filepath}",
                    error_code="invalid_write_target",
                    context={"filepath": filepath},
                    recoverable=False,
                )
            os.makedirs(parent, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write(content)
            return {
                "filepath": os.path.relpath(file_path, repo_dir),
                "chars_written": len(content),
            }

        def guard_command(command: str) -> list[str]:
            normalized = (command or "").strip()
            if not normalized:
                raise ToolExecutionError(
                    "Rejected: empty command",
                    error_code="empty_command",
                    context={},
                    recoverable=True,
                )
            if any(token in normalized for token in blocked_shell_tokens):
                raise ToolExecutionError(
                    "Rejected: shell operators are blocked",
                    error_code="blocked_shell_operator",
                    context={"command": normalized},
                    recoverable=False,
                )
            try:
                parsed = shlex.split(normalized)
            except ValueError as exc:
                raise ToolExecutionError(
                    f"Rejected: invalid command syntax ({exc})",
                    error_code="invalid_command_syntax",
                    context={"command": normalized},
                    recoverable=True,
                ) from exc
            if not parsed:
                raise ToolExecutionError(
                    "Rejected: empty command",
                    error_code="empty_command",
                    context={},
                    recoverable=True,
                )
            if parsed[0] not in allowed_commands:
                raise ToolExecutionError(
                    f"Rejected command '{parsed[0]}' — not in allowlist",
                    error_code="command_not_allowed",
                    context={"command": normalized, "binary": parsed[0]},
                    recoverable=False,
                )
            for arg in parsed[1:]:
                if arg.startswith("/") or arg == ".." or arg.startswith("../"):
                    raise ToolExecutionError(
                        f"Rejected path argument: {arg}",
                        error_code="unsafe_command_argument",
                        context={"command": normalized, "argument": arg},
                        recoverable=False,
                    )
            return parsed

        def run_command(args: dict[str, Any], _context: ExecutionContext):
            command = str(args.get("command", ""))
            timeout = int(args.get("timeout", 60) or 60)
            parsed = guard_command(command)
            try:
                result = subprocess.run(
                    parsed,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=repo_dir,
                    shell=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ToolExecutionError(
                    f"Command timed out after {timeout}s",
                    error_code="command_timeout",
                    context={"command": command, "timeout": timeout},
                    recoverable=True,
                ) from exc
            except Exception as exc:
                raise ToolExecutionError(
                    f"Command failed to start: {exc}",
                    error_code="command_start_failed",
                    context={"command": command},
                    recoverable=True,
                ) from exc
            return {
                "command": command,
                "returncode": result.returncode,
                "stdout": result.stdout[:3000],
                "stderr": result.stderr[:3000],
                "success": result.returncode == 0,
            }

        def git_status(_args: dict[str, Any], _context: ExecutionContext):
            return render_git_result(git_ops._run("status", "--short", "--branch"))

        def git_diff(args: dict[str, Any], _context: ExecutionContext):
            path = str(args.get("path", "") or "")
            if path:
                result = git_ops._run("diff", "--", path)
            else:
                result = git_ops._run("diff")
            return render_git_result(result)

        def git_commit(args: dict[str, Any], _context: ExecutionContext):
            message = str(args.get("message", "")).strip()
            if not message:
                raise ToolExecutionError(
                    "Commit message is required",
                    error_code="missing_commit_message",
                    context={},
                    recoverable=True,
                )
            return render_git_result(git_ops.commit(message))

        def git_push(args: dict[str, Any], _context: ExecutionContext):
            branch = str(args.get("branch", "") or "")
            force = bool(args.get("force", False))
            return render_git_result(git_ops.push(branch, force=force))

        def git_create_branch(args: dict[str, Any], _context: ExecutionContext):
            branch_name = str(args.get("branch_name", "")).strip()
            from_branch = str(args.get("from_branch", "main") or "main")
            if not branch_name:
                raise ToolExecutionError(
                    "Branch name is required",
                    error_code="missing_branch_name",
                    context={},
                    recoverable=True,
                )
            return render_git_result(git_ops.create_branch(branch_name, from_branch=from_branch))

        def web_fetch(args: dict[str, Any], _context: ExecutionContext):
            url = str(args.get("url", "")).strip()
            max_chars = int(args.get("max_chars", 8000) or 8000)
            if not url:
                raise ToolExecutionError(
                    "URL is required",
                    error_code="missing_url",
                    context={},
                    recoverable=True,
                )
            content = researcher.read_page(url, max_chars=max_chars)
            if content.startswith("Error reading"):
                raise NetworkError(
                    content,
                    error_code="web_fetch_failed",
                    context={"url": url},
                    recoverable=True,
                )
            return {"url": url, "content": content}

        def web_search(args: dict[str, Any], _context: ExecutionContext):
            query = str(args.get("query", "")).strip()
            num_results = int(args.get("num_results", 5) or 5)
            if not query:
                raise ToolExecutionError(
                    "Query is required",
                    error_code="missing_query",
                    context={},
                    recoverable=True,
                )
            results = researcher.search(query, num_results=num_results)
            if results and results[0].title == "Search error":
                raise NetworkError(
                    results[0].snippet,
                    error_code="web_search_failed",
                    context={"query": query},
                    recoverable=True,
                )
            return {
                "query": query,
                "results": [
                    {"title": item.title, "url": item.url, "snippet": item.snippet}
                    for item in results
                ],
            }

        def ssh_command(args: dict[str, Any], _context: ExecutionContext):
            node = str(args.get("node", "")).strip()
            command = str(args.get("command", "")).strip()
            timeout = int(args.get("timeout", 10) or 10)
            if not node or not command:
                raise ToolExecutionError(
                    "node and command are required",
                    error_code="missing_ssh_arguments",
                    context={"node": node},
                    recoverable=True,
                )
            node_cfg = config.get_node(node)
            target = node
            if node_cfg:
                ip = node_cfg.get("ip") or node
                ssh_user = node_cfg.get("ssh_user", "")
                target = f"{ssh_user}@{ip}" if ssh_user else ip
            try:
                result = subprocess.run(
                    [
                        "ssh",
                        "-o", f"ConnectTimeout={timeout}",
                        "-o", "BatchMode=yes",
                        target,
                        command,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout + 2,
                    shell=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise NetworkError(
                    f"SSH command timed out after {timeout}s",
                    error_code="ssh_timeout",
                    context={"node": node, "command": command, "timeout": timeout},
                    recoverable=True,
                ) from exc
            except Exception as exc:
                raise NetworkError(
                    f"SSH command failed to start: {exc}",
                    error_code="ssh_start_failed",
                    context={"node": node, "command": command},
                    recoverable=True,
                ) from exc
            return {
                "node": node,
                "target": target,
                "command": command,
                "returncode": result.returncode,
                "stdout": result.stdout[:3000],
                "stderr": result.stderr[:3000],
                "success": result.returncode == 0,
            }

        self.register_tool(
            name="read_file",
            description="Read a file from the active repository.",
            input_schema={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["filepath"],
            },
            handler=read_file,
            permission_level=PermissionLevel.READ,
            timeout=10,
        )
        self.register_tool(
            name="list_files",
            description="List files in the active repository.",
            input_schema={
                "type": "object",
                "properties": {
                    "directory": {"type": "string"},
                    "pattern": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
            handler=list_files,
            permission_level=PermissionLevel.READ,
            timeout=10,
        )
        self.register_tool(
            name="write_file",
            description="Create or overwrite a file in the active repository.",
            input_schema={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["filepath", "content"],
            },
            handler=write_file,
            permission_level=PermissionLevel.WRITE,
            timeout=15,
        )
        self.register_tool(
            name="run_command",
            description="Run a guarded shell command without shell=True.",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["command"],
            },
            handler=run_command,
            permission_level=PermissionLevel.EXECUTE,
            timeout=60,
        )
        self.register_tool(
            name="git_status",
            description="Show git working tree status.",
            input_schema={"type": "object", "properties": {}},
            handler=git_status,
            permission_level=PermissionLevel.READ,
            timeout=15,
        )
        self.register_tool(
            name="git_diff",
            description="Show git diff for the repository or a specific path.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
            handler=git_diff,
            permission_level=PermissionLevel.READ,
            timeout=15,
        )
        self.register_tool(
            name="git_commit",
            description="Commit staged git changes.",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            handler=git_commit,
            permission_level=PermissionLevel.WRITE,
            timeout=30,
        )
        self.register_tool(
            name="git_push",
            description="Push a git branch to origin.",
            input_schema={
                "type": "object",
                "properties": {
                    "branch": {"type": "string"},
                    "force": {"type": "boolean"},
                },
            },
            handler=git_push,
            permission_level=PermissionLevel.EXECUTE,
            timeout=60,
        )
        self.register_tool(
            name="git_create_branch",
            description="Create and checkout a git branch from a base branch.",
            input_schema={
                "type": "object",
                "properties": {
                    "branch_name": {"type": "string"},
                    "from_branch": {"type": "string"},
                },
                "required": ["branch_name"],
            },
            handler=git_create_branch,
            permission_level=PermissionLevel.WRITE,
            timeout=30,
        )
        self.register_tool(
            name="web_fetch",
            description="Fetch and extract readable text from a web page.",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["url"],
            },
            handler=web_fetch,
            permission_level=PermissionLevel.EXECUTE,
            timeout=20,
        )
        self.register_tool(
            name="web_search",
            description="Search the web using DuckDuckGo HTML results.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "num_results": {"type": "integer"},
                },
                "required": ["query"],
            },
            handler=web_search,
            permission_level=PermissionLevel.EXECUTE,
            timeout=20,
        )
        self.register_tool(
            name="ssh_command",
            description="Run a command on a configured fleet node via SSH.",
            input_schema={
                "type": "object",
                "properties": {
                    "node": {"type": "string"},
                    "command": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["node", "command"],
            },
            handler=ssh_command,
            permission_level=PermissionLevel.ADMIN,
            timeout=20,
        )

        return self
