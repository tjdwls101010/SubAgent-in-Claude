"""The Claude engine: a `claude -p` session driven through stream-json. Owns how a turn's settings are resolved and re-sent (a resumed `-p` session restores only its model), the prompt's way in (the run's inbox, written to stdin), when stdin closes (every message answered, or the last answer followed by silence), which result answers the run, and how its events read as a header, a status row and log lines.

When stdin closes: (a) every delivered message's uuid appears in some result, or (b) the last result seen is not a background follow-up, came after the last delivered message, no hook is still running, and stdout has been silent for the idle grace — which covers a message a hook dropped, whose uuid no result will ever carry. Both are decided under the run's lock with the inbox empty, so a message `send` queued is delivered first.
"""

from __future__ import annotations

import os
import re
import threading
import uuid as uuidlib
from pathlib import Path

from delegate.claude_cli import EFFORTS, PROFILES, worktree_location, VERIFIED_VERSION, quota as usage_report, VERSION_FLOOR, VERSION_FLOOR_TEXT, activity_rows, auth_status, build_argv, model_aliases, claude_version, encode_user_message, final_result, profile_exceptions, read_events, render_rows, sandbox_prereqs, summarize, turn_preamble, version_floor_check, worker_env
from delegate.errors import Refusal
from delegate.git import branch_exists, main_checkout, startup_snapshot, toplevel
from delegate.registry import TERMINAL, close, enqueue, is_live, iter_runs, newest_on, read_meta, reap, registry_args, supervisor_alive, take, update_meta, worker_alive
from delegate.util import invocation, iso, now, timings

NAME = "claude"
# Whether a run can ask for the priority service tier (`--priority`).
TAKES_PRIORITY = False
STDIN = "pipe"
EXPECTED_MODE = {"read-only": "dontAsk", "write": "acceptEdits", "full": "auto"}
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
FILES_NOTE = "files_changed lists Edit, Write and NotebookEdit calls; files a shell command wrote are not in it"


def _text(v):
    return ".".join(str(x) for x in v)


# -- settings -----------------------------------------------------------------


def read_schema(path, command):
    """The text of a --schema file, which must hold one JSON object."""
    import json

    p = Path(path).expanduser()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise Refusal(f"--schema {path}: {e.strerror or e}", arguments=True, help=f"{command} --help")
    except UnicodeDecodeError as e:
        raise Refusal(f"--schema {path} is not UTF-8 text: {e}", arguments=True, help=f"{command} --help")
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise Refusal(f"--schema {path} is not JSON: {e}", arguments=True, help=f"{command} --help")
    if not isinstance(obj, dict):
        raise Refusal(f"--schema {path} must hold a JSON object", arguments=True, help=f"{command} --help")
    return json.dumps(obj, ensure_ascii=False)


def settings(flags, record=None, *, command="start", cwd=None):
    """The settings for one turn: each of profile, model, effort and schema from the flags, else from the session's last recorded run (`record`), and for a new session from the flags alone — there is no default model or effort, so a run never depends on which settings file a worker reads. For a session the registry never saw (`record` None, `flags["resume"]`) the model is the session's own (`claude -p --resume` restores it) and the effort must be given, since the session does not keep it."""
    if flags.get("priority"):
        raise Refusal("--priority is a codex setting; claude has no service tier to request", arguments=True, help=f"{command} --help")
    record = record or {}
    resume = bool(flags.get("resume"))
    out = {"engine": NAME}
    for key in ("profile", "model", "effort"):
        out[key] = flags.get(key) or record.get(key)
    if not out["profile"]:
        out["profile"] = "write" if not resume else None
    if resume and not record and not out["profile"]:
        raise Refusal("this session is not in the registry, so its profile is unknown; pass --profile", arguments=True, help="resume --help")
    if not out["effort"]:
        raise Refusal("this session is not in the registry, so its effort is unknown (a resumed claude -p session does not keep it); pass --effort" if resume else "--effort is required: there is no default effort", arguments=True, help=f"{command} --help")
    if not out["model"] and not resume:
        raise Refusal("--model is required: there is no default model; `claude -p /model` lists the aliases", arguments=True, help=f"{command} --help")
    if out["profile"] not in PROFILES:
        raise Refusal(f"unknown profile {out['profile']!r}", arguments=True, help=f"{command} --help")
    if flags.get("model") and not flags["model"].startswith("claude-"):
        aliases = model_aliases()
        # A model taken up now is checked against what `/model` offers, when it can be read; one inherited from the session is not, and claude reports any other at its first init.
        if aliases and flags["model"] not in aliases:
            raise Refusal(f"model {flags['model']!r} is neither an alias claude offers nor a full model id (claude-…)", arguments=True, help=f"{command} --help", known_aliases=aliases, hint="`models --engine claude` lists them")
    if out["effort"] not in EFFORTS:
        raise Refusal(f"effort {out['effort']!r} is not one claude takes: {', '.join(EFFORTS)}", arguments=True, help=f"{command} --help", valid_efforts=list(EFFORTS))
    out["schema_text"] = read_schema(flags["schema"], command) if flags.get("schema") else record.get("schema_text")
    out["changed"] = {k: record[k] for k in ("profile", "model", "effort") if record and record.get(k) and record.get(k) != out[k]}
    return out


