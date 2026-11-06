"""R1-001/002 regressions on disposable data and the real HTTP route handlers.

The handler is in memory; no existing server, user recording or AI is accessed.
The existing service fixture is composed, so its tests are not discovered twice.
"""

import copy
import contextlib
import hashlib
import io
import json
import threading
import unittest
import uuid
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import test_retries as retry_tests
from coach import http, retries, rpc


class Handler:
    def __init__(self, data=None):
        raw = json.dumps(data).encode() if data is not None else b""
        self.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json"}
        self.rfile = io.BytesIO(raw)
        self.status = None
        self.dto = None

    def send(self, status, dto):
        self.status, self.dto = status, dto


class RetryReviewBackendTest(unittest.TestCase):
    def setUp(self):
        fence = patch.object(rpc.RPC, "__init__", side_effect=AssertionError("external RPC forbidden"))
        fence.start()
        self.addCleanup(fence.stop)
        self.f = retry_tests.ServiceIntegrationTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.media = self.f.st.DATA / "sessions" / self.f.source["id"] / "fixture.part"
        self.media.parent.mkdir(parents=True)
        self.media.write_bytes(b"synthetic R1 preservation evidence")
        with self.f.st.db(True) as con:
            con.execute("INSERT INTO media VALUES (?,?,?,?,?,?,?)",
                        (self.f.source["id"], "screen", "a" * 32, 0, 0, "video/webm", self.media.stat().st_size))

    def request(self, path, data=None):
        handler = Handler(data)
        route = http.get if data is None else http.post
        self.assertTrue(route(handler, self.f.lab, path))
        return handler.status, handler.dto

    def source_bytes(self):
        return self.f.source_bytes(), hashlib.sha256(self.media.read_bytes()).hexdigest()

    def start_data(self, plan):
        self.f.clock[0] = retry_tests.DUE
        return dict(item_id=plan["items"][0]["id"], kind="single", mode="training",
                    duration=40, resources="none", model="fixture", effort="low")

    def assert_no_started_retry(self, plan):
        with self.f.st.db() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM events WHERE kind='start'").fetchone()[0], 0)
            self.assertIsNone(con.execute("SELECT session_id FROM retry_items WHERE id=?",
                                          (plan["items"][0]["id"],)).fetchone()[0])
        self.assertFalse(self.f.lab.timers)

    def assert_blocked_snapshot(self, plan):
        before = self.source_bytes()
        data = self.start_data(plan)
        for _ in range(2):
            status, dto = self.request("/api/v2/retry-start", data)
            self.assertEqual((status, dto.get("code"), dto.get("cause")),
                             (400, "SOURCE_UNAVAILABLE", "PROBLEM_CORRUPT"), dto)
            self.assert_no_started_retry(plan)
        status, cards = self.request("/api/v2/retries")
        self.assertEqual(status, 200, cards)
        self.assertEqual(len(cards["unavailable"]), 1)
        self.assertFalse(cards["active"])
        self.assertFalse(cards["due"])
        self.assertEqual(cards["unavailable"][0]["item_id"], plan["items"][0]["id"])
        self.assertFalse(cards["unavailable"][0]["can_start"])
        self.assertEqual(self.source_bytes(), before)

    def test_r1_001_missing_public_rejected_before_commit_and_replay(self):
        plan = self.f.register()
        self.f.st.mutate(self.f.source["id"], lambda s: s["problems"][0]["tests"][0].pop("public"))
        self.assert_blocked_snapshot(plan)

    def test_r1_001_raw_defaults_and_required_runner_fields_fail_closed(self):
        plan = self.f.register()
        original = copy.deepcopy(self.f.source)
        corruptions = {
            "null_public": lambda p: p["tests"][0].update(public=None),
            "integer_public": lambda p: p["tests"][0].update(public=1),
            "string_public": lambda p: p["tests"][0].update(public="false"),
            "hidden_missing_public": lambda p: p["tests"][-1].pop("public"),
            "generated_public": lambda p: p["tests"][-1].update(public=True),
            "null_time_limit": lambda p: p["tests"][0].update(time_limit_ms=None),
            "missing_args": lambda p: p["tests"][0].pop("args"),
            "missing_expected": lambda p: p["tests"][0].pop("expected"),
            "missing_param_name": lambda p: p["params"][0].pop("name"),
            "missing_method": lambda p: p.pop("method"),
            "missing_starter": lambda p: p.pop("starter"),
        }
        for name, corrupt in corruptions.items():
            with self.subTest(name=name):
                source = copy.deepcopy(original)
                corrupt(source["problems"][0])
                with self.f.st.db(True) as con:
                    con.execute("UPDATE sessions SET state=? WHERE id=?",
                                (self.f.st.dump(source), source["id"]))
                self.assert_blocked_snapshot(plan)

    def test_r1_001_transaction_rechecks_snapshot_after_ide_preparation(self):
        plan = self.f.register()
        injected = []

        def prepare(*args):
            self.f.st.mutate(self.f.source["id"], lambda s: s["problems"][0]["tests"][0].pop("public"))
            injected.append(self.source_bytes())

        self.f.lab.ide.prepare = prepare
        status, dto = self.request("/api/v2/retry-start", self.start_data(plan))
        self.assertEqual((status, dto.get("code"), dto.get("cause")),
                         (400, "SOURCE_UNAVAILABLE", "PROBLEM_CORRUPT"), dto)
        self.assert_no_started_retry(plan)
        self.assertEqual(self.source_bytes(), injected[0])

    def test_r1_001_rejected_source_can_be_repaired_then_started_once(self):
        plan = self.f.register()
        self.f.st.mutate(self.f.source["id"], lambda s: s["problems"][0]["tests"][0].pop("public"))
        self.assert_blocked_snapshot(plan)
        # Fixture-only repair is distinct from the product operation under test.
        self.f.st.mutate(self.f.source["id"], lambda s: s["problems"][0]["tests"][0].update(public=True))
        before = self.source_bytes()
        data = self.start_data(plan)
        with patch.object(self.f.lab, "all_problems", side_effect=AssertionError("catalog fallback forbidden")):
            status, first = self.request("/api/v2/retry-start", data)
            replay_status, replay = self.request("/api/v2/retry-start", data)
        self.assertEqual((status, replay_status), (200, 200))
        self.assertTrue(first["created"])
        self.assertFalse(replay["created"])
        self.assertEqual(first["session"]["id"], replay["session"]["id"])
        self.assertEqual(first["session"]["codes"]["pair"], self.f.source["problems"][0]["starter"])
        self.assertEqual(self.source_bytes(), before)

    def complete_retry(self):
        s = self.f.start()
        self.f.clock[0] += 120000
        result = retry_tests.run("current-result", "passed", ms=60000)
        self.f.st.mutate(s["id"], lambda state: state["runs"].append(result))
        self.f.st.event(s["id"], "submit", {"run_id": result["id"]}, "pair", ms=60000)
        self.f.lab.finish(s["id"])
        return s

    def event(self, sid, payload="[]", ms=10):
        with self.f.st.db(True) as con:
            con.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                        (sid, uuid.uuid4().hex, ms, "switch", "pair", payload))

    def assert_current(self, dto):
        self.assertEqual(dto["current"]["minutes"], 2.0)
        self.assertEqual(dto["current"]["outcome"], "passed")
        self.assertEqual(dto["current"]["run_id"], "current-result")
        self.assertEqual(dto["current"]["result_basis"], "submit_events")

    def test_r1_002_source_switch_array_preserves_current_http_comparison(self):
        s = self.complete_retry()
        self.event(self.f.source["id"])
        before = self.source_bytes()
        status, dto = self.request("/api/v2/retry-comparison/" + s["id"])
        self.assertEqual(status, 200, dto)
        self.assertEqual((dto["state"], dto["code"]), ("unavailable", "SOURCE_UNAVAILABLE"))
        self.assert_current(dto)
        self.assertNotIn("source", dto)
        self.assertNotIn("reason", dto)
        self.assertEqual(self.request("/api/v2/session/" + s["id"])[0], 200)
        self.assertEqual(self.source_bytes(), before)

    def test_r1_002_other_malformed_switch_shapes_are_unavailable(self):
        s = self.complete_retry()
        for payload, ms in [("null", 10), ('"text"', 10), ('{"to":[]}', 10),
                            ('{"to":{}}', 10), ('{"to":"pair"}', "bad-ms")]:
            with self.subTest(payload=payload, ms=ms):
                with self.f.st.db(True) as con:
                    con.execute("DELETE FROM events WHERE sid=? AND kind='switch'", (self.f.source["id"],))
                self.event(self.f.source["id"], payload, ms)
                before = self.source_bytes()
                status, dto = self.request("/api/v2/retry-comparison/" + s["id"])
                self.assertEqual((status, dto.get("state"), dto.get("code")),
                                 (200, "unavailable", "SOURCE_UNAVAILABLE"), dto)
                self.assert_current(dto)
                self.assertEqual(self.source_bytes(), before)

    def test_r1_002_missing_or_invalid_json_source_preserves_current(self):
        s = self.complete_retry()
        for state in ("{", None):
            with self.subTest(state=state):
                with self.f.st.db(True) as con:
                    if state is None:
                        con.execute("DELETE FROM sessions WHERE id=?", (self.f.source["id"],))
                    else:
                        con.execute("UPDATE sessions SET state=? WHERE id=?", (state, self.f.source["id"]))
                before = self.source_bytes()
                status, dto = self.request("/api/v2/retry-comparison/" + s["id"])
                self.assertEqual((status, dto.get("state"), dto.get("code")),
                                 (200, "unavailable", "SOURCE_UNAVAILABLE"), dto)
                self.assert_current(dto)
                self.assertEqual(self.source_bytes(), before)

    def test_r1_002_source_invalid_json_event_preserves_current(self):
        s = self.complete_retry()
        self.event(self.f.source["id"], "{")
        before = self.source_bytes()
        status, dto = self.request("/api/v2/retry-comparison/" + s["id"])
        self.assertEqual((status, dto.get("state"), dto.get("code")),
                         (200, "unavailable", "SOURCE_UNAVAILABLE"), dto)
        self.assert_current(dto)
        self.assertEqual(self.source_bytes(), before)

    def test_r1_002_current_switch_corruption_is_an_error_not_source_fallback(self):
        s = self.complete_retry()
        self.event(s["id"])
        before = self.source_bytes()
        status, dto = self.request("/api/v2/retry-comparison/" + s["id"])
        self.assertEqual((status, dto.get("code"), dto.get("cause")),
                         (400, "SOURCE_UNAVAILABLE", "EVENT_CORRUPT"), dto)
        self.assertNotIn("current", dto)
        self.assertNotIn("state", dto)
        self.assertEqual(self.source_bytes(), before)

    def test_r1_002_programming_attribute_error_is_not_swallowed_as_source_damage(self):
        s = self.complete_retry()
        metrics = self.f.lab.metrics

        def bug(attempt):
            if attempt["id"] == self.f.source["id"]:
                raise AttributeError("injected unrelated metrics bug")
            return metrics(attempt)

        with patch.object(self.f.lab, "metrics", side_effect=bug):
            with self.assertRaisesRegex(AttributeError, "unrelated metrics bug"):
                self.f.lab.retry_comparison(s["id"])


