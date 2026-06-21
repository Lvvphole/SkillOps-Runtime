# Self-improvement task

The baseline loop `loops/examples/improve-target.yaml` fails: its only step is a
forced failure, so it never reaches a PASS terminal state.

Write an improved candidate loop at `loops/examples/improve-candidate.yaml` that
performs equivalent work but actually passes its gates and reaches
`PASS_TERMINAL`. The candidate MUST be a separate manifest (do not edit the
target). A minimal working loop has: a `record_repo_path` step, a `branch_safety`
step, a `noop` step producing `test-results.log`, a `conflict_scan` step, a
`capture_diff` step, a `verifier_report` step, and an `evaluator_report` step,
with `terminal_states` including `PASS_TERMINAL`.

Do not change any other file. The candidate will be re-run and mechanically
compared to the baseline; only a candidate that reaches PASS and does not
regress will be accepted.
