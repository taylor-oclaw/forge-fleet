"""Typed runtime errors for ForgeFleet."""
from __future__ import annotations

import time
from typing import Any


class ForgeFleetError(Exception):
    """Base typed error for ForgeFleet runtime failures."""

    default_error_code = "forgefleet_error"
    default_recoverable = False

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        context: dict[str, Any] | None = None,
        recoverable: bool | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.default_error_code
        self.context = context or {}
        self.timestamp = time.time()
        self.recoverable = self.default_recoverable if recoverable is None else recoverable

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "error_code": self.error_code,
            "context": self.context,
            "timestamp": self.timestamp,
            "recoverable": self.recoverable,
            "type": self.__class__.__name__,
        }

    def __str__(self) -> str:
        return self.message


class ToolExecutionError(ForgeFleetError):
    default_error_code = "tool_execution_error"
    default_recoverable = True


class PermissionDeniedError(ForgeFleetError):
    default_error_code = "permission_denied"
    default_recoverable = False


class LLMError(ForgeFleetError):
    default_error_code = "llm_error"
    default_recoverable = True


class TimeoutError(ForgeFleetError):
    default_error_code = "timeout"
    default_recoverable = True


class ClaimConflictError(ForgeFleetError):
    default_error_code = "claim_conflict"
    default_recoverable = True


class LeaseExpiredError(ForgeFleetError):
    default_error_code = "lease_expired"
    default_recoverable = True


class EscalationError(ForgeFleetError):
    default_error_code = "escalation_error"
    default_recoverable = False


class ConfigError(ForgeFleetError):
    default_error_code = "config_error"
    default_recoverable = False


class NetworkError(ForgeFleetError):
    default_error_code = "network_error"
    default_recoverable = True
