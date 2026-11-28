"""Steps 3-3 to 3-6 and the rollback, one schedule at a time, each recorded in the journal as it is done so a run stopped anywhere goes on where it stopped and a step already done is skipped.

The expected end state comes from the latest slot results, never from the inventory alone: a one-off a worker completed — on the old side while its queue drained, or on the new side — ends disabled with no dispatcher, whatever its state was when the inventory was taken.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from delegate.errors import Refusal
from delegate.github import delete_workflow, dispatch_workflow, gh_path, parse_spec, read_workflow, render_workflow, set_workflow_enabled, unfinished_runs, workflow_state, write_workflow
from delegate.launchd import agent_state, agents_dir, calendar_entries, install_agent, load_agent, remove_agent
from delegate.registry import read_meta, schedule_lock, schedule_state, take_over_legacy_state, write_definition
from delegate.schedule import dispatcher_argv, dispatcher_label, dispatcher_log, fields_of, fire_times, legacy_dispatcher_label, merge

from .inventory import OLD_REGISTRY, at_instant, completed_slot, old_state
from .journal import Journal

# Old run states that are over: a run in any other state may still be working.
OVER = ("completed", "failed", "timed_out", "interrupted", "orphaned", "quota_exhausted")


class NotYet(Exception):
    """A step that cannot be done now and is worth trying again later (a queue still draining); the journal keeps what is done."""


def old_live_runs(project, name):
    """Run ids of the `claude` skill's scheduled runs of `name` that are not recorded over."""
    out = []
    for meta in sorted((Path(project) / OLD_REGISTRY).glob("*/meta.json")):
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        o = m.get("origin") or {}
        if o.get("kind") == "schedule" and o.get("name") == name and m.get("state") not in OVER:
            out.append(m.get("run_id"))
    return out


def _move_plist_out(label, backup):
    """Keep the plist at `backup` (moved, not copied), then unload it and delete it from LaunchAgents; a plist already kept is not taken twice."""
    src = agents_dir() / f"{label}.plist"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if src.exists() and not backup.exists():
        shutil.copy2(src, backup)
    gone = remove_agent(label)
    if gone["status"] == "failed":
        raise NotYet(f"the dispatcher {label} could not be unloaded: {gone.get('error')}")


def freeze(j: Journal, row, ctx):
    """3-3: the old dispatcher moved out of launchd, the workflow disabled, then wait until GitHub has no run of it queued or running and the old registry no live worker of it."""
    name, s = row["name"], j.of(row["name"])
    if s.get("rolled_back"):
        # Moved back earlier: the move starts over.
        j.data["schedules"][name] = {}
        j.save()
        s = {}
    if not s.get("plist_moved"):
        label = legacy_dispatcher_label(ctx["repo"], name)
        _move_plist_out(label, j.backup / f"{label}.plist")
        j.update(name, plist_moved=True, plist_backup=str(j.backup / f"{label}.plist"), frozen_at=ctx["now"])
    if not s.get("disabled"):
        set_workflow_enabled(ctx["repo"], row["file"], False)
        j.update(name, disabled=True)
    pending = unfinished_runs(ctx["repo"], row["file"])
    live = old_live_runs(ctx["project"], name)
    if pending or live:
        raise NotYet(f"{name}: {len(pending)} run(s) of its workflow are not finished and {len(live)} old worker(s) are live; run this step again once they end")
    j.update(name, drained=True)


def move_state(j: Journal, row, ctx):
    """3-4: the old state.json, once the queue has drained, taken over as this skill's record — slots and jobs, each old job marked legacy with how its run ended, so a retry decision about an old slot still has an answer — with the workflow's definition, and the old record left as it was for a rollback. Before the queue drains, an old run could still fill a slot the copy would not hold."""
    name = row["name"]
    if j.of(name).get("state_moved"):
        return
    if not j.of(name).get("drained"):
        raise NotYet(f"{name}: its queue has not drained (freeze); its old record could still change")
    with schedule_lock(ctx["registry"], name):
        take_over_legacy_state(ctx["registry"], name, str(Path(ctx["project"]) / OLD_REGISTRY), keep_definition=True)
        # The definition is the one the workflow's jobs carry, whatever the old record held.
        write_definition(ctx["registry"], name, row["definition"])
    j.update(name, state_moved=True)


