# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Drive workers on two engines — GPT through the Codex CLI, Claude through headless Claude Code — as managed subagents. This file is the whole command surface — every flag and help text, each command's `Prints:` and `Exits:`, the checks made from the arguments alone, dispatch into the `delegate` package, and rendering — and the entry point a detached supervisor re-executes. What a command does lives in `delegate/`.

Exit codes:
    0  success
    1  the registry's state refused the command, or what it asked for failed; the reply carries `error`
    2  the command line itself must change; the reply carries `error` and the `help` to read
    3  `doctor` found a blocker
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from delegate.doctor import doctor
from delegate.engine import ENGINES, PROFILES, supervise
from delegate.errors import Refusal
from delegate.observe import LISTING_ROWS, STALL_SECONDS, log, result, status
from delegate.runs import refuse_inside_worker, resume, start, stop

EXIT_REFUSED, EXIT_ARGUMENTS, EXIT_BLOCKED = 1, 2, 3

ROOT_DESC = "Start workers on either of two engines in the background and manage them like subagents: a handle at once, progress, stopping, continuing, and the result — text or schema-shaped JSON — when it is ready."

ROOT_EPILOG = """\
Engines: `codex` (GPT through the Codex CLI, `codex exec`) and `claude` (headless Claude Code, `claude -p`). There is no default engine, model or effort: a new run names all three.
Every command prints one JSON line on stdout — its result, or a refusal carrying `error` — unless its --help says it prints text.
Exit codes: 0 success; 1 the registry's state refused the command, or what it asked for failed, and `error` says why (with `instead` when there is another way); 2 the command line must change, and the reply's `help` names the --help to read; 3 `doctor` found a blocker.
Runs of every engine live in .delegate-runs/ of the repository's main checkout (outside git, of the current directory); --project or --runs-dir point a command at another registry."""

PROFILE_HELP = """what the worker may do (default write). The names are shared by every engine; what each allows there:
  read-only  codex: reads anything and runs tests and builds, writing only TMPDIR and ~/.cache, network off (`read_only: scratch`); where that cannot hold — an old codex, a --cwd that is itself TMPDIR, a thread recorded before it — `read_only: strict`, which cannot write even a temporary file, and `read_only_note` says why. claude: reads anything, and runs any shell command inside a sandbox that can write only its TMPDIR and ~/.cache and reach only hosts your settings allow (none by default) — so it can run tests and builds and bring back what they printed. No edit tools. Hooks, MCP tools and WebFetch run outside the sandbox under your permission rules.
  write      codex: `workspace-write` — writes in its directory, network off. claude: edits and shell writes inside the working directory, any shell command inside the same sandbox and network as read-only; edits outside it are denied and listed in `permission_denials`. Claude Code also denies edits under .claude/skills/ and .claude/plans in this profile.
  full       codex: `danger-full-access` — no sandbox: it writes anywhere and reaches the network. claude: Claude Code's auto mode — its classifier reviews each call, network and writes outside the directory are possible (also under .claude/skills/), and a call it blocks is a denial; the skill adds no sandbox. A model without auto mode starts in another mode and the reply says `mode_mismatch`.
For claude the profile is a restriction laid over your own Claude Code settings, not a replacement: settings that widen it are listed in the reply's `profile_exceptions`."""

ENGINE_HELP = "the engine to run on: codex (GPT through the Codex CLI) or claude (headless Claude Code). Required — there is no default engine"
MODEL_HELP = "the model, as the engine names it: for codex a slug from its catalog (`codex debug models`), checked before the run starts when the catalog can be read; for claude an alias or a full model id that `claude -p /model` accepts. A model in the catalog can still be one your account cannot use: its first turn fails with the engine's error. Required for a new run — there is no default model"
EFFORT_HELP = "the reasoning effort, as the engine takes it: codex the efforts the model's catalog entry lists; claude low, medium, high, xhigh or max. Required for a new run — there is no default effort"
PRIORITY_HELP = "codex only: request service_tier \"priority\" (Fast mode), recorded for the thread's later turns; any other engine refuses it"

