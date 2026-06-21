"""Command discovery, branch safety, conflict markers, terminal-state mapping,
release lock, PR evidence mapping, manifest source-of-truth."""
import os
import subprocess

from skillops import gitsafety
from skillops.runtime import Engine
from skillops.store import Store
from skillops.terminal import (is_valid_terminal_string, required_evidence_for)
from skillops.verifier import verify_terminal_state
from tests.conftest import REPO_ROOT, minigate_spec, write_loop


# ---- command discovery (no invented commands) ----------------------------
def test_command_discovery_from_source_docs():
    cmds = gitsafety.discover_commands(REPO_ROOT)
    assert "python -m pytest -q" in cmds["test"]
    assert any("loop validate" in c for c in cmds["validate"])


def test_no_commands_invented_on_empty_repo(tmp_path):
    cmds = gitsafety.discover_commands(str(tmp_path))
    assert cmds["test"] == [] and cmds["validate"] == []


# ---- branch safety -------------------------------------------------------
def test_branch_safety_blocks_default_branch(feature_repo):
    subprocess.run(["git", "checkout", "-q", "main"], cwd=feature_repo,
                   check=False, capture_output=True)
    # Some git versions name the default 'master'; force 'main' for the test.
    subprocess.run(["git", "branch", "-m", "main"], cwd=feature_repo,
                   check=False, capture_output=True)
    info = gitsafety.branch_safety(feature_repo, "main")
    assert info["on_default_branch"] is True
    assert info["direct_main_write_risk"] is True


def test_branch_safety_ok_on_feature(feature_repo):
    info = gitsafety.branch_safety(feature_repo, "main")
    assert info["on_default_branch"] is False


# ---- conflict markers ----------------------------------------------------
def test_conflict_markers_detected(feature_repo):
    bad = os.path.join(feature_repo, "merge.txt")
    with open(bad, "w") as fh:
        fh.write("a\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> other\n")
    subprocess.run(["git", "add", "-A"], cwd=feature_repo, check=True,
                   capture_output=True)
    offenders = gitsafety.scan_conflict_markers(feature_repo)
    assert "merge.txt" in offenders


def test_clean_repo_has_no_conflict_markers(feature_repo):
    assert gitsafety.scan_conflict_markers(feature_repo) == []


# ---- terminal-state validation ------------------------------------------
def test_vague_terminal_strings_invalid():
    for bad in ("done", "complete", "looks good", "passed by agent", "implemented"):
        assert not is_valid_terminal_string(bad)
    assert is_valid_terminal_string("PASS_TERMINAL")


def test_terminal_state_requires_mapped_evidence(tmp_path):
    store = Store(str(tmp_path / "x.db"))
    store.create_run("r1", "l", "p", str(tmp_path))
    res = verify_terminal_state("PASS_TERMINAL", str(tmp_path), store, "r1")
    assert res["valid"] is False and res["missing"]
    store.close()


# ---- release lock / PR evidence mapping ----------------------------------
def test_pr_gated_pass_requires_pr_url(feature_repo):
    loop_path = write_loop(feature_repo, minigate_spec())
    # release enabled but no pr_url -> release-pr escalates.
    engine = Engine(feature_repo, options={"release": True})
    result = engine.run(loop_path)
    engine.store.close()
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"


def test_pr_gated_pass_with_pr_url(feature_repo):
    loop_path = write_loop(feature_repo, minigate_spec())
    engine = Engine(feature_repo, options={
        "release": True,
        "pr_url": "https://github.com/Lvvphole/SkillOps-Runtime/pull/1",
        "push_output": "branch pushed ok\n",
    })
    result = engine.run(loop_path)
    engine.store.close()
    assert result.terminal_state == "PASS_CANDIDATE_PR_CREATED"

    store = Store(f"{feature_repo}/artifacts/skillops.db")
    arts = {a["name"] for a in store.get_artifacts(result.run_id)}
    assert {"pr-url.txt", "pr-body.md", "push-output.txt",
            "post-commit-status.txt"} <= arts
    store.close()


def test_pass_candidate_evidence_mapping():
    req = required_evidence_for("PASS_CANDIDATE_PR_CREATED")
    assert "pr-url.txt" in req and "pr-body.md" in req


# ---- manifest source of truth -------------------------------------------
def test_unknown_handler_escalates(feature_repo):
    spec = minigate_spec()
    spec["steps"] = [{"id": "x", "owner": "runtime", "handler": "does_not_exist",
                      "produces": []}]
    loop_path = write_loop(feature_repo, spec)
    engine = Engine(feature_repo)
    result = engine.run(loop_path)
    engine.store.close()
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"
