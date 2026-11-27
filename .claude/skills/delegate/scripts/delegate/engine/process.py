"""The detached supervisor every run goes through, whatever its engine: it starts the engine's process behind a gate, copies its stdout to events.jsonl while feeding each line to the engine's driver, enforces the run's deadline, honours a stop that lands at any moment, and records the outcome the driver concludes. What a line means and when a run has answered belong to the driver (`claude.Driver`, `codex.Driver`)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from delegate.registry import TERMINAL, read_meta, record_if_active, update_meta
from delegate.util import group_alive, now, pid_alive, timings

# The variables that tell a command it runs inside a worker. DELEGATE_RUN is this skill's; CLAUDE_SKILL_RUN is the marker of the `claude` skill this one replaces, set too while that skill can still be installed, so a worker cannot start workers through it either. The `codex` skill it replaces has no such marker.
MARKER = "DELEGATE_RUN"
OTHER_MARKERS = ("CLAUDE_SKILL_RUN",)


def markers(run_id):
    return {MARKER: run_id, **{name: run_id for name in OTHER_MARKERS}}


def inside_worker(env=None):
    """The run id of the worker this process runs inside, from any marker, or None."""
    env = os.environ if env is None else env
    for name in (MARKER, *OTHER_MARKERS):
        if env.get(name):
            return env[name]
    return None


def log(run_dir, text):
    with open(Path(run_dir) / "supervisor.log", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {text}\n")


def signal_ladder(pgid, pid, *, grace=None, kill_grace=None, on_signal=None):
    """Interrupt a process group: SIGINT, then SIGTERM after `grace`, then SIGKILL after `kill_grace`, stopping as soon as `pid` is gone; then, when something of the group outlived `pid` (a tool's child that ignores SIGINT), SIGKILL to what is left after `kill_grace`, so nothing of the run keeps working once it is recorded stopped. SIGINT first lets the engine end its turn and keep the session or thread resumable. Returns the signals sent."""
    t = timings()
    grace = t["stop_grace"] if grace is None else grace
    kill_grace = t["kill_grace"] if kill_grace is None else kill_grace
    sent = []
    for sig, wait in ((signal.SIGINT, grace), (signal.SIGTERM, kill_grace), (signal.SIGKILL, 0)):
        if not pid_alive(pid, pgid):
            break
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        except PermissionError:
            break
        sent.append(sig.name)
        if on_signal:
            on_signal(sig.name)
        end = time.time() + wait
        while time.time() < end and pid_alive(pid, pgid):
            time.sleep(0.05)
    if sent and sent[-1] != "SIGKILL" and group_alive(pgid):
        # The group id stays reserved while any member lives, so what answers here is still this run's.
        end = time.time() + kill_grace
        while time.time() < end and group_alive(pgid):
            time.sleep(0.05)
        try:
            os.killpg(pgid, signal.SIGKILL)
            sent.append("SIGKILL")
        except (ProcessLookupError, PermissionError):
            pass
    return sent


class Pump:
    """Reads the worker's stdout line by line: appends each raw line to events.jsonl and hands it to the driver — `feed(event)` under `lock`, then `after_feed(event)` outside it, for work that must not hold the lock (a startup snapshot). A line that is not a JSON object is fed as None."""

    def __init__(self, proc, events_path, driver):
        self.proc = proc
        self.driver = driver
        self.lock = threading.Lock()
        self.lines = 0
        self.last_line_at = now()
        self.events_path = events_path
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        with open(self.events_path, "ab") as out:
            for raw in iter(self.proc.stdout.readline, b""):
                out.write(raw if raw.endswith(b"\n") else raw + b"\n")
                out.flush()
                try:
                    e = json.loads(raw)
                except ValueError:
                    e = None
                if not isinstance(e, dict):
                    e = None
                with self.lock:
                    self.lines += 1
                    self.last_line_at = now()
                    self.driver.feed(e)
                self.driver.after_feed(e)


# The engine starts behind a gate: a shell that waits on a pipe and only then becomes the engine with `exec`, keeping its pid. Nothing of the engine runs, not even a startup hook, until the supervisor has recorded that pid and found no stop request.
GATE = 'read -r go <&"$0" && [ "$go" = go ] && exec "$@"; exit 125'


def record_stopped(m):
    """Record a stop that no worker process reported: interrupted, with every message that was never delivered — all of them when the worker never ran."""
    if m.get("state") not in TERMINAL:
        m.update({"state": "interrupted", "error": "stopped", "ended_at": now(), "stdin_open": False, "undelivered": [x["uuid"] for x in m.get("messages") or [] if x.get("after_line") is None]})


def supervise(run_dir, adapter):
    """Run one engine process for the run in `run_dir` to its end, driven by `adapter`'s driver. Exits when the process has and the outcome is recorded."""
    t = timings()
    run_dir = str(run_dir)
    meta = read_meta(run_dir)
    update_meta(run_dir, lambda m: m.update({"supervisor_pid": os.getpid(), "supervisor_pgid": os.getpgid(0)}))
    env = adapter.worker_env(dict(os.environ), markers(meta["run_id"]))
    stderr_file = open(Path(run_dir) / "stderr.log", "ab")
    time.sleep(t["pause_before_launch"])
    # `stop` can signal the worker only once its pid is recorded, so a stop that lands before that is honoured here: before launching, and when the pid is recorded — under the run's lock, as `stop` sets its request and reads the pid under it, so whichever comes second sees the other.
    if update_meta(run_dir, lambda m: m.get("stop_requested") and record_stopped(m)).get("state") in TERMINAL:
        log(run_dir, "stopped before the worker was launched")
        stderr_file.close()
        return 0
    if not Path(meta["launch_cwd"]).is_dir():
        stderr_file.close()
        record_if_active(run_dir, {"state": "failed", "error": f"the working directory recorded for this run is gone: {meta['launch_cwd']}", "ended_at": now(), "stdin_open": False})
        return 1
    gate_read, gate_write = os.pipe()
    try:
        proc = subprocess.Popen(["/bin/sh", "-c", GATE, str(gate_read), *meta["argv"]], cwd=meta["launch_cwd"], stdin=subprocess.PIPE if adapter.STDIN == "pipe" else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr_file, env=env, start_new_session=True, pass_fds=(gate_read,))
    except OSError as e:
        os.close(gate_read)
        os.close(gate_write)
        stderr_file.close()
        record_if_active(run_dir, {"state": "failed", "error": f"could not start {adapter.NAME}: {e}", "ended_at": now(), "stdin_open": False})
        return 1
    os.close(gate_read)
    time.sleep(t["pause_before_pid"])

    def record_pid(m):
        m.update({"worker_pid": proc.pid, "worker_pgid": proc.pid, "started_at": now()})
        if m.get("stop_requested"):
            record_stopped(m)

    def open_gate(m):
        # Under the same lock, once the pid is written: a stop lands either before (the engine never runs) or after (it finds the pid and signals a running engine), never in between.
        if m.get("state") in TERMINAL:
            return
        try:
            os.write(gate_write, b"go\n")
        except OSError as e:
            log(run_dir, f"could not open the gate: {e}")

    recorded = update_meta(run_dir, record_pid, then=open_gate)
    os.close(gate_write)
    if recorded.get("state") in TERMINAL:
        # The gate closed unopened makes the shell exit without ever running the engine.
        proc.wait()
        stderr_file.close()
        log(run_dir, "stopped before the worker ran")
        return 0
    log(run_dir, f"{adapter.NAME} pid {proc.pid}: {' '.join(meta['argv'][:3])} …")
    driver = adapter.driver(run_dir, meta, proc)
    pump = Pump(proc, Path(run_dir) / "events.jsonl", driver)
    driver.bind(pump)
    deadline = (now() + float(meta["settings"]["timeout"])) if meta["settings"].get("timeout") else None
    ending = None
    ladder = None
    while proc.poll() is None:
        driver.tick()
        if ending is None:
            if deadline and now() >= deadline:
                log(run_dir, "deadline passed")
                update_meta(run_dir, lambda m: m.update({"timed_out_at": now()}))
                ending = "deadline"
            else:
                asked = read_meta(run_dir).get("stop_requested")
                # `stop` signals the group itself when it can tell the process is this run's. The supervisor, which owns the child, is the fallback: if the worker outlives the time `stop`'s own ladder takes, it ends it.
                if asked and now() - asked >= t["stop_grace"] + t["kill_grace"] + 1:
                    log(run_dir, "stop requested and the worker still runs")
                    ending = "stop"
            if ending:
                ladder = threading.Thread(target=signal_ladder, args=(proc.pid, proc.pid), daemon=True)
                ladder.start()
        time.sleep(t["poll"])
    if ladder:
        # The ladder's last step sweeps what of the group outlived the engine; the outcome is recorded after it.
        ladder.join(timeout=t["stop_grace"] + 2 * t["kill_grace"] + 5)
    pump.thread.join(timeout=10)
    returncode = proc.returncode
    log(run_dir, f"{adapter.NAME} exited {returncode}")
    stderr_file.close()
    stderr_tail = ""
    try:
        stderr_tail = (Path(run_dir) / "stderr.log").read_bytes()[-2000:].decode("utf-8", "ignore").strip()
    except OSError:
        pass

    def finish(m):
        if m.get("state") in TERMINAL:
            return
        fields = driver.outcome(m, returncode, stderr_tail)
        if m.get("stop_requested"):
            fields.update({"state": "interrupted", "error": "stopped"})
        elif m.get("timed_out_at"):
            fields.update({"state": "timed_out", "error": f"--timeout of {m['settings'].get('timeout'):g} s passed"})
        fields.setdefault("exit_code", returncode)
        fields.setdefault("ended_at", now())
        if fields.get("state") == "failed" and stderr_tail and "stderr_tail" not in fields:
            fields["stderr_tail"] = stderr_tail[-600:]
        m.update(fields)
        m["stdin_open"] = False

    update_meta(run_dir, finish)
    return 0


def run_supervisor(run_dir, adapter):
    """`supervise`, recording a failure when the supervisor itself breaks: an outcome must be recorded whatever happens."""
    try:
        return supervise(run_dir, adapter)
    except Exception as e:
        import traceback

        log(run_dir, "supervisor error:\n" + traceback.format_exc())
        try:
            record_if_active(run_dir, {"state": "failed", "error": f"supervisor error: {e}", "ended_at": now(), "stdin_open": False})
        except Exception:
            pass
        return 1
