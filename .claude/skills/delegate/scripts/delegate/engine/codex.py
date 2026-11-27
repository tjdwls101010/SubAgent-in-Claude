"""The Codex engine: one `codex exec` turn per run, stdin closed, the prompt after `--`, the final message in `last-message.txt`, and a thread id that Codex reports only once it has started. Owns how a turn's settings are resolved and re-asserted (`codex exec resume` takes neither `-s` nor `-C`, so the sandbox travels as `-c` entries and the directory is the process's), which read-only a run gets (`scratch` writes TMPDIR and ~/.cache, `strict` cannot write even those), isolation from the user's config.toml, and how its events read as a header, a status row and log lines."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from delegate.claude_cli import PARENT_IDENTITY
from delegate.codex_cli import ISOLATION_FLOOR_TEXT, apply_preamble, build_argv, changed_paths, check_model_effort, codex_home, codex_support, config_summary, event_lines, first_thread_id, login_status, model_catalog, read_only_blocker, refuse_without_isolation, scan_progress, stderr_tail
from delegate.errors import Refusal
from delegate.git import add_detached, toplevel
from delegate.registry import TERMINAL, registry_args, update_meta, worker_alive
from delegate.util import clip, invocation, nfc, now

NAME = "codex"
# Whether a run can ask for the priority service tier (`--priority`).
TAKES_PRIORITY = True
STDIN = "devnull"
SANDBOX = {"read-only": "read-only", "write": "workspace-write", "full": "danger-full-access"}

# Why a read-only run keeps the stricter sandbox when it is not new and does not ask for read-only again.
KEPT_STRICT = "this thread recorded the stricter read-only, which cannot write even a temporary file, and keeps it: tests and builds that need one fail. `resume --profile read-only` moves it to the read-only that writes TMPDIR and ~/.cache"


# -- settings -----------------------------------------------------------------


def read_schema(path, command):
    p = Path(path).expanduser()
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise Refusal(f"--schema {path}: {e.strerror or e}", arguments=True, help=f"{command} --help")
    except ValueError as e:
        raise Refusal(f"--schema {path} is not JSON: {e}", arguments=True, help=f"{command} --help")
    if not isinstance(obj, dict):
        raise Refusal(f"--schema {path} must hold a JSON object", arguments=True, help=f"{command} --help")
    return json.dumps(obj, ensure_ascii=False)


def pin_codex_home():
    """CODEX_HOME as one absolute path for every codex this process starts — the version and catalog checks, login, and the worker — so they all read the same home (a `~` or relative path would differ by directory or shell)."""
    if os.environ.get("CODEX_HOME"):
        os.environ["CODEX_HOME"] = str(Path(os.environ["CODEX_HOME"]).expanduser().resolve())


def settings(flags, record=None, *, command="start", cwd=None):
    """The settings for one turn: each from the flags, else from the thread's last recorded run, and for a new thread or one the registry never saw from the flags alone — model and effort included, since an isolated run reads no config.toml and the skill has no default. A model or effort taken up now is checked against this install's catalog (when it can be read); one inherited from the thread is not, so a model retired upstream cannot block the resume of an old thread. `cwd` is where the run will work, which decides whether a read-only run can write scratch."""
    pin_codex_home()
    record = record or {}
    resume = bool(flags.get("resume"))
    out = {"engine": NAME}
    for key in ("profile", "model", "effort"):
        out[key] = flags.get(key) or record.get(key)
    if not out["profile"]:
        if resume and not record:
            raise Refusal("this thread is not in the registry, so its sandbox was never recorded; pass --profile, which is recorded from then on", arguments=True, help="resume --help")
        out["profile"] = "write"
    for key in ("model", "effort"):
        if not out[key]:
            why = "this thread is not in the registry and an isolated run reads no config.toml, so its" if resume else "there is no default"
            raise Refusal(f"--{key} is required: {why} {key}; `codex debug models` lists them", arguments=True, help=f"{command} --help")
    if out["profile"] not in SANDBOX:
        raise Refusal(f"unknown profile {out['profile']!r}", arguments=True, help=f"{command} --help")
    adopted_model = flags.get("model")
    adopted_effort = flags.get("effort")
    if adopted_model or adopted_effort:
        catalog = model_catalog()
        model = adopted_model
        if not model and catalog and out["model"] in {m["slug"] for m in catalog}:
            # A new effort on an inherited model is checked against that model's own efforts; an inherited model gone from the catalog is not checked at all.
            model = out["model"]
        try:
            check_model_effort(model, adopted_effort or (out["effort"] if adopted_model else None), catalog=catalog)
        except Refusal as e:
            raise Refusal(e.message, arguments=True, help=f"{command} --help", **{k: v for k, v in e.fields.items() if k != "hint"}, hint="`codex debug models` lists each model with its efforts") from None
    out["sandbox"] = SANDBOX[out["profile"]]
    # A thread recorded as loading config.toml (before isolation existed) keeps loading it; every other run is isolated.
    out["isolated"] = record.get("isolated", True) if record else True
    if flags.get("priority"):
        out["service_tier"] = "priority"
    else:
        out["service_tier"] = record.get("service_tier") if record else None
    out["read_only"] = out["read_only_note"] = None
    if out["sandbox"] == "read-only":
        if not out["isolated"]:
            out["read_only"], out["read_only_note"] = "strict", "this thread loads your config.toml, which could change the read-only profile, so it keeps the stricter read-only, which cannot write even a temporary file: tests and builds that need one fail"
        elif record and record.get("read_only") != "scratch" and flags.get("profile") != "read-only":
            out["read_only"], out["read_only_note"] = "strict", KEPT_STRICT
        else:
            blocker = read_only_blocker(cwd or os.getcwd())
            out["read_only"], out["read_only_note"] = ("strict", blocker) if blocker else ("scratch", None)
    out["schema_text"] = read_schema(flags["schema"], command) if flags.get("schema") else record.get("schema_text")
    out["changed"] = {k: record[k] for k in ("profile", "model", "effort") if record and record.get(k) and record.get(k) != out[k]}
    return out


def check_installed():
    pin_codex_home()
    if not shutil.which("codex"):
        raise Refusal("codex is not on PATH; install the Codex CLI")
    refuse_without_isolation()


def new_native_id():
    """A Codex thread id is chosen by Codex and reported after launch."""
    return None


def check_native_id(ref):
    """A thread id or a thread name, as `codex exec resume` takes it; a word starting with `-` would be read as a flag."""
    if not ref or not ref.strip() or ref.startswith("-"):
        raise Refusal(f"{ref!r} cannot name a Codex thread", arguments=True, help="resume --help")


def discover_native_id(run_dir):
    """The thread id a run's events name (`thread.started`, the first line Codex writes), for a run whose meta does not say."""
    return first_thread_id(Path(run_dir) / "events.jsonl")


