"""Retry core and P4 service integration on isolated SQLite; AI and IDE mocked."""

import contextlib
import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from coach import retries
from coach.problems import BUILTINS, validate_problem


LEGACY_SCHEMA = (
    "CREATE TABLE sessions(id TEXT PRIMARY KEY,created INTEGER,state TEXT)",
    "CREATE TABLE events(sid TEXT,id TEXT,ms INTEGER,kind TEXT,pid TEXT,payload TEXT,PRIMARY KEY(sid,id))",
    "CREATE TABLE problems(id TEXT PRIMARY KEY,payload TEXT)",
    "CREATE TABLE jobs(id TEXT PRIMARY KEY,sid TEXT,state TEXT)",
    "CREATE TABLE media(sid TEXT,kind TEXT,segment TEXT,seq INTEGER,start_ms INTEGER,mime TEXT,size INTEGER,PRIMARY KEY(sid,kind,segment,seq))",
)


def at(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


NOW = at("2026-10-01T12:00:00+00:00")
DUE = at("2026-10-09T12:00:00+00:00")


def fixture(sid="original", pid="pair", status="completed"):
    problem = copy.deepcopy(next(p for p in BUILTINS if p["id"] == "pair"))
    problem["id"] = pid
    problem["tags"] = ["PAST_TAG_SENTINEL"]
    return {"id": sid, "title": problem["title"], "started": NOW - 86400000 * 30,
            "ended": NOW - 86400000 * 30 + 2400000 if status == "completed" else None,
            "status": status, "mode": "training", "kind": "single", "duration": 40,
            "resources": "none", "deadline": NOW - 86400000 * 30 + 2400000,
            "deadline_codes": None, "problems": [problem], "codes": {pid: "PAST_CODE_SENTINEL"},
            "runs": [], "messages": [], "report": {"summary": "PAST_REPORT_SENTINEL"},
            "transcripts": [], "thread": "PAST_THREAD_SENTINEL", "model": "fixture", "effort": "max"}


def run(rid="result", status="failed", ms=1000, pid="pair", submit=True):
    return {"id": rid, "pid": pid, "ms": ms, "submit": submit, "overtime": False,
            "result": {"status": status, "passed": int(status == "passed"), "total": 1,
                       "duration_ms": 999999}}


def submit(rid="result", ms=1000, pid="pair", rowid=1):
    return {"ms": ms, "rowid": rowid, "kind": "submit", "pid": pid, "payload": {"run_id": rid}}


class ClassificationTest(unittest.TestCase):
    def classify(self, runs=(), events=(), jobs=(), **changes):
        session = fixture()
        session.update(changes)
        session["runs"] = list(runs)
        return retries.classify_attempt(session, list(events), list(jobs), "pair")

    def test_final_submission_order_ignores_reverse_job_completion(self):
        result = self.classify([run("last", "failed", 2000), run("first", "passed", 1000)],
                               [submit("last", 2000, rowid=2), submit("first", 1000)])
        self.assertEqual((result["outcome"], result["run_id"], result["eligible"]), ("failed", "last", True))
        self.assertEqual(result["result_basis"], "submit_events")

    def test_same_ms_submit_uses_rowid_and_not_code_client_sequence(self):
        result = self.classify([run("last", "failed"), run("first", "passed")],
                               [submit("last", rowid=20), submit("first", rowid=10)])
        self.assertEqual((result["outcome"], result["run_id"]), ("failed", "last"))
        events = [submit("first"), submit("last")]
        for event in events:
            event.pop("rowid")
        self.assertEqual(self.classify([run("last"), run("first", "passed")], events)["run_id"], "last")

    def test_deadline_inclusive_and_overtime_uses_event_ms(self):
        deadline = 40 * 60000
        result = self.classify([run("in", "failed", 0), run("over", "passed", 0)],
                               [submit("in", deadline), submit("over", deadline + 1, rowid=2)])
        self.assertEqual((result["outcome"], result["overtime_outcome"], result["eligible"]), ("failed", "passed", True))

    def test_examples_and_other_problem_submissions_do_not_count(self):
        result = self.classify([run("example", "passed", submit=False), run("other", "passed", pid="other")],
                               [{"kind": "run", "pid": "pair", "ms": 1000, "payload": {}}, submit("other", pid="other")])
        self.assertEqual((result["outcome"], result["run_id"], result["eligible"]), ("not_submitted", None, True))

    def test_overtime_only_success_remains_not_submitted_in_time(self):
        result = self.classify([run("late", "passed", 2400001)], [submit("late", 2400001)])
        self.assertEqual((result["outcome"], result["overtime_outcome"], result["eligible"]), ("not_submitted", "passed", True))

    def test_grader_error_and_unknown_status_are_never_eligible(self):
        for status, outcome in [("error", "grader_error"), ("unknown", "ungraded")]:
            with self.subTest(status=status):
                result = self.classify([run(status=status)], [submit()])
                self.assertEqual((result["outcome"], result["eligible"], result["code"]), (outcome, False, "GRADE_UNAVAILABLE"))

    def test_missing_last_result_does_not_reuse_earlier_success(self):
        result = self.classify([run("first", "passed")], [submit("first"), submit("missing", 2000, rowid=2)])
        self.assertEqual((result["outcome"], result["run_id"], result["code"]), ("ungraded", "missing", "GRADE_UNAVAILABLE"))

    def test_pending_java_blocks_registration_while_other_jobs_do_not(self):
        for status in ("queued", "running"):
            with self.subTest(status=status):
                result = self.classify(jobs=[{"kind": "java", "status": status}])
                self.assertEqual((result["eligible"], result["code"], result["grading_pending"]), (False, "GRADING_PENDING", True))
        result = self.classify(jobs=[{"kind": "report", "status": "running"}, {"kind": "transcribe", "status": "queued"}])
        self.assertTrue(result["eligible"])

    def test_restart_failed_job_with_submit_is_ungraded(self):
        result = self.classify(events=[submit()], jobs=[{"kind": "java", "status": "failed"}])
        self.assertEqual((result["outcome"], result["grading_pending"], result["code"]), ("ungraded", False, "GRADE_UNAVAILABLE"))

    def test_last_pass_and_active_source_are_not_candidates(self):
        self.assertEqual(self.classify([run(status="passed")], [submit()])["code"], "NOT_ELIGIBLE")
        self.assertEqual(self.classify(status="active")["code"], "SOURCE_ACTIVE")

    def test_legacy_fallback_ms_then_saved_array_index(self):
        result = self.classify([run("first", "passed"), run("last", "failed")])
        self.assertEqual((result["outcome"], result["run_id"], result["result_basis"]), ("failed", "last", "legacy_runs"))
        self.assertEqual(self.classify([run("late", "failed", 2000), run("early", "passed", 1000)])["run_id"], "late")

    def test_automatic_deadline_submit_counts(self):
        event = submit(ms=2400000)
        event["payload"]["automatic"] = True
        self.assertEqual(self.classify([run(ms=2400000)], [event])["outcome"], "failed")

    def test_submission_events_are_authoritative_when_run_has_no_event(self):
        result = self.classify([run("accepted", "passed"), run("orphan", "failed", 2000)], [submit("accepted")])
        self.assertEqual(result["outcome"], "passed")

    def test_no_elapsed_time_or_raw_result_or_code_is_exported(self):
        result = self.classify([run()], [submit()])
        self.assertNotIn("minutes", result)
        self.assertNotIn("duration_ms", json.dumps(result))
        self.assertNotIn("PAST_", json.dumps(result))

    def test_corrupt_submission_and_job_records_fail_closed(self):
        cases = [dict(events=[dict(submit(), payload=[])]),
                 dict(events=[dict(submit(), ms=True)]),
                 dict(runs=[dict(run(), id=[])]),
                 dict(jobs=[{"kind": "java", "status": []}]),
                 dict(problems=None), dict(status=[])]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(retries.RetryError) as caught:
                self.classify(**changes)
            self.assertEqual(caught.exception.code, "SOURCE_UNAVAILABLE")


class SnapshotValidationTest(unittest.TestCase):
    def test_missing_public_is_rejected_in_every_saved_test_without_normalizing(self):
        for index in range(len(fixture()["problems"][0]["tests"])):
            with self.subTest(index=index):
                source = fixture()
                source["problems"][0]["tests"][index].pop("public")
                before = copy.deepcopy(source)
                with self.assertRaises(retries.RetryError) as caught:
                    retries._problem(source, "pair")
                self.assertEqual((caught.exception.code, caught.exception.details["cause"]),
                                 ("SOURCE_UNAVAILABLE", "PROBLEM_CORRUPT"))
                self.assertEqual(source, before)

    def test_valid_builtin_and_imported_snapshots_are_returned_unchanged(self):
        imported = validate_problem(dict(title="snapshot", description="Return the input.",
                                        starter="class Solution { public int answer(int n) { return n; } }",
                                        returns="int", method="answer", params=[dict(type="int", name="n")],
                                        tests=[dict(args=[1], expected=1)], level=1, tags=["fixture"]))
        for problem in [*BUILTINS, imported]:
            with self.subTest(pid=problem["id"]):
                source = fixture()
                source["problems"] = [copy.deepcopy(problem)]
                before = copy.deepcopy(source)
                self.assertEqual(retries._problem(source, problem["id"]), problem)
                self.assertEqual(source, before)

    def test_optional_runner_defaults_do_not_require_import_normalization(self):
        source = fixture()
        problem = source["problems"][0]
        problem.pop("url")
        for test in problem["tests"]:
            test.pop("time_limit_ms", None)
            test.pop("performance", None)
        before = copy.deepcopy(source)
        self.assertEqual(retries._problem(source, "pair"), problem)
        self.assertEqual(source, before)


class RetryFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="retry-p1-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "lab.sqlite3"
        self.con = self.connect()
        self.addCleanup(self.con.close)
        for statement in LEGACY_SCHEMA:
            self.con.execute(statement)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("BEGIN IMMEDIATE")
        retries.init_schema(self.con)
        self.con.commit()
        self.source = fixture()
        self.save(self.source)
        self.media = Path(self.temp.name) / "fake-media.part"
        self.media.write_bytes(b"fixture-only-media")
        self.con.execute("INSERT INTO media VALUES (?,?,?,?,?,?,?)", (self.source["id"], "screen", "a" * 32, 0, 0, "video/webm", 18))
        self.add_event(self.source["id"], "note", {"text": "fixture note"})
        self.con.commit()

    def connect(self):
        return sqlite3.connect(self.path, timeout=20)

    def save(self, session):
        self.con.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?)", (session["id"], session["started"], json.dumps(session, ensure_ascii=False)))
        self.con.commit()

    def add_event(self, sid, kind, payload, ms=0, pid="pair"):
        self.con.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (sid, uuid.uuid4().hex, ms, kind, pid, json.dumps(payload)))

    def job(self, sid, kind="java", status="running"):
        self.con.execute("INSERT INTO jobs VALUES (?,?,?)", (uuid.uuid4().hex, sid, json.dumps({"kind": kind, "status": status})))
        self.con.commit()

    @contextlib.contextmanager
    def write(self, con=None):
        con = con or self.con
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise

    def plan(self, source=None, reason="  PAST_REASON_SENTINEL  ", timezone="Etc/UTC", now=NOW):
        with self.write():
            return retries.create_plan(self.con, source or self.source, "pair", reason, timezone, now)

    def fresh(self, plan, index=0, now=DUE):
        item = plan["items"][index]
        session = fixture(uuid.uuid4().hex, status="active")
        session.update(started=now, deadline=now + 2400000, report=None, thread=None,
                       codes={"pair": session["problems"][0]["starter"]},
                       retry={"plan_id": plan["id"], "item_id": item["id"], "source_sid": plan["source_sid"], "pid": "pair"})
        return session

    def link(self, plan, index=0, now=DUE):
        with self.write():
            return retries.link_started_session(self.con, plan["items"][index]["id"], self.fresh(plan, index, now))

    def end(self, session, status="passed"):
        session = copy.deepcopy(session)
        session.update(status="completed", ended=session["started"] + 120000, runs=[run(status=status)])
        self.save(session)
        self.add_event(session["id"], "submit", {"run_id": "result"}, 1000)
        self.con.commit()

    def unchanged_original(self):
        return {"sessions": self.con.execute("SELECT * FROM sessions WHERE id=?", (self.source["id"],)).fetchall(),
                "events": self.con.execute("SELECT rowid,* FROM events WHERE sid=?", (self.source["id"],)).fetchall(),
                "media": self.con.execute("SELECT * FROM media WHERE sid=?", (self.source["id"],)).fetchall(),
                "jobs": self.con.execute("SELECT * FROM jobs WHERE sid=?", (self.source["id"],)).fetchall(),
                "media_sha256": hashlib.sha256(self.media.read_bytes()).hexdigest()}

    def assert_code(self, code, call):
        with self.assertRaises(retries.RetryError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        return caught.exception


class DatabaseTest(RetryFixture, unittest.TestCase):
    def test_schema_additive_idempotent_and_does_not_commit_outer_transaction(self):
        before = self.unchanged_original()
        with self.write():
            retries.init_schema(self.con)
            self.assertTrue(self.con.in_transaction)
        self.assertEqual(before, self.unchanged_original())
        tables = {row[0] for row in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {"sessions", "events", "problems", "jobs", "media", "retry_plans", "retry_items"})
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_plans").fetchone()[0], 0)
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.execute("BEGIN IMMEDIATE")
        retries.init_schema(con)
        con.rollback()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0], 0)

    def test_registration_anchor_is_now_and_creates_exact_three_dates(self):
        before = self.unchanged_original()
        result = self.plan()
        self.assertTrue(result["created"])
        self.assertEqual(result["plan"]["anchor_date"], "2026-10-01")
        self.assertEqual([item["due_date"] for item in result["plan"]["items"]], ["2026-10-02", "2026-10-04", "2026-10-08"])
        self.assertEqual([item["offset_days"] for item in result["plan"]["items"]], [1, 3, 7])
        self.assertTrue(all(item["state"] == "scheduled" for item in result["plan"]["items"]))
        self.assertEqual(result["plan"]["reason"], "PAST_REASON_SENTINEL")
        self.assertEqual(before, self.unchanged_original())

    def test_natural_key_idempotency_and_payload_mismatch_preserve_original(self):
        first = self.plan()
        same = self.plan(source=self.source["id"])
        other = self.plan(reason="changed", timezone="Asia/Seoul", now=DUE)
        self.assertFalse(same["created"])
        self.assertNotIn("payload_mismatch", same)
        self.assertTrue(other["payload_mismatch"])
        self.assertEqual(first["plan"]["id"], other["plan"]["id"])
        self.assertEqual([item["id"] for item in first["plan"]["items"]], [item["id"] for item in other["plan"]["items"]])
        self.assertEqual((other["plan"]["reason"], other["plan"]["timezone"]), ("PAST_REASON_SENTINEL", "Etc/UTC"))

    def test_other_source_for_same_pid_reports_existing_open_plan(self):
        first = self.plan()["plan"]
        other = fixture("other")
        self.save(other)
        exc = self.assert_code("PLAN_EXISTS", lambda: self.plan(other))
        self.assertEqual(exc.as_dict()["plan_id"], first["id"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_items").fetchone()[0], 3)

    def test_invalid_input_is_rejected_before_any_rows_are_written(self):
        for reason in (None, "", " \n ", "x" * 201, "\x00x", "x\t", "x\r", "x\x7f", "\ud800"):
            with self.subTest(reason=repr(reason)):
                self.assert_code("INVALID_INPUT", lambda: self.plan(reason=reason))
        for timezone in ("", "invalid/no_such_zone", "../Etc/UTC", None, []):
            with self.subTest(timezone=timezone):
                self.assert_code("INVALID_INPUT", lambda: self.plan(timezone=timezone))
        for now in (True, -1, 1.5, 10**25):
            with self.subTest(now=now):
                self.assert_code("INVALID_INPUT", lambda: self.plan(now=now))
        with self.write():
            self.assert_code("INVALID_INPUT", lambda: retries.create_plan(self.con, self.source, "absent", "reason", "Etc/UTC", NOW))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_plans").fetchone()[0], 0)

    def test_unicode_reason_uses_codepoints_and_allows_newline(self):
        reason = "😀" * 199 + "\n"
        saved = self.plan(reason=reason)["plan"]["reason"]
        self.assertEqual(saved, "😀" * 199)
        self.assertEqual(self.plan(reason="😀" * 200)["plan"]["reason"], saved)

    def test_write_requires_caller_transaction_and_never_opens_connection(self):
        with self.assertRaisesRegex(RuntimeError, "BEGIN IMMEDIATE"):
            retries.create_plan(self.con, self.source, "pair", "reason", "Etc/UTC", NOW)
        with self.write(), patch("sqlite3.connect", side_effect=AssertionError("hidden connection")):
            retries.create_plan(self.con, self.source, "pair", "reason", "Etc/UTC", NOW)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_items").fetchone()[0], 3)

    def test_twenty_concurrent_registrations_produce_one_plan_three_items(self):
        barrier = threading.Barrier(20)
        before = self.unchanged_original()
        def create(_):
            with contextlib.closing(self.connect()) as con, con:
                barrier.wait(timeout=10)
                con.execute("BEGIN IMMEDIATE")
                return retries.create_plan(con, self.source, "pair", "reason", "Etc/UTC", NOW)
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(create, range(20)))
        self.assertEqual(sum(result["created"] for result in results), 1)
        self.assertEqual(len({result["plan"]["id"] for result in results}), 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_plans").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_items").fetchone()[0], 3)
        self.assertEqual(before, self.unchanged_original())

    def test_create_partial_failure_rolls_back_even_when_caught_by_caller(self):
        self.con.execute("CREATE TRIGGER fail_item BEFORE INSERT ON retry_items WHEN NEW.offset_days=3 BEGIN SELECT RAISE(ABORT,'fixture injected insert failure'); END")
        with self.write():
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected insert failure"):
                retries.create_plan(self.con, self.source, "pair", "reason", "Etc/UTC", NOW)
            self.assertTrue(self.con.in_transaction)
            self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_plans").fetchone()[0], 0)
            self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_items").fetchone()[0], 0)

    def test_outer_rollback_undoes_whole_successful_registration(self):
        self.con.execute("BEGIN IMMEDIATE")
        retries.create_plan(self.con, self.source, "pair", "reason", "Etc/UTC", NOW)
        with contextlib.closing(self.connect()) as observer:
            self.assertEqual(observer.execute("SELECT COUNT(*) FROM retry_plans").fetchone()[0], 0)
        self.con.rollback()
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_items").fetchone()[0], 0)

    def test_registration_rechecks_stored_source_instead_of_stale_caller(self):
        active = copy.deepcopy(self.source)
        active["status"] = "active"
        self.save(active)
        self.assert_code("SOURCE_ACTIVE", self.plan)
        self.save(self.source)
        self.job(self.source["id"])
        self.assert_code("GRADING_PENDING", self.plan)

    def test_registration_error_pass_and_missing_result_codes(self):
        for status, expected in [("error", "GRADE_UNAVAILABLE"), ("passed", "NOT_ELIGIBLE"), ("unknown", "GRADE_UNAVAILABLE")]:
            with self.subTest(status=status):
                source = copy.deepcopy(self.source)
                source["runs"] = [run(status=status)]
                self.save(source)
                self.assert_code(expected, self.plan)

    def test_malformed_stored_source_never_creates_a_plan(self):
        for changes in ({"started": None}, {"started": True}, {"ended": None},
                        {"ended": self.source["started"] - 1}, {"messages": {}}, {"status": []}):
            with self.subTest(changes=changes):
                source = copy.deepcopy(self.source)
                source.update(changes)
                self.con.execute("UPDATE sessions SET state=? WHERE id=?", (json.dumps(source), source["id"]))
                self.con.commit()
                self.assert_code("SOURCE_UNAVAILABLE", self.plan)
        self.con.execute("UPDATE sessions SET state='{' WHERE id=?", (self.source["id"],))
        self.con.commit()
        self.assert_code("SOURCE_UNAVAILABLE", self.plan)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_plans").fetchone()[0], 0)

    def test_link_atomic_fresh_session_and_original_snapshot_preserved(self):
        plan = self.plan()["plan"]
        before = self.unchanged_original()
        self.con.execute("INSERT INTO problems VALUES (?,?)", ("pair", json.dumps({"starter": "CATALOG_CHANGED"})))
        self.con.commit()
        result = self.link(plan)
        session = result["session"]
        self.assertTrue(result["created"])
        self.assertEqual(session["problems"], self.source["problems"])
        self.assertEqual(session["codes"], {"pair": self.source["problems"][0]["starter"]})
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM events WHERE sid=? AND kind='start'", (session["id"],)).fetchone()[0], 1)
        self.assertEqual(before, self.unchanged_original())

    def test_future_out_of_order_and_other_active_rejected(self):
        plan = self.plan()["plan"]
        self.assert_code("NOT_DUE", lambda: self.link(plan, now=NOW))
        self.assert_code("PREVIOUS_PENDING", lambda: self.link(plan, index=2))
        self.save(fixture("ordinary-active", status="active"))
        self.assert_code("ACTIVE_SESSION_EXISTS", lambda: self.link(plan))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM events WHERE kind='start'").fetchone()[0], 0)

    def test_link_rejects_prior_code_thread_and_wrong_problem_snapshot(self):
        plan = self.plan()["plan"]
        for changes in ({"thread": "PAST_THREAD"}, {"codes": {"pair": "PAST_CODE"}}, {"problems": []},
                        {"retry": {"item_id": "wrong"}}, {"runs": None}, {"messages": None},
                        {"report_history": [{"summary": "PAST_REPORT"}]}, {"past_reports": ["PAST_REPORT"]}):
            candidate = self.fresh(plan)
            candidate.update(changes)
            with self.write():
                self.assert_code("SOURCE_UNAVAILABLE", lambda: retries.link_started_session(self.con, plan["items"][0]["id"], candidate))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)

    def test_response_loss_returns_same_active_then_completed_sid(self):
        plan = self.plan()["plan"]
        first = self.link(plan)["session"]
        self.save(fixture("another-active", status="active"))
        for completed in (False, True):
            if completed:
                self.end(first)
            candidate = self.fresh(plan, now=NOW)
            candidate.update(duration=5, mode="exam", resources="search")
            with self.write():
                again = retries.link_started_session(self.con, plan["items"][0]["id"], candidate)
            self.assertFalse(again["created"])
            self.assertTrue(again["payload_mismatch"])
            self.assertEqual(again["session"]["id"], first["id"])
            self.assertEqual(again["session"]["duration"], 40)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM events WHERE kind='start'").fetchone()[0], 1)

    def test_twenty_concurrent_starts_commit_one_sid_one_start_event(self):
        plan = self.plan()["plan"]
        barrier = threading.Barrier(20)
        def start(_):
            candidate = self.fresh(plan)
            with contextlib.closing(self.connect()) as con, con:
                barrier.wait(timeout=10)
                con.execute("BEGIN IMMEDIATE")
                return retries.link_started_session(con, plan["items"][0]["id"], candidate)
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(start, range(20)))
        self.assertEqual(sum(result["created"] for result in results), 1)
        self.assertEqual(len({result["session"]["id"] for result in results}), 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 2)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM events WHERE kind='start'").fetchone()[0], 1)

    def test_each_start_insert_failure_rolls_back_without_orphan_session(self):
        plan = self.plan()["plan"]
        for table, event in [("sessions", "INSERT"), ("events", "INSERT"), ("retry_items", "UPDATE")]:
            with self.subTest(table=table):
                self.con.execute(f"CREATE TRIGGER fail_link BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'fixture link failure'); END")
                with self.write():
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "fixture link failure"):
                        retries.link_started_session(self.con, plan["items"][0]["id"], self.fresh(plan))
                    self.assertEqual(self.con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)
                    self.assertEqual(self.con.execute("SELECT COUNT(*) FROM events WHERE kind='start'").fetchone()[0], 0)
                    self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_items WHERE session_id IS NOT NULL").fetchone()[0], 0)
                self.con.execute("DROP TRIGGER fail_link")

    def test_grading_blocks_next_round_and_terminal_error_completes_attempt(self):
        plan = self.plan()["plan"]
        session = self.link(plan)["session"]
        self.end(session, status="error")
        self.job(session["id"])
        with self.write():
            retries.reconcile(self.con, plan["id"], DUE)
        self.assertEqual(retries.first_open_item(self.con, plan["id"])["state"], "grading")
        self.assertEqual(len(retries.list_cards(self.con, DUE)["grading"]), 1)
        self.assert_code("PREVIOUS_PENDING", lambda: self.link(plan, index=1))
        self.con.execute("UPDATE jobs SET state=? WHERE sid=?", (json.dumps({"kind": "java", "status": "failed"}), session["id"]))
        self.con.commit()
        with self.write():
            retries.reconcile(self.con, plan["id"], DUE)
        self.assertEqual(retries.first_open_item(self.con, plan["id"])["offset_days"], 3)

    def test_same_day_sequential_rounds_and_pass_keep_remaining_schedule(self):
        plan = self.plan()["plan"]
        for index in range(3):
            session = self.link(plan, index=index)["session"]
            self.end(session)
            # Read-only authoritative state works even before a reconcile hook.
            cards = retries.list_cards(self.con, DUE)
            self.assertEqual(len(cards["due"]), int(index < 2))
            with self.write():
                state = retries.reconcile(self.con, plan["id"], DUE)
            self.assertEqual(state["status"], "completed" if index == 2 else "open")
        self.assertIsNone(retries.first_open_item(self.con, plan["id"]))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_items WHERE completed_at IS NOT NULL").fetchone()[0], 3)
        original = self.plan()
        self.assertFalse(original["created"])
        other = fixture("new-source")
        self.save(other)
        self.assertTrue(self.plan(other)["created"])

    def test_create_repairs_missed_final_hook_before_open_plan_conflict(self):
        plan = self.plan()["plan"]
        for index in range(3):
            self.end(self.link(plan, index=index)["session"])
        self.assertEqual(self.con.execute("SELECT status FROM retry_plans").fetchone()[0], "open")
        other = fixture("new-source")
        self.save(other)
        self.assertTrue(self.plan(other)["created"])

    def test_read_only_cards_hide_past_data_count_backlog_and_sort(self):
        plan = self.plan()["plan"]
        early = fixture("earlier", "another")
        self.save(early)
        with self.write():
            retries.create_plan(self.con, early, "another", "PRIVATE_REASON", "Etc/UTC", NOW - 86400000)
        before = self.con.total_changes
        cards = retries.list_cards(self.con, DUE)
        self.assertEqual(self.con.total_changes, before)
        self.assertEqual([card["pid"] for card in cards["due"]], ["another", "pair"])
        self.assertEqual(cards["due"][1]["outstanding_due_count"], 3)
        self.assertEqual(cards["due"][1]["overdue_days"], 7)
        self.assertEqual(cards["due"][1]["item_id"], plan["items"][0]["id"])
        self.assertNotIn("PAST_", json.dumps(cards))
        self.assertNotIn("PRIVATE_REASON", json.dumps(cards))
        self.assertNotIn("reason", cards["due"][0])
        self.assertEqual(cards["server_now"], DUE)
        self.assertEqual(cards["next_refresh_at"], at("2026-10-10T00:00:00+00:00"))

    def test_active_resume_and_general_active_blocks_new_start(self):
        plan = self.plan()["plan"]
        self.save(fixture("general", status="active"))
        self.assertFalse(retries.list_cards(self.con, DUE)["due"][0]["can_start"])
        self.con.execute("DELETE FROM sessions WHERE id='general'")
        self.con.commit()
        sid = self.link(plan)["session"]["id"]
        cards = retries.list_cards(self.con, DUE)
        self.assertEqual(cards["active"][0]["session_id"], sid)
        self.assertEqual(cards["active"][0]["state"], "active")

    def test_missing_source_and_linked_session_are_unavailable_without_deletion(self):
        plan = self.plan()["plan"]
        linked = self.link(plan)["session"]
        for sid in (linked["id"], self.source["id"]):
            self.con.execute("DELETE FROM sessions WHERE id=?", (sid,))
            self.con.commit()
            before = self.con.total_changes
            cards = retries.list_cards(self.con, DUE)
            self.assertEqual(self.con.total_changes, before)
            self.assertEqual(cards["unavailable"][0]["code"], "SOURCE_UNAVAILABLE")
            self.assertNotIn("title", cards["unavailable"][0])
            self.assertFalse(cards["unavailable"][0]["can_start"])
            self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_items").fetchone()[0], 3)

    def test_corrupt_source_snapshot_or_link_relation_quarantined(self):
        plan = self.plan()["plan"]
        linked = self.link(plan)["session"]
        linked["retry"]["pid"] = "wrong"
        self.save(linked)
        self.assertEqual(retries.first_open_item(self.con, plan["id"])["state"], "unavailable")
        self.assert_code("SOURCE_UNAVAILABLE", lambda: self.link(plan, index=1))
        source = copy.deepcopy(self.source)
        source["problems"][0]["tests"] = []
        self.save(source)
        self.assertEqual(len(retries.list_cards(self.con, DUE)["unavailable"]), 1)

    def test_reconcile_cache_is_not_authority_when_new_java_job_appears(self):
        plan = self.plan()["plan"]
        session = self.link(plan)["session"]
        self.end(session)
        with self.write():
            retries.reconcile(self.con, plan["id"], DUE)
        self.job(session["id"])
        self.assertEqual(retries.first_open_item(self.con, plan["id"])["state"], "grading")
        with self.write():
            retries.reconcile(self.con, plan["id"], DUE)
        self.assertIsNone(self.con.execute("SELECT completed_at FROM retry_items WHERE id=?", (plan["items"][0]["id"],)).fetchone()[0])

    def test_foreign_keys_off_still_validates_link_relation(self):
        self.assertEqual(self.con.execute("PRAGMA foreign_keys").fetchone()[0], 0)
        plan = self.plan()["plan"]
        with self.write():
            self.con.execute("UPDATE retry_items SET session_id='missing' WHERE id=?", (plan["items"][0]["id"],))
        self.assertEqual(retries.first_open_item(self.con, plan["id"])["state"], "unavailable")
        self.assert_code("SOURCE_UNAVAILABLE", lambda: self.link(plan, index=1))

    def test_foreign_keys_enabled_and_unique_session_link(self):
        self.con.execute("PRAGMA foreign_keys=ON")
        plan = self.plan()["plan"]
        linked = self.link(plan)["session"]
        with self.write():
            with self.assertRaises(sqlite3.IntegrityError):
                self.con.execute("UPDATE retry_items SET session_id=? WHERE id=?", (linked["id"], plan["items"][1]["id"]))
            with self.assertRaises(sqlite3.IntegrityError):
                self.con.execute("UPDATE retry_items SET session_id='missing' WHERE id=?", (plan["items"][1]["id"],))

    def test_existing_metrics_supply_per_problem_wall_time_without_lab_construction(self):
        with patch.dict(os.environ, {"COACH_DATA_DIR": self.temp.name}):
            from coach import store as st
            from coach.service import Lab
        session = copy.deepcopy(self.source)
        other = copy.deepcopy(session["problems"][0])
        other["id"] = "other"
        session["problems"].append(other)
        session["ended"] = session["started"] + 86400000 + 12 * 60000 + 8000
        self.save(session)
        self.add_event(session["id"], "switch", {"to": "other"}, 3 * 60000)
        self.add_event(session["id"], "switch", {"to": "pair"}, 8 * 60000)
        self.con.commit()
        with patch.object(st, "DATA", Path(self.temp.name)):
            metrics = {metric["pid"]: metric for metric in Lab.metrics(None, session)}
        self.assertEqual(metrics["pair"]["minutes"], 1447.1)
        self.assertEqual(metrics["other"]["minutes"], 5.0)
        self.assertNotIn("minutes", retries.classify_attempt(session, [], [], "pair"))

    def test_empty_list_envelope_has_no_midnight_or_cards(self):
        self.assertEqual(retries.list_cards(self.con, NOW), {
            "server_now": NOW, "next_refresh_at": None, "due": [], "active": [],
            "grading": [], "unavailable": [], "upcoming": [],
            "capabilities": {"retry_plan_expected_anchor_date": True}})


