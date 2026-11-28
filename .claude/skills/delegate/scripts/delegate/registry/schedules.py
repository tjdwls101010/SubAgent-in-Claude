"""Schedule state under <registry>/.schedules/<name>/: state.json, which holds the slot automatic triggers last filled (K4), who filled each recent slot and whether it was retried, and each job's hand-over from its Run step to its Finish step (K19) — plus the files a scheduled run keeps beside it. Every write happens under the schedule's lock.

A job is one attempt of one GitHub Actions run, keyed `<run id>-<attempt>`. The state file is a cache of what the runs' own `origin` records: when it is lost, the slot claims and retries are rebuilt from them (a refusal that left no run cannot be).
"""

from __future__ import annotations

from pathlib import Path

from delegate.util import now

from .locks import locked, read_json, write_json
from .runs import TERMINAL, ensure_runs_dir, is_live, iter_runs, reap

KEEP = 50
NOT_COMPLETED = ("failed", "timed_out", "orphaned")
# The registry the `claude` skill this one replaces kept beside this one, in the same project, with schedule state of the same format.
LEGACY_RUNS_DIR = ".claude-runs"


def schedule_dir(registry, name):
    """Where schedule `name` keeps its state and files."""
    return str(Path(registry) / ".schedules" / name)


def _state_path(registry, name):
    return Path(schedule_dir(registry, name)) / "state.json"


def _lock(registry, name):
    return locked(Path(registry) / ".locks" / f"schedule-{name}.lock")


def sync_lock(registry):
    """The lock under which this repository's checkout is brought up to its origin (K30), so two jobs ending together, or a job and `schedule`, do not fetch and fast-forward one checkout at once."""
    ensure_runs_dir(registry)
    return locked(Path(registry) / ".locks" / "sync.lock")


def schedule_state(registry, name):
    """The schedule's state as stored, or None when there is none."""
    return read_json(_state_path(registry, name))


def _schedule_runs(registry, name):
    """(run_dir, meta) of every run this schedule started, oldest first."""
    return [(d, m) for d, m in iter_runs(registry) if (m.get("origin") or {}).get("kind") == "schedule" and m["origin"].get("name") == name]


def _rebuild(registry, name):
    """The state as the runs' origins record it: which job filled each slot, which retried it, and the latest slot."""
    state = {"name": name, "last_slot": None, "slots": {}, "jobs": {}}
    for _, m in _schedule_runs(registry, name):
        o = m["origin"]
        if o.get("slot") is None:
            continue
        claim = state["slots"].setdefault(str(o["slot"]), {"job": None, "retried": None})
        if o.get("retry_of"):
            claim["retried"] = o["job"]
            claim["job"] = claim["job"] or o["retry_of"]
        else:
            claim["job"] = o["job"]
        state["last_slot"] = max(state["last_slot"] or 0, o["slot"])
    return state


def _load(registry, name):
    return schedule_state(registry, name) or _rebuild(registry, name)


def _prune(state):
    for key in ("slots", "jobs"):
        items = state.get(key) or {}
        if len(items) > KEEP:
            order = sorted(items, key=lambda k: (items[k].get("at") or 0, k)) if key == "jobs" else sorted((k for k in items if not items[k].get("exact")), key=int)
            for k in order[: len(items) - KEEP]:
                del items[k]


def _save(registry, name, state):
    ensure_runs_dir(registry)
    Path(schedule_dir(registry, name)).mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now()
    _prune(state)
    write_json(_state_path(registry, name), state)


def _job_run(registry, name, job):
    """The newest run job `job` started, reaped, or None."""
    found = [d for d, m in _schedule_runs(registry, name) if m["origin"].get("job") == job]
    return reap(found[-1]) if found else None


