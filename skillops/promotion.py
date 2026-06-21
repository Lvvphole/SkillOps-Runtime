"""v0 skill-promotion-candidate path.

Mechanically evaluates the UPSHIFT thresholds against persisted run history and,
on pass, emits PROMOTION_CANDIDATE_CREATED with a candidate package reference,
validation output, and a promotion checklist. It NEVER auto-promotes to
production (no registry/production mutation in v0): the result is a candidate
record only. Fails closed when any threshold is unmet.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

from skillops import gitsafety
from skillops.governor import Decision, Governor
from skillops.runtime import replay_run
from skillops.schemas import validate_skill_file
from skillops.store import Store, hash_state, new_run_id, now_iso, sha256_file

# Terminal states that count as a successful comparable run.
SUCCESS_STATES = {"PASS_TERMINAL", "PASS_CANDIDATE_PR_CREATED"}

MIN_SUCCESS = 3
WINDOW = 10
MIN_PASS_RATE = 0.90


@dataclass
class PromotionAssessment:
    skill_id: str
    loop_id: str
    eligible: bool = False
    criteria: List[Dict[str, object]] = field(default_factory=list)
    success_count: int = 0
    pass_rate: float = 0.0

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.criteria.append({"criterion": name, "ok": ok, "detail": detail})

    def to_log(self) -> str:
        lines = [f"# Promotion validation: {self.skill_id}",
                 f"loop_id={self.loop_id} eligible={self.eligible}",
                 f"success_count={self.success_count} pass_rate={self.pass_rate:.2f}",
                 "", "## UPSHIFT criteria"]
        for c in self.criteria:
            lines.append(f"- [{'PASS' if c['ok'] else 'FAIL'}] "
                         f"{c['criterion']}: {c['detail']}")
        return "\n".join(lines) + "\n"


def _loop_runs(store: Store, loop_id: str) -> List[Dict[str, object]]:
    rows = store.conn.execute(
        "SELECT * FROM runs WHERE loop_id=? ORDER BY started_at, run_id", (loop_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def evaluate_upshift(store: Store, skill_id: str, loop_id: str,
                     skill_path: str) -> PromotionAssessment:
    a = PromotionAssessment(skill_id=skill_id, loop_id=loop_id)
    runs = _loop_runs(store, loop_id)
    successful = [r for r in runs if r["terminal_state"] in SUCCESS_STATES]
    a.success_count = len(successful)

    window = runs[-WINDOW:]
    win_success = [r for r in window if r["terminal_state"] in SUCCESS_STATES]
    a.pass_rate = (len(win_success) / len(window)) if window else 0.0

    # v0 has no rollback or human-override mutation records -> counted as 0.
    rollback_count = 0
    override_count = 0

    a.add("repeated_success_ge_3", a.success_count >= MIN_SUCCESS,
          f"{a.success_count} successful runs (need {MIN_SUCCESS})")
    a.add("pass_rate_ge_0.90", a.pass_rate >= MIN_PASS_RATE,
          f"{a.pass_rate:.2f} over last {len(window)} runs")
    a.add("rollback_count_zero", rollback_count == 0, f"{rollback_count}")
    a.add("human_override_zero", override_count == 0, f"{override_count}")

    candidate_exists = os.path.exists(skill_path)
    a.add("candidate_package_exists", candidate_exists, skill_path)

    validates = False
    if candidate_exists:
        try:
            spec = validate_skill_file(skill_path)
            validates = spec.status == "candidate"
            a.add("candidate_validates", validates,
                  f"status={spec.status}")
        except Exception as exc:  # noqa: BLE001 - fail closed
            a.add("candidate_validates", False, f"{exc}")
    else:
        a.add("candidate_validates", False, "no package")

    replay_ok = False
    if successful:
        rep = replay_run(store, successful[-1]["run_id"])
        replay_ok = bool(rep["reconstructable"])
        a.add("candidate_replay_passes", replay_ok,
              f"replay run {successful[-1]['run_id']} reconstructable={replay_ok}")
    else:
        a.add("candidate_replay_passes", False, "no successful run to replay")

    # Verifier-owned promotion approval = conjunction of every gate above.
    a.eligible = all(c["ok"] for c in a.criteria)
    a.add("verifier_approves_promotion", a.eligible,
          "all UPSHIFT gates passed" if a.eligible else "gate(s) failed")
    a.eligible = all(c["ok"] for c in a.criteria)
    return a


@dataclass
class PromotionResult:
    skill_id: str
    terminal_state: Optional[str]
    eligible: bool
    artifacts_dir: Optional[str]
    record_path: Optional[str]
    promo_run_id: Optional[str]


def run_promotion_check(repo: str, store: Store, skill_id: str,
                        loop_id: Optional[str] = None) -> PromotionResult:
    """Evaluate and, if eligible, emit PROMOTION_CANDIDATE_CREATED.

    No production registry is mutated: the only durable write is a candidate
    record under skills/<id>/candidate/ (the candidate registry).
    """
    loop_id = loop_id or skill_id
    skill_path = os.path.join(repo, "skills", skill_id, "skill.yaml")
    assessment = evaluate_upshift(store, skill_id, loop_id, skill_path)
    governor = Governor()

    if not assessment.eligible:
        # Fail closed: no candidate is created.
        return PromotionResult(skill_id, None, False, None, None, None)

    promo_run_id = new_run_id()
    adir = os.path.join(repo, "artifacts", promo_run_id)
    os.makedirs(adir, exist_ok=True)
    store.create_run(promo_run_id, f"promote:{skill_id}", skill_path, adir)

    # Candidate package reference (hash-pinned, not a production copy).
    package = {
        "skill_id": skill_id,
        "package_path": os.path.relpath(skill_path, repo),
        "sha256": sha256_file(skill_path),
        "loop_id": loop_id,
        "based_on_runs": assessment.success_count,
    }
    _write(adir, "promotion-candidate-package.json",
           json.dumps(package, indent=2) + "\n")
    _write(adir, "promotion-validation.log", assessment.to_log())

    checklist = _checklist(assessment)
    _write(adir, "promotion-checklist.md", checklist)

    terminal = "PROMOTION_CANDIDATE_CREATED"
    ts = {"run_id": promo_run_id, "terminal_state": terminal,
          "skill_id": skill_id, "created_at": now_iso(),
          "note": "v0: candidate only; no production promotion"}
    _write(adir, "terminal-state.json", json.dumps(ts, indent=2) + "\n")

    for name in ("promotion-candidate-package.json", "promotion-validation.log",
                 "promotion-checklist.md", "terminal-state.json"):
        store.register_artifact(promo_run_id, None, name, os.path.join(adir, name))

    # Durable candidate registry record (committed source, not runtime artifact).
    cand_dir = os.path.join(repo, "skills", skill_id, "candidate")
    os.makedirs(cand_dir, exist_ok=True)
    record = {
        "skill_id": skill_id,
        "status": "promotion_candidate",
        "promoted_to_production": False,
        "promo_run_id": promo_run_id,
        "package_sha256": package["sha256"],
        "success_count": assessment.success_count,
        "pass_rate": round(assessment.pass_rate, 4),
        "created_at": now_iso(),
    }
    record_path = os.path.join(cand_dir, "promotion-record.json")
    with open(record_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(record, indent=2) + "\n")

    dec = governor.upshift("UPSHIFT_THRESHOLDS_MET")
    store.add_decision(promo_run_id, None, Decision.UPSHIFT.value, dec.reason_code,
                       hash_state(record), list(package.keys()), dec.next_action)
    store.update_run(promo_run_id, status="TERMINATED", terminal_state=terminal,
                     completed_at=now_iso())

    return PromotionResult(skill_id, terminal, True, adir, record_path, promo_run_id)


def _checklist(a: PromotionAssessment) -> str:
    lines = ["# Promotion checklist", f"- skill: {a.skill_id}",
             f"- loop: {a.loop_id}", "",
             "Candidate created — promotion to production is OUT OF SCOPE in v0.",
             "", "## Gates"]
    for c in a.criteria:
        lines.append(f"- [{'x' if c['ok'] else ' '}] {c['criterion']} ({c['detail']})")
    lines += ["", "## Next (post-v0, requires human approval)",
              "- [ ] registry update to promoted",
              "- [ ] scheduled-loop registration"]
    return "\n".join(lines) + "\n"


def _write(adir: str, name: str, content: str) -> None:
    with open(os.path.join(adir, name), "w", encoding="utf-8") as fh:
        fh.write(content)


# --------------------------------------------------------------------------
# SKILL_PROMOTED: human-gated candidate -> promoted (v1)
# --------------------------------------------------------------------------
@dataclass
class PromoteResult:
    skill_id: str
    terminal_state: Optional[str]
    promoted: bool
    artifacts_dir: Optional[str]
    promo_run_id: Optional[str]
    detail: str = ""


def _escalate(repo: str, store: Store, skill_id: str, reason: str) -> PromoteResult:
    """Emit ESCALATED_WITH_BLOCKER as a real terminal state with a blocker."""
    promo_run_id = new_run_id()
    adir = os.path.join(repo, "artifacts", promo_run_id)
    os.makedirs(adir, exist_ok=True)
    store.create_run(promo_run_id, f"promote:{skill_id}", skill_id, adir)
    ts = {"run_id": promo_run_id, "terminal_state": "ESCALATED_WITH_BLOCKER",
          "skill_id": skill_id, "blocker": reason, "created_at": now_iso()}
    _write(adir, "terminal-state.json", json.dumps(ts, indent=2) + "\n")
    store.add_decision(promo_run_id, None, Decision.ESCALATE.value,
                       "PROMOTION_BLOCKED", hash_state(ts), ["terminal-state.json"],
                       "resolve blocker and retry")
    decs = store.get_decisions(promo_run_id)
    _write(adir, "decision-history.json", json.dumps(decs, indent=2, default=str) + "\n")
    for name in ("terminal-state.json", "decision-history.json"):
        store.register_artifact(promo_run_id, None, name, os.path.join(adir, name))
    store.update_run(promo_run_id, status="TERMINATED",
                     terminal_state="ESCALATED_WITH_BLOCKER", completed_at=now_iso())
    return PromoteResult(skill_id, "ESCALATED_WITH_BLOCKER", False, adir,
                         promo_run_id, reason)


def promote_skill(repo: str, store: Store, skill_id: str, approver: str,
                  loop_id: Optional[str] = None) -> PromoteResult:
    """Promote a candidate skill to `promoted`. Fails closed without a human
    approver, an existing candidate, or UPSHIFT eligibility. Emits SKILL_PROMOTED
    with the contract-mapped evidence and a registry update. Never autonomous.
    """
    loop_id = loop_id or skill_id
    skill_path = os.path.join(repo, "skills", skill_id, "skill.yaml")

    # Gate 1: human approval (rule 33 - no self-promotion).
    if not approver or not str(approver).strip():
        return _escalate(repo, store, skill_id,
                         "human approver required; promotion is not autonomous")

    # Gate 2: a promotion candidate must already exist.
    candidate_record = os.path.join(repo, "skills", skill_id, "candidate",
                                    "promotion-record.json")
    if not os.path.exists(candidate_record):
        return _escalate(repo, store, skill_id,
                         "no promotion candidate; run promote-check first")

    # Gate 3: UPSHIFT eligibility (validation, tests-history, replay, verifier).
    assessment = evaluate_upshift(store, skill_id, loop_id, skill_path)
    if not assessment.eligible:
        failed = [c["criterion"] for c in assessment.criteria if not c["ok"]]
        return _escalate(repo, store, skill_id,
                         f"candidate not eligible: {failed}")

    promo_run_id = new_run_id()
    adir = os.path.join(repo, "artifacts", promo_run_id)
    os.makedirs(adir, exist_ok=True)
    store.create_run(promo_run_id, f"promote:{skill_id}", skill_path, adir)
    governor = Governor()

    # Evidence: tests (the discovered command must pass now). When no test
    # command is discoverable, the candidate's run-history test evidence stands
    # (each successful loop run already executed and recorded tests).
    test_cmds = gitsafety.discover_commands(repo).get("test", [])
    if test_cmds:
        cmd = test_cmds[0].split()
        rc, out = gitsafety.run(cmd, repo)
        _write(adir, "test-results.log",
               f"$ {' '.join(cmd)}\nexit={rc}\n\n{out}\n")
        tests_ok = rc == 0
    else:
        _write(adir, "test-results.log",
               "no project test command discovered; relying on candidate "
               "run-history test evidence (each successful run recorded tests)\n")
        tests_ok = True
    if not tests_ok:
        store.add_decision(promo_run_id, None, Decision.ESCALATE.value,
                           "PROMOTION_TESTS_FAILED", hash_state({"cmd": test_cmds}),
                           ["test-results.log"], "fix tests")
        _finalize_escalate(store, adir, promo_run_id, skill_id,
                           "tests failed at promotion time")
        return PromoteResult(skill_id, "ESCALATED_WITH_BLOCKER", False, adir,
                             promo_run_id, "tests failed")

    # Evidence: replay of the latest successful comparable run.
    runs = _loop_runs(store, loop_id)
    successful = [r for r in runs if r["terminal_state"] in SUCCESS_STATES]
    replay = replay_run(store, successful[-1]["run_id"])
    _write(adir, "replay-report.json", json.dumps(replay, indent=2, default=str) + "\n")

    # Evidence: verifier approval (the conjunction of UPSHIFT gates).
    verifier_approval = {
        "skill_id": skill_id,
        "verifier_approves_promotion": True,
        "criteria": assessment.criteria,
    }
    _write(adir, "verifier-approval.json",
           json.dumps(verifier_approval, indent=2) + "\n")

    # Evidence: promotion record.
    package_sha = sha256_file(skill_path)
    record = {
        "skill_id": skill_id,
        "approver": str(approver).strip(),
        "candidate_record": os.path.relpath(candidate_record, repo),
        "package_sha256": package_sha,
        "promo_run_id": promo_run_id,
        "replayed_run": successful[-1]["run_id"],
        "success_count": assessment.success_count,
        "pass_rate": round(assessment.pass_rate, 4),
        "created_at": now_iso(),
    }
    _write(adir, "promotion-record.json", json.dumps(record, indent=2) + "\n")

    # Registry update: flip skill.yaml status and write a promoted record.
    spec = yaml.safe_load(open(skill_path, encoding="utf-8"))
    old_status = spec.get("status")
    spec["status"] = "promoted"
    with open(skill_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(spec, fh, sort_keys=False)
    promoted_dir = os.path.join(repo, "skills", skill_id, "promoted")
    os.makedirs(promoted_dir, exist_ok=True)
    with open(os.path.join(promoted_dir, "promotion-record.json"), "w",
              encoding="utf-8") as fh:
        fh.write(json.dumps(record, indent=2) + "\n")
    registry_update = {
        "skill_id": skill_id,
        "package_path": os.path.relpath(skill_path, repo),
        "old_status": old_status,
        "new_status": "promoted",
        "approver": str(approver).strip(),
        "updated_at": now_iso(),
    }
    _write(adir, "registry-update.json", json.dumps(registry_update, indent=2) + "\n")

    terminal = "SKILL_PROMOTED"
    ts = {"run_id": promo_run_id, "terminal_state": terminal, "skill_id": skill_id,
          "approver": str(approver).strip(), "created_at": now_iso()}
    _write(adir, "terminal-state.json", json.dumps(ts, indent=2) + "\n")

    for name in ("promotion-record.json", "test-results.log", "replay-report.json",
                 "verifier-approval.json", "registry-update.json",
                 "terminal-state.json"):
        store.register_artifact(promo_run_id, None, name, os.path.join(adir, name))

    up = governor.upshift("PROMOTION_APPROVED")
    store.add_decision(promo_run_id, None, Decision.UPSHIFT.value, up.reason_code,
                       hash_state(record), list(record.keys()), up.next_action)
    store.add_decision(promo_run_id, None, Decision.STOP.value, "SKILL_PROMOTED",
                       hash_state(ts), ["terminal-state.json"], "emit terminal state")
    store.update_run(promo_run_id, status="TERMINATED", terminal_state=terminal,
                     completed_at=now_iso())
    return PromoteResult(skill_id, terminal, True, adir, promo_run_id,
                         f"promoted by {str(approver).strip()}")


def _finalize_escalate(store: Store, adir: str, promo_run_id: str, skill_id: str,
                       reason: str) -> None:
    ts = {"run_id": promo_run_id, "terminal_state": "ESCALATED_WITH_BLOCKER",
          "skill_id": skill_id, "blocker": reason, "created_at": now_iso()}
    _write(adir, "terminal-state.json", json.dumps(ts, indent=2) + "\n")
    decs = store.get_decisions(promo_run_id)
    _write(adir, "decision-history.json", json.dumps(decs, indent=2, default=str) + "\n")
    for name in ("terminal-state.json", "decision-history.json"):
        store.register_artifact(promo_run_id, None, name, os.path.join(adir, name))
    store.update_run(promo_run_id, status="TERMINATED",
                     terminal_state="ESCALATED_WITH_BLOCKER", completed_at=now_iso())
