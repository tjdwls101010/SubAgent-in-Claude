"""A scheduled job's two steps on the runner (K1): `Run` starts the worker and holds the job until it ends; `Finish`, which GitHub runs after any end — success, failure, a cancel, a timeout — stops what is still alive, records what changed, brings the project's checkout up to what GitHub's default branch holds (K30), cleans up and writes the summary.

Run, in order (K28): the project must exist (K23); a check only checks (K13); an automatic trigger decides and claims its slot (K4), any other job is recorded; refusals that cost nothing come before the worker; the worker is started under the schedule's origin; the step follows it to its end, printing its activity and warning on attention without stopping it; the exit code is the run's outcome.

Finish's stages are independent (K20): each failure is collected into the summary, and the step exits 1 only when it could not confirm the worker stopped or could not write the step summary.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import shlex
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from delegate.batch import batch, clean
from delegate.engine import adapter
from delegate.errors import Refusal
from delegate.git import commits_beyond, committed_files, contains, head_commit, numstat, status_snapshot, toplevel
from delegate.github import JOB_HANDLES, GitHubError, annotation, job_context, legacy_workflow_file, pull_evidence, resume_commands, scrub_job_env, set_workflow_enabled, stop_commands, write_output, write_summary
from delegate.launchd import remove_agent
from delegate.observe import settled_lines, snapshot, wait_ended
from delegate.registry import claim_slot, find_by_origin_job, group_view, is_live, list_groups, read_definition, read_job, read_meta, reap, record_job, resolve_runs_dir, schedule_dir, schedule_lock, sync_lock, update_group, update_job, update_meta, worker_alive
from delegate.runs import start, stop
from delegate.util import entry_path, invocation, now

from .local import left_alone, sync_local
from .spec import dispatcher_holder, dispatcher_label, holder_words, worktree_group
from .summary import code_span, render_summary, went_text
from .times import current_slot

# A one-off's automatic triggers more than this after its time do nothing: GitHub drops a queued job after 24 hours, and a cron without a year would otherwise fire again a year later (K4).
ONE_OFF_EXPIRY = 26 * 3600
FOLLOW_SECONDS = 2.0
# `recovery` is a dispatch that names the slot it fills (the `slot` input): one that was missed, while the schedule was moved or this Mac was off.
AUTOMATIC = ("dispatcher", "schedule", "recovery")
# How much of the earlier schedules' worktree groups one Finish looks at, so the summary and the one-off's end still have their time (K20).
SWEEP_GROUPS = 10
SWEEP_SECONDS = 60
RED = ("failed", "timed_out", "orphaned", "interrupted", "quota_exhausted")


def clock():
    """Now, in epoch seconds; DELEGATE_TEST_NOW fixes it in tests, as DELEGATE_TEST_TIMINGS fixes the timings."""
    fixed = os.environ.get("DELEGATE_TEST_NOW")
    return float(fixed) if fixed else time.time()


class Step:
    """A step's stdout: plain lines, and the step's own workflow commands. From the first line on, the runner reads no workflow command it does not get through `command` (K21), so nothing a worker writes can annotate the run, mask a value or end the step."""

    def __init__(self, write):
        self.write = write
        self.token = secrets.token_hex(12)
        self.lock = threading.Lock()
        self.say(stop_commands(self.token))

    def say(self, line):
        with self.lock:
            self.write(line + "\n")

    def command(self, line):
        with self.lock:
            self.write(f"{resume_commands(self.token)}\n{line}\n{stop_commands(self.token)}\n")


def trigger_of(ctx, source, slot=""):
    """`dispatcher`, `schedule` or `recovery` (a dispatch naming its slot) for the automatic triggers, `re-run` for any attempt after the first (it is someone pressing Re-run, whatever the first attempt was), `manual` otherwise."""
    if ctx["attempt"] > 1:
        return "re-run"
    if ctx["event"] == "schedule":
        return "schedule"
    if (slot or "").strip():
        return "recovery"
    if source == "dispatcher":
        return "dispatcher"
    return "manual"


def local_time(ts, tz):
    d = datetime.fromtimestamp(ts, ZoneInfo(tz))
    return f"{d.strftime('%Y-%m-%d %H:%M')} {d.tzname()}"


def at_instant(spec):
    return int(datetime.strptime(spec["at"], "%Y-%m-%dT%H:%M").replace(tzinfo=ZoneInfo(spec["timezone"])).timestamp())


def asked_slot(spec, text, now):
    """The slot a recovery names, as (epoch seconds, None), or (None, why it cannot be filled): it must be one of the schedule's own fire times, and one that has come."""
    try:
        slot = int(str(text).strip())
    except ValueError:
        return None, f"the slot input {text!r} is not a time in epoch seconds"
    if slot > now:
        return None, f"the slot {local_time(slot, spec['timezone'])} has not come"
    if current_slot(spec, slot) != slot:
        return None, f"{local_time(slot, spec['timezone'])} is not a time this schedule fires at"
    return slot, None


