"""A Codex run's life: detached start, every terminal state, the stop ladder and the sweep of what Codex leaves behind, and what a dead supervisor means.

Carried from codex in claude e8461a1 tests/test_run_lifecycle.py. Dropped there with reason: the unit test of the old ladder's injectable `kill` (the ladder is now the shared one, observed here through what reaches the processes), `codex_elapsed_seconds` (a row's `elapsed_seconds` is the run's time), and the legacy `waiting` runs (the old registries are not read).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

from support.harness import CODEX_FIXTURES as FIXTURES, Sandbox


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


class CodexCase(Sandbox):
    def meta(self, run_id):
        return json.loads((self.run_dir(run_id) / "meta.json").read_text())

    def write_meta(self, run_id, meta):
        (self.run_dir(run_id) / "meta.json").write_text(json.dumps(meta))

    def running(self, *args, hang=60, **env):
        """A codex run that stays in its turn, once both pids are recorded: (reply, meta)."""
        out = self.codex("start", *args, env={"FAKE_CODEX_HANG": str(hang), **{k: str(v) for k, v in env.items()}})
        m = self.wait_for(lambda: (lambda m: m if m.get("worker_pid") and m.get("state") == "running" else None)(self.meta(out["run_id"])), timeout=30, what="the run to be running")
        return out, m

    def grandchild(self, pidfile):
        pid = self.wait_for(lambda: pidfile.exists() and pidfile.read_text().strip() and int(pidfile.read_text()), timeout=20, what="the run's descendant")
        self.addCleanup(lambda: alive(pid) and os.kill(pid, signal.SIGKILL))
        return pid


class DetachedStart(CodexCase):
    def test_start_returns_a_handle_while_the_turn_is_still_running(self):
        t0 = time.monotonic()
        out = self.codex("start", "x", env={"FAKE_CODEX_HANG": "30"})
        self.assertLess(time.monotonic() - t0, 10, "start waited for the turn")
        self.assertEqual(out["state"], "running")
        self.assertTrue(out["native_id"])
        self.assertEqual(out["engine"], "codex")
        self.cli_json("stop", "--run", out["run_id"])

    def test_the_run_finishes_after_the_launcher_has_exited(self):
        out = self.codex("start", "x")
        row = self.wait_state(out["run_id"])
        self.assertEqual((row["state"], row["exit_code"]), ("completed", 0))
        self.assertEqual(row["native_id"], out["native_id"])
        self.assertIsNotNone(row["ended"])

    def test_stdin_is_closed_and_the_prompt_follows_the_terminator(self):
        out = self.codex("start", "--", "--looks-like-a-flag")
        self.wait_state(out["run_id"])
        call = self.codex_calls()[-1]
        self.assertFalse(call["stdin_is_tty"])
        self.assertEqual(call["argv"][-2], "--")
        self.assertTrue(call["argv"][-1].endswith("\n\n--looks-like-a-flag"))
        self.assertEqual(call["cwd"], str(self.project))


class TerminalStates(CodexCase):
    def test_a_nonzero_exit_is_failed_and_the_result_says_so(self):
        out = self.codex("start", "x", env={"FAKE_CODEX_EXIT": "4"})
        header, _ = self.result_view("--run", out["run_id"], "--wait", "--wait-timeout", "30")
        self.assertEqual((header["state"], header["exit_code"]), ("failed", 4))
        self.assertEqual(self.status(out["run_id"])["exit_code"], 4)

    def test_turn_failed_is_surfaced_without_reading_the_log(self):
        out = self.codex("start", "x", env={"FAKE_CODEX_FIXTURE": str(FIXTURES / "turn-failed.jsonl"), "FAKE_CODEX_EXIT": "1"})
        row = self.wait_state(out["run_id"])
        self.assertIn("rate limit", row["turn_failed"])
        self.assertIn("rate limit", self.result_view("--run", out["run_id"])[0]["turn_failed"])

    def test_stderr_is_surfaced_without_codexs_routine_stdin_notice(self):
        out = self.codex("start", "x", env={"FAKE_CODEX_STDERR": "boom: config broken"})
        row = self.wait_state(out["run_id"])
        self.assertIn("boom: config broken", row["stderr_tail"])
        self.assertNotIn("Reading additional input", row["stderr_tail"])

    def test_a_deadline_is_its_own_state(self):
        out = self.codex("start", "--timeout", "1.5", "x", env={"FAKE_CODEX_HANG": "60"})
        row = self.wait_state(out["run_id"], timeout=30)
        self.assertEqual(row["state"], "timed_out")
        self.assertIn("timed out", row["error"])

    def test_the_deadline_counts_from_launch_not_from_the_thread_id(self):
        t0 = time.monotonic()
        out = self.codex("start", "--timeout", "1.5", "x", env={"FAKE_CODEX_PRE_DELAY": "8", "FAKE_CODEX_HANG": "60"}, expect=1)
        self.assertEqual(out["state"], "timed_out", "it ended before codex reported a thread: a start that failed")
        self.assertEqual(self.wait_state(out["run_id"], timeout=30)["state"], "timed_out")
        self.assertLess(time.monotonic() - t0, 8)

    def test_a_deadline_ends_the_whole_process_group(self):
        cases = [({}, "codex exits on SIGINT and leaves a descendant that ignores it"), ({"FAKE_CODEX_IGNORE_SIGINT": "1"}, "codex and its descendant ignore SIGINT"), ({"FAKE_CODEX_IGNORE_SIGINT": "1", "FAKE_CODEX_IGNORE_SIGTERM": "1"}, "only SIGKILL ends them")]
        for n, (env, why) in enumerate(cases):
            with self.subTest(why=why):
                pidfile = self.root / f"grandchild-{n}.pid"
                out = self.codex("start", "--timeout", "1", "x", env={"FAKE_CODEX_HANG": "60", "FAKE_CODEX_GRANDCHILD": str(pidfile), **env})
                grandchild = self.grandchild(pidfile)
                self.assertEqual(self.wait_state(out["run_id"], timeout=40)["state"], "timed_out")
                self.assertTrue(self.wait_for(lambda: not alive(grandchild), timeout=5, what="the descendant to be gone"))
                self.assertFalse(alive(self.meta(out["run_id"])["worker_pid"]))

    def test_stop_ends_a_descendant_its_codex_left_behind(self):
        pidfile = self.root / "grandchild.pid"
        out, _m = self.running("x", FAKE_CODEX_GRANDCHILD=pidfile)
        grandchild = self.grandchild(pidfile)
        res = self.cli_json("stop", "--run", out["run_id"])["stopped"][0]
        self.assertTrue(self.wait_for(lambda: not alive(grandchild), timeout=10, what="the descendant to be gone"))
        self.assertEqual(res["signals_sent"], ["SIGINT", "SIGKILL"], "what was sent is what is reported")

    def test_a_timed_run_that_is_stopped_is_interrupted_not_timed_out(self):
        out, _m = self.running("--timeout", "600", "x")
        self.cli_json("stop", "--run", out["run_id"])
        self.assertEqual(self.wait_state(out["run_id"])["state"], "interrupted")

    def test_killing_the_whole_process_group_leaves_nothing_stuck_running(self):
        out, m = self.running("x")
        os.killpg(int(m["worker_pgid"]), signal.SIGKILL)
        os.kill(int(m["supervisor_pid"]), signal.SIGKILL)
        row = self.wait_state(out["run_id"])
        self.assertEqual(row["state"], "orphaned")
        self.assertIsNotNone(row["ended"])

    def test_a_dead_codex_leaves_its_supervisor_to_record_the_outcome(self):
        out, m = self.running("x")
        os.kill(int(m["worker_pid"]), signal.SIGKILL)
        self.assertEqual(self.wait_state(out["run_id"])["state"], "failed")


class StopLadder(CodexCase):
    """Signals go to the run's recorded process group, SIGINT first, escalating only if Codex does not exit."""

    def test_sigint_alone_ends_a_cooperative_run_and_keeps_its_thread(self):
        out, _m = self.running("x")
        res = self.cli_json("stop", "--run", out["run_id"])["stopped"][0]
        self.assertEqual(res["signals_sent"], ["SIGINT"])
        self.assertEqual(res["native_id"], out["native_id"])
        self.assertEqual(self.wait_state(out["run_id"])["state"], "interrupted")

    def test_the_ladder_escalates_when_sigint_is_ignored(self):
        out, m = self.running("x", FAKE_CODEX_IGNORE_SIGINT=1)
        res = self.cli_json("stop", "--run", out["run_id"])["stopped"][0]
        self.assertEqual(res["signals_sent"], ["SIGINT", "SIGTERM"])
        self.assertFalse(alive(m["worker_pid"]))

    def test_stopping_one_run_leaves_a_concurrent_one_running(self):
        a, am = self.running("--label", "a", "x")
        b, bm = self.running("--label", "b", "x")
        self.assertNotEqual(am["worker_pgid"], bm["worker_pgid"])
        self.cli_json("stop", "--run", a["run_id"])
        self.wait_state(a["run_id"])
        self.assertEqual(self.status(b["run_id"])["state"], "running")
        self.assertTrue(alive(bm["worker_pid"]))
        self.cli_json("stop", "--run", b["run_id"])

    def test_stopping_a_finished_run_does_not_rewrite_its_outcome(self):
        out = self.codex("start", "x")
        self.wait_state(out["run_id"])
        self.assertEqual(self.cli_json("stop", "--run", out["run_id"])["stopped"][0]["state"], "completed")
        self.assertEqual(self.status(out["run_id"])["state"], "completed")


