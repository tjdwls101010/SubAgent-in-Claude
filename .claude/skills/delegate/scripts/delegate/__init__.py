"""Workers on two engines — GPT through the Codex CLI, Claude through headless Claude Code — as managed subagents. The tree, by kind:

Features (one per reason to change):
    engine/      the engine contract and one adapter per engine (claude.py, codex.py): settings, argv, the prompt's way in, the detached supervisor's driver, reading a run's events, diagnosis. The only unit that imports an engine's CLI unit
    runs/        start, resume and stop; creating a run of any engine
    batch/       several runs, of any engines, as one named group: their slots, checkouts of their own, and clean
    observe/     status, log and result, including `result --wait` and the attention that ends a wait early; `snapshot` and `wait_ended` for callers that read a run without parsing text
    doctor.py    the environment a run would start in, per engine
    models.py    the models and efforts each engine offers now, and its account's limits
    schedule/    schedule, unschedule and the scheduled job's Run and Finish steps: a workflow file on GitHub, a dispatcher on this Mac, slots, the summary

Systems (formats someone else owns):
    claude_cli/  everything about the Claude Code CLI: argv and profiles, the worker's environment, stream-json in and out, version, auth, settings files
    codex_cli/   everything about the Codex CLI: `exec` argv and the read-only permissions profile, the event stream and stderr, CODEX_HOME and config.toml, the model catalog and version floors, login
    git/         the git CLI: top level, main checkout, worktrees, branches, a checkout's state before and after a run
    github/      GitHub through gh: the API calls, the workflow file a schedule is, the job's environment and step summary, runners
    launchd/     launchd and LaunchAgent plists: the dispatcher a schedule installs on this Mac

Store:
    registry/    <main checkout>/.delegate-runs: run directories and meta.json, the publish lock, the send inbox, group manifests and their generations, schedule state

Shared:
    errors.py    Refusal, the one error a command turns into an exit code
    util.py      time, text trimming, process liveness, the skill's own invocation string, timings

Imports run feature → store → system → shared; the edges between features are runs → engine, observe → engine, doctor → engine, models → engine, batch → runs, batch → engine, schedule → runs, schedule → batch, schedule → observe, schedule → engine and doctor → schedule; only engine/ imports claude_cli/ and codex_cli/.
"""