def check_installed():
    """Refuse a run the installed claude cannot carry."""
    problem = version_floor_check()
    if problem:
        raise Refusal(problem)


def new_native_id():
    """A Claude session id is chosen before launch (`--session-id`), so the registry knows it from publication on."""
    return str(uuidlib.uuid4())


def check_native_id(ref):
    """Whether `ref` can name a Claude session this registry never saw."""
    if not UUID.match(ref or ""):
        raise Refusal(f"{ref!r} is not a Claude session id (a UUID), so there is no session to resume", arguments=True, help="resume --help")


def discover_native_id(run_dir):
    """The session id a run's events name, for a run whose meta does not say: every stream-json event carries it."""
    events, _ = read_events(Path(run_dir) / "events.jsonl")
    return next((e["session_id"] for e in events if e.get("session_id")), None)


def unregistered_note():
    return "this session was not in the registry; it now runs under the profile and effort you gave, and on the model the session recorded unless --model changed it"


def prepare(meta, prompt, *, group_info=None, worktree_info=None, origin=None):
    """(argv, the text the first message carries) for one turn. Facts that can change from turn to turn (the profile, a group, a worktree, a schedule) go in front of the prompt, not into the system prompt, which a resumed session keeps as first recorded."""
    s = meta["settings"]
    preamble = turn_preamble(s["profile"], group=group_info, worktree=worktree_info, origin=origin)
    text = f"{preamble}\n\n{prompt}" if preamble else prompt
    argv = build_argv(s, session_id=meta["native_id"], resume=meta["kind"] == "resume", cwd=meta["cwd"], worktree=(meta.get("worktree") or {}).get("name") if meta["kind"] == "start" else None, schema_text=s.get("schema_text"))
    return argv, text


def worktree_plan(repo, name, base, main):
    """A group member's checkout as Claude Code makes it with `--worktree <name>`: at <repo>/.claude/worktrees/<name> on branch worktree-<name>, from the local HEAD (`worktree.baseRef: head`), with what .worktreeinclude names copied in. Claude Code creates it when the run starts."""
    where = worktree_location(repo, name)
    return {"engine": NAME, "name": name, "path": where["path"], "branch": where["branch"], "base": base, "repo": repo, "main": main}


def worktree_taken(plan):
    """Why a planned checkout cannot be made fresh, or None: Claude Code would reopen a leftover of the same name instead of starting clean."""
    if Path(plan["path"]).exists() or branch_exists(plan["repo"], plan["branch"]):
        return f"worktree {plan['name']} is still there from an earlier group (path {plan['path']} or branch {plan['branch']}); Claude Code would reopen it instead of starting fresh"
    return None


def extra_meta(cwd, s):
    roots = [r for r in {toplevel(cwd), main_checkout(cwd)} if r]
    return {"stdin_open": True, "messages": [], "profile_exceptions": profile_exceptions(cwd, s["profile"], project_roots=roots)}


def after_publish(run_dir, text, prompt):
    """The first message goes in through the inbox, as every later `send` does."""
    enqueue(run_dir, str(uuidlib.uuid4()), text, kind="prompt", head=prompt)


def started(meta):
    return bool(meta.get("init"))


def not_started_note():
    return "claude has not reported its init yet; `status --run` shows it once it does"


def reply_fields(meta):
    init = meta.get("init") or {}
    return {"permission_mode": init.get("permission_mode"), "mode_mismatch": meta.get("mode_mismatch"), "profile_exceptions": meta.get("profile_exceptions") or [], "model": init.get("model") or meta["settings"]["model"], "cwd": init.get("cwd") or meta["cwd"]}


