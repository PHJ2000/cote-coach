"""Independent HTTP acceptance oracles for retry-v1 + retry-v1.1-anchor-date.

Run from an isolated copy of the latest working tree:
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_retry_contract.py

Missing product routes are ordinary FAILs labelled EXPECTED_API_NOT_IMPLEMENTED,
never xfails/skips. Fixture/import/transport exceptions remain unittest ERRORs.
The tests do not call or copy coach.retries implementation helpers. V11's existing
regressions and V12's real Java/media gate require separate parent execution.
"""
from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import uuid
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from retry_fixture import (DEFAULT_NOW, LEGACY_DDL, PID, SENTINELS, STARTER,
                           FakeCoach, FakeIDE, FakeSpeech, RetryFixture, dumps, epoch)


V_MAP = {
    "V1": "test_v1_*: additive old-DB init, 20 HTTP create racers, idempotency/conflict, INSERT-trigger rollback",
    "V2": "test_v2_* + RetryAnchorDateContractTest: calendar/validation/capability, real HTTP + SQLite write-lock and guard-to-INSERT boundaries (V2-AD1..6)",
    "V3": "test_v3_* + RetryAnchorDateContractTest: start racers, response-loss replay, duplicate/eligibility/conflict priority and ordered HTTP create racers (V3-AD1..3)",
    "V4": "test_v4_* and retry-browser.cjs: active payload/prompt/DOM sentinel absence, fresh thread, completed-only comparison",
    "V5": "test_v5_*: raw original rows/media bytes unchanged, deleted/changed catalog snapshot, unavailable source",
    "V6": "test_v6_*: explicit result order/boundary/error/overtime, multi-problem wall time, live comparison refresh",
    "V7": "test_v7_*: job-admission/finish race, pending/failed Java, exam expiry, training overtime, final reconcile",
    "V8": "test_v8_* and retry-browser.cjs: two nonempty segments/gap, observed old-sid 400 + byte/id/URL preservation + delivery; retry-review-coverage.cjs: zero-byte stop and negative fault probes",
    "V9": "retry-browser.cjs: keyboard/reason/XSS/list errors/focus/midnight + additive capability; retry-review-coverage.cjs: V9-AD1..3 real guard and explicit UI transport fault boundaries, review preservation regressions",
    "V10": "test_v10_*: actual HTTP ZIP backup -> isolated restore, legacy archive and relation/byte equality",
    "V11": "Separate existing Python/JS regressions + npm run build, on immutable isolated source only",
    "V12": "retry-browser.cjs is synthetic capture + mock Java/STT/coach. Real Java and media decode remain a parent gate; no substitute PASS",
}


class RetryFixtureIsolationTest(unittest.TestCase):
    def test_fixture_uses_temp_before_import_and_loopback_ephemeral_port(self):
        with RetryFixture() as f:
            self.assertTrue(f.data.name.startswith("cote-retry-p3-"))
            self.assertEqual(f.st.DATA, f.data)
            self.assertTrue(f.base_url.startswith("http://127.0.0.1:"))
            self.assertNotIn(f.http.server_port, (8766, 8876, 8881))
            self.assertEqual(f.request("/_retry_fixture/status")[0], 200)
            with self.assertRaises(ValueError):
                f.request("http://localhost:8766/api/v2/start", {})

    def test_fixture_has_no_external_execution_and_controls_are_token_guarded(self):
        with RetryFixture() as f:
            self.assertIsInstance(f.lab.codex, FakeCoach)
            self.assertIsInstance(f.lab.ide, FakeIDE)
            self.assertIsInstance(f.lab.speech, FakeSpeech)
            self.assertEqual(f.request("/api/v2/models")[0], 200)
            self.assertEqual(f.request("/api/v2/health")[1]["fixture_mocks"], True)
            self.assertEqual(f.request("/_retry_fixture/status", headers={"X-Retry-Fixture": "wrong"})[0], 403)
            self.assertEqual(f.request("/api/state")[0], 404)
            self.assertEqual(f.java_calls, [])
            self.assertEqual(f.lab.codex.calls, [])

    def test_fixture_old_rows_and_fake_media_are_reproducible(self):
        with RetryFixture() as f:
            self.assertEqual(f.legacy_baseline, f.raw_snapshot())
            self.assertEqual(len(f.raw_snapshot()["media_hashes"]), 2)
            self.assertEqual(len(f.sql("SELECT * FROM media")), 2)

    def test_fixture_mocks_real_run_and_hint_without_replacing_http_routes(self):
        with RetryFixture(media=False) as f:
            status, s = f.request("/api/v2/start", dict(problem_ids=[PID], kind="single", mode="training",
                                                       duration=40, resources="none", model="fixture-model", effort="low"))
            self.assertEqual(status, 200, s)
            f.now_ms += 1000
            self.assertEqual(f.request("/api/v2/run", dict(sid=s["id"], pid=PID, submit=True))[0], 200)
            f.settle()
            self.assertEqual(len(f.java_calls), 1)
            self.assertEqual(f.request("/api/v2/chat", dict(sid=s["id"], pid=PID, kind="hint", message="새 힌트",
                                                          model="fixture-model", effort="low"))[0], 200)
            f.settle()
            self.assertEqual(len(f.lab.codex.calls), 1)
            self.assertEqual(f.handshake()["external_ai_calls"], 0)

    def test_fixture_upload_rejection_is_observed_token_guarded_and_source_scoped(self):
        with RetryFixture(media=False) as f:
            segment = "e" * 32
            policy = dict(sid=f.source["id"], segment=segment, reject=True)
            self.assertEqual(f.request("/_retry_fixture/upload-rejection", policy,
                                       headers={"X-Retry-Fixture": "wrong"})[0], 403)
            self.assertEqual(f.request("/_retry_fixture/upload-rejection", dict(policy, sid="other"))[0], 400)
            self.assertEqual(f.request("/_retry_fixture/upload-rejection", policy)[0], 200)
            before = f.raw_snapshot(f.source["id"])
            url = "/api/v2/upload?" + urlencode(dict(sid=f.source["id"], kind="screen", segment=segment,
                                                     seq=0, start=0, mime="video/webm"))
            raw = b"P3 observed rejected source bytes"
            self.assertEqual(f.request(url, raw=raw)[0], 400)
            self.assertEqual(before, f.raw_snapshot(f.source["id"]))
            self.assertEqual(f.upload_proof(), [dict(sid=f.source["id"], segment=segment, kind="screen",
                                                    seq="0", bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), status=400)])
            self.assertEqual(f.request("/_retry_fixture/upload-rejection", dict(policy, reject=False))[0], 200)
            self.assertEqual(f.request(url, raw=raw)[0], 200)
            self.assertEqual([x["status"] for x in f.upload_proof()], [400, 200])
            self.assertEqual(f.sql("SELECT size FROM media WHERE sid=? AND segment=?", (f.source["id"], segment)), [(len(raw),)])


