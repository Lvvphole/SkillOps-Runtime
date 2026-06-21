"""Real release path: genuine git push (hard-stop on failure) and adapter-based
PR creation producing PASS_CANDIDATE_PR_CREATED."""
import subprocess

import pytest

from skillops.runtime import Engine
from skillops.store import Store
from tests.conftest import minigate_spec, write_loop


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo_with_remote(tmp_path):
    """Feature-branch repo whose origin is a real local bare repository."""
    bare = tmp_path / "origin.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "remote", "add", "origin", str(bare))
    (repo / "README.md").write_text("# temp\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "checkout", "-q", "-b", "feature/work")
    return str(repo)


def _run(repo, options):
    loop_path = write_loop(repo, minigate_spec())
    engine = Engine(repo, options=options)
    result = engine.run(loop_path)
    engine.store.close()
    return result


def test_real_push_and_adapter_pr_pass_candidate(repo_with_remote):
    url = "https://github.com/Lvvphole/SkillOps-Runtime/pull/2"
    result = _run(repo_with_remote, {
        "release": True,
        "pr_adapter": lambda ctx: url,
    })
    assert result.terminal_state == "PASS_CANDIDATE_PR_CREATED"

    store = Store(f"{repo_with_remote}/artifacts/skillops.db")
    arts = {a["name"] for a in store.get_artifacts(result.run_id)}
    assert {"push-output.txt", "pr-url.txt", "pr-body.md",
            "post-commit-status.txt"} <= arts
    body = open(f"{result.artifacts_dir}/pr-body.md").read()
    for section in ("## Summary", "## Tests run", "## Rollback plan",
                    "## PR URL", url):
        assert section in body
    # branch was genuinely pushed to the bare origin
    push_log = open(f"{result.artifacts_dir}/push-output.txt").read()
    assert "exit=0" in push_log
    store.close()


def test_failed_push_hard_stops(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "remote", "add", "origin", "file:///nonexistent/origin.git")
    (repo / "README.md").write_text("# temp\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "checkout", "-q", "-b", "feature/work")

    result = _run(str(repo), {"release": True, "pr_url": "http://x/pull/1"})
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"


def test_pr_adapter_failure_fails_closed(repo_with_remote):
    def boom(ctx):
        raise RuntimeError("adapter unavailable")

    result = _run(repo_with_remote, {"release": True, "pr_adapter": boom})
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"
