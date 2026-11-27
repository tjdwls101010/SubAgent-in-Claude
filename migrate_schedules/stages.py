"""Steps 3-3 to 3-6 and the rollback, one schedule at a time, each recorded in the journal as it is done so a run stopped anywhere goes on where it stopped and a step already done is skipped.

The expected end state comes from the latest slot results, never from the inventory alone: a one-off a worker completed — on the old side while its queue drained, or on the new side — ends disabled with no dispatcher, whatever its state was when the inventory was taken.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from delegate.github import delete_workflow, dispatch_workflow, gh_path, parse_spec, read_workflow, render_workflow, set_workflow_enabled, unfinished_runs, workflow_state, write_workflow
from delegate.launchd import agents_dir, calendar_entries, install_agent, load_agent, remove_agent
from delegate.registry import schedule_lock, schedule_state, take_over_legacy_state, write_definition
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
    """3-4: the old state.json, as it stands after the queue drained, taken over as this skill's record — slots and jobs, each old job marked legacy with how its run ended, so a retry decision about an old slot still has an answer — with the definition kept, and the old record left as it was for a rollback."""
    name = row["name"]
    if j.of(name).get("state_moved"):
        return
    with schedule_lock(ctx["registry"], name):
        if not take_over_legacy_state(ctx["registry"], name, str(Path(ctx["project"]) / OLD_REGISTRY), keep_definition=True):
            # A schedule that never ran has no old record: its definition is the workflow's.
            write_definition(ctx["registry"], name, row["definition"])
    j.update(name, state_moved=True)


def new_spec(row, ctx):
    """The spec the move publishes: the old one as it is — definition included — under this skill's command, in the same file and the old concurrency group."""
    text = (read_workflow(ctx["repo"], row["file"], ctx["branch"]) or {}).get("text")
    recorded = parse_spec(text)
    spec = merge({"name": row["name"]}, recorded, timezone=recorded["timezone"], project=ctx["project"], cli=ctx["cli"], now=ctx["now"], keep_definition=True)
    return {**spec, "file": row["file"], "concurrency": f"claude-{row['name']}"}


def publish(j: Journal, row, ctx):
    """3-5: a moved schedule's workflow rewritten whole by this skill's renderer in one commit against the file the inventory read; a finished one-off's workflow deleted. The definition published is checked against the state just written."""
    name, s = row["name"], j.of(row["name"])
    if s.get("published"):
        return
    if row["class"] == "finished":
        current = read_workflow(ctx["repo"], row["file"], ctx["branch"])
        if current is not None:
            done = delete_workflow(ctx["repo"], row["file"], f"Remove finished one-off {name} (moved to delegate)", ctx["branch"], current["sha"])
            j.update(name, published={"deleted": True, "commit": done["commit"], "old_text": current["text"]})
        else:
            j.update(name, published={"deleted": True, "commit": None})
        return
    current = read_workflow(ctx["repo"], row["file"], ctx["branch"])
    if current is None or current.get("sha") != row["sha"]:
        raise RuntimeError(f"{name}: .github/workflows/{row['file']} changed since the inventory (sha {row['sha']}); take the inventory again")
    spec = new_spec(row, ctx)
    text = render_workflow(spec)
    done = write_workflow(ctx["repo"], row["file"], text, f"Move schedule {name} to the delegate skill", ctx["branch"], sha=current["sha"])
    back = parse_spec((read_workflow(ctx["repo"], row["file"], ctx["branch"]) or {}).get("text"))
    stored = (schedule_state(ctx["registry"], name) or {}).get("definition")
    if not back or back.get("definition") != stored:
        raise RuntimeError(f"{name}: the published definition {(back or {}).get('definition')} is not the state's {stored}")
    j.update(name, published={"commit": done["commit"], "old_text": current["text"], "old_sha": current["sha"]}, spec=spec)


def completed_anywhere(row, ctx):
    """Whether a one-off's slot was completed — by the old records, or by a run of this skill since."""
    if not row.get("at"):
        return False
    slot = int(at_instant(row))
    if completed_slot(ctx["project"], old_state(ctx["project"], row["name"]), slot):
        return True
    state = schedule_state(ctx["registry"], row["name"]) or {}
    return any((r.get("one_off_end") or {}).get("done") or (r.get("slot") == slot and r.get("legacy_run_state") == "completed") for r in (state.get("jobs") or {}).values())


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