def unregistered_note():
    return "this thread was not in the registry; it now runs under the profile, model and effort you gave, recorded from now on"


def worktree_plan(repo, name, base, main):
    """A group member's checkout as this skill cuts it: detached at the run's own directory (`<run>/wt`, inside the registry, which git ignores), from `base`, when the run is published — tracked files at that commit and nothing else."""
    return {"engine": NAME, "name": name, "path": None, "branch": None, "base": base, "repo": repo, "main": main}


def worktree_taken(plan):
    return None


def prepare(meta, prompt, *, group_info=None, worktree_info=None, origin=None):
    """(argv, None): the prompt travels in the argv, behind the paragraphs that tell Codex it is a non-interactive turn (and, for a group member, which group and which checkout). A fresh group member's checkout is cut here, under the publish lock, so it exists from the moment the run is published and `clean` cannot remove it in between."""
    s = meta["settings"]
    run_dir = Path(meta["run_dir"])
    wt = meta.get("worktree")
    if wt and meta["kind"] == "start" and not wt.get("path"):
        path = run_dir / "wt"
        ok, err = add_detached(wt["repo"], path, wt["base"])
        if not ok:
            raise Refusal(f"could not create the checkout for this member: {err}", base=wt["base"], path=str(path))
        meta["worktree"] = {**wt, "path": str(path)}
        meta["cwd"] = meta["launch_cwd"] = str(path)
        if worktree_info is not None:
            worktree_info = {**worktree_info, "path": str(path)}
    schema_path = None
    if s.get("schema_text") is not None:
        schema_path = run_dir / "schema.json"
        schema_path.write_text(s["schema_text"], encoding="utf-8")
    batch = None
    if group_info:
        batch = {"n": group_info["size"], "group": group_info["name"]}
        if worktree_info:
            batch.update({"worktree": worktree_info["path"], "base": worktree_info["base"], "uncommitted": worktree_info["uncommitted"]})
    spec = {"run_dir": str(run_dir), "cwd": meta["cwd"], "sandbox": s["sandbox"], "read_only": s.get("read_only"), "isolated": s.get("isolated", True), "service_tier": s.get("service_tier"), "model": s.get("model"), "effort": s.get("effort"), "schema_path": str(schema_path) if schema_path else None, "skip_git_repo_check": toplevel(meta["cwd"]) is None}
    argv = build_argv(spec, kind=meta["kind"], prompt=apply_preamble(prompt, batch=batch), thread_ref=meta.get("native_id") if meta["kind"] == "resume" else None)
    return argv, None


