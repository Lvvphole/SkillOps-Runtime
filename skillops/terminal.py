"""Allowed terminal states and their required-evidence mappings.

A terminal state is valid only when it is a member of ``TERMINAL_STATES`` and
every artifact name in ``REQUIRED_EVIDENCE[state]`` exists for the run. Vague
strings such as ``done`` / ``complete`` / ``looks good`` are never valid.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List


class TerminalState(str, Enum):
    PASS_TERMINAL = "PASS_TERMINAL"
    PASS_CANDIDATE_PR_CREATED = "PASS_CANDIDATE_PR_CREATED"
    FAIL_RECOVERABLE = "FAIL_RECOVERABLE"
    FAIL_PATCH_PR_CREATED = "FAIL_PATCH_PR_CREATED"
    ESCALATED_WITH_BLOCKER = "ESCALATED_WITH_BLOCKER"
    PROMOTION_CANDIDATE_CREATED = "PROMOTION_CANDIDATE_CREATED"
    SKILL_PROMOTED = "SKILL_PROMOTED"


TERMINAL_STATES = {s.value for s in TerminalState}

# Explicitly rejected, vague, agent-claim style states.
INVALID_TERMINAL_STRINGS = {
    "done", "complete", "looks good", "should work", "all set",
    "passed by agent", "implemented", "finished",
}

# Required evidence artifact names per terminal state. Names are run-relative
# basenames under artifacts/<run_id>/.
REQUIRED_EVIDENCE: Dict[str, List[str]] = {
    TerminalState.PASS_TERMINAL.value: [
        "verification-report.md",
        "evaluation-report.md",
        "terminal-state.json",
        "checkpoint-history.json",
        "decision-history.json",
        "test-results.log",
    ],
    TerminalState.PASS_CANDIDATE_PR_CREATED.value: [
        "verification-report.md",
        "evaluation-report.md",
        "terminal-state.json",
        "test-results.log",
        "post-commit-status.txt",
        "push-output.txt",
        "pr-url.txt",
        "pr-body.md",
    ],
    TerminalState.FAIL_RECOVERABLE.value: [
        "terminal-state.json",
        "decision-history.json",
    ],
    TerminalState.FAIL_PATCH_PR_CREATED.value: [
        "terminal-state.json",
        "verification-report.md",
        "push-output.txt",
        "pr-url.txt",
    ],
    TerminalState.ESCALATED_WITH_BLOCKER.value: [
        "terminal-state.json",
        "decision-history.json",
    ],
    TerminalState.PROMOTION_CANDIDATE_CREATED.value: [
        "terminal-state.json",
        "promotion-candidate-package.json",
        "promotion-validation.log",
        "promotion-checklist.md",
    ],
    TerminalState.SKILL_PROMOTED.value: [
        "terminal-state.json",
    ],
}


def is_valid_terminal_string(value: str) -> bool:
    """A terminal-state string is valid only if registered in the enum."""
    if value is None:
        return False
    if value in INVALID_TERMINAL_STRINGS:
        return False
    return value in TERMINAL_STATES


def required_evidence_for(state: str) -> List[str]:
    return list(REQUIRED_EVIDENCE.get(state, []))