def run_checks(spec):
    """What a scheduled worker needs on this runner, checked without starting one (K13)."""
    checks = []
    engine = spec["settings"].get("engine") or "claude"
    found = adapter(engine).diagnose(spec["project"])
    report = found["report"]
    # The engine's blockers are its CLI, its version and its login — what the runner's service, not a terminal, has to have; the login gets a row of its own, the runner's commonest problem.
    login = [b for b in found["blockers"] if "logged in" in b or "login" in b]
    rest = [b for b in found["blockers"] if b not in login]
    checks.append({"name": engine, "ok": not rest, "detail": "; ".join(rest) or f"{report.get('path')} {report.get('version')}"})
    if report.get("installed"):
        auth = report.get("auth") or {}
        checks.append({"name": "login", "ok": not login, "detail": f"logged in ({auth.get('authMethod')}, {auth.get('subscriptionType')})" if not login else f"{'; '.join(login)} (authMethod {auth.get('authMethod')}): the runner's service cannot read the login"})
    sb = report.get("sandbox")
    if spec["settings"]["profile"] in ("read-only", "write") and sb is not None:
        ok = sb["supported"] and not sb["missing"]
        checks.append({"name": "sandbox", "ok": ok, "detail": "available" if ok else (f"missing {', '.join(sb['missing'])}" if sb["supported"] else f"not supported on {sb['platform']}")})
    uv = shutil.which("uv")
    checks.append({"name": "uv", "ok": bool(uv), "detail": uv or "uv is not on the runner's PATH"})
    project = spec["project"]
    checks.append({"name": "project", "ok": True, "detail": project + ("" if toplevel(project) else " (not a git checkout)")})
    if spec.get("worktree"):
        ok = bool(head_commit(project))
        checks.append({"name": "worktree", "ok": ok, "detail": "the repository has a commit to start from" if ok else "--worktree needs a commit to start from"})
    return {"kind": "check", "ok": all(c["ok"] for c in checks), "checks": checks}


def _scrub_worker_env():
    """K9: the worker and its supervisor inherit this process's environment, so the job's handles go before the worker starts."""
    for key in list(os.environ):
        if key in JOB_HANDLES or key.startswith("DELEGATE_SCHEDULE_"):
            del os.environ[key]


def _start_worker(spec, prompt, schema_path, registry, group, origin, on_published=None):
    """Start the worker as `start` would, or — given a `group` — as that one-member `batch --worktree` group (K10). `on_published` is called once the run is in the registry (for `start`). Returns (run_id, refusal)."""
    s = spec["settings"]
    if group:
        reply = batch(name=group, task_args=[prompt], tasks_file=None, defaults={"engine": s.get("engine") or "claude", "profile": s["profile"], "model": s["model"], "effort": s["effort"], "schema": schema_path, "timeout": s["timeout"], "cwd": None}, cwd=spec["project"], use_worktrees=True, runs_dir=registry, origin=origin, preflight=False)
        member = (reply.get("runs") or [{}])[0]
        if not member.get("run_id"):
            return None, member.get("error") or "the worktree member did not start"
        return member["run_id"], None
    reply = start(prompt=prompt, cwd=spec["project"], engine=s.get("engine") or "claude", flags={"profile": s["profile"], "model": s["model"], "effort": s["effort"], "schema": schema_path, "cwd": spec["project"]}, label=spec["name"], timeout=s["timeout"], runs_dir=registry, origin=origin, on_published=on_published, preflight=False)
    return reply["run_id"], None


def _follow(run_id, registry, step, stop_event):
    """Print the run's activity as `log` lines while it runs (K1)."""
    printed = 0
    while True:
        try:
            lines = settled_lines(cwd=registry, ref=run_id, runs_dir=registry)
        except Refusal:
            lines = []
        # 성진: every poll re-reads the whole events file; read it incrementally if long runs make the step slow.
        for line in lines[printed:]:
            step.say(line)
        printed = max(printed, len(lines))
        if stop_event.is_set():
            return
        stop_event.wait(FOLLOW_SECONDS)