def new_spec(row, ctx, text=None, frozen_at=None):
    """The spec the move publishes: the old one as it is — definition included — under this skill's command, in the same file and the old concurrency group. A one-off is judged as of the freeze: its time may pass while it is frozen, and resume then dispatches that slot."""
    if text is None:
        text = (read_workflow(ctx["repo"], row["file"], ctx["branch"]) or {}).get("text")
    recorded = parse_spec(text)
    when = min(ctx["now"], frozen_at or ctx["now"])
    spec = merge({"name": row["name"]}, recorded, timezone=recorded["timezone"], project=ctx["project"], cli=ctx["cli"], now=when, keep_definition=True)
    return {**spec, "file": row["file"], "concurrency": f"claude-{row['name']}"}


def publish(j: Journal, row, ctx):
    """3-5: a moved schedule's workflow rewritten whole by this skill's renderer in one commit against the file the inventory read; a finished one-off's workflow deleted, also only as the inventory read it. What a rollback needs is journaled before the commit, and a commit already made is recognised when the step runs again."""
    name, s = row["name"], j.of(row["name"])
    if s.get("published"):
        return
    if not s.get("state_moved"):
        raise RuntimeError(f"{name}: run the state step first")
    current = read_workflow(ctx["repo"], row["file"], ctx["branch"])
    if row["class"] == "finished":
        if current is not None and current.get("sha") != row["sha"]:
            raise RuntimeError(f"{name}: .github/workflows/{row['file']} changed since the inventory (sha {row['sha']}); find out what changed it before deleting it")
        if current is not None:
            j.update(name, pending={"old_text": current["text"], "old_sha": current["sha"]})
            done = delete_workflow(ctx["repo"], row["file"], f"Remove finished one-off {name} (moved to delegate)", ctx["branch"], current["sha"])
            j.update(name, published={"deleted": True, "commit": done["commit"], "old_text": current["text"]})
        else:
            j.update(name, published={"deleted": True, "commit": None, "old_text": (s.get("pending") or {}).get("old_text")})
        return
    pending = s.get("pending")
    old_text = (pending or {}).get("old_text") or (current or {}).get("text")
    spec = new_spec(row, ctx, text=old_text, frozen_at=s.get("frozen_at"))
    text = render_workflow(spec)
    stored = (schedule_state(ctx["registry"], name) or {}).get("definition")
    if spec["definition"] != stored:
        raise RuntimeError(f"{name}: the definition to publish {spec['definition']} is not the state's {stored}; nothing was committed")
    if pending and current is not None and current.get("text") == text:
        # The commit was made by an earlier run that stopped before journaling it.
        j.update(name, published={"commit": None, "old_text": pending["old_text"], "old_sha": pending["old_sha"]}, spec=spec)
        return
    if current is None or current.get("sha") != row["sha"]:
        raise RuntimeError(f"{name}: .github/workflows/{row['file']} changed since the inventory (sha {row['sha']}) and is not what this step writes; find out what changed it — `rollback` restores the old text")
    j.update(name, pending={"old_text": current["text"], "old_sha": current["sha"]})
    done = write_workflow(ctx["repo"], row["file"], text, f"Move schedule {name} to the delegate skill", ctx["branch"], sha=current["sha"])
    j.update(name, published={"commit": done["commit"], "old_text": current["text"], "old_sha": current["sha"]}, spec=spec)


def _run_state(registry, run_id):
    """The recorded state of a run of this skill, or None."""
    try:
        return read_meta(str(Path(registry) / run_id)).get("state") if run_id else None
    except Refusal:
        return None


def completed_anywhere(row, ctx):
    """Whether a one-off's slot was completed — by the old records, or by a run of this skill since."""
    if not row.get("at"):
        return False
    slot = int(at_instant(row))
    if completed_slot(ctx["project"], old_state(ctx["project"], row["name"]), slot):
        return True
    # This skill's record of that slot: the job that claimed it or its retry, whose run completed — a job taken over from the old side by how its run ended, a job of this skill by its run's meta. A cleanup marker says nothing of the slot: it can be another one-off's of the same name, and a completed run whose cleanup failed has none.
    state = schedule_state(ctx["registry"], row["name"]) or {}
    claim = (state.get("slots") or {}).get(str(slot)) or {}
    for job in (claim.get("retried"), claim.get("job")):
        rec = (state.get("jobs") or {}).get(job or "") or {}
        if (rec.get("legacy_run_state") if rec.get("legacy") else _run_state(ctx["registry"], rec.get("run_id"))) == "completed":
            return True
    return False


def expected(row, ctx):
    """`active` or `disabled`: the state the workflow should be in after the move, from the latest slot results."""
    if row["class"] == "finished" or completed_anywhere(row, ctx):
        return "disabled"
    return "active" if row.get("workflow_state") == "active" else "disabled"


