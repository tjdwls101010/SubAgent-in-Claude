"""The transition's completion checks (step 5 ①–③) for one repository, read from GitHub and this Mac now: every schedule the inventory listed is accounted for — moved and running the new command in its expected state with its dispatcher loaded, deleted, or held — and no workflow on the default branch and no LaunchAgent still runs the old skill."""

from __future__ import annotations

from delegate.github import list_workflow_files, parse_spec, workflow_states, written_by_legacy
from delegate.launchd import agent_state, agents_dir
from delegate.registry import schedule_state
from delegate.schedule import dispatcher_label, legacy_dispatcher_label

from .stages import expected

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
            "new_cli": bool(spec) and spec.get("cli") == ctx["cli"],
            "state": states.get(row["file"]) == ("active" if want == "active" else "disabled_manually"),
            "definition": bool(spec) and spec.get("definition") == (schedule_state(ctx["registry"], name) or {}).get("definition"),
            "dispatcher": agent_state(dispatcher_label(ctx["repo"], name)) == ("loaded" if want == "active" else "missing"),
        }
        rows.append({"name": name, "disposition": "moved", "expected": want, "checks": checks, "ok": all(checks.values())})
        problems += [f"{name}: {k}" for k, v in checks.items() if not v]
    held_files = {r["file"] for r in rows if r["disposition"] == "held"}
    still_old = sorted(f for f, t in files.items() if t and f not in held_files and (written_by_legacy(t) or OLD_CLI in t))
    problems += [f"{f}: still runs the claude skill" for f in still_old]
    held_labels = {f"{legacy_dispatcher_label(ctx['repo'], r['name'])}.plist" for r in inv["schedules"] if r["class"] == "held" and r.get("name")}
    old_agents = sorted(p.name for p in agents_dir().glob("*.plist") if p.name not in held_labels and (p.name.startswith("claude-schedule.") or OLD_CLI in p.read_text(errors="replace")))
    problems += [f"{a}: a LaunchAgent that runs the claude skill" for a in old_agents]
    return {"ok": not problems, "problems": problems, "schedules": rows, "old_workflows": still_old, "old_launch_agents": old_agents}