def scheduled_run(spec, env, write):
    """The Run step. `spec` holds name, project, settings (profile, model, effort, timeout), cron or at, timezone and worktree; `env` is the job's environment; `write` takes stdout text. Returns the exit code: 0 when the worker completed (with structured output for a schema run), a late duplicate was skipped, or a check passed; 1 otherwise."""
    step = Step(write)
    name = spec["name"]
    ctx = job_context(env)
    if ctx is None:
        step.command(annotation("error", name, "__scheduled runs inside a GitHub Actions job; GITHUB_RUN_ID is not set"))
        return 1
    project = spec["project"]
    if not Path(project).is_dir():
        # K23: nothing is written, since the registry would be created at a path that no longer exists.
        step.command(annotation("error", f"{name}: project missing", f"{project} is not a directory on this runner; if the project moved, run `schedule --name {shlex.quote(name)} --project <new path>` again"))
        return 1
    registry = resolve_runs_dir(project)
    job = ctx["job"]
    mode = env.get("DELEGATE_SCHEDULE_MODE") or "run"
    if mode == "check":
        outcome = run_checks(spec)
        record_job(registry, name, job, {"trigger": "manual", "mode": "check", "url": ctx["url"], "outcome": outcome})
        for c in outcome["checks"]:
            step.say(f"check {c['name']} {'ok' if c['ok'] else 'FAILED'} {c['detail']}")
        if not outcome["ok"]:
            step.command(annotation("error", f"{name}: check failed", "; ".join(c["detail"] for c in outcome["checks"] if not c["ok"])))
        return 0 if outcome["ok"] else 1

    trigger = trigger_of(ctx, env.get("DELEGATE_SCHEDULE_SOURCE") or "", env.get("DELEGATE_SCHEDULE_SLOT") or "")
    s = spec["settings"]
    group = worktree_group(name, ctx["run_id"], ctx["attempt"]) if spec.get("worktree") else None
    entry = {"trigger": trigger, "mode": "run", "profile": s["profile"], "definition": spec.get("definition"), "url": ctx["url"], "attempt": ctx["attempt"], "group": group, "tree_before": None if spec.get("worktree") else status_snapshot(project), "outcome": None}
    slot = retry_of = None
    if trigger == "recovery":
        now = clock()
        slot, why = asked_slot(spec, env.get("DELEGATE_SCHEDULE_SLOT"), now)
        if why:
            record_job(registry, name, job, {**entry, "outcome": {"kind": "refused", "error": why}})
            step.command(annotation("error", f"{name}: not started", why))
            return 1
        # A missed slot is filled once, whatever ran after it and however long ago its time was; a slot already filled is decided as any trigger for it.
        claim = claim_slot(registry, name, slot, job, entry, exact=True)
        if claim["decision"] == "skip":
            prev = claim["previous"] or {}
            step.say(f"skipped: slot {local_time(slot, spec['timezone'])} already ran" + (f" in {prev.get('url')} ({prev.get('state')})" if prev.get("url") else ""))
            return 0
        if claim["decision"] == "retry":
            retry_of = claim["previous"]
            step.say(f"retrying slot {local_time(slot, spec['timezone'])}: the run that took it ended {retry_of.get('state')}")
    elif trigger in AUTOMATIC:
        now = clock()
        if spec.get("at") and now - at_instant(spec) > ONE_OFF_EXPIRY:
            record_job(registry, name, job, {**entry, "outcome": {"kind": "skipped", "reason": f"the one-off time {spec['at']} passed more than 26 hours ago"}})
            step.say(f"skipped: the one-off time {spec['at']} passed more than 26 hours ago")
            return 0
        slot = current_slot(spec, now)
        if slot is None:
            record_job(registry, name, job, {**entry, "outcome": {"kind": "skipped", "early": True, "reason": f"the one-off time {spec['at']} has not come"}})
            step.say(f"skipped: the one-off time {spec['at']} has not come; only a manual run starts it early")
            return 0
        claim = claim_slot(registry, name, slot, job, entry)
        if claim["decision"] == "skip":
            prev = claim["previous"] or {}
            step.say(f"skipped: slot {local_time(slot, spec['timezone'])} already ran" + (f" in {prev.get('url')} ({prev.get('state')})" if prev.get("url") else ""))
            return 0
        if claim["decision"] == "retry":
            retry_of = claim["previous"]
            step.say(f"retrying slot {local_time(slot, spec['timezone'])}: the run that took it ended {retry_of.get('state')}")
    else:
        record_job(registry, name, job, entry)

    if spec.get("worktree") and not head_commit(project):
        error = "--worktree needs a commit to start from, and this repository has none; the worker was not started"
        update_job(registry, name, job, {"outcome": {"kind": "refused", "error": error}})
        step.command(annotation("error", f"{name}: not started", error))
        return 1
    prompt = env.get("DELEGATE_SCHEDULE_PROMPT") or ""
    schema_path = None
    if env.get("DELEGATE_SCHEDULE_SCHEMA"):
        # K22: the workflow carries the schema's text; the worker gets it as a file of this job's.
        schema_path = str(Path(schedule_dir(registry, name)) / f"schema-{job}.json")
        Path(schema_path).parent.mkdir(parents=True, exist_ok=True)
        Path(schema_path).write_text(env["DELEGATE_SCHEDULE_SCHEMA"], encoding="utf-8")
    origin = {"kind": "schedule", "name": name, "trigger": trigger, "slot": slot, "job": job, "url": ctx["url"], "repository": ctx["repository"], "when": local_time(slot if slot is not None else clock(), spec["timezone"]), "project": project, "pull": spec.get("pull") is not False}
    if retry_of:
        origin["retry_of"] = retry_of["job"]
    _scrub_worker_env()
    # K27, K30: from the snapshot the run's changes are measured against until the run is in the registry — from then on it holds the checkout's updates off itself — nothing may update the checkout, or that would count as the run's change. The lock goes as soon as the run is published, not after the init wait.
    held = contextlib.ExitStack()
    try:
        if not spec.get("worktree"):
            held.enter_context(sync_lock(registry))
            update_job(registry, name, job, {"tree_before": status_snapshot(project)})
        def published(meta):
            # The run now holds updates off itself, until its Finish marks its changes read (K30).
            if not spec.get("worktree"):
                with contextlib.suppress(Refusal, OSError):
                    update_meta(str(Path(registry) / meta["run_id"]), lambda m: m.setdefault("changes_read_at", None))
            held.close()

        run_id, refused = _start_worker(spec, prompt, schema_path, registry, group, origin, on_published=published)
    except Refusal as e:
        run_id, refused = None, e.message
    finally:
        held.close()
    if refused:
        update_job(registry, name, job, {"outcome": {"kind": "refused", "error": refused}})
        step.command(annotation("error", f"{name}: not started", refused))
        return 1
    # Also by id, so Finish finds the run even when its meta.json no longer parses.
    update_job(registry, name, job, {"run_id": run_id})
    step.say(f"run {run_id} · {s.get('engine') or 'claude'} · {s['profile']} · {s['model']}/{s['effort']} · {trigger}" + (f" · slot {local_time(slot, spec['timezone'])}" if slot is not None else ""))
    done = threading.Event()
    follower = threading.Thread(target=_follow, args=(run_id, registry, step, done), daemon=True)
    follower.start()
    seen = []

    def on_attention(items):
        for a in items:
            seen.append({"reason": a["reason"], "at": local_time(clock(), spec["timezone"])})
            step.command(annotation("warning", f"{name}: {a['reason']}", a.get("note") or a["reason"]))
        update_job(registry, name, job, {"attention": seen})

    final = wait_ended(cwd=registry, ref=run_id, runs_dir=registry, on_attention=on_attention)
    done.set()
    follower.join(timeout=FOLLOW_SECONDS * 3)
    state = final["meta"].get("state")
    ok = state == "completed" and (not final["schema"] or final["json"] is not None)
    step.say(f"run={run_id} state={state}" + ("" if ok or state != "completed" else " (no structured output)"))
    return 0 if ok else 1


