"""Run directories under <main checkout>/.delegate-runs: claiming one under the registry's publish lock, its meta.json written atomically under the run's lock, compare-and-set for outcomes, reaping a run whose supervisor died, and finding a run by its id, its engine's native id (a Claude session, a Codex thread) or a prefix of either.

A run's meta carries `engine` and `native_id` (None until the engine names it: a Codex thread id arrives after launch), the common lifecycle (`state`, `created_at`, `ended_at`, epoch seconds) and the supervisor's and worker's pid and process group (`supervisor_pid`/`supervisor_pgid`, `worker_pid`/`worker_pgid`); everything else in it belongs to the engine that wrote it.
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import time
from pathlib import Path

from delegate.errors import NotFound, Refusal
from delegate.git import main_checkout
from delegate.util import iso, now, pid_alive

from .locks import locked, read_json, write_json

DIRNAME = ".delegate-runs"
TERMINAL = ("completed", "failed", "interrupted", "timed_out", "orphaned", "quota_exhausted")
ACTIVE = ("starting", "running", "finishing")
# A run published but never handed to a supervisor (its creator died in between) is orphaned after this long.
UNSUPERVISED_GRACE = 60.0


def resolve_runs_dir(cwd, runs_dir=None):
    """The registry for work started from `cwd`: `runs_dir` when given, otherwise .delegate-runs in the repository's main checkout, so every worktree of a repository shares one; outside git, in `cwd` itself."""
    if runs_dir:
        return str(Path(runs_dir).expanduser().resolve())
    base = main_checkout(cwd) or str(Path(cwd).resolve())
    return str(Path(base) / DIRNAME)


def registry_args(runs_dir):
    """`--runs-dir <dir>` when `runs_dir` is not the registry a command run from the current directory would find, so a printed command reaches the same runs wherever it is run; nothing otherwise."""
    if os.path.realpath(runs_dir) == os.path.realpath(resolve_runs_dir(os.getcwd())):
        return []
    return ["--runs-dir", str(runs_dir)]


def ensure_runs_dir(runs_dir):
    """Create the registry if needed, with a .gitignore of `*` so git never sees it."""
    path = Path(runs_dir)
    (path / ".locks").mkdir(parents=True, exist_ok=True)
    ignore = path / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")
    return str(path)


def meta_path(run_dir):
    return Path(run_dir) / "meta.json"


def run_lock(run_dir):
    return locked(Path(run_dir) / ".lock")


@contextlib.contextmanager
def publish_lock(runs_dir):
    """The one lock every run is published under and that `clean` holds while it removes a worktree, so no run of any engine can start working in a checkout between `clean`'s look and its removal."""
    ensure_runs_dir(runs_dir)
    with locked(Path(runs_dir) / ".locks" / "publish.lock"):
        yield


def read_meta(run_dir):
    """A run's meta.json; Refusal when it is missing or does not parse."""
    try:
        meta = read_json(meta_path(run_dir))
    except ValueError as e:
        raise Refusal(f"meta.json of {Path(run_dir).name} does not parse: {e}")
    if meta is None:
        raise Refusal(f"no run at {run_dir}")
    return meta


def update_meta(run_dir, change, then=None):
    """Apply `change(meta)` under the run's lock and write the result; `change` edits the dict in place. `then(meta)`, when given, runs after the write and still under the lock. Returns the new meta."""
    with run_lock(run_dir):
        meta = read_meta(run_dir)
        change(meta)
        write_json(meta_path(run_dir), meta)
        if then:
            then(meta)
        return meta


def record_if_active(run_dir, fields):
    """Write `fields` only while the run has not recorded an outcome, so the first outcome written wins. True when written."""
    with run_lock(run_dir):
        meta = read_meta(run_dir)
        if meta.get("state") in TERMINAL:
            return False
        meta.update(fields)
        write_json(meta_path(run_dir), meta)
        return True


def supervisor_alive(meta):
    return pid_alive(meta.get("supervisor_pid"), meta.get("supervisor_pgid"))


def worker_alive(meta):
    """Whether the engine process the run started is still running in its own process group, which tells a reused pid apart."""
    return pid_alive(meta.get("worker_pid"), meta.get("worker_pgid"))


def is_live(meta):
    """Whether anything of the run may still be working: an outcome not yet recorded, or the recorded worker still running in its own process group (an orphan still writing)."""
    return meta.get("state") not in TERMINAL or worker_alive(meta)


def reap(run_dir):
    """Record `orphaned` for a run whose supervisor is gone without recording an outcome, with what it left undelivered. Returns the meta as it now stands. The decision is made again under the run's lock, so a supervisor that records itself in between is never taken for dead."""
    meta = read_meta(run_dir)
    if not _abandoned(meta):
        return meta

    def orphan(m):
        if _abandoned(m):
            inbox = sorted(p.name for p in (Path(run_dir) / "inbox").glob("*.json")) if (Path(run_dir) / "inbox").is_dir() else []
            m.update({"state": "orphaned", "ended_at": now(), "error": "the supervisor died before recording an outcome", "undelivered": [n.split("-", 1)[1][:-5] for n in inbox]})

    return update_meta(run_dir, orphan)


