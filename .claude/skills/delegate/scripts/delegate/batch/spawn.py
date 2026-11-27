"""Planning and starting a group's members, of any engine: each member's settings checked before anything starts, which members get a checkout of their own, what each is told, and starting them side by side."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from delegate.engine import adapter
from delegate.errors import Refusal
from delegate.git import head_commit, local_branches, main_checkout, status_entries, toplevel, uncommitted_count
from delegate.registry import update_slot
from delegate.runs import WRITERS, create_run, resume, start_reply

ROW_DROP = ("next", "events", "note")


def member_flags(task, defaults):
    return {k: task.get(k) or defaults.get(k) for k in ("profile", "model", "effort", "schema")} | {"priority": task.get("priority", defaults.get("priority", False))}


def plan_members(group, tasks, defaults, caller_cwd, use_worktrees):
    """Each member's engine, settings, directory and checkout, decided before anything starts. A fresh member's settings are checked now (a missing engine, model or effort, an effort its engine does not take, a model not in the codex catalog), so a batch that would fail on one member starts none. A fresh member gets a checkout of its own when --worktree is on, its profile can write, it names no cwd, the group gives none, and the repository has a commit to start from."""
    repo = toplevel(caller_cwd)
    head = head_commit(repo) if repo else None
    main = (main_checkout(repo) or repo) if repo else None
    # The branches there before any member works, so `clean` can tell a branch a member made from a person's.
    before = local_branches(repo) if repo and use_worktrees else []
    plans = []
    for i, task in enumerate(tasks, 1):
        flags = member_flags(task, defaults)
        plan = {"index": i, "task": task, "label": task.get("label"), "kind": task["kind"], "worktree": None}
        if task["kind"] == "start":
            engine = task.get("engine") or defaults.get("engine")
            if not engine:
                raise Refusal(f"{task['where']}: no engine — give the task an engine or the batch --engine; there is no default", arguments=True, help="batch --help")
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
            plan["flags"] = flags
        plans.append(plan)
    for p in plans:
        if p["worktree"]:
            why = adapter(p["engine"]).worktree_taken(p["worktree"])
            if why:
                raise Refusal(why, path=p["worktree"]["path"], branch=p["worktree"]["branch"], instead="clean that group, or choose another group name")
    return plans, {"repo": repo, "head": head, "uncommitted": uncommitted_count(repo) if repo else None}


def missing_ignored(repo, limit=20):
    """What git ignores in the caller's tree (top-level entries), which no checkout starts with — except what .worktreeinclude has Claude Code copy into a claude member's."""
    entries = status_entries(repo) or []
    tops = sorted({rel.rstrip("/").split("/", 1)[0] for xy, rel in entries if xy == "!!"} - {".delegate-runs", ".claude-runs", ".codex-runs"})
    return tops[:limit]


def start_member(runs_dir, group, generation, size, plan, defaults, caller_cwd, facts, origin=None):
    """Start one member; returns its slot and its reply row. A member that fails to start keeps its slot with an `error`."""
    slot = {"index": plan["index"], "label": plan["label"], "kind": plan["kind"], "engine": plan.get("engine"), "run_id": None, "worktree": plan["worktree"], "error": None}
    group_info = {"name": group, "index": plan["index"], "size": size}
    task = plan["task"]

    def published(meta):
        update_slot(runs_dir, group, generation, plan["index"], {"run_id": meta["run_id"], "engine": meta["engine"], "worktree": meta.get("worktree")})

    try:
        if plan["kind"] == "start":
            wt = plan["worktree"]
            wt_info = {"path": wt["path"], "base": wt["base"], "uncommitted": facts["uncommitted"]} if wt else None
            # A claude member is launched from the repository and Claude Code makes its checkout; a codex member's checkout is cut when it is published, and it runs there.
            meta = create_run(runs_dir=runs_dir, engine_name=plan["engine"], kind="start", prompt=task["prompt"], settings=plan["settings"], cwd=wt["repo"] if wt else plan["cwd"], launch_cwd=wt["repo"] if wt else None, label=plan["label"] or group, timeout=defaults.get("timeout"), group=group, worktree=wt, group_info=group_info, worktree_info=wt_info, extra_meta={"group_index": plan["index"]}, on_published=published, origin=origin)
            reply = start_reply(meta, runs_dir)
        else:
            reply = resume(ref=task["resume"], engine=plan.get("engine"), prompt=task["prompt"], cwd=caller_cwd, flags={**plan["flags"], "cwd": str(Path(task["cwd"]).expanduser().resolve()) if task.get("cwd") else None}, label=plan["label"], timeout=defaults.get("timeout"), runs_dir=runs_dir, group=group, group_info=group_info, group_index=plan["index"], on_published=published)
    except Refusal as e:
        slot["error"] = e.message
        return slot, {"index": plan["index"], "label": plan["label"], "engine": plan.get("engine"), "run_id": None, "error": e.message, **{k: v for k, v in e.fields.items() if k in ("instead", "run_id")}}
    except Exception as e:  # One member's unexpected failure must not take the slots of the others with it.
        slot["error"] = f"{type(e).__name__}: {e}"
        return slot, {"index": plan["index"], "label": plan["label"], "engine": plan.get("engine"), "run_id": None, "error": slot["error"]}
    slot.update({"run_id": reply["run_id"], "engine": reply["engine"], "error": reply.get("error")})
    wt_path = reply["cwd"] if plan["worktree"] else None
    row = {"index": plan["index"], "label": plan["label"], **{k: v for k, v in reply.items() if k not in ROW_DROP}, "worktree": (plan["worktree"] or {}).get("path") or wt_path}
    return slot, row


def start_members(runs_dir, group, generation, plans, defaults, caller_cwd, facts, origin=None):
    """Start every member at once, so N members cost one start wait, not N."""
    with ThreadPoolExecutor(max_workers=min(8, len(plans))) as pool:
        futures = [pool.submit(start_member, runs_dir, group, generation, len(plans), p, defaults, caller_cwd, facts, origin) for p in plans]
        return [f.result() for f in futures]