RUN_EPILOG = """\
Answers once the engine confirms the start (codex: its first event, with its thread id; claude: its first init), or after 15 s with `state: starting` and a `note`, so a start that fails at once — not logged in, a bad flag, an engine too old — says so here. A detached supervisor drives the run, so it outlives this command and nothing announces its end: run the reply's `next.command` in the background; it returns when the run ends or needs attention, and prints the result.
A codex worker is isolated: Codex does not load your config.toml, so no MCP servers, plugins, agent roles or hooks, and the run's model and effort are only what you name. A claude worker inherits your whole Claude Code environment — CLAUDE.md files, skills, hooks, plugins, MCP servers, memory — under the profile's restrictions, with background tasks off so its turn ending is the answer (a tool still running after it is shown as `finishing` until the process exits), and without the tools that start or message other agents. Every worker is told nobody can answer it, that its final message is what you receive, to leave git commits, branches, pushes and merges to you unless the prompt asks, and that it cannot start workers: inside a worker this skill refuses to start, stop or resume runs."""

START_DESC = """\
Start a fresh worker on PROMPT, detached.
Prints: one JSON line — `run_id`, `engine`, `native_id` (the engine's own id for the conversation: a Codex thread id, null until Codex reports it, or a Claude session id), `state`, `next` (the `result --run <id> --wait` call to run in the background), `profile`, `model`, `effort`, `cwd`, `concurrent_writers` (other live runs of any engine that can write in or around the same directory), `events` (the raw event file), and the engine's own fields — codex: `sandbox`, `isolated`, `read_only` and `read_only_note` for a read-only run, `service_tier`; claude: `permission_mode` (what claude reported), `mode_mismatch`, `profile_exceptions`; `error` and `stderr_tail` instead of `next` when the run failed while starting.
Exits: 0 started; 1 refused or failed — run from inside a worker, the engine missing or too old, or the run failed before the engine confirmed it (`error` says why); 2 the command line must change — no --engine, --model or --effort, a model or effort the engine does not take (codex: not in the catalog), --priority for an engine other than codex, no prompt or an empty one, an unreadable --prompt-file or --schema, a --cwd that is not a directory, or anything else the parser refuses."""

RESUME_DESC = """\
Run another turn on an existing session or thread, detached: a new run with its own events, under the profile, model, effort and schema its last run recorded, except where a flag changes them — every turn re-sends them, because a resumed `claude -p` restores only the model and `codex exec resume` takes no sandbox or directory flag. Continue when the new work builds on what the worker worked out; start fresh when it does not. A conversation does not cross engines: to hand work to the other engine, start a fresh run there in the same --cwd and put what `result` printed for the previous run into its prompt.
For a session the registry never saw, give --engine, --profile, --cwd (the directory it ran in: Claude Code finds a session by its project directory; for a session that ran in a Claude Code worktree, the directory it was launched from and the worktree both work, and claude re-enters the worktree) and, for codex, --model and --effort (an isolated run reads no config.toml), for claude --effort (a resumed session does not keep it: without one it runs at another effort than it had); claude keeps the session's model unless --model changes it.
Prints: one JSON line as `start` prints, plus `profile_changed_from` when --profile changed it, and `note` for a session the registry never saw.
Exits: 0 started; 1 refused or failed — the session has a live run (`instead` says what to do), run from inside a worker, the engine missing or too old, a run with no native id, or the run failed before the engine confirmed it (a session the engine does not know fails here); 2 the command line must change — no REF, no prompt or an empty one, an --engine other than the one the run recorded (`instead` gives the two commands that hand the work over), an unknown REF without --engine, a session or thread the registry never saw without --profile, --cwd, (codex) --model and --effort or (claude) --effort, a model or effort the engine does not take, or anything else the parser refuses."""

STATUS_DESC = f"""\
What the registry knows: every live run plus the {LISTING_ROWS} newest, or one run in full.
Prints: one JSON line. With no selector: `registry`, `live` (run ids), `runs` (a short row each: `run_id`, `engine`, `native_id`, `state`, `label`, `group`, `profile`, `model`, `idle_seconds`, `error`, `attention`, `last_message`), `runs_truncated`. With --run: that run's full row — the above plus `stored_state`, `kind`, `effort`, `cwd`, `worktree`, `created`, `ended`, `elapsed_seconds`, `exit_code`, `in_progress` (calls without a result yet), `files_changed`, `usage`, `unparsed_events`, `stderr_tail` for a failure, `still_writing` when the worker outlived its supervisor, `events`, and the engine's own — codex: `sandbox`, `read_only`, `read_only_note`, `isolated`, `service_tier`, `turns_completed`, `commands`, `turn_failed`; claude: `permission_mode`, `mode_mismatch`, `tasks_open`, `permission_denials`, `tool_calls`, `messages`, `unanswered`, `undelivered`, `stdin_open`, `retrying_since`, `rate_limit`, `cost_usd`, `profile_exceptions`.
Exits: 0 answered; 1 refused — no such run, an ambiguous prefix, or a meta.json that does not parse."""

