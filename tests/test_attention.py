"""What ends a wait early, on either engine and in a mixed group: a codex run that goes silent (stalled), a claude member's attention while a codex member runs on, a member that fails while others run (`member_failed`), and a wait again that does not come back for what it already reported. `returned_because` says which. And `send`, which a codex run refuses with where to go instead.
"""

from __future__ import annotations

import json

from support.harness import CLAUDE_RUN, CODEX_FIXTURES, CODEX_RUN, FAST_TIMINGS, Sandbox


def header_of(proc):
    return json.loads(proc.stdout.split("\n", 1)[0])


class CodexStall(Sandbox):
    def test_a_silent_codex_run_is_stalled_and_ends_the_wait(self):
        """A codex turn inside one long command writes nothing for a while: past the stall time it is `stalled`, and the wait comes back with what it is inside."""
        out = self.codex("start", "x", env={"FAKE_CODEX_FIXTURE": str(CODEX_FIXTURES / "mid-command.jsonl"), "FAKE_CODEX_HANG": "60"})
        header = header_of(self.cli("result", "--run", out["run_id"], "--wait", "--wait-timeout", "30", timeout=60))
        self.assertEqual(header["returned_because"], "attention")
        (att,) = header["attention"]
        self.assertEqual(att["reason"], "stalled")
        self.assertEqual(att["in_progress"][0]["command"], "sleep 2 && echo tick4")
        self.assertEqual(self.status(out["run_id"])["state"], "stalled")
        again = header_of(self.cli("result", "--run", out["run_id"], "--wait", "--wait-timeout", "2", timeout=60))
        self.assertEqual(again["returned_because"], "timeout", "a stall counts afresh from the start of a new wait")
        self.cli_json("stop", "--run", out["run_id"])

    def test_an_ended_run_returns_because_it_ended(self):
        out = self.codex("start", "x")
        header = header_of(self.cli("result", "--run", out["run_id"], "--wait", timeout=60))
        self.assertEqual((header["returned_because"], header["state"]), ("ended", "completed"))
        self.assertNotIn("returned_because", header_of(self.cli("result", "--run", out["run_id"])), "only a wait says why it returned")