def live_instead(run_id, meta, runs_dir):
    """What to do instead of starting another run on a session that has a live one: send to it while its stdin is open, otherwise stop it first."""
    extra = registry_args(runs_dir)
    if meta.get("state") in ("starting", "running") and meta.get("stdin_open") and supervisor_alive(meta):
        return {"instead": invocation("send", run_id, "<message>", *extra), "why": "the run is live and still reading messages"}
    return {"instead": f"{invocation('stop', '--run', run_id, *extra)}, then resume", "why": "the run is finishing or its supervisor is gone while claude still runs"}


SEND_LIMIT = 16


def send_instead(run_id, meta, runs_dir):
    """What to do instead of a send the run cannot take: never pointing back at send."""
    state = meta.get("state")
    extra = registry_args(runs_dir)
    if state == "finishing":
        return {"instead": invocation("result", "--run", run_id, "--wait", *extra), "or": f"{invocation('stop', '--run', run_id, *extra)}, then resume", "why": "the answer is already in and stdin is closed"}
    if state in TERMINAL and not worker_alive(meta):
        # A newer run of the same session may be live; resume would be refused for it, so point at that run instead.
        newest_dir = newest_on(runs_dir, NAME, meta["native_id"]) if meta.get("native_id") else None
        newest = reap(newest_dir) if newest_dir else meta
        if newest.get("run_id") != run_id and is_live(newest):
            if newest.get("state") in ("starting", "running") and newest.get("stdin_open") and supervisor_alive(newest):
                return {"instead": invocation("send", newest["run_id"], "<message>", *extra), "why": f"run {run_id} has ended, but its session goes on in the live run {newest['run_id']}"}
            return send_instead(newest["run_id"], newest, runs_dir)
        return {"instead": invocation("resume", run_id, "<message>", *extra), "why": "the run has ended; resume continues its session"}
    if state in ("starting", "running") and supervisor_alive(meta):
        # Its stdin closed because the answer is in: a stop now could turn that answer into an interruption.
        return {"instead": invocation("result", "--run", run_id, "--wait", *extra), "or": f"{invocation('stop', '--run', run_id, *extra)}, then resume", "why": "the run no longer reads messages: its answer is in or coming"}
    return {"instead": f"{invocation('stop', '--run', run_id, *extra)}, then resume", "why": "the run's supervisor is gone or its stdin is closed"}


def send(run_dir, meta, message, *, interrupt, runs_dir):
    """Queue `message` for a live run whose supervisor still holds stdin open, and wait briefly for the supervisor to write it. `interrupt` sends it with priority `now`, which ends the running turn; otherwise `next`, read once the running tools finish."""
    import time as _time

    run_id = meta["run_id"]
    if meta.get("state") in TERMINAL or not meta.get("stdin_open") or not supervisor_alive(meta):
        raise Refusal(f"run {run_id} is {meta.get('state')} and no longer reads messages", run_id=run_id, state=meta.get("state"), **send_instead(run_id, meta, runs_dir))
    uid = str(uuidlib.uuid4())
    priority = "now" if interrupt else "next"
    why = enqueue(run_dir, uid, message, priority=priority, kind="send", limit=SEND_LIMIT)
    if why == "closed":
        meta = reap(run_dir)
        raise Refusal(f"run {run_id} closed its stdin before the message could be queued", run_id=run_id, state=meta.get("state"), **send_instead(run_id, meta, runs_dir))
    if why == "limit":
        raise Refusal(f"run {run_id} already took {SEND_LIMIT} messages, the most one run accepts", run_id=run_id, instead=f"{invocation('result', '--run', run_id, '--wait', *registry_args(runs_dir))}, then resume", why="start another turn with resume once this run ends")
    end = now() + timings()["send_wait"]
    delivered = False
    while now() < end:
        m = next((x for x in read_meta(run_dir).get("messages") or [] if x.get("uuid") == uid), None)
        if m and m.get("delivered_at"):
            delivered = True
            break
        _time.sleep(0.05)
    reply = {"run_id": run_id, "engine": NAME, "message_uuid": uid, "priority": priority, "delivered": delivered}
    if not delivered:
        reply["note"] = "queued but not yet written to the worker; `status --run` shows it under `undelivered` if the supervisor dies first"
    return reply


def is_worker_command(command):
    """Whether a `ps` command line is the claude this skill starts, so a pid reused by another program is never signalled."""
    return "claude" in (command or "") and "stream-json" in (command or "")


# -- the supervisor's driver --------------------------------------------------