STATUS_EPILOG = f"""\
States:
  starting     published; the engine has not confirmed the start (codex: no thread id yet)
  running      the worker has the turn
  retrying     claude: running, and the last event is an API retry (`retrying_since` says since when)
  stalled      starting, running or finishing with no event for {STALL_SECONDS} s; advisory, nothing is killed — read `in_progress` beside it
  finishing    claude: the answer is in and stdin is closed, but claude is still running something
  completed    ended: the last answer succeeded
  failed       ended: the engine exited non-zero, the answer is an error, or (claude) a hook ended the only message (`error` says which)
  interrupted  ended: stopped
  timed_out    ended: its --timeout passed
  orphaned     ended: its supervisor died without recording an outcome; `still_writing` says the worker is still running
`attention` lists what needs a look now: `stalled`; claude also `retrying` for 120 s, `rate_limited` (a rejected rate limit), `finishing` for 120 s, `possibly_blocked`."""

LOG_DESC = """\
What one run did, read from its events.
Prints: text, one line per event that matters, in the engine's own terms — codex: `thread <id>`, `turn.started`, `cmd exit=<n> out=<bytes>B <command>` with the head and tail of the output indented beneath a failure, `cmd.running <command>` while one runs, `msg <text>` whole, `file <kind> <path>`, `search <query>`, `mcp <server>/<tool> status=<s>`, `error <message>`, `turn.completed in=… cached=… out=… reasoning=…`, `turn.failed <error>`; claude: `init model=… mode=… cwd=…` (again when either changes), `msg <text>` with the whole message, `bash exit=<n> out=<bytes>B <command>` with the head and tail of the output indented beneath a failure, `edit|write|read <path>`, `grep|glob <pattern>`, `fetch <url>`, `search <query>`, `skill <name>`, `agent <type> <description> tools=<n> out=<bytes>B`, `mcp <server>/<tool> ok|error`, `denied <tool> <input>`, `sent <priority> <message>`, `task <status> <description>`, `retry attempt=<a>/<max> error=<kind> status=<http>`, `ratelimit <status> 5h=<%> 7d=<%>` when the status changes, `compact`, `result <subtype> turns=<n> terminal=<reason> cost=$<x>` (with `origin=task-notification` for a background follow-up), `running <tool> <summary>` for calls still without a result — then `run=<id> state=<state>`.
Exits: 0 printed; 1 refused — no such run; 2 the command line must change — no --run."""

RESULT_DESC = """\
What a run concluded: at once (partial while live), or with --wait once it ends or needs attention.
Prints: a JSON header line — `run_id`, `engine`, `native_id`, `state`, `exit_code`, `error`, `attention`, `files_changed`, `usage`, the engine's own fields — codex: `turn_failed`, `commands`, `sandbox`, `read_only`, `isolated`, `parse_error` when a --schema run's final message is not JSON; claude: `answered` (the messages the answer replies to), `unanswered`, `last_message_unanswered`, `answer_unconfirmed` (claude did not say which message it answered), `blocked_by_hook`, `last_message_blocked`, `possibly_blocked`, `permission_denials`, `files_changed_note`, `tool_calls`, `cost_usd`, `followups` (background follow-up turns, not the answer), `mode_mismatch`, `profile_exceptions` — then `message_bytes`, and `note` and `next` while the run is live; then the final message. A --schema run prints one indented JSON document instead, whose `json` is the structured output (null while starting or running; null with `message` holding the final text when the run ended without one). Once a claude run is `finishing` its answer is decided: the text and `json` are the answer, and `note` says claude is still running something.
Exits: 0 printed, whatever the run's state, also when --wait-timeout passes; 1 refused — no such run; 2 the command line must change — no --run, --wait-timeout without --wait or not positive."""

RESULT_EPILOG = f"""\
--wait returns when the run ends, or early when attention appears that was not there when it began: no event for {STALL_SECONDS} s (`stalled`), and for claude API retries for 120 s (`retrying`), a rejected rate limit (`rate_limited`), claude still running 120 s after the answer (`finishing`), or a turn that ended with no reply and no hook notice (`possibly_blocked`). The header's `attention` says which, with what the run was doing; its `next` waits again for something new. Nothing is stopped for it.
`files_changed` lists the paths the engine's events say it changed (codex: its file_change events, relative to the run's directory; claude: Edit, Write and NotebookEdit calls); files a shell command wrote are not in it.
A codex run's final message is the file Codex wrote at the end (`-o`), or while it runs the latest agent message."""