class AnchorGuardTest(RetryFixture, unittest.TestCase):
    def create(self, expected, now=NOW, **changes):
        with self.write():
            return retries.create_plan(self.con, self.source, "pair", changes.get("reason", "reason"),
                                       changes.get("timezone", "Etc/UTC"), now,
                                       expected_anchor_date=expected)

    def test_anchor_guard_saves_same_observation_and_rolls_back_mismatch(self):
        before = self.unchanged_original()
        with self.write():
            with self.assertRaises(retries.RetryError) as caught:
                retries.create_plan(self.con, self.source, "pair", "reason", "Etc/UTC", NOW,
                                    expected_anchor_date="2026-09-30")
            self.assertEqual(caught.exception.code, "ANCHOR_DATE_CHANGED")
            self.assertEqual(caught.exception.details["server_now"], NOW)
            self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_plans").fetchone()[0], 0)
            self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_items").fetchone()[0], 0)
        plan = self.create("2026-10-01")["plan"]
        self.assertEqual((plan["created_at"], plan["anchor_date"]), (NOW, "2026-10-01"))
        self.assertEqual([i["due_date"] for i in plan["items"]], ["2026-10-02", "2026-10-04", "2026-10-08"])
        self.assertEqual(self.unchanged_original(), before)

    def test_anchor_guard_idempotency_precedes_source_and_date_checks(self):
        first = self.create("2026-10-01")["plan"]
        with patch.object(retries, "_session", side_effect=AssertionError("source revalidation on replay")):
            replay = self.create("0001-01-01", now=DUE)
        self.assertFalse(replay["created"])
        self.assertNotIn("payload_mismatch", replay)
        self.assertEqual(replay["plan"]["id"], first["id"])
        self.assertEqual([i["id"] for i in replay["plan"]["items"]], [i["id"] for i in first["items"]])
        changed = self.create("9999-12-31", now=DUE, reason="changed", timezone="Asia/Seoul")
        self.assertTrue(changed["payload_mismatch"])
        self.assertEqual(changed["plan"]["anchor_date"], first["anchor_date"])

    def test_anchor_guard_exact_ascii_gregorian_format_and_legacy_none(self):
        for expected in ("", " 2026-10-01", "2026-10-01 ", "2026-1-01", "2026-10-1",
                         "20261001", "２０２６-１０-０１", "2026-02-29", "0000-01-01",
                         "2026-10-01T00:00:00Z", 20261001, True, [], {}):
            with self.subTest(expected=expected):
                with self.assertRaises(retries.RetryError) as caught:
                    self.create(expected)
                self.assertEqual(caught.exception.code, "INVALID_INPUT")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM retry_plans").fetchone()[0], 0)
        self.assertTrue(self.create(None)["created"])


