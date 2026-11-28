#!/usr/bin/env python3
"""S6: the skill as a headless orchestrator meets it — the draft SKILL.md against a control (its frontmatter and call paragraph only) — on the scenarios in scenarios.md, isolated, saving what each session did.

    python3 tests/e2e/run_e2e.py --scenario judge --variant draft --out .tmp/e2e
    python3 tests/e2e/run_e2e.py --scenario 1 --variant control --out .tmp/e2e

`judge` is one `-p` turn: the twelve situations of the plan's contrast run, answered with only the skill's text to go by. Scenarios 1–3 are real orchestrator sessions with the skill loaded as a plugin, whose workers are the fake engines from tests/support (first on PATH), so they cost no worker quota and end the same way every time; what they test is the orchestrator's calls. Writes <out>/<scenario>-<variant>/: transcript*.jsonl (stream-json), digest.txt (every tool call with Bash's run_in_background and timeout, then a METRICS line) and the throwaway repo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / ".claude" / "skills" / "delegate"
SUPPORT = REPO / "tests" / "support"
PLAN = REPO / ".claude" / "plans" / "261127_delegate 스킬; codex·claude 작업자를 한 스킬로.md"
# The orchestrator is the real claude, found before the fakes go first on PATH for its workers.
CLAUDE = shutil.which("claude")

FILES = {
    1: {"src/calc.py": "def average(values):\n    return sum(values) / len(values)\n\n\ndef percent(part, whole):\n    return part / whole * 10\n"},
    2: {"src/calc.py": "def average(values):\n    return sum(values) / len(values)\n\n\ndef percent(part, whole):\n    return part / whole * 10\n"},
    3: {"src/util.py": "import os\n\n\ndef join(a, b):\n    return os.path.join(a, b)\n\n\ndef stem(path):\n    return os.path.splitext(os.path.basename(path))[0]\n"},
}
PROMPTS = {
    1: "Have GPT review src/calc.py for bugs and tell me what it finds. Don't change any files yourself.",
    2: "Get two independent reviews of src/calc.py at the same time — one from GPT and one from another Claude session — and tell me where they agree and where they differ. Don't change any files.",
    3: "Have GPT add type hints to every function in src/util.py, and let me know when it is done.",
}
# What the fake engines do in each scenario: a codex worker takes about a minute, so how the session waits shows; 3's stops at its usage limit, as a real one does.
FAKES = {1: {"FAKE_CODEX_DELAY": "6"}, 2: {"FAKE_CODEX_DELAY": "6"}, 3: {"FAKE_CODEX_FIXTURE": str(SUPPORT / "fixtures" / "codex" / "usage-limit.jsonl"), "FAKE_CODEX_EXIT": "1", "FAKE_CODEX_DELAY": "6"}}


def control_text(draft):
    """The draft's frontmatter and its call paragraph, without any judgement text."""
    front, body = draft.split("\n---\n", 1)
    call = [p for p in body.strip().split("\n\n") if not p.startswith("#")][0]
    return f"{front}\n---\n\n# Workers on two engines as managed subagents\n\n{call}\n"


def skill_text(variant):
    draft = (SKILL / "SKILL.md").read_text()
    return draft if variant == "draft" else control_text(draft)


def judge_prompt(variant):
    """The contrast run's prompt with its facts paragraph replaced by the skill's text: the facts are now the skill's to give."""
    block = PLAN.read_text().split("```\nYou are the orchestrating", 1)[1].split("```", 1)[0]
    situations = block[block.index("For each situation below"):]
    return f"""You are the orchestrating Claude Code session of a user who works with you over many turns. Besides your own tools (including your built-in Agent subagents), you have one delegation skill installed; its SKILL.md follows, and its commands' --help would give you every flag.

<skill name="delegate">
{skill_text(variant)}
</skill>

{situations}"""


def entry(plugin):
    return f'uv run "{plugin}/.claude/skills/delegate/scripts/cli.py"'


