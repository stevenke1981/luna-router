"""Step-boundary, reversible reasoning policy prototype (no live Codex mutation).

Inputs MUST represent evidence about one completed work step. This module emits
next-turn configuration recommendations; it cannot interrupt an active inference.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Literal

EFFORT_ORDER = ('minimal', 'low', 'medium', 'high', 'xhigh', 'max')

@dataclass(frozen=True)
class StepEvidence:
    kind: Literal['routine','complex','blocked','verified_failure','verified_success','unknown']
    evidence: bool = False
    reason: str = ''

@dataclass
class RuntimeState:
    model: str
    effort: str
    supported_efforts: list[str]
    consecutive_verified_failures: int = 0
    consecutive_routine_steps: int = 0
    hard_floor: str | None = None
    completed: bool = False


def transition(state: RuntimeState, event: StepEvidence) -> tuple[RuntimeState, str]:
    """Reversible effort change after a completed step, without fabricating effort support.

    Model-tier escalation remains delegated to v2.3 escalation.advance_route and
    its job-scoped state machine; this function NEVER silently changes a model.
    """
    from dataclasses import replace
    allowed = [e for e in EFFORT_ORDER if e in state.supported_efforts]
    if not allowed or state.effort not in allowed:
        raise ValueError('unsupported active effort')
    if state.hard_floor and state.hard_floor not in allowed:
        raise ValueError('unsupported native floor')
    if state.completed:
        return state, 'completed_no_change'
    new = replace(state)
    if event.kind == 'verified_success' and event.evidence:
        new.completed = True
        new.consecutive_verified_failures = 0
        return new, 'verified_complete'
    if event.kind == 'verified_failure' and event.evidence:
        new.consecutive_verified_failures += 1
        new.consecutive_routine_steps = 0
        if new.consecutive_verified_failures < 2:
            return new, 'failure_1_of_2'
        new.consecutive_verified_failures = 0
        higher = [e for e in allowed if EFFORT_ORDER.index(e) > EFFORT_ORDER.index(new.effort)]
        if higher:
            old = new.effort
            new.effort = higher[0]
            return new, f'effort_up:{old}->{new.effort}'
        return new, 'escalate_model_required_at_native_ceiling'
    if event.kind == 'blocked':
        new.consecutive_routine_steps = 0
        return new, 'blocker_no_escalation'
    if event.kind == 'complex' and event.evidence:
        new.consecutive_routine_steps = 0
        higher = [e for e in allowed if EFFORT_ORDER.index(e) > EFFORT_ORDER.index(new.effort)]
        if higher:
            old = new.effort
            new.effort = higher[0]
            return new, f'proactive_effort_up:{old}->{new.effort}'
        return new, 'complex_at_ceiling'
    if event.kind == 'routine' and event.evidence:
        new.consecutive_routine_steps += 1
        if new.consecutive_routine_steps >= 2:
            floor_index = EFFORT_ORDER.index(new.hard_floor) if new.hard_floor else 0
            lower = [e for e in allowed if floor_index <= EFFORT_ORDER.index(e) < EFFORT_ORDER.index(new.effort)]
            new.consecutive_routine_steps = 0
            if lower:
                old = new.effort
                new.effort = lower[-1]
                return new, f'cooldown_effort_down:{old}->{new.effort}'
        return new, 'routine_observed'
    return new, 'unknown_no_change'


def simulate(initial: RuntimeState, events: list[StepEvidence]) -> list[dict]:
    result=[]
    state=initial
    for i,event in enumerate(events,1):
        state, action=transition(state,event)
        result.append({'step':i,'event':event.kind,'action':action, 'state':asdict(state)})
    return result