def extra_meta(cwd, s):
    return {}


def after_publish(run_dir, text, prompt):
    return None


def started(meta):
    """Codex has taken the turn: this run's events opened with `thread.started` (a resume repeats it with the same id), which the driver records."""
    return bool(meta.get("thread_started"))


def not_started_note():
    return "codex has not reported a thread id yet; `status --run` shows it once it does, and a run without one cannot be resumed yet"


def reply_fields(meta):
    s = meta["settings"]
    out = {"sandbox": s["sandbox"], "isolated": s.get("isolated", True)}
    if s.get("read_only"):
        out["read_only"] = s["read_only"]
    if s.get("read_only_note"):
        out["read_only_note"] = s["read_only_note"]
    if s.get("service_tier"):
        out["service_tier"] = s["service_tier"]
    return out


def live_instead(run_id, meta, runs_dir):
    """A Codex turn cannot be steered while it runs: wait for it, or stop it and resume."""
    extra = registry_args(runs_dir)
    return {"instead": invocation("result", "--run", run_id, "--wait", *extra), "or": f"{invocation('stop', '--run', run_id, *extra)}, then resume", "why": "two turns on one thread would append to one rollout; this skill's codex runs take no message mid-turn"}


def is_worker_command(command):
    return "codex" in (command or "") and "exec" in (command or "")


def worker_env(base_env, markers):
    """The supervisor's environment without the calling Claude session's identity, plus the worker markers. CODEX_HOME is made absolute: Codex runs in the run's directory, where a relative one would name another place."""
    env = {k: v for k, v in base_env.items() if k not in PARENT_IDENTITY}
    if env.get("CODEX_HOME"):
        env["CODEX_HOME"] = str(Path(env["CODEX_HOME"]).expanduser().resolve())
    env.update(markers)
    return env


# -- the supervisor's driver --------------------------------------------------


class Driver:
    """What the supervisor asks of a Codex run: record the thread id once `thread.started` names it, mark the run running at its first event, and conclude the outcome from the exit code and the turn's events."""

    def __init__(self, run_dir, meta, proc):
        self.run_dir = run_dir
        self.meta = meta
        self.proc = proc
        self.pump = None
        self.thread_id = None
        self.recorded = False
        self.running = False
        self.events = 0

    def bind(self, pump):
        self.pump = pump

    def feed(self, e):
        self.events += 1
        if e and e.get("type") == "thread.started" and e.get("thread_id") and not self.thread_id:
            self.thread_id = e["thread_id"]

    def after_feed(self, e):
        return None

    def tick(self):
        with self.pump.lock:
            tid, seen = self.thread_id, self.events
        changes = {}
        if tid and not self.recorded:
            self.recorded = True
            changes.update({"native_id": tid, "thread_started": True})
        if seen and not self.running:
            self.running = True
            changes["state"] = "running"
        if changes:
            update_meta(self.run_dir, lambda m: m.update({k: v for k, v in changes.items() if k != "state" or m.get("state") not in TERMINAL}))

    def outcome(self, meta, returncode, stderr_text):
        info = scan_progress(Path(self.run_dir) / "events.jsonl", terminal=True)
        fields = {"exit_code": returncode, "ended_at": now(), "unparsed_events": info["unparsed_events"]}
        if info["thread_id"]:
            # The id Codex confirmed, which replaces a thread name the turn was asked to resume.
            fields.update({"thread_started": True, "native_id": info["thread_id"]})
        failed = info["turn_failed"]
        if returncode == 0 and not failed and info["thread_id"]:
            fields.update({"state": "completed", "error": None})
        elif returncode == 0 and not failed:
            fields.update({"state": "failed", "error": "codex exited without starting a thread"})
        else:
            message = (failed or {}).get("message") if isinstance(failed, dict) else None
            tail = stderr_tail(Path(self.run_dir) / "stderr.log")
            error = message or (f"codex exited {returncode}" + (f": {tail.splitlines()[-1][:300]}" if tail else ""))
            fields.update({"state": "failed", "error": error})
        # Without the notice Codex always prints, so the supervisor does not record raw stderr in its place.
        fields["stderr_tail"] = tail[-600:] if (tail := stderr_tail(Path(self.run_dir) / "stderr.log")) else None
        return fields


def driver(run_dir, meta, proc):
    return Driver(run_dir, meta, proc)


# -- reading a run --------------------------------------------------------------


def _final(run_dir):
    path = Path(run_dir) / "last-message.txt"
    return path.read_bytes() if path.exists() else None


