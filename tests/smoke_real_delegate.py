"""S5: both real engines end to end. Opt-in because it spends both subscriptions' limits:

    DELEGATE_REAL=1 DELEGATE_SMOKE_CLAUDE_MODEL=<model> DELEGATE_SMOKE_CODEX_MODEL=<model> python3 -m unittest smoke_real_delegate -v

(from tests/; each model a cheap one the account can use, as `models` lists it — the skill names none). Per engine: a start waited for with the `next` its reply names, then a resume; a read-only run that executes the project's tests and leaves the tree untouched. Then a mixed group collected and cleaned, a hand-over to the other engine in the same directory (a resume across engines is refused with the way to do it), `send` into a live Claude worker, and `stop`.

The project sits outside TMPDIR and ~/.cache, so "the tree is untouched" is not hidden by a writable parent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from support.harness import ENTRY, REPO

CLAUDE_MODEL = os.environ.get("DELEGATE_SMOKE_CLAUDE_MODEL")
CODEX_MODEL = os.environ.get("DELEGATE_SMOKE_CODEX_MODEL")
REAL = os.environ.get("DELEGATE_REAL") == "1" and CLAUDE_MODEL and CODEX_MODEL
ENGINE = {"claude": ("--engine", "claude", "--model", CLAUDE_MODEL or "", "--effort", "low"), "codex": ("--engine", "codex", "--model", CODEX_MODEL or "", "--effort", "low")}
TESTS = {"calc.py": "def add(a, b):\n    return a - b\n", "test_calc.py": "import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n\nif __name__ == '__main__':\n    unittest.main()\n"}


@unittest.skipUnless(REAL, "set DELEGATE_REAL=1, DELEGATE_SMOKE_CLAUDE_MODEL and DELEGATE_SMOKE_CODEX_MODEL")
class RealEngines(unittest.TestCase):
    def setUp(self):
        outside = REPO / ".tmp"
        outside.mkdir(exist_ok=True)
        self.base = Path(tempfile.mkdtemp(prefix="delegate-smoke-", dir=outside)).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.project = self.base / "project"
        self.project.mkdir()
        for rel, text in TESTS.items():
            (self.project / rel).write_text(text)
        for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=s@example.com", "-c", "user.name=s", "commit", "-qm", "init"]):
            subprocess.run(["git", "-C", str(self.project), *args], check=True)
        self.env = {k: v for k, v in os.environ.items() if k not in ("DELEGATE_RUN", "CLAUDE_SKILL_RUN", "CODEX_BRIDGE_RUN")}
        self.addCleanup(self.stop_running)

    def stop_running(self):
        p = subprocess.run(["uv", "run", str(ENTRY), "status", "--project", str(self.project)], cwd=self.project, env=self.env, capture_output=True, text=True)
        try:
            running = json.loads(p.stdout).get("running") or []
        except ValueError:
            return
        for run_id in running:
            subprocess.run(["uv", "run", str(ENTRY), "stop", "--run", run_id, "--project", str(self.project)], cwd=self.project, env=self.env, capture_output=True)

    def cli(self, *args, expect=0, timeout=900):
        p = subprocess.run(["uv", "run", str(ENTRY), *args, "--project", str(self.project)], cwd=self.project, env=self.env, capture_output=True, text=True, timeout=timeout)
        self.assertEqual(p.returncode, expect, p.stdout + p.stderr)
        return p.stdout

    def start(self, engine, prompt, *flags):
        return json.loads(self.cli("start", *ENGINE[engine], *flags, prompt))

    def waited(self, reply, state="completed"):
        """What the reply's `next` prints once the run ends, bounded here: its header and its message."""
        self.assertIn(f"result --run {reply['run_id']} --wait", reply["next"]["command"])
        header, _, body = self.cli("result", "--run", reply["run_id"], "--wait", "--wait-timeout", "600").partition("\n")
        header = json.loads(header)
        self.assertEqual(header["state"], state, header)
        return header, body

    def untouched(self):
        # .claude/ is what a Claude session itself leaves in its directory — Claude Code's sandbox scratch (.cc-writes) and whatever the user's own hooks make there — not a change the worker made.
        changed = [l for l in subprocess.run(["git", "-C", str(self.project), "status", "--porcelain"], capture_output=True, text=True).stdout.splitlines() if l[3:] != ".claude/"]
        self.assertEqual(changed, [])

    def test_each_engine_starts_waits_and_resumes(self):
        for engine in ("codex", "claude"):
            with self.subTest(engine=engine):
                started = self.start(engine, "Reply with exactly the word: first", "--profile", "read-only")
                first, answer = self.waited(started)
                self.assertIn("first", answer.lower())
                self.assertTrue(first["native_id"])
                resumed = json.loads(self.cli("resume", started["run_id"], "Reply with exactly the word: second"))
                self.assertEqual(resumed["native_id"], first["native_id"], "the same session or thread goes on")
                _, answer = self.waited(resumed)
                self.assertIn("second", answer.lower())

    def test_a_read_only_run_executes_the_tests_and_changes_nothing(self):
        prompt = "Run `python3 -m unittest test_calc` in this directory and reply with the failing assertion's message only. Do not change any file."
        for engine in ("codex", "claude"):
            with self.subTest(engine=engine):
                _, answer = self.waited(self.start(engine, prompt, "--profile", "read-only"))
                self.assertIn("5", answer, "it ran the test: 2 + 3 was expected to be 5")
                self.untouched()

    def test_a_mixed_group_is_collected_and_cleaned(self):
        tasks = self.base / "tasks.jsonl"
        tasks.write_text("".join(json.dumps({"prompt": f"Reply with exactly the word: {w}", "engine": e, "model": dict(codex=CODEX_MODEL, claude=CLAUDE_MODEL)[e], "effort": "low", "profile": "read-only"}) + "\n" for e, w in (("codex", "alpha"), ("claude", "beta"))))
        reply = json.loads(self.cli("batch", "--group", "smoke-mix", "--tasks-file", str(tasks)))
        self.assertEqual(reply["spawned"], 2, reply)
        header, _, body = self.cli("result", "--group", "smoke-mix", "--wait", "--wait-timeout", "600").partition("\n")
        header = json.loads(header)
        self.assertEqual((header["group_state"], [m["engine"] for m in header["members"]]), ("completed", ["codex", "claude"]), header)
        self.assertIn("alpha", body.lower())
        self.assertIn("beta", body.lower())
        self.assertTrue(json.loads(self.cli("clean", "--group", "smoke-mix"))["name_released"])

    def test_the_other_engine_takes_over_in_the_same_directory(self):
        gpt = self.start("codex", "Reply with exactly the word: gpt", "--profile", "read-only")
        self.waited(gpt)
        refused = json.loads(self.cli("resume", gpt["run_id"], "--engine", "claude", "go on", expect=2))
        self.assertIn("start --engine claude", " ".join(refused["instead"]), refused)
        took = self.start("claude", "Reply with exactly the word: claude", "--profile", "read-only", "--cwd", str(self.project))
        header, answer = self.waited(took)
        self.assertEqual(header["cwd"] if "cwd" in header else took["cwd"], str(self.project))
        self.assertIn("claude", answer.lower())

    def test_send_reaches_a_live_claude_worker_and_stop_ends_one(self):
        live = self.start("claude", "Run the shell command `sleep 25`, then reply with one word: the last word of any message you got while it ran, or none.", "--profile", "read-only")
        time.sleep(8)
        sent = json.loads(self.cli("send", live["run_id"], "The word is BANANA."))
        self.assertTrue(sent, sent)
        _, answer = self.waited(live)
        self.assertIn("banana", answer.lower())
        long = self.start("claude", "Run the shell command `sleep 300`, then reply done.", "--profile", "read-only")
        time.sleep(8)
        stopped = json.loads(self.cli("stop", "--run", long["run_id"]))
        self.assertEqual([s["state"] for s in stopped["stopped"]], ["interrupted"], stopped)
