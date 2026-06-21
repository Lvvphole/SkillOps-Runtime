"""Recursive self-improvement: a meta-loop runs a baseline, drives the agent to
write a candidate loop, re-runs the candidate, and a mechanical regression gate
confirms improvement from the persisted ledger (never from the agent's claims).
"""
import json
import os

import yaml

from skillops.agents import AgentRunResult
from skillops.runtime import Engine
from skillops.store import Store


def _write(repo, relpath, spec):
    path = os.path.join(repo, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(spec, fh, sort_keys=False)
    return path


def _failing_target():
    return {
        "loop_id": "target", "name": "t", "version": "0",
        "max_iterations": 20, "default_branch": "main",
        "terminal_states": ["PASS_TERMINAL", "FAIL_RECOVERABLE",
                            "ESCALATED_WITH_BLOCKER"],
        "steps": [{"id": "broken", "owner": "executor", "handler": "always_fail",
                   "produces": ["broken.txt"]}],
    }


def _passing_loop(loop_id="candidate"):
    return {
        "loop_id": loop_id, "name": "c", "version": "0",
        "max_iterations": 100, "default_branch": "main",
        "terminal_states": ["PASS_TERMINAL", "FAIL_RECOVERABLE",
                            "ESCALATED_WITH_BLOCKER"],
        "steps": [
            {"id": "repo-path", "owner": "runtime", "handler": "record_repo_path",
             "produces": ["repo-path-confirmation.txt"]},
            {"id": "branch-safety", "owner": "runtime", "handler": "branch_safety",
             "produces": ["branch-safety.txt"]},
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


def _meta_loop():
    return {
        "loop_id": "self-improve", "name": "si", "version": "0",
        "max_iterations": 300, "default_branch": "main",
        "terminal_states": ["PASS_TERMINAL", "FAIL_RECOVERABLE",
                            "ESCALATED_WITH_BLOCKER"],
        "steps": [
            {"id": "repo-path", "owner": "runtime", "handler": "record_repo_path",
             "produces": ["repo-path-confirmation.txt"]},
            {"id": "branch-safety", "owner": "runtime", "handler": "branch_safety",
             "produces": ["branch-safety.txt"]},
            {"id": "baseline", "owner": "governor", "handler": "run_subloop",
             "produces": ["baseline-result.json", "subloop-run.log"],
             "params": {"child_loop": "target.yaml",
                        "result_name": "baseline-result.json", "record_only": True}},
            {"id": "improve", "owner": "executor", "handler": "agent_execute",
             "produces": ["agent-task.md", "agent-output.log"]},
            {"id": "candidate", "owner": "governor", "handler": "run_subloop",
             "produces": ["candidate-result.json", "subloop-run.log"],
             "params": {"child_loop": "candidate.yaml",
                        "result_name": "candidate-result.json", "record_only": True}},
            {"id": "gate", "owner": "verifier", "handler": "regression_gate",
             "produces": ["regression-gate.json", "improvement-report.md"],
             "params": {"baseline_step": "baseline", "candidate_step": "candidate"}},
            {"id": "tests", "owner": "executor", "handler": "noop",
             "produces": ["test-results.log"]},
            {"id": "verify", "owner": "verifier", "handler": "verifier_report",
             "produces": ["verification-report.md"]},
            {"id": "evaluate", "owner": "evaluator", "handler": "evaluator_report",
             "produces": ["evaluation-report.md"]},
        ],
    }


def _adapter_writes(repo, spec):
    """Mock agent: writes `spec` as the candidate manifest into the repo."""
    def adapter(ctx, task):
        _write(repo, "candidate.yaml", spec)
        return AgentRunResult(ok=True, output="wrote candidate.yaml")
    return adapter


def _run_meta(repo, adapter):
    _write(repo, "target.yaml", _failing_target())
    mpath = _write(repo, "meta.yaml", _meta_loop())
    engine = Engine(repo, options={"agent_adapter": adapter, "task": "improve"})
    result = engine.run(mpath)
    engine.store.close()
    return result


def test_self_improvement_verified_end_to_end(feature_repo):
    # Agent writes a candidate that PASSES; baseline FAILS -> improvement.
    result = _run_meta(feature_repo, _adapter_writes(feature_repo, _passing_loop()))
    assert result.terminal_state == "PASS_TERMINAL"

    gate = json.load(open(f"{result.artifacts_dir}/regression-gate.json"))
    assert gate["improved"] is True and gate["non_regression"] is True
    assert gate["candidate_terminal"] == "PASS_TERMINAL"
    assert gate["baseline_terminal"] != "PASS_TERMINAL"

    # baseline + candidate child runs both linked under the meta run in one ledger.
    store = Store(f"{feature_repo}/artifacts/skillops.db")
    children = store.get_children(result.run_id)
    assert len(children) == 2
    assert {c["loop_id"] for c in children} == {"target", "candidate"}
    store.close()


def test_candidate_not_better_fails_closed(feature_repo):
    # Agent writes a candidate that ALSO fails -> gate fails closed.
    result = _run_meta(feature_repo, _adapter_writes(feature_repo, _failing_target()))
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"


def test_no_in_place_self_modification(feature_repo):
    # Agent "improves" by pointing the candidate at the SAME target manifest.
    def adapter(ctx, task):
        # candidate.yaml is a copy of target referencing the same loop_id, but the
        # gate compares loop_path; write candidate.yaml identical path scenario by
        # making the candidate step run the target file itself.
        _write(feature_repo, "candidate.yaml", _failing_target())
        return AgentRunResult(ok=True, output="x")

    # Point the candidate step at the SAME file as baseline to trip the guard.
    _write(feature_repo, "target.yaml", _failing_target())
    meta = _meta_loop()
    for s in meta["steps"]:
        if s["id"] == "candidate":
            s["params"]["child_loop"] = "target.yaml"  # same manifest as baseline
    mpath = _write(feature_repo, "meta.yaml", meta)
    engine = Engine(feature_repo, options={"agent_adapter": adapter, "task": "x"})
    result = engine.run(mpath)
    engine.store.close()

    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"
    gate = json.load(open(f"{result.artifacts_dir}/regression-gate.json"))
    assert gate["same_manifest"] is True and gate["non_regression"] is False


def test_invalid_candidate_manifest_fails_closed(feature_repo):
    # Agent writes a structurally invalid candidate -> candidate run_subloop fails.
    def adapter(ctx, task):
        path = os.path.join(feature_repo, "candidate.yaml")
        with open(path, "w") as fh:
            fh.write("loop_id: x\n")  # missing required fields
        return AgentRunResult(ok=True, output="x")

    result = _run_meta(feature_repo, adapter)
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"
