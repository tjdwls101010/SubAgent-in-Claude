"""`doctor`: the environment a run would start in, per engine — its CLI and version, login, what its profiles need, settings that widen them — and the registry. It starts no worker."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from delegate.engine import ENGINES, adapter
from delegate.registry import live_runs, resolve_runs_dir
from delegate.util import entry_path, invocation, skill_dir


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
    }