class RetryContractTest(unittest.TestCase):
    def setUp(self):
        self.f = RetryFixture().__enter__()
        self.addCleanup(self.f.close)

    def ok(self, path, data=None, **kwargs):
        status, dto = self.f.request(path, data, **kwargs)
        missing = isinstance(dto, dict) and dto.get("error") in {"경로 없음", "찾을 수 없는 경로입니다."}
        label = "EXPECTED_API_NOT_IMPLEMENTED" if missing else "CONTRACT_FAILURE"
        self.assertEqual(status, 200, f"{label}: {path}: HTTP {status}: {dto}")
        return dto

    def error(self, path, code, data=None):
        status, dto = self.f.request(path, data)
        self.assertEqual(status, 400, f"retry-v1 requires HTTP 400: {path}: {dto}")
        self.assertEqual(dto.get("code"), code, f"Missing/wrong stable code: {path}: {dto}")
        self.assertTrue(dto.get("error"), dto)
        return dto

    def register(self, source=None, **changes):
        d = dict(source_sid=(source or self.f.source)["id"], pid=(source or self.f.source)["problems"][0]["id"],
                 reason=SENTINELS["reason"], timezone="Asia/Seoul")
        d.update(changes)
        dto = self.ok("/api/v2/retry-plan", d)
        self.assertIn("created", dto)
        self.assertEqual([i["offset_days"] for i in dto["plan"]["items"]], [1, 3, 7])
        return dto

    def due(self, plan, days=7):
        self.f.now_ms += days * 86400000
        return plan["items"][0]

    def start(self, item, **changes):
        d = dict(item_id=item["id"], mode="training", kind="single", duration=40,
                 resources="none", model="fixture-model", effort="low")
        d.update(changes)
        return self.ok("/api/v2/retry-start", d)

    def retry(self, source=None, **settings):
        plan = self.register(source)["plan"]
        item = self.due(plan)
        return plan, self.start(item, **settings)["session"]

    def finish(self, s, elapsed=120000):
        self.f.now_ms += elapsed
        return self.ok("/api/v2/finish", dict(sid=s["id"]))

    def compare(self, s):
        return self.ok("/api/v2/retry-comparison/" + s["id"])

    def no_past(self, value):
        raw = dumps(value)
        for name, sentinel in SENTINELS.items():
            self.assertNotIn(sentinel, raw, f"Past {name} leaked")

    def test_v1_old_db_adds_tables_without_rewriting_legacy_rows(self):
        self.assertEqual(self.f.legacy_baseline, self.f.raw_snapshot())
        names = {x[0] for x in self.f.sql("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"retry_plans", "retry_items"} <= names, "EXPECTED_API_NOT_IMPLEMENTED: additive retry schema absent")
        self.assertEqual(self.f.sql("SELECT COUNT(*) FROM retry_plans")[0][0], 0)
        self.assertEqual(self.f.sql("SELECT COUNT(*) FROM retry_items")[0][0], 0)

    def test_v1_twenty_concurrent_http_registrations_have_one_plan_three_items(self):
        before = self.f.raw_snapshot(self.f.source["id"])
        barrier = threading.Barrier(20)
        d = dict(source_sid=self.f.source["id"], pid=PID, reason=SENTINELS["reason"], timezone="Asia/Seoul")

        def call(_):
            barrier.wait(5)
            return self.f.request("/api/v2/retry-plan", d)

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            responses = list(pool.map(call, range(20)))
        for status, dto in responses:
            self.assertEqual(status, 200, f"EXPECTED_API_NOT_IMPLEMENTED or race failure: {dto}")
        self.assertEqual(len({x[1]["plan"]["id"] for x in responses}), 1)
        self.assertEqual(sum(x[1]["created"] for x in responses), 1)
        self.assertEqual(self.f.sql("SELECT COUNT(*) FROM retry_plans")[0][0], 1)
        self.assertEqual(self.f.sql("SELECT COUNT(*) FROM retry_items")[0][0], 3)
        self.assertEqual(before, self.f.raw_snapshot(self.f.source["id"]))

    def test_v1_duplicate_payload_preserves_first_reason_timezone(self):
        original = self.register()
        again = self.register(reason="changed", timezone="Etc/UTC")
        self.assertFalse(again["created"])
        self.assertTrue(again["payload_mismatch"])
        self.assertEqual(again["plan"], original["plan"])
        other = self.f.seed()
        error = self.error("/api/v2/retry-plan", "PLAN_EXISTS", dict(source_sid=other["id"], pid=PID,
                           reason="different original", timezone="Etc/UTC"))
        self.assertEqual(error["plan_id"], original["plan"]["id"])

    def test_v1_create_rolls_back_at_each_plan_item_insert_stage(self):
        self.register()  # Ensure schema/API present before installing faults.
        for table, clause in (("retry_plans", ""), ("retry_items", "WHEN NEW.offset_days=1"),
                              ("retry_items", "WHEN NEW.offset_days=3"), ("retry_items", "WHEN NEW.offset_days=7")):
            with self.subTest(table=table, clause=clause):
                s = self.f.seed(pid="fault-" + uuid.uuid4().hex)
                before = self.f.raw_snapshot(s["id"])
                count = [self.f.sql("SELECT COUNT(*) FROM " + t)[0][0] for t in ("retry_plans", "retry_items")]
                self.f.sql(f"CREATE TRIGGER p3_fault BEFORE INSERT ON {table} {clause} BEGIN SELECT RAISE(ABORT,'P3 injected disk fault'); END")
                try:
                    status, _ = self.f.request("/api/v2/retry-plan", dict(source_sid=s["id"], pid=s["problems"][0]["id"],
                                                                        reason="fault", timezone="Etc/UTC"))
                    self.assertEqual(status, 400)
                finally:
                    self.f.sql("DROP TRIGGER p3_fault")
                self.assertEqual(count, [self.f.sql("SELECT COUNT(*) FROM " + t)[0][0] for t in ("retry_plans", "retry_items")])
                self.assertEqual(before, self.f.raw_snapshot(s["id"]))
                self.assertTrue(self.register(s)["created"])

    def test_v2_october_registration_anchors_now_not_old_source_end(self):
        plan = self.register()["plan"]
        self.assertEqual(plan["anchor_date"], "2026-10-01")
        self.assertEqual([i["due_date"] for i in plan["items"]], ["2026-10-02", "2026-10-04", "2026-10-08"])

    def test_v2_calendar_dates_month_leap_dst_and_zone_boundaries(self):
        cases = [
            ("2026-01-31T14:59:00Z", "Asia/Seoul", "2026-01-31", ["2026-02-01", "2026-02-03", "2026-02-07"]),
            ("2028-02-28T12:00:00Z", "Etc/UTC", "2028-02-28", ["2028-02-29", "2028-03-02", "2028-03-06"]),
            ("2026-03-08T04:59:00Z", "America/New_York", "2026-03-07", ["2026-03-08", "2026-03-10", "2026-03-14"]),
            ("2026-11-01T03:59:00Z", "America/New_York", "2026-10-31", ["2026-11-01", "2026-11-03", "2026-11-07"]),
            ("2026-10-01T15:01:00Z", "Etc/UTC", "2026-10-01", ["2026-10-02", "2026-10-04", "2026-10-08"]),
            ("2026-10-01T15:01:00Z", "Asia/Seoul", "2026-10-02", ["2026-10-03", "2026-10-05", "2026-10-09"]),
        ]
        for iso, tz, anchor, expected in cases:
            with self.subTest(iso=iso, timezone=tz):
                self.f.now_ms = epoch(iso)
                source = self.f.seed(pid="calendar-" + uuid.uuid4().hex)
                plan = self.register(source, timezone=tz)["plan"]
                try:
                    self.assertEqual(plan["anchor_date"], anchor)
                    self.assertEqual([i["due_date"] for i in plan["items"]], expected)
                    dto = self.ok("/api/v2/retries")
                    next_midnight = {
                        ("2026-01-31T14:59:00Z", "Asia/Seoul"): "2026-01-31T15:00:00Z",
                        ("2028-02-28T12:00:00Z", "Etc/UTC"): "2028-02-29T00:00:00Z",
                        ("2026-03-08T04:59:00Z", "America/New_York"): "2026-03-08T05:00:00Z",
                        ("2026-11-01T03:59:00Z", "America/New_York"): "2026-11-01T04:00:00Z",
                        ("2026-10-01T15:01:00Z", "Etc/UTC"): "2026-10-02T00:00:00Z",
                        ("2026-10-01T15:01:00Z", "Asia/Seoul"): "2026-10-02T15:00:00Z",
                    }[(iso, tz)]
                    self.assertEqual(dto["next_refresh_at"], epoch(next_midnight))
                finally:
                    # Independent date examples have exactly one synthetic plan;
                    # no conditional assertion can silently skip DST refresh.
                    self.f.sql("DELETE FROM retry_items WHERE plan_id=?", (plan["id"],))
                    self.f.sql("DELETE FROM retry_plans WHERE id=?", (plan["id"],))

    def test_v2_local_midnight_due_pc_date_ignored_and_plan_zone_fixed(self):
        self.f.now_ms = epoch("2026-10-01T14:59:00Z")
        plan = self.register()["plan"]
        self.assertEqual(self.ok("/api/v2/retries")["due"], [])
        self.error("/api/v2/retry-start", "NOT_DUE", dict(item_id=plan["items"][0]["id"],
                   kind="single", mode="training", duration=40, resources="none"))
        self.f.now_ms = epoch("2026-10-01T15:00:00Z")
        # Client query/date cannot move the server's due boundary.
        dto = self.ok("/api/v2/retries?today=2000-01-01&timezone=Etc%2FUTC")
        card = dto["due"][0]
        self.assertEqual((card["today"], card["timezone"], card["due_date"]), ("2026-10-02", "Asia/Seoul", "2026-10-02"))
        self.assertEqual(dto["server_now"], self.f.now_ms)
        self.assertEqual(dto["next_refresh_at"], epoch("2026-10-02T15:00:00Z"))

    def test_v2_dst_transition_day_next_refresh_is_23_or_25_hours(self):
        for now, midnight, expected_dates, hours in (
            ("2026-03-08T05:00:00Z", "2026-03-09T04:00:00Z", ["2026-03-09", "2026-03-11", "2026-03-15"], 23),
            ("2026-11-01T04:00:00Z", "2026-11-02T05:00:00Z", ["2026-11-02", "2026-11-04", "2026-11-08"], 25),
        ):
            with self.subTest(now=now, hours=hours):
                self.f.now_ms = epoch(now)
                source = self.f.seed(pid="dst-day-" + uuid.uuid4().hex)
                plan = self.register(source, timezone="America/New_York")["plan"]
                try:
                    self.assertEqual([i["due_date"] for i in plan["items"]], expected_dates)
                    dto = self.ok("/api/v2/retries")
                    self.assertEqual(dto["next_refresh_at"], epoch(midnight))
                    self.assertEqual(dto["next_refresh_at"] - dto["server_now"], hours * 3600000)
                finally:
                    self.f.sql("DELETE FROM retry_items WHERE plan_id=?", (plan["id"],))
                    self.f.sql("DELETE FROM retry_plans WHERE id=?", (plan["id"],))

    def test_v2_invalid_reason_timezone_and_unicode_codepoint_limit(self):
        self.ok("/api/v2/retries")
        d = dict(source_sid=self.f.source["id"], pid=PID, reason="why", timezone="Asia/Seoul")
        for changes in [dict(reason=" "), dict(reason="x" * 201), dict(reason="a\x00b"),
                        dict(reason="a\x01b"), dict(timezone="Mars/Olympus"), dict(timezone=""),
                        dict(timezone=None), dict(pid="missing"), dict(source_sid="missing")]:
            with self.subTest(changes=changes):
                self.error("/api/v2/retry-plan", "INVALID_INPUT", d | changes)
        good = self.register(reason="  " + "🧠" * 200 + "  ")["plan"]
        self.assertEqual(good["reason"], "🧠" * 200)
        other = self.f.seed(pid="newline")
        self.assertEqual(self.register(other, reason="a\nb")["plan"]["reason"], "a\nb")

    def test_v3_twenty_concurrent_starts_one_sid_one_start_event(self):
        plan = self.register()["plan"]
        item = self.due(plan)
        barrier = threading.Barrier(20)
        d = dict(item_id=item["id"], kind="single", mode="training", duration=40, resources="none")

        def call(_):
            barrier.wait(5)
            return self.f.request("/api/v2/retry-start", d)

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            responses = list(pool.map(call, range(20)))
        for status, dto in responses:
            self.assertEqual(status, 200, dto)
        sid = responses[0][1]["session"]["id"]
        self.assertNotEqual(sid, self.f.source["id"])
        self.assertEqual({x[1]["session"]["id"] for x in responses}, {sid})
        self.assertEqual(sum(x[1]["created"] for x in responses), 1)
        self.assertEqual(self.f.sql("SELECT COUNT(*) FROM sessions")[0][0], 2)
        self.assertEqual(self.f.sql("SELECT COUNT(*) FROM events WHERE sid=? AND kind='start'", (sid,))[0][0], 1)
        self.assertEqual(self.f.sql("SELECT session_id FROM retry_items WHERE id=?", (item["id"],))[0][0], sid)

    def test_v3_response_loss_replay_and_changed_settings_do_not_create(self):
        plan, s = self.retry()
        item = plan["items"][0]
        again = self.start(item, duration=5, mode="exam", resources="search")
        self.assertFalse(again["created"])
        self.assertTrue(again["payload_mismatch"])
        self.assertEqual(again["session"]["id"], s["id"])
        self.assertEqual((again["session"]["duration"], again["session"]["mode"]), (40, "training"))
        self.finish(s)
        replay = self.start(item)
        self.assertFalse(replay["created"])
        self.assertEqual((replay["session"]["id"], replay["session"]["status"]), (s["id"], "completed"))

    def test_v3_future_and_out_of_order_and_other_active_rejected(self):
        plan = self.register()["plan"]
        settings = dict(kind="single", mode="training", duration=40, resources="none")
        self.error("/api/v2/retry-start", "NOT_DUE", settings | dict(item_id=plan["items"][0]["id"]))
        self.due(plan)
        self.error("/api/v2/retry-start", "PREVIOUS_PENDING", settings | dict(item_id=plan["items"][1]["id"]))
        normal = self.ok("/api/v2/start", settings | dict(problem_ids=[PID]))
        dto = self.ok("/api/v2/retries")
        self.assertFalse(dto["due"][0]["can_start"])
        self.error("/api/v2/retry-start", "ACTIVE_SESSION_EXISTS", settings | dict(item_id=plan["items"][0]["id"]))
        self.finish(normal)
        self.assertTrue(self.start(plan["items"][0])["created"])

    def test_v3_start_rollback_session_start_event_and_item_link_stages(self):
        plan = self.register()["plan"]
        item = self.due(plan)
        for table, event, condition in (("sessions", "INSERT", "json_extract(NEW.state,'$.retry') IS NOT NULL"),
                                        ("events", "INSERT", "NEW.kind='start' AND NEW.sid!='" + self.f.source["id"] + "'"),
                                        ("retry_items", "UPDATE OF session_id", "NEW.session_id IS NOT NULL")):
            with self.subTest(stage=table):
                before = self.f.raw_snapshot()
                items = self.f.sql("SELECT * FROM retry_items ORDER BY id")
                self.f.sql(f"CREATE TRIGGER p3_start_fault BEFORE {event} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'P3 injected start write fault'); END")
                try:
                    status, _ = self.f.request("/api/v2/retry-start", dict(item_id=item["id"], kind="single",
                                                   mode="training", duration=40, resources="none"))
                    self.assertEqual(status, 400)
                finally:
                    self.f.sql("DROP TRIGGER p3_start_fault")
                self.assertEqual(before, self.f.raw_snapshot())
                self.assertEqual(items, self.f.sql("SELECT * FROM retry_items ORDER BY id"))
        self.assertTrue(self.start(item)["created"])

    def test_v3_client_source_code_reason_and_problem_ids_cannot_replace_item_snapshot(self):
        plan = self.register()["plan"]
        item = self.due(plan)
        dto = self.start(item, source_sid="forged", problem_ids=["brackets"], pid="brackets", reason="forged",
                         code="forged", codes={PID: "forged"})
        s = dto["session"]
        self.assertEqual(s["codes"], {PID: STARTER})
        self.assertEqual(s["retry"]["source_sid"], self.f.source["id"])
        self.assertEqual([p["id"] for p in s["problems"]], [PID])
        status, _ = self.f.request("/api/v2/run", dict(sid=s["id"], pid="brackets", submit=True))
        self.assertEqual(status, 400)
        self.assertEqual(self.f.java_calls, [])

    def test_v3_ide_preparation_outside_write_transaction_and_before_timer_origin(self):
        plan = self.register()["plan"]
        item = self.due(plan)
        preparations = []

        def prepare(key, code):
            # A second writer must be able to acquire the DB while IDE prepares.
            with closing(sqlite3.connect(self.f.data / "lab.sqlite3", timeout=0.05)) as con:
                con.execute("BEGIN IMMEDIATE")
                con.rollback()
            preparations.append((key, code))
            self.f.now_ms += 90000

        self.f.lab.ide.prepare = prepare
        s = self.start(item)["session"]
        self.assertTrue(preparations)
        self.assertEqual(s["started"], self.f.now_ms)
        self.assertEqual(s["deadline"] - s["started"], 40 * 60000)
        self.assertEqual(preparations[0][1], STARTER)

    def test_v4_active_json_starter_fresh_state_and_comparison_block(self):
        _, s = self.retry()
        self.no_past(s)
        self.assertEqual(s["codes"], {PID: STARTER})
        for key in ("runs", "messages", "transcripts", "events", "media"):
            if key == "events":
                self.assertEqual([e["kind"] for e in s[key]], ["start"])
            else:
                self.assertEqual(s[key], [], key)
        self.assertIsNone(s["thread"])
        self.assertIsNone(s["report"])
        self.assertIsNone(s["deadline_codes"])
        for key in ("frames", "analysis_inputs", "report_history"):
            self.assertEqual(s.get(key, []), [], f"Previous {key} copied")
        self.assertFalse(s.get("code_versions"), "Previous code version copied")
        self.assertNotIn("tags", s["problems"][0])
        self.assertNotIn("retry_candidates", s)
        self.assertEqual(set(s["retry"]), {"plan_id", "item_id", "source_sid", "pid"})
        self.error("/api/v2/retry-comparison/" + s["id"], "RETRY_ACTIVE")
        self.no_past(self.ok("/api/v2/session/" + s["id"]))

    def test_v4_actual_hint_prompt_excludes_every_past_sentinel_and_past_reports(self):
        _, s = self.retry()
        self.ok("/api/v2/chat", dict(sid=s["id"], pid=PID, kind="hint", message="새 접근 힌트",
                                    model="fixture-model", effort="low"))
        self.f.settle()
        call = self.f.lab.codex.calls[-1]
        self.assertIsNone(call["thread"])
        self.no_past(call)
        self.assertEqual(call["prompt"]["evidence"]["past_reports"], [])
        self.assertNotIn("tags", call["prompt"]["evidence"]["problems"][0])
        self.assertEqual(call["prompt"]["evidence"]["current_code"], {PID: STARTER})

    def test_v4_safe_list_defaults_no_reason_and_completed_only_reason(self):
        plan, s = self.retry()
        dto = self.ok("/api/v2/retries")
        self.no_past(dto)
        self.assertEqual(dto["active"][0]["defaults"], dict(mode="training", duration=40, resources="none"))
        self.assertEqual(dto["active"][0]["session_id"], s["id"])
        self.finish(s)
        comparison = self.compare(s)
        self.assertEqual(comparison["state"], "ready")
        self.assertEqual(comparison["reason"], SENTINELS["reason"])
        self.assertEqual(comparison["source_sid"], self.f.source["id"])
        for forbidden in ("codes", "report", "media", "messages"):
            self.assertNotIn(forbidden, comparison["source"])
        self.assertEqual(self.f.sql("SELECT due_date FROM retry_items WHERE plan_id=? ORDER BY offset_days", (plan["id"],)),
                         [("2026-10-02",), ("2026-10-04",), ("2026-10-08",)])

    def test_v5_raw_original_state_events_media_and_bytes_unchanged(self):
        before = self.f.raw_snapshot(self.f.source["id"])
        plan, s = self.retry()
        self.assertEqual(before, self.f.raw_snapshot(self.f.source["id"]))
        self.finish(s)
        self.compare(s)
        self.start(plan["items"][0])
        self.assertEqual(before, self.f.raw_snapshot(self.f.source["id"]))
        self.assertEqual(self.ok("/api/v2/session/" + self.f.source["id"])["codes"][PID], SENTINELS["code"])

    def test_v5_catalog_changed_deleted_snapshot_hidden_tests_and_conditions_retained(self):
        plan = self.register()["plan"]
        self.due(plan)
        altered = copy.deepcopy(self.f.source["problems"][0])
        altered["starter"] = "catalog replacement"
        altered["tests"][1]["expected"] = 9999
        self.f.sql("UPDATE problems SET payload=? WHERE id=?", (dumps(altered), PID))
        self.f.sql("DELETE FROM problems WHERE id=?", (PID,))
        s = self.start(plan["items"][0])["session"]
        stored = self.f.st.session(s["id"])
        self.assertEqual(stored["problems"], self.f.source["problems"])
        self.assertEqual(stored["codes"], {PID: STARTER})
        self.ok("/api/v2/run", dict(sid=s["id"], pid=PID, submit=True))
        self.f.settle()
        self.assertEqual(self.f.java_calls[-1]["problem"]["tests"], self.f.source["problems"][0]["tests"])

    def test_v5_missing_or_corrupt_original_is_unavailable_without_catalog_fallback(self):
        plan = self.register()["plan"]
        self.due(plan)
        self.f.sql("DELETE FROM sessions WHERE id=?", (self.f.source["id"],))
        dto = self.ok("/api/v2/retries")
        self.assertEqual(dto["unavailable"][0]["code"], "SOURCE_UNAVAILABLE")
        self.assertFalse(dto["unavailable"][0]["can_start"])
        self.error("/api/v2/retry-start", "SOURCE_UNAVAILABLE", dict(item_id=plan["items"][0]["id"],
                   mode="training", kind="single", duration=40, resources="none"))
        self.assertEqual(self.f.sql("SELECT COUNT(*) FROM retry_items")[0][0], 3)
        self.assertEqual(self.f.sql("SELECT COUNT(*) FROM sessions")[0][0], 0)

    def test_v5_source_loss_after_retry_finish_preserves_current_comparison(self):
        _, s = self.retry()
        self.finish(s)
        self.f.sql("DELETE FROM sessions WHERE id=?", (self.f.source["id"],))
        dto = self.compare(s)
        self.assertEqual((dto["state"], dto["code"]), ("unavailable", "SOURCE_UNAVAILABLE"))
        self.assertEqual(dto["current"]["outcome"], "not_submitted")

    def test_v5_corrupt_source_is_quarantined_without_hiding_healthy_plan(self):
        broken_plan = self.register()["plan"]
        healthy = self.f.seed(pid="healthy-plan")
        healthy_plan = self.register(healthy)["plan"]
        self.due(broken_plan)
        self.f.sql("UPDATE sessions SET state='not JSON' WHERE id=?", (self.f.source["id"],))
        before = self.f.raw_snapshot()
        plans_before = self.f.sql("SELECT * FROM retry_plans ORDER BY id")
        items_before = self.f.sql("SELECT * FROM retry_items ORDER BY id")
        dto = self.ok("/api/v2/retries")
        self.assertEqual(dto["unavailable"][0]["plan_id"], broken_plan["id"])
        self.assertEqual(dto["due"][0]["plan_id"], healthy_plan["id"])
        self.assertEqual(before, self.f.raw_snapshot())
        self.assertEqual(plans_before, self.f.sql("SELECT * FROM retry_plans ORDER BY id"))
        self.assertEqual(items_before, self.f.sql("SELECT * FROM retry_items ORDER BY id"))

    def test_v6_eligibility_failed_no_submit_examples_overtime_and_errors(self):
        self.ok("/api/v2/retries")
        cases = [([( "failed", 2400000, True, 0)], None),
                 ([], None), ([("passed", 1000, False, 0)], None),
                 ([("failed", 1000, True, 0), ("passed", 2400001, True, 1)], None),
                 ([("passed", 2400000, True, 0)], "NOT_ELIGIBLE"),
                 ([("error", 1000, True, 0)], "GRADE_UNAVAILABLE")]
        for runs, expected in cases:
            with self.subTest(runs=runs):
                source = self.f.seed(pid="eligible-" + uuid.uuid4().hex, runs=runs)
                view = self.ok("/api/v2/session/" + source["id"])
                candidate = next(c for c in view["retry_candidates"] if c["pid"] == source["problems"][0]["id"])
                self.assertEqual(candidate["eligible"], expected is None)
                d = dict(source_sid=source["id"], pid=source["problems"][0]["id"], reason="why", timezone="Etc/UTC")
                if expected:
                    self.assertEqual(candidate["code"], expected)
                    self.error("/api/v2/retry-plan", expected, d)
                else:
                    self.ok("/api/v2/retry-plan", d)

    def test_v6_submit_admission_order_not_completion_order_and_same_ms_tie(self):
        # Completion array ends in an earlier admitted PASS; last admission FAIL wins.
        source = self.f.seed(pid="ordering", runs=[("failed", 1000, True, 1), ("passed", 1000, True, 0)])
        _, s = self.retry(source)
        self.finish(s)
        dto = self.compare(s)
        self.assertEqual(dto["source"]["outcome"], "failed")
        self.assertEqual(dto["source"]["run_id"], source["runs"][0]["id"])
        self.assertTrue(dto["source"]["result_basis"])
        other = self.f.seed(pid="ordering-pass", runs=[("passed", 1000, True, 1), ("failed", 1000, True, 0)])
        self.error("/api/v2/retry-plan", "NOT_ELIGIBLE", dict(source_sid=other["id"], pid="ordering-pass", reason="why", timezone="Etc/UTC"))

    def test_v6_legacy_without_submit_events_falls_back_and_explains_basis(self):
        source = self.f.seed(pid="legacy-order", emit_events=False,
                             runs=[("passed", 1000, True, 0), ("failed", 1000, True, 1)])
        _, s = self.retry(source)
        self.finish(s)
        dto = self.compare(s)
        self.assertEqual(dto["source"]["outcome"], "failed")
        self.assertEqual(dto["source"]["run_id"], source["runs"][1]["id"])
        self.assertTrue(dto["source"]["result_basis"], "Legacy fallback basis must be explicit")

    def test_v6_multi_problem_wall_time_rounding_overtime_and_condition_differences(self):
        source = self.f.seed(pid="multi", multi=True, elapsed_ms=45 * 60000,
                             runs=[("failed", 1200000, True, 0), ("passed", 2500000, True, 1)])
        self.f.st.event(source["id"], "switch", dict(to="multi-other"), "multi", ms=402000)
        self.f.st.event(source["id"], "switch", dict(to="multi"), "multi-other", ms=1032000)
        _, s = self.retry(source, duration=30, resources="search")
        self.finish(s, elapsed=123600)
        dto = self.compare(s)
        self.assertEqual(dto["source"]["minutes"], 34.5)
        self.assertEqual(dto["current"]["minutes"], 2.1)
        self.assertEqual(dto["delta_minutes"], -32.4)
        self.assertEqual(dto["source"]["outcome"], "failed")
        self.assertEqual(dto["source"]["overtime_outcome"], "passed")
        self.assertEqual(dto["current"]["outcome"], "not_submitted")
        self.assertEqual((dto["source"]["hints"], dto["current"]["hints"]), (1, 0))
        self.assertTrue(dto["conditions_differ"])
        self.assertEqual(dto["time_basis"], "문제 선택 구간의 경과 시간(탭을 닫은 시간 포함)")
        self.assertTrue(dto["limitations"])

    def test_v6_comparison_refresh_recomputes_late_switch_without_zero_fallback(self):
        source = self.f.seed(pid="late-switch", multi=True, elapsed_ms=20 * 60000)
        _, s = self.retry(source)
        self.finish(s)
        first = self.compare(s)
        self.assertEqual(first["source"]["minutes"], 20.0)
        self.f.st.event(source["id"], "switch", dict(to="late-switch-other"), "late-switch", ms=600000)
        self.f.now_ms += 1000
        second = self.compare(s)
        self.assertEqual(second["source"]["minutes"], 10.0)
        self.assertGreater(second["computed_at"], first["computed_at"])

    def test_v6_general_session_comparison_returns_not_retry(self):
        self.ok("/api/v2/retries")
        self.error("/api/v2/retry-comparison/" + self.f.source["id"], "NOT_RETRY")

    def test_v6_incomplete_minutes_are_null_and_delta_not_zero(self):
        _, s = self.retry()
        self.finish(s)
        normal_metrics = self.f.lab.metrics

        def incomplete_source_minutes(attempt):
            # Inject incomplete output at the existing metrics boundary. This
            # verifies the comparison consumer without copying its calculations.
            values = normal_metrics(attempt)
            if attempt["id"] == self.f.source["id"]:
                values = [dict(value, minutes=None) for value in values]
            return values

        self.f.lab.metrics = incomplete_source_minutes
        dto = self.compare(s)
        self.assertEqual(dto["state"], "ready")
        self.assertIsNone(dto["source"]["minutes"])
        self.assertEqual(dto["current"]["minutes"], 2.0)
        self.assertIsNone(dto["delta_minutes"])

    def test_v6_current_example_pass_not_submission_and_grader_error_not_failed(self):
        for submit, marker, expected in ((False, "passed", "not_submitted"), (True, "error", "grader_error")):
            with self.subTest(submit=submit, marker=marker):
                source = self.f.seed(pid="current-outcome-" + uuid.uuid4().hex)
                _, s = self.retry(source)
                pid = source["problems"][0]["id"]
                self.f.now_ms += 1000
                self.ok("/api/v2/events", dict(sid=s["id"], events=[dict(id=uuid.uuid4().hex, kind="code", pid=pid,
                        ms=1000, payload=dict(code=STARTER + " /*fixture:" + marker + "*/"))]))
                self.ok("/api/v2/run", dict(sid=s["id"], pid=pid, submit=submit))
                self.f.settle()
                self.finish(s)
                self.assertEqual(self.compare(s)["current"]["outcome"], expected)

    def test_v7_active_or_pending_original_not_registered_ungraded_not_failed(self):
        self.ok("/api/v2/retries")
        active = self.f.seed(pid="active-source", status="active")
        self.error("/api/v2/retry-plan", "SOURCE_ACTIVE", dict(source_sid=active["id"], pid="active-source", reason="why", timezone="Etc/UTC"))
        source = self.f.seed(runs=[])
        self.f.pending_submission(source["id"])
        d = dict(source_sid=source["id"], pid=PID, reason="why", timezone="Etc/UTC")
        self.error("/api/v2/retry-plan", "GRADING_PENDING", d)
        self.f.restart()
        self.error("/api/v2/retry-plan", "GRADE_UNAVAILABLE", d)

    def test_v7_delayed_java_finish_grading_then_ready_blocks_next_item(self):
        plan, s = self.retry()
        self.f.java_gate.arm()
        try:
            self.ok("/api/v2/run", dict(sid=s["id"], pid=PID, submit=True))
            self.assertTrue(self.f.java_gate.entered.wait(3))
            self.finish(s)
            self.assertEqual(self.compare(s)["state"], "grading")
            self.assertEqual(self.ok("/api/v2/retries")["grading"][0]["session_id"], s["id"])
            self.error("/api/v2/retry-start", "PREVIOUS_PENDING", dict(item_id=plan["items"][1]["id"],
                       kind="single", mode="training", duration=40, resources="none"))
        finally:
            self.f.java_gate.open()
        self.f.settle()
        dto = self.compare(s)
        self.assertEqual((dto["state"], dto["current"]["outcome"]), ("ready", "failed"))
        self.assertTrue(self.start(plan["items"][1])["created"])

    def test_v7_run_admission_finish_race_never_publishes_premature_ready(self):
        _, s = self.retry()
        self.f.admission_gate.arm()
        self.f.java_gate.arm()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        try:
            run = pool.submit(self.f.request, "/api/v2/run", dict(sid=s["id"], pid=PID, submit=True))
            self.assertTrue(self.f.admission_gate.entered.wait(3))
            finish = pool.submit(self.f.request, "/api/v2/finish", dict(sid=s["id"]))
            self.assertTrue(self.f.finish_entered.wait(3))
            try:
                end_status, _ = finish.result(timeout=0.25)
            except concurrent.futures.TimeoutError:
                end_status = None  # Expected lifecycle lock has serialized finish.
            if end_status is not None:
                status, dto = self.f.request("/api/v2/retry-comparison/" + s["id"])
                self.assertFalse(status == 200 and dto.get("state") == "ready",
                                 "Finish finalized while admitted Java job had not been inserted")
            self.f.admission_gate.open()
            self.assertEqual(run.result(4)[0], 200)
            self.assertTrue(self.f.java_gate.entered.wait(3))
            self.assertEqual(finish.result(4)[0], 200)
            self.assertEqual(self.compare(s)["state"], "grading")
        finally:
            self.f.admission_gate.open()
            self.f.java_gate.open()
            pool.shutdown(wait=True)
        self.f.settle()
        self.assertEqual(self.compare(s)["state"], "ready")

    def test_v7_exam_expiry_forced_java_and_no_recursive_deadlock(self):
        _, s = self.retry(mode="exam", duration=5)
        self.f.java_gate.arm()
        try:
            self.f.now_ms += 300000
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                expired = pool.submit(self.f.request, "/api/v2/session/" + s["id"])
                status, current = expired.result(timeout=4)
            self.assertEqual(status, 200, current)
            self.assertEqual(current["status"], "completed")
            self.assertTrue(self.f.java_gate.entered.wait(3))
            self.assertEqual(self.compare(s)["state"], "grading")
        finally:
            self.f.java_gate.open()
        self.f.settle()
        current = self.ok("/api/v2/session/" + s["id"])
        self.assertEqual(len(current["runs"]), 1)
        self.assertTrue(current["runs"][0]["automatic"])
        self.assertFalse(current["runs"][0]["overtime"])
        self.assertEqual(current["runs"][0]["ms"], 300000)
        self.assertEqual(self.compare(s)["current"]["outcome"], "failed")

    def test_v7_training_overtime_pass_stays_separate_and_remaining_days_kept(self):
        plan, s = self.retry(duration=5)
        self.f.now_ms += 300001
        current = self.ok("/api/v2/session/" + s["id"])
        self.assertEqual(current["status"], "active")
        self.ok("/api/v2/events", dict(sid=s["id"], events=[dict(id=uuid.uuid4().hex, kind="code", pid=PID,
                        ms=300001, payload=dict(code=STARTER + " /*fixture:passed*/"))]))
        self.ok("/api/v2/run", dict(sid=s["id"], pid=PID, submit=True))
        self.f.settle()
        self.finish(s, elapsed=0)
        dto = self.compare(s)
        self.assertEqual((dto["current"]["outcome"], dto["current"]["overtime_outcome"]), ("not_submitted", "passed"))
        self.assertEqual(self.f.sql("SELECT COUNT(*) FROM retry_items WHERE plan_id=?", (plan["id"],))[0][0], 3)
        self.assertEqual(self.f.sql("SELECT status FROM retry_plans WHERE id=?", (plan["id"],))[0][0], "open")

    def test_v7_restart_failed_java_ungraded_and_non_java_jobs_do_not_block(self):
        _, s = self.retry()
        jid = self.f.pending_submission(s["id"])
        self.finish(s)
        self.assertEqual(self.compare(s)["state"], "grading")
        self.f.restart()
        dto = self.compare(s)
        self.assertEqual((dto["state"], dto["current"]["outcome"]), ("ready", "ungraded"))
        self.assertEqual(self.f.st.session(s["id"])["status"], "completed")
        self.assertEqual(self.f.lab.get_job(jid)["status"], "failed")
        job = dict(id=uuid.uuid4().hex, sid=s["id"], kind="codex", status="running", created=self.f.now_ms,
                   text="", error=None, result=None)
        self.f.sql("INSERT INTO jobs VALUES (?,?,?)", (job["id"], s["id"], dumps(job)))
        self.assertEqual(self.compare(s)["state"], "ready")

    def test_v7_overdue_order_same_day_all_three_complete_close_no_auto_chain(self):
        plan, s = self.retry()
        for index, item in enumerate(plan["items"]):
            if index:
                s = self.start(item)["session"]
            dto = self.ok("/api/v2/retries")
            self.assertEqual(dto["active"][0]["outstanding_due_count"], 3 - index)
            self.finish(s, elapsed=1000)
            self.assertEqual(self.compare(s)["source_sid"], self.f.source["id"])
        self.assertEqual(self.f.sql("SELECT status FROM retry_plans WHERE id=?", (plan["id"],))[0][0], "completed")
        self.assertEqual(self.f.sql("SELECT COUNT(*) FROM retry_items WHERE completed_at IS NOT NULL")[0][0], 3)
        self.assertEqual(self.ok("/api/v2/retries")["due"], [])
        self.assertEqual(self.f.sql("SELECT COUNT(*) FROM retry_plans")[0][0], 1)
        new_source = self.f.seed()
        self.assertTrue(self.register(new_source)["created"])

    def test_v8_original_retry_two_segments_gap_and_late_source_upload_separate(self):
        _, s = self.retry()
        self.f.now_ms += 7000
        for segment, start, end in (("c" * 32, 0, 1000), ("d" * 32, 5000, 6000)):
            q = urlencode(dict(sid=s["id"], kind="screen", segment=segment, seq=0, start=start, end=end, mime="video/webm"))
            self.ok("/api/v2/upload?" + q, raw=b"P3 retry fake bytes")
            events = [dict(id=uuid.uuid4().hex, kind="recording_start", ms=start, pid=PID, payload=dict(kind="screen", segment=segment)),
                      dict(id=uuid.uuid4().hex, kind="recording_stop", ms=end, pid=PID,
                           payload=dict(kind="screen", segment=segment, chunks=1, state="normal"))]
            self.ok("/api/v2/events", dict(sid=s["id"], events=events))
        self.finish(s, elapsed=0)
        before_retry = self.f.raw_snapshot(s["id"])
        q = urlencode(dict(sid=self.f.source["id"], kind="screen", segment="a" * 32, seq=1, start=0, mime="video/webm"))
        self.ok("/api/v2/upload?" + q, raw=b"P3 old outbox tail")
        self.assertEqual(before_retry, self.f.raw_snapshot(s["id"]))
        for sid in (self.f.source["id"], s["id"]):
            view = self.ok("/api/v2/session/" + sid)
            self.assertEqual(len(view["media"]), 2)
            self.assertTrue(view["recording_gaps"])
        self.assertEqual({m[0] for m in self.f.sql("SELECT DISTINCT segment FROM media WHERE sid=?", (s["id"],))}, {"c" * 32, "d" * 32})

    def test_v8_zero_chunk_stop_is_nonplayable_missing_coverage_not_an_upload(self):
        _, s = self.retry()
        segment = "f" * 32
        events = [dict(id=uuid.uuid4().hex, kind="recording_start", ms=0, pid=PID,
                       payload=dict(kind="screen", segment=segment, offset_ms=0)),
                  dict(id=uuid.uuid4().hex, kind="recording_stop", ms=10, pid=PID,
                       payload=dict(kind="screen", segment=segment, chunks=0, state="interrupted", end_ms=10))]
        self.ok("/api/v2/events", dict(sid=s["id"], events=events))
        self.f.now_ms += 1000
        view = self.ok("/api/v2/session/" + s["id"])
        group = next(m for m in view["media"] if m["segment"] == segment)
        self.assertEqual((group["bytes"], group["chunks"], group["expected_chunks"]), (0, 0, 0))
        self.assertFalse(group["playable"])
        self.assertIsNone(group["url"])
        self.assertTrue(view["recording_gaps"])
        self.assertEqual(self.f.sql("SELECT * FROM media WHERE sid=?", (s["id"],)), [])
        self.assertEqual(self.f.upload_proof(), [])

    def test_review_r1_001_incomplete_snapshot_rejected_without_orphan_active(self):
        plan = self.register()["plan"]
        corrupt = copy.deepcopy(self.f.source)
        del corrupt["problems"][0]["tests"][0]["public"]
        self.f.sql("UPDATE sessions SET state=? WHERE id=?", (dumps(corrupt), corrupt["id"]))
        before = self.f.raw_snapshot(corrupt["id"])
        sessions = self.f.sql("SELECT id FROM sessions ORDER BY id")
        item = self.due(plan)
        self.error("/api/v2/retry-start", "SOURCE_UNAVAILABLE", dict(item_id=item["id"],
                   kind="single", mode="training", duration=40, resources="none"))
        self.assertEqual(self.f.sql("SELECT id FROM sessions ORDER BY id"), sessions)
        self.assertIsNone(self.f.sql("SELECT session_id FROM retry_items WHERE id=?", (item["id"],))[0][0])
        self.assertEqual(before, self.f.raw_snapshot(corrupt["id"]))
        self.assertEqual(self.ok("/api/v2/retries")["active"], [])

    def test_review_r1_002_source_switch_corruption_preserves_current_comparison(self):
        _, s = self.retry()
        self.finish(s)
        self.f.sql("INSERT INTO events VALUES (?,?,?,?,?,?)", (self.f.source["id"], uuid.uuid4().hex,
                    10, "switch", PID, "[]"))
        before = self.f.raw_snapshot(self.f.source["id"])
        dto = self.compare(s)
        self.assertEqual(dto["current"]["minutes"], 2.0)
        self.assertEqual(dto["current"]["outcome"], "not_submitted")
        self.assertTrue(dto["state"] == "unavailable" or dto.get("source", {}).get("minutes") is None)
        if dto["state"] == "unavailable":
            self.assertEqual(dto["code"], "SOURCE_UNAVAILABLE")
        self.assertEqual(before, self.f.raw_snapshot(self.f.source["id"]))
        self.assertEqual(self.ok("/api/v2/session/" + s["id"])["status"], "completed")

    def test_v10_http_backup_restore_retry_relationships_and_media_bytes(self):
        plan, s = self.retry()
        self.finish(s)
        archive = self.ok("/api/v2/backup")
        self.assertIsInstance(archive, bytes)
        from restore import restore
        with tempfile.TemporaryDirectory(prefix="p3-retry-restore-") as temp:
            root = Path(temp)
            file = root / "backup.zip"
            file.write_bytes(archive)
            restore(file, root / "restored")
            with closing(sqlite3.connect(root / "restored/lab.sqlite3")) as con:
                for table in ("retry_plans", "retry_items", "sessions", "events", "media"):
                    self.assertEqual(con.execute("SELECT * FROM " + table + " ORDER BY rowid").fetchall(),
                                     self.f.sql("SELECT * FROM " + table + " ORDER BY rowid"), table)
                self.assertEqual(con.execute("SELECT source_sid FROM retry_plans WHERE id=?", (plan["id"],)).fetchone()[0], self.f.source["id"])
                self.assertEqual(con.execute("SELECT session_id FROM retry_items WHERE id=?", (plan["items"][0]["id"],)).fetchone()[0], s["id"])
            for relative in self.f.raw_snapshot()["media_hashes"]:
                self.assertEqual((root / "restored" / relative).read_bytes(), (self.f.data / relative).read_bytes())

    def test_v10_legacy_archive_restore_remains_compatible(self):
        from restore import restore
        with tempfile.TemporaryDirectory(prefix="p3-legacy-restore-") as temp:
            root = Path(temp)
            db = root / "old.sqlite3"
            with closing(sqlite3.connect(db)) as con, con:
                con.executescript(LEGACY_DDL)
                con.execute("INSERT INTO sessions VALUES (?,?,?)", (self.f.source["id"], self.f.source["started"], dumps(self.f.source)))
            archive = root / "old.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.write(db, "lab.sqlite3")
            restore(archive, root / "restored")
            with closing(sqlite3.connect(root / "restored/lab.sqlite3")) as con:
                self.assertEqual(json.loads(con.execute("SELECT state FROM sessions").fetchone()[0]), self.f.source)
                self.assertNotIn("retry_plans", {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")})


class RetryAnchorDateContractTest(unittest.TestCase):
    """Contract B1..B5 through unchanged HTTP handlers and an actual temp DB.

    These tests deliberately FAIL on the pinned v1 product. No fake product
    guard, expectedFailure, skips, schema rewrite or direct retry helper calls.
    Transport observations are written before assertions, including unexpected
    successful writes, so a missing guard cannot hide its side effects.
    """
    ok = RetryContractTest.ok
    register = RetryContractTest.register

    def setUp(self):
        self.f = RetryFixture().__enter__()
        self.observations = []
        self.addCleanup(self.f.close)
        self.addCleanup(self.write_observations)

    def write_observations(self):
        folder = os.environ.get("RETRY_ANCHOR_OUTPUT")
        if folder:
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            (target / (self._testMethodName + ".json")).write_text(dumps(dict(
                test=self.id(), contract="retry-v1.1-anchor-date", observations=self.observations,
                actual_http=True, actual_sqlite=True, product_helpers_replaced=False,
                external_ai_calls=0)) + "\n")

    def body(self, source=None, **changes):
        source = source or self.f.source
        return dict(source_sid=source["id"], pid=source["problems"][0]["id"],
                    reason="F3 anchor contract", timezone="Asia/Seoul",
                    expected_anchor_date="2026-10-01") | changes

    def rows(self):
        return {table: self.f.sql("SELECT * FROM " + table + " ORDER BY id")
                for table in ("retry_plans", "retry_items")}

    def source_hash(self, sid):
        return hashlib.sha256(dumps(self.f.raw_snapshot(sid)).encode()).hexdigest()

    def before(self, source=None):
        return dict(rows=self.rows(), source_hash=self.source_hash((source or self.f.source)["id"]))

    def observe(self, gate, body, response, before, source=None, **details):
        after = self.before(source)
        self.observations.append(dict(gate=gate, status=response[0], response=response[1],
                                      source_sid=body["source_sid"], request_timezone=body.get("timezone"),
                                      expected_present="expected_anchor_date" in body,
                                      expected_anchor_date=body.get("expected_anchor_date"),
                                      before_counts={k: len(v) for k, v in before["rows"].items()},
                                      after_counts={k: len(v) for k, v in after["rows"].items()},
                                      retry_rows_unchanged=before["rows"] == after["rows"],
                                      source_hash_before=before["source_hash"], source_hash_after=after["source_hash"],
                                      **details))
        return after

    def rejected(self, gate, body, response, before, code, source=None):
        after = self.observe(gate, body, response, before, source)
        status, dto = response
        for field, actual, expected in (("HTTP", status, 400), ("code", dto.get("code"), code),
                                        ("rows", after["rows"], before["rows"]),
                                        ("source/media", after["source_hash"], before["source_hash"])):
            with self.subTest(gate=gate, assertion=field):
                self.assertEqual(actual, expected)
        with self.subTest(gate=gate, assertion="error message"):
            self.assertTrue(dto.get("error"), dto)
        return dto

    def mismatch(self, gate, body, response, before, current, preview, source=None):
        dto = self.rejected(gate, body, response, before, "ANCHOR_DATE_CHANGED", source)
        for field, expected in dict(expected_anchor_date=body["expected_anchor_date"],
                                    current_anchor_date=current, timezone=body["timezone"],
                                    server_now=self.f.now_ms,
                                    preview=[dict(offset_days=n, due_date=d) for n, d in zip((1, 3, 7), preview)]).items():
            with self.subTest(gate=gate, assertion=field):
                self.assertEqual(dto.get(field), expected)
        if isinstance(dto.get("preview"), list):
            for item in dto["preview"]:
                self.assertEqual(set(item), {"offset_days", "due_date"})
        self.assertNotIn("plan", dto)

    def saved_calendar(self, plan, anchor, dates, now, timezone):
        self.assertEqual(plan["anchor_date"], anchor)
        self.assertEqual(plan["timezone"], timezone)
        self.assertEqual(plan["created_at"], now)
        self.assertEqual(datetime.fromtimestamp(plan["created_at"] / 1000, ZoneInfo(timezone)).date().isoformat(), anchor)
        self.assertEqual([(i["offset_days"], i["due_date"]) for i in plan["items"]], list(zip((1, 3, 7), dates)))
        self.assertNotIn("expected_anchor_date", plan, "Request preconditions are not persistent plan fields")

    @staticmethod
    def identity(plan):
        return {k: plan[k] for k in ("id", "source_sid", "pid", "reason", "timezone", "anchor_date", "created_at")} | {
            "items": [{k: i[k] for k in ("id", "offset_days", "due_date", "session_id")} for i in plan["items"]]}

    def test_v2_ad1_matching_expected_and_additive_schema(self):
        self.f.now_ms = epoch("2026-10-01T14:59:59Z")
        schema = self.f.sql("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name")
        body, before = self.body(), self.before()
        response = self.f.request("/api/v2/retry-plan", body)
        self.observe("V2-AD1", body, response, before)
        self.assertEqual(response[0], 200, response[1])
        self.assertIs(response[1]["created"], True)
        self.saved_calendar(response[1]["plan"], "2026-10-01", ["2026-10-02", "2026-10-04", "2026-10-08"], self.f.now_ms, "Asia/Seoul")
        self.assertEqual(self.f.sql("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"), schema)
        self.assertEqual(self.source_hash(self.f.source["id"]), before["source_hash"])

    def test_v2_ad1_capability_is_exact_true_with_legacy_envelope(self):
        dto = self.ok("/api/v2/retries")
        self.observations.append(dict(gate="V2-AD1/B1-capability", response=dto))
        self.assertTrue({"server_now", "next_refresh_at", "due", "active", "grading", "unavailable", "upcoming"} <= set(dto))
        self.assertIs(dto.get("capabilities", {}).get("retry_plan_expected_anchor_date"), True)
        self.assertEqual(dto["server_now"], self.f.now_ms)

    def test_v2_ad2_preview_then_post_after_seoul_midnight_rolls_back(self):
        self.f.now_ms = epoch("2026-10-01T14:59:59Z")
        preview_clock = self.ok("/api/v2/retries")["server_now"]
        body, before = self.body(), self.before()
        self.f.now_ms += 2000
        response = self.f.request("/api/v2/retry-plan", body)
        self.observations.append(dict(gate="V2-AD2-clock", preview_get_now=preview_clock, post_now=self.f.now_ms))
        self.mismatch("V2-AD2", body, response, before, "2026-10-02", ["2026-10-03", "2026-10-05", "2026-10-09"])

    def test_v2_ad2_reconcile_changes_also_rollback_on_stale_date(self):
        old = self.register()["plan"]
        # Synthetic completed linked sessions, with intentionally stale caches.
        # A failed new registration must not commit reconciliation of this plan.
        for item in old["items"]:
            completed = self.f.seed()
            completed["retry"] = dict(plan_id=old["id"], item_id=item["id"], source_sid=old["source_sid"], pid=PID)
            self.f.sql("UPDATE sessions SET state=? WHERE id=?", (dumps(completed), completed["id"]))
            self.f.sql("UPDATE retry_items SET session_id=? WHERE id=?", (completed["id"], item["id"]))
        new = self.f.seed()
        self.f.now_ms = epoch("2026-10-01T15:00:01Z")
        body, before = self.body(new), self.before(new)
        response = self.f.request("/api/v2/retry-plan", body)
        self.mismatch("V2-AD2-reconcile-rollback", body, response, before, "2026-10-02", ["2026-10-03", "2026-10-05", "2026-10-09"], new)

    def test_v2_ad3_actual_write_lock_wait_crosses_midnight(self):
        self.f.now_ms = epoch("2026-10-01T14:59:59Z")
        body, before = self.body(), self.before()
        holder = sqlite3.connect(self.f.data / "lab.sqlite3", timeout=5)
        holder.execute("BEGIN IMMEDIATE")
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            with self.f.observe_sqlite() as probe:
                waiting = pool.submit(self.f.request, "/api/v2/retry-plan", body)
                self.assertTrue(probe.write_attempt.wait(3), "Real HTTP registration did not attempt BEGIN IMMEDIATE")
                self.assertFalse(waiting.done(), "Registration did not wait for the real SQLite writer")
                self.f.now_ms += 2000
                holder.commit()
                response = waiting.result(timeout=5)
                self.observations.append(dict(gate="V2-AD3-lock-proof", **probe.proof()))
        finally:
            holder.rollback()
            holder.close()
            pool.shutdown(wait=True)
        self.mismatch("V2-AD3", body, response, before, "2026-10-02", ["2026-10-03", "2026-10-05", "2026-10-09"])
        reads = [e for e in probe.proof()["events"] if e["event"] == "server_clock_read" and e["phase"] == "write_lock_held"]
        self.assertTrue(reads, "No authoritative server clock read after the actual write lock")
        self.assertEqual(reads[-1]["now"], self.f.now_ms)

    def test_v2_ad4_guard_clock_is_reused_when_insert_crosses_midnight(self):
        self.f.now_ms = epoch("2026-10-01T14:59:59Z")
        guard_now = self.f.now_ms
        body, before = self.body(), self.before()
        with self.f.observe_sqlite(advance_at_plan_insert=2000) as probe:
            response = self.f.request("/api/v2/retry-plan", body)
        self.observe("V2-AD4", body, response, before, sqlite=probe.proof())
        self.assertEqual(probe.insert_clock_moves, 1, "Test did not move the clock before the actual INSERT")
        self.assertEqual(self.f.now_ms, guard_now + 2000)
        self.assertEqual(response[0], 200, response[1])
        self.saved_calendar(response[1]["plan"], "2026-10-01", ["2026-10-02", "2026-10-04", "2026-10-08"], guard_now, "Asia/Seoul")
        events = probe.proof()["events"]
        insert = next(e for e in events if e["event"] == "before_plan_insert")
        reads = [e for e in events if e["event"] == "server_clock_read" and e["phase"] == "write_lock_held" and e["index"] < insert["index"]]
        self.assertEqual(len(reads), 1, "Registration must use one guard clock observation inside its write transaction")
        self.assertEqual(reads[0]["now"], guard_now)
        self.assertEqual(self.source_hash(self.f.source["id"]), before["source_hash"])

    def test_v2_ad5_legacy_omission_still_creates(self):
        body, before = self.body(), self.before()
        del body["expected_anchor_date"]
        response = self.f.request("/api/v2/retry-plan", body)
        self.observe("V2-AD5-legacy", body, response, before)
        self.assertEqual(response[0], 200, response[1])
        self.assertIs(response[1]["created"], True)
        self.saved_calendar(response[1]["plan"], "2026-10-01", ["2026-10-02", "2026-10-04", "2026-10-08"], DEFAULT_NOW, "Asia/Seoul")

    def test_v2_ad5_explicit_malformed_values_cannot_be_legacy(self):
        invalid = [None, 20261001, True, [], {}, "", " 2026-10-01", "2026-10-01 ",
                   "2026-1-01", "2026-10-1", "２０２６-１０-０１", "2026-10-01T00:00:00Z",
                   "2026-02-29", "2026-13-01", "0000-01-01", "10000-01-01"]
        for index, expected in enumerate(invalid):
            with self.subTest(case=index, expected=expected):
                source = self.f.seed(pid="fixture-invalid-anchor-" + str(index))
                body, before = self.body(source, expected_anchor_date=expected), self.before(source)
                self.rejected("V2-AD5-malformed", body, self.f.request("/api/v2/retry-plan", body), before, "INVALID_INPUT", source)

    def test_v2_ad5_timezone_required_even_on_duplicate(self):
        self.register()
        for value in (None, "Mars/Olympus", "", 123, " Asia/Seoul", "__missing__"):
            with self.subTest(timezone=value):
                body, before = self.body(timezone=value), self.before()
                if value == "__missing__":
                    del body["timezone"]
                self.rejected("V2-AD5-timezone", body, self.f.request("/api/v2/retry-plan", body), before, "INVALID_INPUT")

    def test_v2_ad5_valid_past_future_and_boundary_years_are_mismatches(self):
        for index, expected in enumerate(("2026-09-30", "2026-10-02", "0001-01-01", "9999-12-31")):
            with self.subTest(expected=expected):
                source = self.f.seed(pid="fixture-valid-stale-" + str(index))
                body, before = self.body(source, expected_anchor_date=expected), self.before(source)
                self.mismatch("V2-AD5-valid-stale", body, self.f.request("/api/v2/retry-plan", body), before,
                              "2026-10-01", ["2026-10-02", "2026-10-04", "2026-10-08"], source)

    def test_v2_ad6_request_timezone_dst_and_client_query_are_authoritative(self):
        cases = [("2026-10-01T15:00:01Z", "Etc/UTC", "2026-10-01", ["2026-10-02", "2026-10-04", "2026-10-08"], None),
                 ("2026-10-01T15:00:01Z", "Asia/Seoul", "2026-10-02", ["2026-10-03", "2026-10-05", "2026-10-09"], None),
                 ("2026-03-08T05:00:00Z", "America/New_York", "2026-03-08", ["2026-03-09", "2026-03-11", "2026-03-15"], 23),
                 ("2026-11-01T04:00:00Z", "America/New_York", "2026-11-01", ["2026-11-02", "2026-11-04", "2026-11-08"], 25)]
        for index, (now, zone, anchor, dates, hours) in enumerate(cases):
            with self.subTest(timezone=zone, now=now):
                # next_refresh_at is the earliest across all plans. Each zone
                # scenario has its own synthetic plans, as in the v1 DST test.
                self.f.sql("DELETE FROM retry_items")
                self.f.sql("DELETE FROM retry_plans")
                self.f.now_ms = epoch(now)
                source = self.f.seed(pid="fixture-zone-anchor-" + str(index))
                body, before = self.body(source, timezone=zone, expected_anchor_date=anchor), self.before(source)
                response = self.f.request("/api/v2/retry-plan", body)
                self.observe("V2-AD6", body, response, before, source)
                self.assertEqual(response[0], 200, response[1])
                self.saved_calendar(response[1]["plan"], anchor, dates, self.f.now_ms, zone)
                if hours:
                    dto = self.ok("/api/v2/retries?today=2042-12-31&timezone=Etc%2FUTC")
                    self.assertEqual(dto["server_now"], self.f.now_ms)
                    self.assertEqual(dto["next_refresh_at"] - dto["server_now"], hours * 3600000)

    def test_v3_ad1_lost_success_replay_across_midnight_keeps_ids_and_dates(self):
        self.f.now_ms = epoch("2026-10-01T14:59:59Z")
        body, before = self.body(), self.before()
        # Actual first HTTP save, but discard its response as a caller would on
        # transport loss; verify the committed result from independent SQL.
        first = self.f.request("/api/v2/retry-plan", body)
        self.assertEqual(first[0], 200, first[1])
        plan_id = self.f.sql("SELECT id FROM retry_plans WHERE source_sid=?", (body["source_sid"],))[0][0]
        committed = self.rows()
        self.f.now_ms += 2000
        replay = self.f.request("/api/v2/retry-plan", copy.deepcopy(body))
        self.observe("V3-AD1", body, replay, before, committed_plan_id=plan_id)
        self.assertEqual(replay[0], 200, replay[1])
        self.assertIs(replay[1]["created"], False)
        self.assertEqual(self.identity(replay[1]["plan"]), self.identity(first[1]["plan"]))
        self.assertEqual(self.rows(), committed)
        self.assertNotIn("payload_mismatch", replay[1])

    def test_v3_ad2_duplicate_expected_only_is_not_payload_mismatch(self):
        original = self.register(expected_anchor_date="2026-10-01")
        for expected in ("2026-09-30", "2026-10-02", "0001-01-01", "9999-12-31"):
            with self.subTest(expected=expected):
                body, before = self.body(expected_anchor_date=expected, reason=SENTINELS["reason"]), self.before()
                response = self.f.request("/api/v2/retry-plan", body)
                self.observe("V3-AD2-expected-only", body, response, before)
                self.assertEqual(response[0], 200, response[1])
                self.assertIs(response[1]["created"], False)
                self.assertEqual(self.identity(response[1]["plan"]), self.identity(original["plan"]))
                self.assertNotIn("payload_mismatch", response[1])
                self.assertEqual(self.rows(), before["rows"])

    def test_v3_ad2_duplicate_reason_or_timezone_keeps_original_calendar(self):
        original = self.register(expected_anchor_date="2026-10-01")
        body, before = self.body(expected_anchor_date="2026-10-02", timezone="America/New_York", reason="changed reason"), self.before()
        response = self.f.request("/api/v2/retry-plan", body)
        self.observe("V3-AD2-payload-mismatch", body, response, before)
        self.assertEqual(response[0], 200, response[1])
        self.assertIs(response[1]["created"], False)
        self.assertIs(response[1]["payload_mismatch"], True)
        self.assertEqual(self.identity(response[1]["plan"]), self.identity(original["plan"]))
        self.assertEqual(self.rows(), before["rows"])

    def test_v3_ad2_duplicate_malformed_expected_is_still_invalid(self):
        self.register()
        body, before = self.body(expected_anchor_date=None), self.before()
        self.rejected("V3-AD2-malformed", body, self.f.request("/api/v2/retry-plan", body), before, "INVALID_INPUT")

    def test_v3_ad2_existing_plan_precedes_changed_source_eligibility(self):
        original = self.register()
        changed = copy.deepcopy(self.f.source)
        changed["runs"][0]["result"]["status"] = "passed"
        changed["runs"][0]["result"]["passed"] = 2
        self.f.sql("UPDATE sessions SET state=? WHERE id=?", (dumps(changed), changed["id"]))
        body, before = self.body(expected_anchor_date="2026-10-02", reason=SENTINELS["reason"]), self.before()
        response = self.f.request("/api/v2/retry-plan", body)
        self.observe("V3-AD2-duplicate-before-eligibility", body, response, before)
        self.assertEqual(response[0], 200, response[1])
        self.assertIs(response[1]["created"], False)
        self.assertEqual(self.identity(response[1]["plan"]), self.identity(original["plan"]))
        self.assertEqual(self.rows(), before["rows"])
        self.assertEqual(self.source_hash(changed["id"]), before["source_hash"])

    def test_v3_ad3_two_actual_http_requests_are_serialized_by_sqlite(self):
        self.f.now_ms = epoch("2026-10-01T15:00:01Z")
        for index, first_expected in enumerate(("2026-10-02", "2026-10-01")):
            with self.subTest(first_expected=first_expected):
                source = self.f.seed(pid="fixture-anchor-racer-" + str(index))
                first_body = self.body(source, expected_anchor_date=first_expected)
                second_body = self.body(source, expected_anchor_date="2026-10-02")
                before = self.before(source)
                holder = sqlite3.connect(self.f.data / "lab.sqlite3", timeout=5)
                holder.execute("BEGIN IMMEDIATE")
                pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
                try:
                    with self.f.observe_sqlite() as probe:
                        first = pool.submit(self.f.request, "/api/v2/retry-plan", first_body)
                        self.assertTrue(probe.write_attempt.wait(3))
                        second = pool.submit(self.f.request, "/api/v2/retry-plan", second_body)
                        holder.commit()
                        responses = [first.result(timeout=5), second.result(timeout=5)]
                        self.observations.append(dict(gate="V3-AD3-sqlite-racers", first_expected=first_expected,
                                                      responses=responses, **probe.proof()))
                finally:
                    holder.rollback()
                    holder.close()
                    pool.shutdown(wait=True)
                self.observe("V3-AD3-second", second_body, responses[1], before, source)
                self.assertEqual(responses[1][0], 200, responses)
                self.assertEqual(responses[0][0], 200 if index == 0 else 400, responses)
                if index == 0:
                    self.assertIs(responses[0][1]["created"], True)
                    self.assertIs(responses[1][1]["created"], False)
                    self.assertEqual(self.identity(responses[0][1]["plan"]), self.identity(responses[1][1]["plan"]))
                else:
                    self.assertEqual(responses[0][1].get("code"), "ANCHOR_DATE_CHANGED")
                    self.assertIs(responses[1][1]["created"], True)
                plan = responses[1][1]["plan"]
                self.assertEqual(self.f.sql("SELECT COUNT(*) FROM retry_plans WHERE source_sid=?", (source["id"],)), [(1,)])
                self.assertEqual(self.f.sql("SELECT COUNT(*) FROM retry_items WHERE plan_id=?", (plan["id"],)), [(3,)])
                self.assertEqual(self.source_hash(source["id"]), before["source_hash"])

    def test_v3_ad3_other_open_plan_conflict_precedes_stale_guard(self):
        old = self.register()["plan"]
        source = self.f.seed()
        body, before = self.body(source, expected_anchor_date="2026-10-02"), self.before(source)
        dto = self.rejected("V3-AD3-PLAN_EXISTS-priority", body, self.f.request("/api/v2/retry-plan", body), before, "PLAN_EXISTS", source)
        self.assertEqual(dto.get("plan_id"), old["id"])

    def test_v2_ad5_new_source_errors_precede_stale_date_guard(self):
        cases = [("SOURCE_ACTIVE", dict(status="active")),
                 ("NOT_ELIGIBLE", dict(runs=[("passed", 1000, True, 0)])),
                 ("GRADE_UNAVAILABLE", dict(runs=[("error", 1000, True, 0)])),
                 ("GRADING_PENDING", dict())]
        for index, (code, seed) in enumerate(cases):
            with self.subTest(code=code):
                source = self.f.seed(pid="fixture-anchor-eligibility-" + str(index), **seed)
                if code == "GRADING_PENDING":
                    self.f.pending_submission(source["id"])
                body, before = self.body(source, expected_anchor_date="2026-10-02"), self.before(source)
                self.rejected("V2-AD5-priority", body, self.f.request("/api/v2/retry-plan", body), before, code, source)


if __name__ == "__main__":
    unittest.main()
