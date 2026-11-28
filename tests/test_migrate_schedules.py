"""The move of the `claude` skill's schedules to this skill (migrate_schedules/, the plan's transition step 3), against the fake gh and launchctl: the read-only inventory and its classes, the steps in order with the end state checked by `verify`, a queue that must drain first, a step that failed partway run again, a rollback after the new side ran, and a moved one-off ended by its own Finish."""

from __future__ import annotations

import json
import plistlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from support.harness import ENTRY, REPO as SKILL_REPO, engine
from test_schedule_define import AT, NEW, REPO, TZ, Scheduling

github = engine("delegate.github")
JOB = SKILL_REPO / "migrate_schedules" / "run.py"
LEGACY_HEADER = "# Written by the claude skill's `schedule` command. Run it again with the same --name to change it; `unschedule` removes it."
OLD_PREFIX = "claude-schedule.someone-project."
NEW_PREFIX = "delegate-schedule.someone-project."


def ts(day, hour, minute=0):
    return int(datetime(2026, 11, day, hour, minute, tzinfo=ZoneInfo(TZ)).timestamp())


T0 = ts(17, 5, 47)


class MoveWorld(Scheduling):
    """A repository with four schedules of the claude skill — recurring, upcoming, finished, held — their old records, and the steps run as the job runs them."""

    def legacy_text(self, name, *, cron=None, at=None, profile="full", definition="d0d0d0d0"):
        spec = {"name": name, "cron": cron, "at": at, "timezone": TZ, "profile": profile, "model": "sonnet", "effort": "high", "timeout": 3600, "worktree": False, "runs_on": ["self-hosted"], "project": str(self.project), "cli": "/Users/someone/.claude/skills/claude/scripts/cli.py", "definition": definition, "prompt": f"work for {name}\n", "schema": None}
        text = github.render_workflow(spec)
        # What the claude skill wrote: its header over the same spec line (the job reads nothing else of an old file).
        return LEGACY_HEADER + text[text.index("\n"):]

    def old_registry(self, name, state, runs=()):
        d = self.project / ".claude-runs" / ".schedules" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(json.dumps({"name": name, **state}))
        for run_id, run_state in runs:
            r = self.project / ".claude-runs" / run_id
            r.mkdir(parents=True, exist_ok=True)
            (r / "meta.json").write_text(json.dumps({"run_id": run_id, "state": run_state, "origin": {"kind": "schedule", "name": name}}))

    def plant_old(self, name, text, state="active"):
        self.plant(f"{name}.yml", text, state=state)
        label = OLD_PREFIX + name
        self.plant_plist(label=label)
        return label

    def setUp(self):
        super().setUp()
        nightly = self.legacy_text("nightly", cron="0 3 * * *")
        self.plant_old("nightly", nightly)
        slot = ts(17, 3)
        self.old_registry("nightly", {"last_slot": slot, "slots": {str(slot): {"job": "1-1", "retried": None}}, "jobs": {"1-1": {"trigger": "dispatcher", "profile": "full", "slot": slot, "run_id": "r-nightly-1", "at": slot + 60}}, "definition": "d0d0d0d0"}, runs=[("r-nightly-1", "completed")])
        # A one-off that never ran yet has no old record at all.
        self.plant_old("later", self.legacy_text("later", at="2026-12-01T06:00"))
        done_slot = ts(16, 6)
        self.plant_old("done", self.legacy_text("done", at="2026-11-16T06:00"), state="disabled_manually")
        self.old_registry("done", {"last_slot": done_slot, "slots": {str(done_slot): {"job": "2-1", "retried": None}}, "jobs": {"2-1": {"trigger": "dispatcher", "profile": "full", "slot": done_slot, "run_id": "r-done-1"}}, "definition": "d0d0d0d0"}, runs=[("r-done-1", "completed")])
        self.plant_old("stuck", self.legacy_text("stuck", at="2026-11-16T07:00"))
        self.old_registry("stuck", {"last_slot": None, "slots": {}, "jobs": {}, "definition": "d0d0d0d0"})
        self.plant("ci.yml", "name: ci\non: push\n")

    def job(self, step, *extra, now=T0, expect=0):
        proc = subprocess.run([sys.executable, str(JOB), step, "--project", str(self.project), "--cli", str(ENTRY), "--now", str(now), *extra], cwd=self.project, env=self.env, capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, expect, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def writes(self):
        return [c["argv"] for c in self.gh_calls() if any(m in c["argv"] for m in ("PUT", "DELETE", "POST")) or c["argv"][:2] == ["workflow", "run"]]

    def through(self, *steps, now=T0):
        out = {}
        for step in steps:
            out[step] = self.job(step, now=now)
        return out


class Move(MoveWorld):
    def test_the_inventory_classifies_and_writes_nothing(self):
        inv = self.job("inventory")
        by = {r["name"]: r for r in inv["schedules"]}
        self.assertEqual({n: r["class"] for n, r in by.items()}, {"nightly": "recurring", "later": "upcoming", "done": "finished", "stuck": "held"})
        self.assertIn("still enabled", by["stuck"]["why"])
        self.assertEqual(by["done"]["completed"], {"job": "2-1", "run_id": "r-done-1"})
        self.assertEqual(by["nightly"]["dispatcher"]["label"], OLD_PREFIX + "nightly")
        self.assertEqual(self.writes(), [])
        self.assertEqual([c for c in self.lc_calls() if c[0] != "print"], [])
        self.assertTrue(self.plist(OLD_PREFIX + "nightly"), "the old dispatcher is where it was")

    def test_the_move_end_to_end(self):
        self.job("inventory")
        self.through("freeze", "state", "publish")
        self.assertIsNone(self.plist(OLD_PREFIX + "nightly"), "the old dispatcher is out of launchd")
        self.assertTrue((self.runs_dir() / ".migration" / "backup" / f"{OLD_PREFIX}nightly.plist").exists(), "and kept for a rollback")
        self.assertEqual(self.repo()["workflows"]["nightly.yml"], "disabled_manually", "frozen until resumed")
        text = self.committed("nightly")
        spec = self.spec_of("nightly")
        self.assertTrue(text.startswith("# Written by the delegate skill's"))
        self.assertEqual((spec["definition"], spec["cli"], spec["concurrency"], spec["profile"]), ("d0d0d0d0", str(ENTRY), "claude-nightly", "full"))
        self.assertNotIn("done.yml", self.files(), "a finished one-off's workflow is deleted")
        self.assertIn("stuck.yml", self.files(), "a held one is left alone")
        state = json.loads((self.runs_dir() / ".schedules" / "nightly" / "state.json").read_text())
        self.assertEqual((state["definition"], state["jobs"]["1-1"]["legacy"], state["jobs"]["1-1"]["legacy_run_state"]), ("d0d0d0d0", True, "completed"))
        later = T0 + 86400  # a day frozen: the next 03:00 passed meanwhile
        self.job("resume", now=later)
        self.assertEqual(self.repo()["workflows"]["nightly.yml"], "active")
        self.assertEqual(self.plist(NEW_PREFIX + "nightly")["Label"], NEW_PREFIX + "nightly")
        dispatched = [c for c in self.gh_calls() if c["argv"][:2] == ["workflow", "run"]]
        self.assertEqual([(c["argv"][2], [a for a in c["argv"] if a.startswith("slot=")]) for c in dispatched], [("nightly.yml", [f"slot={ts(18, 3)}"])], "the missed slot is dispatched as itself")
        check = self.job("verify", now=later)
        self.assertEqual(check["problems"], [])
        held = [r for r in check["schedules"] if r["disposition"] == "held"]
        self.assertEqual([(r["name"], "still enabled" in r["why"]) for r in held], [("stuck", True)], "a held schedule is listed as the exception it is")

    def test_freeze_waits_for_the_queue_to_drain(self):
        self.job("inventory")
        w = self.world()
        w["runs"] = [{"databaseId": 1, "workflowName": "nightly.yml", "status": "queued", "conclusion": None}]
        self.world(w)
        out = self.job("freeze", expect=3)
        waiting = {r["name"]: r for r in out["results"]}["nightly"]
        self.assertEqual(waiting["status"], "waiting")
        state = {r["name"]: r["status"] for r in self.job("state", expect=3)["results"]}
        self.assertEqual(state["nightly"], "waiting", "an old run still queued could fill a slot the copy would miss")
        w = self.world()
        w["runs"][0]["status"] = "completed"
        self.world(w)
        self.assertTrue(all(r["status"] == "done" for r in self.job("freeze")["results"]))

    def test_a_step_that_failed_partway_goes_on_where_it_stopped(self):
        self.job("inventory")
        self.through("freeze", "state")
        self.world(protected=True)
        out = self.job("publish", expect=1)
        self.assertTrue(all(r["status"] == "failed" for r in out["results"]), out)
        self.world(protected=False)
        self.job("publish")
        puts = [c for c in self.gh_calls() if "PUT" in c["argv"] and "contents" in " ".join(c["argv"])]
        self.job("publish")
        self.assertEqual(len([c for c in self.gh_calls() if "PUT" in c["argv"] and "contents" in " ".join(c["argv"])]), len(puts), "what is done is not done again")

    def test_a_rollback_after_the_new_side_ran(self):
        self.job("inventory")
        self.through("freeze", "state", "publish", "resume")
        # The new side fills a slot before the rollback.
        slot = ts(18, 3)
        state_path = self.runs_dir() / ".schedules" / "nightly" / "state.json"
        state = json.loads(state_path.read_text())
        state["slots"][str(slot)] = {"job": "9-1", "retried": None}
        state["jobs"]["9-1"] = {"trigger": "dispatcher", "slot": slot, "profile": "full", "at": slot}
        state["last_slot"] = slot
        state_path.write_text(json.dumps(state))
        old_text = self.legacy_text("nightly", cron="0 3 * * *")
        out = self.job("rollback", "--only", "nightly")
        self.assertEqual(out["results"][0]["note"]["slots_moved_back"], [str(slot)])
        self.assertEqual(self.committed("nightly"), old_text)
        self.assertIsNone(self.plist(NEW_PREFIX + "nightly"))
        self.assertEqual(self.plist(OLD_PREFIX + "nightly")["Label"], OLD_PREFIX + "nightly")
        self.assertEqual(self.repo()["workflows"]["nightly.yml"], "active")
        old = json.loads((self.project / ".claude-runs" / ".schedules" / "nightly" / "state.json").read_text())
        self.assertIn(str(slot), old["slots"], "the old side will not run the slot the new side ran")

    def test_a_moved_one_off_is_ended_by_its_own_finish(self):
        self.job("inventory")
        self.through("freeze", "state", "publish")
        self.job("resume")
        spec = self.spec_of("later")
        when = int(datetime(2026, 12, 1, 6, 1, tzinfo=ZoneInfo(TZ)).timestamp())
        d = self.root / "job" / "7"
        d.mkdir(parents=True)
        env = {"GITHUB_RUN_ID": "7", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": REPO, "GITHUB_SERVER_URL": "https://github.com", "GITHUB_STEP_SUMMARY": str(d / "summary"), "GITHUB_OUTPUT": str(d / "output"), "GH_TOKEN": "t", "DELEGATE_SCHEDULE_MODE": "run", "DELEGATE_SCHEDULE_SOURCE": "dispatcher", "DELEGATE_SCHEDULE_PROMPT": "x", "DELEGATE_TEST_NOW": str(when)}
        args = ["--name", "later", "--project", str(self.project), "--engine", "claude", "--profile", spec["profile"], "--model", spec["model"], "--effort", spec["effort"], "--timeout", "3600", "--at", spec["at"], "--timezone", spec["timezone"], "--workflow", "later.yml", "--definition", spec["definition"]]
        run = self.cli("__scheduled", "run", *args, env=env, timeout=120)
        finish = self.cli("__scheduled", "finish", *args, env=env, timeout=120)
        self.assertEqual((run.returncode, finish.returncode), (0, 0), run.stdout + finish.stdout)
        self.assertEqual(self.repo()["workflows"]["later.yml"], "disabled_manually", (d / "summary").read_text())
        self.assertIsNone(self.plist(NEW_PREFIX + "later"))


class MoveSafety(MoveWorld):
    """What a move must not do halfway (PR⑧ claude review): lose schedules to a second inventory, strand a one-off whose time passes while frozen, forget a commit it made, resume before it published, or report a rollback done that is not."""

    def test_a_second_inventory_is_refused_while_a_move_is_under_way(self):
        self.job("inventory")
        self.through("freeze", "state")
        refused = self.job("inventory", expect=1)
        self.assertEqual(refused["moving"], ["done", "later", "nightly"])

    def test_a_one_off_whose_time_passes_while_frozen_is_still_moved_and_run(self):
        self.job("inventory")
        self.through("freeze", "state")
        past = int(datetime(2026, 12, 1, 7, 0, tzinfo=ZoneInfo(TZ)).timestamp())
        self.job("publish", now=past)
        self.job("resume", now=past)
        dispatched = [c["argv"] for c in self.gh_calls() if c["argv"][:3] == ["workflow", "run", "later.yml"]]
        self.assertEqual([[a for a in c if a.startswith("slot=")] for c in dispatched], [[f"slot={int(datetime(2026, 12, 1, 6, 0, tzinfo=ZoneInfo(TZ)).timestamp())}"]])

    def test_a_commit_made_before_the_journal_was_written_is_recognised(self):
        self.job("inventory")
        self.through("freeze", "state", "publish")
        j = json.loads((self.runs_dir() / ".migration" / "journal.json").read_text())
        del j["schedules"]["nightly"]["published"]  # as if the job stopped right after its commit
        (self.runs_dir() / ".migration" / "journal.json").write_text(json.dumps(j))
        puts = len([c for c in self.gh_calls() if "PUT" in c["argv"] and "contents" in " ".join(c["argv"])])
        self.assertEqual({r["name"]: r["status"] for r in self.job("publish")["results"]}["nightly"], "done")
        self.assertEqual(len([c for c in self.gh_calls() if "PUT" in c["argv"] and "contents" in " ".join(c["argv"])]), puts)
        self.job("rollback", "--only", "nightly")
        self.assertTrue(self.committed("nightly").startswith(LEGACY_HEADER), "the rollback still has the old text")

    def test_resume_before_publish_is_refused_and_touches_nothing(self):
        self.job("inventory")
        self.through("freeze", "state")
        out = self.job("resume", expect=1)
        moved = [r for r in out["results"] if r["name"] in ("later", "nightly")]
        self.assertTrue(moved and all(r["status"] == "failed" and "publish" in r["error"] for r in moved), out)
        self.assertEqual(self.repo()["workflows"]["nightly.yml"], "disabled_manually")
        self.assertIsNone(self.plist(NEW_PREFIX + "nightly"))

    def test_a_rollback_after_a_resume_that_stopped_halfway_removes_the_new_dispatcher(self):
        self.job("inventory")
        self.through("freeze", "state", "publish")
        self.world(fail_dispatch=True)
        self.job("resume", now=T0 + 86400, expect=1)
        self.assertTrue(self.plist(NEW_PREFIX + "nightly"), "installed before the dispatch failed")
        self.world(fail_dispatch=False)
        self.job("rollback", "--only", "nightly", now=T0 + 86400)
        self.assertIsNone(self.plist(NEW_PREFIX + "nightly"))
        self.assertTrue(self.plist(OLD_PREFIX + "nightly"))

    def test_a_rollback_whose_dispatcher_did_not_load_is_not_done(self):
        self.job("inventory")
        self.through("freeze", "state", "publish")
        self.env["FAKE_LAUNCHCTL_FAIL"] = "bootstrap"
        out = self.job("rollback", "--only", "nightly", now=T0 + 3 * 86400, expect=3)
        self.assertEqual(out["results"][0]["status"], "waiting")
        del self.env["FAKE_LAUNCHCTL_FAIL"]
        note = self.job("rollback", "--only", "nightly", now=T0 + 3 * 86400)["results"][0]["note"]
        self.assertEqual(note["missed_slots"], [str(ts(18, 3)), str(ts(19, 3)), str(ts(20, 3))], "slots the old skill cannot fill as themselves are reported")

    def test_verify_fails_while_a_move_is_halfway(self):
        self.job("inventory")
        self.through("freeze", "state", "publish")
        check = self.job("verify", expect=1)
        self.assertTrue(any("nightly: the move stopped" in p for p in check["problems"]), check["problems"])


class MoveReview(MoveWorld):
    """What the PR⑧ GPT review found the move could still get wrong, each reproduced at the job's command line: a slot lost, run twice or left unclaimed, a schedule left disabled or without its dispatcher, or a `verify` or `rollback` that says ok when it is not."""

    def test_a_slot_that_passes_while_resume_runs_is_recovered(self):
        self.job("inventory")
        self.through("freeze", "state", "publish")
        # Resume starts a second before the next 03:00 and, a gh call at a time, installs the dispatcher after it: neither the dispatcher nor the recovery covers that slot unless the recovery reaches up to the install.
        self.world(slow=1)
        self.job("resume", now=ts(18, 3) - 1)
        dispatched = [[a for a in c["argv"] if a.startswith("slot=")] for c in self.gh_calls() if c["argv"][:3] == ["workflow", "run", "nightly.yml"]]
        self.assertEqual(dispatched, [[f"slot={ts(18, 3)}"]])
