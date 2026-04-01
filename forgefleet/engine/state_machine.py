"""Explicit execution state machine for ForgeFleet runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any

from .errors import EscalationError, ForgeFleetError
from .lifecycle_policy import LifecyclePolicy
from .ownership import OwnershipManager


class ExecutionState(str, Enum):
    IDLE = "IDLE"
    CLAIMING = "CLAIMING"
    CONTEXT_GATHERING = "CONTEXT_GATHERING"
    PLANNING = "PLANNING"
    PRE_REVIEW = "PRE_REVIEW"
    BUILDING = "BUILDING"
    POST_REVIEW = "POST_REVIEW"
    COMPLETING = "COMPLETING"
    FAILED = "FAILED"
    ESCALATING = "ESCALATING"


@dataclass
class StateDefinition:
    state: ExecutionState
    allowed_transitions: set[ExecutionState]
    entry_action: Callable[[dict[str, Any]], None] | None = None
    exit_action: Callable[[dict[str, Any]], None] | None = None


@dataclass
class ExecutionStateMachine:
    ticket_id: str
    tracker: Any | None = None
    ownership: OwnershipManager | None = None
    lifecycle: LifecyclePolicy | None = None
    actor: str = "forgefleet"
    claim_on_enter: bool = False
    release_on_idle: bool = False
    current_state: ExecutionState = ExecutionState.IDLE
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        self.lifecycle = self.lifecycle or LifecyclePolicy()
        self._definitions = self._build_definitions()
        self._record_state(None, self.current_state, {"event": "initialized"})

    def transition(self, new_state: ExecutionState, details: dict[str, Any] | None = None) -> ExecutionState:
        details = details or {}
        definition = self._definitions[self.current_state]
        if new_state not in definition.allowed_transitions:
            raise ForgeFleetError(
                f"Invalid state transition: {self.current_state.value} -> {new_state.value}",
                error_code="invalid_state_transition",
                context={
                    "ticket_id": self.ticket_id,
                    "from_state": self.current_state.value,
                    "to_state": new_state.value,
                },
                recoverable=False,
            )

        if definition.exit_action:
            definition.exit_action(details)

        previous = self.current_state
        self.current_state = new_state
        self._record_state(previous, new_state, details)

        new_definition = self._definitions[new_state]
        if new_definition.entry_action:
            new_definition.entry_action(details)

        return self.current_state

    def fail(self, error: ForgeFleetError, details: dict[str, Any] | None = None) -> str:
        payload = dict(details or {})
        payload["error"] = error.to_dict()

        if self.current_state != ExecutionState.FAILED:
            self.transition(ExecutionState.FAILED, payload)

        escalate = self._should_escalate(error, payload)
        if not escalate:
            return "failed"

        self.transition(ExecutionState.ESCALATING, payload)
        if not self.ownership:
            return "escalation_skipped"

        ok, reason = self.ownership.escalate(self.ticket_id)
        if not ok and reason not in {"already_at_top", "no_task"}:
            raise EscalationError(
                f"Escalation failed for {self.ticket_id}: {reason}",
                context={"ticket_id": self.ticket_id, "reason": reason, "error": error.to_dict()},
                recoverable=False,
            )
        return reason

    def _build_definitions(self) -> dict[ExecutionState, StateDefinition]:
        return {
            ExecutionState.IDLE: StateDefinition(
                state=ExecutionState.IDLE,
                allowed_transitions={ExecutionState.CLAIMING},
                entry_action=self._on_enter_idle,
                exit_action=self._noop,
            ),
            ExecutionState.CLAIMING: StateDefinition(
                state=ExecutionState.CLAIMING,
                allowed_transitions={ExecutionState.CONTEXT_GATHERING, ExecutionState.FAILED},
                entry_action=self._on_enter_claiming,
                exit_action=self._noop,
            ),
            ExecutionState.CONTEXT_GATHERING: StateDefinition(
                state=ExecutionState.CONTEXT_GATHERING,
                allowed_transitions={ExecutionState.PLANNING, ExecutionState.FAILED},
                entry_action=self._noop,
                exit_action=self._noop,
            ),
            ExecutionState.PLANNING: StateDefinition(
                state=ExecutionState.PLANNING,
                allowed_transitions={ExecutionState.PRE_REVIEW, ExecutionState.FAILED},
                entry_action=self._noop,
                exit_action=self._noop,
            ),
            ExecutionState.PRE_REVIEW: StateDefinition(
                state=ExecutionState.PRE_REVIEW,
                allowed_transitions={ExecutionState.BUILDING, ExecutionState.FAILED},
                entry_action=self._noop,
                exit_action=self._noop,
            ),
            ExecutionState.BUILDING: StateDefinition(
                state=ExecutionState.BUILDING,
                allowed_transitions={ExecutionState.POST_REVIEW, ExecutionState.FAILED},
                entry_action=self._noop,
                exit_action=self._noop,
            ),
            ExecutionState.POST_REVIEW: StateDefinition(
                state=ExecutionState.POST_REVIEW,
                allowed_transitions={ExecutionState.BUILDING, ExecutionState.COMPLETING, ExecutionState.FAILED},
                entry_action=self._noop,
                exit_action=self._noop,
            ),
            ExecutionState.COMPLETING: StateDefinition(
                state=ExecutionState.COMPLETING,
                allowed_transitions={ExecutionState.IDLE, ExecutionState.FAILED},
                entry_action=self._noop,
                exit_action=self._noop,
            ),
            ExecutionState.FAILED: StateDefinition(
                state=ExecutionState.FAILED,
                allowed_transitions={ExecutionState.ESCALATING},
                entry_action=self._noop,
                exit_action=self._noop,
            ),
            ExecutionState.ESCALATING: StateDefinition(
                state=ExecutionState.ESCALATING,
                allowed_transitions=set(),
                entry_action=self._noop,
                exit_action=self._noop,
            ),
        }

    def _record_state(
        self,
        previous: ExecutionState | None,
        new_state: ExecutionState,
        details: dict[str, Any],
    ):
        event = {
            "ticket_id": self.ticket_id,
            "from_state": previous.value if previous else None,
            "to_state": new_state.value,
            "details": details,
        }
        self.history.append(event)
        if self.tracker:
            self.tracker.log_state_transition(
                ticket_id=self.ticket_id,
                from_state=previous.value if previous else "",
                to_state=new_state.value,
                actor=self.actor,
                details=details,
            )
            self.tracker.log_event(
                ticket_id=self.ticket_id,
                event_type="state_transition",
                actor=self.actor,
                details=event,
            )

    def _should_escalate(self, error: ForgeFleetError, details: dict[str, Any]) -> bool:
        execution_retries = int(details.get("execution_retries", 0) or 0)
        review_loops = int(details.get("review_loops", 0) or 0)
        retry_blocked = not self.lifecycle.should_retry_execution(execution_retries)
        review_blocked = not self.lifecycle.should_retry_review(review_loops)
        return retry_blocked or review_blocked or not error.recoverable

    def _on_enter_claiming(self, details: dict[str, Any]):
        if not self.ownership:
            return
        if self.claim_on_enter:
            owner_level = str(details.get("owner_level", "junior"))
            self.ownership.claim_or_raise(self.ticket_id, owner_level=owner_level)
            return
        self.ownership.require_can_execute(self.ticket_id)

    def _on_enter_idle(self, details: dict[str, Any]):
        if not self.release_on_idle or not self.ownership:
            return
        final_state = str(details.get("final_state", "released"))
        self.ownership.release(self.ticket_id, final_state=final_state)

    @staticmethod
    def _noop(_details: dict[str, Any]):
        return None
