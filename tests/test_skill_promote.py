"""SKILL_PROMOTED: human-gated candidate -> promoted promotion."""
import json
import os

import yaml

from skillops.promotion import promote_skill, run_promotion_check
from skillops.runtime import Engine
from skillops.store import Store
from skillops.terminal import required_evidence_for
from tests.conftest import minigate_spec, write_loop


def _make_skill(repo, skill_id="minigate", status="candidate"):
    d = os.path.join(repo, "skills", skill_id)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "skill.yaml")
    with open(path, "w") as fh:
        yaml.safe_dump({"skill_id": skill_id, "name": "Mini", "version": "0",
                        "owner": "executor", "status": status}, fh)
    return path


def _three_successes_and_candidate(repo):
    loop_path = write_loop(repo, minigate_spec())
    for _ in range(3):
        e = Engine(repo)
        e.run(loop_path)
        e.store.close()
    store = Store(f"{repo}/artifacts/skillops.db")
    run_promotion_check(repo, store, "minigate")  # writes candidate record
    return store


def test_promote_fails_closed_without_approver(feature_repo):
    skill_path = _make_skill(feature_repo)
    store = _three_successes_and_candidate(feature_repo)
    result = promote_skill(feature_repo, store, "minigate", approver="")
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"
    assert result.promoted is False
    # registry untouched
    assert yaml.safe_load(open(skill_path))["status"] == "candidate"
    store.close()


def test_promote_fails_closed_without_candidate(feature_repo):
    _make_skill(feature_repo)
    # runs exist but no promote-check -> no candidate record
    loop_path = write_loop(feature_repo, minigate_spec())
    for _ in range(3):
        e = Engine(feature_repo)
        e.run(loop_path)
        e.store.close()
    store = Store(f"{feature_repo}/artifacts/skillops.db")
    result = promote_skill(feature_repo, store, "minigate", approver="alice")
    assert result.terminal_state == "ESCALATED_WITH_BLOCKER"
    assert "candidate" in result.detail
    store.close()


def test_promote_succeeds_with_approval_and_eligibility(feature_repo):
    skill_path = _make_skill(feature_repo)
    store = _three_successes_and_candidate(feature_repo)
    result = promote_skill(feature_repo, store, "minigate", approver="alice")
    assert result.terminal_state == "SKILL_PROMOTED"
    assert result.promoted is True

    # Registry update: status flipped to promoted + durable promoted record.
    assert yaml.safe_load(open(skill_path))["status"] == "promoted"
    assert os.path.exists(
        f"{feature_repo}/skills/minigate/promoted/promotion-record.json")

    # All five contract-mapped evidence artifacts + terminal-state present.
    for name in required_evidence_for("SKILL_PROMOTED"):
        path = os.path.join(result.artifacts_dir, name)
        assert os.path.exists(path) and os.path.getsize(path) > 0

    # UPSHIFT and STOP decisions persisted for the promotion run.
    decs = {d["decision"] for d in store.get_decisions(result.promo_run_id)}
    assert {"UPSHIFT", "STOP"} <= decs

    rec = json.load(open(f"{result.artifacts_dir}/promotion-record.json"))
    assert rec["approver"] == "alice"
    store.close()


def test_repromote_already_promoted_fails_closed(feature_repo):
    skill_path = _make_skill(feature_repo)
    store = _three_successes_and_candidate(feature_repo)
    first = promote_skill(feature_repo, store, "minigate", approver="alice")
    assert first.terminal_state == "SKILL_PROMOTED"

    # skill.yaml is now status: promoted -> candidate_validates fails -> closed.
    second = promote_skill(feature_repo, store, "minigate", approver="alice")
    assert second.terminal_state == "ESCALATED_WITH_BLOCKER"
    assert yaml.safe_load(open(skill_path))["status"] == "promoted"
    store.close()
