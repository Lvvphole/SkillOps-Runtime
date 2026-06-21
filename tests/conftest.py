"""Shared fixtures: temp git repos and synthetic loop manifests.

Synthetic loops use lightweight handlers (noop/flaky) so tests are fast and do
not recursively invoke the real test runner.
"""
import os
import subprocess

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def feature_repo(tmp_path):
    """A git repo checked out on a feature branch with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("# temp\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "checkout", "-q", "-b", "feature/work")
    return str(repo)


def write_loop(repo, spec):
    path = os.path.join(repo, "loop.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(spec, fh, sort_keys=False)
    return path


# A synthetic loop that mirrors coding-pr-gate's verify/evaluate/release shape.
def minigate_spec():
    return {
        "loop_id": "minigate",
        "name": "Mini Gate",
        "version": "0",
        "max_iterations": 100,
        "default_branch": "main",
        "terminal_states": [
            "PASS_TERMINAL", "PASS_CANDIDATE_PR_CREATED",
            "FAIL_RECOVERABLE", "ESCALATED_WITH_BLOCKER",
        ],
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
            {"id": "release-commit", "owner": "executor", "handler": "release_commit",
             "release": True, "produces": ["post-commit-status.txt"]},
            {"id": "release-push", "owner": "executor", "handler": "release_push",
             "release": True, "produces": ["push-output.txt"]},
            {"id": "release-pr", "owner": "executor", "handler": "release_pr",
             "release": True, "produces": ["pr-url.txt", "pr-body.md"]},
        ],
    }
