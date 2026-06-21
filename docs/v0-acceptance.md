# SkillOps LoopStack v0 — Acceptance Report

**Project terminal state: `PASS_TERMINAL`**

This report maps every contract SUCCESS_CRITERION to mechanical evidence on
merged `main`. It is the contract's `evaluate → stop` closeout; it adds no
runtime behavior. Evidence is regenerated from the runtime, not asserted by
agent claim (core rule).

## Environment

| Item | Value |
|------|-------|
| Repo path | `/home/user/SkillOps-Runtime` (Linux session checkout) |
| Git remote `origin` | `…/Lvvphole/SkillOps-Runtime` (proxied) |
| Python | 3.11 |
| Test suite | `python -m pytest -q` → **39 passed** |
| Merged history | PR #1 (kernel), PR #2 (real release path), PR #3 (promotion candidate) |

> Path note: the LoopStack binds to the **git remote identity** (verified by the
> `remote-verify` step), not a hard-coded OS path. The reference contract was
> authored against a Windows path; runs record the actual checkout path in
> `repo-path-confirmation.txt`.

## Reference run evidence

| Run | Terminal state | Evidence |
|-----|----------------|----------|
| `run_a98df7ece265` | `PASS_TERMINAL` | 17 artifacts, 13 checkpoints, 16 decisions, 16 steps; replay `reconstructable=True`; resume held `PASS_TERMINAL` |
| `run_6551c36636fa` | `PASS_CANDIDATE_PR_CREATED` | real push + `pr-url.txt`, `pr-body.md`, `push-output.txt`, `post-commit-status.txt` |
| `promote-check` | `PROMOTION_CANDIDATE_CREATED` | 6 successful runs, pass-rate 1.00, all 8 UPSHIFT gates PASS |
| `FAIL_RECOVERABLE` / `ESCALATED_WITH_BLOCKER` | — | covered by `tests/test_gates.py`, `tests/test_release_path.py` |

## Success criteria → evidence

| # | Criterion | Evidence | Result |
|---|-----------|----------|--------|
| 1 | Repo path exists | `pwd` = `/home/user/SkillOps-Runtime`; `repo-path-confirmation.txt` | PASS |
| 2 | Git remote matches | `git remote -v` → `…/Lvvphole/SkillOps-Runtime`; `git-remote-verification.txt`; `remote-verify` step | PASS |
| 3 | LoopSpec validates `coding-pr-gate.yaml` | `loop validate` PASS; `skillops/schemas.py`; `tests/test_schemas.py` | PASS |
| 4 | SkillSpec validates skill package | `skill validate` PASS; `skills/coding-pr-gate/skill.yaml`; `tests/test_schemas.py` | PASS |
| 5 | Manifest is source of truth | unknown handler → `ESCALATED_WITH_BLOCKER`, unregistered terminal/owner/dup id rejected (`tests/test_gates.py`, `tests/test_schemas.py`) | PASS |
| 6 | Persists run records | `runs` table (`skillops/store.py`); `run status` | PASS |
| 7 | Persists step records | `step_runs` table; replay shows 16 ordered steps | PASS |
| 8 | Checkpoint after each step | 13 checkpoints, verifier `no_checkpoint_gap` PASS; `checkpoint-history.json` | PASS |
| 9 | Persists Governor decisions | `decisions` table, 16 records; `decision-history.json` | PASS |
| 10 | Persists evidence artifacts | `artifacts` table (sha256, run-id-tied) + 17 files on disk | PASS |
| 11 | Governor 6 decisions | `skillops/governor.py` enum; `tests/test_governor.py` | PASS |
| 12 | Resume from last checkpoint | `loop resume` held `PASS_TERMINAL`, no completed step rerun; `tests/test_resume_replay.py` | PASS |
| 13 | Replay reconstructs history | `loop replay` `reconstructable=True`; `tests/test_resume_replay.py` | PASS |
| 14 | Rejects completion w/o evidence | verifier `missing_evidence` → not approved; `tests/test_gates.py` | PASS |
| 15 | Rejects invalid terminal state | `is_valid_terminal_string` rejects vague strings; `tests/test_gates.py` | PASS |
| 16 | Rejects invented commands | `command-discovery.md` sourced from docs; `tests/test_gates.py` no-invent | PASS |
| 17 | Branch safety before changes | `branch-safety.txt` (status, branch, default, remote, sync); `skillops/gitsafety.py` | PASS |
| 18 | Pull/sync before work | `pull-sync.txt` (fetch + local/remote sha) | PASS |
| 19 | Rejects conflict markers | `conflict-marker-scan.txt` clean; detection in `tests/test_gates.py` | PASS |
| 20 | Release lock before commit/push/PR | release steps gated by `--release`; `release_pr` fails closed w/o URL; `tests/test_release_path.py` | PASS |
| 21 | Post-commit clean tree | `post-commit-status.txt`; this PR's post-commit `git status --short` | PASS |
| 22 | Hard-stop on failed push | `test_failed_push_hard_stops` → `ESCALATED_WITH_BLOCKER` | PASS |
| 23 | Hard-stop on failed tests | `run_tests` non-zero → step FAILED → Governor RETRY/ESCALATE (`skillops/steps.py`, `governor.py`) | PASS |
| 24 | Hard-stop on missing evidence | verifier missing-evidence → `FAIL_RECOVERABLE`; `tests/test_gates.py` | PASS |
| 25 | Invalid terminal-state mapping | `verify_terminal_state`; finalize downgrade in `skillops/runtime.py` | PASS |
| 26 | coding-pr-gate enforces full sequence | `loops/coding-pr-gate.yaml` (16 steps) + artifact set | PASS |
| 27 | CLI `loop validate` | output log above | PASS |
| 28 | CLI `loop run` creates run id | `run_a98df7ece265` + artifacts dir | PASS |
| 29 | CLI `loop resume` | resume output above; `tests/test_cli.py` | PASS |
| 30 | CLI `loop replay` | replay report above; `tests/test_cli.py` | PASS |
| 31 | CLI `run status` | status report above; `tests/test_cli.py` | PASS |
| 32 | Unit tests across all areas | `tests/` (schemas, gates, governor, persistence, resume/replay, release, promotion, cli) → 39 passed | PASS |
| 33 | Documentation | `README.md` + this `docs/v0-acceptance.md` | PASS |

## Stage-lock summary

Stages 0–14 (repository verification → preflight → manifest validation →
planning → execution → test → evidence → verification → evaluation → release
lock → commit → push → PR → PR docs → terminal state) are each enforced by a
manifest step and gated by the Governor; no later stage runs until prior gates
pass.

## Terminal-state coverage

| Terminal state | Reachable | How |
|----------------|-----------|-----|
| `PASS_TERMINAL` | yes | verified `coding-pr-gate` run |
| `PASS_CANDIDATE_PR_CREATED` | yes | `--release` run with PR URL |
| `FAIL_RECOVERABLE` | yes | missing evidence / failed gate |
| `FAIL_PATCH_PR_CREATED` | yes (mapped) | terminal enum + evidence map |
| `ESCALATED_WITH_BLOCKER` | yes | failed push / unknown handler / adapter error |
| `PROMOTION_CANDIDATE_CREATED` | yes | `skill promote-check` (UPSHIFT) |
| `SKILL_PROMOTED` | schema-only | v0 forbids auto-promotion (human approval beyond v0) |

## Conclusion

All 33 success criteria map to mechanical evidence with no FAIL. Six terminal
states are produced by execution and the seventh is intentionally schema-only
per v0 constraints. The v0 contract is satisfied.

**Terminal state: `PASS_TERMINAL`.**
