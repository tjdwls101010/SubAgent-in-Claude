"""The Codex account's usage limits, in the skill's terms: asked of `codex app-server` (`account/rateLimits/read`, one request, no model call), or else read from the newest `token_count` event in this CODEX_HOME's rollouts — `codex exec --json` reports none — and the reset time a usage-limit failure names.

Every reader answers None rather than raising: a limit that cannot be read is unknown, never a reason to refuse a run.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from .config import codex_home

# The window lengths Codex reports, in minutes, by the name the skill gives them.
WINDOWS = {300: "5h", 10080: "7d"}
# The limit id of the account's ordinary usage, the one bucket every catalog model draws on (measured: the only one with windows).
ORDINARY = "codex"
APP_SERVER_SECONDS = 10.0
ROLLOUTS_SCANNED = 30


def _windows(primary, secondary, *, percent, minutes, resets):
    out = []
    for w in (primary, secondary):
        if not isinstance(w, dict) or w.get(percent) is None:
            continue
        mins = w.get(minutes)
        out.append({"kind": WINDOWS.get(mins, f"{mins}m" if mins else "?"), "used_percent": w.get(percent), "resets_at": w.get(resets)})
    return out


def from_app_server(answer):
    """`account/rateLimits/read`'s result as {limit_id, allowed, reached, plan, windows}, or None."""
    if not isinstance(answer, dict):
        return None
    rl = answer.get("rateLimits")
    if not isinstance(rl, dict):
        return None
    return {"limit_id": rl.get("limitId"), "allowed": answer.get("ordinaryUsageAllowed"), "reached": rl.get("rateLimitReachedType"), "plan": rl.get("planType"), "windows": _windows(rl.get("primary"), rl.get("secondary"), percent="usedPercent", minutes="windowDurationMins", resets="resetsAt")}


def from_rollout_event(event):
    """A rollout `token_count` event's `rate_limits` as the same shape (`allowed` unknown), with `observed_at` from the event's timestamp and `credits_left` (credits or an unlimited plan pay past a full window; None when the event does not say); None for another event, another bucket's limit (or one that names none) or a limit without windows."""
    payload = event.get("payload") if isinstance(event, dict) else None
    rl = payload.get("rate_limits") if isinstance(payload, dict) else None
    if not isinstance(rl, dict) or rl.get("limit_id") != ORDINARY or (rl.get("primary") is None and rl.get("secondary") is None):
        return None
    credits = rl.get("credits")
    try:
        observed = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError, AttributeError):
        observed = None
    return {"limit_id": rl.get("limit_id"), "allowed": None, "reached": rl.get("rate_limit_reached_type"), "plan": rl.get("plan_type"), "windows": _windows(rl.get("primary"), rl.get("secondary"), percent="used_percent", minutes="window_minutes", resets="resets_at"), "observed_at": observed, "credits_left": bool(credits.get("has_credits") or credits.get("unlimited")) if isinstance(credits, dict) else None}


def ask_app_server(timeout=APP_SERVER_SECONDS):
    """The limits now, from a one-request `codex app-server` session; None when it cannot be asked."""
    import queue
    import threading

    exe = shutil.which("codex")
    if not exe:
        return None
    try:
        proc = subprocess.Popen([exe, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return None
    lines = queue.Queue()

    def pump():
        # A thread reads, so a line already buffered behind another is never waited for in vain.
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    end = time.time() + timeout
    try:
        for msg in ({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "delegate", "version": "1"}}}, {"jsonrpc": "2.0", "method": "initialized", "params": {}}, {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}}):
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
        while time.time() < end:
            try:
                line = lines.get(timeout=max(0.05, end - time.time()))
            except queue.Empty:
                return None
            if line is None:
                return None
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("id") == 2:
                return from_app_server(obj.get("result"))
        return None
    except (OSError, ValueError):
        return None
    finally:
        proc.kill()
        proc.wait()


def read_rollouts(home=None, scanned=ROLLOUTS_SCANNED):
    """The newest windowed ordinary-usage limit any rollout under `home`/sessions recorded, or None."""
    sessions = Path(home or codex_home()) / "sessions"
    try:
        files = sorted(sessions.glob("**/rollout-*.jsonl"), key=lambda p: p.stat().st_mtime)[-scanned:]
    except OSError:
        return None
    best = None
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if '"token_count"' not in line or '"rate_limits"' not in line:
                continue
            try:
                obs = from_rollout_event(json.loads(line))
            except ValueError:
                continue
            if obs and (best is None or (obs.get("observed_at") or 0) >= (best.get("observed_at") or 0)):
                best = obs
    return best


_AT = re.compile(r"try again at (?P<when>[^.]+?)\.?\s*$", re.I)


def limit_resets_at(message, now=None):
    """The time a usage-limit message says to try again, as epoch seconds in this Mac's time zone, or None. Codex writes it as a clock time ("4:31 AM": the next such time) or a date ("Nov 14th, 2026 4:01 AM")."""
    m = _AT.search(message or "")
    if not m:
        return None
    when = re.sub(r"(\d)(st|nd|rd|th)", r"\1", m.group("when").strip())
    base = datetime.fromtimestamp(now or time.time())
    for fmt in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p"):
        try:
            return datetime.strptime(when, fmt).timestamp()
        except ValueError:
            pass
    try:
        t = datetime.strptime(when, "%I:%M %p")
    except ValueError:
        return None
    at = base.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    if at <= base:
        at += timedelta(days=1)
    return at.timestamp()


def is_usage_limit(message):
    return bool(message) and "usage limit" in message.lower()