def _completed_or_live(registry, name, state, job):
    """Whether the job that filled a slot got somewhere that a retry must not repeat: its run completed, was stopped, was ended by a usage limit (`quota_exhausted`: such a slot is not tried again), or is still live. A refusal before any run, and a run that failed, timed out or was orphaned, did not."""
    meta = _job_run(registry, name, job)
    if meta is None:
        rec = state["jobs"].get(job) or {}
        if rec.get("legacy_run_state"):
            # A job of the `claude` skill, whose run is in that skill's registry: how it ended was recorded when its state was taken over.
            return rec["legacy_run_state"] not in NOT_COMPLETED
        return (rec.get("outcome") or {}).get("kind") != "refused"
    if is_live(meta) or meta.get("state") not in TERMINAL:
        return True
    return meta.get("state") not in NOT_COMPLETED


def _profile_of(registry, name, state, job):
    """The profile job `job` ran under: a slot is retried only when the job that failed it was read-only, whatever the schedule says now."""
    rec = state["jobs"].get(job) or {}
    if rec.get("profile"):
        return rec["profile"]
    meta = _job_run(registry, name, job)
    return ((meta or {}).get("settings") or {}).get("profile")


def _previous(registry, name, state, job):
    meta = _job_run(registry, name, job) if job else None
    rec = state["jobs"].get(job) or {}
    return {"job": job, "run_id": meta["run_id"] if meta else None, "state": meta.get("state") if meta else (rec.get("outcome") or {}).get("kind"), "url": ((meta or {}).get("origin") or {}).get("url") or rec.get("url")}


def slot_unfilled(registry, name, slot):
    """Whether `slot` is one no job has filled: absent from the state, or recorded with no job (a slot a move from the `claude` skill marked for recovery)."""
    with _lock(registry, name):
        claim = _load(registry, name)["slots"].get(str(slot))
        return not (claim or {}).get("job")


def _legacy_claim(state, name, slot, exact):
    """For a schedule taken over from the `claude` skill: what its registry says of `slot` when this one holds no job of its own for it — a job its command queued before the takeover may have filled it, or retried it, since; a run of it may have ended since; and a slot its record pruned may still have run (its runs' origins say). The claim found, its jobs as their runs now stand and the old latest slot are merged into `state`; returns the claim, or None. The old registry is only read."""
    legacy = state["legacy_registry"]
    if not Path(legacy).is_dir():  # removed or moved since the takeover: nothing to read, and taking its lock would make the directory again
        return None
    with locked(Path(legacy) / ".locks" / f"schedule-{name}.lock"):
        old = read_json(Path(legacy) / ".schedules" / name / "state.json") or {}
    if old.get("last_slot") is not None:
        state["last_slot"] = max(state["last_slot"] or old["last_slot"], old["last_slot"])
    mine = state["slots"].get(str(slot)) or {}
    if mine.get("job") and not (state["jobs"].get(mine["job"]) or {}).get("legacy"):
        return None
    claim = (old.get("slots") or {}).get(str(slot))
    if exact and not (claim or {}).get("job"):
        claim = _rebuild(legacy, name)["slots"].get(str(slot)) or claim
    if not (claim or {}).get("job"):
        claim = mine
    if not claim.get("job"):
        return None
    retried = mine.get("retried") or claim.get("retried")
    for job in (claim["job"], retried):
        if job and (state["jobs"].get(job) or {"legacy": True}).get("legacy"):
            rec = (old.get("jobs") or {}).get(job) or {}
            run = next((m for _, m in reversed(_schedule_runs(legacy, name)) if m["origin"].get("job") == job), {})
            ended, profile = run.get("state"), (run.get("settings") or {}).get("profile")
            # A job its record pruned is known only from its run: the profile it ran under decides whether its slot is retried.
            state["jobs"][job] = {**(state["jobs"].get(job) or {}), **({"profile": profile} if profile else {}), **rec, "legacy": True, **({"legacy_run_state": ended} if ended else {})}
    claim = {"job": claim["job"], "retried": retried, **({"exact": True} if exact or mine.get("exact") else {})}
    state["slots"][str(slot)] = claim
    return claim


