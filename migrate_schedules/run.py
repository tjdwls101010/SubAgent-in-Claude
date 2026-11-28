"""Move the schedules the `claude` skill wrote in one repository over to the `delegate` skill (the plan's transition step 3). A maintenance job: the skill never imports it, and it reaches the skill only through its units' interfaces.

Steps, each run with the same --project, each repeatable (what is done is skipped):
    inventory   read only: every old schedule, its state on GitHub and this Mac, and its class (recurring, upcoming, finished, held); saved as the journal's inventory and printed. Run it again before each batch: a schedule a step has touched keeps the row it is moved by (listed under `kept`)
    freeze      the old dispatchers moved out of launchd, the workflows disabled, then wait for their queues to drain (exit 3 while one is not)
    state       the old state.json of each schedule written as this skill's, definition kept
    publish     each moved workflow rewritten by this skill's renderer in one commit; each finished one-off's workflow deleted
    resume      each moved workflow put in its expected state, then this skill's dispatcher installed; slots missed while frozen dispatched with their own slot; run it again until each dispatched slot is claimed (exit 3 until then: after a dispatch, while a dispatched run is still on its way, and after an ended run that claimed nothing is dispatched again)
    rollback    back to the claude skill, schedule by schedule
    verify      the transition's checks: every inventoried schedule accounted for, no workflow or dispatcher left that runs the old skill

--only NAME limits a step to those schedules (repeatable). A step started while another runs waits for it. Exit codes: 0 done; 1 a step failed (the journal keeps what was done); 3 a step must wait (a queue still draining) — run it again.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "delegate" / "scripts"
# A job outside the skill: it reaches the skill's package, and itself as a package, from the repository root.
sys.path[:0] = [str(SKILL_SCRIPTS), str(ROOT)]

from delegate.git import main_checkout, remote_url  # noqa: E402
from delegate.github import repo_facts, repo_from_remote  # noqa: E402
from delegate.registry import resolve_runs_dir  # noqa: E402

from migrate_schedules.inventory import inventory  # noqa: E402
from migrate_schedules.journal import Journal, held  # noqa: E402
from migrate_schedules.stages import NotYet, freeze, move_state, publish, resume, rollback  # noqa: E402
from migrate_schedules.verify import verify  # noqa: E402

DEFAULT_CLI = str(Path.home() / ".claude" / "skills" / "delegate" / "scripts" / "cli.py")
STEPS = {"freeze": freeze, "state": move_state, "publish": publish, "resume": resume, "rollback": rollback}


def context(args):
    project = main_checkout(args.project) or str(Path(args.project).resolve())
    repo = repo_from_remote(remote_url(project))
    if not repo:
        raise SystemExit(f"{project} has no GitHub remote named origin")
    facts = repo_facts(repo)
    # The job's clock: `--now` sets where it starts, and it moves on as the command runs, as time does.
    start, base = time.monotonic(), float(args.now) if args.now else None
    clock = (lambda: base + time.monotonic() - start) if base is not None else time.time
    return {"project": project, "repo": facts["full_name"] or repo, "branch": facts["default_branch"], "registry": resolve_runs_dir(project), "cli": args.cli, "now": clock(), "clock": clock}


def main(argv=None):
    p = argparse.ArgumentParser(prog="migrate_schedules/run.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("step", choices=("inventory", *STEPS, "verify"))
    p.add_argument("--project", required=True)
    p.add_argument("--only", action="append", metavar="NAME")
    p.add_argument("--cli", default=DEFAULT_CLI, help=f"the delegate cli.py the moved workflows call (default {DEFAULT_CLI})")
    p.add_argument("--now", help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    ctx = context(args)
    with held(ctx["registry"]):
        return _step(args, ctx, Journal(ctx["registry"]))


def _step(args, ctx, j):
    if args.step == "inventory":
        inv = j.set_inventory(inventory(ctx["project"], ctx["repo"], ctx["now"]))
        print(json.dumps(inv, ensure_ascii=False, indent=1))
        return 0
    if args.step == "verify":
        out = verify(j, ctx)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if out["ok"] else 1
    if not j.inventory:
        print(json.dumps({"error": "no inventory in the journal; run the inventory step first"}))
        return 1
    rows = [r for r in j.inventory["schedules"] if r.get("name") and r["class"] != "held" and (not args.only or r["name"] in args.only)]
    if args.step == "rollback":
        rows = [r for r in rows if j.of(r["name"])]
    results, code = [], 0
    for row in rows:
        try:
            note = STEPS[args.step](j, row, ctx)
            results.append({"name": row["name"], "status": "done", **({"note": note} if note else {})})
        except NotYet as e:
            results.append({"name": row["name"], "status": "waiting", "why": str(e)})
            code = code or 3
        except Exception as e:  # one schedule's failure is reported and the others go on; the journal keeps what was done
            results.append({"name": row["name"], "status": "failed", "error": f"{type(e).__name__}: {getattr(e, 'message', None) or e}"})
            code = 1
    print(json.dumps({"step": args.step, "results": results}, ensure_ascii=False, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