class AnOrphanThatIsStillWriting(CodexCase):
    def orphan(self):
        out, m = self.running("x")
        os.kill(int(m["supervisor_pid"]), signal.SIGKILL)
        self.wait_for(lambda: not alive(m["supervisor_pid"]), timeout=10, what="the supervisor to be gone")
        self.assertEqual(self.status(out["run_id"])["stored_state"], "orphaned")
        self.assertTrue(alive(m["worker_pid"]), "fixture needs codex to outlive its supervisor")
        return out, m

    def test_status_calls_it_orphaned_but_counts_it_as_live(self):
        out, _m = self.orphan()
        self.assertIn(out["run_id"], self.cli_json("status")["live"])
        self.assertTrue(self.status(out["run_id"])["still_writing"])
        self.cli_json("stop", "--run", out["run_id"])

    def test_stop_reaches_its_codex_through_the_process_group(self):
        out, m = self.orphan()
        self.assertIn(out["run_id"], [s["run_id"] for s in self.cli_json("stop", "--run", out["run_id"])["stopped"]])
        self.assertTrue(self.wait_for(lambda: not alive(m["worker_pid"]), timeout=10, what="codex to be gone"))

    def test_once_its_codex_is_gone_the_wait_ends_on_orphaned(self):
        out, m = self.orphan()
        os.kill(int(m["worker_pid"]), signal.SIGKILL)
        self.wait_for(lambda: not alive(m["worker_pid"]), timeout=10, what="codex to be gone")
        header, _ = self.result_view("--run", out["run_id"], "--wait", "--wait-timeout", "10")
        self.assertEqual(header["state"], "orphaned")
        self.assertNotIn("next", header, "nothing writes any more, so the result is final")


class APidAnotherProcessNowHolds(CodexCase):
    """The system hands an exited process's pid to the next process it starts, so a recorded pid that answers may be someone else's."""

    def stand_in(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        return p.pid

    def test_a_finished_run_whose_worker_pid_was_reused_is_not_live(self):
        out = self.codex("start", "x")
        self.wait_state(out["run_id"])
        self.write_meta(out["run_id"], {**self.meta(out["run_id"]), "worker_pid": self.stand_in()})
        self.assertNotIn(out["run_id"], self.cli_json("status")["live"])
        self.assertNotIn("still_writing", self.status(out["run_id"]))
        again = self.cli_json("resume", out["run_id"], "again")
        self.wait_state(again["run_id"])

    def test_a_running_run_whose_supervisor_pid_was_reused_is_orphaned(self):
        out = self.codex("start", "x")
        self.wait_state(out["run_id"])
        self.write_meta(out["run_id"], {**self.meta(out["run_id"]), "state": "running", "supervisor_pid": self.stand_in()})
        self.assertEqual(self.status(out["run_id"])["state"], "orphaned")
