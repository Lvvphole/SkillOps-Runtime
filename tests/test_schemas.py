import os

import pytest

from skillops.schemas import (SchemaError, parse_loopspec, parse_skillspec,
                              validate_loop_file, validate_skill_file)
from tests.conftest import REPO_ROOT, minigate_spec


def test_valid_loop_parses():
    spec = parse_loopspec(minigate_spec())
    assert spec.loop_id == "minigate"
    assert len(spec.steps) == 10


def test_missing_field_raises():
    bad = minigate_spec()
    del bad["max_iterations"]
    with pytest.raises(SchemaError):
        parse_loopspec(bad)


def test_invalid_terminal_state_rejected():
    bad = minigate_spec()
    bad["terminal_states"] = ["done"]  # vague, not registered
    with pytest.raises(SchemaError):
        parse_loopspec(bad)


def test_invalid_owner_rejected():
    bad = minigate_spec()
    bad["steps"][0]["owner"] = "wizard"
    with pytest.raises(SchemaError):
        parse_loopspec(bad)


def test_duplicate_step_id_rejected():
    bad = minigate_spec()
    bad["steps"][1]["id"] = bad["steps"][0]["id"]
    with pytest.raises(SchemaError):
        parse_loopspec(bad)


def test_real_coding_pr_gate_validates():
    spec = validate_loop_file(os.path.join(REPO_ROOT, "loops", "coding-pr-gate.yaml"))
    assert spec.loop_id == "coding-pr-gate"
    assert "PASS_CANDIDATE_PR_CREATED" in spec.terminal_states


def test_skill_validates_and_status_checked():
    spec = validate_skill_file(
        os.path.join(REPO_ROOT, "skills", "coding-pr-gate", "skill.yaml"))
    assert spec.skill_id == "coding-pr-gate"
    assert spec.status == "candidate"

    bad = {"skill_id": "x", "name": "x", "version": "0", "owner": "executor",
           "status": "production"}
    with pytest.raises(SchemaError):
        parse_skillspec(bad)