def resume(j: Journal, row, ctx):
    """3-6: the workflow put back in its expected state — confirmed on GitHub — and only then this skill's dispatcher installed; the slots that passed while it was frozen and that nobody filled dispatched with their own slot, through the same claim as any trigger."""
    name, s = row["name"], j.of(row["name"])
    if row["class"] == "finished" or s.get("resumed"):
        return
    spec = s.get("spec") or new_spec(row, ctx)
    want = expected(row, ctx)
    if want == "active":
        set_workflow_enabled(ctx["repo"], row["file"], True)
        if workflow_state(ctx["repo"], row["file"]) != "active":
            raise NotYet(f"{name}: GitHub does not show its workflow enabled yet")
        label = dispatcher_label(ctx["repo"], name)
        got = install_agent(label, dispatcher_argv(gh_path(), ctx["repo"], row["file"], ctx["branch"]), calendar_entries(fields_of(spec)), dispatcher_log(ctx["repo"], name))
        if got["status"] != "installed":
            raise NotYet(f"{name}: the dispatcher was not installed: {got.get('error')}")
        filled = set((schedule_state(ctx["registry"], name) or {}).get("slots") or {})
        recovered = []
        for slot in missed_slots(spec, s.get("frozen_at") or ctx["now"], ctx["now"]):
            if str(int(slot)) not in filled and str(int(slot)) not in (s.get("recovered") or []):
                dispatch_workflow(ctx["repo"], row["file"], ctx["branch"], {"source": "dispatcher", "slot": str(int(slot))})
                recovered.append(str(int(slot)))
        j.update(name, resumed=True, resumed_state=want, recovered=(s.get("recovered") or []) + recovered)
    else:
        set_workflow_enabled(ctx["repo"], row["file"], False)
        j.update(name, resumed=True, resumed_state=want, recovered=s.get("recovered") or [])


def rollback(j: Journal, row, ctx):
    """Back to the `claude` skill for one schedule: the new side frozen and drained first, whatever slot it filled written into the old state, the old file restored by a commit, the workflow put in its expected state, and the old dispatcher put back. A slot missed meanwhile that the old skill cannot fill (it takes no slot) is reported."""
    name, s = row["name"], j.of(row["name"])
    if s.get("rolled_back"):
        return {}
    if s.get("resumed") and not s.get("new_frozen"):
        label = dispatcher_label(ctx["repo"], name)
        _move_plist_out(label, j.backup / f"{label}.plist")
        set_workflow_enabled(ctx["repo"], row["file"], False)
        j.update(name, new_frozen=True)
    if s.get("resumed"):
        pending = unfinished_runs(ctx["repo"], row["file"])
        if pending:
            raise NotYet(f"{name}: {len(pending)} run(s) of its workflow are not finished; run the rollback again once they end")
    note = {}
    new = schedule_state(ctx["registry"], name) or {}
    old_path = Path(ctx["project"]) / OLD_REGISTRY / ".schedules" / name / "state.json"
    old = old_state(ctx["project"], name)
    if old is not None:
        fresh = {k: v for k, v in (new.get("slots") or {}).items() if k not in (old.get("slots") or {})}
        if fresh:
            old["slots"].update(fresh)
            old["jobs"].update({job: {**rec, "moved_back": True} for job, rec in (new.get("jobs") or {}).items() if not rec.get("legacy")})
            old["last_slot"] = max([int(k) for k in old["slots"]] or [0]) or None
            tmp = old_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(old))
            os.replace(tmp, old_path)
            note["slots_moved_back"] = sorted(fresh)
    published = s.get("published") or {}
    if published.get("old_text"):
        current = read_workflow(ctx["repo"], row["file"], ctx["branch"])
        write_workflow(ctx["repo"], row["file"], published["old_text"], f"Move schedule {name} back to the claude skill", ctx["branch"], sha=(current or {}).get("sha"))
    want = expected(row, ctx)
    set_workflow_enabled(ctx["repo"], row["file"], want == "active")
    backup = Path(s.get("plist_backup") or "")
    if want == "active" and backup.is_file():
        label = legacy_dispatcher_label(ctx["repo"], name)
        shutil.copy2(backup, agents_dir() / f"{label}.plist")
        load_agent(label)
    j.update(name, rolled_back=True, rollback_note=note)
    return note