class MixedGroupWaits(Sandbox):
    def setUp(self):
        super().setUp()
        # Long enough that a member sleeping in a tool is not stalled while the wait is about something else.
        self.env["DELEGATE_TEST_TIMINGS"] = json.dumps({**FAST_TIMINGS, "stall": 60})

    def tasks(self, *lines):
        path = self.root / f"t{len(list(self.root.glob('t*.jsonl')))}.jsonl"
        path.write_text("".join(json.dumps(l) + "\n" for l in lines))
        return str(path)

    def wait_group(self, name, *args):
        proc = self.cli("result", "--group", name, "--wait", *args, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return header_of(proc)

    def test_claude_attention_returns_while_codex_runs(self):
        f = self.tasks({"prompt": "c [[fake:silent:1]] [[fake:ratelimit_late:rejected]] [[fake:sleep:30]]", "engine": "claude", "model": "claude-test-model", "effort": "high"}, {"prompt": "x", "engine": "codex", "model": "fake-big", "effort": "high"})
        self.cli_json("batch", "--group", "att", "--tasks-file", f, timeout=120, env={"FAKE_CODEX_HANG": "60"})
        header = self.wait_group("att")
        self.assertEqual(header["returned_because"], "attention")
        self.assertEqual([(a["index"], [x["reason"] for x in a["attention"]]) for a in header["attention"]], [(1, ["rate_limited"])])
        self.assertEqual([m["state"] for m in header["members"]], ["running", "running"], "the codex member is still running")
        self.cli_json("stop", "--group", "att", timeout=120)

    def test_a_member_that_fails_returns_the_wait_once(self):
        f = self.tasks({"prompt": "c [[fake:error]]", "engine": "claude", "model": "claude-test-model", "effort": "high"}, {"prompt": "x", "engine": "codex", "model": "fake-big", "effort": "high"})
        self.cli_json("batch", "--group", "mf", "--tasks-file", f, timeout=120, env={"FAKE_CODEX_HANG": "8"})
        header = self.wait_group("mf")
        self.assertEqual(header["returned_because"], "member_failed")
        self.assertEqual([m["state"] for m in header["members"]], ["failed", "running"])
        self.assertIn("failure", header["note"])
        self.assertIn("next", header)
        again = self.wait_group("mf", "--wait-timeout", "30")
        self.assertEqual(again["returned_because"], "ended", "the failure already reported does not end the next wait")
        self.assertEqual([m["state"] for m in again["members"]], ["failed", "completed"])

    def test_a_codex_member_that_fails_while_claude_runs(self):
        f = self.tasks({"prompt": "c [[fake:sleep:30]]", "engine": "claude", "model": "claude-test-model", "effort": "high"}, {"prompt": "x", "engine": "codex", "model": "fake-big", "effort": "high"})
        # The codex turn fails a few seconds after it started, once the wait is under way: a failure already there when a wait begins does not end it.
        self.cli_json("batch", "--group", "cf", "--tasks-file", f, timeout=120, env={"FAKE_CODEX_FIXTURE": str(CODEX_FIXTURES / "turn-failed.jsonl"), "FAKE_CODEX_EXIT": "1", "FAKE_CODEX_DELAY": "1.5"})
        header = self.wait_group("cf")
        self.assertEqual(header["returned_because"], "member_failed")
        self.assertEqual([m["state"] for m in header["members"]], ["running", "failed"])
        self.cli_json("stop", "--group", "cf", timeout=120)


class FailuresAroundWaits(MixedGroupWaits):
    """PR④ claude review: a failure that came before the wait began is still reported, once; a member someone stops is not a failure."""

    def test_a_member_that_failed_before_the_first_wait_is_reported_by_it(self):
        import time

        f = self.tasks({"prompt": "c [[fake:sleep:20]]", "engine": "claude", "model": "claude-test-model", "effort": "high"}, {"prompt": "x", "engine": "codex", "model": "fake-big", "effort": "high"})
        self.cli_json("batch", "--group", "early", "--tasks-file", f, timeout=120, env={"FAKE_CODEX_FIXTURE": str(CODEX_FIXTURES / "turn-failed.jsonl"), "FAKE_CODEX_EXIT": "1"})
        self.wait_for(lambda: self.cli_json("status", "--group", "early")["failed"], what="the codex member to fail")
        time.sleep(1)  # the caller launches `next` a moment after the batch replied
        header = self.wait_group("early")
        self.assertEqual(header["returned_because"], "member_failed")
        again = self.wait_group("early", "--wait-timeout", "60")
        self.assertEqual(again["returned_because"], "ended", "reported once")

    def test_two_waits_at_once_report_one_failure_once(self):
        """PR④ GPT review: two group waits under way when a member fails — one of them reports it, the other waits on."""
        import subprocess
        import sys

        from support.harness import ENTRY

        f = self.tasks({"prompt": "c [[fake:sleep:30]]", "engine": "claude", "model": "claude-test-model", "effort": "high"}, {"prompt": "x", "engine": "codex", "model": "fake-big", "effort": "high"})
        self.cli_json("batch", "--group", "two", "--tasks-file", f, timeout=120, env={"FAKE_CODEX_FIXTURE": str(CODEX_FIXTURES / "turn-failed.jsonl"), "FAKE_CODEX_EXIT": "1", "FAKE_CODEX_DELAY": "2"})
        waiters = [subprocess.Popen([sys.executable, str(ENTRY), "result", "--group", "two", "--wait", "--wait-timeout", "8"], cwd=self.project, env=self.env, stdout=subprocess.PIPE, text=True) for _ in range(2)]
        outs = [w.communicate(timeout=120)[0] for w in waiters]
        self.assertEqual(sorted(json.loads(o.split("\n", 1)[0])["returned_because"] for o in outs), ["member_failed", "timeout"])
        self.cli_json("stop", "--group", "two", timeout=120)

    def test_stopping_the_group_during_a_wait_is_not_a_failure(self):
        import subprocess
        import sys

        from support.harness import ENTRY

        f = self.tasks({"prompt": "a [[fake:sleep:30]]", "engine": "claude", "model": "claude-test-model", "effort": "high"}, {"prompt": "b [[fake:sleep:30]]", "engine": "claude", "model": "claude-test-model", "effort": "high"})
        self.cli_json("batch", "--group", "sg", "--tasks-file", f, timeout=120)
        waiter = subprocess.Popen([sys.executable, str(ENTRY), "result", "--group", "sg", "--wait"], cwd=self.project, env=self.env, stdout=subprocess.PIPE, text=True)
        self.cli_json("stop", "--group", "sg", timeout=120)
        out, _ = waiter.communicate(timeout=120)
        self.assertEqual(json.loads(out.split("\n", 1)[0])["returned_because"], "ended")


class Send(Sandbox):
    def test_a_codex_run_takes_no_message_and_says_where_to_go(self):
        out = self.codex("start", "x", env={"FAKE_CODEX_HANG": "60"})
        refused = self.cli_json("send", out["run_id"], "change course", expect=1)
        self.assertIn("result --run", refused["instead"])
        self.assertIn("stop --run", refused["or"])
        self.assertIn("resume", refused["or"])
        self.assertNotIn(" send ", refused["instead"] + refused["or"])
        self.cli_json("stop", "--run", out["run_id"])

    def test_a_live_claude_session_points_resume_at_send(self):
        reply = self.start("x [[fake:sleep:30]]")
        refusal = self.cli_json("resume", reply["run_id"], "more", expect=1)
        self.assertIn(f"send {reply['run_id']}", refusal["instead"])
        self.cli_json("stop", "--run", reply["run_id"])
