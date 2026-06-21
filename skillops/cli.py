"""SkillOps command-line interface (argparse).

Commands (discovered, not invented; mirrored in README and pyproject.toml):
  python -m skillops loop validate --loop <path>
  python -m skillops skill validate --skill <path>
  python -m skillops loop run --loop <path> [--release] [--pr-url URL]
  python -m skillops loop resume --run-id <id>
  python -m skillops loop replay --run-id <id>
  python -m skillops run status --run-id <id>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from skillops.promotion import evaluate_upshift, run_promotion_check
from skillops.runtime import Engine, replay_run, status_run
from skillops.schemas import SchemaError, validate_loop_file, validate_skill_file
from skillops.store import Store


def _repo_root() -> str:
    return os.environ.get("SKILLOPS_REPO", os.getcwd())


def _db_path(repo: str) -> str:
    return os.path.join(repo, "artifacts", "skillops.db")


def cmd_loop_validate(args) -> int:
    try:
        spec = validate_loop_file(args.loop)
    except SchemaError as exc:
        print(f"LOOP VALIDATION: FAIL\n{exc}")
        return 1
    print("LOOP VALIDATION: PASS")
    print(f"  loop_id={spec.loop_id} version={spec.version} steps={len(spec.steps)}")
    print(f"  terminal_states={spec.terminal_states}")
    print(f"  skills={spec.skills}")
    return 0


def cmd_skill_validate(args) -> int:
    try:
        spec = validate_skill_file(args.skill)
    except SchemaError as exc:
        print(f"SKILL VALIDATION: FAIL\n{exc}")
        return 1
    print("SKILL VALIDATION: PASS")
    print(f"  skill_id={spec.skill_id} status={spec.status} version={spec.version}")
    return 0


def cmd_skill_promote_check(args) -> int:
    repo = _repo_root()
    store = Store(_db_path(repo))
    loop_id = args.loop or args.skill
    if args.dry_run:
        a = evaluate_upshift(store, args.skill, loop_id,
                             os.path.join(repo, "skills", args.skill, "skill.yaml"))
        print(a.to_log())
        store.close()
        return 0 if a.eligible else 2
    result = run_promotion_check(repo, store, args.skill, loop_id)
    if result.eligible:
        print(f"PROMOTION: {result.terminal_state}")
        print(f"  promo_run_id={result.promo_run_id}")
        print(f"  artifacts_dir={result.artifacts_dir}")
        print(f"  candidate_record={result.record_path}")
    else:
        a = evaluate_upshift(store, args.skill, loop_id,
                             os.path.join(repo, "skills", args.skill, "skill.yaml"))
        print("PROMOTION: BELOW_THRESHOLD (fail closed, no candidate created)")
        print(a.to_log())
    store.close()
    return 0 if result.eligible else 2


def cmd_loop_run(args) -> int:
    repo = _repo_root()
    options = {"release": bool(args.release)}
    if args.pr_url:
        options["pr_url"] = args.pr_url
    engine = Engine(repo, db_path=_db_path(repo), options=options)
    result = engine.run(args.loop)
    print(f"RUN: run_id={result.run_id}")
    print(f"  artifacts_dir={result.artifacts_dir}")
    print(f"  terminal_state={result.terminal_state}")
    engine.store.close()
    return 0 if result.terminal_state.startswith("PASS") else 2


def cmd_loop_resume(args) -> int:
    repo = _repo_root()
    engine = Engine(repo, db_path=_db_path(repo))
    result = engine.resume(args.run_id)
    print(f"RESUME: run_id={result.run_id}")
    print(f"  terminal_state={result.terminal_state}")
    engine.store.close()
    return 0 if result.terminal_state.startswith("PASS") else 2


def cmd_loop_replay(args) -> int:
    repo = _repo_root()
    store = Store(_db_path(repo))
    report = replay_run(store, args.run_id)
    print(json.dumps(report, indent=2))
    store.close()
    return 0 if report["reconstructable"] else 2


def cmd_run_status(args) -> int:
    repo = _repo_root()
    store = Store(_db_path(repo))
    report = status_run(store, args.run_id)
    print(json.dumps(report, indent=2))
    store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="skillops", description="SkillOps LoopStack v0")
    sub = p.add_subparsers(dest="group", required=True)

    loop = sub.add_parser("loop", help="loop operations")
    loop_sub = loop.add_subparsers(dest="action", required=True)

    lv = loop_sub.add_parser("validate", help="validate a LoopSpec manifest")
    lv.add_argument("--loop", required=True)
    lv.set_defaults(func=cmd_loop_validate)

    lr = loop_sub.add_parser("run", help="start a run from a loop manifest")
    lr.add_argument("--loop", required=True)
    lr.add_argument("--release", action="store_true",
                    help="enable release-gated steps (commit/push/PR)")
    lr.add_argument("--pr-url", dest="pr_url", default=None)
    lr.set_defaults(func=cmd_loop_run)

    lrs = loop_sub.add_parser("resume", help="resume from last successful checkpoint")
    lrs.add_argument("--run-id", dest="run_id", required=True)
    lrs.set_defaults(func=cmd_loop_resume)

    lrp = loop_sub.add_parser("replay", help="reconstruct run history from records")
    lrp.add_argument("--run-id", dest="run_id", required=True)
    lrp.set_defaults(func=cmd_loop_replay)

    skill = sub.add_parser("skill", help="skill operations")
    skill_sub = skill.add_subparsers(dest="action", required=True)
    sv = skill_sub.add_parser("validate", help="validate a SkillSpec manifest")
    sv.add_argument("--skill", required=True)
    sv.set_defaults(func=cmd_skill_validate)

    spc = skill_sub.add_parser(
        "promote-check",
        help="evaluate UPSHIFT thresholds; emit PROMOTION_CANDIDATE_CREATED on pass")
    spc.add_argument("--skill", required=True, help="skill id (skills/<id>/skill.yaml)")
    spc.add_argument("--loop", default=None,
                     help="comparable loop id (defaults to skill id)")
    spc.add_argument("--dry-run", action="store_true",
                     help="assess only; create no candidate")
    spc.set_defaults(func=cmd_skill_promote_check)

    run = sub.add_parser("run", help="run inspection")
    run_sub = run.add_subparsers(dest="action", required=True)
    rs = run_sub.add_parser("status", help="report run status by id")
    rs.add_argument("--run-id", dest="run_id", required=True)
    rs.set_defaults(func=cmd_run_status)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