class StdinWriter:
    """Writes messages to claude's stdin from a thread of its own, so a message bigger than the pipe, which claude has not started reading, never holds up the supervisor's loop (its deadline and stop) or the run's lock. `close()` closes stdin once everything queued is written."""

    def __init__(self, stdin, on_error):
        import queue

        self.stdin = stdin
        self.on_error = on_error
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        self.pending = 0
        self.written = set()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while True:
            item = self.queue.get()
            if item is None:
                break
            uuid, data = item
            try:
                self.stdin.write(data)
                self.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self.on_error(e)
                break
            finally:
                with self.lock:
                    self.pending -= 1
            with self.lock:
                self.written.add(uuid)
        try:
            self.stdin.close()
        except OSError:
            pass

    def put(self, uuid, data):
        with self.lock:
            self.pending += 1
        self.queue.put((uuid, data))

    def busy(self):
        """Whether a queued message is not yet fully written."""
        with self.lock:
            return self.pending > 0

    def was_written(self, uuid):
        with self.lock:
            return uuid in self.written

    def close(self):
        self.queue.put(None)


class Driver:
    """What the supervisor asks of a Claude run while it lives: feed each stdout line to the stream state, deliver queued messages, record init, rate limits and retries, close stdin when the answer is in, mark `finishing` when the process outlives stdin, and conclude the outcome."""

    def __init__(self, run_dir, meta, proc):
        from delegate.claude_cli import StreamState

        self.run_dir = run_dir
        self.meta = meta
        self.proc = proc
        self.state = StreamState()
        self.first_init_result = None
        self.init_done = threading.Event()
        self.pump = None
        self.seen_init = False
        self.rate_limit = None
        self.account_read = False
        self.retrying = False
        self.finishing = False
        self.stdin_open = True
        self._first = False
        self.profile = meta["settings"]["profile"]
        self.wt = meta.get("worktree")
        from .process import log

        self.writer = StdinWriter(proc.stdin, lambda e: log(run_dir, f"stdin write failed: {e}"))

    def bind(self, pump):
        self.pump = pump

    def feed(self, e):
        if e is None:
            self.state.unparsed += 1
            return
        self._first = e.get("type") == "system" and e.get("subtype") == "init" and self.state.init is None
        self.state.feed(e)

    def after_feed(self, e):
        if self._first:
            self._first = False
            # Right here, not on the next poll: the worker's first tool call is already on its way.
            if self.wt and self.meta.get("kind") == "start" and (self.state.init or {}).get("cwd"):
                try:
                    self.first_init_result = startup_snapshot(self.state.init["cwd"], self.wt.get("repo") or self.meta["launch_cwd"])
                except OSError:
                    self.first_init_result = None
            self.init_done.set()

    def _deliver(self, msg):
        # Handed to the writer: delivery is recorded under the run's lock, and the write itself happens outside it.
        self.writer.put(msg["uuid"], encode_user_message(msg["text"], msg["uuid"], msg.get("priority")).encode("utf-8"))

    def _lines_read(self):
        with self.pump.lock:
            return self.pump.lines

    def _should_close(self, meta):
        delivered = [m for m in meta.get("messages") or [] if m.get("after_line") is not None]
        if not delivered or self.writer.busy():
            return False
        with self.pump.lock:
            results = list(self.state.results)
            quiet = now() - self.pump.last_line_at
            hooks_open = len(self.state.hooks_open)
        answered = set()
        for r in results:
            answered |= set(r["uuids"] or [])
        if all(m.get("uuid") in answered for m in delivered):
            return "answered"
        if not results:
            return False
        last = results[-1]
        if last["origin"] == "task-notification":
            return False
        if last["index"] < delivered[-1]["after_line"]:
            return False
        if quiet >= timings()["idle_grace"] and hooks_open == 0:
            return "idle"
        return False

    def tick(self):
        from .process import log, signal_ladder

        run_dir = self.run_dir
        if self.stdin_open:
            try:
                for m in take(run_dir, self._deliver, self._lines_read):
                    log(run_dir, f"delivered {m['kind']} {m['uuid']} priority={m.get('priority')}")
            except (BrokenPipeError, OSError) as e:
                log(run_dir, f"stdin write failed: {e}")
        with self.pump.lock:
            init = self.state.init if self.init_done.is_set() else None
            rl = self.state.rate_limit
            last_retry = self.state.last_event_is_retry
        changes = {}
        if init and not self.seen_init:
            self.seen_init = True
            changes.update({"init": init, "state": "running", "mode_mismatch": init["permission_mode"] != EXPECTED_MODE.get(self.profile)})
            if self.wt and self.meta.get("kind") == "start" and init.get("cwd"):
                # The worktree's startup artifacts, taken once, at the first init of the run that created it: SessionStart hooks have run and no tool call has yet.
                changes["startup_artifacts"] = self.first_init_result or {}
                changes["cwd"] = init["cwd"]
                changes["worktree"] = {**self.wt, "path": init["cwd"]}
            elif init.get("cwd") and os.path.realpath(init["cwd"]) != os.path.realpath(self.meta.get("cwd") or ""):
                # Claude Code decides where a session runs (a resumed worktree session re-enters its worktree); the registry records where it works, which is what occupancy and a handover rely on.
                changes["cwd"] = init["cwd"]
            expect = self.meta.get("expect_cwd")
            if expect and init.get("cwd") and os.path.realpath(init["cwd"]) != os.path.realpath(expect):
                changes["cwd_mismatch"] = f"cwd_mismatch: claude started in {init['cwd']}, not the recorded worktree {expect}; SessionStart hooks and the first tool call may already have run in the launch directory"
        if rl is not None and rl != self.rate_limit:
            self.rate_limit = rl
            changes["rate_limit"] = {**rl, "observed_at": now()}
            if not self.account_read:
                # Whose limits the event speaks of, read once, as the run's first event comes: a later start takes the event as its own account's only when its login matches.
                self.account_read = True
                changes["account"] = login_account()
        if last_retry and not self.retrying:
            self.retrying = True
            changes["retrying_since"] = now()
        elif not last_retry and self.retrying:
            self.retrying = False
            changes["retrying_since"] = None
        if changes:
            # A run already recorded ended (a reaper's orphan, a stop) keeps its outcome; what the stream reported is added without moving its state.
            update_meta(run_dir, lambda m: m.update({k: v for k, v in changes.items() if k != "state" or m.get("state") not in TERMINAL}))
            if changes.get("cwd_mismatch"):
                log(run_dir, changes["cwd_mismatch"])
                signal_ladder(self.proc.pid, self.proc.pid)
        if self.stdin_open:
            if close(run_dir, self._should_close):
                self.stdin_open = False
                log(run_dir, "stdin closed")
                self.writer.close()
        elif not self.finishing and self.proc.poll() is None:
            # stdin is closed but the process lives on: whatever it still runs (an MCP task) is shown as finishing.
            import time

            time.sleep(timings()["poll"])
            if self.proc.poll() is None:
                self.finishing = True
                update_meta(run_dir, lambda m: m.update({"state": "finishing", "finishing_since": now()}) if m.get("state") not in TERMINAL else None)

    def outcome(self, meta, returncode, stderr_tail):
        """The state, error and answer fields recorded when the process has exited (a stop or a deadline is the supervisor's to record over these)."""
        with self.pump.lock:
            init = self.state.init
        fields = {}
        if init and not meta.get("init"):
            # The process can end between two polls; its init is still part of what the run reported.
            fields.update({"init": init, "mode_mismatch": init["permission_mode"] != EXPECTED_MODE.get(self.profile)})
        events, unparsed = read_events(Path(self.run_dir) / "events.jsonl")
        messages = meta.get("messages") or []
        final = final_result(events, messages)
        fields.update({"exit_code": returncode, "ended_at": now(), "unanswered": [m["uuid"] for m in final["unanswered"]], "undelivered": [m["uuid"] for m in messages if m.get("after_line") is None or not self.writer.was_written(m["uuid"])], "unparsed_events": unparsed})
        result = final["result"]
        if meta.get("cwd_mismatch"):
            state, error = "failed", meta["cwd_mismatch"]
        elif returncode != 0:
            state = "failed"
            error = final["error"] or (f"claude exited {returncode}" + (f": {stderr_tail.splitlines()[-1][:300]}" if stderr_tail else ""))
        elif final["error"]:
            state, error = "failed", final["error"]
        elif result is None:
            state, error = "failed", "claude exited without a result"
        elif result["is_error"] or result["subtype"] != "success":
            state = "failed"
            parts = [result["subtype"]]
            if result["terminal_reason"]:
                parts.append(f"terminal_reason={result['terminal_reason']}")
            if result["api_error_status"]:
                parts.append(f"api_error_status={result['api_error_status']}")
            if result["errors"]:
                parts.append("; ".join(str(e) for e in result["errors"])[:300])
            elif result["text"]:
                parts.append(result["text"][:300])
            error = " ".join(parts)
        else:
            state, error = "completed", None
        fields.update({"state": state, "error": error})
        with self.pump.lock:
            rl = self.state.rate_limit
        if state == "failed" and rl and rl.get("status") == "rejected":
            # The account's limit, not the work.
            fields.update({"state": "quota_exhausted", "resets_at": rl.get("resets_at")})
        return fields


