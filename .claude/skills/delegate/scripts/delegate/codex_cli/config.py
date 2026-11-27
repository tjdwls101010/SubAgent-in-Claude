"""CODEX_HOME, and the few top-level values of the user's config.toml that `doctor` reports. A run never reads them: it is isolated (`--ignore-user-config`) and names its own model and effort."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path


def codex_home() -> Path:
    """CODEX_HOME when set, resolved — sessions, config and auth all move with it — else ~/.codex."""
    v = os.environ.get("CODEX_HOME")
    return Path(v).expanduser().resolve() if v else Path.home() / ".codex"


def config_scalars(keys, path=None):
    """Named top-level string values from config.toml; `{}` when the file cannot be read or does not parse.

    Only top-level keys count: `model` under `[profiles.work]` is a different setting, and Codex applies the top-level one unless a profile is selected, which this skill never does. A value that is not a string is not one of these settings and is left out.
    """
    path = Path(path) if path else codex_home() / "config.toml"
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k in keys and isinstance(v, str)}


def config_summary():
    """What `doctor` reports of config.toml: its path when the file exists, else None, and the top-level `sandbox_mode` and `approval_policy` it sets, each None when unset."""
    path = codex_home() / "config.toml"
    raw = config_scalars(("sandbox_mode", "approval_policy"), path)
    return {"path": str(path) if path.exists() else None,
            "sandbox_mode": raw.get("sandbox_mode"), "approval_policy": raw.get("approval_policy")}
