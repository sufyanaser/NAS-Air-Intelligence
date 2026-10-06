from __future__ import annotations

from enum import StrEnum


class AgentState(StrEnum):
    CREATED = "CREATED"
    RESOLVING_STREAM = "RESOLVING_STREAM"
    VERIFYING_STREAM = "VERIFYING_STREAM"
    READY = "READY"
    CAPTURING = "CAPTURING"
    PROCESSING = "PROCESSING"
    FINALIZING = "FINALIZING"
    REPORTING = "REPORTING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    STREAM_UNAVAILABLE = "STREAM_UNAVAILABLE"
    CAPTURE_FAILED = "CAPTURE_FAILED"
    FAILED = "FAILED"


TERMINAL_STATES = frozenset(
    {
        AgentState.COMPLETED,
        AgentState.COMPLETED_WITH_WARNINGS,
        AgentState.STREAM_UNAVAILABLE,
        AgentState.CAPTURE_FAILED,
        AgentState.FAILED,
    }
)

TRANSITIONS: dict[AgentState, frozenset[AgentState]] = {
    AgentState.CREATED: frozenset({AgentState.RESOLVING_STREAM, AgentState.FAILED}),
    AgentState.RESOLVING_STREAM: frozenset(
        {AgentState.VERIFYING_STREAM, AgentState.STREAM_UNAVAILABLE, AgentState.FAILED}
    ),
    AgentState.VERIFYING_STREAM: frozenset(
        {AgentState.READY, AgentState.STREAM_UNAVAILABLE, AgentState.FAILED}
    ),
    AgentState.READY: frozenset({AgentState.CAPTURING, AgentState.FAILED}),
    # CAPTURING -> FINALIZING covers a stop request or an already-drained analyzer;
    # PROCESSING is entered once capture ends while the analyzer is still draining.
    AgentState.CAPTURING: frozenset(
        {
            AgentState.PROCESSING,
            AgentState.FINALIZING,
            AgentState.CAPTURE_FAILED,
            AgentState.FAILED,
        }
    ),
    AgentState.PROCESSING: frozenset({AgentState.FINALIZING, AgentState.FAILED}),
    AgentState.FINALIZING: frozenset({AgentState.REPORTING, AgentState.FAILED}),
    AgentState.REPORTING: frozenset(
        {
            AgentState.COMPLETED,
            AgentState.COMPLETED_WITH_WARNINGS,
            AgentState.CAPTURE_FAILED,
            AgentState.FAILED,
        }
    ),
}


class InvalidTransition(RuntimeError):
    pass


def check_transition(current: AgentState, new: AgentState) -> None:
    if current == new:
        return
    if new not in TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition(f"{current.value} -> {new.value} is not allowed")
