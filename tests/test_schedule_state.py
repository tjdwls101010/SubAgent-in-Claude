"""The schedule state store (K4·K19): which automatic trigger fills a slot, the one retry a failed read-only slot gets, the hand-over from a job's Run step to its Finish step, and rebuilding all of it from the runs when the state file is lost. The cases are the plan's K4 walk-through."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from support.harness import engine

registry = engine("delegate.registry")

S3, S4 = 1794852000, 1794855600  # 03:00 and 04:00 KST, 2026-11-17


class Claims(unittest.TestCase):
    def setUp(self):
        self.reg = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.reg, ignore_errors=True)

    def entry(self, profile="read-only", trigger="dispatcher"):
        return {"trigger": trigger, "mode": "run", "profile": profile}

    def run_for(self, job, slot, state, retry_of=None, live=False):
        """A run of this schedule as `__scheduled run` publishes it, ended in `state`."""
        origin = {"kind": "schedule", "name": "nightly", "trigger": "dispatcher", "slot": slot, "job": job, "url": f"https://example.invalid/runs/{job}"}
        if retry_of:
            origin["retry_of"] = retry_of
        meta = {"state": state, "settings": {"profile": "read-only"}, "origin": origin}
        if live:
            meta["supervisor_pid"] = os.getpid()
        registry.publish_run(self.reg, f"s-{job}", lambda existing: None, lambda rid, d: meta)

    def claim(self, slot, job, **kw):
        return registry.claim_slot(self.reg, "nightly", slot, job, self.entry(**kw))

    def test_first_trigger_fills_the_slot_and_a_late_one_is_skipped(self):
        # ① the 03:00 dispatcher runs; the backup arriving at 05:47 computes the same slot and skips
        self.assertEqual(self.claim(S3, "1-1")["decision"], "first")
        self.run_for("1-1", S3, "completed")
        late = self.claim(S3, "2-1")
        self.assertEqual(late["decision"], "skip")
        self.assertEqual(late["previous"]["job"], "1-1")
        self.assertEqual(late["previous"]["url"], "https://example.invalid/runs/1-1")
        self.assertEqual(registry.read_job(self.reg, "nightly", "2-1")["outcome"]["kind"], "skipped")

    def test_a_recovered_slot_runs_once_however_old_it_is(self):
        # More slots than the record keeps ran after it: a recovery of that old slot fills it once, and a second dispatch of it finds it filled.
        base = S3 - 100 * 300
        for n in range(60):
            self.claim(S3 + n * 300, f"{n}-1")
        first = registry.claim_slot(self.reg, "nightly", base, "r-1", self.entry(), exact=True)
        again = registry.claim_slot(self.reg, "nightly", base, "r-2", self.entry(), exact=True)
        self.assertEqual((first["decision"], again["decision"]), ("first", "skip"))

    def test_a_pruned_slot_that_ran_is_not_recovered_again(self):
        self.claim(S3, "1-1")
        self.run_for("1-1", S3, "completed")
        for n in range(1, 60):
            self.claim(S3 + n * 300, f"{n + 1}-1")
        self.assertNotIn(str(S3), registry.schedule_state(self.reg, "nightly")["slots"], "the record keeps only the newest slots")
        self.assertEqual(registry.claim_slot(self.reg, "nightly", S3, "r-1", self.entry(), exact=True)["decision"], "skip")

    def test_a_pruned_slot_recovered_as_its_retry_is_retried_once(self):
        # The failed read-only slot has left the record; its recovery is its one retry, whose start is refused: a second recovery is not another retry.
        self.claim(S3, "1-1")
        self.run_for("1-1", S3, "failed")
        for n in range(1, 60):
            self.claim(S3 + n * 300, f"{n + 1}-1")
        self.assertEqual(registry.claim_slot(self.reg, "nightly", S3, "r-1", self.entry(), exact=True)["decision"], "retry")
        registry.update_job(self.reg, "nightly", "r-1", {"outcome": {"kind": "refused", "error": "no commit"}})
        self.assertEqual(registry.claim_slot(self.reg, "nightly", S3, "r-2", self.entry(), exact=True)["decision"], "skip")

    def old_registry(self, name, ended):
        """The `claude` skill's record of `name`: slot S3 filled by a read-only job whose run ended `ended`."""
        old = Path(self.reg) / "old"
        (old / ".schedules" / name).mkdir(parents=True)
        (old / ".schedules" / name / "state.json").write_text(json.dumps({"name": name, "last_slot": S3, "slots": {str(S3): {"job": "1-1", "retried": None}}, "jobs": {"1-1": {"trigger": "dispatcher", "profile": "read-only", "slot": S3, "run_id": "old-run"}}, "definition": "d0"}))
        (old / "old-run").mkdir(exist_ok=True)
        (old / "old-run" / "meta.json").write_text(json.dumps({"run_id": "old-run", "state": ended}))
        return str(old)

    def test_a_taken_over_slot_is_decided_by_how_its_old_run_ended(self):
        # The record names runs that live in the old skill's registry: how each ended was recorded when it was taken over.
        for ended, decision in (("failed", "retry"), ("completed", "skip")):
            with self.subTest(ended=ended):
                name = f"moved-{ended}"
                with registry.schedule_lock(self.reg, name):
                    registry.take_over_legacy_state(self.reg, name, self.old_registry(name, ended), keep_definition=True)
                self.assertEqual(registry.schedule_state(self.reg, name)["definition"], "d0", "a move keeps the definition")
                got = registry.claim_slot(self.reg, name, S3, "2-1", self.entry())
                self.assertEqual(got["decision"], decision)

    def test_a_takeover_never_replaces_a_record_this_skill_wrote(self):
        self.claim(S3, "1-1")
        before = registry.schedule_state(self.reg, "nightly")["jobs"]
        with registry.schedule_lock(self.reg, "nightly"):
            registry.take_over_legacy_state(self.reg, "nightly", self.old_registry("nightly", "completed"))
        self.assertEqual(registry.schedule_state(self.reg, "nightly")["jobs"], before)

    def test_an_older_slot_after_a_newer_one_is_skipped(self):
        # ② an hourly backup 70 minutes late: the 04:00 slot already ran, so the 03:00 trigger finds nothing to fill
        self.claim(S4, "1-1")
        self.assertEqual(self.claim(S3, "2-1")["decision"], "skip")

    def test_triggers_queued_while_asleep_run_once(self):
        # ③ three slots missed: the wake-up dispatcher fills the newest, the queued backups skip
        self.assertEqual(self.claim(S4, "9-1")["decision"], "first")
        self.assertEqual([self.claim(S4, f"{n}-1")["decision"] for n in (6, 7, 8)], ["skip"] * 3)

    def test_a_backup_fills_a_slot_the_dispatcher_missed(self):
        # ④ the dispatcher's gh call failed: the backup is the first trigger for the slot
        self.assertEqual(self.claim(S3, "2-1", trigger="schedule")["decision"], "first")

    def test_a_failed_read_only_slot_is_retried_once(self):
        self.claim(S3, "1-1")
        self.run_for("1-1", S3, "failed")
        again = self.claim(S3, "2-1")
        self.assertEqual(again["decision"], "retry")
        self.assertEqual(again["previous"]["job"], "1-1")
        self.assertEqual(registry.read_job(self.reg, "nightly", "2-1")["retry_of"], "1-1")
        self.run_for("2-1", S3, "failed", retry_of="1-1")
        self.assertEqual(self.claim(S3, "3-1")["decision"], "skip")

    def test_no_retry_for_writers_stopped_runs_or_live_runs(self):
        for state, profile, live in (("failed", "write", False), ("interrupted", "read-only", False), ("completed", "read-only", False), ("running", "read-only", True)):
            with self.subTest(state=state, profile=profile):
                self.setUp()
                self.claim(S3, "1-1", profile=profile)
                self.run_for("1-1", S3, state, live=live)
                self.assertEqual(self.claim(S3, "2-1", profile=profile)["decision"], "skip")

    def test_a_refusal_before_the_worker_counts_as_not_completed(self):
        # K28 ④: the slot was taken, then the start was refused: read-only retries once, a writer does not
        self.claim(S3, "1-1")
        registry.update_job(self.reg, "nightly", "1-1", {"outcome": {"kind": "refused", "error": "no commit"}})
        self.assertEqual(self.claim(S3, "2-1")["decision"], "retry")
        self.setUp()
        self.claim(S3, "1-1", profile="write")
        registry.update_job(self.reg, "nightly", "1-1", {"outcome": {"kind": "refused", "error": "no commit"}})
        self.assertEqual(self.claim(S3, "2-1", profile="write")["decision"], "skip")

    def test_the_retry_follows_the_profile_of_the_job_that_failed(self):
        # The schedule went from write to read-only between the two triggers: the writer's half-done slot is not tried again.
        self.claim(S3, "1-1", profile="write")
        registry.update_job(self.reg, "nightly", "1-1", {"outcome": {"kind": "refused", "error": "below the version floor"}})
        self.assertEqual(self.claim(S3, "2-1", profile="read-only")["decision"], "skip")

    def test_a_lost_state_file_is_rebuilt_from_the_runs(self):
        self.claim(S3, "1-1")
        self.run_for("1-1", S3, "failed")
        self.claim(S3, "2-1")
        self.run_for("2-1", S3, "failed", retry_of="1-1")
        shutil.rmtree(Path(registry.schedule_dir(self.reg, "nightly")))
        self.assertIsNone(registry.schedule_state(self.reg, "nightly"))
        self.assertEqual(self.claim(S3, "3-1")["decision"], "skip", "the slot's retry is known from the retry run's origin")
        self.assertEqual(registry.schedule_state(self.reg, "nightly")["last_slot"], S3)

    def test_manual_jobs_are_recorded_without_a_slot(self):
        self.claim(S3, "1-1")
        registry.record_job(self.reg, "nightly", "5-1", {"trigger": "manual", "mode": "run", "profile": "read-only"})
        self.assertEqual(registry.schedule_state(self.reg, "nightly")["last_slot"], S3)
        self.assertEqual(registry.read_job(self.reg, "nightly", "5-1")["trigger"], "manual")
        self.assertIsNone(registry.read_job(self.reg, "nightly", "6-1"))

    def test_runs_are_found_by_their_job(self):
        self.run_for("1-1", S3, "completed")
        self.run_for("2-1", S4, "completed")
        found = registry.find_by_origin_job(self.reg, "2-1")
        self.assertEqual(len(found), 1)
        self.assertEqual(registry.read_meta(found[0])["origin"]["slot"], S4)
        self.assertEqual(registry.find_by_origin_job(self.reg, "3-1"), [])


