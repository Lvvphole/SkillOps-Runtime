"""Step handlers. Each handler observes the repo / runtime and writes evidence.

A handler returns a StepResult. The engine owns persistence (step rows,
artifact registration, checkpoints); handlers only do work and emit files.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List

from skillops import gitsafety
from skillops.evaluator import evaluate_run
from skillops.schemas import validate_loop_file
from skillops.store import Store
from skillops.verifier import verify_run


@dataclass
class StepContext:
    run_id: str
    repo: str
    artifacts_dir: str
    store: Store
    loop: object
    loop_path: str
    options: Dict[str, object] = field(default_factory=dict)
    state: Dict[str, object] = field(default_factory=dict)


@dataclass
class StepResult:
    ok: bool
    outputs: Dict[str, object] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)
    message: str = ""
    escalate: bool = False


def write_artifact(ctx: StepContext, name: str, content: str) -> str:
    path = os.path.join(ctx.artifacts_dir, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return name


def _verifiable_artifact_names(loop) -> List[str]:
    """Artifacts the verifier inspects: gather/test outputs, not reports."""
    skip = {"verifier_report", "evaluator_report"}
    names: List[str] = []
    for s in loop.steps:
        if s.handler in skip or s.release:
            continue
        names.extend(s.produces)
    return names


# --------------------------------------------------------------------------
# Generic handlers (used by tests and as building blocks)
# --------------------------------------------------------------------------
def h_noop(ctx: StepContext, step) -> StepResult:
    ev = []
    for name in step.produces:
        ev.append(write_artifact(ctx, name, f"noop:{step.id}\n"))
    return StepResult(ok=True, evidence=ev, message="noop ok")


def h_always_fail(ctx: StepContext, step) -> StepResult:
    return StepResult(ok=False, message="forced failure")


def h_flaky(ctx: StepContext, step) -> StepResult:
    """Fails until the env var named in params['env'] is set. Used to exercise
    resume-from-checkpoint behaviour deterministically."""
    env = str(step.params.get("env", "SKILLOPS_FLAKY_OK"))
    if os.environ.get(env):
        ev = [write_artifact(ctx, n, f"flaky ok via {env}\n") for n in step.produces]
        return StepResult(ok=True, evidence=ev, message="flaky passed")
    return StepResult(ok=False, message=f"flaky blocked (set {env})")


# --------------------------------------------------------------------------
# coding-pr-gate handlers
# --------------------------------------------------------------------------
def h_record_repo_path(ctx: StepContext, step) -> StepResult:
    repo = os.path.abspath(ctx.repo)
    exists = os.path.isdir(repo)
    body = (
        f"repo_path={repo}\n"
        f"exists={exists}\n"
        f"is_git_repo={os.path.isdir(os.path.join(repo, '.git'))}\n"
        f"note=execution environment is the cloned checkout for this session\n"
    )
    name = write_artifact(ctx, "repo-path-confirmation.txt", body)
    return StepResult(ok=exists, evidence=[name],
                      outputs={"repo_path": repo, "exists": exists})


def h_verify_remote(ctx: StepContext, step) -> StepResult:
    url = gitsafety.remote_url(ctx.repo)
    expect = str(step.params.get("expect_substring", "SkillOps-Runtime")).lower()
    ok = expect in url.lower()
    body = f"remote_origin={url}\nexpect_substring={expect}\nmatch={ok}\n"
    name = write_artifact(ctx, "git-remote-verification.txt", body)
    return StepResult(ok=ok, evidence=[name], outputs={"remote": url},
                      message="remote verified" if ok else "remote mismatch")


def h_source_doc_inspection(ctx: StepContext, step) -> StepResult:
    docs = gitsafety.inspect_source_docs(ctx.repo)
    lines = ["# Source-doc inspection", ""]
    for doc, present in docs.items():
        lines.append(f"- {doc}: {'present' if present else 'absent'}")
    name = write_artifact(ctx, "source-doc-inspection.md", "\n".join(lines) + "\n")
    return StepResult(ok=True, evidence=[name], outputs={"docs": docs})


def h_command_discovery(ctx: StepContext, step) -> StepResult:
    cmds = gitsafety.discover_commands(ctx.repo)
    lines = ["# Command discovery", "",
             "Commands are discovered from source docs only; none invented.", ""]
    for kind, items in cmds.items():
        lines.append(f"## {kind}")
        lines += [f"- {c}" for c in items] or ["- (none discovered)"]
        lines.append("")
    name = write_artifact(ctx, "command-discovery.md", "\n".join(lines))
    ok = bool(cmds.get("test") and cmds.get("validate"))
    return StepResult(ok=ok, evidence=[name], outputs={"commands": cmds},
                      message="commands discovered" if ok else "missing commands")


def h_git_status(ctx: StepContext, step) -> StepResult:
    status = gitsafety.git_status_short(ctx.repo)
    name = write_artifact(ctx, "git-status-before.txt", status or "(clean)\n")
    return StepResult(ok=True, evidence=[name], outputs={"dirty": bool(status.strip())})


def h_branch_safety(ctx: StepContext, step) -> StepResult:
    info = gitsafety.branch_safety(ctx.repo, ctx.loop.default_branch)
    body = json.dumps(info, indent=2) + "\n"
    name = write_artifact(ctx, "branch-safety.txt", body)
    # Direct writes to the default branch are forbidden -> escalate.
    ok = not info["on_default_branch"]
    return StepResult(ok=ok, evidence=[name], outputs=info, escalate=not ok,
                      message="branch safe" if ok else "on default branch (forbidden)")


def h_pull_sync(ctx: StepContext, step) -> StepResult:
    info = gitsafety.fetch_sync(ctx.repo, gitsafety.current_branch(ctx.repo))
    body = json.dumps(info, indent=2) + "\n"
    name = write_artifact(ctx, "pull-sync.txt", body)
    # Either we synced, or we recorded proof of local/remote state.
    return StepResult(ok=True, evidence=[name], outputs=info)


def h_validate_manifest(ctx: StepContext, step) -> StepResult:
    try:
        spec = validate_loop_file(ctx.loop_path)
        log = (f"LoopSpec VALID: loop_id={spec.loop_id} steps={len(spec.steps)} "
               f"terminal_states={spec.terminal_states}\n")
        ok = True
    except Exception as exc:  # noqa: BLE001 - record any schema failure
        log = f"LoopSpec INVALID: {exc}\n"
        ok = False
    name = write_artifact(ctx, "loop-validate.log", log)
    return StepResult(ok=ok, evidence=[name], message=log.strip())


def h_run_tests(ctx: StepContext, step) -> StepResult:
    cmd = list(step.params.get("command", ["python", "-m", "pytest", "-q"]))
    rc, out = gitsafety.run(cmd, ctx.repo)
    log = f"$ {' '.join(cmd)}\nexit={rc}\n\n{out}\n"
    name = write_artifact(ctx, "test-results.log", log)
    return StepResult(ok=rc == 0, evidence=[name], outputs={"exit": rc},
                      message="tests passed" if rc == 0 else "tests FAILED")


def h_conflict_scan(ctx: StepContext, step) -> StepResult:
    offenders = gitsafety.scan_conflict_markers(ctx.repo)
    body = ("no conflict markers in tracked files\n" if not offenders
            else "CONFLICT MARKERS FOUND:\n" + "\n".join(offenders) + "\n")
    name = write_artifact(ctx, "conflict-marker-scan.txt", body)
    return StepResult(ok=not offenders, evidence=[name],
                      outputs={"offenders": offenders})


def h_capture_diff(ctx: StepContext, step) -> StepResult:
    rc, out = gitsafety.run(["git", "diff", "HEAD"], ctx.repo)
    header = f"# git diff HEAD (exit={rc}, {len(out)} bytes)\n"
    name = write_artifact(ctx, "final-diff.patch", header + out)
    return StepResult(ok=True, evidence=[name], outputs={"bytes": len(out)})


def h_verifier_report(ctx: StepContext, step) -> StepResult:
    required = _verifiable_artifact_names(ctx.loop)
    report = verify_run(ctx.store, ctx.run_id, ctx.repo, ctx.artifacts_dir, required)
    ctx.state["verifier"] = report
    name = write_artifact(ctx, "verification-report.md", report.to_markdown())
    return StepResult(ok=report.approved, evidence=[name],
                      outputs={"approved": report.approved},
                      message="verifier approved" if report.approved
                      else "verifier rejected")


def h_evaluator_report(ctx: StepContext, step) -> StepResult:
    verifier = ctx.state.get("verifier")
    if verifier is None:
        return StepResult(ok=False, message="no verifier report to evaluate")
    required = _verifiable_artifact_names(ctx.loop)
    report = evaluate_run(ctx.store, ctx.run_id, verifier, required)
    ctx.state["evaluator"] = report
    name = write_artifact(ctx, "evaluation-report.md", report.to_markdown())
    return StepResult(ok=report.passed, evidence=[name],
                      outputs={"passed": report.passed})


# --------------------------------------------------------------------------
# release-gated handlers (only run when options['release'] is true)
# --------------------------------------------------------------------------
def h_release_commit(ctx: StepContext, step) -> StepResult:
    rc, out = gitsafety.run(["git", "status", "--short"], ctx.repo)
    name = write_artifact(ctx, "post-commit-status.txt", out or "(clean)\n")
    return StepResult(ok=True, evidence=[name], message="commit gate recorded")


def h_release_push(ctx: StepContext, step) -> StepResult:
    out = str(ctx.options.get("push_output", "push deferred in v0 (no auto-push)\n"))
    name = write_artifact(ctx, "push-output.txt", out)
    ok = "error" not in out.lower() and "fail" not in out.lower()
    return StepResult(ok=ok, evidence=[name], escalate=not ok)


def h_release_pr(ctx: StepContext, step) -> StepResult:
    pr_url = str(ctx.options.get("pr_url", "")).strip()
    if not pr_url:
        return StepResult(ok=False, escalate=True,
                          message="no PR URL provided; cannot pass PR-gated state")
    n1 = write_artifact(ctx, "pr-url.txt", pr_url + "\n")
    n2 = write_artifact(ctx, "pr-body.md", str(ctx.options.get(
        "pr_body", "# PR\n\nSummary, architecture, files, tests, risks, rollback.\n")))
    return StepResult(ok=True, evidence=[n1, n2], outputs={"pr_url": pr_url})


HANDLERS: Dict[str, Callable[[StepContext, object], StepResult]] = {
    "noop": h_noop,
    "always_fail": h_always_fail,
    "flaky": h_flaky,
    "record_repo_path": h_record_repo_path,
    "verify_remote": h_verify_remote,
    "source_doc_inspection": h_source_doc_inspection,
    "command_discovery": h_command_discovery,
    "git_status": h_git_status,
    "branch_safety": h_branch_safety,
    "pull_sync": h_pull_sync,
    "validate_manifest": h_validate_manifest,
    "run_tests": h_run_tests,
    "conflict_scan": h_conflict_scan,
    "capture_diff": h_capture_diff,
    "verifier_report": h_verifier_report,
    "evaluator_report": h_evaluator_report,
    "release_commit": h_release_commit,
    "release_push": h_release_push,
    "release_pr": h_release_pr,
}
