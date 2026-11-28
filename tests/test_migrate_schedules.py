"""The move of the `claude` skill's schedules to this skill (migrate_schedules/, the plan's transition step 3), against the fake gh and launchctl: the read-only inventory and its classes, the steps in order with the end state checked by `verify`, a queue that must drain first, a step that failed partway run again, a rollback after the new side ran, and a moved one-off ended by its own Finish."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from support.harness import ENTRY, REPO as SKILL_REPO, engine
from test_schedule_define import AT, NEW, REPO, TZ, Scheduling

github = engine("delegate.github")
registry = engine("delegate.registry")
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
        if expect is not None:
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
        self.job("resume", now=later, expect=3)
        self.assertEqual(self.repo()["workflows"]["nightly.yml"], "active")
        self.assertEqual(self.plist(NEW_PREFIX + "nightly")["Label"], NEW_PREFIX + "nightly")
        dispatched = [c for c in self.gh_calls() if c["argv"][:2] == ["workflow", "run"]]
        self.assertEqual([(c["argv"][2], [a for a in c["argv"] if a.startswith("slot=")]) for c in dispatched], [("nightly.yml", [f"slot={ts(18, 3)}"])], "the missed slot is dispatched as itself")
        # The recovery's run claims its slot, as its Run step does.
        registry.claim_slot(str(self.runs_dir()), "nightly", ts(18, 3), "44000000001-1", {"trigger": "recovery", "profile": "full"}, exact=True)
        check = self.job("verify", now=later, expect=1)
        self.assertEqual([p.split(":")[0] for p in check["problems"]], ["stuck.yml"], "the held one is still active on the old command")
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

    def test_a_drained_schedule_is_not_asked_about_again(self):
        # Once drained its workflow is off and its dispatcher gone, so no run can appear; asking GitHub again for each of 39 schedules on every pass spends the API limit the live dispatchers share.
        self.job("inventory")
        self.job("freeze")
        asked = len([c for c in self.gh_calls() if c["argv"][:2] == ["run", "list"]])
        self.job("freeze")
        self.assertEqual(len([c for c in self.gh_calls() if c["argv"][:2] == ["run", "list"]]), asked)

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

    def test_a_second_inventory_keeps_the_rows_of_a_move_under_way(self):
        first = {r.get("name"): r for r in self.job("inventory")["schedules"]}
        self.through("freeze", "state")
        again = self.job("inventory")
        self.assertEqual(again["kept"], ["done", "later", "nightly"])
        rows = {r.get("name"): r for r in again["schedules"]}
        self.assertEqual({n: rows[n] for n in again["kept"]}, {n: first[n] for n in again["kept"]}, "frozen, they are not read again as held")

    def test_a_one_off_whose_time_passes_while_frozen_is_still_moved_and_run(self):
        self.job("inventory")
        self.through("freeze", "state")
        past = int(datetime(2026, 12, 1, 7, 0, tzinfo=ZoneInfo(TZ)).timestamp())
        self.job("publish", now=past)
        self.job("resume", now=past, expect=3)
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
        self.job("resume", now=ts(18, 3) - 1, expect=3)
        dispatched = [[a for a in c["argv"] if a.startswith("slot=")] for c in self.gh_calls() if c["argv"][:3] == ["workflow", "run", "nightly.yml"]]
        self.assertEqual(dispatched, [[f"slot={ts(18, 3)}"]])

    def test_a_recovery_counts_once_its_run_claims_the_slot(self):
        self.job("inventory")
        self.through("freeze", "state", "publish")
        later, slot = T0 + 86400, ts(18, 3)
        self.job("resume", now=later, expect=3)
        # GitHub took the dispatch, but its run ended (cancelled, failed to start) before it claimed the slot: the slot has not run.
        check = self.job("verify", now=later, expect=1)
        self.assertTrue(any(p.startswith("nightly:") and str(slot) in p for p in check["problems"]), check["problems"])
        w = self.world()
        w["runs"][-1]["status"] = "queued"
        self.world(w)
        self.assertEqual({r["name"]: r["status"] for r in self.job("resume", now=later, expect=3)["results"]}["nightly"], "waiting", "a run still on its way may yet claim it")
        w["runs"][-1]["status"] = "completed"
        self.world(w)
        self.assertEqual({r["name"]: r["status"] for r in self.job("resume", now=later, expect=3)["results"]}["nightly"], "waiting", "dispatched again: resume is not done until a run claims it")
        dispatched = [[a for a in c["argv"] if a.startswith("slot=")] for c in self.gh_calls() if c["argv"][:3] == ["workflow", "run", "nightly.yml"]]
        self.assertEqual(dispatched, [[f"slot={slot}"], [f"slot={slot}"]], "an ended run that claimed nothing is dispatched again")
        registry.claim_slot(str(self.runs_dir()), "nightly", slot, "44000000002-1", {"trigger": "recovery", "profile": "full"}, exact=True)
        self.job("resume", now=later)
        self.assertEqual(len([c for c in self.gh_calls() if c["argv"][:3] == ["workflow", "run", "nightly.yml"]]), 2, "a claimed slot is not dispatched again")
        self.assertEqual([p for p in self.job("verify", now=later, expect=None)["problems"] if p.startswith("nightly")], [])

    def run_one_off(self, name, when, run_id):
        """The moved one-off's Run and Finish steps, as its workflow runs them at `when`."""
        spec = self.spec_of(name)
        d = self.root / "job" / run_id
        d.mkdir(parents=True)
        env = {"GITHUB_RUN_ID": run_id, "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": REPO, "GITHUB_SERVER_URL": "https://github.com", "GITHUB_STEP_SUMMARY": str(d / "summary"), "GITHUB_OUTPUT": str(d / "output"), "GH_TOKEN": "t", "DELEGATE_SCHEDULE_MODE": "run", "DELEGATE_SCHEDULE_SOURCE": "dispatcher", "DELEGATE_SCHEDULE_PROMPT": "x", "DELEGATE_TEST_NOW": str(when)}
        args = ["--name", name, "--project", str(self.project), "--engine", "claude", "--profile", spec["profile"], "--model", spec["model"], "--effort", spec["effort"], "--timeout", "3600", "--at", spec["at"], "--timezone", spec["timezone"], "--workflow", f"{name}.yml", "--definition", spec["definition"]]
        run = self.cli("__scheduled", "run", *args, env=env, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout)
        self.cli("__scheduled", "finish", *args, env=env, timeout=120)

    def test_a_one_off_is_done_only_by_its_own_slot(self):
        self.job("inventory")
        self.through("freeze", "state", "publish")
        # An earlier one-off of the same name ended cleanly on this skill's side; the slot `later` waits for is still ahead.
        registry.update_job(str(self.runs_dir()), "later", "5-1", {"trigger": "dispatcher", "slot": ts(16, 6), "one_off_end": {"disabled": True, "dispatcher": "removed", "done": True, "definition": "0th3r0ne"}})
        self.job("resume")
        self.assertEqual(self.repo()["workflows"]["later.yml"], "active")
        self.assertTrue(self.plist(NEW_PREFIX + "later"))

    def test_a_one_off_completed_on_the_new_side_is_not_switched_back_on(self):
        self.job("inventory")
        self.through("freeze", "state", "publish", "resume")
        when = int(datetime(2026, 12, 1, 6, 1, tzinfo=ZoneInfo(TZ)).timestamp())
        # Its worker completed, but its Finish could not turn the workflow off.
        self.world(fail_disable=True)
        self.run_one_off("later", when, "8")
        self.world(fail_disable=False)
        self.assertEqual(self.repo()["workflows"]["later.yml"], "active")
        self.job("rollback", "--only", "later", now=when + 3600)
        self.assertEqual(self.repo()["workflows"]["later.yml"], "disabled_manually", "its slot ran: back on the old side it stays off")
        self.assertIsNone(self.plist(OLD_PREFIX + "later"))

    def test_a_rollback_hands_back_every_claim_and_retry(self):
        self.job("inventory")
        self.through("freeze", "state", "publish", "resume")
        runs = str(self.runs_dir())
        at = int(datetime(2026, 12, 1, 6, 0, tzinfo=ZoneInfo(TZ)).timestamp())
        # `later` never ran on the old side, so it has no old record; the new side claims its slot (its read-only worker then failed, so the slot keeps its one retry).
        registry.claim_slot(runs, "later", at, "9-1", {"trigger": "dispatcher", "profile": "read-only"})
        # The new side retried the old side's 03:00 slot of `nightly`.
        old_slot = ts(17, 3)
        registry.update_job(runs, "nightly", "9-2", {"trigger": "dispatcher", "profile": "read-only", "slot": old_slot, "retry_of": "1-1"})
        state_path = self.runs_dir() / ".schedules" / "nightly" / "state.json"
        state = json.loads(state_path.read_text())
        state["slots"][str(old_slot)]["retried"] = "9-2"
        state_path.write_text(json.dumps(state))
        self.job("rollback", "--only", "later", "--only", "nightly", now=at + 3600)
        old = self.project / ".claude-runs" / ".schedules"
        later = json.loads((old / "later" / "state.json").read_text())
        self.assertEqual(later["slots"][str(at)]["job"], "9-1", "the old side will not run the slot again as its first")
        self.assertEqual(later["definition"], "d0d0d0d0", "and its Finish still knows the workflow as its own")
        nightly = json.loads((old / "nightly" / "state.json").read_text())
        self.assertEqual(nightly["slots"][str(old_slot)], {"job": "1-1", "retried": "9-2"}, "the slot's one retry is spent")

    def test_a_rollback_leaves_a_workflow_changed_since_the_move_alone(self):
        self.job("inventory")
        self.through("freeze", "state", "publish", "resume")
        # Someone changed the moved workflow afterwards (another prompt, through `schedule`).
        edited = self.committed("nightly").replace("work for nightly", "other work for nightly")
        self.plant("nightly.yml", edited)
        out = self.job("rollback", "--only", "nightly", expect=1)
        self.assertIn("changed", out["results"][0]["error"])
        self.assertEqual(self.committed("nightly"), edited)
        self.assertEqual(self.repo()["workflows"]["nightly.yml"], "active", "and is not switched off on the way")

    def test_a_deleted_finished_one_off_is_rolled_back(self):
        self.job("inventory")
        self.through("freeze", "state", "publish")
        self.assertNotIn("done.yml", self.files())
        self.job("rollback", "--only", "done")
        self.assertEqual(self.committed("done"), self.legacy_text("done", at="2026-11-16T06:00"))
        self.assertEqual(self.repo()["workflows"]["done.yml"], "disabled_manually")
        self.assertIsNone(self.plist(OLD_PREFIX + "done"), "a finished one-off gets no dispatcher back")

    def test_two_steps_at_once_keep_each_others_journal(self):
        self.job("inventory")
        self.through("freeze", "state")
        self.world(slow=0.5)
        procs = []
        for name in ("nightly", "later"):
            procs.append(subprocess.Popen([sys.executable, str(JOB), "publish", "--project", str(self.project), "--cli", str(ENTRY), "--now", str(T0), "--only", name], cwd=self.project, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for p in procs:
            p.communicate(timeout=300)
        self.world(slow=0)
        journal = json.loads((self.runs_dir() / ".migration" / "journal.json").read_text())["schedules"]
        for name in ("nightly", "later"):
            if self.committed(name).startswith("# Written by the delegate skill's"):
                self.assertTrue((journal[name].get("published") or {}).get("old_text"), f"{name} was rewritten on GitHub, and the journal must keep how to undo it")
        self.assertTrue(all(p.returncode == 0 for p in procs), [p.returncode for p in procs])

    def test_the_inventory_reads_a_schedules_text_and_sha_from_one_version(self):
        # Between the listing and the read of its SHA, someone scheduled the finished one-off `done` again (still with the claude skill): the listing has the finished one, the file is the new one.
        finished = self.legacy_text("done", at="2026-11-16T06:00")
        again = self.legacy_text("done", at="2026-12-05T06:00", definition="e1e1e1e1")
        self.plant("done.yml", again, state="disabled_manually", sha="again", graphql_text=finished)
        inv = {r["name"]: r for r in self.job("inventory")["schedules"]}
        self.assertNotEqual(inv["done"]["class"], "finished", inv["done"])
        self.through("freeze", "state", "publish")
        self.assertIn("done.yml", self.files(), "the schedule made again is not deleted")

    def test_a_rollback_waits_for_a_worker_still_running_on_this_mac(self):
        self.job("inventory")
        self.through("freeze", "state", "publish", "resume")
        # Its Actions job was cancelled, and the worker it started goes on here.
        origin = {"kind": "schedule", "name": "nightly", "trigger": "dispatcher", "slot": ts(18, 3), "job": "9-1"}
        worker = subprocess.Popen(["sleep", "300"], start_new_session=True)
        self.addCleanup(lambda: (worker.kill(), worker.wait()))
        run_id, run_dir, _ = registry.publish_run(str(self.runs_dir()), "s-9-1", lambda existing: None, lambda rid, d: {"state": "running", "supervisor_pid": worker.pid, "supervisor_pgid": worker.pid, "settings": {"profile": "full"}, "origin": origin})
        out = self.job("rollback", "--only", "nightly", expect=3)
        self.assertEqual(out["results"][0]["status"], "waiting")
        self.assertIsNone(self.plist(OLD_PREFIX + "nightly"), "the old side does not start while the new one still works")
        registry.update_meta(run_dir, lambda m: m.update({"state": "completed"}))
        self.job("rollback", "--only", "nightly")
        self.assertTrue(self.plist(OLD_PREFIX + "nightly"))

    def test_freeze_sees_a_queued_run_behind_a_hundred_newer_ones(self):
        self.job("inventory")
        w = self.world()
        old = {"workflowName": "nightly.yml", "event": "schedule", "displayTitle": "nightly · schedule", "createdAt": "2026-11-01T03:00:00Z", "url": "https://github.com/x"}
        w["runs"] = [{**old, "databaseId": 1, "status": "queued", "conclusion": None}] + [{**old, "databaseId": 2 + i, "status": "completed", "conclusion": "cancelled"} for i in range(100)]
        self.world(w)
        out = self.job("freeze", "--only", "nightly", expect=3)
        self.assertEqual(out["results"][0]["status"], "waiting")

    def test_the_inventory_is_read_again_for_the_next_batch(self):
        self.job("inventory")
        for step in ("freeze", "state", "publish", "resume"):
            self.job(step, "--only", "nightly")
        # While the canary is watched, `later` fires on the old side: its worker completes and its Finish turns it off.
        at = int(datetime(2026, 12, 1, 6, 0, tzinfo=ZoneInfo(TZ)).timestamp())
        self.old_registry("later", {"last_slot": at, "slots": {str(at): {"job": "3-1", "retried": None}}, "jobs": {"3-1": {"trigger": "dispatcher", "profile": "full", "slot": at, "run_id": "r-later-1"}}, "definition": "d0d0d0d0"}, runs=[("r-later-1", "completed")])
        w = self.world()
        w["repos"][REPO]["workflows"]["later.yml"] = "disabled_manually"
        self.world(w)
        now = at + 3600
        inv = {r.get("name"): r for r in self.job("inventory", now=now)["schedules"]}
        self.assertEqual((inv["nightly"]["class"], inv["later"]["class"]), ("recurring", "finished"), "the moved canary keeps its row; the rest is read as it is now")
        for step in ("freeze", "state", "publish", "resume"):
            self.job(step, now=now)
        self.assertNotIn("later.yml", self.files())
        self.assertTrue(self.committed("nightly").startswith("# Written by the delegate skill's"))

    def test_a_freeze_that_stopped_after_unloading_keeps_its_time(self):
        label = OLD_PREFIX + "nightly"
        agents = self.home / "Library" / "LaunchAgents"
        (self.lc_state / "loaded.json").write_text(json.dumps({label: str(agents / f"{label}.plist")}))
        self.job("inventory")
        # launchd lets the old dispatcher go, then its plist cannot be deleted: the step stops with the dispatcher already unloaded.
        agents.chmod(0o555)
        try:
            self.job("freeze", "--only", "nightly", expect=3)
        finally:
            agents.chmod(0o755)
        later = T0 + 86400
        for step in ("freeze", "state", "publish", "resume"):
            self.job(step, "--only", "nightly", now=later, expect=3 if step == "resume" else 0)
        dispatched = [[a for a in c["argv"] if a.startswith("slot=")] for c in self.gh_calls() if c["argv"][:3] == ["workflow", "run", "nightly.yml"]]
        self.assertEqual(dispatched, [[f"slot={ts(18, 3)}"]], "the slot missed since the first freeze unloaded it")

    def test_a_rollback_that_stopped_after_loading_the_old_dispatcher_finishes(self):
        self.job("inventory")
        self.through("freeze", "state", "publish")
        path = self.runs_dir() / ".migration" / "journal.json"
        before = path.read_text()
        self.job("rollback", "--only", "nightly")
        path.write_text(before)  # as if it stopped right after launchd loaded the old dispatcher
        self.assertEqual(self.job("rollback", "--only", "nightly")["results"][0]["status"], "done")
        self.assertTrue(self.plist(OLD_PREFIX + "nightly"))

    def test_a_rollback_without_the_old_dispatchers_backup_is_not_done(self):
        self.job("inventory")
        self.through("freeze", "state", "publish")
        (self.runs_dir() / ".migration" / "backup" / f"{OLD_PREFIX}nightly.plist").unlink()
        out = self.job("rollback", "--only", "nightly", expect=1)
        self.assertIn("backup", out["results"][0]["error"])

    def test_verify_reads_the_whole_workflow_not_only_its_spec(self):
        self.job("inventory")
        self.through("freeze", "state", "publish", "resume")
        broken = "\n".join(l for l in self.committed("nightly").splitlines() if not l.lstrip().startswith("run: ")) + "\n"
        self.plant("nightly.yml", broken)
        check = self.job("verify", expect=None)
        self.assertTrue(any(p.startswith("nightly:") for p in check["problems"]), check["problems"])

    def test_verify_fails_while_a_held_workflow_still_runs_the_old_skill(self):
        self.job("inventory")
        self.through("freeze", "state", "publish", "resume")
        check = self.job("verify", expect=1)
        self.assertEqual([p.split(":")[0] for p in check["problems"]], ["stuck.yml"], check["problems"])
        self.assertEqual([r["name"] for r in check["schedules"] if r["disposition"] == "held"], ["stuck"], "it is still listed as the exception it is")
        w = self.world()
        w["repos"][REPO]["workflows"]["stuck.yml"] = "disabled_manually"
        self.world(w)
        self.assertEqual(self.job("verify")["problems"], [], "switched off, it waits for a decision without firing the old command")


class MoveRepairReview(MoveWorld):
    run_one_off = MoveReview.run_one_off

    """What the GPT review of the move's repairs found, each reproduced at the job's command line: a resume that says done before its slot runs, a rollback that undoes or overwrites what it should leave alone, a one-off left on, a stale inventory, a recovery the error message cannot lead to, and another repository's dispatcher taken."""

    def test_the_first_resume_that_dispatches_a_slot_is_not_done(self):
        self.job("inventory")
        self.through("freeze", "state", "publish")
        later, slot = T0 + 86400, ts(18, 3)
        # The missed 03:00 is dispatched; until a run claims it, "exit 0 means done" would stop the operator before it ran.
        out = self.job("resume", now=later, expect=3)
        self.assertEqual({r["name"]: r["status"] for r in out["results"]}["nightly"], "waiting")
        self.assertEqual(self.repo()["workflows"]["nightly.yml"], "active")
        self.assertTrue(self.plist(NEW_PREFIX + "nightly"))
        registry.claim_slot(str(self.runs_dir()), "nightly", slot, "44000000001-1", {"trigger": "recovery", "profile": "full"}, exact=True)
        self.job("resume", now=later)
        self.assertEqual(len([c for c in self.gh_calls() if c["argv"][:3] == ["workflow", "run", "nightly.yml"]]), 1, "claimed after the first dispatch, it is not dispatched again")

    def test_a_rollback_refused_for_a_changed_workflow_keeps_its_dispatcher(self):
        self.job("inventory")
        self.through("freeze", "state", "publish", "resume")
        edited = self.committed("nightly").replace("work for nightly", "other work for nightly")
        self.plant("nightly.yml", edited)
        out = self.job("rollback", "--only", "nightly", expect=1)
        self.assertIn("changed", out["results"][0]["error"])
        self.assertEqual(self.repo()["workflows"]["nightly.yml"], "active")
        self.assertTrue(self.plist(NEW_PREFIX + "nightly"), "a rollback that touches nothing leaves the active workflow its dispatcher")

    def test_a_rollback_does_not_overwrite_a_change_made_while_it_runs(self):
        self.job("inventory")
        self.through("freeze", "state", "publish", "resume")
        # Someone's `schedule` lands right after the rollback checked the file was the move's.
        edited = self.committed("nightly").replace("work for nightly", "other work for nightly")
        self.world(commit_on_disable={"path": ".github/workflows/nightly.yml", "text": edited})
        out = self.job("rollback", "--only", "nightly", expect=1)
        self.assertEqual(out["results"][0]["status"], "failed")
        self.assertEqual(self.committed("nightly"), edited, "the change made meanwhile is kept")

    def test_a_one_off_completed_on_the_new_side_is_finished_after_a_rollback(self):
        self.job("inventory")
        self.through("freeze", "state", "publish", "resume")
        when = int(datetime(2026, 12, 1, 6, 1, tzinfo=ZoneInfo(TZ)).timestamp())
        self.run_one_off("later", when, "8")
        self.assertEqual(self.repo()["workflows"]["later.yml"], "disabled_manually")
        self.job("rollback", "--only", "later", now=when + 3600)
        # Moved again: its time passed, it is off, and a worker completed its slot — on this skill's side, which the old record now holds.
        inv = {r.get("name"): r for r in self.job("inventory", now=when + 7200)["schedules"]}
        self.assertEqual(inv["later"]["class"], "finished", inv["later"])
