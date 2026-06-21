"""The LoopStack engine: executes steps, persists everything, and emits a
mechanically-determined terminal state. Also provides resume, replay, status.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from skillops.governor import Decision, Governor
from skillops.schemas import LoopSpec, validate_loop_file
from skillops.steps import HANDLERS, StepContext, StepResult
from skillops.store import Store, hash_state, new_run_id, now_iso
from skillops.terminal import TerminalState, required_evidence_for
from skillops.verifier import verify_terminal_state


def artifacts_dir_for(repo: str, run_id: str) -> str:
    return os.path.join(repo, "artifacts", run_id)


@dataclass
class RunResult:
    run_id: str
    terminal_state: str
    status: str
    artifacts_dir: str


class Engine:
    def __init__(self, repo: str, db_path: Optional[str] = None,
                 options: Optional[Dict[str, object]] = None):
        self.repo = os.path.abspath(repo)
        self.db_path = db_path or os.path.join(self.repo, "artifacts", "skillops.db")
        self.store = Store(self.db_path)
        self.options = options or {}
        self.governor = Governor()
        self._log: List[str] = []

    def log(self, msg: str) -> None:
        line = f"[{now_iso()}] {msg}"
        self._log.append(line)

    # ---- public entry points ---------------------------------------------
    def run(self, loop_path: str) -> RunResult:
        loop = validate_loop_file(loop_path)
        run_id = new_run_id()
        adir = artifacts_dir_for(self.repo, run_id)
        os.makedirs(adir, exist_ok=True)
        self.store.create_run(run_id, loop.loop_id, os.path.abspath(loop_path), adir)
        self.log(f"run start run_id={run_id} loop={loop.loop_id}")
        return self._execute(run_id, loop, loop_path, adir, start_index=0)

    def resume(self, run_id: str) -> RunResult:
        run = self.store.get_run(run_id)
        if not run:
            raise ValueError(f"unknown run_id: {run_id}")
        loop_path = run["loop_path"]
        loop = validate_loop_file(loop_path)
        adir = run["artifacts_dir"]
        cp = self.store.last_checkpoint(run_id)
        start_index = cp["resume_pointer"] if cp else 0
        done = self.store.completed_step_ids(run_id)
        self.log(f"resume run_id={run_id} from checkpoint={'none' if not cp else cp['seq']} "
                 f"start_index={start_index} completed={done}")
        self.store.update_run(run_id, status="RUNNING", terminal_state=None,
                              completed_at=None)
        return self._execute(run_id, loop, loop_path, adir, start_index=start_index)

    # ---- core loop -------------------------------------------------------
    def _execute(self, run_id: str, loop: LoopSpec, loop_path: str,
                 adir: str, start_index: int) -> RunResult:
        ctx = StepContext(run_id=run_id, repo=self.repo, artifacts_dir=adir,
                          store=self.store, loop=loop, loop_path=loop_path,
                          options=self.options, state={})
        release = bool(self.options.get("release", False))
        iteration = self.store.get_run(run_id)["iteration"]
        terminal: Optional[str] = None
        escalated = False

        for idx in range(start_index, len(loop.steps)):
            step = loop.steps[idx]

            # Release-gated steps are skipped unless release is requested.
            if step.release and not release:
                row = self.store.start_step(run_id, step.id, step.owner, {}, attempt=1)
                self.store.complete_step(row, "SKIPPED", {"reason": "release_disabled"}, [])
                self._record_decision(run_id, step.id, Decision.CONTINUE,
                                      "RELEASE_DISABLED", {}, [], "skip release step")
                self.log(f"step {step.id} SKIPPED (release disabled)")
                continue

            handler = HANDLERS.get(step.handler)
            if handler is None:
                self.log(f"step {step.id} ERROR: unknown handler '{step.handler}'")
                self._record_decision(run_id, step.id, Decision.ESCALATE,
                                      "UNKNOWN_HANDLER", {}, [], "escalate")
                terminal = TerminalState.ESCALATED_WITH_BLOCKER.value
                escalated = True
                break

            result, attempt = self._run_step_with_retries(ctx, loop, run_id, step,
                                                           iteration)
            iteration += attempt
            self.store.update_run(run_id, iteration=iteration)

            is_last = idx == len(loop.steps) - 1
            decision = self.governor.decide(
                step_ok=result.ok, attempt=attempt, required=step.required,
                is_last_step=is_last, iteration=iteration,
                max_iterations=loop.max_iterations, reason=result.message or "",
            )
            self._record_decision(run_id, step.id, decision.decision,
                                  decision.reason_code,
                                  {"ok": result.ok, "attempt": attempt},
                                  result.evidence, decision.next_action)

            if not result.ok:
                self.log(f"step {step.id} FAILED after {attempt} attempt(s): {result.message}")
                if result.escalate or decision.decision == Decision.ESCALATE:
                    terminal = TerminalState.ESCALATED_WITH_BLOCKER.value
                    escalated = True
                else:
                    terminal = TerminalState.FAIL_RECOVERABLE.value
                break

            # Success: checkpoint after the completed step (no checkpoint gaps).
            snapshot = {
                "completed_steps": self.store.completed_step_ids(run_id),
                "step_id": step.id,
                "outputs": result.outputs,
            }
            self.store.add_checkpoint(run_id, step.id, snapshot, resume_pointer=idx + 1)
            self.log(f"step {step.id} COMPLETED, checkpoint -> resume_pointer={idx + 1}")

        else:
            # Loop finished without break -> all steps done.
            terminal = self._determine_pass_state(ctx, loop, release)

        if terminal is None:
            terminal = self._determine_pass_state(ctx, loop, release)

        return self._finalize(run_id, loop, adir, terminal, escalated)

    def _run_step_with_retries(self, ctx: StepContext, loop: LoopSpec, run_id: str,
                               step, iteration: int):
        attempt = 0
        result = StepResult(ok=False, message="not run")
        while attempt < self.governor.same_failure_limit:
            attempt += 1
            row = self.store.start_step(run_id, step.id, step.owner,
                                        {"params": step.params}, attempt=attempt)
            try:
                result = HANDLERS[step.handler](ctx, step)
            except Exception as exc:  # noqa: BLE001
                result = StepResult(ok=False, message=f"handler raised: {exc}")
            status = "COMPLETED" if result.ok else "FAILED"
            self.store.complete_step(row, status, result.outputs, result.evidence)
            for name in result.evidence:
                path = os.path.join(ctx.artifacts_dir, name)
                self.store.register_artifact(run_id, step.id, name, path)
            if result.ok:
                break
            if result.escalate or not step.required:
                break
            self.log(f"step {step.id} attempt {attempt} failed; governor RETRY")
        return result, attempt

    def _record_decision(self, run_id, step_id, decision, reason, state, evidence,
                         next_action):
        self.store.add_decision(run_id, step_id,
                                decision.value if hasattr(decision, "value") else decision,
                                reason, hash_state(state), evidence, next_action)

    def _determine_pass_state(self, ctx: StepContext, loop: LoopSpec,
                              release: bool) -> str:
        verifier = ctx.state.get("verifier")
        evaluator = ctx.state.get("evaluator")
        v_ok = bool(verifier and verifier.approved)
        e_ok = bool(evaluator and evaluator.passed)
        # Fall back to persisted step outcomes (e.g. on resume, when verify and
        # evaluate already completed in an earlier execution slice).
        if not v_ok or not e_ok:
            steps = {s["step_id"]: s["status"]
                     for s in self.store.get_steps(ctx.run_id)}
            for s in loop.steps:
                if s.handler == "verifier_report" and steps.get(s.id) == "COMPLETED":
                    v_ok = True
                if s.handler == "evaluator_report" and steps.get(s.id) == "COMPLETED":
                    e_ok = True
        if not (v_ok and e_ok):
            return TerminalState.FAIL_RECOVERABLE.value
        # PR-gated pass is decided by produced evidence, not by raw options.
        if release and self.store.has_artifact(ctx.run_id, "pr-url.txt"):
            return TerminalState.PASS_CANDIDATE_PR_CREATED.value
        return TerminalState.PASS_TERMINAL.value

    def _finalize(self, run_id: str, loop: LoopSpec, adir: str, terminal: str,
                  escalated: bool) -> RunResult:
        # Write finalize artifacts before terminal-state verification.
        checkpoints = self.store.get_checkpoints(run_id)
        decisions = self.store.get_decisions(run_id)
        self._dump(adir, "checkpoint-history.json", checkpoints)
        self._dump(adir, "decision-history.json", decisions)

        # Terminal state must be registered in the manifest and map to evidence.
        if terminal not in loop.terminal_states:
            terminal = TerminalState.ESCALATED_WITH_BLOCKER.value

        ts_payload = {
            "run_id": run_id,
            "terminal_state": terminal,
            "required_evidence": required_evidence_for(terminal),
            "created_at": now_iso(),
        }
        self._dump(adir, "terminal-state.json", ts_payload)

        for name in ("checkpoint-history.json", "decision-history.json",
                     "terminal-state.json"):
            self.store.register_artifact(run_id, None, name, os.path.join(adir, name))

        # Verifier owns terminal-state approval.
        ts_check = verify_terminal_state(terminal, adir, self.store, run_id)
        if not ts_check["valid"] and terminal in (
            TerminalState.PASS_TERMINAL.value,
            TerminalState.PASS_CANDIDATE_PR_CREATED.value,
        ):
            self.log(f"terminal-state {terminal} rejected: {ts_check}")
            terminal = TerminalState.FAIL_RECOVERABLE.value
            ts_payload["terminal_state"] = terminal
            ts_payload["downgrade_reason"] = ts_check
            ts_payload["required_evidence"] = required_evidence_for(terminal)
            self._dump(adir, "terminal-state.json", ts_payload)
            self.store.register_artifact(run_id, None, "terminal-state.json",
                                         os.path.join(adir, "terminal-state.json"))

        # loop-run.log
        self._write(adir, "loop-run.log", "\n".join(self._log) + "\n")
        self.store.register_artifact(run_id, None, "loop-run.log",
                                     os.path.join(adir, "loop-run.log"))

        status = "TERMINATED"
        self.store.update_run(run_id, status=status, terminal_state=terminal,
                              completed_at=now_iso())
        self.log(f"run {run_id} terminal_state={terminal}")
        # rewrite loop-run.log to include final line
        self._write(adir, "loop-run.log", "\n".join(self._log) + "\n")
        return RunResult(run_id=run_id, terminal_state=terminal, status=status,
                         artifacts_dir=adir)

    def _dump(self, adir: str, name: str, obj) -> None:
        self._write(adir, name, json.dumps(obj, indent=2, default=str) + "\n")

    def _write(self, adir: str, name: str, content: str) -> None:
        with open(os.path.join(adir, name), "w", encoding="utf-8") as fh:
            fh.write(content)


# --------------------------------------------------------------------------
# replay / status (read-only reconstruction from persisted records)
# --------------------------------------------------------------------------
def replay_run(store: Store, run_id: str) -> Dict[str, object]:
    run = store.get_run(run_id)
    if not run:
        raise ValueError(f"unknown run_id: {run_id}")
    steps = store.get_steps(run_id)
    checkpoints = store.get_checkpoints(run_id)
    decisions = store.get_decisions(run_id)
    artifacts = store.get_artifacts(run_id)
    missing = []
    completed = [s for s in steps if s["status"] == "COMPLETED"]
    cp_steps = {c["step_id"] for c in checkpoints}
    for s in completed:
        if s["step_id"] not in cp_steps:
            missing.append(f"checkpoint missing for {s['step_id']}")
    return {
        "run_id": run_id,
        "loop_id": run["loop_id"],
        "terminal_state": run["terminal_state"],
        "ordered_steps": [
            {"step_id": s["step_id"], "owner": s["owner_role"],
             "status": s["status"], "attempt": s["attempt"],
             "evidence": json.loads(s["evidence"] or "[]")}
            for s in steps
        ],
        "checkpoints": [{"seq": c["seq"], "step_id": c["step_id"],
                         "resume_pointer": c["resume_pointer"]} for c in checkpoints],
        "decisions": [{"step_id": d["step_id"], "decision": d["decision"],
                       "reason_code": d["reason_code"],
                       "next_action": d["next_action"]} for d in decisions],
        "artifacts": [{"name": a["name"], "sha256": a["sha256"][:12]} for a in artifacts],
        "missing_records": missing,
        "reconstructable": not missing and run["terminal_state"] is not None,
    }


def status_run(store: Store, run_id: str) -> Dict[str, object]:
    run = store.get_run(run_id)
    if not run:
        raise ValueError(f"unknown run_id: {run_id}")
    cp = store.last_checkpoint(run_id)
    steps = store.get_steps(run_id)
    artifacts = store.get_artifacts(run_id)
    last_step = steps[-1]["step_id"] if steps else None
    return {
        "run_id": run_id,
        "loop_id": run["loop_id"],
        "status": run["status"],
        "terminal_state": run["terminal_state"],
        "last_checkpoint": None if not cp else {"seq": cp["seq"],
                                                "step_id": cp["step_id"],
                                                "resume_pointer": cp["resume_pointer"]},
        "current_step": last_step,
        "evidence_count": len(artifacts),
        "evidence": [a["name"] for a in artifacts],
        "started_at": run["started_at"],
        "completed_at": run["completed_at"],
    }