STOP_DESC = """\
Stop runs through their recorded process groups, never by name: SIGINT, then SIGTERM after 10 s, then SIGKILL 3 s later. SIGINT first lets the engine end the turn so the session or thread can be resumed.
Prints: one JSON line — `stopped`, an entry per run with `run_id`, `engine`, `native_id`, `signals_sent` and `state` (`interrupted` for a run that was live; one that had ended keeps its state, with `note`).
Exits: 0 every run was tried; 1 refused — run from inside a worker, or no such run; 2 the command line must change — no --run."""

DOCTOR_DESC = """\
The environment a run would start in, checked without starting a worker: Python; per engine its CLI on PATH and version against the floor the skill needs, login, what its sandboxed profiles need on this platform, and settings in your files that widen them; and the registry with its live runs.
Prints: one JSON line — `ok`, `blockers`, `warnings` (each prefixed with its engine), `python`, `engines` (per engine `ok`, `blockers`, `warnings` and its report — codex: `path`, `installed`, `version`, `isolation`, `read_only_scratch`, `login` (`ok`, `cause`, `detail`), `codex_home`, `codex_home_from_env`, `config` (`path`, `sandbox_mode`, `approval_policy` — which runs do not read); claude: `path`, `version`, `floor`, `verified`, `auth` (`loggedIn`, `authMethod`, `subscriptionType`, `apiProvider`), `sandbox` (`platform`, `supported`, `missing`), `profile_exceptions` per profile), `skill_dir`, `entry`, `invocation` (the command form the pre-approval matches), `registry` (`path`, `exists`, `writable`, `live_runs`).
Exits: 0 no blocker; 3 a blocker would stop every run of an engine checked — its CLI too old or not logged in, Python below 3.11, a registry that cannot be written; an engine whose CLI is not installed is a blocker only when --engine names it, and a warning otherwise; 2 an argument the parser refuses."""


class OneLinePerParagraph(argparse.HelpFormatter):
    """Each help paragraph and list item as one physical line, never re-wrapped; suppressed subcommands left out of the listing."""

    def _split_lines(self, text, width):
        return text.splitlines() or [""]

    def _fill_text(self, text, width, indent):
        return "\n".join(indent + line for line in text.splitlines())

    def _iter_indented_subactions(self, action):
        for sub in super()._iter_indented_subactions(action):
            if sub.help != argparse.SUPPRESS:
                yield sub

    def _format_action(self, action):
        if isinstance(action, argparse._SubParsersAction):
            action.metavar = "{" + ",".join(n for n, p in action.choices.items() if n in VISIBLE) + "}"
        return super()._format_action(action)


VISIBLE = ("start", "resume", "status", "log", "result", "stop", "doctor")


class ArgumentError(Exception):
    def __init__(self, message, command):
        super().__init__(message)
        self.command = command


class Parser(argparse.ArgumentParser):
    def error(self, message):
        name = self.prog.split()[-1] if " " in self.prog else None
        raise ArgumentError(message, name if name in VISIBLE or name == "__supervise" else None)


def positive(text):
    import math

    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number")
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a positive, finite number")
    return value


def add_registry(p):
    p.add_argument("--project", metavar="DIR", help="resolve the registry (and, for start, the default working directory) from DIR instead of the current directory")
    p.add_argument("--runs-dir", metavar="DIR", help="use this registry directory as is")