def missed_slots(spec, since, now):
    """The schedule's fire times after `since` up to `now`."""
    out, t = [], since
    while True:
        nxt = [x for x in fire_times(spec, t, 8) if x <= now]
        out += nxt
        if len(nxt) < 8:
            return out
        t = nxt[-1]


def filled_slots(row, ctx):
    """The slots either side filled: this skill's record and the old one as it is now."""
    return set((schedule_state(ctx["registry"], row["name"]) or {}).get("slots") or {}) | set((old_state(ctx["project"], row["name"]) or {}).get("slots") or {})


def resume(j: Journal, row, ctx):
    """3-6: once the workflow is published under this skill, it is put back in its expected state — confirmed on GitHub — and only then this skill's dispatcher installed; the slots that passed while it was frozen and that neither side filled are dispatched with their own slot, through the same claim as any trigger."""
    name, s = row["name"], j.of(row["name"])
    if row["class"] == "finished":
        return
    if s.get("resumed"):
        return _recover_again(row, ctx, s)
    missing = [step for step, key in (("freeze", "drained"), ("state", "state_moved"), ("publish", "published")) if not s.get(key)]
    if missing:
        raise RuntimeError(f"{name}: run {', '.join(missing)} first; resuming before would start the old command under the new dispatcher")
    spec = s.get("spec") or new_spec(row, ctx, frozen_at=s.get("frozen_at"))
    want = expected(row, ctx)
    if want == "active":
        set_workflow_enabled(ctx["repo"], row["file"], True)
        if workflow_state(ctx["repo"], row["file"]) != "active":
            raise NotYet(f"{name}: GitHub does not show its workflow enabled yet")
        label = dispatcher_label(ctx["repo"], name)
        got = install_agent(label, dispatcher_argv(gh_path(), ctx["repo"], row["file"], ctx["branch"]), calendar_entries(fields_of(spec)), dispatcher_log(ctx["repo"], name))
        if got["status"] != "installed":
            raise NotYet(f"{name}: the dispatcher was not installed: {got.get('error')}")
        j.update(name, new_dispatcher=True)
        # The slots up to now, after the install: one whose time came while this command ran reached neither the frozen side nor a dispatcher loaded after it.
        filled, until = filled_slots(row, ctx), ctx["clock"]()
        for slot in missed_slots(spec, s.get("frozen_at") or ctx["now"], until):
            key = str(int(slot))
            if key not in filled and key not in (j.of(name).get("recovered") or []):
                dispatch_workflow(ctx["repo"], row["file"], ctx["branch"], {"source": "dispatcher", "slot": key})
                j.update(name, recovered=(j.of(name).get("recovered") or []) + [key])
        j.update(name, resumed=True, resumed_state=want)
    else:
        set_workflow_enabled(ctx["repo"], row["file"], False)
        j.update(name, resumed=True, resumed_state=want)


def unclaimed(row, ctx, s):
    """The slots resume dispatched that no run has claimed yet: a dispatch GitHub took is not a slot run until its run claims it."""
    filled = filled_slots(row, ctx)
    return [k for k in s.get("recovered") or [] if k not in filled]


def _recover_again(row, ctx, s):
    """A resume run again: each dispatched slot still unclaimed is waited for while a run of the workflow is on its way, and dispatched again once none is — a second dispatch of a slot already claimed is skipped by its exact claim."""
    open_ = unclaimed(row, ctx, s)
    if not open_:
        return None
    pending = unfinished_runs(ctx["repo"], row["file"])
    if pending:
        raise NotYet(f"{row['name']}: slot(s) {', '.join(open_)} were dispatched and are not claimed yet; {len(pending)} run(s) of its workflow are not finished")
    for key in open_:
        dispatch_workflow(ctx["repo"], row["file"], ctx["branch"], {"source": "dispatcher", "slot": key})
    return {"dispatched_again": open_}


def _own_file(row, ctx, s, old_text):
    """Stop unless the workflow file is one the move knows: the old text, what it published (or, when the publish stopped before journaling, what it would have), or nothing after a finished one-off's deletion. Anything else is someone's change since, which a rollback would undo."""
    current = read_workflow(ctx["repo"], row["file"], ctx["branch"])
    if current is None:
        if (s.get("published") or {}).get("deleted") or (s.get("pending") and row["class"] == "finished"):
            return
    else:
        known = {old_text}
        if s.get("spec"):
            known.add(render_workflow(s["spec"]))
        elif row["class"] != "finished":
            known.add(render_workflow(new_spec(row, ctx, text=old_text, frozen_at=s.get("frozen_at"))))
        if current.get("text") in known:
            return
    raise RuntimeError(f"{row['name']}: .github/workflows/{row['file']} changed since the move " + ("deleted it" if current is None else "wrote it") + "; nothing was rolled back — find out what changed it first")