class CalendarTest(RetryFixture, unittest.TestCase):
    def test_dates_month_year_leap_and_dst_boundaries(self):
        cases = [
            ("2026-10-01T15:00:00+00:00", "Etc/UTC", "2026-10-01", ["2026-10-02", "2026-10-04", "2026-10-08"]),
            ("2026-10-01T15:00:00+00:00", "Asia/Seoul", "2026-10-02", ["2026-10-03", "2026-10-05", "2026-10-09"]),
            ("2026-01-31T23:59:00+00:00", "Etc/UTC", "2026-01-31", ["2026-02-01", "2026-02-03", "2026-02-07"]),
            ("2028-02-28T12:00:00+00:00", "Etc/UTC", "2028-02-28", ["2028-02-29", "2028-03-02", "2028-03-06"]),
            ("2026-12-31T12:00:00+00:00", "Etc/UTC", "2026-12-31", ["2027-01-01", "2027-01-03", "2027-01-07"]),
            ("2026-03-07T23:59:00-05:00", "America/New_York", "2026-03-07", ["2026-03-08", "2026-03-10", "2026-03-14"]),
            ("2026-10-31T23:59:00-04:00", "America/New_York", "2026-10-31", ["2026-11-01", "2026-11-03", "2026-11-07"]),
        ]
        for index, (now, timezone, anchor, dates) in enumerate(cases):
            with self.subTest(timezone=timezone, now=now):
                source = fixture("calendar-" + str(index), "calendar-pid-" + str(index))
                self.save(source)
                with self.write():
                    plan = retries.create_plan(self.con, source, source["problems"][0]["id"], "reason", timezone, at(now))["plan"]
                self.assertEqual(plan["anchor_date"], anchor)
                self.assertEqual([item["due_date"] for item in plan["items"]], dates)

    def test_local_midnight_due_is_server_clock_based(self):
        now = at("2026-10-01T23:59:00+09:00")
        self.plan(timezone="Asia/Seoul", now=now)
        self.assertEqual(len(retries.list_cards(self.con, now)["upcoming"]), 1)
        self.assertEqual(retries.list_cards(self.con, now)["next_refresh_at"], now + 60000)
        self.assertEqual(len(retries.list_cards(self.con, now + 60000)["due"]), 1)
        self.assertEqual(retries.list_cards(self.con, now + 60000)["due"][0]["today"], "2026-10-02")

    def test_next_midnight_has_dst_23_and_25_hour_days(self):
        for index, (now, expected, hours) in enumerate([
            ("2026-03-08T00:00:00-05:00", "2026-03-09T00:00:00-04:00", 23),
            ("2026-11-01T00:00:00-04:00", "2026-11-02T00:00:00-05:00", 25),
        ]):
            with self.subTest(now=now):
                source = fixture("dst-" + str(index), "dst-pid-" + str(index))
                self.save(source)
                with self.write():
                    retries.create_plan(self.con, source, source["problems"][0]["id"], "reason", "America/New_York", at(now))
                card = next(card for card in retries.list_cards(self.con, at(now))["upcoming"] if card["pid"] == source["problems"][0]["id"])
                # Only this plan contributes to the envelope's next midnight.
                with self.write():
                    self.con.execute("DELETE FROM retry_items WHERE plan_id IN (SELECT id FROM retry_plans WHERE pid<>?)", (card["pid"],))
                    self.con.execute("DELETE FROM retry_plans WHERE pid<>?", (card["pid"],))
                refresh = retries.list_cards(self.con, at(now))["next_refresh_at"]
                self.assertEqual(refresh, at(expected))
                self.assertEqual(refresh - at(now), hours * 3600000)

    def test_multiple_plan_timezones_keep_original_due_dates_and_refresh_minimum(self):
        self.plan(timezone="Asia/Seoul")
        source = fixture("second", "second-pid")
        self.save(source)
        with self.write():
            retries.create_plan(self.con, source, "second-pid", "reason", "Etc/UTC", NOW)
        cards = retries.list_cards(self.con, NOW)
        self.assertEqual(cards["next_refresh_at"], at("2026-10-01T15:00:00+00:00"))
        self.assertEqual({card["timezone"] for card in cards["upcoming"]}, {"Etc/UTC", "Asia/Seoul"})
        future = retries.list_cards(self.con, NOW + 86400000)
        self.assertEqual({card["due_date"] for card in future["due"]}, {"2026-10-02"})