class TakenOver(unittest.TestCase):
    """A schedule taken over from the `claude` skill, whose registry has the same format: a job its command still runs, or a slot its record no longer holds, is not run a second time by this skill."""

    def setUp(self):
        self.reg, self.old = tempfile.mkdtemp(), tempfile.mkdtemp()
        for d in (self.reg, self.old):
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)

    def entry(self, profile="write"):
        return {"trigger": "schedule", "mode": "run", "profile": profile}

    def take_over(self):
        with registry.schedule_lock(self.reg, "nightly"):
            self.assertTrue(registry.take_over_legacy_state(self.reg, "nightly", self.old))

    def old_run(self, job, slot, state="completed"):
        origin = {"kind": "schedule", "name": "nightly", "trigger": "dispatcher", "slot": slot, "job": job}
        registry.publish_run(self.old, f"s-{job}", lambda existing: None, lambda rid, d: {"state": state, "settings": {"profile": "write"}, "origin": origin})

    def test_a_slot_the_old_command_fills_after_the_takeover_is_skipped(self):
        # The old command filled 03:00; a job of it queued before the takeover runs after it and fills 04:00 in its own registry.
        registry.claim_slot(self.old, "nightly", S3, "1-1", self.entry())
        self.take_over()
        self.assertEqual(registry.claim_slot(self.old, "nightly", S4, "2-1", self.entry())["decision"], "first")
        self.old_run("2-1", S4)
        self.assertEqual(registry.claim_slot(self.reg, "nightly", S4, "3-1", self.entry())["decision"], "skip")

    def test_a_read_only_slot_the_old_command_failed_after_the_takeover_is_retried_once(self):
        registry.claim_slot(self.old, "nightly", S3, "1-1", self.entry("read-only"))
        self.take_over()
        registry.claim_slot(self.old, "nightly", S4, "2-1", self.entry("read-only"))
        self.old_run("2-1", S4, state="failed")
        self.assertEqual([registry.claim_slot(self.reg, "nightly", S4, j, self.entry("read-only"))["decision"] for j in ("3-1", "4-1")], ["retry", "skip"])

    def test_a_recovery_of_a_slot_the_old_record_pruned_is_skipped(self):
        self.old_run("0-1", S3)
        for n in range(61):
            registry.claim_slot(self.old, "nightly", S3 + n * 300, f"{n}-1", self.entry())
        self.assertNotIn(str(S3), registry.schedule_state(self.old, "nightly")["slots"])
        self.take_over()
        self.assertEqual(registry.claim_slot(self.reg, "nightly", S3, "r-1", self.entry(), exact=True)["decision"], "skip")

    def test_a_recovery_of_an_old_failed_slot_this_record_pruned_is_retried_once(self):
        # The old record still holds a failed read-only 03:00; this record has pruned it. Its recovery is the one retry, refused before a worker: a second recovery is not another retry.
        registry.claim_slot(self.old, "nightly", S3, "1-1", self.entry("read-only"))
        self.old_run("1-1", S3, state="failed")
        self.take_over()
        for n in range(1, 61):
            registry.claim_slot(self.reg, "nightly", S3 + n * 300, f"{n + 1}-1", self.entry())
        self.assertNotIn(str(S3), registry.schedule_state(self.reg, "nightly")["slots"])
        self.assertEqual(registry.claim_slot(self.reg, "nightly", S3, "r-1", self.entry("read-only"), exact=True)["decision"], "retry")
        registry.update_job(self.reg, "nightly", "r-1", {"outcome": {"kind": "refused", "error": "no commit"}})
        self.assertEqual(registry.claim_slot(self.reg, "nightly", S3, "r-2", self.entry("read-only"), exact=True)["decision"], "skip")

    def test_an_old_registry_removed_after_the_takeover_is_not_made_again(self):
        registry.claim_slot(self.old, "nightly", S3, "1-1", self.entry())
        self.take_over()
        shutil.rmtree(self.old)
        self.assertEqual(registry.claim_slot(self.reg, "nightly", S4, "2-1", self.entry())["decision"], "first")
        self.assertFalse(Path(self.old).exists(), "a claim reads the old registry and never creates it")

    def test_releasing_an_old_definition_where_the_old_skill_never_ran_creates_nothing(self):
        absent = Path(self.old) / "never"
        self.assertFalse(registry.release_legacy_definition("nightly", absent))
        self.assertFalse(absent.exists())


