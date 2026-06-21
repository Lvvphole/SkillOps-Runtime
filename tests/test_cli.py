"""CLI command coverage via the in-process entry point."""
import json
import os

from skillops import cli
from tests.conftest import REPO_ROOT, minigate_spec, write_loop


def test_cli_loop_validate_real_manifest(capsys):
    rc = cli.main(["loop", "validate", "--loop",
                   os.path.join(REPO_ROOT, "loops", "coding-pr-gate.yaml")])
    out = capsys.readouterr().out
    assert rc == 0 and "LOOP VALIDATION: PASS" in out


def test_cli_skill_validate_real_manifest(capsys):
    rc = cli.main(["skill", "validate", "--skill",
                   os.path.join(REPO_ROOT, "skills", "coding-pr-gate", "skill.yaml")])
    assert rc == 0 and "SKILL VALIDATION: PASS" in capsys.readouterr().out


def test_cli_validate_fail_on_bad_manifest(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("loop_id: x\n")  # missing required fields
    rc = cli.main(["loop", "validate", "--loop", str(bad)])
    assert rc == 1 and "FAIL" in capsys.readouterr().out


def test_cli_run_status_replay(feature_repo, monkeypatch, capsys):
    monkeypatch.setenv("SKILLOPS_REPO", feature_repo)
    loop_path = write_loop(feature_repo, minigate_spec())

    rc = cli.main(["loop", "run", "--loop", loop_path])
    out = capsys.readouterr().out
    assert rc == 0 and "terminal_state=PASS_TERMINAL" in out
    run_id = [l for l in out.splitlines() if "run_id=" in l][0].split("run_id=")[1].strip()

    rc = cli.main(["run", "status", "--run-id", run_id])
    status = json.loads(capsys.readouterr().out)
    assert status["terminal_state"] == "PASS_TERMINAL"
    assert status["last_checkpoint"] is not None

    rc = cli.main(["loop", "replay", "--run-id", run_id])
    replay = json.loads(capsys.readouterr().out)
    assert rc == 0 and replay["reconstructable"] is True