class RetryAnchorBackendTest(unittest.TestCase):
    setUp = RetryReviewBackendTest.setUp
    request = RetryReviewBackendTest.request
    source_bytes = RetryReviewBackendTest.source_bytes

    def data(self, **changes):
        return dict(source_sid=self.f.source["id"], pid="pair", reason="anchor reason",
                    timezone="Asia/Seoul", expected_anchor_date="2026-10-01") | changes

    def counts(self):
        with self.f.st.db() as con:
            return tuple(con.execute("SELECT count(*) FROM " + table).fetchone()[0]
                         for table in ("retry_plans", "retry_items"))

    def cache_rows(self):
        with self.f.st.db() as con:
            return {table: [tuple(r) for r in con.execute("SELECT * FROM " + table + " ORDER BY rowid")]
                    for table in ("retry_plans", "retry_items")}

    def unchanged_plan(self, got, original):
        self.assertEqual({k: got[k] for k in ("id", "source_sid", "pid", "reason", "timezone", "anchor_date", "created_at")},
                         {k: original[k] for k in ("id", "source_sid", "pid", "reason", "timezone", "anchor_date", "created_at")})
        self.assertEqual([{k: i[k] for k in ("id", "offset_days", "due_date")} for i in got["items"]],
                         [{k: i[k] for k in ("id", "offset_days", "due_date")} for i in original["items"]])

    def before_midnight(self):
        self.f.clock[0] = retry_tests.at("2026-10-01T14:59:59+00:00")

    def test_ad_capability_is_exact_true_and_old_list_fields_remain(self):
        status, dto = self.request("/api/v2/retries")
        self.assertEqual(status, 200, dto)
        self.assertIs(dto.get("capabilities", {}).get("retry_plan_expected_anchor_date"), True)
        self.assertEqual(set(dto) - {"capabilities"},
                         {"server_now", "next_refresh_at", "due", "active", "grading", "unavailable", "upcoming"})
        self.assertEqual(self.counts(), (0, 0))

    def test_ad_same_date_creates_expected_dates_from_one_guard_observation(self):
        self.before_midnight()
        before = self.source_bytes()
        now = self.f.clock[0]
        status, dto = self.request("/api/v2/retry-plan", self.data())
        self.assertEqual(status, 200, dto)
        self.assertTrue(dto["created"])
        self.assertEqual((dto["plan"]["created_at"], dto["plan"]["anchor_date"]), (now, "2026-10-01"))
        self.assertEqual([i["due_date"] for i in dto["plan"]["items"]], ["2026-10-02", "2026-10-04", "2026-10-08"])
        self.assertEqual(self.counts(), (1, 3))
        self.assertEqual(self.source_bytes(), before)

    def test_ad_midnight_mismatch_returns_safe_preview_and_writes_zero(self):
        self.before_midnight()
        self.assertEqual(self.request("/api/v2/retries")[1]["server_now"], self.f.clock[0])
        self.f.clock[0] += 2000
        before = self.source_bytes()
        status, dto = self.request("/api/v2/retry-plan", self.data())
        self.assertEqual((status, dto.get("code")), (400, "ANCHOR_DATE_CHANGED"), dto)
        self.assertEqual({k: dto[k] for k in ("expected_anchor_date", "current_anchor_date", "timezone", "server_now")},
                         dict(expected_anchor_date="2026-10-01", current_anchor_date="2026-10-02",
                              timezone="Asia/Seoul", server_now=self.f.clock[0]))
        self.assertEqual(dto["preview"], [dict(offset_days=d, due_date=date)
                                        for d, date in zip((1, 3, 7), ("2026-10-03", "2026-10-05", "2026-10-09"))])
        self.assertEqual(set(dto), {"error", "code", "expected_anchor_date", "current_anchor_date", "timezone", "server_now", "preview"})
        self.assertTrue(dto["error"])
        self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(self.source_bytes(), before)

    def test_ad_explicit_null_malformed_date_and_bad_timezone_remain_invalid(self):
        before = self.source_bytes()
        inputs = [self.data(expected_anchor_date=v) for v in
                  (None, "", "2026-1-01", "2026-10-1", "2026-02-29", "0000-01-01", "2026-10-01 ",
                   "２０２６-１０-０１", "2026-10-01T00:00:00Z", 20261001, True, [], {})]
        inputs += [self.data(timezone=v) for v in (None, "", "unknown/timezone", [])]
        missing_timezone = self.data()
        missing_timezone.pop("timezone")
        inputs.append(missing_timezone)
        for data in inputs:
            with self.subTest(data=data):
                status, dto = self.request("/api/v2/retry-plan", data)
                self.assertEqual((status, dto.get("code")), (400, "INVALID_INPUT"), dto)
                self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(self.source_bytes(), before)

    def test_ad_omitted_field_keeps_legacy_creation_without_persisting_expected(self):
        data = self.data()
        data.pop("expected_anchor_date")
        status, dto = self.request("/api/v2/retry-plan", data)
        self.assertEqual(status, 200, dto)
        self.assertTrue(dto["created"])
        self.assertNotIn("expected_anchor_date", dto["plan"])
        with self.f.st.db() as con:
            columns = [r[1] for r in con.execute("PRAGMA table_info(retry_plans)")]
        self.assertNotIn("expected_anchor_date", columns)

    def test_ad_replay_after_midnight_and_source_damage_returns_original_plan(self):
        self.before_midnight()
        status, dto = self.request("/api/v2/retry-plan", self.data())
        self.assertEqual(status, 200, dto)
        first = dto["plan"]
        self.f.clock[0] += 2000
        self.f.st.mutate(self.f.source["id"], lambda s: s.update(status="active", ended=None))
        before = self.source_bytes()
        for state in (None, "{"):
            with self.subTest(state=state):
                if state is not None:
                    with self.f.st.db(True) as con:
                        con.execute("UPDATE sessions SET state=? WHERE id=?", (state, self.f.source["id"]))
                    before = self.source_bytes()
                status, replay = self.request("/api/v2/retry-plan", self.data())
                self.assertEqual(status, 200, replay)
                self.assertFalse(replay["created"])
                self.assertNotIn("payload_mismatch", replay)
                self.unchanged_plan(replay["plan"], first)
                self.assertEqual(self.counts(), (1, 3))
                self.assertEqual(self.source_bytes(), before)

    def test_ad_replay_expected_only_does_not_set_mismatch_but_reason_zone_do(self):
        status, dto = self.request("/api/v2/retry-plan", self.data())
        self.assertEqual(status, 200, dto)
        first = dto["plan"]
        status, replay = self.request("/api/v2/retry-plan", self.data(expected_anchor_date="0001-01-01"))
        self.assertEqual(status, 200, replay)
        self.assertNotIn("payload_mismatch", replay)
        self.unchanged_plan(replay["plan"], first)
        status, changed = self.request("/api/v2/retry-plan", self.data(expected_anchor_date="9999-12-31", reason="changed", timezone="Etc/UTC"))
        self.assertEqual(status, 200, changed)
        self.assertTrue(changed["payload_mismatch"])
        self.unchanged_plan(changed["plan"], first)
        for data in (self.data(expected_anchor_date=None), self.data(expected_anchor_date="bad"), self.data(timezone="bad"), self.data(reason="")):
            with self.subTest(data=data):
                status, error = self.request("/api/v2/retry-plan", data)
                self.assertEqual((status, error.get("code")), (400, "INVALID_INPUT"), error)
        self.assertEqual(self.counts(), (1, 3))

    def test_ad_open_other_plan_has_priority_over_date_and_source_eligibility(self):
        first = self.f.register()
        other = copy.deepcopy(self.f.source)
        other["id"] = uuid.uuid4().hex
        other["runs"] = [retry_tests.run(status="passed")]
        with self.f.st.db(True) as con:
            con.execute("INSERT INTO sessions VALUES (?,?,?)", (other["id"], other["started"], self.f.st.dump(other)))
        status, error = self.request("/api/v2/retry-plan", self.data(source_sid=other["id"], expected_anchor_date="0001-01-01"))
        self.assertEqual((status, error.get("code"), error.get("plan_id")), (400, "PLAN_EXISTS", first["id"]), error)
        self.assertEqual(self.counts(), (1, 3))

    def test_ad_write_lock_wait_across_midnight_uses_post_lock_clock(self):
        self.before_midnight()
        entered = threading.Event()
        real_db = self.f.st.db
        results = []

        @contextlib.contextmanager
        def observed_db(write=False):
            if write:
                entered.set()
            with real_db(write) as con:
                yield con

        with real_db(True):
            self.f.st.now.reset_mock()
            with patch.object(self.f.st, "db", observed_db):
                worker = threading.Thread(target=lambda: results.append(self.request("/api/v2/retry-plan", self.data())))
                worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    self.assertEqual(self.f.st.now.call_count, 0, "clock captured before write lock")
                    self.f.clock[0] += 2000
                finally:
                    self.f.clock[0] = retry_tests.at("2026-10-01T15:00:01+00:00")
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual((results[0][0], results[0][1].get("code")), (400, "ANCHOR_DATE_CHANGED"), results)
        self.assertEqual(self.counts(), (0, 0))

    def test_ad_guard_clock_is_observed_after_eligibility_checks(self):
        self.before_midnight()
        classify = retries.classify_attempt

        def cross_midnight(*args):
            result = classify(*args)
            self.f.clock[0] += 2000
            return result

        with patch.object(retries, "classify_attempt", side_effect=cross_midnight):
            status, dto = self.request("/api/v2/retry-plan", self.data())
        self.assertEqual((status, dto.get("code")), (400, "ANCHOR_DATE_CHANGED"), dto)
        self.assertEqual(dto["server_now"], self.f.clock[0])
        self.assertEqual(self.counts(), (0, 0))

    def test_ad_clock_crossing_after_guard_before_commit_keeps_confirmed_dates(self):
        self.before_midnight()
        observed = self.f.clock[0]
        real_db = self.f.st.db

        @contextlib.contextmanager
        def cross_at_commit(write=False):
            with real_db(write) as con:
                yield con
                if write:
                    self.f.clock[0] += 2000

        self.f.st.now.reset_mock()
        with patch.object(self.f.st, "db", cross_at_commit):
            status, dto = self.request("/api/v2/retry-plan", self.data())
        self.assertEqual(status, 200, dto)
        self.assertEqual(self.f.st.now.call_count, 1)
        self.assertEqual((dto["plan"]["created_at"], dto["plan"]["anchor_date"]), (observed, "2026-10-01"))
        self.assertEqual([i["due_date"] for i in dto["plan"]["items"]], ["2026-10-02", "2026-10-04", "2026-10-08"])
        self.assertEqual(self.f.clock[0], observed + 2000)

    def test_ad_mismatch_rolls_back_completion_cache_reconciliation(self):
        plan = self.f.register()
        self.f.clock[0] = retry_tests.DUE
        for item in plan["items"]:
            s = self.f.lab.retry_start(dict(item_id=item["id"], kind="single", mode="training", duration=40, resources="none"))["session"]
            self.f.lab.finish(s["id"])
        with self.f.st.db(True) as con:
            con.execute("UPDATE retry_plans SET status='open' WHERE id=?", (plan["id"],))
            con.execute("UPDATE retry_items SET completed_at=NULL WHERE plan_id=?", (plan["id"],))
            other = copy.deepcopy(self.f.source)
            other["id"] = uuid.uuid4().hex
            con.execute("INSERT INTO sessions VALUES (?,?,?)", (other["id"], other["started"], self.f.st.dump(other)))
        before = self.cache_rows(), self.source_bytes()
        status, dto = self.request("/api/v2/retry-plan", self.data(source_sid=other["id"], expected_anchor_date="0001-01-01"))
        self.assertEqual((status, dto.get("code")), (400, "ANCHOR_DATE_CHANGED"), dto)
        self.assertEqual((self.cache_rows(), self.source_bytes()), before)

    def test_ad_valid_future_and_past_dates_timezone_and_dst_preview(self):
        cases = [("Etc/UTC", "2026-10-01T15:00:01+00:00", "2026-10-01", ["2026-10-02", "2026-10-04", "2026-10-08"]),
                 ("Asia/Seoul", "2026-10-01T15:00:01+00:00", "2026-10-02", ["2026-10-03", "2026-10-05", "2026-10-09"]),
                 ("America/New_York", "2026-03-08T07:00:00+00:00", "2026-03-08", ["2026-03-09", "2026-03-11", "2026-03-15"]),
                 ("America/New_York", "2026-11-01T06:00:00+00:00", "2026-11-01", ["2026-11-02", "2026-11-04", "2026-11-08"])]
        for zone, now, anchor, dates in cases:
            for expected in ("0001-01-01", "9999-12-31"):
                with self.subTest(zone=zone, expected=expected):
                    self.f.clock[0] = retry_tests.at(now)
                    status, dto = self.request("/api/v2/retry-plan", self.data(timezone=zone, expected_anchor_date=expected))
                    self.assertEqual((status, dto.get("code")), (400, "ANCHOR_DATE_CHANGED"), dto)
                    self.assertEqual(dto["current_anchor_date"], anchor)
                    self.assertEqual(dto["server_now"], self.f.clock[0])
                    self.assertEqual([i["due_date"] for i in dto["preview"]], dates)
                    self.assertEqual(self.counts(), (0, 0))

    def test_ad_twenty_mixed_stale_valid_requests_create_one_plan_three_items(self):
        data = [self.data(expected_anchor_date="2026-10-01" if i % 2 else "2026-09-30") for i in range(20)]
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(lambda d: self.request("/api/v2/retry-plan", d), data))
        for supplied, (status, dto) in zip(data, results):
            if supplied["expected_anchor_date"] == "2026-10-01":
                self.assertEqual(status, 200, dto)
            else:
                self.assertTrue(status == 200 or (status == 400 and dto.get("code") == "ANCHOR_DATE_CHANGED"), dto)
        successes = [dto for status, dto in results if status == 200]
        self.assertEqual(sum(dto["created"] for dto in successes), 1)
        self.assertEqual(len({dto["plan"]["id"] for dto in successes}), 1)
        self.assertEqual(self.counts(), (1, 3))


if __name__ == "__main__":
    unittest.main()
