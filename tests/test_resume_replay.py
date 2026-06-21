import os

from skillops.runtime import Engine, replay_run, status_run
from skillops.store import Store
from tests.conftest import write_loop


def flaky_spec():
    return {
        "loop_id": "flaky-loop",
        "name": "Flaky",
        "version": "0",
        "max_iterations": 100,
        "terminal_states": ["PASS_TERMINAL", "FAIL_RECOVERABLE",
                            "ESCALATED_WITH_BLOCKER"],
        "steps": [
            {"id": "step-a", "owner": "runtime", "handler": "record_repo_path",
             "produces": ["repo-path-confirmation.txt"]},
            {"id": "step-b", "owner": "executor", "handler": "flaky",
             "produces": ["b.txt"], "params": {"env": "SKILLOPS_TEST_FLAKY"}},
            {"id": "step-c", "owner": "executor", "handler": "noop",
             "produces": ["c.txt"]},
        ],
    }


def test_resume_from_last_checkpoint(feature_repo, monkeypatch):
    monkeypatch.delenv("SKILLOPS_TEST_FLAKY", raising=False)
    loop_path = write_loop(feature_repo, flaky_spec())

    engine = Engine(feature_repo)
    result = engine.run(loop_path)
    engine.store.close()
    # step-b blocked -> run stops without completing.
    assert result.terminal_state in ("FAIL_RECOVERABLE", "ESCALATED_WITH_BLOCKER")

    store = Store(f"{feature_repo}/artifacts/skillops.db")
    rid = result.run_id
    # Only step-a checkpointed; resume_pointer should point at step-b (index 1).
    cps = store.get_checkpoints(rid)
    assert {c["step_id"] for c in cps} == {"step-a"}
    assert store.last_checkpoint(rid)["resume_pointer"] == 1
    a_runs_before = [s for s in store.get_steps(rid) if s["step_id"] == "step-a"]
    store.close()

    # Unblock and resume.
    monkeypatch.setenv("SKILLOPS_TEST_FLAKY", "1")
    engine2 = Engine(feature_repo)
    result2 = engine2.resume(rid)
    engine2.store.close()

    store = Store(f"{feature_repo}/artifacts/skillops.db")
    completed = store.completed_step_ids(rid)
    assert "step-b" in completed and "step-c" in completed
    # Resume must NOT rerun the already-completed step-a.
    a_runs_after = [s for s in store.get_steps(rid) if s["step_id"] == "step-a"]
    assert len(a_runs_after) == len(a_runs_before) == 1
    store.close()


def test_replay_reconstructs_history(feature_repo, monkeypatch):
    monkeypatch.setenv("SKILLOPS_TEST_FLAKY", "1")
    loop_path = write_loop(feature_repo, flaky_spec())
    engine = Engine(feature_repo)
    result = engine.run(loop_path)
    engine.store.close()

    store = Store(f"{feature_repo}/artifacts/skillops.db")
    rep = replay_run(store, result.run_id)
    assert rep["reconstructable"] is True
    assert [s["step_id"] for s in rep["ordered_steps"]][:3] == \
        ["step-a", "step-b", "step-c"]
    assert rep["checkpoints"], "checkpoints must be present"
    assert rep["decisions"], "decisions must be present"
    assert not rep["missing_records"]

    st = status_run(store, result.run_id)
    assert st["terminal_state"] == result.terminal_state
    assert st["last_checkpoint"] is not None
    store.close()
