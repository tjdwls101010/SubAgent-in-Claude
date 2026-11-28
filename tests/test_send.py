"""Steering a live run: messages written into its stdin, with priority `next` or `now`, the close rule with more than one message (J1), what a hook-dropped send leaves (J1b), and the refusals that point somewhere else (J16)."""

from __future__ import annotations

import json
import time
import unittest

from support.harness import FAST_TIMINGS, Sandbox, engine


def worker_calls(calls):
    return [c for c in calls if "-p" in c["argv"]]


class Send(Sandbox):
    def result(self, run_id, env=None):
        proc = self.cli("result", "--run", run_id, "--wait", timeout=60, env=env)
        header, _, text = proc.stdout.partition("\n")
        return json.loads(header), text

    def test_next_joins_the_running_turn(self):
        reply = self.start("first [[fake:sleep:2]]")
        sent = self.cli_json("send", reply["run_id"], "also this")
        self.assertEqual((sent["priority"], sent["delivered"]), ("next", True))
        header, text = self.result(reply["run_id"])
        self.assertEqual(header["state"], "completed")
        self.assertEqual(sorted(a["head"] for a in header["answered"]), ["also this", "first [[fake:sleep:2]]"])
        self.assertIn("+ also this", text)
        (call,) = worker_calls(self.fake_calls())
        self.assertEqual([(m["message"]["content"], m.get("priority")) for m in call["messages"]][1], ("also this", "next"))
        self.assertEqual(call["messages"][1]["uuid"], sent["message_uuid"])
        log = self.cli("log", "--run", reply["run_id"]).stdout
        self.assertIn("sent next also this", log)

    def test_a_message_the_worker_does_not_read_is_not_delivered(self):
        """PR④ GPT review: a message bigger than the pipe, to a worker that reads nothing, is not `delivered` while its write is still blocked, and a stop then records it `undelivered`."""
        import os
        import signal

        reply = self.start("first [[fake:sleep:30]]")
        self.wait_state(reply["run_id"], states=("running",))
        pid = json.loads((self.run_dir(reply["run_id"]) / "meta.json").read_text())["worker_pid"]
        os.kill(pid, signal.SIGSTOP)
        try:
            sent = self.cli_json("send", reply["run_id"], input="x" * 1_000_000)
            self.assertFalse(sent["delivered"], "a 1 MB message cannot fit in the pipe of a stopped reader")
            self.cli_json("stop", "--run", reply["run_id"])
        finally:
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        row = self.wait_state(reply["run_id"])
        self.assertEqual((row["state"], row["undelivered"]), ("interrupted", [sent["message_uuid"]]))

    def test_interrupt_ends_the_turn_and_answers_the_message(self):
        reply = self.start("first [[fake:sleep:30]]")
        sent = self.cli_json("send", reply["run_id"], "change course [[fake:reply:new direction]]", "--interrupt")
        self.assertEqual(sent["priority"], "now")
        header, text = self.result(reply["run_id"])
        self.assertEqual(text, "new direction\n")
        self.assertEqual([a["uuid"] for a in header["answered"]], [sent["message_uuid"]])
        self.assertEqual(header["state"], "completed")

    def test_stdin_stays_open_until_the_second_message_is_answered(self):
        slow = json.dumps({**FAST_TIMINGS, "idle_grace": 4, "stall": 30})
        reply = self.start("one [[fake:nouuid]] [[fake:reply:first answer]]", env={"DELEGATE_TEST_TIMINGS": slow})
        self.wait_for(lambda: self.status(reply["run_id"])["last_message"] == "first answer", what="first answer")
        # The second turn stays silent longer than the grace: an answer from before the second message must not close stdin.
        sent = self.cli_json("send", reply["run_id"], "two [[fake:silent:6]] [[fake:reply:second answer]]")
        self.assertTrue(sent["delivered"])
        header, text = self.result(reply["run_id"], env={"DELEGATE_TEST_TIMINGS": slow})
        self.assertEqual(text, "second answer\n")
        self.assertEqual([a["uuid"] for a in header["answered"]], [sent["message_uuid"]])
        (call,) = worker_calls(self.fake_calls())
        self.assertEqual(len(call["messages"]), 2, "both messages went to the same process")
        self.assertGreaterEqual(call["eof_at"], call["results_at"][-1], "stdin closed before the second message was answered")

    def test_hook_dropped_send_keeps_the_answer_and_says_so(self):
        reply = self.start("first [[fake:sleep:2]] [[fake:reply:the answer]]")
        sent = self.cli_json("send", reply["run_id"], "dropped [[fake:block_inturn]]")
        header, text = self.result(reply["run_id"])
        self.assertEqual(header["state"], "completed")
        self.assertEqual(text, "the answer\n")
        self.assertTrue(header["last_message_blocked"])
        self.assertEqual([b["uuid"] for b in header["blocked_by_hook"]], [sent["message_uuid"]])
        self.assertEqual(header["unanswered"], [])

    def test_send_to_an_ended_run_points_to_resume(self):
        reply = self.start("x")
        self.wait_state(reply["run_id"])
        refusal = self.cli_json("send", reply["run_id"], "late", expect=1)
        self.assertIn(f"resume {reply['run_id']}", refusal["instead"])
        self.assertNotIn(" send ", refusal["instead"])

    def test_send_to_an_older_run_points_at_the_sessions_live_run(self):
        first = self.start("x")
        self.wait_state(first["run_id"])
        second = self.cli_json("resume", first["run_id"], "again [[fake:sleep:10]]")
        refusal = self.cli_json("send", first["run_id"], "more", expect=1)
        self.assertIn(f"send {second['run_id']}", refusal["instead"])
        self.assertNotIn(" resume ", refusal["instead"])
        self.cli_json("stop", "--run", second["run_id"])

    def test_finishing_run_refusals_do_not_point_at_each_other(self):
        reply = self.start("x [[fake:reply:done]] [[fake:linger:8]]")
        self.wait_state(reply["run_id"], states=("finishing",))
        send = self.cli_json("send", reply["run_id"], "more", expect=1)
        self.assertIn("result --run", send["instead"])
        self.assertIn("--wait", send["instead"])
        resume = self.cli_json("resume", reply["run_id"], "more", expect=1)
        # The answer is in: a stop would record it as interrupted, so the wait comes first and the stop is only the alternative.
        self.assertIn("result --run", resume["instead"])
        self.assertIn("--wait", resume["instead"])
        self.assertIn("stop --run", resume["or"])
        self.assertNotIn(" send ", resume["instead"] + resume["or"])
        self.cli_json("stop", "--run", reply["run_id"])

    def test_live_run_resume_points_to_send(self):
        reply = self.start("x [[fake:sleep:30]]")
        refusal = self.cli_json("resume", reply["run_id"], "more", expect=1)
        self.assertIn(f"send {reply['run_id']}", refusal["instead"])
        self.cli_json("stop", "--run", reply["run_id"])

    def test_limit(self):
        reply = self.start("x [[fake:sleep:30]]")
        for i in range(16):
            self.cli_json("send", reply["run_id"], f"m{i}")
        refusal = self.cli_json("send", reply["run_id"], "one too many", expect=1)
        self.assertIn("16", refusal["error"])
        self.assertNotIn(" send ", refusal["instead"])
        self.cli_json("stop", "--run", reply["run_id"])

    def test_arguments_and_inside_a_worker(self):
        reply = self.start("x [[fake:sleep:5]]")
        self.assertEqual(self.cli_json("send", reply["run_id"], "  ", expect=2)["help"], "send --help")
        self.cli_json("send", expect=2)
        inside = self.cli_json("send", reply["run_id"], "hi", expect=1, env={"DELEGATE_RUN": "other"})
        self.assertIn("worker", inside["error"])
        self.cli_json("stop", "--run", reply["run_id"])