def _changes(spec, rec, meta, edited):
    """What changed during the run (K27): against the snapshot the Run step took before the worker, for a run in the project checkout; against its base commit, for a run in its own worktree. Commits the worker made count as changes and say so. `edited` (the edit tools' files) stands in where git cannot tell."""
    wt = meta.get("worktree")
    if wt and wt.get("path"):
        snap = status_snapshot(wt["path"])
        if snap is None:
            return {"where": "the worktree", "kind": "worktree", "files": []}
        paths = sorted(snap["entries"])
        counts = numstat(wt["path"], [p for p in paths if snap["entries"][p]["status"] != "??"])
        files = _merged([{**f, "committed": True} for f in committed_files(snap["root"], wt.get("base"), snap["head"])], [_file(snap["root"], p, snap["entries"][p], counts) for p in paths])
        return {"where": "the worktree", "kind": "worktree", "files": files}
    project = spec["project"]
    before = (rec or {}).get("tree_before")
    after = status_snapshot(project)
    if after is None or before is None:
        return {"where": "the project directory", "not_git": list(edited), "files": []}
    paths = sorted(p for p in set(before["entries"]) | set(after["entries"]) if before["entries"].get(p) != after["entries"].get(p))
    fresh = [p for p in paths if p in after["entries"] and p not in before["entries"] and after["entries"][p]["status"] != "??"]
    counts = numstat(project, fresh)
    out = {"where": "the project checkout", "files": []}
    landed = []
    if (before.get("head"), before.get("branch")) != (after.get("head"), after.get("branch")) and before.get("head") and after.get("head"):
        landed = [{**f, "committed": True} for f in committed_files(after["root"], before["head"], after["head"])]
        out["head_moved"] = {"from": f"{before.get('branch') or 'detached'} @ {before['head'][:7]}", "to": f"{after.get('branch') or 'detached'} @ {after['head'][:7]}", "commits": commits_beyond(after["root"], after["head"], before["head"]) or 0}
    working = []
    for p in paths:
        if p not in after["entries"]:
            working.append({"status": "  ", "path": p, "note": "no longer changed: reverted or committed"})
        elif p in before["entries"]:
            working.append({"status": after["entries"][p]["status"], "path": p, "note": "already modified before the run"})
        else:
            working.append(_file(after["root"], p, after["entries"][p], counts))
    out["files"] = _merged(landed, working)
    return out


def _merged(committed, working):
    """One entry per path: a committed file that also shows in the working tree is committed and changed again, and a path the commit explains is not listed twice."""
    by_path = {f["path"]: f for f in committed}
    for f in working:
        c = by_path.get(f["path"])
        if c is None:
            by_path[f["path"]] = f
        elif f.get("note") != "no longer changed: reverted or committed":
            c["note"] = "committed, then deleted" if "D" in f["status"] else "committed, then changed again"
            c["uncommitted_too"] = True
    return [by_path[p] for p in sorted(by_path)]


def _file(root, rel, entry, counts):
    f = {"status": entry["status"], "path": rel}
    if entry["status"] == "??":
        try:
            f["new_lines"] = len((Path(root) / rel).read_bytes().splitlines())
        except OSError:
            pass
    elif counts.get(rel):
        f["lines"] = counts[rel]
    return f


