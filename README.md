# SkillOps-Runtime

SkillOps Runtime is an executable control harness for coding agents and agentic
workflows. It turns AI-generated implementation work into verifier-owned,
PR-gated, auditable software-delivery units by requiring code changes, tests,
evidence artifacts, terminal state, clean git state, pushed branch, and PR URL
before the loop can pass.

This repository contains **SkillOps LoopStack v0**: a checkpointed loop kernel
that converts coding-agent output into replayable, evidence-verified, resumable,
PR-gated software-delivery runs.

## Core rule

> Nothing passes because an agent says it passed. It passes only when the
> LoopStack can **replay** the run, **verify** the evidence, **resume** from
> checkpoints, and emit a **valid terminal state**.

Agent explanations, summaries, and confidence statements are never evidence.

## Non-negotiable execution rules (v0)

- **Manifest is the source of truth.** The runtime executes only registered
  loops, registered handlers, defined steps, defined terminal states, and
  declared evidence. Unknown handlers / unregistered terminal states escalate.
- **No invented commands.** Commands are discovered from `README.md`,
  `AGENTS.md`, `pyproject.toml`, `package.json`, `Makefile`, or CI config.
- **Branch safety.** Branch identity is recorded; direct writes to the default
  branch are forbidden (the `branch-safety` step escalates on `main`).
- **Evidence before analysis.** The Verifier inspects artifacts only; the
  Evaluator scores strictly against the loop contract.
- **Release lock.** Commit / push / PR steps are release-gated and run only with
  `--release`, after validation, tests, evidence, checkpoint, decision,
  verifier, and evaluator gates pass.
- **Terminal states map to evidence.** A terminal state is valid only if it is
  registered in the manifest **and** every mapped artifact exists for the run.
- **No auto-merge and no auto-deploy in v0.** A PR may be created; nothing is
  merged or deployed automatically. Agent-authored skills enter the registry as
  `candidate` only.

## v0 scope

Checkpointed loop kernel + schemas + SQLite persistence + Governor v0 + CLI +
tests + Git/PR gates + one reference loop (`coding-pr-gate`). No dashboard, no
chat integrations, no hot-reload, no autonomous production skill mutation.

## Local path / GitHub repo

- GitHub repository: `https://github.com/Lvvphole/SkillOps-Runtime`
- Development branch: `claude/skillops-loopstack-v0-qobzcg`

> Note: the LoopStack binds to the **git remote identity** (verified by the
> `remote-verify` step), not a hard-coded OS path. The reference contract was
> authored against a Windows path; runs record the actual checkout path in
> `repo-path-confirmation.txt`.

## Install

```bash
pip install -e .          # installs the `skillops` console script + PyYAML
pip install pytest        # for the test suite
```

## CLI commands

```bash
# Validate the loop / skill manifests
python -m skillops loop validate  --loop  loops/coding-pr-gate.yaml
python -m skillops skill validate --skill skills/coding-pr-gate/skill.yaml

# Start a run (creates a run id + artifacts/<run_id>/)
python -m skillops loop run --loop loops/coding-pr-gate.yaml
#   --release            enable commit/push/PR-gated steps
#   --pr-url <URL>       required for PASS_CANDIDATE_PR_CREATED

# Resume from the last successful checkpoint (no completed step is rerun)
python -m skillops loop resume --run-id <run_id>

# Replay run history from persisted records
python -m skillops loop replay --run-id <run_id>

# Inspect run status by id
python -m skillops run status --run-id <run_id>

# Evaluate UPSHIFT thresholds over run history; emit a promotion candidate
python -m skillops skill promote-check --skill coding-pr-gate
#   --dry-run     assess only, create no candidate
```

## Skill promotion (v0: candidate only)

`skill promote-check` mechanically evaluates the UPSHIFT thresholds against
persisted run history — ≥3 successful comparable runs, ≥0.90 pass rate over the
last 10, zero rollbacks, zero human overrides, candidate package validates, and
the latest successful run replays — and **fails closed** otherwise. On pass it
emits `PROMOTION_CANDIDATE_CREATED` with a candidate package reference,
validation log, and promotion checklist, plus a durable candidate-registry
record at `skills/<id>/candidate/promotion-record.json`.

It **never** promotes to production: v0 forbids autonomous production skill
mutation, so `SKILL_PROMOTED` remains schema-only and requires human approval
beyond v0.

## Data model (SQLite, `artifacts/skillops.db`)

| table | purpose |
|-------|---------|
| `runs` | run id, loop id, status, terminal state, timestamps, artifacts dir |
| `step_runs` | step id, owner role, status, inputs, outputs, attempt, evidence |
| `checkpoints` | run/step, sequence, state snapshot, resume pointer |
| `decisions` | Governor decision, reason code, input-state hash, next action |
| `artifacts` | evidence name, path, sha256, kind — tied to the run id |

Every executable step writes a step record; every completed step writes a
checkpoint; every loop-direction choice writes a decision record.

## Governor v0

Decisions: `CONTINUE`, `RETRY`, `DOWNSHIFT`, `UPSHIFT`, `ESCALATE`, `STOP`.
The Governor decides only from gate results and the recorded attempt count
(same-failure limit = 3, iteration cap is mechanical).

## Terminal states

`PASS_TERMINAL`, `PASS_CANDIDATE_PR_CREATED`, `FAIL_RECOVERABLE`,
`FAIL_PATCH_PR_CREATED`, `ESCALATED_WITH_BLOCKER`,
`PROMOTION_CANDIDATE_CREATED`, `SKILL_PROMOTED`.

Vague strings such as `done`, `complete`, `looks good`, `passed by agent`,
`implemented`, `finished` are explicitly rejected.

## coding-pr-gate reference loop

Step sequence (release steps run only with `--release`):

```
repo-path → remote-verify → source-docs → command-discovery → git-status →
branch-safety → pull-sync → manifest-validate → tests → conflict-scan →
capture-diff → verify → evaluate →
[release-commit → release-push → release-pr]
```

### Artifact paths (`artifacts/<run_id>/`)

```
repo-path-confirmation.txt   git-remote-verification.txt   command-discovery.md
source-doc-inspection.md     git-status-before.txt         branch-safety.txt
pull-sync.txt                loop-validate.log             loop-run.log
test-results.log             conflict-marker-scan.txt      final-diff.patch
verification-report.md       evaluation-report.md          checkpoint-history.json
decision-history.json        terminal-state.json
# release-gated:
post-commit-status.txt       push-output.txt               pr-body.md   pr-url.txt
```

`artifacts/` is generated runtime evidence and is git-ignored (not source).

## Git / PR sequence

```
repo path confirmed → remote verified → source docs inspected → commands
discovered → git state checked → pull/sync → branch safety → manifest validated
→ tests → evidence → diff captured → verifier report → evaluator report →
release lock → commit → post-commit status → push → PR created → PR docs →
PR URL written → terminal state emitted
```

`PASS_CANDIDATE_PR_CREATED` requires passing tests, verifier + evaluator
reports, clean post-commit status, a pushed branch, a PR URL, and PR
documentation. No PR URL → no `PASS_CANDIDATE_PR_CREATED`.

## Tests

```bash
python -m pytest -q
```

Covers schemas, manifest source-of-truth, command discovery, run/step/
checkpoint/decision/evidence persistence, Governor decisions, resume, replay,
terminal-state validation, branch safety, conflict-marker detection, release
lock, and PR evidence mapping.
