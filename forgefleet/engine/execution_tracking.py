"""Persistent execution tracking for ownership, collaboration, escalation, and model usage."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from .db import connect
from .errors import ConfigError, ForgeFleetError


@dataclass
class ExecutionTracker:
    """Persist execution, event, model-usage, tool, and state records to Postgres."""

    def __post_init__(self):
        self._init_db()

    def _init_db(self):
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS task_execution (
                        ticket_id TEXT PRIMARY KEY,
                        current_owner TEXT,
                        owner_level TEXT,
                        state TEXT,
                        status_reason TEXT,
                        handoff_count INTEGER DEFAULT 0,
                        escalation_count INTEGER DEFAULT 0,
                        contributors_json TEXT DEFAULT '[]',
                        reviewers_json TEXT DEFAULT '[]',
                        escalation_path_json TEXT DEFAULT '[]',
                        last_model_json TEXT DEFAULT '{}',
                        updated_at DOUBLE PRECISION
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS execution_events (
                        id BIGSERIAL PRIMARY KEY,
                        ticket_id TEXT,
                        event_type TEXT,
                        actor TEXT,
                        details_json TEXT DEFAULT '{}',
                        created_at DOUBLE PRECISION
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS model_usage_events (
                        id BIGSERIAL PRIMARY KEY,
                        ticket_id TEXT,
                        stage TEXT,
                        model_name TEXT,
                        node_name TEXT,
                        role TEXT,
                        details_json TEXT DEFAULT '{}',
                        created_at DOUBLE PRECISION
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tool_execution_events (
                        id BIGSERIAL PRIMARY KEY,
                        ticket_id TEXT,
                        session_id TEXT,
                        tool_name TEXT,
                        source TEXT,
                        permission_level TEXT,
                        success BOOLEAN,
                        error_code TEXT,
                        actor TEXT,
                        args_json TEXT DEFAULT '{}',
                        result_json TEXT DEFAULT '{}',
                        duration_ms INTEGER DEFAULT 0,
                        created_at DOUBLE PRECISION
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS execution_state_transitions (
                        id BIGSERIAL PRIMARY KEY,
                        ticket_id TEXT,
                        from_state TEXT,
                        to_state TEXT,
                        actor TEXT,
                        details_json TEXT DEFAULT '{}',
                        created_at DOUBLE PRECISION
                    )
                    """
                )
        except Exception as exc:
            raise ConfigError(
                "Failed to initialize execution tracking storage",
                error_code="execution_tracking_init_failed",
                context={"error": str(exc)},
                recoverable=False,
            ) from exc

    def upsert_execution(self, ticket_id: str, current_owner: str, owner_level: str,
                         state: str, status_reason: str = "", handoff_count: int = 0,
                         escalation_count: int = 0, contributors: list | None = None,
                         reviewers: list | None = None, escalation_path: list | None = None,
                         last_model: dict | None = None):
        now = time.time()
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO task_execution (
                        ticket_id, current_owner, owner_level, state, status_reason,
                        handoff_count, escalation_count, contributors_json,
                        reviewers_json, escalation_path_json, last_model_json, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticket_id) DO UPDATE SET
                        current_owner=EXCLUDED.current_owner,
                        owner_level=EXCLUDED.owner_level,
                        state=EXCLUDED.state,
                        status_reason=EXCLUDED.status_reason,
                        handoff_count=EXCLUDED.handoff_count,
                        escalation_count=EXCLUDED.escalation_count,
                        contributors_json=EXCLUDED.contributors_json,
                        reviewers_json=EXCLUDED.reviewers_json,
                        escalation_path_json=EXCLUDED.escalation_path_json,
                        last_model_json=EXCLUDED.last_model_json,
                        updated_at=EXCLUDED.updated_at
                    """,
                    (
                        ticket_id, current_owner, owner_level, state, status_reason,
                        handoff_count, escalation_count,
                        json.dumps(contributors or []),
                        json.dumps(reviewers or []),
                        json.dumps(escalation_path or []),
                        json.dumps(last_model or {}),
                        now,
                    ),
                )
        except Exception as exc:
            raise ConfigError(
                "Failed to persist execution state",
                error_code="execution_upsert_failed",
                context={"ticket_id": ticket_id, "state": state, "error": str(exc)},
                recoverable=True,
            ) from exc

    def log_event(self, ticket_id: str, event_type: str, actor: str, details: dict | None = None):
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO execution_events (ticket_id, event_type, actor, details_json, created_at) VALUES (%s, %s, %s, %s, %s)",
                    (ticket_id, event_type, actor, json.dumps(details or {}), time.time()),
                )
        except Exception as exc:
            raise ConfigError(
                "Failed to log execution event",
                error_code="execution_event_log_failed",
                context={"ticket_id": ticket_id, "event_type": event_type, "error": str(exc)},
                recoverable=True,
            ) from exc

    def log_model_usage(self, ticket_id: str, stage: str, model_name: str,
                        node_name: str, role: str, details: dict | None = None):
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO model_usage_events (ticket_id, stage, model_name, node_name, role, details_json, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (ticket_id, stage, model_name, node_name, role, json.dumps(details or {}), time.time()),
                )
        except Exception as exc:
            raise ConfigError(
                "Failed to log model usage",
                error_code="model_usage_log_failed",
                context={"ticket_id": ticket_id, "stage": stage, "model_name": model_name, "error": str(exc)},
                recoverable=True,
            ) from exc

    def log_tool_execution(
        self,
        ticket_id: str = "",
        session_id: str = "",
        tool_name: str = "",
        source: str = "builtin",
        permission_level: str = "",
        success: bool = False,
        duration_ms: int = 0,
        actor: str = "forgefleet",
        args: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error: ForgeFleetError | None = None,
    ):
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tool_execution_events (
                        ticket_id, session_id, tool_name, source, permission_level,
                        success, error_code, actor, args_json, result_json, duration_ms, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        ticket_id,
                        session_id,
                        tool_name,
                        source,
                        permission_level,
                        success,
                        error.error_code if error else None,
                        actor,
                        json.dumps(args or {}),
                        json.dumps(result or {}),
                        duration_ms,
                        time.time(),
                    ),
                )
        except Exception as exc:
            raise ConfigError(
                "Failed to log tool execution",
                error_code="tool_execution_log_failed",
                context={"ticket_id": ticket_id, "tool_name": tool_name, "error": str(exc)},
                recoverable=True,
            ) from exc

    def log_state_transition(
        self,
        ticket_id: str,
        from_state: str,
        to_state: str,
        actor: str,
        details: dict[str, Any] | None = None,
    ):
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO execution_state_transitions (
                        ticket_id, from_state, to_state, actor, details_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        ticket_id,
                        from_state,
                        to_state,
                        actor,
                        json.dumps(details or {}),
                        time.time(),
                    ),
                )
        except Exception as exc:
            raise ConfigError(
                "Failed to log state transition",
                error_code="state_transition_log_failed",
                context={"ticket_id": ticket_id, "from_state": from_state, "to_state": to_state, "error": str(exc)},
                recoverable=True,
            ) from exc