def _clean_group(group, job, registry, project, warnings, landed=None):
    """K10: clean the worktree group this job created — and only one whose manifest names this job, never a name another batch holds — removing a worktree that holds nothing, or whose work `landed` says is on GitHub (K30), and releasing the name. A group that stays is marked finished, so a later job's Finish may look at it again (`_sweep`). Returns what the summary says of it; failures go to `warnings`."""
    if not group or group not in {g["name"] for g in list_groups(registry)}:
        return {}
    try:
        manifest = group_view(registry, group)
    except Refusal:
        return {}
    if (manifest.get("origin") or {}).get("job") != job:
        return {}
    try:
        result = clean(name=group, cwd=project, runs_dir=registry, landed=landed)
    except Exception as e:  # K20: a cleanup that fails is a warning; the summary must still be written.
        warnings.append(f"The worktree was not cleaned up ({getattr(e, 'message', None) or e}); `{invocation('clean', '--group', group, '--project', project)}` does it.")
        return {}
    finally:
        # This job's own group only: from now on a later job's Finish may look at it (`_sweep`).
        with contextlib.suppress(Refusal, OSError):
            update_group(registry, group, manifest.get("generation"), {"finished_at": now()})
    if result["kept"]:
        k = result["kept"][0]
        return {"kept": {"path": k["path"], "branch": k["branch"], "clean": invocation("clean", "--group", group, "--project", project), "open_pr": k.get("open_pr"), "landed": k.get("landed"), "why": k.get("why")}}
    went = next((r for r in result["removed"] if r.get("landed")), None)
    if went:
        return {"landed": went}
    kept_branch = next((r["branch_kept"] for r in result["removed"] if r.get("branch_kept")), None)
    if kept_branch:
        return {"branch_kept": {"branch": kept_branch}}
    return {"removed": True} if result["removed"] else {}


def _tree(spec, rec, meta):
    wt = (meta or {}).get("worktree")
    if wt and wt.get("path"):
        return {"kind": "worktree", "path": wt["path"], "branch": wt.get("branch"), "base": wt.get("base")}
    before = (rec or {}).get("tree_before")
    if before is None:
        return {"kind": "none", "path": spec["project"]}
    return {"kind": "checkout", "branch": before.get("branch"), "head": before.get("head"), "uncommitted_before": len(before.get("entries") or {})}


NOT_COMPLETED = ("failed", "timed_out", "orphaned")


def _end_one_off(spec, ctx, registry, rec, state):
    """K8: a one-off's automatic job turns its schedule off once its time will not be tried again — the run completed, it was stopped, a usage limit ended it (never retried), it was a writer (never retried), it was itself the retry, or the time is long past — and only while the definition this job was started from is still the one this Mac recorded (a schedule called again or removed meanwhile is not touched). Returns (notes, warnings) for the summary."""
    if not spec.get("at") or rec.get("trigger") not in AUTOMATIC:
        return [], []
    outcome = (rec.get("outcome") or {}).get("kind")
    if (rec.get("outcome") or {}).get("early"):
        return [], []
    if rec.get("trigger") == "recovery" and rec.get("slot") is None:
        # A recovery refused for the slot it named claimed no slot, so the one-off's own time is still to come.
        return [], []
    not_completed = state in NOT_COMPLETED or outcome == "refused"
    if not_completed and rec.get("profile") == "read-only" and not rec.get("retry_of"):
        return ["This one-off's run did not complete; the next automatic trigger for its time retries it once, and that run turns the schedule off."], []
    repo = ctx.get("repository")
    name = spec["name"]
    notes, warnings = [], []
    ended = {"disabled": False, "dispatcher": None}
    # The definition the job was started from, as its Run step recorded it: a Finish run again later may be given the flags of a schedule set since.
    mine = rec.get("definition") or spec.get("definition")
    with schedule_lock(registry, name):
        current = read_definition(registry, name)
        if current != mine:
            return ["This one-off was scheduled again or removed while it ran, so this run left the newer definition alone."], []
        # The file this job's step names: a step written before files took the name as given names claude-NAME.yml, and so is one that names none.
        file = spec.get("workflow") or legacy_workflow_file(name)
        try:
            set_workflow_enabled(repo, file, False)
            ended["disabled"] = True
            notes.append(f"This one-off is done: its workflow is disabled ({code_span(f'gh workflow enable {shlex.quote(file)} --repo {repo}')} and a manual run would run it again; a disabled workflow cannot be re-run).")
        except GitHubError as e:
            warnings.append(f"The one-off's workflow could not be disabled ({e.message}); {code_span(f'gh workflow disable {shlex.quote(file)} --repo {repo}')} does it.")
        held = dispatcher_holder(repo, name)
        if held:
            ended["dispatcher"] = "left"
            warnings.append(f"The one-off's dispatcher was not removed: its plist path on this Mac holds {holder_words(held, code_span)}, which was left alone.")
        else:
            gone = remove_agent(dispatcher_label(repo, name))
            ended["dispatcher"] = gone["status"]
            if gone["status"] == "failed":
                warnings.append(f"The one-off's dispatcher could not be removed ({gone.get('error')}); {code_span(f'unschedule --name {shlex.quote(name)}')} removes it.")
    if ctx.get("job"):
        # What is left of the one-off's end is what a Finish run again, `doctor` and a move from the `claude` skill read. Recorded after the schedule's lock is let go: update_job takes it itself.
        update_job(registry, name, ctx["job"], {"one_off_end": {**ended, "done": ended["disabled"] and ended["dispatcher"] in ("removed", "absent"), "definition": mine, "at": now()}})
    return notes, warnings