def build_parser():
    root = Parser(prog="cli.py", description=ROOT_DESC, epilog=ROOT_EPILOG, formatter_class=OneLinePerParagraph)
    sub = root.add_subparsers(dest="command", metavar="COMMAND", parser_class=Parser)

    p = sub.add_parser("start", usage="cli.py start [PROMPT | --prompt-file FILE] --engine E --model M --effort E [--profile P] [--schema FILE] [options]", help="start a fresh worker, detached", description=START_DESC, epilog=RUN_EPILOG, formatter_class=OneLinePerParagraph)
    p.add_argument("prompt", nargs="?", metavar="PROMPT", help="what the worker should do; or give --prompt-file, or pipe it on stdin")
    p.add_argument("--prompt-file", metavar="FILE", help="read the prompt from FILE")
    p.add_argument("--engine", choices=tuple(ENGINES), help=ENGINE_HELP)
    p.add_argument("--model", help=MODEL_HELP)
    p.add_argument("--effort", help=EFFORT_HELP)
    p.add_argument("--profile", choices=PROFILES, help=PROFILE_HELP)
    p.add_argument("--schema", metavar="FILE", help="a JSON Schema file: the run's answer is structured output of that shape, which `result` prints as JSON")
    p.add_argument("--priority", action="store_true", help=PRIORITY_HELP)
    p.add_argument("--timeout", type=positive, metavar="SEC", help="stop the run after SEC seconds and record it timed_out")
    p.add_argument("--label", help="a short name that goes into the run id and listings")
    p.add_argument("--cwd", metavar="DIR", help="the worker's working directory (default: the top level of the current git checkout, or the current directory)")
    add_registry(p)

    p = sub.add_parser("resume", usage="cli.py resume REF [PROMPT | --prompt-file FILE] [--profile P] [--model M] [--effort E] [--schema FILE] [options]", help="run another turn on a session or thread, detached", description=RESUME_DESC, epilog=RUN_EPILOG, formatter_class=OneLinePerParagraph)
    p.add_argument("ref", metavar="REF", help="a run id, a native id (session or thread), or a unique prefix of either; or the native id of one this registry never saw (then see above)")
    p.add_argument("prompt", nargs="?", metavar="PROMPT", help="what the worker should do next; or give --prompt-file, or pipe it on stdin")
    p.add_argument("--prompt-file", metavar="FILE", help="read the prompt from FILE")
    p.add_argument("--engine", choices=tuple(ENGINES), help="the engine of a session this registry never saw; for a known run it must be the one recorded")
    p.add_argument("--profile", choices=PROFILES, help="change the profile from this turn on (see start --help); required for a session the registry never saw")
    p.add_argument("--model", help="change the model from this turn on")
    p.add_argument("--effort", help="change the effort from this turn on (see start --help)")
    p.add_argument("--schema", metavar="FILE", help="a JSON Schema file for this and later turns' answers")
    p.add_argument("--priority", action="store_true", help=PRIORITY_HELP)
    p.add_argument("--timeout", type=positive, metavar="SEC", help="stop this run after SEC seconds and record it timed_out")
    p.add_argument("--label", help="a short name for this run (default: the last run's label)")
    p.add_argument("--cwd", metavar="DIR", help="the directory to resume in (default: where it last ran); required for a session the registry never saw")
    add_registry(p)

    p = sub.add_parser("status", usage="cli.py status [--run REF]", help="list runs, or show one run", description=STATUS_DESC, epilog=STATUS_EPILOG, formatter_class=OneLinePerParagraph)
    p.add_argument("--run", metavar="REF", help="a run id, a native id, or a unique prefix of either")
    add_registry(p)

    p = sub.add_parser("log", usage="cli.py log --run REF", help="what one run did, as text", description=LOG_DESC, formatter_class=OneLinePerParagraph)
    p.add_argument("--run", metavar="REF", required=True, help="a run id, a native id, or a unique prefix of either")
    add_registry(p)

    p = sub.add_parser("result", usage="cli.py result --run REF [--wait [--wait-timeout SEC]]", help="what a run concluded, now or once it ends", description=RESULT_DESC, epilog=RESULT_EPILOG, formatter_class=OneLinePerParagraph)
    p.add_argument("--run", metavar="REF", required=True, help="a run id, a native id, or a unique prefix of either")
    p.add_argument("--wait", action="store_true", help="block until the run ends or new attention appears")
    p.add_argument("--wait-timeout", type=positive, metavar="SEC", help="with --wait, give up after SEC seconds and print the partial result")
    add_registry(p)

    p = sub.add_parser("stop", usage="cli.py stop --run REF [--run REF …]", help="interrupt runs", description=STOP_DESC, formatter_class=OneLinePerParagraph)
    p.add_argument("--run", metavar="REF", action="append", required=True, help="a run to stop; repeat for several")
    add_registry(p)

    p = sub.add_parser("doctor", usage="cli.py doctor [--engine E] [--project DIR]", help="check the environment a run would start in", description=DOCTOR_DESC, formatter_class=OneLinePerParagraph)
    p.add_argument("--engine", choices=tuple(ENGINES), help="check only this engine (default: every engine)")
    add_registry(p)

    p = sub.add_parser("__supervise", help=argparse.SUPPRESS)
    p.add_argument("--run-dir", required=True)
    return root


