# SkillOps-Runtime

SkillOps Runtime is an executable control harness for coding agents and agentic
workflows. It turns AI-generated implementation work into verifier-owned,
PR-gated, auditable software-delivery units by requiring code changes, tests,
evidence artifacts, terminal state, clean git state, pushed branch, and PR URL
before the loop can pass.

This repository contains **SkillOps LoopStack**: a checkpointed loop kernel that
converts coding-agent output into replayable, evidence-verified, resumable,
PR-gated software-delivery runs. Beyond verifying a diff you hand it, the kernel
now also **drives** a coding agent to produce the work inside the loop,
**stacks** loops (depth-capped nesting), and proposes **gate-verified
self-improvements as PRs** — all under the same rule: nothing passes because an
agent says it passed.

## Core rule

> Nothing passes because an agent says it passed. It passes only when the
> LoopStack can **replay** the run, **verify** the evidence, **resume** from
> checkpoints, and emit a **valid terminal state**.

Agent explanations, summaries, and confidence statements are never evidence —
neither are an agent's claims that it implemented a change or that a change is an
improvement. Diffs, tests, the verifier, and the regression gate decide, from
the persisted ledger.

## Capabilities

The kernel composes three evidence-gated pillars (detailed sections below):

1. **[Agent-driven mode](#agent-driven-mode-referee--driver)** — a loop step
   drives a real coding agent to generate the diff; the agent's narrative is
   never evidence.
2. **[Stacked (nested) loops](#stacked-nested-loops)** — a loop dispatches a
   child loop as its own ledgered run, bounded by `MAX_LOOP_DEPTH`.
3. **[Recursive self-improvement](#recursive-self-improvement-bounded-gated)** —
   a meta-loop generates a candidate loop, re-runs it, and a regression gate
   mechanically confirms the improvement before emitting a PR.

## Non-negotiable execution rules

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
- **No auto-merge and no auto-deploy.** A PR may be created; nothing is merged or
  deployed automatically. Agent-authored skills and self-improvement candidates
  enter the registry as `candidate` only; promotion to production is
  human-gated.

## Scope

Checkpointed loop kernel + schemas + SQLite persistence + Governor + CLI + tests
+ Git/PR gates. Loops: a `coding-pr-gate` reference loop and its agent-driven
variant, a stacked parent/child pair, and a self-improvement meta-loop (see
**Loop catalog**). Step handlers added beyond the base gates: `agent_execute`
(drive an agent), `run_subloop` (dispatch a child loop), `regression_gate`
(mechanically compare candidate vs. baseline). No dashboard, no chat
integrations, no hot-reload, **no auto-merge / no auto-deploy**, and no
autonomous production/skill promotion.

## Loop catalog

| manifest | role |
|----------|------|
| `loops/coding-pr-gate.yaml` | verify a diff you hand it (reference loop) |
| `loops/coding-pr-gate-agent.yaml` | **drive** an agent to generate the diff inside the loop |
| `loops/stacked-parent.yaml` | dispatches a child loop as a nested run |
| `loops/stacked-child.yaml` | minimal child loop (any loop can be a child) |
| `loops/self-improve.yaml` | meta-loop: baseline → agent candidate → re-run → regression gate → PR |
| `loops/examples/improve-target.yaml` | deliberately failing baseline fixture for `self-improve` |

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

# Agent-driven / stacked / self-improving loops (same CLI)
python -m skillops loop run --loop loops/coding-pr-gate-agent.yaml
python -m skillops loop run --loop loops/stacked-parent.yaml
python -m skillops loop run --loop loops/self-improve.yaml

# Resume from the last successful checkpoint (no completed step is rerun)
python -m skillops loop resume --run-id <run_id>

# Replay run history from persisted records (surfaces parent_run_id + children)
python -m skillops loop replay --run-id <run_id>

# Inspect run status by id (also reports parent_run_id + child run ids)
python -m skillops run status --run-id <run_id>

# Evaluate UPSHIFT thresholds over run history; emit a promotion candidate
python -m skillops skill promote-check --skill coding-pr-gate
#   --dry-run     assess only, create no candidate
```

## Agent-driven mode (referee → driver)

`loops/coding-pr-gate.yaml` *verifies a diff you hand it*.
`loops/coding-pr-gate-agent.yaml` adds an `implement` step that **drives a real
coding agent to generate the diff inside the loop** — the harness becomes a
driver, with every existing gate as the agent's safety rail.

```bash
# task.md describes the change; the agent CLI is configured in the manifest
python -m skillops loop run --loop loops/coding-pr-gate-agent.yaml
```

Configure the agent in the `implement` step (`skillops/agents.py`):
- `agent: shell` + `command: ["claude", "-p"]` (or `codex`, `cursor-agent`), or
- pass a programmatic adapter: `Engine(options={"agent_adapter": <callable>,
  "task": "..."})`.

**The agent's narrative is never evidence.** Its stdout is logged (secret-
scrubbed) to `agent-output.log`, but pass is decided only by the resulting
**staged diff** (the step escalates if the agent produced no change), then the
**tests** and **verifier** gates. No adapter / no task / no change → fail closed.

## Stacked (nested) loops

A loop can **dispatch another loop** via the `run_subloop` step: the child runs
as its own full run (own `run_id`, own terminal state) sharing one audit ledger.
An outer governance loop can thus wrap an inner execution loop.

```bash
# stacked-parent dispatches stacked-child as a nested run
python -m skillops loop run --loop loops/stacked-parent.yaml
python -m skillops loop replay --run-id <parent_run_id>   # shows the child link
```

The `dispatch` step's params: `child_loop` (manifest path), `pass_states`
(child terminal states that count as pass), `child_task`, `child_release`. The
child can be **any** loop, including `coding-pr-gate-agent.yaml`.

**Guardrails.** The child's mechanically-determined terminal state — not its
narrative — decides the parent step. Nesting is bounded by `MAX_LOOP_DEPTH`
(=3; deeper dispatch fails closed) so the run tree stays finite. **Release is
not inherited**: children run with `release=False` unless a parent step sets
`child_release: true`, so auto-merge/deploy stay off. Every child persists with
its `parent_run_id`, so the full nested tree is replayable from one ledger.

## Recursive self-improvement (bounded, gated)

`loops/self-improve.yaml` is a **meta-loop**: it runs a target loop to record a
**baseline**, drives the agent to write an **improved candidate loop** (a
*separate* manifest), re-runs the candidate, and a mechanical **regression gate**
confirms the candidate is not worse than baseline — then emits a PR.

```bash
python -m skillops loop run --loop loops/self-improve.yaml            # gate only
python -m skillops loop run --loop loops/self-improve.yaml --release  # + candidate PR
```

The decision is **verified, not claimed**: `regression_gate` reads each run's
terminal state + evidence count *from the ledger* and compares
`(pass_flag, evidence_count)` — the agent's "it's better" is never evidence
(`regression-gate.json` records both run ids, loop paths, terminals, and scores).

**Guardrails.** (1) *No in-place self-modification* — the candidate must be a
separate manifest; the gate fails closed if candidate path == baseline path.
(2) *Improvement verified, not claimed* — fail closed unless the candidate
reaches a PASS terminal and does not regress. (3) *Bounded recursion* — nested
re-runs go through `run_subloop` (`MAX_LOOP_DEPTH`); one candidate per meta-run.
(4) *Human gate to land* — output is a PR; promotion to production stays
human-approved (`skill promote`). (5) *One ledger* — baseline, candidate, and
meta runs are all linked and replayable.

## Skill promotion (v0: candidate only)

`skill promote-check` mechanically evaluates the UPSHIFT thresholds against
persisted run history — ≥3 successful comparable runs, ≥0.90 pass rate over the
last 10, zero rollbacks, zero human overrides, candidate package validates, and
the latest successful run replays — and **fails closed** otherwise. On pass it
emits `PROMOTION_CANDIDATE_CREATED` with a candidate package reference,
validation log, and promotion checklist, plus a durable candidate-registry
record at `skills/<id>/candidate/promotion-record.json`.

It **never** promotes autonomously. Promotion to `promoted` is a separate,
human-gated step:

```bash
python -m skillops skill promote --skill coding-pr-gate --approve <approver>
```

`skill promote` fails closed unless (a) a human `--approve` identity is given,
(b) a promotion candidate already exists, and (c) the candidate is still
UPSHIFT-eligible (validation, tests, replay, verifier approval). On pass it
emits `SKILL_PROMOTED` with the contract-mapped evidence — promotion record,
tests, replay report, verifier approval, registry update — flips
`skills/<id>/skill.yaml` to `status: promoted`, and writes a durable
`skills/<id>/promoted/promotion-record.json`. Re-promoting an already-promoted
skill fails closed.

## Data model (SQLite, `artifacts/skillops.db`)

| table | purpose |
|-------|---------|
| `runs` | run id, loop id, status, terminal state, timestamps, artifacts dir, `parent_run_id` (nested runs) |
| `step_runs` | step id, owner role, status, inputs, outputs, attempt, evidence |
| `checkpoints` | run/step, sequence, state snapshot, resume pointer |
| `decisions` | Governor decision, reason code, input-state hash, next action |
| `artifacts` | evidence name, path, sha256, kind — tied to the run id |

Every executable step writes a step record; every completed step writes a
checkpoint; every loop-direction choice writes a decision record.

## Governor v0

Decisions: `CONTINUE`, `RETRY`, `DOWNSHIFT`, `UPSHIFT`, `ESCALATE`, `STOP`.
The Governor decides only from gate results and the recorded attempt count
(same-failure limit = 3, iteration cap is mechanical). `UPSHIFT` is exercised by
the promotion path when the candidate clears every threshold; `DOWNSHIFT` is
reserved for future level transitions.

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
lock, and PR evidence mapping — plus the three pillars: agent-driven execution
(`test_agent_execute.py`), stacked/nested loops with ledger linkage
(`test_stacked_loops.py`), and gated self-improvement (`test_self_improve.py`).