def driver(run_dir, meta, proc):
    return Driver(run_dir, meta, proc)


# -- reading a run --------------------------------------------------------------


def view(run_dir, meta):
    """The run's events read in the skill's terms (see `view_of`)."""
    events, unparsed = read_events(Path(run_dir) / "events.jsonl")
    return view_of(events, meta, unparsed)


def view_of(events, meta, unparsed=0):
    """`events` of the run `meta` describes, read in the skill's terms. Common keys: `text` (the answer once decided, else the latest message), `decided` (the answer can no longer change: ended, or stdin closed), `json` (a --schema run's structured output once decided), `schema`, `files_changed`, `usage`, `last_message`, `in_progress`, `model`, `cwd`, `rows` (for `log`), `unparsed`, `retrying` (the last event is an API retry), `rate_limit`, `header` and `row` (the engine's own fields for `result` and `status`)."""
    summary = summarize(events)
    final = final_result(events, meta.get("messages") or [])
    live = meta.get("state") not in TERMINAL
    # Once stdin has closed (finishing) the answer is decided; whatever claude still prints is not it. An orphan whose claude still runs has not decided.
    writing = worker_alive(meta)
    decided = (not live and not writing) or meta.get("state") == "finishing" or bool(meta.get("finishing_since"))
    chosen = final["result"]
    text = chosen["text"] if chosen and decided else (summary["last_message"] or (chosen["text"] if chosen else "")) or ""
    schema = (meta.get("settings") or {}).get("schema_text") is not None
    init = meta.get("init") or summary["init"] or {}
    rel = init.get("cwd") or meta.get("cwd")
    header = {
        "answered": final["answered"],
        "unanswered": final["unanswered"],
        "last_message_unanswered": final["last_message_unanswered"],
        "answer_unconfirmed": final["answer_unconfirmed"],
        "blocked_by_hook": final["blocked_by_hook"] or None,
        "last_message_blocked": final["last_message_blocked"],
        "possibly_blocked": final["possibly_blocked"] or None,
        "permission_denials": summary["denials"],
        "files_changed_note": FILES_NOTE,
        "tool_calls": summary["tool_calls"],
        "cost_usd": summary["cost_usd"],
        "followups": final["followups"],
        "mode_mismatch": meta.get("mode_mismatch"),
        "profile_exceptions": meta.get("profile_exceptions") or [],
    }
    row = {
        "permission_mode": init.get("permission_mode"),
        "mode_mismatch": meta.get("mode_mismatch"),
        "tasks_open": summary["tasks_open"],
        "permission_denials": summary["denials"],
        "tool_calls": summary["tool_calls"],
        "messages": len(meta.get("messages") or []),
        "unanswered": [m["uuid"] for m in final["unanswered"]],
        "undelivered": meta.get("undelivered") or [],
        "stdin_open": bool(meta.get("stdin_open")) and live,
        "retrying_since": iso(meta.get("retrying_since")),
        "rate_limit": meta.get("rate_limit") or summary["rate_limit"],
        "cost_usd": summary["cost_usd"],
        "profile_exceptions": meta.get("profile_exceptions") or [],
    }
    return {
        "text": text,
        "decided": decided,
        "json": chosen["structured_output"] if schema and chosen and decided else None,
        "schema": schema,
        "files_changed": summary["files_edited"],
        "usage": summary["usage"],
        "last_message": summary["last_message"],
        "in_progress": summary["in_progress"],
        "model": init.get("model") or (meta.get("settings") or {}).get("model"),
        "cwd": init.get("cwd") or meta.get("cwd"),
        "rows": activity_rows(events, rel_to=rel, messages=meta.get("messages") or []),
        "unparsed": unparsed,
        "retrying": summary["last_event_is_retry"] and meta.get("state") != "finishing",
        "writing": writing,
        "rate_limit": meta.get("rate_limit") or summary["rate_limit"],
        "header": header,
        "row": row,
        "_summary": summary,
        "_final": final,
    }