def _abandoned(meta):
    if meta.get("state") in TERMINAL:
        return False
    if meta.get("supervisor_pid"):
        return not supervisor_alive(meta)
    return now() - (meta.get("created_at") or 0) >= UNSUPERVISED_GRACE


def _reap_quietly(run_dir, meta):
    """`reap`, but a run that cannot be reaped is shown as it was read: one broken record never stops another run from being published."""
    try:
        return reap(run_dir)
    except (Refusal, OSError, ValueError, IndexError):
        return meta


def new_run_id(label=None):
    slug = re.sub(r"[^a-z0-9-]+", "-", (label or "run").lower()).strip("-")[:24] or "run"
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}-{secrets.token_hex(2)}"


def publish_run(runs_dir, label, check, make_meta):
    """Create a run under the registry's publish lock: `check(runs)` sees every readable run as (run_dir, meta), reaped, and may refuse; then a fresh run directory is claimed and `make_meta(run_id, run_dir)` written as its meta.json. Returns (run_id, run_dir, meta)."""
    with publish_lock(runs_dir):
        check([(d, _reap_quietly(d, m)) for d, m in iter_runs(runs_dir)])
        for _ in range(20):
            run_id = new_run_id(label)
            run_dir = Path(runs_dir) / run_id
            try:
                run_dir.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise Refusal("could not claim a free run id")
        (run_dir / "inbox").mkdir()
        meta = make_meta(run_id, str(run_dir))
        meta.setdefault("run_id", run_id)
        meta.setdefault("created_at", now())
        meta.setdefault("created", iso(meta["created_at"]))
        write_json(meta_path(run_dir), meta)
        return run_id, str(run_dir), meta


def iter_runs(runs_dir):
    """(run_dir, meta) for every readable run, oldest first. A meta.json that does not parse is skipped here; `unreadable_runs` names them."""
    path = Path(runs_dir)
    if not path.is_dir():
        return []
    out = []
    for d in path.iterdir():
        if d.name.startswith(".") or not d.is_dir():
            continue
        try:
            meta = read_json(d / "meta.json")
        except ValueError:
            continue
        if meta:
            out.append((str(d), meta))
    out.sort(key=lambda x: (x[1].get("created_at") or 0, Path(x[0]).name))
    return out


def unreadable_runs(runs_dir):
    """The run directories whose meta.json exists but does not parse."""
    path = Path(runs_dir)
    out = []
    for d in sorted(path.iterdir()) if path.is_dir() else []:
        if d.name.startswith(".") or not d.is_dir() or not (d / "meta.json").exists():
            continue
        try:
            read_json(d / "meta.json")
        except ValueError:
            out.append(str(d))
    return out


def find_run(runs_dir, ref, engine=None):
    """The run directory `ref` names: a run id, a native id (its newest run; of `engine` when given), or a prefix of either that picks one run or one native id. NotFound when nothing matches; Refusal when a prefix matches more than one."""
    if not ref:
        raise Refusal("no run named", arguments=True)
    if "/" not in ref and ref not in (".", ".."):
        exact = Path(runs_dir) / ref
        if (exact / "meta.json").exists():
            return str(exact)
    runs = [(d, m) for d, m in iter_runs(runs_dir)]
    natives = [(d, m) for d, m in runs if engine is None or m.get("engine") == engine]
    by_native = [(d, m.get("engine")) for d, m in natives if ref in (m.get("native_id"), m.get("resume_ref"))]
    if by_native:
        if len({e for _, e in by_native}) > 1:
            raise Refusal(f"{ref!r} names a session or thread in more than one engine; give a run id", matches=[Path(d).name for d, _ in by_native][-10:])
        return by_native[-1][0]
    ids = [d for d, m in runs if Path(d).name.startswith(ref)]
    prefixed = {(m.get("engine"), m.get("native_id")) for d, m in natives if (m.get("native_id") or "").startswith(ref)}
    if len(ids) == 1 and not prefixed - {(read_json(Path(ids[0]) / "meta.json").get("engine"), read_json(Path(ids[0]) / "meta.json").get("native_id"))}:
        return ids[0]
    if not ids and len(prefixed) == 1:
        eng, nid = prefixed.pop()
        return [d for d, m in natives if m.get("native_id") == nid and m.get("engine") == eng][-1]
    if ids or prefixed:
        raise Refusal(f"{ref!r} matches more than one run; give more of the id", matches=sorted({Path(d).name for d in ids} | {n for _, n in prefixed})[:10])
    raise NotFound(f"no run, session or thread {ref!r} in {runs_dir}")


def newest_on(runs_dir, engine, native_id):
    """The newest run of `engine` on `native_id`, or None."""
    found = [d for d, m in iter_runs(runs_dir) if m.get("engine") == engine and m.get("native_id") == native_id]
    return found[-1] if found else None


def live_runs(runs_dir):
    """(run_dir, meta) of every live run, after reaping dead supervisors."""
    out = []
    for d, _ in iter_runs(runs_dir):
        meta = reap(d)
        if is_live(meta):
            out.append((d, meta))
    return out
