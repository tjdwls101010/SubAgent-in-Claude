"""Workers on two engines — GPT through the Codex CLI, Claude through headless Claude Code — as managed subagents. The tree, by kind:

Features (one per reason to change):
    engine/      the engine contract and one adapter per engine (claude.py, codex.py): settings, argv, the prompt's way in, the detached supervisor's driver, reading a run's events, diagnosis. The only unit that imports an engine's CLI unit
    runs/        start, resume and stop; creating a run of any engine
    observe/     status, log and result, including `result --wait` and the attention that ends a wait early; `snapshot` and `wait_ended` for callers that read a run without parsing text
    doctor.py    the environment a run would start in, per engine

Systems (formats someone else owns):
    claude_cli/  everything about the Claude Code CLI: argv and profiles, the worker's environment, stream-json in and out, version, auth, settings files
    git/         the git CLI: top level, main checkout, worktrees, branches, a checkout's state before and after a run

Store:
    registry/    <main checkout>/.delegate-runs: run directories and meta.json, the publish lock, the send inbox

Shared:
    errors.py    Refusal, the one error a command turns into an exit code
    util.py      time, text trimming, process liveness, the skill's own invocation string, timings

Imports run feature → store → system → shared; the edges between features are runs → engine, observe → engine, doctor → engine; only engine/ imports claude_cli/.
"""
