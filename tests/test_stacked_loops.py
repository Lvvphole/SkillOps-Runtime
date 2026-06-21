"""Stacked (nested) loops: a parent loop dispatches a child loop as its own
full run, sharing one audit ledger. The child's mechanically-determined
terminal state — not its narrative — decides the parent step.
"""
import json
import os

import yaml

from skillops.runtime import Engine, replay_run
from skillops.store import Store


def _write(repo, name, spec):
    path = os.path.join(repo, name)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(spec, fh, sort_keys=False)
    return path


def _child_pass():
    return {
        "loop_id": "child-pass", "name": "c", "version": "0",
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


def _child_escalate():
    return {
        "loop_id": "child-esc", "name": "c", "version": "0",
        "max_iterations": 100, "default_branch": "main",
        "terminal_states": ["PASS_TERMINAL", "FAIL_RECOVERABLE",
                            "ESCALATED_WITH_BLOCKER"],
        "steps": [
            {"id": "boom", "owner": "executor", "handler": "always_fail",
             "produces": ["x.txt"]},
        ],
    }


def _parent(child_file="child.yaml", pass_states=None, depth_param=None):
    dispatch = {"id": "dispatch", "owner": "governor", "handler": "run_subloop",
                "produces": ["subloop-result.json", "subloop-run.log"],
                "params": {"child_loop": child_file}}
    if pass_states is not None:
        dispatch["params"]["pass_states"] = pass_states
    return {
        "loop_id": "parent", "name": "p", "version": "0",
        "max_iterations": 200, "default_branch": "main",
        "terminal_states": ["PASS_TERMINAL", "FAIL_RECOVERABLE",
                            "ESCALATED_WITH_BLOCKER"],
        "steps": [
            {"id": "repo-path", "owner": "runtime", "handler": "record_repo_path",
             "produces": ["repo-path-confirmation.txt"]},
            {"id": "branch-safety", "owner": "runtime", "handler": "branch_safety",
             "produces": ["branch-safety.txt"]},
            dispatch,
            {"id": "tests", "owner": "executor", "handler": "noop",
             "produces": ["test-results.log"]},
            {"id": "verify", "owner": "verifier", "handler": "verifier_report",
             "produces": ["verification-report.md"]},
            {"id": "evaluate", "owner": "evaluator", "handler": "evaluator_report",
             "produces": ["evaluation-report.md"]},
        ],
    }


def _run(repo, parent_spec, options=None):
    _write(repo, "child.yaml", _child_pass())  # default child present
    ppath = _write(repo, "parent.yaml", parent_spec)
    engine = Engine(repo, options=options or {})
    result = engine.run(ppath)
    engine.store.close()
    return result


def test_parent_dispatches_child_pass(feature_repo):
    result = _run(feature_repo, _parent())
    assert result.terminal_state == "PASS_TERMINAL"

    store = Store(f"{feature_repo}/artifacts/skillops.db")
    # child run persisted under one ledger, linked to the parent
    children = store.get_children(result.run_id)
    assert len(children) == 1
    assert children[0]["parent_run_id"] == result.run_id
    assert children[0]["terminal_state"] == "PASS_TERMINAL"
    # parent evidence references the child
    payload = json.load(open(f"{result.artifacts_dir}/subloop-result.json"))
    assert payload["child_run_id"] == children[0]["run_id"]
    assert payload["passed"] is True
    # replay surfaces the parent->child link
    rp = replay_run(store, result.run_id)
    assert rp["children"][0]["run_id"] == children[0]["run_id"]
    store.close()


def test_child_escalation_propagates(feature_repo):
    _write(feature_repo, "esc.yaml", _child_escalate())
    result = _run(feature_repo, _parent(child_file="esc.yaml"))
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"


def test_depth_guard_escalates(feature_repo):
    # Dispatching at the depth cap fails closed (the recursion guardrail).
    result = _run(feature_repo, _parent(), options={"_loop_depth": 3})
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"


def test_missing_child_manifest_fails_closed(feature_repo):
    result = _run(feature_repo, _parent(child_file="does-not-exist.yaml"))
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"


def test_child_release_not_inherited(feature_repo):
    # Parent in release mode must NOT push the child into release mode.
    result = _run(feature_repo, _parent(), options={"release": True})
    store = Store(f"{feature_repo}/artifacts/skillops.db")
    child = store.get_children(result.run_id)[0]
    # child produced no release artifacts (release stayed off)
    assert not store.has_artifact(child["run_id"], "pr-url.txt")
    assert child["terminal_state"] == "PASS_TERMINAL"
    store.close()