def claim_slot(registry, name, slot, job, entry, exact=False):
    """Decide, under the schedule's lock, what an automatic trigger for `slot` does (K4), and record job `job` with `entry` (K19 ①). Returns `decision` — `first` (the slot was free and is now this job's), `retry` (the read-only job that filled it did not complete and has not been retried, and this job is read-only too: it is that slot's one retry) or `skip` (it is filled, or a later slot already ran) — and `previous`, the job that filled it with its run id, state and run URL. `entry` must carry `profile`; a skipped job is recorded with outcome `skipped`.

    `exact` is a recovery of that very slot, named by the dispatch: it is decided by that slot's own record alone, so a slot missed while a later one ran can still be filled — once, as any slot."""
    with _lock(registry, name):
        state = _load(registry, name)
        claim = state["slots"].get(str(slot))
        if exact and not (claim or {}).get("job"):
            # A slot old enough to have been pruned from the record may still have run: the runs' own origins say.
            claim = _rebuild(registry, name)["slots"].get(str(slot)) or claim
            if (claim or {}).get("job"):
                # Kept from now on as any recovered slot, so the retry it may grant is recorded for good.
                claim = state["slots"][str(slot)] = {**claim, "exact": True}
        if state.get("legacy_registry"):
            claim = _legacy_claim(state, name, slot, exact) or claim
        rec = {**entry, "slot": slot, "at": now()}
        latest = slot == state["last_slot"] or exact
        if (exact and not (claim or {}).get("job")) or state["last_slot"] is None or slot > state["last_slot"]:
            state["last_slot"] = max(slot, state["last_slot"] or slot)
            # A recovered slot is kept however old it is, so a second dispatch of it finds it filled.
            state["slots"][str(slot)] = {"job": job, "retried": None, **({"exact": True} if exact else {})}
            decision, previous = "first", None
        elif claim and claim.get("job") and latest and not claim.get("retried") and entry.get("profile") == "read-only" and _profile_of(registry, name, state, claim["job"]) == "read-only" and not _completed_or_live(registry, name, state, claim["job"]):
            claim["retried"] = job
            if exact:
                # A retry a recovery grants is kept as any recovered slot: if its start is refused it leaves no run, and a slot pruned from the record could not show it.
                claim["exact"] = True
            rec["retry_of"] = claim["job"]
            decision, previous = "retry", _previous(registry, name, state, claim["job"])
        else:
            decision = "skip"
            previous = _previous(registry, name, state, claim.get("job")) if claim else None
            rec["outcome"] = {"kind": "skipped", "previous": previous, "reason": "this slot already ran" if claim else "a later slot already ran"}
        state["jobs"][job] = rec
        _save(registry, name, state)
        return {"decision": decision, "previous": previous}


def record_job(registry, name, job, entry):
    """Record a job that claims no slot (a manual run, a re-run, a check), leaving the slots alone."""
    with _lock(registry, name):
        state = _load(registry, name)
        state["jobs"][job] = {**entry, "at": now()}
        _save(registry, name, state)


def update_job(registry, name, job, fields):
    """Merge `fields` into job `job`'s record, creating it if needed. Returns the record."""
    with _lock(registry, name):
        state = _load(registry, name)
        rec = state["jobs"].setdefault(job, {"at": now()})
        rec.update(fields)
        _save(registry, name, state)
        return rec


def read_job(registry, name, job):
    """Job `job`'s record, or None."""
    state = schedule_state(registry, name)
    return ((state or {}).get("jobs") or {}).get(job)


def find_by_origin_job(registry, job):
    """The run directories whose meta's `origin.job` is `job`, oldest first."""
    return [d for d, m in iter_runs(registry) if (m.get("origin") or {}).get("job") == job]


def schedule_lock(registry, name):
    """The schedule's lock, for callers that must hold it across their own steps: `schedule`'s commit and install, `unschedule`, and a one-off Finish comparing the definition and disabling it (K8). The functions below that say so must be called under it; every other function here takes it itself."""
    ensure_runs_dir(registry)
    return _lock(registry, name)


def read_definition(registry, name):
    """The definition the latest `schedule` committed, as this Mac recorded it, or None. Call under schedule_lock."""
    return (schedule_state(registry, name) or {}).get("definition")


