"""The transition's completion checks (step 5 ①–③, ⑥ and ⑧) for one repository, read from GitHub and this Mac now: every schedule the inventory listed is accounted for — moved and running the new command in its expected state with its dispatcher loaded and every slot it dispatched claimed, deleted, or held and not active on the old command — and no workflow on the default branch and no LaunchAgent still runs the old skill."""

from __future__ import annotations

from delegate.github import list_workflow_files, parse_spec, render_workflow, workflow_states, written_by_legacy
from delegate.launchd import agent_state, agents_dir
from delegate.registry import schedule_state
from delegate.schedule import dispatcher_label, legacy_dispatcher_label

from .stages import expected, unclaimed

OLD_CLI = "skills/claude/scripts/cli.py"


def verify(j, ctx):
    inv = j.inventory or {"schedules": []}
    files = {f["file"]: f.get("text") for f in list_workflow_files(ctx["repo"], ctx["branch"])}
    states = workflow_states(ctx["repo"])
    rows, problems = [], []
    for row in inv["schedules"]:
        name = row.get("name")
        if row["class"] == "held" or not name:
            rows.append({"name": name, "file": row["file"], "disposition": "held", "why": row.get("why")})
            text = files.get(row["file"]) or ""
            # Held is left as it is, but an active one still fires the old command: the old skill cannot go while it does (step 5 ⑧).
            if states.get(row["file"]) == "active" and (written_by_legacy(text) or OLD_CLI in text):
                problems.append(f"{row['file']}: held ({row.get('why')}) and still active with the claude skill's command; disable it, or decide what it becomes, before the old skill goes")
            continue
        if row["class"] == "finished":
            ok = row["file"] not in files and agent_state(dispatcher_label(ctx["repo"], name)) == "missing"
            rows.append({"name": name, "disposition": "deleted", "ok": ok})
            if not ok:
                problems.append(f"{name}: a finished one-off's workflow or dispatcher is still there")
            continue
        text = files.get(row["file"])
        spec = parse_spec(text) if text else None
        want = expected(row, ctx)
        checks = {
            "new_header": bool(text) and not written_by_legacy(text),
            # The whole file as the move published it — its cron, its steps' commands — not only the spec line a broken body still carries.
            "published": bool(text) and bool(j.of(name).get("spec")) and text == render_workflow(j.of(name)["spec"]),
            "new_cli": bool(spec) and spec.get("cli") == ctx["cli"],
            "state": states.get(row["file"]) == ("active" if want == "active" else "disabled_manually"),
            "definition": bool(spec) and spec.get("definition") == (schedule_state(ctx["registry"], name) or {}).get("definition"),
            "dispatcher": agent_state(dispatcher_label(ctx["repo"], name)) == ("loaded" if want == "active" else "missing"),
            "recovered": not unclaimed(row, ctx, j.of(name)),
        }
        rows.append({"name": name, "disposition": "moved", "expected": want, "checks": checks, "ok": all(checks.values())})
        problems += [f"{name}: {k}" + (f" (slot(s) {', '.join(unclaimed(row, ctx, j.of(name)))} dispatched, not claimed; run resume again)" if k == "recovered" else "") for k, v in checks.items() if not v]
    held_files = {r["file"] for r in rows if r["disposition"] == "held"}
    still_old = sorted(f for f, t in files.items() if t and f not in held_files and (written_by_legacy(t) or OLD_CLI in t))
    problems += [f"{f}: still runs the claude skill" for f in still_old]
    held_labels = {f"{legacy_dispatcher_label(ctx['repo'], r['name'])}.plist" for r in inv["schedules"] if r["class"] == "held" and r.get("name")}
    prefix = legacy_dispatcher_label(ctx["repo"], "")
    # This repository's old dispatchers only: another repository's are its own move's business.
    old_agents = sorted(p.name for p in agents_dir().glob(f"{prefix}*.plist") if p.name not in held_labels)
    # Every schedule a step touched must have finished the move or been moved back.
    for name, s in sorted(j.data["schedules"].items()):
        row = next((r for r in inv["schedules"] if r.get("name") == name), {})
        if s and not s.get("rolled_back") and not s.get("resumed") and not (row.get("class") == "finished" and s.get("published")):
            problems.append(f"{name}: the move stopped after {', '.join(k for k in ('drained', 'state_moved', 'published') if s.get(k)) or 'it started'}; run the next step or roll it back")
    problems += [f"{a}: a LaunchAgent that runs the claude skill" for a in old_agents]
    return {"ok": not problems, "problems": problems, "schedules": rows, "old_workflows": still_old, "old_launch_agents": old_agents}