def attention(meta, v, at):
    """Claude's own reasons to look at a run now, beside the stall every engine has: API retries going on too long, a rejected rate limit, a process still alive long after stdin closed, or a turn that ended with no reply and no hook notice."""
    t = timings()
    out = []
    summary, final = v["_summary"], v["_final"]
    state = meta.get("state")
    if state in ("starting", "running", "finishing"):
        if meta.get("retrying_since") and at - meta["retrying_since"] >= t["retry_attention"]:
            out.append({"reason": "retrying", "since": iso(meta["retrying_since"]), "retry": summary["retry"]})
        rl = meta.get("rate_limit") or summary["rate_limit"]
        if rl and rl.get("status") == "rejected":
            out.append({"reason": "rate_limited", "rate_limit": rl})
        if state == "finishing" and meta.get("finishing_since") and at - meta["finishing_since"] >= t["finishing_attention"]:
            out.append({"reason": "finishing", "since": iso(meta["finishing_since"]), "tasks_open": summary["tasks_open"], "note": "the answer is in; the process is still running something after stdin closed"})
    if final["possibly_blocked"]:
        out.append({"reason": "possibly_blocked", "uuids": final["possibly_blocked"], "note": "a turn ended with no reply and no hook notice; a hook may have stopped the message"})
    return out