def _render_kept(snap, context, warnings, local):
    """Render the summary and keep a copy at `local` (K17); a copy that cannot be written becomes one more warning in what GitHub gets."""
    text = render_summary(snap, {**context, "warnings": warnings})
    if local is None:
        return text
    try:
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_text(text, encoding="utf-8")
    except OSError as e:
        text = render_summary(snap, {**context, "warnings": warnings + [f"The local copy of this summary could not be written: {e}"]})
    return text


def _post(step, name, ctx, text):
    """Write the step summary; 1 when it cannot be written (K20)."""
    path = ctx.get("summary_path")
    try:
        if not path:
            raise OSError("the runner gave no GITHUB_STEP_SUMMARY")
        write_summary(path, text)
    except OSError as e:
        step.command(annotation("error", f"{name}: summary not written", str(e)))
        return 1
    return 0


def _bring_local(spec, ctx, registry, run_id, unconfirmed):
    """K30: the project's checkout brought up to what GitHub's default branch now holds — what the worker merged there included — unless the schedule says `--no-pull`, or the worker works in that checkout and could not be confirmed stopped. Never raises; the reply is `git.sync_default`'s, or `{status: off}`."""
    if spec.get("pull") is False:
        return {"status": "off"}
    if unconfirmed and not spec.get("worktree"):
        return left_alone(spec["project"], "the worker works in this checkout and could not be confirmed stopped")
    try:
        return sync_local(spec["project"], registry, expect_repo=ctx.get("repository"), skip_run=run_id)
    except Exception as e:  # K20: a stage that fails becomes a warning; the summary must still be written.
        return left_alone(spec["project"], f"the update could not be tried: {getattr(e, 'message', None) or repr(e)}") | {"status": "failed"}


def _landed(spec, ctx, registry, local):
    """K30: the question `clean` asks of a worktree's last commit — is it on GitHub? — answered from GitHub's default branch as this job fetched it (fetched now if the update did not, as under --no-pull) and from the pull requests whose head it is. None when GitHub's default branch is not known here."""
    branch, remote = local.get("branch"), local.get("to")
    if not remote:
        with contextlib.suppress(Exception):
            fetched = sync_local(spec["project"], registry, expect_repo=ctx.get("repository"), fetch_only=True)
            branch, remote = fetched.get("branch"), fetched.get("to")
    repo, project = ctx.get("repository"), spec["project"]
    if not branch or not repo:
        return None

    def landed(head):
        if remote and contains(project, remote, head, env=scrub_job_env(os.environ)):
            return {"landed": True, "label": f"on {branch}", "default": branch}
        found = pull_evidence(repo, head, branch)
        if found["kind"] == "merged":
            return {"landed": True, "label": f"#{found['number']}", "pr": found["number"], "default": branch}
        if found["kind"] == "open":
            return {"landed": False, "open_pr": found["number"]}
        return None

    return landed


def _sweep(registry, project, repo, landed, own):
    """K30: the worktree groups earlier jobs of this repository's schedules left — only those whose own Finish marked them finished, never a group being started or still live (`clean` refuses those) or one a person made — looked at again, so a pull request merged after its run ended still gets its worktree cleaned. Returns notes for the summary."""
    if not landed:
        return []
    notes, started = [], time.monotonic()
    waiting = [g for g in list_groups(registry) if g.get("name") != own and g.get("finished_at") and (g.get("origin") or {}).get("kind") == "schedule" and ((g.get("origin") or {}).get("repository") or "").lower() == (repo or "").lower()]
    for n, g in enumerate(waiting):
        if n >= SWEEP_GROUPS or time.monotonic() - started > SWEEP_SECONDS:
            notes.append(f"{len(waiting) - n} earlier worktree group(s) were left for a later run's end to look at.")
            break
        try:
            # Not one that changed after its own Finish: someone may be working in it, as its summary suggested (resuming the session there).
            result = clean(name=g["name"], cwd=project, runs_dir=registry, landed=landed, untouched_since=g.get("finished_at"))
            notes += [f"An earlier run's worktree {code_span(r['path'])} went: {went_text(r)}." for r in result["removed"] if r.get("landed")]
            notes += [f"An earlier run's worktree {code_span(k['path'])} is kept: its work is on GitHub ({k['landed']}), but {k['why']}." for k in result["kept"] if k.get("landed") and k.get("why")]
        except Refusal:  # live or starting: not this time
            continue
        except Exception as e:  # K20: said, not raised
            notes.append(f"The earlier worktree group {code_span(g['name'])} could not be looked at: {e!r}.")
    return notes


