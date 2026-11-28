"""`doctor`: the environment a run would start in, per engine — its CLI and version, login, what its profiles need, settings that widen them — and the registry. It starts no worker."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from delegate.engine import ENGINES, adapter
from delegate.github import GitHubError, gh_status, local_runners, repo_facts, repo_from_remote, runner_problems, runners, workflow_states
from delegate.git import main_checkout, remote_url, toplevel, worktree_entries
from delegate.launchd import agent_state, supported as launchd_supported
from delegate.registry import DIRNAME, list_groups, live_runs, read_meta, resolve_runs_dir
from delegate.util import entry_path, invocation, skill_dir, timings


def doctor(*, cwd, engine=None, runs_dir=None):
    blockers, warnings = [], []
    if sys.version_info < (3, 11):
        blockers.append(f"Python {sys.version.split()[0]} is older than 3.11; run the skill through uv run")
    engines = {}
    for name in ([engine] if engine else list(ENGINES)):
        d = adapter(name).diagnose(cwd)
        engines[name] = {**d["report"], "ok": not d["blockers"], "blockers": d["blockers"], "warnings": d["warnings"]}
        if not engine and d["report"].get("installed") is False:
            # One engine missing stops only its own runs: named with --engine it is a blocker, otherwise a warning in the verdict (the engine's own `ok` stays false).
            warnings += [f"{name}: {b}" for b in d["blockers"] + d["warnings"]]
            continue
        blockers += [f"{name}: {b}" for b in d["blockers"]]
        warnings += [f"{name}: {w}" for w in d["warnings"]]
    if not engine and engines and all(e.get("installed") is False for e in engines.values()):
        blockers.append("no engine is installed: neither " + " nor ".join(engines) + " is on PATH")
    registry = resolve_runs_dir(cwd, runs_dir)
    reg = {"path": registry, "exists": os.path.isdir(registry)}
    # Run commands create the registry with its missing parents, so what must be writable is the nearest folder that exists.
    probe = Path(registry)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    locks = Path(registry) / ".locks"
    reg["writable"] = probe.is_dir() and os.access(probe, os.W_OK) and (not locks.exists() or (locks.is_dir() and os.access(locks, os.W_OK)))
    if not reg["writable"]:
        blockers.append(f"the registry {registry} cannot be written (it, or the nearest folder that exists, is not a writable directory)")
    reg["live_runs"] = [m.get("run_id") for _, m in live_runs(registry)] if reg["exists"] else []
    groups = list_groups(registry) if reg["exists"] else []
    reg["groups"] = [g["name"] for g in groups]
    # A checkout belongs to the group whose member recorded its exact path; names alone are ambiguous (fix-1 against fix-db-1).
    owner = {}
    for g in groups:
        for slot in g.get("members") or []:
            if slot.get("kind", "start") != "start":
                # A resumed member works in a checkout an earlier group made, which stays that group's.
                continue
            path = (slot.get("worktree") or {}).get("path")
            if not path and slot.get("run_id"):
                try:
                    path = (read_meta(Path(registry) / slot["run_id"]).get("worktree") or {}).get("path")
                except Exception:
                    path = None
            if path:
                owner[os.path.realpath(path)] = g["name"]
    repo = main_checkout(cwd)
    # A path a group's slot records is listed wherever its registry is: a codex checkout given --runs-dir is not under .delegate-runs.
    reg["worktrees"] = [{"path": e["path"], "branch": e["branch"], "group": owner.get(os.path.realpath(e["path"]))} for e in (worktree_entries(repo) if repo else []) if "/.claude/worktrees/" in e["path"] or (DIRNAME in e["path"] and e["path"].endswith("/wt")) or os.path.realpath(e["path"]) in owner]
    sched, sched_warnings = schedule_readiness_within(cwd, registry, timings()["doctor_gh_wait"])
    warnings += sched_warnings
    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": sorted(set(warnings), key=warnings.index),
        "python": sys.version.split()[0],
        "engines": engines,
        "skill_dir": skill_dir(),
        "entry": entry_path(),
        "invocation": invocation("<command>"),
        "registry": reg,
        "schedule": sched,
    }


def schedule_readiness_within(cwd, registry, seconds):
    """schedule_readiness, given up after `seconds` or on a gh that cannot run: what scheduling needs must not hold up or break the diagnosis of ordinary runs."""
    import threading

    box = {}

    def work():
        try:
            box["result"] = schedule_readiness(cwd, registry)
        except GitHubError as e:
            box["result"] = ({"platform_ok": launchd_supported(), "gh": {"error": e.message}, "repo": None, "runners": None}, [f"schedules need gh, which failed: {e.message}"])
        except Exception as e:  # a check that is only advice must not break doctor or pass for a timeout
            box["result"] = ({"platform_ok": launchd_supported(), "gh": None, "repo": None, "runners": None, "error": f"{type(e).__name__}: {e}"}, [f"whether this project can be scheduled could not be checked: {type(e).__name__}: {e}"])

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(seconds)
    if "result" not in box:
        return {"platform_ok": launchd_supported(), "gh": {"error": f"no answer within {seconds:g} s"}, "repo": None, "runners": None}, [f"gh did not answer within {seconds:g} s, so whether this project can be scheduled is unknown"]
    return box["result"]


def schedule_readiness(cwd, registry):
    """What `schedule` would need from here, read without changing anything (K13): this platform, gh, the project's GitHub repository, and its runners — GitHub's list and this Mac's services, with the same `problems` a `schedule` reply carries. A doctor in a conversation runs in the login session and cannot see what the runner's service sees; only a check run proves that. Nothing here is a blocker, since ordinary runs do not need it."""
    warnings = []
    platform_ok = launchd_supported()
    if not platform_ok:
        warnings.append("schedules need launchd, so macOS")
    gh = gh_status()
    out = {"platform_ok": platform_ok, "gh": {"path": gh["path"], "logged_in": gh["logged_in"], "workflow_scope": "workflow" in gh["scopes"]}, "repo": None, "runners": None}
    if gh["error"]:
        warnings.append(f"schedules need gh: {gh['error']}")
        return out, warnings
    if "workflow" not in gh["scopes"]:
        warnings.append("gh's token lacks the `workflow` scope that committing a schedule needs; `gh auth refresh -s workflow`")
    top = toplevel(cwd)
    project = (main_checkout(cwd) or top) if top else None
    repo = repo_from_remote(remote_url(project)) if project else None
    if not repo:
        out["repo"] = {"error": "not a git checkout with a GitHub remote named origin"}
        warnings.append("schedules need a git checkout with a GitHub remote named origin")
        return out, warnings
    try:
        facts = repo_facts(repo)
    except GitHubError as e:
        out["repo"] = {"name": repo, "error": e.message}
        warnings.append(f"gh cannot see {repo}: {e.message}")
        return out, warnings
    repo = facts["full_name"] or repo  # GitHub's spelling, as `schedule` uses it
    out["repo"] = {"name": repo, "private": facts["private"], "default_branch": facts["default_branch"]}
    out["unfinished_one_offs"] = unfinished_one_offs(registry, repo)
    warnings += [f"schedule {u['name']}: {u['why']}; {u['instead']}" for u in out["unfinished_one_offs"]]
    if not facts["private"]:
        warnings.append(f"{repo} is public: `schedule` refuses it unless --public-ok")
    try:
        registered = runners(repo)
    except GitHubError as e:
        out["runners"] = {"error": e.message}
        warnings.append(f"the runners of {repo} could not be listed: {e.message}")
        return out, warnings
    local = local_runners(repo)
    problems = runner_problems(registered, local)
    out["runners"] = {"registered": registered, "local": local, "problems": problems}
    warnings += [f"schedule runner: {p['problem']} — {p['why']}" for p in problems]
    return out, warnings


def unfinished_one_offs(registry, repo):
    """One-offs whose run's Finish tried to end them and left part of it undone: the workflow still enabled on GitHub, or the dispatcher still on this Mac. Read from what each job recorded of its end, checked against GitHub and launchd now."""
    from delegate.registry import schedule_state
    from delegate.schedule import dispatcher_label

    found, states = [], None
    root = Path(registry) / ".schedules"
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        try:
            state = schedule_state(registry, d.name) or {}
        except ValueError:
            continue
        name = state.get("name") or d.name
        ended = [(job, rec) for job, rec in (state.get("jobs") or {}).items() if rec.get("one_off_end")]
        if not ended:
            continue
        job, rec = max(ended, key=lambda x: (x[1]["one_off_end"].get("at") or 0, x[0]))
        if rec["one_off_end"].get("definition") != state.get("definition"):
            # Scheduled again since, or removed: what that run left is not this schedule's now.
            continue
        why = []
        if not rec["one_off_end"].get("disabled"):
            if states is None:
                try:
                    states = workflow_states(repo)
                except GitHubError:
                    states = {}
            # The file the end recorded; an end recorded before it did names none, and the schedule's file is one of the two.
            files = (rec["one_off_end"]["workflow"],) if rec["one_off_end"].get("workflow") else (f"{name}.yml", f"claude-{name}.yml")
            if any(v == "active" for f, v in states.items() if f in files):
                why.append("its workflow is still enabled")
        if agent_state(dispatcher_label(repo, name)) != "missing":
            why.append("its dispatcher is still on this Mac")
        if why:
            found.append({"name": name, "job": job, "why": "its run ended, but " + " and ".join(why), "instead": invocation("unschedule", "--name", name)})
    return found
