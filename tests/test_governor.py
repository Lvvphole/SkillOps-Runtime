from skillops.governor import Decision, Governor


def test_decision_enum_has_six_values():
    assert {d.value for d in Decision} == {
        "CONTINUE", "RETRY", "DOWNSHIFT", "UPSHIFT", "ESCALATE", "STOP"}


def test_continue_on_success_midloop():
    g = Governor()
    d = g.decide(step_ok=True, attempt=1, required=True, is_last_step=False,
                 iteration=1, max_iterations=100)
    assert d.decision == Decision.CONTINUE


def test_stop_on_last_step_success():
    g = Governor()
    d = g.decide(step_ok=True, attempt=1, required=True, is_last_step=True,
                 iteration=1, max_iterations=100)
    assert d.decision == Decision.STOP


def test_retry_then_escalate_on_repeated_failure():
    g = Governor(same_failure_limit=3)
    d1 = g.decide(step_ok=False, attempt=1, required=True, is_last_step=False,
                  iteration=1, max_iterations=100)
    assert d1.decision == Decision.RETRY
    d3 = g.decide(step_ok=False, attempt=3, required=True, is_last_step=False,
                  iteration=3, max_iterations=100)
    assert d3.decision == Decision.ESCALATE


def test_iteration_cap_escalates():
    g = Governor()
    d = g.decide(step_ok=True, attempt=1, required=True, is_last_step=False,
                 iteration=100, max_iterations=100)
    assert d.decision == Decision.ESCALATE


def test_downshift_and_upshift_available():
    g = Governor()
    assert g.downshift("TEST_FAILURE", "test repair loop").decision == Decision.DOWNSHIFT
    assert g.upshift("3_SUCCESSES").decision == Decision.UPSHIFT
