"""Ownership, lease, and handoff management for distributed task execution.

Core model:
- One owner per ticket (single-threaded accountability)
- Many contributors/reviewers allowed (multi-threaded execution)
- Explicit handoff (changes owner)
- Explicit escalation (moves upward: intern → junior → senior → executive → human)
- All state persists to ForgeFleet Postgres via ExecutionTracker
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from .db import connect
from .errors import ClaimConflictError, ConfigError, LeaseExpiredError
from .transcript import get_transcript_store
from .. import config


ESCALATION_LADDER = ["intern", "junior", "senior", "executive", "human"]
TERMINAL_STATES = {
    "completed",
    "done",
    "released",
    "failed",
    "blocked",
    "cancelled",
    "abandoned",
    "mc_claim_failed",
    "lease_expired",
}

logger = logging.getLogger(__name__)


@dataclass
class TaskOwnership:
    """Ownership and collaboration state for a single ticket."""

    ticket_id: str
    owner: str
    owner_level: str = "junior"
    claimed_at: float = 0.0
    lease_seconds: int = 1800
    handoff_count: int = 0
    escalation_count: int = 0
    source_owner: str = ""
    state: str = "claimed"
    status_reason: str = ""
    contributors: list[str] = field(default_factory=list)
    reviewers: list[str] = field(default_factory=list)
    escalation_path: list[str] = field(default_factory=list)
    last_model: dict = field(default_factory=dict)

    @property
    def expires_at(self) -> float:
        return self.claimed_at + self.lease_seconds

    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class OwnershipManager:
    """Manages task ownership with collaboration, handoff, and escalation.

    Integrates with ExecutionTracker for Postgres persistence.
    """

    def __init__(self, node_name: str = "", max_handoffs: int = 3,
                 lease_seconds: int = 1800, tracker=None, transcript_store=None):
        self.node_name = node_name or config.get_node_name()
        self.max_handoffs = max_handoffs
        self.lease_seconds = lease_seconds
        self.tracker = tracker
        self.transcript = transcript_store or get_transcript_store()
        self.tasks: dict[str, TaskOwnership] = {}

        if not self.tracker:
            logger.warning(
                "OwnershipManager running in degraded mode on node '%s': Postgres tracker unavailable",
                self.node_name,
            )

    def claim(self, ticket_id: str, owner_level: str = "junior") -> tuple[bool, str]:
        existing = self.tasks.get(ticket_id)
        if existing and not existing.is_expired() and existing.owner != self.node_name:
            error = ClaimConflictError(
                f"Ticket already owned by {existing.owner}",
                context={"ticket_id": ticket_id, "current_owner": existing.owner},
                recoverable=True,
            )
            return False, f"owned_by:{existing.owner}" or error.error_code

        db_handoff_count = existing.handoff_count if existing else 0
        db_escalation_count = existing.escalation_count if existing else 0
        db_source_owner = existing.owner if existing else ""

        if self.tracker:
            claimed, reason, db_info = self._claim_in_postgres(ticket_id, owner_level)
            if not claimed:
                return False, reason
            db_handoff_count = db_info.get("handoff_count", db_handoff_count)
            db_escalation_count = db_info.get("escalation_count", db_escalation_count)
            db_source_owner = db_info.get("source_owner", db_source_owner)

        task = TaskOwnership(
            ticket_id=ticket_id,
            owner=self.node_name,
            owner_level=owner_level,
            claimed_at=time.time(),
            lease_seconds=self.lease_seconds,
            handoff_count=db_handoff_count,
            escalation_count=db_escalation_count,
            source_owner=db_source_owner,
            state="claimed",
        )
        self.tasks[ticket_id] = task
        self._persist(task, "claimed")
        return True, "claimed"

    def claim_or_raise(self, ticket_id: str, owner_level: str = "junior") -> TaskOwnership:
        claimed, reason = self.claim(ticket_id, owner_level=owner_level)
        if not claimed:
            raise ClaimConflictError(
                f"Unable to claim ticket {ticket_id}: {reason}",
                context={"ticket_id": ticket_id, "reason": reason},
                recoverable=True,
            )
        task = self.tasks.get(ticket_id)
        if not task:
            raise ClaimConflictError(
                f"Claim succeeded but task cache missing for {ticket_id}",
                error_code="claim_cache_miss",
                context={"ticket_id": ticket_id},
                recoverable=True,
            )
        return task

    def _claim_in_postgres(self, ticket_id: str, owner_level: str) -> tuple[bool, str, dict]:
        """Distributed claim check with SELECT FOR UPDATE to prevent cross-node duplicates."""
        now = time.time()
        info = {"handoff_count": 0, "escalation_count": 0, "source_owner": ""}

        try:
            with connect() as conn:
                conn.autocommit = False
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT current_owner, state, updated_at, handoff_count, escalation_count
                        FROM task_execution
                        WHERE ticket_id = %s
                        FOR UPDATE
                        """,
                        (ticket_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        current_owner, state, updated_at, handoff_count, escalation_count = row
                        info["handoff_count"] = handoff_count or 0
                        info["escalation_count"] = escalation_count or 0
                        info["source_owner"] = current_owner or ""
                        lease_active = bool(updated_at and updated_at + self.lease_seconds > now)
                        if (
                            current_owner
                            and current_owner != self.node_name
                            and state not in TERMINAL_STATES
                            and lease_active
                        ):
                            conn.rollback()
                            conflict = ClaimConflictError(
                                f"Ticket {ticket_id} already owned by {current_owner}",
                                context={"ticket_id": ticket_id, "current_owner": current_owner, "state": state},
                                recoverable=True,
                            )
                            return False, f"owned_in_db:{current_owner}" or conflict.error_code, info

                    cur.execute(
                        """
                        INSERT INTO task_execution (
                            ticket_id, current_owner, owner_level, state, status_reason,
                            handoff_count, escalation_count, contributors_json,
                            reviewers_json, escalation_path_json, last_model_json, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (ticket_id) DO UPDATE SET
                            current_owner = EXCLUDED.current_owner,
                            owner_level = EXCLUDED.owner_level,
                            state = EXCLUDED.state,
                            status_reason = EXCLUDED.status_reason,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (
                            ticket_id,
                            self.node_name,
                            owner_level,
                            "claimed",
                            "",
                            info["handoff_count"],
                            info["escalation_count"],
                            "[]",
                            "[]",
                            "[]",
                            "{}",
                            now,
                        ),
                    )
                conn.commit()
            return True, "claimed", info
        except Exception as exc:
            error = ConfigError(
                "Postgres distributed claim check failed",
                error_code="db_claim_error",
                context={"ticket_id": ticket_id, "error": str(exc)},
                recoverable=True,
            )
            logger.warning("%s", error)
            return False, error.error_code, info

    def add_contributor(self, ticket_id: str, contributor: str) -> tuple[bool, str]:
        task = self.tasks.get(ticket_id)
        if not task:
            return False, "no_task"
        if contributor not in task.contributors:
            task.contributors.append(contributor)
        self._persist(task, "contributor_added", details={"contributor": contributor})
        return True, "contributor_added"

    def add_reviewer(self, ticket_id: str, reviewer: str) -> tuple[bool, str]:
        task = self.tasks.get(ticket_id)
        if not task:
            return False, "no_task"
        if reviewer not in task.reviewers:
            task.reviewers.append(reviewer)
        self._persist(task, "reviewer_added", details={"reviewer": reviewer})
        return True, "reviewer_added"

    def handoff(self, ticket_id: str, new_owner: str,
                new_level: str = "") -> tuple[bool, str]:
        task = self.tasks.get(ticket_id)
        if not task:
            return False, "no_task"
        if task.handoff_count >= self.max_handoffs:
            return False, "handoff_limit_reached"

        task.source_owner = task.owner
        task.owner = new_owner
        if new_level:
            task.owner_level = new_level
        task.handoff_count += 1
        task.claimed_at = time.time()
        task.state = "handed_off"
        self._persist(task, "handed_off", details={
            "from": task.source_owner, "to": new_owner, "level": task.owner_level,
        })
        return True, "handed_off"

    def escalate(self, ticket_id: str, new_owner: str = "") -> tuple[bool, str]:
        task = self.tasks.get(ticket_id)
        if not task:
            return False, "no_task"

        current_idx = ESCALATION_LADDER.index(task.owner_level) \
            if task.owner_level in ESCALATION_LADDER else 0
        if current_idx >= len(ESCALATION_LADDER) - 1:
            return False, "already_at_top"

        next_level = ESCALATION_LADDER[current_idx + 1]
        task.escalation_path.append(f"{task.owner}@{task.owner_level}")
        task.escalation_count += 1

        old_owner = task.owner
        if new_owner:
            task.owner = new_owner
        task.owner_level = next_level
        task.state = "escalated"
        task.claimed_at = time.time()
        self._persist(task, "escalated", details={
            "from_level": ESCALATION_LADDER[current_idx],
            "to_level": next_level,
            "from_owner": old_owner,
            "to_owner": task.owner,
        })
        return True, f"escalated_to_{next_level}"

    def record_model(self, ticket_id: str, stage: str, model_name: str,
                     node_name: str, role: str,
                     details: dict | None = None) -> tuple[bool, str]:
        task = self.tasks.get(ticket_id)
        if not task:
            return False, "no_task"
        task.last_model = {"model": model_name, "node": node_name, "stage": stage}
        if self.tracker:
            self.tracker.log_model_usage(
                ticket_id=ticket_id, stage=stage, model_name=model_name,
                node_name=node_name, role=role, details=details,
            )
        return True, "model_recorded"

    def renew(self, ticket_id: str) -> tuple[bool, str]:
        task = self.tasks.get(ticket_id)
        if not task:
            return False, "no_task"
        task.claimed_at = time.time()
        task.state = "renewed"
        self._persist(task, "renewed")
        return True, "renewed"

    def renew_lease(self, ticket_id: str) -> tuple[bool, str]:
        """Explicit lease-renewal API used by long-running pipeline stages."""
        return self.renew(ticket_id)

    def reap_expired_leases(self, limit: int = 200) -> list[str]:
        """Mark stale Postgres ownership leases as expired for recovery."""
        if not self.tracker:
            return []

        cutoff = time.time() - self.lease_seconds
        expired_ids: list[str] = []

        try:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT ticket_id, current_owner
                        FROM task_execution
                        WHERE NOT (state = ANY(%s))
                          AND updated_at < %s
                        ORDER BY updated_at ASC
                        LIMIT %s
                        """,
                        (list(TERMINAL_STATES), cutoff, limit),
                    )
                    rows = cur.fetchall()

                    for ticket_id, current_owner in rows:
                        expired_ids.append(ticket_id)
                        cur.execute(
                            """
                            UPDATE task_execution
                            SET state = %s,
                                status_reason = %s,
                                updated_at = %s
                            WHERE ticket_id = %s
                            """,
                            ("lease_expired", "lease_reaper", time.time(), ticket_id),
                        )
                        cur.execute(
                            """
                            INSERT INTO execution_events (ticket_id, event_type, actor, details_json, created_at)
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (
                                ticket_id,
                                "lease_reaped",
                                self.node_name,
                                json.dumps({"previous_owner": current_owner}),
                                time.time(),
                            ),
                        )

            for ticket_id in expired_ids:
                self.tasks.pop(ticket_id, None)

        except Exception as exc:
            error = ConfigError(
                "Failed to reap expired leases",
                error_code="lease_reap_failed",
                context={"error": str(exc), "limit": limit},
                recoverable=True,
            )
            logger.warning("%s", error)

        return expired_ids

    def release(self, ticket_id: str,
                final_state: str = "released") -> tuple[bool, str]:
        task = self.tasks.get(ticket_id)
        if not task:
            return False, "no_task"
        task.state = final_state
        self._persist(task, final_state)
        del self.tasks[ticket_id]
        return True, final_state

    def can_execute(self, ticket_id: str) -> tuple[bool, str]:
        task = self.tasks.get(ticket_id)
        if not task:
            return False, "no_task"
        if task.owner != self.node_name:
            return False, f"owned_by:{task.owner}"
        if task.is_expired():
            return False, "lease_expired"
        return True, "ok"

    def require_can_execute(self, ticket_id: str) -> TaskOwnership:
        task = self.tasks.get(ticket_id)
        if not task:
            raise ClaimConflictError(
                f"Ticket {ticket_id} is not currently claimed",
                error_code="no_task",
                context={"ticket_id": ticket_id},
                recoverable=True,
            )
        if task.owner != self.node_name:
            raise ClaimConflictError(
                f"Ticket {ticket_id} is owned by {task.owner}",
                context={"ticket_id": ticket_id, "current_owner": task.owner},
                recoverable=True,
            )
        if task.is_expired():
            raise LeaseExpiredError(
                f"Ownership lease expired for {ticket_id}",
                context={"ticket_id": ticket_id, "owner": task.owner},
                recoverable=True,
            )
        return task

    def get_task(self, ticket_id: str) -> Optional[TaskOwnership]:
        return self.tasks.get(ticket_id)

    def _log_timeline(self, ticket_id: str, event_type: str,
                      description: str, details: dict | None = None):
        if not ticket_id:
            return
        try:
            self.transcript.log_event(ticket_id, event_type, description, metadata=details or {})
        except Exception as exc:
            logger.warning("Failed to log ownership timeline event for %s: %s", ticket_id, exc)

    def _persist(self, task: TaskOwnership, event_type: str,
                 details: dict | None = None):
        descriptions = {
            "claimed": f"{task.owner} claimed ticket {task.ticket_id}",
            "claimed_existing": f"{task.owner} reclaimed ticket {task.ticket_id}",
            "handed_off": f"Ticket {task.ticket_id} handed off to {task.owner}",
            "escalated": f"Ticket {task.ticket_id} escalated to {task.owner_level}",
            "renewed": f"Lease renewed for ticket {task.ticket_id}",
            "released": f"Ticket {task.ticket_id} released",
            "completed": f"Ticket {task.ticket_id} completed",
            "failed": f"Ticket {task.ticket_id} failed",
        }
        self._log_timeline(
            task.ticket_id,
            event_type,
            descriptions.get(event_type, f"Ownership event: {event_type}"),
            {
                "owner": task.owner,
                "owner_level": task.owner_level,
                "state": task.state,
                "handoff_count": task.handoff_count,
                "escalation_count": task.escalation_count,
                **(details or {}),
            },
        )

        if not self.tracker:
            return
        try:
            self.tracker.upsert_execution(
                ticket_id=task.ticket_id,
                current_owner=task.owner,
                owner_level=task.owner_level,
                state=task.state,
                status_reason=task.status_reason,
                handoff_count=task.handoff_count,
                escalation_count=task.escalation_count,
                contributors=task.contributors,
                reviewers=task.reviewers,
                escalation_path=task.escalation_path,
                last_model=task.last_model,
            )
            self.tracker.log_event(
                ticket_id=task.ticket_id,
                event_type=event_type,
                actor=task.owner,
                details=details,
            )
        except ConfigError as exc:
            logger.warning("Ownership persistence degraded for %s: %s", task.ticket_id, exc)