def view(run_dir, meta):
    """The run's events and final-message file read in the skill's terms; keys as `claude.view_of` gives them."""
    run_dir = Path(run_dir)
    live = meta.get("state") not in TERMINAL
    writing = worker_alive(meta)
    info = scan_progress(run_dir / "events.jsonl", terminal=not live and not writing)
    raw = _final(run_dir)
    # An orphan whose codex still runs has no final answer yet, whatever its recorded state.
    decided = not live and not writing
    schema = (meta.get("settings") or {}).get("schema_text") is not None
    text = raw.decode("utf-8", "replace") if raw is not None else (info["last_agent_message"] or "")
    parsed, parse_error = None, None
    if schema and decided and text:
        try:
            parsed = json.loads(raw.decode("utf-8") if raw is not None else text)
        except (ValueError, UnicodeDecodeError) as e:
            parse_error = str(e)
    cwd = (meta.get("worktree") or {}).get("path") or meta.get("cwd")
    files = []
    for p in sorted(changed_paths(run_dir / "events.jsonl")):
        try:
            files.append(str(Path(nfc(p)).relative_to(nfc(cwd))) if cwd else p)
        except ValueError:
            files.append(p)
    s = meta.get("settings") or {}
    turn_failed = clip(json.dumps(info["turn_failed"], ensure_ascii=False), 400) if info["turn_failed"] else None
    header = {"turn_failed": turn_failed, "commands": info["commands"], "sandbox": s.get("sandbox"), "read_only": s.get("read_only"), "isolated": s.get("isolated")}
    if parse_error:
        header["parse_error"] = parse_error
    row = {"sandbox": s.get("sandbox"), "read_only": s.get("read_only"), "read_only_note": s.get("read_only_note"), "isolated": s.get("isolated"), "service_tier": s.get("service_tier"), "turns_completed": info["turns_completed"], "commands": info["commands"], "turn_failed": turn_failed}
    tail = stderr_tail(run_dir / "stderr.log")
    if tail:
        # Codex reports configuration and connection trouble here even on a turn that completes.
        row["stderr_tail"] = tail
    in_progress = [info["in_progress_item"]] if info["in_progress_item"] and live else []
    return {
        "text": text,
        "decided": decided,
        "json": parsed,
        "schema": schema,
        "files_changed": files,
        "usage": info["usage"] or {},
        "last_message": info["last_agent_message"],
        "in_progress": in_progress,
        "model": s.get("model"),
        "cwd": meta.get("cwd"),
        "lines": event_lines(run_dir / "events.jsonl", Path(cwd) if cwd else None),
        "unparsed": info["unparsed_events"],
        "retrying": False,
        "writing": writing,
        "rate_limit": None,
        "header": header,
        "row": row,
        "native_id": info["thread_id"],
    }


def attention(meta, v, at):
    """Codex's own reasons to look at a run, beside the stall every engine has: none — `codex exec --json` reports no retries and no rate-limit state while a turn runs."""
    return []


def log_lines(v):
    return list(v["lines"])


# -- doctor -----------------------------------------------------------------


def diagnose(cwd):
    """`{report, blockers, warnings}` for the Codex engine: the codex on PATH and its version against what isolated and read-only runs need, `codex login status`, CODEX_HOME, and what config.toml sets (which an isolated run does not read)."""
    pin_codex_home()
    blockers, warnings = [], []
    path = shutil.which("codex")
    if not path:
        return {"report": {"path": None, "installed": False}, "blockers": ["codex is not on PATH"], "warnings": []}
    support = codex_support()
    if support["isolation"] is False:
        blockers.append(f"codex {support['version']} is older than {ISOLATION_FLOOR_TEXT}, the first release with --ignore-user-config, which every run passes")
    elif support["version"] is None:
        warnings.append("`codex --version` could not be read, so whether this install isolates runs is unknown")
    blocker = read_only_blocker(toplevel(cwd) or cwd)
    if blocker:
        warnings.append(f"read-only runs here get the stricter sandbox: {blocker}")
    login = login_status()
    if login["ok"] is False:
        if login["cause"] == "environment":
            blockers.append(f"`codex login status` failed on an environment or config problem, not an auth one: {login['detail']}")
        else:
            blockers.append("codex is not logged in; run `codex login` in a terminal")
    elif login["ok"] is None:
        warnings.append(f"login could not be checked: {login['detail']}")
    report = {"path": path, "installed": True, "version": support["version"], "isolation": support["isolation"], "read_only_scratch": not blocker, "login": login, "codex_home": str(codex_home()), "codex_home_from_env": bool(os.environ.get("CODEX_HOME")), "config": config_summary()}
    return {"report": report, "blockers": blockers, "warnings": warnings}
