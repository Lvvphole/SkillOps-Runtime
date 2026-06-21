"""Verifier - checks artifacts only. Intentions and agent claims never count.

The verifier inspects files, records, and command outputs tied to a run id and
returns a structured report. It owns terminal-state approval: a run cannot
reach a PASS terminal state unless the verifier confirms the mapped evidence.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List

from skillops.gitsafety import scan_conflict_markers
from skillops.store import Store
from skillops.terminal import is_valid_terminal_string, required_evidence_for

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),                 # AWS access key id
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),             # GitHub PAT
]


@dataclass
class VerifierReport:
    run_id: str
    approved: bool
    checks: List[Dict[str, object]] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    conflict_files: List[str] = field(default_factory=list)
    secret_hits: List[str] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail})

    def to_markdown(self) -> str:
        lines = [
            "# Verification Report",
            f"- run_id: {self.run_id}",
            f"- approved: {self.approved}",
            "",
            "## Checks",
        ]
        for c in self.checks:
            mark = "PASS" if c["ok"] else "FAIL"
            lines.append(f"- [{mark}] {c['check']}: {c['detail']}")
        if self.missing_evidence:
            lines.append("\n## Missing evidence")
            lines += [f"- {m}" for m in self.missing_evidence]
        if self.conflict_files:
            lines.append("\n## Conflict markers")
            lines += [f"- {f}" for f in self.conflict_files]
        if self.secret_hits:
            lines.append("\n## Secret-like content (BLOCKER)")
            lines += [f"- {s}" for s in self.secret_hits]
        return "\n".join(lines) + "\n"


def scan_secrets(artifacts_dir: str) -> List[str]:
    """Evidence artifacts must not contain secrets."""
    hits: List[str] = []
    for root, _dirs, files in os.walk(artifacts_dir):
        for fn in files:
            path = os.path.join(root, fn)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            for pat in SECRET_PATTERNS:
                if pat.search(text):
                    hits.append(os.path.relpath(path, artifacts_dir))
                    break
    return hits


def verify_run(store: Store, run_id: str, repo: str, artifacts_dir: str,
               required_artifacts: List[str]) -> VerifierReport:
    """Mechanically verify a run's artifacts. Returns a structured report."""
    report = VerifierReport(run_id=run_id, approved=False)

    run = store.get_run(run_id)
    report.add("run_record_exists", run is not None,
               "runs row present" if run else "no run row")

    # Evidence artifacts must exist on disk, be non-empty, and be registered.
    for name in required_artifacts:
        path = os.path.join(artifacts_dir, name)
        on_disk = os.path.exists(path) and os.path.getsize(path) > 0
        registered = store.has_artifact(run_id, name)
        ok = on_disk and registered
        if not ok:
            report.missing_evidence.append(name)
        report.add(f"evidence:{name}", ok,
                   f"on_disk={on_disk} registered={registered}")

    # Every completed step must have a checkpoint (no checkpoint gaps).
    completed = store.completed_step_ids(run_id)
    checkpoints = {c["step_id"] for c in store.get_checkpoints(run_id)}
    gap = [s for s in completed if s not in checkpoints]
    report.add("no_checkpoint_gap", not gap,
               f"uncheckpointed completed steps: {gap}")

    # Every decision is recorded (presence of decision rows for the run).
    decisions = store.get_decisions(run_id)
    report.add("decisions_recorded", len(decisions) > 0,
               f"{len(decisions)} decision records")

    # Conflict markers in tracked files fail verification.
    report.conflict_files = scan_conflict_markers(repo)
    report.add("no_conflict_markers", not report.conflict_files,
               f"{len(report.conflict_files)} files with markers")

    # Secrets must not leak into artifacts.
    report.secret_hits = scan_secrets(artifacts_dir)
    report.add("no_secrets_in_artifacts", not report.secret_hits,
               f"{len(report.secret_hits)} suspicious files")

    report.approved = all(c["ok"] for c in report.checks)
    return report


def verify_terminal_state(state: str, artifacts_dir: str, store: Store,
                          run_id: str) -> Dict[str, object]:
    """Terminal-state approval is verifier-owned. A state is valid only if it
    is registered AND every mapped evidence artifact exists for the run.
    """
    if not is_valid_terminal_string(state):
        return {"valid": False, "reason": f"unregistered terminal state '{state}'",
                "missing": []}
    missing = []
    for name in required_evidence_for(state):
        path = os.path.join(artifacts_dir, name)
        if not (os.path.exists(path) and os.path.getsize(path) > 0
                and store.has_artifact(run_id, name)):
            missing.append(name)
    return {"valid": len(missing) == 0, "reason": "ok" if not missing
            else "missing mapped evidence", "missing": missing}
