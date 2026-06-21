"""Governor v0 - mechanical loop-direction decisions.

The Governor never trusts agent claims. It decides only from gate results and
the recorded attempt count. Every decision it returns is persisted by the
runtime as a decision record (a decision without a record is invalid).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    CONTINUE = "CONTINUE"
    RETRY = "RETRY"
    DOWNSHIFT = "DOWNSHIFT"
    UPSHIFT = "UPSHIFT"
    ESCALATE = "ESCALATE"
    STOP = "STOP"


@dataclass
class DecisionRecord:
    decision: Decision
    reason_code: str
    next_action: str


# Maximum repeats of the same failing gate before escalation (contract: 3).
SAME_FAILURE_LIMIT = 3


class Governor:
    """Pure decision function over (step_ok, attempt, is_last, max_iter)."""

    def __init__(self, same_failure_limit: int = SAME_FAILURE_LIMIT):
        self.same_failure_limit = same_failure_limit

    def decide(
        self,
        *,
        step_ok: bool,
        attempt: int,
        required: bool,
        is_last_step: bool,
        iteration: int,
        max_iterations: int,
        reason: str = "",
    ) -> DecisionRecord:
        # Iteration cap is mechanical.
        if iteration >= max_iterations:
            return DecisionRecord(
                Decision.ESCALATE, "ITERATION_CAP_REACHED",
                "emit ESCALATED_WITH_BLOCKER",
            )

        if step_ok:
            if is_last_step:
                return DecisionRecord(
                    Decision.STOP, "ALL_STEPS_COMPLETE",
                    "determine terminal state",
                )
            return DecisionRecord(
                Decision.CONTINUE, reason or "GATE_PASSED",
                "advance to next step",
            )

        # Step failed.
        if not required:
            return DecisionRecord(
                Decision.CONTINUE, "OPTIONAL_STEP_SKIPPED",
                "advance past optional step",
            )

        if attempt < self.same_failure_limit:
            return DecisionRecord(
                Decision.RETRY, reason or "GATE_FAILED_RETRY",
                "retry from last valid checkpoint",
            )

        # Exhausted retries on a required gate -> downshift to escalation.
        return DecisionRecord(
            Decision.ESCALATE, reason or "SAME_FAILURE_LIMIT_REACHED",
            "emit ESCALATED_WITH_BLOCKER",
        )

    def downshift(self, reason_code: str, target: str) -> DecisionRecord:
        return DecisionRecord(Decision.DOWNSHIFT, reason_code,
                              f"drop to {target}")

    def upshift(self, reason_code: str) -> DecisionRecord:
        return DecisionRecord(Decision.UPSHIFT, reason_code,
                              "create candidate skill (no production mutation)")
