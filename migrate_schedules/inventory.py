"""Step 3-1, read only: every schedule the `claude` skill wrote in a repository, what it is now on GitHub and on this Mac, and what the move would do with it — nothing is written anywhere.

A schedule's class decides its fate. `recurring` and `upcoming` (a one-off whose time is ahead) are moved; `finished` (a one-off whose time passed, whose workflow is disabled, and whose slot a worker completed by the old records) is deleted; anything else — a time passed but still enabled, a time ahead but disabled, no completion on record, a file that does not parse — is `held`: left alone and reported with why.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from delegate.github import branch_protected, list_workflow_files, parse_spec, read_workflow, repo_facts, workflow_index, written_by_legacy
from delegate.launchd import agent_state, agents_dir
from delegate.schedule import fire_times, legacy_dispatcher_label

OLD_REGISTRY = ".claude-runs"
# What a reference to the `claude` skill that a moved schedule needs looks like: its command or its skill names, in a prompt or in the instructions a worker reads.
OLD_SKILL_MARKS = ("skills/claude/scripts", "Skill(claude)", "Skill(codex)", "/claude ", "/codex ", "skills/codex/scripts")


def at_instant(spec):
    return datetime.strptime(spec["at"], "%Y-%m-%dT%H:%M").replace(tzinfo=ZoneInfo(spec["timezone"])).timestamp()


def old_state(project, name):
    """The `claude` skill's state.json for schedule `name`, or None."""
    path = Path(project) / OLD_REGISTRY / ".schedules" / name / "state.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def old_run_state(project, run_id):
    """The recorded state of a run in the `claude` skill's registry, or None."""
    try:
        return json.loads((Path(project) / OLD_REGISTRY / run_id / "meta.json").read_text(encoding="utf-8")).get("state")
    except (OSError, ValueError, TypeError):
        return None


def completed_slot(project, state, slot):
    """The job that completed `slot` by the old records — the slot's job or its retry, whose run's meta says completed — or None."""
    claim = ((state or {}).get("slots") or {}).get(str(int(slot))) or {}
    for job in (claim.get("retried"), claim.get("job")):
        rec = ((state or {}).get("jobs") or {}).get(job or "") or {}
        if rec.get("run_id") and old_run_state(project, rec["run_id"]) == "completed":
            return {"job": job, "run_id": rec["run_id"]}
    return None


def classify(spec, wf_state, done, now):
    """(`recurring` | `upcoming` | `finished` | `held`, why) for a schedule, from its spec, its workflow's state on GitHub and whether its one-off slot was completed."""
    if not spec.get("at"):
        return ("recurring", None) if wf_state == "active" else ("held", f"a recurring schedule whose workflow is {wf_state}; it is moved only as it is, so it is left for you")
    passed = at_instant(spec) <= now
    if not passed:
        return ("upcoming", None) if wf_state == "active" else ("held", f"its time is ahead but its workflow is {wf_state}")
    if wf_state == "active":
        return "held", "its time has passed but its workflow is still enabled"
    if not done:
        return "held", "its time has passed and its workflow is disabled, but no record says a worker completed its slot"
    return "finished", None


def leftovers(project):
    """What the `claude` skill's worktree runs left in the project: checkouts under .claude/worktrees and refs/claude/leftovers/*, which the move does not touch."""
    def git(*args):
        p = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True)
        return p.stdout.splitlines() if p.returncode == 0 else []

    worktrees = [l.split(" ", 1)[1] for l in git("worktree", "list", "--porcelain") if l.startswith("worktree ") and "/.claude/worktrees/" in l]
    refs = git("for-each-ref", "--format=%(refname)", "refs/claude/leftovers/")
    return {"worktrees": worktrees, "refs": refs}


def old_skill_mentions(project, specs):
    """Where a moved schedule's worker would still be told to use the old skills: its prompt, and the project's CLAUDE.md, AGENTS.md and skills."""
    found = []
    for spec in specs:
        hits = [m for m in OLD_SKILL_MARKS if m in (spec.get("prompt") or "")]
        if hits:
            found.append({"where": f"prompt of {spec['name']}", "marks": hits})
    for path in [Path(project) / "CLAUDE.md", Path(project) / "AGENTS.md", *sorted((Path(project) / ".claude" / "skills").glob("*/SKILL.md"))]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        hits = [m for m in OLD_SKILL_MARKS if m in text]
        if hits:
            found.append({"where": str(path), "marks": hits})
    return found


def inventory(project, repo, now):
    """The report of step 3-1 for `repo`, whose checkout is `project`. Reads GitHub, launchd and both registries; writes nothing."""
    facts = repo_facts(repo)
    repo = facts["full_name"] or repo
    branch = facts["default_branch"]
    index = workflow_index(repo)
    states = {f: w["state"] for f, w in index.items()}
    rows, specs = [], []
    for entry in list_workflow_files(repo, branch):
        text = entry.get("text")
        if not text or not written_by_legacy(text):
            continue
        file = entry["file"]
        try:
            spec = parse_spec(text)
        except ValueError as e:
            rows.append({"file": file, "class": "held", "why": f"its spec line does not parse: {e}"})
            continue
        if not spec:
            rows.append({"file": file, "class": "held", "why": "it has the old header but no spec naming it"})
            continue
        specs.append(spec)
        name = spec["name"]
        current = read_workflow(repo, file, branch) or {}
        state = old_state(project, name)
        label = legacy_dispatcher_label(repo, name)
        wf_state = states.get(file)
        done = completed_slot(project, state, at_instant(spec)) if spec.get("at") else None
        kind, why = classify(spec, wf_state, done, now)
        rows.append({
            "name": name,
            "file": file,
            "workflow_id": (index.get(file) or {}).get("id"),
            "sha": current.get("sha"),
            "workflow_state": wf_state,
            "class": kind,
            "why": why,
            "cron": spec.get("cron"),
            "at": spec.get("at"),
            "timezone": spec.get("timezone"),
            "profile": spec.get("profile"),
            "model": spec.get("model"),
            "effort": spec.get("effort"),
            "worktree": spec.get("worktree"),
            "definition": spec.get("definition"),
            "state_definition": (state or {}).get("definition"),
            "next": [datetime.fromtimestamp(t, ZoneInfo(spec["timezone"])).isoformat() for t in fire_times(spec, now, 2)],
            "completed": done,
            "dispatcher": {"label": label, "plist": str(agents_dir() / f"{label}.plist"), "state": agent_state(label)},
            "last_slot": (state or {}).get("last_slot"),
            "jobs": len((state or {}).get("jobs") or {}),
        })
    counts = {}
    for r in rows:
        counts[r["class"]] = counts.get(r["class"], 0) + 1
    return {
        "repo": repo,
        "branch": branch,
        # A protected default branch refuses the move's commits: they would go through a pull request prepared ahead, merged in the freeze.
        "branch_protected": branch_protected(repo, branch),
        "project": str(project),
        "at": datetime.fromtimestamp(now).astimezone().isoformat(),
        "counts": counts,
        "schedules": rows,
        "leftovers": leftovers(project),
        "old_skill_mentions": old_skill_mentions(project, specs),
    }