def log_lines(v):
    lines = render_rows(v["rows"])
    if v["unparsed"]:
        lines.append(f"unparsed lines={v['unparsed']}")
    return lines


# -- models and limits ----------------------------------------------------------

FRESH = 600.0


def _resets(text):
    from datetime import datetime

    try:
        return datetime.fromisoformat(text).timestamp() if isinstance(text, str) else text
    except ValueError:
        return None


def quota_from_usage(report, model=None, at=None):
    """`/usage` limits as the skill reports them, for a run on `model`: exhausted when a limit that applies to it (the account's, or one scoped to its model family) is used up and has not reset."""
    at = at or now()
    if not report or report.get("error"):
        return {"state": "unknown", "scope": None, "limits": [], "observed_at": None, "source": None, "why": (report or {}).get("error") or "no usage report"}
    limits = []
    for item in report.get("limits") or []:
        resets = _resets(item.get("resets_at"))
        if resets and resets <= at:
            continue
        applies = not item.get("scope") or (model and item["scope"].lower() in model.lower())
        limits.append({"kind": item.get("kind"), "used_percent": item.get("percent"), "resets_at": resets, "scope": item.get("scope"), "applies": bool(applies)})
    # With extra usage on, Claude Code goes on past a full plan limit, paid.
    full = [] if report.get("extra_usage_enabled") else [l for l in limits if l["applies"] and (l["used_percent"] or 0) >= 100]
    out = {"state": "exhausted" if full else ("ok" if limits else "unknown"), "scope": "the account's limits" + (f" and those scoped to {model}" if model else ""), "limits": limits, "observed_at": at, "source": "claude -p /usage"}
    if full:
        out["resets_at"] = max(l["resets_at"] for l in full if l["resets_at"]) if any(l["resets_at"] for l in full) else None
    return out


def quota_from_event(rl, at=None):
    """A `rate_limit_event` a run recorded, as the same shape: the account's state when the event came."""
    at = at or now()
    rejected = rl.get("status") == "rejected" and (rl.get("resets_at") or 0) > at
    windows = [{"kind": k, "used_percent": round(rl[k] * 100) if isinstance(rl.get(k), (int, float)) else None} for k in ("five_hour", "seven_day")]
    out = {"state": "exhausted" if rejected else "ok", "scope": "the account's limits", "windows": windows, "observed_at": rl.get("observed_at"), "source": "a run's rate_limit_event"}
    if rejected:
        out["resets_at"] = rl.get("resets_at")
    return out


_QUOTA = {}
# The rate-limit types of a rate_limit_event that are the account's own windows, not one model family's.
ACCOUNT_LIMITS = ("five_hour", "seven_day")


def login_account():
    """The login a run's limits belong to, as far as `claude auth status` tells (its reported fields, never the email or organisation); None when it cannot be read or nobody is logged in."""
    auth = auth_status()
    return None if auth.get("error") or not auth.get("loggedIn") else auth