def setup(out, scenario, variant):
    if out.exists():
        stop_runs(out / "repo", out / "plugin")
        shutil.rmtree(out)
    plugin = out / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "delegate", "version": "0.0.0-e2e", "skills": ["./.claude/skills/"]}))
    dest = plugin / ".claude" / "skills" / "delegate"
    shutil.copytree(SKILL, dest, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", ".claude"))
    (dest / "SKILL.md").write_text(skill_text(variant))
    repo = out / "repo"
    for rel, text in FILES[scenario].items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=e2e@example.com", "-c", "user.name=e2e", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    state = out / "fakes"
    state.mkdir()
    env = {**os.environ, "PATH": os.pathsep.join([str(SUPPORT / "fake_claude"), str(SUPPORT / "fake_codex"), os.environ["PATH"]]), "FAKE_CLAUDE_STATE": str(state / "claude"), "FAKE_CLAUDE_FIXTURES": str(SUPPORT / "fixtures" / "claude"), "CODEX_HOME": str(state / "codex-home"), "FAKE_CODEX_ARGV_LOG": str(state / "codex-argv.jsonl"), **FAKES[scenario]}
    (state / "codex-home").mkdir()
    settings = out / "settings.json"
    settings.write_text(json.dumps({"sandbox": {"enabled": True, "allowUnsandboxedCommands": False, "autoAllowBashIfSandboxed": True, "excludedCommands": ["uv run *cli.py*"]}}))
    return plugin, repo, env, settings


def stop_runs(repo, plugin):
    """Detached runs outlive the session that started them; stop them so none writes into a rerun's repo."""
    cli = plugin / ".claude" / "skills" / "delegate" / "scripts" / "cli.py"
    if not (repo / ".delegate-runs").exists() or not cli.exists():
        return
    listing = subprocess.run(["uv", "run", str(cli), "status", "--project", str(repo)], capture_output=True, text=True)
    try:
        running = [r["run_id"] for r in json.loads(listing.stdout).get("runs", []) if r.get("state") in ("starting", "running")]
    except Exception:
        return
    for run_id in running:
        subprocess.run(["uv", "run", str(cli), "stop", "--run", run_id, "--project", str(repo)], capture_output=True, text=True)


def base_cmd(plugin, settings, model):
    return [CLAUDE, "-p", "--setting-sources", "", "--strict-mcp-config", "--plugin-dir", str(plugin), "--settings", str(settings), "--tools", "Bash,Read,Edit,Write,Glob,Grep,Monitor,Skill",
            # a skill's allowed-tools covers only a user-typed command; a skill the model picks itself needs the user's own permission rule, which this stands in for
            "--allowedTools", f"Skill,Monitor,Bash({entry(plugin)} *)", "--permission-mode", "acceptEdits", "--model", model, "--output-format", "stream-json", "--verbose"]


def run_stream(cmd, repo, env, prompt, transcript, idle, cap):
    """A session whose stdin stays open, so a finished background task can start another turn; closed after `idle` quiet seconds following a result, or at `cap`."""
    p = subprocess.Popen(cmd + ["--input-format", "stream-json"], cwd=repo, env=env, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
    p.stdin.flush()
    last = {"t": time.time(), "result": False}

    def read():
        with transcript.open("w") as fh:
            for line in p.stdout:
                fh.write(line)
                fh.flush()
                last["t"] = time.time()
                if '"type":"result"' in line.replace(" ", ""):
                    last["result"] = True

    t = threading.Thread(target=read, daemon=True)
    t.start()
    start = time.time()
    while time.time() - start < cap and not (last["result"] and time.time() - last["t"] > idle):
        time.sleep(2)
    p.stdin.close()
    try:
        p.wait(timeout=120)
    except subprocess.TimeoutExpired:
        p.kill()
    t.join(timeout=10)


# A call that reads the reply through a pipe or a parser, and one that holds a value in a shell variable or substitution: both mean the reply was not usable as printed, and the second no longer matches a pre-approval.
# 성진: a regex over the command text, so a `|` or `$` inside a quoted prompt counts too; tokenise with shlex if such prompts start to move the comparison.
PIPED = re.compile(r"cli\.py[^|]*\|")
SHELL_VALUE = re.compile(r"\$[A-Za-z_{(]|`")


def digest(out):
    lines, calls, denials = [], [], 0
    for tr in sorted(out.glob("transcript*.jsonl")):
        lines.append(f"== {tr.name}")
        for raw in tr.read_text().splitlines():
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "assistant":
                for c in ev["message"].get("content", []):
                    if c.get("type") == "tool_use":
                        i = c.get("input", {})
                        extra = " ".join(f"{k}={i[k]}" for k in ("run_in_background", "timeout") if k in i)
                        lines.append(f"{c['name']} {extra} :: {i.get('command') or i.get('file_path') or json.dumps(i)[:200]}")
                        if c["name"] == "Bash" and "cli.py" in i.get("command", ""):
                            calls.append(i)
                    elif c.get("type") == "text" and c.get("text", "").strip():
                        lines.append("TEXT :: " + c["text"].strip().replace("\n", " ")[:400])
            elif ev.get("type") == "result":
                denials += len(ev.get("permission_denials") or [])
                lines.append(f"RESULT denials={ev.get('permission_denials')} turns={ev.get('num_turns')} :: " + (ev.get("result") or "").replace("\n", " ")[:600])
    commands = [i.get("command", "") for i in calls]
    lines.append("METRICS " + " ".join(f"{k}={v}" for k, v in {
        "bridge_calls": len(calls),
        "piped": sum(bool(PIPED.search(c)) for c in commands),
        "shell_values": sum(bool(SHELL_VALUE.search(c)) for c in commands),
        "denials": denials,
        "background_waits": sum(bool(i.get("run_in_background")) and "--wait" in c for i, c in zip(calls, commands)),
        "results": sum(" result " in f" {c} " for c in commands),
    }.items()))
    (out / "digest.txt").write_text("\n".join(lines) + "\n")
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=["judge", *map(str, sorted(PROMPTS))], required=True, help="`judge` or a scenario number in scenarios.md")
    ap.add_argument("--variant", choices=("draft", "control"), required=True, help="the SKILL.md as written, or only its frontmatter and call paragraph")
    ap.add_argument("--out", type=Path, required=True, help="directory to write <scenario>-<variant>/ under")
    ap.add_argument("--model", default="opus", help="the orchestrator's model (default: opus, what the user's sessions run)")
    ap.add_argument("--idle", type=float, default=120, help="seconds of quiet after a result before stdin is closed (default: 120)")
    ap.add_argument("--cap", type=float, default=1200, help="longest a session may run, in seconds (default: 1200)")
    args = ap.parse_args()
    out = args.out.resolve() / f"{args.scenario}-{args.variant}"
    tmp = Path(tempfile.gettempdir()).resolve()
    if out == tmp or tmp in out.parents:
        raise SystemExit(f"--out must be outside TMPDIR ({tmp}): a read-only worker may write there")
    if args.scenario == "judge":
        out.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run([CLAUDE, "-p", "--safe-mode", "--restricted", "--permission-mode", "acceptEdits", "--model", args.model, "--output-format", "json", judge_prompt(args.variant)], cwd=out, capture_output=True, text=True, timeout=1200)
        (out / "answer.json").write_text(proc.stdout)
        print(json.loads(proc.stdout).get("result"))
        return
    scenario = int(args.scenario)
    plugin, repo, env, settings = setup(out, scenario, args.variant)
    try:
        run_stream(base_cmd(plugin, settings, args.model), repo, env, PROMPTS[scenario], out / "transcript.jsonl", args.idle, args.cap)
    finally:
        stop_runs(repo, plugin)
    print("\n".join(digest(out)))


if __name__ == "__main__":
    main()
