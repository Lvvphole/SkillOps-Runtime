"""Preflight: source-doc inspection, command discovery, branch safety, conflict
scan. These functions only observe the repository; they never invent commands.
"""
from __future__ import annotations

import os
import subprocess
from typing import Dict, List, Tuple

CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")

# Files that may legitimately declare project commands.
SOURCE_DOCS = [
    "README.md", "AGENTS.md", "pyproject.toml", "package.json",
    "Makefile", ".github/workflows", "tox.ini", "setup.cfg",
]


def run(cmd: List[str], cwd: str) -> Tuple[int, str]:
    """Run a command, returning (returncode, combined_output)."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=300
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return 127, f"command failed: {' '.join(cmd)}: {exc}"


def inspect_source_docs(repo: str) -> Dict[str, bool]:
    """Return which known source docs are present."""
    found: Dict[str, bool] = {}
    for doc in SOURCE_DOCS:
        found[doc] = os.path.exists(os.path.join(repo, doc))
    return found


def discover_commands(repo: str) -> Dict[str, List[str]]:
    """Discover validation/test/run commands strictly from source docs.

    No command is fabricated. A command is only reported if its declaration is
    discoverable in a real source document.
    """
    commands: Dict[str, List[str]] = {"test": [], "validate": [], "run": []}
    docs = inspect_source_docs(repo)

    pyproject = os.path.join(repo, "pyproject.toml")
    if docs.get("pyproject.toml") and os.path.exists(pyproject):
        with open(pyproject, "r", encoding="utf-8") as fh:
            text = fh.read()
        if "pytest" in text:
            commands["test"].append("python -m pytest -q")
        if "[project.scripts]" in text and "skillops" in text:
            commands["validate"].append(
                "python -m skillops loop validate --loop loops/coding-pr-gate.yaml"
            )
            commands["run"].append(
                "python -m skillops loop run --loop loops/coding-pr-gate.yaml"
            )

    makefile = os.path.join(repo, "Makefile")
    if docs.get("Makefile") and os.path.exists(makefile):
        with open(makefile, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("test:"):
                    commands["test"].append("make test")
    return commands


def git_status_short(repo: str) -> str:
    _, out = run(["git", "status", "--short"], repo)
    return out


def current_branch(repo: str) -> str:
    _, out = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo)
    return out.strip()


def remote_url(repo: str) -> str:
    _, out = run(["git", "remote", "get-url", "origin"], repo)
    return out.strip()


def branch_safety(repo: str, default_branch: str = "main") -> Dict[str, object]:
    """Record branch identity and whether direct-main writes would occur."""
    cur = current_branch(repo)
    status = git_status_short(repo)
    dirty = bool(status.strip())
    on_default = cur == default_branch
    return {
        "current_branch": cur,
        "default_branch": default_branch,
        "remote_url": remote_url(repo),
        "dirty": dirty,
        "on_default_branch": on_default,
        "direct_main_write_risk": on_default,
        "status_short": status,
    }


def fetch_sync(repo: str, branch: str) -> Dict[str, object]:
    """Fetch origin and report whether local branch is current vs remote."""
    rc, fetch_out = run(["git", "fetch", "origin", branch], repo)
    rc2, local = run(["git", "rev-parse", "HEAD"], repo)
    rc3, remote = run(["git", "rev-parse", f"origin/{branch}"], repo)
    local_sha = local.strip()
    remote_sha = remote.strip() if rc3 == 0 else ""
    return {
        "fetch_rc": rc,
        "fetch_output": fetch_out.strip(),
        "local_sha": local_sha,
        "remote_sha": remote_sha,
        "current": (remote_sha == "" or local_sha == remote_sha),
    }


def tracked_files(repo: str) -> List[str]:
    rc, out = run(["git", "ls-files"], repo)
    if rc != 0:
        return []
    return [f for f in out.splitlines() if f.strip()]


def scan_conflict_markers(repo: str) -> List[str]:
    """Return tracked files that contain conflict markers. Empty == clean."""
    offenders: List[str] = []
    for rel in tracked_files(repo):
        path = os.path.join(repo, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except OSError:
            continue
        # A line that *starts* with a marker token (avoid false positives such
        # as the marker tuple in this very module).
        for line in content.splitlines():
            stripped = line.rstrip()
            if any(stripped.startswith(m) and stripped == m or
                   stripped.startswith(m + " ") for m in CONFLICT_MARKERS):
                offenders.append(rel)
                break
    return offenders
