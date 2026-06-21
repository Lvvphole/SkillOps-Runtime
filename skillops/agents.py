"""Agent-execution adapters: the boundary at which a real coding agent generates
the implementation inside a loop step.

The harness drives the agent but never trusts it: the agent's narrative output
is logged, never used to decide pass. Only the resulting diff + passing tests +
verifier approval determine the terminal state. Adapters cannot own completion.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from skillops import gitsafety


@dataclass
class AgentRunResult:
    ok: bool
    output: str = ""
    message: str = ""


# An adapter is a callable(ctx, task_text) -> AgentRunResult that edits the
# repository working tree to implement `task_text`.
AgentAdapter = Callable[[object, str], AgentRunResult]


def _shell_adapter(command: list) -> AgentAdapter:
    """Run a configured agent CLI (e.g. `claude -p`, `codex`) in the repo. The
    command comes from the manifest, not invented (respects no-invented-commands).
    The task text is appended as the final argument."""

    def run(ctx, task_text: str) -> AgentRunResult:
        cmd = list(command) + [task_text]
        rc, out = gitsafety.run(cmd, ctx.repo)
        return AgentRunResult(ok=rc == 0, output=out,
                              message=f"shell agent exit={rc}")

    return run


def resolve_agent_adapter(ctx, step) -> Optional[AgentAdapter]:
    """Resolve an adapter or return None (caller fails closed).

    Precedence:
      1. ctx.options['agent_adapter'] callable (programmatic / tests)
      2. params['agent'] == 'shell' with params['command'] (configured CLI)
    """
    adapter = ctx.options.get("agent_adapter")
    if callable(adapter):
        return adapter
    if step.params.get("agent") == "shell":
        command = step.params.get("command")
        if command:
            return _shell_adapter(list(command))
    return None
