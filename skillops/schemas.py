"""LoopSpec and SkillSpec schemas with mechanical validation.

The manifest is the single source of truth. The runtime refuses to execute
unregistered loops, undefined steps, undefined terminal states, undefined
evidence requirements, or unregistered skills.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from skillops.terminal import TERMINAL_STATES

VALID_OWNER_ROLES = {
    "planner", "executor", "verifier", "evaluator",
    "governor", "memory_manager", "escalation_manager", "runtime",
}


class SchemaError(ValueError):
    """Raised when a manifest fails validation."""


@dataclass
class StepSpec:
    id: str
    owner: str
    description: str
    handler: str
    produces: List[str] = field(default_factory=list)
    required: bool = True
    release: bool = False  # release-gated step (commit/push/pr)
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LoopSpec:
    loop_id: str
    name: str
    version: str
    max_iterations: int
    steps: List[StepSpec]
    terminal_states: List[str]
    evidence_requirements: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    forbidden_paths: List[str] = field(default_factory=list)
    default_branch: str = "main"

    def step_ids(self) -> List[str]:
        return [s.id for s in self.steps]

    def get_step(self, step_id: str) -> Optional[StepSpec]:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None


@dataclass
class SkillSpec:
    skill_id: str
    name: str
    version: str
    owner: str
    status: str  # candidate | promoted
    commands: List[str] = field(default_factory=list)
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)


VALID_SKILL_STATUS = {"candidate", "promoted"}


def _require(data: Dict[str, Any], key: str, kind: type, where: str) -> Any:
    if key not in data:
        raise SchemaError(f"{where}: missing required field '{key}'")
    val = data[key]
    if not isinstance(val, kind):
        raise SchemaError(
            f"{where}: field '{key}' must be {kind.__name__}, got {type(val).__name__}"
        )
    return val


def load_yaml(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise SchemaError(f"manifest file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise SchemaError(f"manifest {path} did not parse to a mapping")
    return data


def parse_loopspec(data: Dict[str, Any]) -> LoopSpec:
    """Validate and build a LoopSpec or raise SchemaError."""
    loop_id = _require(data, "loop_id", str, "LoopSpec")
    name = _require(data, "name", str, "LoopSpec")
    version = str(_require(data, "version", (str, int, float), "LoopSpec"))
    max_iterations = _require(data, "max_iterations", int, "LoopSpec")
    if max_iterations <= 0:
        raise SchemaError("LoopSpec: max_iterations must be > 0")

    raw_steps = _require(data, "steps", list, "LoopSpec")
    if not raw_steps:
        raise SchemaError("LoopSpec: at least one step is required")

    steps: List[StepSpec] = []
    seen_ids = set()
    for i, raw in enumerate(raw_steps):
        where = f"LoopSpec.steps[{i}]"
        if not isinstance(raw, dict):
            raise SchemaError(f"{where}: must be a mapping")
        sid = _require(raw, "id", str, where)
        if sid in seen_ids:
            raise SchemaError(f"{where}: duplicate step id '{sid}'")
        seen_ids.add(sid)
        owner = _require(raw, "owner", str, where)
        if owner not in VALID_OWNER_ROLES:
            raise SchemaError(
                f"{where}: owner '{owner}' not in {sorted(VALID_OWNER_ROLES)}"
            )
        handler = _require(raw, "handler", str, where)
        steps.append(
            StepSpec(
                id=sid,
                owner=owner,
                description=str(raw.get("description", "")),
                handler=handler,
                produces=list(raw.get("produces", []) or []),
                required=bool(raw.get("required", True)),
                release=bool(raw.get("release", False)),
                params=dict(raw.get("params", {}) or {}),
            )
        )

    terminal_states = _require(data, "terminal_states", list, "LoopSpec")
    if not terminal_states:
        raise SchemaError("LoopSpec: terminal_states must be non-empty")
    for ts in terminal_states:
        if ts not in TERMINAL_STATES:
            raise SchemaError(
                f"LoopSpec: terminal state '{ts}' is not an allowed terminal state"
            )

    evidence_requirements = list(data.get("evidence_requirements", []) or [])
    skills = list(data.get("skills", []) or [])
    forbidden_paths = list(data.get("forbidden_paths", []) or [])
    default_branch = str(data.get("default_branch", "main"))

    return LoopSpec(
        loop_id=loop_id,
        name=name,
        version=version,
        max_iterations=max_iterations,
        steps=steps,
        terminal_states=[str(t) for t in terminal_states],
        evidence_requirements=evidence_requirements,
        skills=skills,
        forbidden_paths=forbidden_paths,
        default_branch=default_branch,
    )


def parse_skillspec(data: Dict[str, Any]) -> SkillSpec:
    skill_id = _require(data, "skill_id", str, "SkillSpec")
    name = _require(data, "name", str, "SkillSpec")
    version = str(_require(data, "version", (str, int, float), "SkillSpec"))
    owner = _require(data, "owner", str, "SkillSpec")
    status = _require(data, "status", str, "SkillSpec")
    if status not in VALID_SKILL_STATUS:
        raise SchemaError(
            f"SkillSpec: status '{status}' not in {sorted(VALID_SKILL_STATUS)}"
        )
    return SkillSpec(
        skill_id=skill_id,
        name=name,
        version=version,
        owner=owner,
        status=status,
        commands=list(data.get("commands", []) or []),
        inputs=list(data.get("inputs", []) or []),
        outputs=list(data.get("outputs", []) or []),
        evidence=list(data.get("evidence", []) or []),
    )


def validate_loop_file(path: str) -> LoopSpec:
    return parse_loopspec(load_yaml(path))


def validate_skill_file(path: str) -> SkillSpec:
    return parse_skillspec(load_yaml(path))
