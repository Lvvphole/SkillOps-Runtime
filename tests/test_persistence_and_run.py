"""Run persistence, step/checkpoint/decision/evidence persistence, no gaps."""
from skillops.runtime import Engine
from skillops.store import Store
from tests.conftest import minigate_spec, write_loop


def run_minigate(repo, options=None):
    loop_path = write_loop(repo, minigate_spec())
    engine = Engine(repo, options=options or {})
    result = engine.run(loop_path)
    engine.store.close()
    return result, loop_path


def test_pass_terminal_and_all_records(feature_repo):
    result, _ = run_minigate(feature_repo)
    assert result.terminal_state == "PASS_TERMINAL"

    store = Store(f"{feature_repo}/artifacts/skillops.db")
    rid = result.run_id

    run = store.get_run(rid)
    assert run["status"] == "TERMINATED"
    assert run["terminal_state"] == "PASS_TERMINAL"

    steps = store.get_steps(rid)
    assert any(s["step_id"] == "verify" and s["status"] == "COMPLETED" for s in steps)

    # No checkpoint gap: every completed executable step has a checkpoint.
    completed = store.completed_step_ids(rid)
    cps = {c["step_id"] for c in store.get_checkpoints(rid)}
    assert set(completed) <= cps

    assert store.get_decisions(rid), "decisions must be persisted"

    arts = {a["name"] for a in store.get_artifacts(rid)}
    for required in ("verification-report.md", "evaluation-report.md",
                     "terminal-state.json", "checkpoint-history.json",
                     "decision-history.json", "test-results.log"):
        assert required in arts
        # every artifact has a sha256 tied to the run id
    assert all(a["sha256"] for a in store.get_artifacts(rid))
    store.close()


def test_release_steps_skipped_without_flag(feature_repo):
    result, _ = run_minigate(feature_repo)
    store = Store(f"{feature_repo}/artifacts/skillops.db")
    steps = {s["step_id"]: s["status"] for s in store.get_steps(result.run_id)}
    assert steps["release-pr"] == "SKIPPED"
    store.close()