class Removal(unittest.TestCase):
    """A state directory is deleted for the schedule whose name its state.json records, to the letter: on a file system that ignores case, `Nightly` and `nightly` are one directory."""

    def setUp(self):
        self.reg = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.reg, ignore_errors=True)
        self.dir = Path(registry.schedule_dir(self.reg, "nightly"))

    def test_its_own_state_goes(self):
        registry.record_job(self.reg, "nightly", "1-1", {"trigger": "manual", "mode": "run", "profile": "read-only"})
        self.assertEqual(json.loads((self.dir / "state.json").read_text())["name"], "nightly")
        self.assertIsNone(registry.remove_schedule_state(self.reg, "nightly"))
        self.assertFalse(self.dir.exists())

    def test_another_schedules_state_stays(self):
        self.dir.mkdir(parents=True)
        (self.dir / "state.json").write_text(json.dumps({"name": "NIGHTLY", "last_slot": None, "slots": {}, "jobs": {}}))
        self.assertEqual(registry.remove_schedule_state(self.reg, "nightly"), "NIGHTLY")
        self.assertTrue((self.dir / "state.json").exists())

    def test_a_directory_that_names_no_schedule_goes(self):
        for content in (None, "{not json", json.dumps({"jobs": {}})):
            with self.subTest(content=content):
                self.dir.mkdir(parents=True)
                (self.dir / "summary-1-1.md").write_text("x")
                if content is not None:
                    (self.dir / "state.json").write_text(content)
                self.assertIsNone(registry.remove_schedule_state(self.reg, "nightly"))
                self.assertFalse(self.dir.exists())


if __name__ == "__main__":
    unittest.main()