def _repository_of(registry, rec):
    """The repository a job ran in, as its run recorded it, or as its run page's URL names it."""
    if rec.get("run_id"):
        with contextlib.suppress(Refusal, OSError):
            repo = (read_meta(str(Path(registry) / rec["run_id"])).get("origin") or {}).get("repository")
            if repo:
                return repo
    parts = (rec.get("url") or "").split("/")
    return "/".join(parts[3:5]) if len(parts) > 4 and parts[2] == "github.com" else None


def _end_again(step, spec, ctx, registry, rec):
    """Finish run again for a job that already finished: only a one-off's end (disabling its workflow, removing its dispatcher) is tried again — no worker is stopped or started and no summary rewritten — so it can be repeated until the end holds; once it has held, nothing is done again. Exits 1 while something of the end is still undone."""
    end = rec.get("one_off_end") or {}
    if end.get("done"):
        # A workflow enabled again since, as the summary says to run it by hand, is someone's choice, not an end left undone.
        step.say(json.dumps({"job": ctx["job"], "finished_before": True, "one_off_end": end}, ensure_ascii=False))
        return 0
    state = None
    if rec.get("run_id"):
        with contextlib.suppress(Refusal, OSError):
            state = reap(str(Path(registry) / rec["run_id"])).get("state")
    notes, warnings = _end_one_off(spec, ctx, registry, rec, state)
    for line in notes + warnings:
        step.say(line)
    end = (read_job(registry, spec["name"], ctx["job"]) or {}).get("one_off_end")
    step.say(json.dumps({"job": ctx["job"], "finished_before": True, "one_off_end": end}, ensure_ascii=False))
    return 1 if warnings else 0


