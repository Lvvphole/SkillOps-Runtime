"""Evaluator - scores verified artifacts against the loop contract only.

The evaluator adds no subjective criteria. It consumes the verifier report and
the persisted records and decides whether any correction remains.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from skillops.store import Store
from skillops.verifier import VerifierReport


@dataclass
class EvaluationReport:
    run_id: str
    passed: bool
    criteria: List[Dict[str, object]] = field(default_factory=list)
    corrections: List[str] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.criteria.append({"criterion": name, "ok": ok, "detail": detail})
        if not ok:
            self.corrections.append(f"{name}: {detail}")

    def to_markdown(self) -> str:
        lines = [
            "# Evaluation Report",
            f"- run_id: {self.run_id}",
            f"- passed: {self.passed}",
            "",
            "## Contract criteria",
        ]
        for c in self.criteria:
            mark = "PASS" if c["ok"] else "FAIL"
            lines.append(f"- [{mark}] {c['criterion']}: {c['detail']}")
        if self.corrections:
            lines.append("\n## Required corrections")
            lines += [f"- {c}" for c in self.corrections]
        return "\n".join(lines) + "\n"


def evaluate_run(store: Store, run_id: str, verifier: VerifierReport,
                 required_artifacts: List[str]) -> EvaluationReport:
    rep = EvaluationReport(run_id=run_id, passed=False)

    rep.add("verifier_approved", verifier.approved,
            "verifier must approve before evaluation can pass")

    steps = store.get_steps(run_id)
    required_failed = [s for s in steps if s["status"] == "FAILED"]
    rep.add("no_required_step_failed", not required_failed,
            f"{len(required_failed)} failed steps")

    checkpoints = store.get_checkpoints(run_id)
    rep.add("checkpoints_present", len(checkpoints) > 0,
            f"{len(checkpoints)} checkpoints")

    decisions = store.get_decisions(run_id)
    rep.add("decisions_present", len(decisions) > 0,
            f"{len(decisions)} decisions")

    rep.add("evidence_complete", not verifier.missing_evidence,
            f"missing: {verifier.missing_evidence}")

    rep.passed = all(c["ok"] for c in rep.criteria)
    return rep
