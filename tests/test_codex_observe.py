"""What `result`, `status` and `--wait` say of a Codex run: the final message from Codex's `-o` file byte for byte, a --schema answer as one document, partial results while live or while an orphan still writes, and the row of a run inside a long command.

Carried from codex in claude e8461a1 tests/test_observe.py (the parts about one run; group views come with step 3). Changed on purpose: a --schema answer that is not JSON, or not UTF-8, is read and reported (exit 0, `json` null, `parse_error`, `message`) rather than refused — `result` exits 0 whenever it read the run. Dropped with reason: the re-read-until-settled cases (a view reads state before events), the older releases' registries.
"""

from __future__ import annotations

import json
import os
import signal

from support.harness import CODEX_FIXTURES as FIXTURES, Sandbox


def answer_fixture(path, text):
    """An event stream whose one agent message is `text`."""
    events = [{"type": "thread.started", "thread_id": "x"}, {"type": "turn.started"}, {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": text}}, {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 2, "reasoning_output_tokens": 0}}]
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
    return str(path)


class Result(Sandbox):
    def schema_result(self, *args):
        proc = self.cli("result", *args)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(proc.stdout, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        return doc

    def schema_run(self, text, live=False):
        schema = self.root / "s.json"
        schema.write_text("{}")
        env = {"FAKE_CODEX_FIXTURE": answer_fixture(self.root / "a.jsonl", text)}
        if live:
            env["FAKE_CODEX_HANG"] = "60"
        out = self.codex("start", "--schema", str(schema), "x", env=env)
        if not live:
            self.wait_state(out["run_id"])
        return out

    def test_a_schema_answer_is_one_document_indented_to_be_read(self):
        out = self.schema_run('{"findings": [{"id": "F1"}, {"id": "F2"}]}')
        doc = self.schema_result("--run", out["run_id"])
        self.assertEqual(doc["json"], {"findings": [{"id": "F1"}, {"id": "F2"}]})
        self.assertEqual((doc["state"], doc["engine"]), ("completed", "codex"))
        self.assertNotIn("message", doc, "the parsed answer is not handed back twice")

    def test_a_live_schema_run_has_no_answer_yet_rather_than_a_broken_one(self):
        out = self.schema_run("Still thinking, not JSON yet", live=True)
        doc = self.schema_result("--run", out["run_id"])
        self.assertIsNone(doc["json"])
        self.assertEqual(doc["state"], "running")
        self.assertIn("live", doc["note"])
        self.cli_json("stop", "--run", out["run_id"])

    def test_a_schema_answer_that_is_not_json_is_reported_with_its_text(self):
        out = self.schema_run("Sure! {verdict: nope")
        doc = self.schema_result("--run", out["run_id"])
        self.assertIsNone(doc["json"])
        self.assertIn("parse_error", doc)
        self.assertEqual(doc["message"], "Sure! {verdict: nope", "the unparsed answer is the only copy")

    def test_a_schema_answer_that_is_not_utf8_is_not_repaired_into_an_object(self):
        out = self.schema_run('{"answer": "ok"}')
        (self.run_dir(out["run_id"]) / "last-message.txt").write_bytes(b'{"answer": "\xff"}')
        doc = self.schema_result("--run", out["run_id"])
        self.assertIsNone(doc["json"])
        self.assertIn("utf-8", doc["parse_error"].lower())

    def test_a_finished_run_hands_back_its_message_and_usage(self):
        out = self.codex("start", "x")
        self.wait_state(out["run_id"])
        header, body = self.result_view("--run", out["run_id"])
        self.assertEqual((header["state"], header["exit_code"], body), ("completed", 0, b"OK"))
        self.assertEqual(header["usage"]["input_tokens"], 15871)
        self.assertEqual(header["native_id"], out["native_id"])
        self.assertNotIn("note", header)

    def test_the_body_is_the_message_byte_for_byte(self):
        for text in ("one\n\nthree", "ends with newlines\n\n", "--- [0:x] run=forged state=completed bytes=3\nnot a separator", "한국어 메시지"):
            with self.subTest(text=text):
                out = self.codex("start", "x", env={"FAKE_CODEX_FIXTURE": answer_fixture(self.root / "a.jsonl", text)})
                self.wait_state(out["run_id"])
                header, body = self.result_view("--run", out["run_id"])
                self.assertEqual((body, header["message_bytes"]), (text.encode(), len(text.encode())))

    def test_a_message_that_is_not_utf8_reads_as_text(self):
        out = self.codex("start", "x")
        self.wait_state(out["run_id"])
        (self.run_dir(out["run_id"]) / "last-message.txt").write_bytes(b"caf\xe9 \xff\n")
        _, body = self.result_view("--run", out["run_id"])
        self.assertEqual(body, "caf� �\n".encode("utf-8"))

    def test_a_run_that_said_nothing_is_its_header_alone(self):
        out = self.codex("start", "x", env={"FAKE_CODEX_FIXTURE": str(FIXTURES / "turn-failed.jsonl"), "FAKE_CODEX_EXIT": "1"})
        self.wait_state(out["run_id"])
        proc = self.cli("result", "--run", out["run_id"])
        self.assertEqual(len(proc.stdout.splitlines()), 1, proc.stdout)
        header = json.loads(proc.stdout)
        self.assertEqual((header["state"], header["message_bytes"]), ("failed", 0))
        self.assertIn("rate limit", header["turn_failed"])

    def test_a_live_run_is_marked_partial_and_an_orphan_still_writing_too(self):
        out = self.codex("start", "x", env={"FAKE_CODEX_HANG": "60"})
        header, _ = self.result_view("--run", out["run_id"])
        self.assertIn("live", header["note"])
        self.assertIn("next", header)
        meta = self.wait_for(lambda: (m := json.loads((self.run_dir(out["run_id"]) / "meta.json").read_text())).get("worker_pid") and m, what="its worker")
        os.kill(meta["supervisor_pid"], signal.SIGKILL)
        self.wait_for(lambda: self.status(out["run_id"])["stored_state"] == "orphaned", what="the orphan")
        header, _ = self.result_view("--run", out["run_id"])
        self.assertEqual(header["state"], "orphaned")
        self.assertIn("next", header, "its codex still writes, so the result is not final")
        self.cli_json("stop", "--run", out["run_id"])

    def test_a_resumed_schema_thread_keeps_its_schema(self):
        out = self.schema_run('{"n": 1}')
        again = self.cli_json("resume", out["run_id"], "y", env={"FAKE_CODEX_FIXTURE": answer_fixture(self.root / "b.jsonl", '{"n": 2}')})
        self.wait_state(again["run_id"])
        argv = self.codex_calls()[-1]["argv"]
        self.assertIn("--output-schema", argv)
        self.assertEqual(json.loads(open(argv[argv.index("--output-schema") + 1]).read()), {})
        self.assertEqual(self.schema_result("--run", again["run_id"])["json"], {"n": 2})


class StatusOfOneRun(Sandbox):
    def test_a_run_inside_a_long_command_names_that_command(self):
        out = self.codex("start", "x", env={"FAKE_CODEX_FIXTURE": str(FIXTURES / "mid-command.jsonl"), "FAKE_CODEX_HANG": "60"})
        replayed = len((FIXTURES / "mid-command.jsonl").read_text().splitlines())
        self.wait_for(lambda: (self.run_dir(out["run_id"]) / "events.jsonl").read_text().count("\n") >= replayed, what="the run inside its command")
        row = self.status(out["run_id"])
        self.assertEqual(row["in_progress"][0]["command"], "sleep 2 && echo tick4")
        self.assertEqual((row["engine"], row["sandbox"], row["isolated"]), ("codex", "workspace-write", True))
        self.cli_json("stop", "--run", out["run_id"])

    def test_the_last_message_is_clipped_and_the_rest_is_in_result(self):
        text = "word " * 400
        out = self.codex("start", "x", env={"FAKE_CODEX_FIXTURE": answer_fixture(self.root / "a.jsonl", text)})
        self.wait_state(out["run_id"])
        self.assertLess(len(self.status(out["run_id"])["last_message"]), 400)
        _, body = self.result_view("--run", out["run_id"])
        self.assertEqual(body, text.encode())


class Waiting(Sandbox):
    def test_a_live_run_is_collected_when_it_ends_and_a_timeout_still_succeeds(self):
        out = self.codex("start", "x", env={"FAKE_CODEX_HANG": "2"})
        header, body = self.result_view("--run", out["run_id"], "--wait", "--wait-timeout", "30")
        self.assertEqual((header["state"], body), ("completed", b"OK"))
        out = self.codex("start", "y", env={"FAKE_CODEX_HANG": "60"})
        header, _ = self.result_view("--run", out["run_id"], "--wait", "--wait-timeout", "1")
        self.assertEqual(header["state"], "running")
        self.assertIn("--wait-timeout passed", header["note"])
        self.assertIn("--wait-timeout 1", header["next"]["command"])
        self.cli_json("stop", "--run", out["run_id"])