def read_text(value, path, command, what="prompt"):
    """The text given as a positional, a file, or on stdin — exactly one of them, and not empty."""
    flag = f"--{what}-file"
    if value is not None and path:
        raise Refusal(f"give the {what} once: {what.upper()} or {flag}", arguments=True, help=f"{command} --help")
    if path:
        try:
            text = Path(path).expanduser().read_text(encoding="utf-8")
        except OSError as e:
            raise Refusal(f"{flag} {path}: {e.strerror or e}", arguments=True, help=f"{command} --help")
        except UnicodeDecodeError as e:
            raise Refusal(f"{flag} {path} is not UTF-8 text: {e}", arguments=True, help=f"{command} --help")
    elif value is not None:
        text = value
    elif not sys.stdin.isatty():
        try:
            text = sys.stdin.buffer.read().decode("utf-8")
        except UnicodeDecodeError as e:
            raise Refusal(f"the {what} on stdin is not UTF-8 text: {e}", arguments=True, help=f"{command} --help")
    else:
        text = ""
    if not text.strip():
        raise Refusal(f"the {what} is empty", arguments=True, help=f"{command} --help")
    return text


def base_dir(args):
    return str(Path(args.project).expanduser().resolve()) if getattr(args, "project", None) else os.getcwd()


def run_flags(args):
    flags = {"profile": args.profile, "model": args.model, "effort": args.effort, "schema": args.schema, "priority": getattr(args, "priority", False), "cwd": None}
    if args.cwd:
        cwd = Path(args.cwd).expanduser()
        if not cwd.is_dir():
            raise Refusal(f"--cwd {args.cwd} is not a directory", arguments=True, help=f"{args.command} --help")
        flags["cwd"] = str(cwd.resolve())
    return flags


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def dispatch(args):
    c = args.command
    if c in ("start", "resume", "stop"):
        # Before reading a prompt or the registry: inside a worker these commands are refused whatever their arguments.
        refuse_inside_worker()
    if c == "start":
        if not args.engine:
            raise Refusal("--engine is required: there is no default engine", arguments=True, help="start --help")
        flags = run_flags(args)
        reply = start(engine=args.engine, prompt=read_text(args.prompt, args.prompt_file, "start"), cwd=base_dir(args), flags=flags, label=args.label, timeout=args.timeout, runs_dir=args.runs_dir)
        emit(reply)
        return EXIT_REFUSED if reply.get("error") else 0
    if c == "resume":
        flags = run_flags(args)
        reply = resume(ref=args.ref, engine=args.engine, prompt=read_text(args.prompt, args.prompt_file, "resume"), cwd=base_dir(args), flags=flags, label=args.label, timeout=args.timeout, runs_dir=args.runs_dir)
        emit(reply)
        return EXIT_REFUSED if reply.get("error") else 0
    if c == "status":
        emit(status(cwd=base_dir(args), ref=args.run, runs_dir=args.runs_dir))
        return 0
    if c == "log":
        sys.stdout.write(log(cwd=base_dir(args), ref=args.run, runs_dir=args.runs_dir))
        return 0
    if c == "result":
        if args.wait_timeout and not args.wait:
            raise Refusal("--wait-timeout needs --wait", arguments=True, help="result --help")
        sys.stdout.write(result(cwd=base_dir(args), ref=args.run, wait_for=args.wait, wait_timeout=args.wait_timeout, runs_dir=args.runs_dir))
        return 0
    if c == "stop":
        emit(stop(refs=args.run or [], cwd=base_dir(args), runs_dir=args.runs_dir))
        return 0
    if c == "doctor":
        reply = doctor(cwd=base_dir(args), engine=args.engine, runs_dir=args.runs_dir)
        emit(reply)
        return 0 if reply["ok"] else EXIT_BLOCKED
    if c == "__supervise":
        return supervise(args.run_dir)
    raise Refusal("no command given", arguments=True, help="--help")


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return dispatch(args)
    except ArgumentError as e:
        emit({"error": str(e), "help": f"{e.command} --help" if e.command else "--help"})
        return EXIT_ARGUMENTS
    except Refusal as e:
        emit(e.reply())
        return EXIT_ARGUMENTS if e.arguments else EXIT_REFUSED
    except BrokenPipeError:
        return EXIT_REFUSED
    except OSError as e:
        # A registry or file the command cannot write is an outcome to report, not a traceback.
        emit({"error": f"{e.strerror or e}: {e.filename}" if e.filename else str(e)})
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
