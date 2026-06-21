"""SkillOps LoopStack v0.

A checkpointed loop kernel that converts coding-agent output into replayable,
evidence-verified, resumable, PR-gated software-delivery runs.

Core rule: nothing passes because an agent says it passed. It passes only when
the LoopStack can replay the run, verify the evidence, resume from checkpoints,
and emit a valid terminal state.
"""

__version__ = "0.1.0"
