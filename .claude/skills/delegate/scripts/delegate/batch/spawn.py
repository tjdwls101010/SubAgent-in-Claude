"""Planning and starting a group's members, of any engine: each member's settings checked before anything starts, which members get a checkout of their own, what each is told, and starting them side by side."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from delegate.engine import adapter
from delegate.errors import NotFound, Refusal
from delegate.git import head_commit, local_branches, main_checkout, status_entries, toplevel, uncommitted_count
from delegate.registry import find_run, newest_on, read_meta, update_slot
from delegate.runs import WRITERS, create_run, resume, start_reply

ROW_DROP = ("next", "events", "note")


def member_flags(task, defaults, engine):
    """A member's settings as its line gives them over the batch's flags. The batch's flags are a new member's defaults, and its model, effort and priority belong to its --engine: they reach a member of that engine (any engine, when the batch names none), never one of another engine, which names its own. A resumed member keeps what its run recorded unless its own line changes it."""
    own = {k: task.get(k) for k in ("profile", "model", "effort", "schema")} | {"priority": task.get("priority", False)}
    if task["kind"] != "start":
        return own
    out = {k: own[k] or defaults.get(k) for k in ("profile", "schema")}
    bound = not defaults.get("engine") or engine == defaults.get("engine")
    out |= {k: own[k] or (defaults.get(k) if bound else None) for k in ("model", "effort")}
    out["priority"] = task.get("priority", bool(defaults.get("priority")) and bound and adapter(engine).TAKES_PRIORITY)
    return out


def plan_members(group, tasks, defaults, caller_cwd, use_worktrees, runs_dir=None):
    """Each member's engine, settings, directory and checkout, decided before anything starts. A fresh member's settings are checked now (a missing engine, model or effort, an effort its engine does not take, a model the engine does not offer), and so are a resume line's own settings against the run it names when the registry has it, so a batch that would fail on one member's settings starts none; a resume ref the registry does not know still fails in its own slot. A fresh member gets a checkout of its own when --worktree is on, its profile can write, it names no cwd, the group gives none, and the repository has a commit to start from."""
    repo = toplevel(caller_cwd)
    head = head_commit(repo) if repo else None
    main = (main_checkout(repo) or repo) if repo else None
    # The branches there before any member works, so `clean` can tell a branch a member made from a person's.
    before = local_branches(repo) if repo and use_worktrees else []
    plans = []
    for i, task in enumerate(tasks, 1):
        plan = {"index": i, "task": task, "label": task.get("label"), "kind": task["kind"], "worktree": None}
        if task["kind"] == "start":
            engine = task.get("engine") or defaults.get("engine")
            if not engine:
                raise Refusal(f"{task['where']}: no engine — give the task an engine or the batch --engine; there is no default", arguments=True, help="batch --help")
            flags = member_flags(task, defaults, engine)
            a = adapter(engine)
            plan["engine"] = engine
            plan["cwd"] = str(Path(task["cwd"]).expanduser().resolve()) if task.get("cwd") else (defaults.get("cwd") or repo or str(Path(caller_cwd).resolve()))
            try:
                plan["settings"] = a.settings(flags, None, command="batch", cwd=plan["cwd"])
            except Refusal as e:
                raise Refusal(f"{task['where']}: {e.message}", arguments=e.arguments, **{**e.fields, "help": "batch --help"}) from None
            if use_worktrees and plan["settings"]["profile"] in WRITERS and not task.get("cwd") and not defaults.get("cwd") and repo and head:
                plan["worktree"] = {**a.worktree_plan(repo, f"{group}-{i}", head, main), "branches_before": before}
        else:
            plan["engine"] = task.get("engine")
            plan["flags"] = flags = member_flags(task, defaults, None)
            check_resume_line(task, flags, runs_dir)
        plans.append(plan)
    for p in plans:
        if p["worktree"]:
            why = adapter(p["engine"]).worktree_taken(p["worktree"])
            if why:
                raise Refusal(why, path=p["worktree"]["path"], branch=p["worktree"]["branch"], instead="choose another group name; or `clean` the group that left it, if it is still listed — a branch with commits that `clean` kept stays until you delete it")
    return plans, {"repo": repo, "head": head, "uncommitted": uncommitted_count(repo) if repo else None}


def check_resume_line(task, flags, runs_dir):
    """A resume line's own settings, checked against the run it names as `resume` would check them; nothing when the registry does not know the ref, which then fails in its slot as `resume` would."""
    if not runs_dir or not any(flags.get(k) for k in ("profile", "model", "effort", "priority")):
        return
    try:
        newest = read_meta(find_run(runs_dir, task["resume"], engine=task.get("engine")))
        if newest.get("native_id"):
            newest = read_meta(newest_on(runs_dir, newest["engine"], newest["native_id"]))
    except (NotFound, Refusal):
        return
    try:
        adapter(newest["engine"]).settings({**flags, "resume": True}, dict(newest.get("settings") or {}), command="resume", cwd=newest.get("cwd"))
    except Refusal as e:
        raise Refusal(f"{task['where']}: {e.message}", arguments=e.arguments, **{**e.fields, "help": "batch --help"}) from None