def rollback(j: Journal, row, ctx):
    """Back to the `claude` skill for one schedule: the new side frozen and drained first, whatever slot it filled written into the old state, the old file restored by a commit, the workflow put in its expected state, and the old dispatcher put back and loaded. The slots that passed since the freeze and that neither side filled are reported: the old skill takes no slot, so they cannot be dispatched as themselves. Afterwards the schedule's journal starts over, so the move can be made again."""
    name, s = row["name"], j.of(row["name"])
    if s.get("rolled_back"):
        return {}
    # Whatever of this skill's dispatcher exists goes, also after a resume that stopped halfway.
    label = dispatcher_label(ctx["repo"], name)
    if agent_state(label) != "missing":
        _move_plist_out(label, j.backup / f"{label}.plist")
    old_text = (s.get("published") or {}).get("old_text") or (s.get("pending") or {}).get("old_text")
    if old_text:
        _own_file(row, ctx, s, old_text)
    if s.get("published") or s.get("pending") or s.get("new_dispatcher"):
        set_workflow_enabled(ctx["repo"], row["file"], False)
        pending = unfinished_runs(ctx["repo"], row["file"])
        if pending:
            raise NotYet(f"{name}: {len(pending)} run(s) of its workflow are not finished; run the rollback again once they end")
    note = {}
    new = schedule_state(ctx["registry"], name) or {}
    old_path = Path(ctx["project"]) / OLD_REGISTRY / ".schedules" / name / "state.json"
    # Both sides are drained, so nothing else writes the old record now. A schedule that never ran on the old side has none: it is made, with the definition its old workflow carries, so its Finish still knows the workflow as its own.
    old = old_state(ctx["project"], name) or {"name": name, "last_slot": None, "slots": {}, "jobs": {}, "definition": row.get("definition")}
    slots, changed = old.setdefault("slots", {}), []
    for key, claim in (new.get("slots") or {}).items():
        had = slots.get(key) or {}
        if not had.get("job"):
            slots[key] = {"job": claim.get("job"), "retried": claim.get("retried")}
        elif claim.get("retried") and not had.get("retried"):
            # A retry of an old slot the new side ran spends the slot's one retry.
            slots[key] = {**had, "retried": claim["retried"]}
        else:
            continue
        changed.append(key)
    if changed:
        old.setdefault("jobs", {}).update({job: {**rec, "moved_back": True} for job, rec in (new.get("jobs") or {}).items() if not rec.get("legacy")})
        old["last_slot"] = max([int(k) for k in slots] or [0]) or None
        old_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = old_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(old))
        os.replace(tmp, old_path)
        note["slots_moved_back"] = sorted(changed)
    if old_text:
        current = read_workflow(ctx["repo"], row["file"], ctx["branch"])
        if (current or {}).get("text") != old_text:
            write_workflow(ctx["repo"], row["file"], old_text, f"Move schedule {name} back to the claude skill", ctx["branch"], sha=(current or {}).get("sha"))
    want = expected(row, ctx)
    set_workflow_enabled(ctx["repo"], row["file"], want == "active")
    backup = Path(s.get("plist_backup") or "")
    if want == "active" and backup.is_file():
        old_label = legacy_dispatcher_label(ctx["repo"], name)
        shutil.copy2(backup, agents_dir() / f"{old_label}.plist")
        loaded = load_agent(old_label)
        if loaded["status"] != "loaded":
            raise NotYet(f"{name}: the old dispatcher is back in place but launchd did not load it: {loaded.get('error')}; run the rollback again")
    if s.get("frozen_at"):
        spec = s.get("spec")
        if spec is None:
            try:
                spec = new_spec(row, ctx, text=old_text, frozen_at=s["frozen_at"])
            except Exception:  # a spec that cannot be rebuilt only costs the note
                spec = None
        if spec:
            filled = filled_slots(row, ctx)
            missed = [str(int(t)) for t in missed_slots(spec, s["frozen_at"], ctx["clock"]()) if str(int(t)) not in filled]
            if missed:
                note["missed_slots"] = missed
    j.data["schedules"][name] = {"rolled_back": True, "rolled_back_at": ctx["now"], "rollback_note": note}
    j.save()
    return note