def scheduled_finish(spec, env, write, job=None):
    """The Finish step, run after any end of the job (K20): stop a worker still alive, read what changed, clean up a worktree that holds nothing, keep the summary beside the run and post it to the job. Returns 1 only when the worker could not be confirmed stopped or the step summary could not be written; every other failure is a warning in the summary.

    Run again for a job that already finished — in its job, or on this Mac with `job` naming it — it only tries a one-off's end again (`_end_again`). On this Mac, a `job` with no record here or whose Finish has not run is refused before anything is touched: its worker may still be running in its job."""
    name, project = spec["name"], spec["project"]
    if job and job_context(env) is None:
        rec = (read_job(resolve_runs_dir(project), name, job) if Path(project).is_dir() else None) or {}
        if not rec:
            raise Refusal(f"schedule {name} has no job {job} recorded in {project}", instead="the job is `<run id>-<attempt>` of a run of this schedule's workflow")
        if not rec.get("finished_at"):
            raise Refusal(f"job {job} of schedule {name} has not finished: its Finish step has not run, and its worker may still be running in its job", instead="let the job end; its own Finish does the rest")
    step = Step(write)
    ctx = job_context(env) or ({"job": job} if job else {})
    job = ctx.get("job")
    base = {"name": name, "timezone": spec["timezone"], "prompt": env.get("DELEGATE_SCHEDULE_PROMPT")}
    if not Path(project).is_dir():
        # K23: no registry to keep anything in; GitHub gets the summary.
        text = render_summary(None, {**base, "at": clock(), "outcome": {"kind": "missing", "title": "project missing", "error": f"{project} is not a directory on this runner.", "instead": f"If the project moved, run {code_span(f'schedule --name {shlex.quote(name)} --project <new path>')} again."}})
        return _post(step, name, ctx, text)
    registry = resolve_runs_dir(project)
    rec = (read_job(registry, name, job) if job else None) or {}
    if rec.get("finished_at"):
        if not ctx.get("repository"):
            ctx["repository"] = _repository_of(registry, rec)
        return _end_again(step, spec, ctx, registry, rec)

    def finished(code):
        # From here a Finish run again for this job only completes a one-off's end.
        if job:
            with contextlib.suppress(Refusal, OSError):
                update_job(registry, name, job, {"finished_at": now()})
        return code
    trigger = {"kind": rec.get("trigger") or (trigger_of(ctx, env.get("DELEGATE_SCHEDULE_SOURCE") or "") if ctx else "manual"), "attempt": ctx.get("attempt")}
    if rec.get("retry_of"):
        earlier = find_by_origin_job(registry, rec["retry_of"])
        trigger["retry_of_url"] = ((reap(earlier[-1]).get("origin") or {}).get("url") if earlier else None) or (read_job(registry, name, rec["retry_of"]) or {}).get("url")
    base.update({"trigger": trigger, "slot": rec.get("slot")})
    local = str(Path(schedule_dir(registry, name)) / f"summary-{job}.md")
    outcome = rec.get("outcome") or {}
    if (env.get("DELEGATE_SCHEDULE_MODE") or "run") == "check" or outcome.get("kind") in ("check", "skipped", "refused"):
        if not outcome:
            outcome = {"kind": "check", "ok": False, "checks": [{"name": "check", "ok": False, "detail": "the Run step recorded no result; read its log"}]}
        warnings = []
        if outcome["kind"] == "refused":
            # A worktree group claimed before the start was refused holds nothing; it is released, as after any run (K10).
            _clean_group(rec.get("group"), job, registry, project, warnings)
        notes, ended = _end_one_off(spec, ctx, registry, rec, None) if outcome["kind"] != "check" else ([], [])
        warnings += ended
        text = _render_kept(None, {**base, "at": rec.get("at") or clock(), "outcome": outcome, "notes": notes, "prompt": None if outcome["kind"] == "check" else base["prompt"]}, warnings, local)
        if outcome["kind"] == "refused" or (outcome["kind"] == "check" and not outcome.get("ok")):
            step.command(annotation("error", f"{name} failed", outcome.get("error") or "the check failed"))
        return finished(_post(step, name, ctx, text))
    runs = find_by_origin_job(registry, job) if job else []
    if not runs and rec.get("run_id") and (Path(registry) / rec["run_id"]).is_dir():
        runs = [str(Path(registry) / rec["run_id"])]
    if not runs:
        text = _render_kept(None, {**base, "at": clock(), "outcome": {"kind": "none", "title": "no worker", "error": "The Run step did not start a worker.", "instead": "Its step log above says why."}}, [], local)
        step.command(annotation("error", f"{name} failed", "the Run step did not start a worker"))
        return finished(_post(step, name, ctx, text))

    run_dir = runs[-1]
    run_id = Path(run_dir).name
    warnings, unconfirmed, stopped_by_finish = [], None, False
    try:
        meta = reap(run_dir)
    except (Refusal, OSError) as e:
        meta, unconfirmed = None, f"its meta.json cannot be read ({getattr(e, 'message', None) or e}), so whether the worker still runs is unknown"
    if meta is not None and is_live(meta):
        # The Run step was cancelled or timed out with the worker alive; the runner would kill it as the job ends and leave the run orphaned (probe 5).
        stopped_by_finish = True
        try:
            stop(refs=[run_id], cwd=project, runs_dir=registry)
        except Refusal as e:
            warnings.append(f"Stopping the worker failed: {e.message}")
        try:
            meta = reap(run_dir)
        except (Refusal, OSError) as e:
            meta, unconfirmed = None, f"its meta.json cannot be read after stop ({getattr(e, 'message', None) or e}), so whether the worker still runs is unknown"
        if meta is not None and (is_live(meta) or worker_alive(meta)):
            unconfirmed = f"run {run_id} is still {meta.get('state')} after stop"
    snap = None
    if meta is not None:
        try:
            snap = snapshot(cwd=registry, ref=run_id, runs_dir=registry)
        except Refusal as e:
            unconfirmed = unconfirmed or f"the run cannot be read: {e.message}"
    changes = None
    if meta is not None:
        try:
            changes = _changes(spec, rec, meta, (snap or {}).get("header", {}).get("files_edited") or [])
        except OSError as e:
            warnings.append(f"What changed could not be read: {e}")
    if changes is not None:
        # From here what comes into the checkout is not this run's; another job's update need not wait for this one (K30).
        with contextlib.suppress(Refusal, OSError):
            update_meta(run_dir, lambda m: m.__setitem__("changes_read_at", now()))
    # After the changes are read, so what comes in from GitHub is not counted as the run's (K27).
    local = _bring_local(spec, ctx, registry, run_id, unconfirmed)
    landed = None
    with contextlib.suppress(Exception):
        landed = _landed(spec, ctx, registry, local)
    if changes is not None:
        changes.update(_clean_group(rec.get("group"), job, registry, project, warnings, landed))
    notes, ended = _end_one_off(spec, ctx, registry, rec, (meta or {}).get("state"))
    warnings += ended
    with contextlib.suppress(Exception):
        notes += _sweep(registry, project, ctx.get("repository"), landed, rec.get("group"))
    if unconfirmed:
        warnings.insert(0, f"**The worker could not be confirmed stopped:** {unconfirmed}. Check this Mac, and `{invocation('stop', '--run', run_id, '--project', project)}`.")
    rel = os.path.relpath(run_dir, project)
    context = {**base, "notes": notes, "tree": _tree(spec, rec, meta), "changes": changes, "attention": rec.get("attention") or [], "local": local, "stopped_by_finish": stopped_by_finish, "continue": {"cli": entry_path(), "project": project, "run_dir": (run_dir if rel.startswith("..") else rel) + "/"}, "log_call": invocation("log", "--run", run_id, "--project", project)}
    if snap is None:
        context.update({"at": clock(), "outcome": {"kind": "none", "title": "unreadable run", "error": f"Run {run_id} cannot be read."}})
    text = _render_kept(snap, context, warnings, str(Path(run_dir) / "summary.md"))
    code = _post(step, name, ctx, text)
    state = (meta or {}).get("state")
    if ctx.get("output_path"):
        try:
            write_output(ctx["output_path"], {"run_id": run_id, "state": state})
        except OSError:
            pass
    if local.get("status") in ("not_updated", "failed"):
        step.command(annotation("warning", f"{name}: checkout not updated", local.get("reason") or local["status"]))
    if (state in RED and not stopped_by_finish) or (snap is not None and state == "completed" and snap["schema"] and snap["json"] is None):
        step.command(annotation("error", f"{name} failed", (meta or {}).get("error") or state))
    if unconfirmed:
        step.command(annotation("error", f"{name}: worker not confirmed stopped", unconfirmed))
        code = 1
    return finished(code)
