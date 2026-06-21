"""UPSHIFT promotion-candidate path: thresholds over persisted run history,
PROMOTION_CANDIDATE_CREATED on pass, fail-closed below threshold, and no
production mutation in v0."""
import os

import yaml

from skillops.promotion import evaluate_upshift, run_promotion_check
from skillops.runtime import Engine
from skillops.store import Store
from skillops.terminal import required_evidence_for
from tests.conftest import minigate_spec, write_loop


def _make_skill(repo, skill_id="minigate"):
    d = os.path.join(repo, "skills", skill_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "skill.yaml"), "w") as fh:
        yaml.safe_dump({
            "skill_id": skill_id, "name": "Mini", "version": "0",
            "owner": "executor", "status": "candidate",
        }, fh)
    return os.path.join(d, "skill.yaml")


def _run_minigate(repo, times):
    loop_path = write_loop(repo, minigate_spec())
    for _ in range(times):
        engine = Engine(repo)
        engine.run(loop_path)
        engine.store.close()


def test_promotion_candidate_created_after_three_successes(feature_repo):
    _make_skill(feature_repo)
    _run_minigate(feature_repo, 3)

    store = Store(f"{feature_repo}/artifacts/skillops.db")
    result = run_promotion_check(feature_repo, store, "minigate")
    assert result.eligible is True
    assert result.terminal_state == "PROMOTION_CANDIDATE_CREATED"

    # Every mapped evidence artifact exists for the promotion run.
    for name in required_evidence_for("PROMOTION_CANDIDATE_CREATED"):
        path = os.path.join(result.artifacts_dir, name)
        assert os.path.exists(path) and os.path.getsize(path) > 0

    # Durable candidate registry record written, marked not-promoted.
    assert os.path.exists(result.record_path)
    import json
    rec = json.load(open(result.record_path))
    assert rec["promoted_to_production"] is False
    assert rec["status"] == "promotion_candidate"

    # An UPSHIFT decision was persisted for the promotion run.
    decs = store.get_decisions(result.promo_run_id)
    assert any(d["decision"] == "UPSHIFT" for d in decs)
    store.close()


def test_fail_closed_below_threshold(feature_repo):
    _make_skill(feature_repo)
    _run_minigate(feature_repo, 1)  # only one success

    store = Store(f"{feature_repo}/artifacts/skillops.db")
    a = evaluate_upshift(store, "minigate", "minigate",
                         f"{feature_repo}/skills/minigate/skill.yaml")
    assert a.eligible is False
    assert a.success_count == 1

    result = run_promotion_check(feature_repo, store, "minigate")
    assert result.eligible is False
    assert result.terminal_state is None
    # Fail closed: no candidate registry directory created.
    assert not os.path.exists(f"{feature_repo}/skills/minigate/candidate")
    store.close()


def test_no_production_mutation(feature_repo):
    skill_path = _make_skill(feature_repo)
    _run_minigate(feature_repo, 3)

    store = Store(f"{feature_repo}/artifacts/skillops.db")
    run_promotion_check(feature_repo, store, "minigate")
    store.close()

    # The source skill package is untouched (still status: candidate).
    spec = yaml.safe_load(open(skill_path))
    assert spec["status"] == "candidate"