class ServiceIntegrationTest(unittest.TestCase):
    def setUp(self):
        from coach import store as st
        from coach.service import Lab
        self.st=st;self.temp=tempfile.TemporaryDirectory(prefix='cote-p4-unit-')
        self.old_data=st.DATA;st.DATA=Path(self.temp.name)
        self.clock=[NOW];self.clock_patch=patch.object(st,'now',side_effect=lambda:self.clock[0]);self.clock_patch.start()
        self.lab=Lab();self.lab.ide.prepare=lambda *args:None
        self.source=fixture(uuid.uuid4().hex);self.source['runs']=[run()]
        with st.db(True) as con:con.execute('INSERT INTO sessions VALUES (?,?,?)',(self.source['id'],self.source['started'],st.dump(self.source)))
        st.event(self.source['id'],'submit',{'run_id':'result','code':'PAST_CODE_SENTINEL'},'pair',1000)
        self.releases=[]

    def tearDown(self):
        for release in self.releases:release.set()
        self.join_workers();self.lab.close();self.clock_patch.stop()
        self.st.DATA=self.old_data;self.temp.cleanup()

    def join_workers(self):
        for thread in threading.enumerate():
            if thread.name.endswith('(work)'):thread.join(5)

    def register(self):
        return self.lab.retry_plan(dict(source_sid=self.source['id'],pid='pair',reason='PAST_REASON_SENTINEL',timezone='Etc/UTC'))['plan']

    def start(self,plan=None,**settings):
        plan=plan or self.register();self.clock[0]=DUE
        return self.lab.retry_start(dict(item_id=plan['items'][0]['id'],kind='single',mode='training',duration=40,resources='none',model='fixture',effort='low')|settings)['session']

    def source_bytes(self):
        with self.st.db() as con:
            return {table:[tuple(r) for r in con.execute(f'SELECT * FROM {table} WHERE '+('id' if table=='sessions' else 'sid')+'=? ORDER BY rowid',(self.source['id'],))] for table in ('sessions','events','media','jobs')}

    def test_snapshot_starter_blind_view_hint_and_source_rows_unchanged(self):
        before=self.source_bytes();plan=self.register()
        with patch.object(self.lab,'all_problems',side_effect=AssertionError('catalog must not be consulted')):s=self.start(plan)
        self.assertEqual(s['codes']['pair'],self.source['problems'][0]['starter'])
        self.assertNotIn('tags',s['problems'][0]);self.assertNotIn('retry_candidates',s)
        self.assertIsNone(s['thread']);self.assertEqual(s['messages'],[])
        for value in ('PAST_CODE_SENTINEL','PAST_REPORT_SENTINEL','PAST_TAG_SENTINEL','PAST_REASON_SENTINEL','PAST_THREAD_SENTINEL'):
            self.assertNotIn(value,json.dumps(s));self.assertNotIn(value,json.dumps(self.lab.evidence(s['id'],hint=True)))
        captured=[]
        def coach(thread,model,effort,prompt,progress,cancel,save_thread,images=None):
            captured.append((thread,json.loads(prompt)));return {'message':'new hint','questions':[],'report':None},{},'fresh-thread'
        with patch.object(self.lab.codex,'chat',side_effect=coach):
            self.lab.chat(s['id'],dict(kind='hint',pid='pair',model='fixture',effort='low'));self.join_workers()
        self.assertIsNone(captured[0][0]);self.assertEqual(captured[0][1]['evidence']['past_reports'],[])
        for value in ('PAST_CODE_SENTINEL','PAST_REPORT_SENTINEL','PAST_TAG_SENTINEL','PAST_REASON_SENTINEL'):
            self.assertNotIn(value,json.dumps(captured))
        self.assertEqual(self.source_bytes(),before)
        with self.assertRaises(retries.RetryError) as error:self.lab.retry_comparison(s['id'])
        self.assertEqual(error.exception.code,'RETRY_ACTIVE')

    def test_twenty_service_starts_return_same_session_and_one_start_event(self):
        plan=self.register();self.clock[0]=DUE;data=dict(item_id=plan['items'][0]['id'],kind='single',mode='training',duration=40,resources='none')
        with ThreadPoolExecutor(max_workers=20) as pool:responses=list(pool.map(lambda _:self.lab.retry_start(data),range(20)))
        ids={response['session']['id'] for response in responses};self.assertEqual(len(ids),1)
        self.assertEqual(sum(r['created'] for r in responses),1)
        sid=next(iter(ids))
        with self.st.db() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM sessions').fetchone()[0],2)
            self.assertEqual(con.execute("SELECT count(*) FROM events WHERE sid=? AND kind='start'",(sid,)).fetchone()[0],1)
        repeat=self.lab.retry_start(data|{'duration':20});self.assertTrue(repeat['payload_mismatch']);self.assertEqual(repeat['session']['duration'],40)
        self.lab.finish(sid);repeat=self.lab.retry_start(data);self.assertFalse(repeat['created']);self.assertEqual(repeat['session']['id'],sid);self.assertEqual(repeat['session']['status'],'completed')

    def test_invalid_future_order_active_and_other_pid_are_rejected(self):
        plan=self.register();data=dict(item_id=plan['items'][0]['id'],kind='single',mode='training',duration=40,resources='none')
        with self.assertRaises(retries.RetryError) as error:self.lab.retry_start(data)
        self.assertEqual(error.exception.code,'NOT_DUE');self.clock[0]=DUE
        with self.assertRaises(retries.RetryError) as error:self.lab.retry_start(data|{'item_id':plan['items'][1]['id']})
        self.assertEqual(error.exception.code,'PREVIOUS_PENDING')
        other=self.lab.new_session(dict(problem_ids=['brackets'],kind='single',mode='training',duration=40,resources='none'))
        with self.assertRaises(retries.RetryError) as error:self.lab.retry_start(data)
        self.assertEqual(error.exception.code,'ACTIVE_SESSION_EXISTS');self.lab.finish(other['id'])
        s=self.lab.retry_start(data)['session']
        with self.assertRaises(ValueError):self.lab.run(s['id'],'brackets',True)

    def test_ide_preparation_without_db_lock_timer_starts_after_and_failure_rolls_back(self):
        plan=self.register();self.clock[0]=DUE
        def prepare(*args):
            with self.st.db(True) as con:self.assertEqual(con.execute('SELECT count(*) FROM retry_plans').fetchone()[0],1)
            self.clock[0]+=5000
        self.lab.ide.prepare=prepare
        with self.st.db(True) as con:con.execute("CREATE TRIGGER fail_retry_link BEFORE UPDATE OF session_id ON retry_items BEGIN SELECT RAISE(ABORT,'injected rollback'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.start(plan)
        with self.st.db() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM sessions').fetchone()[0],1)
            self.assertEqual(con.execute("SELECT count(*) FROM events WHERE kind='start'").fetchone()[0],0)
            self.assertIsNone(con.execute('SELECT session_id FROM retry_items WHERE id=?',(plan['items'][0]['id'],)).fetchone()[0])
        with self.st.db(True) as con:con.execute('DROP TRIGGER fail_retry_link')
        s=self.start(plan);self.assertEqual(s['started'],DUE+5000);self.assertEqual(s['deadline'],s['started']+40*60000)

    def delayed_java(self,status='passed'):
        entered=threading.Event();release=threading.Event();self.releases.append(release)
        def java(code,problem,submit):
            entered.set();self.assertTrue(release.wait(5));return dict(status=status,passed=int(status=='passed'),total=1,output='',error=None)
        return entered,release,java

    def test_finish_keeps_grading_until_delayed_java_then_reconciles(self):
        plan=self.register();s=self.start(plan);entered,release,java=self.delayed_java()
        with patch('coach.service.run_java',side_effect=java):
            job=self.lab.run(s['id'],'pair',True);self.assertTrue(entered.wait(2));self.lab.finish(s['id'])
            self.assertEqual(self.lab.retry_comparison(s['id'])['state'],'grading')
            self.assertEqual(self.lab.retry_list()['grading'][0]['session_id'],s['id'])
            with self.assertRaises(retries.RetryError) as error:self.lab.retry_start(dict(item_id=plan['items'][1]['id'],kind='single',mode='training',duration=40,resources='none'))
            self.assertEqual(error.exception.code,'PREVIOUS_PENDING');release.set();self.join_workers()
        self.assertEqual(self.lab.get_job(job['job_id'])['status'],'completed')
        comparison=self.lab.retry_comparison(s['id']);self.assertEqual(comparison['state'],'ready');self.assertEqual(comparison['current']['outcome'],'passed')
        self.assertEqual(self.lab.retry_list()['due'][0]['offset_days'],3)
        with self.st.db() as con:self.assertIsNotNone(con.execute('SELECT completed_at FROM retry_items WHERE id=?',(plan['items'][0]['id'],)).fetchone()[0])

    def test_run_admission_blocks_finish_before_job_insert_without_deadlock(self):
        s=self.start();entered=threading.Event();allow=threading.Event();self.releases.append(allow)
        java_entered,java_release,java=self.delayed_java();finished=threading.Event();errors=[];original=self.lab.job
        def job(*args):entered.set();self.assertTrue(allow.wait(5));return original(*args)
        def start_run():
            try:self.lab.run(s['id'],'pair',True)
            except Exception as error:errors.append(error)
        def finish():
            try:self.lab.finish(s['id']);finished.set()
            except Exception as error:errors.append(error)
        with patch.object(self.lab,'job',side_effect=job),patch('coach.service.run_java',side_effect=java):
            runner=threading.Thread(target=start_run);runner.start();self.assertTrue(entered.wait(2))
            closer=threading.Thread(target=finish);closer.start();self.assertFalse(finished.wait(.05))
            allow.set();runner.join(2);closer.join(2);self.assertFalse(runner.is_alive());self.assertFalse(closer.is_alive());self.assertTrue(finished.is_set());self.assertFalse(errors)
            self.assertEqual(self.lab.retry_comparison(s['id'])['state'],'grading');java_release.set();self.join_workers()
        self.assertEqual(self.lab.retry_comparison(s['id'])['current']['outcome'],'passed')

    def test_exam_expire_registers_forced_job_before_reconcile_and_training_overtime(self):
        plan=self.register();s=self.start(plan,mode='exam');entered,release,java=self.delayed_java()
        with patch('coach.service.run_java',side_effect=java):
            self.clock[0]=s['deadline']+1
            with ThreadPoolExecutor(max_workers=1) as pool:pool.submit(self.lab.expire,s['id']).result(timeout=2)
            self.assertTrue(entered.wait(2));self.assertEqual(self.lab.retry_comparison(s['id'])['state'],'grading')
            self.assertEqual(self.st.session(s['id'])['ended'],s['deadline']);release.set();self.join_workers()
        self.assertEqual(self.lab.retry_comparison(s['id'])['current']['outcome'],'passed')
        second=self.lab.retry_start(dict(item_id=plan['items'][1]['id'],kind='single',mode='training',duration=5,resources='none'))['session']
        self.clock[0]=second['deadline']+1;self.lab.expire(second['id']);self.assertEqual(self.st.session(second['id'])['status'],'active')
        with patch('coach.service.run_java',return_value=dict(status='passed',passed=1,total=1,output='',error=None)):
            self.lab.run(second['id'],'pair',True);self.join_workers()
        self.lab.finish(second['id']);comparison=self.lab.retry_comparison(second['id'])
        self.assertEqual(comparison['current']['outcome'],'not_submitted');self.assertEqual(comparison['current']['overtime_outcome'],'passed')

    def test_restart_failed_java_is_ungraded_and_completion_cache_recovers(self):
        from coach.service import Lab
        plan=self.register();s=self.start(plan);sid=s['id']
        self.st.event(sid,'submit',{'run_id':'never-finished'},'pair',1000)
        self.st.mutate(sid,lambda s:s.update(status='completed',ended=s['started']+2000))
        with self.st.db(True) as con:con.execute('INSERT INTO jobs VALUES (?,?,?)',('interrupted',sid,self.st.dump(dict(id='interrupted',sid=sid,kind='java',status='running'))))
        self.lab.close();self.lab=Lab();self.lab.ide.prepare=lambda *args:None
        comparison=self.lab.retry_comparison(sid);self.assertEqual(comparison['state'],'ready');self.assertEqual(comparison['current']['outcome'],'ungraded')
        with self.st.db() as con:self.assertIsNotNone(con.execute('SELECT completed_at FROM retry_items WHERE id=?',(plan['items'][0]['id'],)).fetchone()[0])

    def test_comparison_reuses_selected_problem_metrics_and_keeps_source_unavailable_current(self):
        source=self.st.session(self.source['id']);other=copy.deepcopy(BUILTINS[2]);source['problems'].append(other);source['codes'][other['id']]=other['starter'];source['ended']=source['started']+600000
        with self.st.db(True) as con:con.execute('UPDATE sessions SET state=? WHERE id=?',(self.st.dump(source),source['id']))
        self.st.event(source['id'],'switch',{'to':other['id']},'pair',120000);self.st.event(source['id'],'switch',{'to':'pair'},other['id'],420000)
        plan=self.register();s=self.start(plan);self.clock[0]=s['started']+216000;self.lab.finish(s['id'])
        comparison=self.lab.retry_comparison(s['id']);self.assertEqual(comparison['source']['minutes'],5.0);self.assertEqual(comparison['current']['minutes'],3.6);self.assertEqual(comparison['delta_minutes'],-1.4)
        with self.st.db(True) as con:con.execute('DELETE FROM sessions WHERE id=?',(source['id'],))
        unavailable=self.lab.retry_comparison(s['id']);self.assertEqual(unavailable['state'],'unavailable');self.assertEqual(unavailable['current']['minutes'],3.6);self.assertNotIn('reason',unavailable)

    def test_three_rounds_close_plan_and_new_failed_source_can_register(self):
        plan=self.register();self.clock[0]=DUE;last=None
        for item in plan['items']:
            s=self.lab.retry_start(dict(item_id=item['id'],kind='single',mode='training',duration=40,resources='none'))['session'];self.lab.finish(s['id']);last=s
        with self.st.db() as con:
            self.assertEqual(con.execute('SELECT status FROM retry_plans WHERE id=?',(plan['id'],)).fetchone()[0],'completed')
            self.assertEqual(con.execute('SELECT count(*) FROM retry_items WHERE plan_id=? AND completed_at IS NOT NULL',(plan['id'],)).fetchone()[0],3)
        self.assertFalse(self.lab.retry_list()['due'])
        self.assertTrue(self.lab.retry_plan(dict(source_sid=last['id'],pid='pair',reason='next attempt',timezone='Etc/UTC'))['created'])


if __name__ == "__main__":
    unittest.main()
