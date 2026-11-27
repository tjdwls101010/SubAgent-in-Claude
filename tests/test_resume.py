"""Continuing a session: every setting re-sent to the resumed process, a flag changing it from that turn on, sessions the registry never saw, another engine refused with the way to hand the work over, and the refusals around a live session."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid

from support.harness import CLAUDE_RUN, ENTRY, FAST_TIMINGS, Sandbox


def worker_calls(calls):
    return [c for c in calls if "-p" in c["argv"]]


def strip_session(argv):
    """argv without the session flag pair, which is the one thing that differs between a start and its resume."""
    out = list(argv)
    for flag in ("--session-id", "--resume"):
        if flag in out:
            i = out.index(flag)
            del out[i : i + 2]
    return out


class Resume(Sandbox):
    def test_every_setting_is_sent_again(self):
        schema = self.root / "s.json"
        schema.write_text('{"type": "object", "properties": {"ok": {"type": "boolean"}}}')
        first = self.start("x [[fake:schema]]", "--profile", "read-only", "--model", "haiku", "--effort", "low", "--schema", str(schema))
        self.wait_state(first["run_id"])
        again = self.cli_json("resume", first["run_id"], "and then [[fake:schema]]")
        self.assertEqual(again["native_id"], first["native_id"])
        self.assertNotEqual(again["run_id"], first["run_id"])
        self.assertEqual(again["permission_mode"], "dontAsk")
        self.wait_state(again["run_id"])
        a, b = worker_calls(self.fake_calls())
        self.assertEqual(b["argv"][b["argv"].index("--resume") + 1], first["native_id"])
        self.assertEqual(strip_session(a["argv"]), strip_session(b["argv"]))
        self.assertEqual(a["cwd"], b["cwd"])
        self.assertEqual(b["env"]["DELEGATE_RUN"], again["run_id"])
        doc = json.loads(self.cli("result", "--run", again["run_id"]).stdout)
        self.assertEqual(doc["json"], {"ok": True})

    def test_a_flag_changes_the_profile_from_that_turn_on(self):
        first = self.start("x", "--profile", "read-only")
        self.wait_state(first["run_id"])
        second = self.cli_json("resume", first["native_id"], "y", "--profile", "full")
        self.assertEqual(second["profile_changed_from"], "read-only")
        self.assertEqual(second["permission_mode"], "auto")
        self.wait_state(second["run_id"])
        third = self.cli_json("resume", second["run_id"], "z")
        self.assertEqual(third["profile"], "full")
        self.assertNotIn("profile_changed_from", third)
        self.wait_state(third["run_id"])

    def test_session_the_registry_never_saw(self):
        sid = str(uuid.uuid4())
        (self.state / "sessions").mkdir(exist_ok=True)
        (self.state / "sessions" / f"{sid}.json").write_text(json.dumps({"turns": 1, "cwd": str(self.project), "worktree": None}))
        given = {"--engine": "claude", "--profile": "read-only", "--cwd": str(self.project), "--effort": "high"}
        for missing in given:
            args = [x for k, v in given.items() if k != missing for x in (k, v)]
            need = self.cli_json("resume", sid, "hi", *args, expect=2)
            self.assertIn(missing, need["error"])
            self.assertEqual(need["help"], "resume --help")
        reply = self.cli_json("resume", sid, "hi", *[x for kv in given.items() for x in kv])
        self.assertEqual(reply["native_id"], sid)
        self.assertIn("not in the registry", reply["note"])
        self.assertEqual(self.wait_state(reply["run_id"])["state"], "completed")
        argv = worker_calls(self.fake_calls())[0]["argv"]
        self.assertNotIn("--model", argv, "claude keeps the session's own model")
        self.assertEqual(argv[argv.index("--effort") + 1], "high")

    def test_a_native_id_that_is_not_a_session_id_is_refused(self):
        self.assertIn("not a Claude session id", self.cli_json("resume", "not-a-uuid", "hi", "--engine", "claude", "--profile", "write", "--cwd", str(self.project), "--effort", "high", expect=2)["error"])

    def test_another_engine_cannot_continue_a_run(self):
        """A conversation does not cross engines: the refusal names the two commands that hand the work over in the same directory."""
        first = self.start("x")
        self.wait_state(first["run_id"])
        out = self.cli_json("resume", first["run_id"], "y", "--engine", "codex", expect=2)
        self.assertEqual(out["help"], "resume --help")

    def test_session_claude_does_not_know_fails_at_start(self):
        reply = self.cli_json("resume", str(uuid.uuid4()), "hi", "--engine", "claude", "--profile", "write", "--cwd", str(self.project), "--effort", "high", expect=1)
        self.assertEqual(reply["state"], "failed")
        self.assertIn("No conversation found", reply["error"])

    def test_unknown_ref_without_an_engine(self):
        out = self.cli_json("resume", "nope", "hi", expect=2)
        self.assertIn("is not a run, session or thread", out["error"])
        self.assertIn("--engine", out["error"])

    def test_prompt_is_required(self):
        first = self.start("x")
        self.wait_state(first["run_id"])
        self.assertEqual(self.cli_json("resume", first["run_id"], input="", expect=2)["help"], "resume --help")
        self.cli_json("resume", expect=2)

    def test_stopped_run_can_be_resumed(self):
        first = self.start("x [[fake:sleep:30]]")
        self.cli_json("stop", "--run", first["run_id"])
        again = self.cli_json("resume", first["run_id"], "carry on [[fake:reply:resumed]]")
        self.assertEqual(self.wait_state(again["run_id"])["state"], "completed")
        self.assertEqual(self.cli("result", "--run", again["run_id"]).stdout.split("\n", 1)[1], "resumed\n")

    def test_a_resume_that_read_stale_settings_is_refused(self):
        """A resume publishes only if the session's newest run is still the one it read its settings from: otherwise a turn that changed them (here to read-only) would be quietly undone."""
        first = self.start("x", "--profile", "write")
        self.wait_state(first["run_id"])
        env = {**self.env, "DELEGATE_TEST_TIMINGS": json.dumps({**FAST_TIMINGS, "pause_before_resume_publish": 4})}
        slow = subprocess.Popen([sys.executable, str(ENTRY), "resume", first["run_id"], "slow"], cwd=self.project, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: slow.poll() is None and slow.kill())
        import time

        time.sleep(1)
        quick = self.cli_json("resume", first["run_id"], "quick", "--profile", "read-only")
        self.wait_state(quick["run_id"])
        out, _ = slow.communicate(timeout=60)
        self.assertEqual(slow.returncode, 1, out)
        self.assertIn("had another turn", json.loads(out)["error"])

    def test_the_timeout_is_inherited(self):
        first = self.start("x", "--timeout", "1.5")
        self.wait_state(first["run_id"])
        again = self.cli_json("resume", first["run_id"], "long [[fake:sleep:6]]")
        self.assertEqual(self.wait_state(again["run_id"], timeout=30)["state"], "timed_out")

    def test_an_unreadable_live_run_on_the_session_blocks_a_resume(self):
        first = self.start("x [[fake:sleep:20]]")
        self.wait_for(lambda: self.events(first["run_id"]), what="its first events")
        (self.run_dir(first["run_id"]) / "meta.json").write_text("{damaged")
        out = self.cli_json("resume", first["native_id"], "y", "--engine", "claude", "--profile", "write", "--cwd", str(self.project), "--effort", "high", expect=1)
        self.assertIn("cannot tell whether", out["error"])