class GatedStdin:
    """A worker's stdin whose flush waits for `gate`: the write of a message completes only when the test says."""

    def __init__(self):
        import threading

        self.gate, self.flushed = threading.Event(), threading.Event()

    def write(self, data):
        pass

    def flush(self):
        self.gate.wait(10)
        self.flushed.set()

    def close(self):
        pass


class ConfirmationAfterTheOutcome(unittest.TestCase):
    """GPT review of the send fixes: a write that completes while the supervisor records the outcome, its confirmation waiting for the run's lock, is undelivered in the outcome, and the late confirmation does not contradict it."""

    def test_a_write_confirmed_after_the_outcome_stays_undelivered(self):
        import shutil
        import tempfile
        import threading
        import types
        from pathlib import Path

        reg = engine("delegate.registry")
        claude = engine("delegate.engine").adapter("claude")
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        _, run_dir, _ = reg.publish_run(str(root / "runs"), "t", lambda runs: None, lambda r, d: {"engine": "claude", "native_id": "s", "state": "running", "stdin_open": True, "settings": {"profile": "write"}})
        reg.enqueue(run_dir, "u1", "hello")
        stdin = GatedStdin()
        driver = claude.driver(run_dir, reg.read_meta(run_dir), types.SimpleNamespace(stdin=stdin, pid=None, poll=lambda: None))
        driver.bind(types.SimpleNamespace(lock=threading.Lock(), lines=0, last_line_at=time.time()))
        driver.tick()  # hands u1 to the writer, whose flush waits on the gate

        def finish(m):
            # As the supervisor records an outcome, under the run's lock: the write completes meanwhile, and its confirmation waits for the lock.
            stdin.gate.set()
            self.assertTrue(stdin.flushed.wait(10))
            time.sleep(0.3)
            m.update(driver.outcome(m, -9, ""))

        reg.update_meta(run_dir, finish)
        time.sleep(0.5)  # the confirmation, let through the lock now
        meta = reg.read_meta(run_dir)
        self.assertEqual(meta["undelivered"], ["u1"])
        self.assertIsNone(meta["messages"][0]["delivered_at"], "a confirmation after the outcome does not contradict it")