def quota(model=None, runs_dir=None):
    """The account's limits now: a rate-limit event a run of this registry recorded in the last 10 minutes under the login now in use (its scope is the account's), else `claude -p /usage`, which no model call answers."""
    key = model or ""
    if key in _QUOTA:
        return _QUOTA[key]
    at = now()
    newest, whose = None, None
    if runs_dir:
        for _, m in iter_runs(runs_dir):
            rl = m.get("rate_limit")
            # Only an event about the account's own windows speaks for every model; one about a model-scoped limit leaves the answer to /usage.
            if m.get("engine") == NAME and rl and rl.get("observed_at") and at - rl["observed_at"] <= FRESH and rl.get("type") in (None, *ACCOUNT_LIMITS) and (newest is None or rl["observed_at"] > newest["observed_at"]):
                newest, whose = rl, m.get("account")
    # An event of another login, or of one that cannot be matched, speaks for another account: /usage answers for this one.
    # 성진: the login is matched on what `claude auth status` reports short of the email and organisation, so two accounts on the same sign-in method and plan look alike; match on more if switching between such accounts becomes usual.
    if newest and not (whose and whose == login_account()):
        newest = None
    _QUOTA[key] = quota_from_event(newest, at) if newest else quota_from_usage(usage_report(worker_env(dict(os.environ), {})), model, at)
    return _QUOTA[key]


def models():
    """The aliases `claude -p /model` offers (each the newest of its family) and the efforts claude takes; a full model id (`claude-…`) is accepted too."""
    aliases = model_aliases()
    return {"models": [{"alias": a} for a in aliases] if aliases else None, "efforts": list(EFFORTS), "full_ids": "a full model id starting with claude- is accepted too", "source": "claude -p /model" if aliases else None, "error": None if aliases else "the model list could not be read (`claude -p /model`)"}


# -- doctor -----------------------------------------------------------------


def diagnose(cwd):
    """`{report, blockers, warnings}` for the Claude engine: claude and its version, login (only how, never the account), what the sandboxed profiles need here, and settings in your files that widen the read-only and write profiles."""
    blockers, warnings = [], []
    info = claude_version()
    if info["error"]:
        blockers.append(info["error"])
    elif info["parsed"] < VERSION_FLOOR:
        blockers.append(f"claude {info['version']} is older than {VERSION_FLOOR_TEXT}, which the skill needs; update Claude Code")
    elif info["parsed"] < VERIFIED_VERSION:
        warnings.append(f"claude {info['version']} is older than {_text(VERIFIED_VERSION)}, the version this skill was verified against")
    auth = auth_status() if not info["error"] else {"error": "no claude"}
    if auth.get("error"):
        blockers.append(f"login could not be read: {auth['error']}")
    elif not auth.get("loggedIn"):
        blockers.append("claude is not logged in; run `claude auth login` in a terminal")
    sandbox = sandbox_prereqs()
    if not sandbox["supported"]:
        warnings.append(f"the read-only and write profiles' sandbox is not supported on {sandbox['platform']}; only full can run")
    elif sandbox["missing"]:
        warnings.append(f"the sandbox needs {', '.join(sandbox['missing'])}; read-only and write runs will fail to start until it is installed")
    limits = quota_from_usage(usage_report(worker_env(dict(os.environ), {}))) if not info["error"] else {"state": "unknown", "limits": [], "why": "no claude"}
    if limits.get("why") and not info["error"]:
        warnings.append(f"usage limits could not be read: {limits['why']}")
    for l in limits.get("limits") or []:
        if (l.get("used_percent") or 0) >= 90:
            warnings.append(f"claude {l['kind']} limit at {l['used_percent']}% (resets {iso(l.get('resets_at'))}); every claude worker draws on it")
    top = toplevel(cwd) or str(Path(cwd).resolve())
    roots = [r for r in {toplevel(cwd), main_checkout(cwd)} if r]
    exceptions = {p: profile_exceptions(top, p, project_roots=roots) for p in ("read-only", "write")}
    for p, items in exceptions.items():
        for item in items:
            if item["setting"] in ("sandbox.excludedCommands", "sandbox.enabled", "unreadable settings file"):
                warnings.append(f"claude {p}: {item['setting']} in {item['file']} — {item['effect']}")
    report = {"path": info["path"], "installed": bool(info["path"]), "quota": {**limits, "observed_at": iso(limits.get("observed_at")), "resets_at": iso(limits.get("resets_at"))}, "version": info["version"], "floor": VERSION_FLOOR_TEXT, "verified": _text(VERIFIED_VERSION), "auth": auth, "sandbox": sandbox, "profile_exceptions": exceptions}
    return {"report": report, "blockers": blockers, "warnings": warnings}