def take_over_legacy_state(registry, name, legacy_registry, keep_definition=False):
    """What the `claude` skill recorded of schedule `name` — its filled slots, and its jobs marked legacy with how each one's run ended — as this skill's record, when this skill holds none of its own yet; then the old record's definition is cleared, so a job the old command still runs leaves the schedule alone at its end. `keep_definition` is a move's: the new record takes the old definition, which the old record keeps for a rollback. Call under schedule_lock. Returns whether there was an old record."""
    old_path = Path(legacy_registry) / ".schedules" / name / "state.json"
    with locked(Path(legacy_registry) / ".locks" / f"schedule-{name}.lock"):
        old = read_json(old_path)
        if not old:
            return False
        current = schedule_state(registry, name)
        if not (current and any(not r.get("legacy") for r in (current.get("jobs") or {}).values())):
            jobs = {}
            for job, rec in (old.get("jobs") or {}).items():
                ended = (read_json(Path(legacy_registry) / rec["run_id"] / "meta.json") or {}).get("state") if rec.get("run_id") else None
                jobs[job] = {**rec, "legacy": True, **({"legacy_run_state": ended} if ended else {})}
            # The old registry is kept: a job its command queued before now still claims its slot there, so this skill's claims read it too (`_legacy_claim`).
            _save(registry, name, {"name": name, "last_slot": old.get("last_slot"), "slots": old.get("slots") or {}, "jobs": jobs, "definition": old.get("definition") if keep_definition else (current or {}).get("definition"), "legacy_registry": str(legacy_registry)})
        if old.get("definition") is not None and not keep_definition:
            write_json(old_path, {**old, "definition": None})
        return True


def release_legacy_definition(name, legacy_registry):
    """Clear the definition the `claude` skill recorded of schedule `name`, under that skill's lock, so a job its command still runs leaves alone, at its end, whatever is scheduled under the name next. A record that names another schedule — one whose name differs only by letter case or Unicode form, at the same path on this Mac — is left alone. Returns whether there was one to clear."""
    old_path = Path(legacy_registry) / ".schedules" / name / "state.json"
    if not old_path.exists():  # the old skill never recorded it: no definition to clear, and taking its lock would make its registry here
        return False
    with locked(Path(legacy_registry) / ".locks" / f"schedule-{name}.lock"):
        old = read_json(old_path)
        if not old or old.get("definition") is None or old.get("name", name) != name:
            return False
        write_json(old_path, {**old, "definition": None})
        return True


def write_definition(registry, name, definition):
    """Record the definition just committed, or None after `unschedule`. Call under schedule_lock."""
    state = _load(registry, name)
    state["definition"] = definition
    _save(registry, name, state)


def schedule_state_holder(registry, name):
    """The other schedule whose state sits where `name`'s would — the name its state.json records, when that is not `name` to the letter — or None. A file system that ignores letter case and Unicode form gives two names one directory."""
    try:
        held = (schedule_state(registry, name) or {}).get("name")
    except ValueError:
        return None
    return held if isinstance(held, str) and held != name else None


def remove_schedule_state(registry, name):
    """Delete the schedule's state and files — unless the directory is another schedule's (see `schedule_state_holder`): then nothing is deleted and that schedule's name is returned. A directory whose state.json is missing or unreadable names nobody and goes; it holds only copies of summaries and schemas. Call under schedule_lock."""
    import shutil

    held = schedule_state_holder(registry, name)
    if held:
        return held
    shutil.rmtree(schedule_dir(registry, name), ignore_errors=True)
    return None


def latest_job(registry, name):
    """The newest job record of the schedule as `(job, record)`, or None."""
    jobs = (schedule_state(registry, name) or {}).get("jobs") or {}
    if not jobs:
        return None
    job = max(jobs, key=lambda k: (jobs[k].get("at") or 0, k))
    return job, jobs[job]


def schedule_runs_live(registry, name):
    """The run ids of this schedule's runs that are still live."""
    return [m["run_id"] for d, m in _schedule_runs(registry, name) if is_live(reap(d))]