def missing_ignored(repo, limit=20):
    """What git ignores in the caller's tree (top-level entries), which no checkout starts with — except what .worktreeinclude has Claude Code copy into a claude member's."""
    entries = status_entries(repo) or []
    tops = sorted({rel.rstrip("/").split("/", 1)[0] for xy, rel in entries if xy == "!!"} - {".delegate-runs", ".claude-runs", ".codex-runs"})
    return tops[:limit]


def start_member(runs_dir, group, generation, size, plan, defaults, caller_cwd, facts, origin=None, preflight=True):
    """Start one member; returns its slot and its reply row. A member that fails to start keeps its slot with an `error`."""
    slot = {"index": plan["index"], "label": plan["label"], "kind": plan["kind"], "engine": plan.get("engine"), "run_id": None, "worktree": plan["worktree"], "error": None}
    group_info = {"name": group, "index": plan["index"], "size": size}
    task = plan["task"]

    def published(meta):
        # A checkout belongs to the group whose fresh member made it: a member resumed here keeps working in its old checkout, which this group neither owns nor cleans.
        update_slot(runs_dir, group, generation, plan["index"], {"run_id": meta["run_id"], "engine": meta["engine"], **({"worktree": meta.get("worktree")} if plan["kind"] == "start" else {})})

    try:
        if plan["kind"] == "start":
            wt = plan["worktree"]
            wt_info = {"path": wt["path"], "base": wt["base"], "uncommitted": facts["uncommitted"]} if wt else None
            # A claude member is launched from the repository and Claude Code makes its checkout; a codex member's checkout is cut when it is published, and it runs there.
            meta = create_run(runs_dir=runs_dir, engine_name=plan["engine"], kind="start", prompt=task["prompt"], settings=plan["settings"], cwd=wt["repo"] if wt else plan["cwd"], launch_cwd=wt["repo"] if wt else None, label=plan["label"] or group, timeout=defaults.get("timeout"), group=group, worktree=wt, group_info=group_info, worktree_info=wt_info, extra_meta={"group_index": plan["index"]}, on_published=published, origin=origin, preflight=preflight)
            reply = start_reply(meta, runs_dir)
        else:
            reply = resume(ref=task["resume"], engine=plan.get("engine"), prompt=task["prompt"], cwd=caller_cwd, flags={**plan["flags"], "cwd": str(Path(task["cwd"]).expanduser().resolve()) if task.get("cwd") else None}, label=plan["label"], timeout=defaults.get("timeout"), runs_dir=runs_dir, group=group, group_info=group_info, group_index=plan["index"], on_published=published)
    except Refusal as e:
        slot["error"] = e.message
        # A refusal's `run_id` is an existing run it names (a live one on the same session), never one this member started.
        return slot, {"index": plan["index"], "label": plan["label"], "engine": plan.get("engine"), "run_id": None, "error": e.message, **({"instead": e.fields["instead"]} if "instead" in e.fields else {}), **({"existing_run_id": e.fields["run_id"]} if e.fields.get("run_id") else {})}
    except Exception as e:  # One member's unexpected failure must not take the slots of the others with it.
        slot["error"] = f"{type(e).__name__}: {e}"
        return slot, {"index": plan["index"], "label": plan["label"], "engine": plan.get("engine"), "run_id": None, "error": slot["error"]}
    slot.update({"run_id": reply["run_id"], "engine": reply["engine"], "error": reply.get("error")})
    wt_path = reply["cwd"] if plan["worktree"] else None
    row = {"index": plan["index"], "label": plan["label"], **{k: v for k, v in reply.items() if k not in ROW_DROP}, "worktree": (plan["worktree"] or {}).get("path") or wt_path}
    return slot, row


def start_members(runs_dir, group, generation, plans, defaults, caller_cwd, facts, origin=None, preflight=True):
    """Start every member at once, so N members cost one start wait, not N."""
    with ThreadPoolExecutor(max_workers=min(8, len(plans))) as pool:
        futures = [pool.submit(start_member, runs_dir, group, generation, len(plans), p, defaults, caller_cwd, facts, origin, preflight) for p in plans]
        return [f.result() for f in futures]
