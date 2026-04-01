"""Tool execution controller for ForgeFleet."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any

from .errors import ForgeFleetError, PermissionDeniedError, TimeoutError, ToolExecutionError
from .execution_tracking import ExecutionTracker
from .permissions import ExecutionContext, PermissionGuard
from .tool_registry import RegisteredTool, ToolRegistry


@dataclass
class ToolExecutionResult:
    tool_name: str
    success: bool
    content: str = ""
    data: Any = None
    error: dict[str, Any] | None = None
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "success": self.success,
            "content": self.content,
            "data": self.data,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }

    def to_legacy_string(self) -> str:
        if self.success:
            if self.content:
                return self.content
            if isinstance(self.data, str):
                return self.data
            try:
                return json.dumps(self.data, indent=2, sort_keys=True)
            except TypeError:
                return str(self.data)
        if self.error:
            return f"Tool error ({self.tool_name}): {self.error.get('message', 'unknown error')}"
        return f"Tool error ({self.tool_name})"


class ToolExecutor:
    """Permission-aware, timeout-aware tool execution with structured results."""

    def __init__(
        self,
        registry: ToolRegistry,
        permission_guard: PermissionGuard,
        tracker: ExecutionTracker | None = None,
        default_context: ExecutionContext | None = None,
        max_concurrent: int = 4,
    ):
        self.registry = registry
        self.permission_guard = permission_guard
        self.tracker = tracker
        self.default_context = default_context or permission_guard.build_context()
        self.max_concurrent = max(1, max_concurrent)

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        context: ExecutionContext | None = None,
    ) -> ToolExecutionResult:
        arguments = arguments or {}
        context = context or self.default_context
        tool: RegisteredTool | None = None
        started = time.time()
        structured_data: Any = None
        error: ForgeFleetError | None = None

        try:
            tool = self.registry.get_tool(tool_name)
            self._validate_arguments(tool, arguments)
            target = self._permission_target(tool, arguments)
            self.permission_guard.ensure_allowed(tool.permission_level, target, context=context)
            structured_data = self._invoke(tool, arguments, context)
            return self._success_result(tool, structured_data, started)
        except ForgeFleetError as exc:
            error = exc
            if tool is None:
                return ToolExecutionResult(
                    tool_name=tool_name,
                    success=False,
                    error=exc.to_dict(),
                    duration_ms=int((time.time() - started) * 1000),
                )
            return self._failure_result(tool, started, exc)
        except Exception as exc:
            error = ToolExecutionError(
                f"Unexpected tool failure for {tool_name}: {exc}",
                error_code="unexpected_tool_failure",
                context={"tool_name": tool_name, "arguments": arguments},
                recoverable=True,
            )
            if tool is None:
                return ToolExecutionResult(
                    tool_name=tool_name,
                    success=False,
                    error=error.to_dict(),
                    duration_ms=int((time.time() - started) * 1000),
                )
            return self._failure_result(tool, started, error)
        finally:
            if tool is not None:
                self._log_execution(
                    tool=tool,
                    context=context,
                    arguments=arguments,
                    result=structured_data,
                    error=error,
                    started=started,
                )

    def execute_many(
        self,
        requests: list[tuple[str, dict[str, Any] | None]],
        *,
        context: ExecutionContext | None = None,
        max_concurrent: int | None = None,
    ) -> list[ToolExecutionResult]:
        context = context or self.default_context
        concurrency = max(1, max_concurrent or self.max_concurrent)
        ordered: dict[int, ToolExecutionResult] = {}

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(self.execute, name, args or {}, context=context): idx
                for idx, (name, args) in enumerate(requests)
            }
            for future, idx in futures.items():
                ordered[idx] = future.result()

        return [ordered[idx] for idx in range(len(requests))]

    def _invoke(self, tool: RegisteredTool, arguments: dict[str, Any], context: ExecutionContext) -> Any:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(tool.handler, arguments, context)
            try:
                return future.result(timeout=tool.timeout)
            except FuturesTimeoutError as exc:
                raise TimeoutError(
                    f"Tool timed out after {tool.timeout}s: {tool.name}",
                    error_code="tool_timeout",
                    context={"tool_name": tool.name, "timeout": tool.timeout},
                    recoverable=True,
                ) from exc

    def _validate_arguments(self, tool: RegisteredTool, arguments: dict[str, Any]):
        schema = tool.input_schema or {}
        if schema.get("type") not in {None, "object"}:
            raise ToolExecutionError(
                f"Unsupported schema type for tool {tool.name}",
                error_code="unsupported_tool_schema",
                context={"tool_name": tool.name, "schema": schema},
                recoverable=False,
            )

        required = schema.get("required", []) or []
        for field_name in required:
            if field_name not in arguments:
                raise ToolExecutionError(
                    f"Missing required argument '{field_name}' for {tool.name}",
                    error_code="missing_required_argument",
                    context={"tool_name": tool.name, "field": field_name},
                    recoverable=True,
                )

        properties = schema.get("properties", {}) or {}
        for field_name, value in arguments.items():
            expected = properties.get(field_name, {}).get("type")
            if expected and not self._matches_type(expected, value):
                raise ToolExecutionError(
                    f"Invalid argument type for '{field_name}' on {tool.name}",
                    error_code="invalid_argument_type",
                    context={
                        "tool_name": tool.name,
                        "field": field_name,
                        "expected_type": expected,
                        "actual_type": type(value).__name__,
                    },
                    recoverable=True,
                )

    @staticmethod
    def _matches_type(expected: str, value: Any) -> bool:
        type_checks = {
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
            "object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
        }
        checker = type_checks.get(expected)
        return True if checker is None else checker(value)

    @staticmethod
    def _permission_target(tool: RegisteredTool, arguments: dict[str, Any]) -> str:
        if "filepath" in arguments:
            return str(arguments.get("filepath", ""))
        if "directory" in arguments:
            return str(arguments.get("directory", "."))
        if tool.name == "ssh_command":
            return f"ssh:{arguments.get('node', '')}:{arguments.get('command', '')}"
        if "command" in arguments:
            return str(arguments.get("command", ""))
        if "url" in arguments:
            return str(arguments.get("url", ""))
        if "query" in arguments:
            return str(arguments.get("query", ""))
        return tool.name

    def _success_result(self, tool: RegisteredTool, data: Any, started: float) -> ToolExecutionResult:
        duration_ms = int((time.time() - started) * 1000)
        content = self._render_content(data)
        return ToolExecutionResult(
            tool_name=tool.name,
            success=True,
            content=content,
            data=data,
            duration_ms=duration_ms,
            metadata={
                "permission_level": tool.permission_level.value,
                "source": tool.source,
                "timeout": tool.timeout,
            },
        )

    def _failure_result(self, tool: RegisteredTool, started: float, error: ForgeFleetError) -> ToolExecutionResult:
        duration_ms = int((time.time() - started) * 1000)
        return ToolExecutionResult(
            tool_name=tool.name,
            success=False,
            content="",
            data=None,
            error=error.to_dict(),
            duration_ms=duration_ms,
            metadata={
                "permission_level": tool.permission_level.value,
                "source": tool.source,
                "timeout": tool.timeout,
            },
        )

    @staticmethod
    def _render_content(data: Any) -> str:
        if data is None:
            return ""
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            if "content" in data and isinstance(data["content"], str):
                return data["content"]
            if "stdout" in data or "stderr" in data:
                stdout = str(data.get("stdout", "")).strip()
                stderr = str(data.get("stderr", "")).strip()
                return "\n".join(part for part in [stdout, stderr] if part)
        try:
            return json.dumps(data, indent=2, sort_keys=True)
        except TypeError:
            return str(data)

    def _log_execution(
        self,
        *,
        tool: RegisteredTool,
        context: ExecutionContext,
        arguments: dict[str, Any],
        result: Any,
        error: ForgeFleetError | None,
        started: float,
    ):
        if not self.tracker:
            return
        duration_ms = int((time.time() - started) * 1000)
        rendered_result = None
        if result is not None:
            if isinstance(result, dict):
                rendered_result = result
            else:
                rendered_result = {"value": result}
        self.tracker.log_tool_execution(
            ticket_id=context.ticket_id,
            session_id=context.session_id,
            tool_name=tool.name,
            source=tool.source,
            permission_level=tool.permission_level.value,
            success=error is None,
            duration_ms=duration_ms,
            actor=context.actor,
            args=arguments,
            result=rendered_result,
            error=error,
        )
