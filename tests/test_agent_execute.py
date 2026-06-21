"""Agent-execution adapter: the harness drives an agent but gates its output.

All tests use a deterministic mock adapter (no API key); the agent's narrative
is never what makes the run pass — the diff + tests + verifier do.
"""
import os

from skillops.agents import AgentRunResult
from skillops.runtime import Engine
from skillops.store import Store
from tests.conftest import write_loop


def agentgate_spec():
    return {
        "loop_id": "agentgate",
        "name": "Agent Gate",
        "version": "0",
        "max_iterations": 100,
        "default_branch": "main",
        "terminal_states": ["PASS_TERMINAL", "FAIL_RECOVERABLE",
                            "ESCALATED_WITH_BLOCKER"],
        "steps": [
            {"id": "repo-path", "owner": "runtime", "handler": "record_repo_path",
             "produces": ["repo-path-confirmation.txt"]},
            {"id": "branch-safety", "owner": "runtime", "handler": "branch_safety",
             "produces": ["branch-safety.txt"]},
            {"id": "implement", "owner": "executor", "handler": "agent_execute",
             "produces": ["agent-task.md", "agent-output.log"]},
            {"id": "tests", "owner": "executor", "handler": "noop",
             "produces": ["test-results.log"]},
            {"id": "conflict-scan", "owner": "verifier", "handler": "conflict_scan",
             "produces": ["conflict-marker-scan.txt"]},
            {"id": "diff", "owner": "executor", "handler": "capture_diff",
             "produces": ["final-diff.patch"]},
            {"id": "verify", "owner": "verifier", "handler": "verifier_report",
             "produces": ["verification-report.md"]},
            {"id": "evaluate", "owner": "evaluator", "handler": "evaluator_report",
             "produces": ["evaluation-report.md"]},
        ],
    }


def _run(repo, options):
    loop_path = write_loop(repo, agentgate_spec())
    engine = Engine(repo, options=options)
    result = engine.run(loop_path)
    engine.store.close()
    return result


def test_agent_drives_end_to_end_pass(feature_repo):
    def adapter(ctx, task):
        with open(os.path.join(ctx.repo, "IMPL.py"), "w") as fh:
            fh.write("def feature():\n    return 'implemented'\n")
        return AgentRunResult(ok=True, output="created IMPL.py")

    result = _run(feature_repo, {"agent_adapter": adapter, "task": "add feature()"})
    assert result.terminal_state == "PASS_TERMINAL"

    store = Store(f"{feature_repo}/artifacts/skillops.db")
    arts = {a["name"] for a in store.get_artifacts(result.run_id)}
    assert {"agent-task.md", "agent-output.log", "final-diff.patch"} <= arts
    # the agent's change is real and captured in the diff
    diff = open(f"{result.artifacts_dir}/final-diff.patch").read()
    assert "IMPL.py" in diff
    store.close()


def test_agent_no_change_escalates(feature_repo):
    def adapter(ctx, task):
        return AgentRunResult(ok=True, output="thought about it, did nothing")

    result = _run(feature_repo, {"agent_adapter": adapter, "task": "do something"})
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"


def test_missing_task_fails_closed(feature_repo):
    def adapter(ctx, task):
        return AgentRunResult(ok=True, output="x")

    result = _run(feature_repo, {"agent_adapter": adapter})  # no task
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"


def test_missing_adapter_fails_closed(feature_repo):
    result = _run(feature_repo, {"task": "add a feature"})  # no adapter, no shell
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"


def test_agent_output_secrets_scrubbed(feature_repo):
    leaked = "AKIA" + "A" * 16  # fake AWS key shape
    def adapter(ctx, task):
        with open(os.path.join(ctx.repo, "IMPL.py"), "w") as fh:
            fh.write("x = 1\n")
        return AgentRunResult(ok=True, output=f"used key {leaked} oops")

    result = _run(feature_repo, {"agent_adapter": adapter, "task": "impl"})
    assert result.terminal_state == "PASS_TERMINAL"
    log = open(f"{result.artifacts_dir}/agent-output.log").read()
    assert leaked not in log and "[REDACTED]" in log
