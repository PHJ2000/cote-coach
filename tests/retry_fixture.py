"""Disposable retry-v1 HTTP fixture; never implements the retry API itself.

Only synthetic records are written. COACH_DATA_DIR is set before product imports;
Java, IDE, Codex, STT and media subprocesses are stubbed before constructing Lab.
The CLI binds 127.0.0.1:0 and emits one JSON handshake. Fixture-only controls need
the random handshake token. Do not point acceptance tests at an existing server.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import re
import secrets
import signal
import sqlite3
import sys
import tempfile
import threading
import types
import uuid
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NOW = 1790841600000  # 2026-10-01T08:00:00Z; not the system clock.
PID = "fixture-retry"
STARTER = "class Solution { public int solution(int n) { return 0; } }"
SENTINELS = {
    "code": "P3_OLD_CODE_927af",
    "report": "P3_OLD_REPORT_927af",
    "tag": "P3_OLD_TAG_927af",
    "reason": "P3_OLD_REASON_927af",
    "message": "P3_OLD_MESSAGE_927af",
    "thread": "P3_OLD_THREAD_927af",
    "transcript": "P3_OLD_TRANSCRIPT_927af",
    "frame": "P3_OLD_FRAME_927af",
    "analysis": "P3_OLD_ANALYSIS_927af",
}
LEGACY_DDL = """
CREATE TABLE sessions(id TEXT PRIMARY KEY,created INTEGER,state TEXT);
CREATE TABLE events(sid TEXT,id TEXT,ms INTEGER,kind TEXT,pid TEXT,payload TEXT,PRIMARY KEY(sid,id));
CREATE TABLE problems(id TEXT PRIMARY KEY,payload TEXT);
CREATE TABLE jobs(id TEXT PRIMARY KEY,sid TEXT,state TEXT);
CREATE TABLE media(sid TEXT,kind TEXT,segment TEXT,seq INTEGER,start_ms INTEGER,mime TEXT,size INTEGER,PRIMARY KEY(sid,kind,segment,seq));
"""
LEGACY_TABLES = ("sessions", "events", "problems", "jobs", "media")


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


def epoch(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def problem(pid=PID):
    return dict(id=pid, title="격리 재도전 숫자 문제", level=1,
                description="정수 n을 그대로 반환하세요. -10 ≤ n ≤ 10.",
                source="P3 synthetic fixture", url="", tags=[SENTINELS["tag"]],
                returns="int", method="solution", params=[dict(type="int", name="n")],
                starter=STARTER, tests=[dict(args=[1], expected=1, public=True),
                                       dict(args=[-7], expected=-7, public=False)],
                time_limit_ms=3000)


def grade(status="failed"):
    passed = 2 if status == "passed" else 0
    return dict(status=status, passed=passed, total=2, duration_ms=17,
                output="P3 synthetic Java result", error="synthetic grader fault" if status == "error" else None,
                tests=[dict(index=i + 1, public=i == 0, passed=status == "passed",
                            expected=x, actual=x if status == "passed" else 0,
                            args=[x], duration_ms=8) for i, x in enumerate((1, -7))])


def source_state(now=DEFAULT_NOW, pid=PID, runs=None, emit_events=True,
                 status="completed", duration=40, elapsed_ms=2400000, multi=False):
    """Make an old attempt without invoking new_session or any retry helper.

    Runs are in completion order; their associated events may have a different
    explicit admission order. Tuple entries: (status, ms, submit, event_order).
    """
    sid = uuid.uuid4().hex
    started = now - 28 * 86400000
    p = problem(pid)
    ps = [p] + ([problem(pid + "-other")] if multi else [])
    definitions = [("failed", 600000, True, 0)] if runs is None else runs
    entries = []
    submit_events = []
    for i, (outcome, ms, submit, order) in enumerate(definitions):
        rid = uuid.uuid4().hex
        entries.append(dict(id=rid, pid=pid, ms=ms, submit=submit, automatic=False,
                            overtime=ms > duration * 60000, code=SENTINELS["code"], result=grade(outcome)))
        if emit_events:
            submit_events.append((order, dict(id=uuid.uuid4().hex, ms=ms,
                                              kind="submit" if submit else "run", pid=pid,
                                              payload=dict(run_id=rid, code=SENTINELS["code"]))))
    s = dict(id=sid, title=p["title"], started=started,
             ended=started + elapsed_ms if status == "completed" else None,
             status=status, mode="training", kind="mock" if multi else "single",
             resources="none", duration=duration, deadline=started + duration * 60000,
             deadline_codes={q["id"]: SENTINELS["code"] for q in ps}, problems=ps,
             codes={q["id"]: SENTINELS["code"] for q in ps}, runs=entries,
             messages=[dict(id=uuid.uuid4().hex, kind="hint", pid=pid,
                            question=SENTINELS["message"], at=started + 1000,
                            answer=dict(message=SENTINELS["message"]), model="fixture-model", effort="low")],
             report=dict(version=2, summary=SENTINELS["report"], strengths=[],
                         weaknesses=[], process_review=[], practice_plan=[], next_practice=[], limitations=[]),
             transcripts=[dict(start_ms=1, end_ms=2, text=SENTINELS["transcript"])],
             thread=SENTINELS["thread"], review_thread=SENTINELS["thread"],
             frames=[dict(ms=2000, path=f"sessions/{sid}/frames/{SENTINELS['frame']}.png")],
             analysis_inputs=[dict(id=uuid.uuid4().hex, kind="hint", created=started + 2000,
                                  model="fixture-model", effort="low", prompt_version=2,
                                  path=f"sessions/{sid}/analysis/{SENTINELS['analysis']}.json")],
             code_versions={pid: [1000, 0]},
             model="fixture-model", effort="low", media_expected=False,
             report_history=[dict(replaced_at=started + 2000, report=dict(summary=SENTINELS["report"]))])
    events = [dict(id=uuid.uuid4().hex, kind="start", ms=0, pid="", payload={})]
    events += [e for _, e in sorted(submit_events, key=lambda x: x[0])]
    return s, events


class Gate:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.enabled = False

    def arm(self):
        self.entered.clear()
        self.release.clear()
        self.enabled = True

    def block(self):
        if self.enabled:
            self.entered.set()
            if not self.release.wait(10):
                raise RuntimeError("P3 fixture gate timed out; test did not release it")

    def open(self):
        self.enabled = False
        self.release.set()


class FakeCoach:
    rpc = None

    def __init__(self, data):
        self.calls = []

    def info(self):
        return dict(auth="chatgpt", limits=None, models=[dict(
            model="fixture-model", displayName="격리 모의 모델", isDefault=True,
            defaultReasoningEffort="low", inputModalities=["text", "image"],
            supportedReasoningEfforts=[dict(reasoningEffort="low")])])

    def chat(self, thread, model, effort, prompt, progress, cancel, save_thread, images=None):
        # This is a local stub, not an external AI invocation.
        self.calls.append(dict(thread=thread, model=model, effort=effort,
                               prompt=json.loads(prompt), images=images))
        tid = "fixture-new-thread-" + uuid.uuid4().hex
        save_thread(tid)
        return dict(message="격리 모의 새 힌트", questions=[], report=None), {}, tid


class FakeSpeech:
    def __init__(self, root):
        pass

    def transcribe(self, path):
        return [dict(start_ms=0, end_ms=100, text="격리 모의 전사")]


class FakeIDE:
    rpc = None

    def __init__(self, root, data):
        self.prepared = []

    def warmup(self):
        pass

    def prepare(self, key, code):
        self.prepared.append((key, code))

    def request(self, key, code, op, *args, **kwargs):
        return dict(items=[], signatures=[], diagnostics=[], isIncomplete=False)


class SQLiteProbe:
    """Observe real SQLite/clock boundaries, without replacing transactions.

    Only the fixture database is instrumented. The clock can move immediately
    before a real plan INSERT, after the product has already evaluated its guard.
    SQL values, session state and reason text are never copied into the trace.
    """
    def __init__(self, fixture, advance_at_plan_insert=None):
        self.fixture = fixture
        self.advance_at_plan_insert = advance_at_plan_insert
        self.write_attempt = threading.Event()
        self.lock = threading.Lock()
        self.events = []
        self.phases = {}
        self.insert_clock_moves = 0

    def record(self, event, **details):
        with self.lock:
            thread = threading.get_ident()
            self.events.append(dict(index=len(self.events), event=event, thread=thread,
                                    now=self.fixture.now_ms, phase=self.phases.get(thread, "outside_write"), **details))

    def before_sql(self, sql):
        statement = " ".join(sql.upper().split())
        if statement.startswith("BEGIN IMMEDIATE"):
            self.phases[threading.get_ident()] = "waiting_for_write_lock"
            self.record("begin_immediate_attempt")
            self.write_attempt.set()
        elif re.match(r'^INSERT(?: OR \w+)? INTO ["`]?RETRY_PLANS\b', statement):
            self.record("before_plan_insert")
            if self.advance_at_plan_insert is not None and not self.insert_clock_moves:
                self.fixture.now_ms += self.advance_at_plan_insert
                self.insert_clock_moves += 1
                self.record("clock_advanced_before_insert")

    def after_sql(self, sql):
        if " ".join(sql.upper().split()).startswith("BEGIN IMMEDIATE"):
            self.phases[threading.get_ident()] = "write_lock_held"
            self.record("write_lock_acquired")

    def proof(self):
        with self.lock:
            return dict(events=copy.deepcopy(self.events), insert_clock_moves=self.insert_clock_moves,
                        actual_sqlite=True, product_registration_helper_replaced=False)


class RetryFixture:
    def __init__(self, now=DEFAULT_NOW, media=True, port=0):
        self.now_ms = now
        self.seed_media = media
        self.port = port
        self.token = secrets.token_hex(24)
        self.workers = []
        self.java_calls = []
        self.upload_attempts = []
        self.upload_rejections = set()
        self.upload_lock = threading.Lock()
        self.sqlite_probe = None
        self.java_gate = Gate()
        self.admission_gate = Gate()
        self.finish_entered = threading.Event()
        self.stack = ExitStack()
        self.http = None
        self.http_thread = None
        self.lab = None

    def __enter__(self):
        try:
            return self.start()
        except BaseException:
            self.close()
            raise

    def start(self):
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="cote-retry-p3-"))
        self.data = Path(self.temp)
        self.stack.enter_context(patch.dict(os.environ, {"COACH_DATA_DIR": self.temp, "PYTHONDONTWRITEBYTECODE": "1"}))
        self.stack.enter_context(patch.object(sys, "dont_write_bytecode", True))
        # Deliberately construct the five-table old format before additive init.
        con = sqlite3.connect(self.data / "lab.sqlite3")
        con.executescript(LEGACY_DDL)
        con.close()
        self.source, events = source_state(self.now_ms)
        self.insert_source(self.source, events, media=self.seed_media)
        self.legacy_baseline = self.raw_snapshot()
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        # Install the process fence before importing any product service code.
        self.stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError(
            "P3 fixture forbids real subprocess execution (Java, AI, STT, media)")))
        from coach import rpc
        self.stack.enter_context(patch.object(rpc.RPC, "__init__", side_effect=AssertionError(
            "P3 fixture forbids real RPC / external AI / IDE processes")))
        from coach import store, service, speech
        import server
        self.st, self.service, self.server = store, service, server
        # unittest discovery may have already imported store in another module;
        # the owned temp remains authoritative and the original value is restored.
        self.stack.enter_context(patch.object(store, "DATA", self.data))
        self.stack.enter_context(patch.object(store, "now", self.read_clock))
        self.stack.enter_context(patch.object(service, "CodexCoach", FakeCoach))
        self.stack.enter_context(patch.object(service, "Speech", FakeSpeech))
        self.stack.enter_context(patch.object(service, "JavaIDE", FakeIDE))
        self.stack.enter_context(patch.object(service, "run_java", self.fake_java))
        self.stack.enter_context(patch.object(service, "subprocess", types.SimpleNamespace(
            run=lambda args, **kwargs: types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"P3 media subprocess mocked"))))
        self.stack.enter_context(patch.object(speech, "ffmpeg", lambda: "P3-mocked-ffmpeg"))
        # Track only product workers made by this fixture, never other sessions.
        thread_api = types.SimpleNamespace(**{k: getattr(threading, k) for k in dir(threading) if not k.startswith("__")})
        thread_api.Thread = self.tracked_thread
        self.stack.enter_context(patch.object(service, "threading", thread_api))
        self.stack.enter_context(patch.object(server, "DB_PATH", self.data / "legacy.sqlite3"))
        self.stack.enter_context(patch.object(server, "LAB", None))
        self.install_lab()
        fixture = self

        class Handler(server.Handler):
            def send(self, status, body, *args):
                attempt = getattr(self, "upload_attempt", None)
                if attempt is not None:
                    with fixture.upload_lock:
                        fixture.upload_attempts.append(dict(attempt, status=status))
                    self.upload_attempt = None
                return super().send(status, body, *args)

            def log_message(self, *args):
                pass

            def do_GET(self):
                path = urlsplit(self.path).path
                if path.startswith("/_retry_fixture/"):
                    if not self.fixture_auth():
                        return
                    if path == "/_retry_fixture/status":
                        self.send(200, fixture.handshake())
                    elif path == "/_retry_fixture/proof":
                        self.send(200, dict(java_calls=fixture.java_calls,
                                            coach_calls=fixture.lab.codex.calls,
                                            snapshot=fixture.raw_snapshot(),
                                            source_snapshot=fixture.raw_snapshot(fixture.source['id']),
                                            upload_attempts=fixture.upload_proof(),
                                            retry_counts={table: fixture.sql("SELECT COUNT(*) FROM " + table)[0][0]
                                                          for table in ("retry_plans", "retry_items")},
                                            now=fixture.now_ms))
                    else:
                        self.send(404, dict(error="Unknown fixture-only path"))
                    return
                if not (path == "/" or path.startswith("/dist/") or path.startswith("/api/v2/")):
                    self.send(404, dict(error="Legacy endpoints are disabled in P3 fixture"))
                    return
                super().do_GET()

            def fixture_auth(self):
                if self.headers.get("X-Retry-Fixture") != fixture.token:
                    self.send(403, dict(error="Disposable fixture token required"))
                    return False
                return self.allowed()

            def do_POST(self):
                path = urlsplit(self.path).path
                if path.startswith("/_retry_fixture/"):
                    if not self.fixture_auth():
                        return
                    try:
                        size = int(self.headers.get("Content-Length", "0"))
                        if not 0 < size < 100000:
                            raise ValueError("Fixture control body required")
                        d = json.loads(self.rfile.read(size))
                        if path == "/_retry_fixture/clock":
                            fixture.now_ms = int(d["now"])
                        elif path == "/_retry_fixture/advance":
                            fixture.now_ms += int(d["ms"])
                        elif path == "/_retry_fixture/expire":
                            fixture.lab.expire(d["sid"])
                        elif path == "/_retry_fixture/settle":
                            fixture.settle()
                        elif path == "/_retry_fixture/upload-rejection":
                            fixture.reject_source_upload(d["sid"], d["segment"], d["reject"])
                        elif path == "/_retry_fixture/seed-source":
                            pid = d.get("pid")
                            if not isinstance(pid, str) or not re.fullmatch(r"fixture-[a-z0-9-]{1,40}", pid):
                                raise ValueError("Synthetic fixture pid required")
                            source = fixture.seed(pid=pid)
                            self.send(200, dict(now=fixture.now_ms, source_sid=source["id"], pid=pid))
                            return
                        else:
                            raise ValueError("Unknown fixture-only control")
                        self.send(200, dict(now=fixture.now_ms))
                    except Exception as exc:
                        self.send(400, dict(error=str(exc)))
                    return
                if not path.startswith("/api/v2/"):
                    self.send(404, dict(error="Legacy endpoints are disabled in P3 fixture"))
                    return
                if path == "/api/v2/upload":
                    # Observe actual loopback POSTs. Accepted requests still run
                    # the unchanged product HTTP upload handler and store code.
                    query = parse_qs(urlsplit(self.path).query)
                    raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    self.rfile = io.BytesIO(raw)
                    sid, segment = query.get("sid", [""])[0], query.get("segment", [""])[0]
                    self.upload_attempt = dict(sid=sid, segment=segment,
                                               kind=query.get("kind", [""])[0],
                                               seq=query.get("seq", [""])[0],
                                               bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                    with fixture.upload_lock:
                        rejected = (sid, segment) in fixture.upload_rejections
                    if rejected:
                        self.send(400, dict(error="P3 old upload held"))
                        return
                super().do_POST()

        class HTTP(server.ThreadingHTTPServer):
            request_queue_size = 64
            daemon_threads = True

        self.http = HTTP(("127.0.0.1", self.port), Handler)
        self.base_url = f"http://127.0.0.1:{self.http.server_port}"
        self.http_thread = threading.Thread(target=self.http.serve_forever, name="P3-disposable-http", daemon=True)
        self.http_thread.start()
        return self

    def install_lab(self):
        self.lab = self.service.Lab()
        self.lab.health = lambda: dict(java=True, ide=True, speech=True, codex=True, fixture_mocks=True)
        original_job, original_finish = self.lab.job, self.lab.finish

        def admission(sid, kind, fn):
            if kind == "java":
                self.admission_gate.block()
            return original_job(sid, kind, fn)

        def finish(sid):
            self.finish_entered.set()
            return original_finish(sid)

        self.lab.job, self.lab.finish = admission, finish
        self.server.LAB = self.lab

    def restart(self):
        self.lab.close()
        self.install_lab()

    def tracked_thread(self, *args, **kwargs):
        worker = threading.Thread(*args, **kwargs)
        self.workers.append(worker)
        return worker

    def fake_java(self, code, p, submit):
        self.java_calls.append(dict(code=code, problem=copy.deepcopy(p), submit=submit))
        self.java_gate.block()
        return grade("passed" if "fixture:passed" in code else "error" if "fixture:error" in code else "failed")

    def read_clock(self):
        if self.sqlite_probe is not None:
            self.sqlite_probe.record("server_clock_read")
        return self.now_ms

    @contextmanager
    def observe_sqlite(self, advance_at_plan_insert=None):
        """Keep sqlite3.Connection.execute/commit real, and restore the probe."""
        if self.sqlite_probe is not None:
            raise RuntimeError("Nested SQLite probes are not supported")
        probe = SQLiteProbe(self, advance_at_plan_insert)
        original_connect = sqlite3.connect
        owned_database = (self.data / "lab.sqlite3").resolve()

        class ObservedConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                probe.before_sql(sql)
                result = super().execute(sql, parameters)
                probe.after_sql(sql)
                return result

            def commit(self):
                probe.record("commit")
                result = super().commit()
                probe.phases[threading.get_ident()] = "outside_write"
                return result

            def rollback(self):
                probe.record("rollback")
                result = super().rollback()
                probe.phases[threading.get_ident()] = "outside_write"
                return result

        def connect(database, *args, **kwargs):
            if Path(database).resolve() == owned_database:
                if "factory" in kwargs:
                    raise RuntimeError("Fixture probe must not replace another connection factory")
                kwargs["factory"] = ObservedConnection
            return original_connect(database, *args, **kwargs)

        self.sqlite_probe = probe
        try:
            with patch.object(sqlite3, "connect", connect):
                yield probe
        finally:
            self.sqlite_probe = None

    def settle(self):
        for worker in list(self.workers):
            worker.join(5)
            if worker.is_alive():
                raise RuntimeError("P3 fixture worker did not settle; release gates first")

    def request(self, path, data=None, raw=None, headers=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Fixture requests require a local relative path")
        body = raw if raw is not None else dumps(data).encode() if data is not None else None
        hs = {"X-Retry-Fixture": self.token}
        if body is not None:
            hs["Content-Type"] = "application/octet-stream" if raw is not None else "application/json"
        hs.update(headers or {})
        request = Request(self.base_url + path, data=body, headers=hs, method="POST" if body is not None else "GET")
        try:
            response = urlopen(request, timeout=8)
        except HTTPError as exc:
            response = exc
        with response:
            payload = response.read()
            result = json.loads(payload) if "application/json" in response.headers.get("Content-Type", "") else payload
            return response.status, result

    def handshake(self):
        return dict(fixture="retry-v1-p3", base_url=self.base_url, token=self.token,
                    pid=PID, source_sid=self.source["id"], starter=STARTER, sentinels=SENTINELS,
                    now=self.now_ms, data_dir=str(self.data), pid_process=os.getpid(),
                    mocked=["java", "ide", "coach", "stt", "media_subprocess"], external_ai_calls=0)

    def reject_source_upload(self, sid, segment, reject):
        # A control cannot affect another session, server or arbitrary URL.
        if sid != self.source["id"] or not isinstance(segment, str) or not re.fullmatch("[a-f0-9]{32}", segment) or type(reject) is not bool:
            raise ValueError("Only this fixture's synthetic source upload may be held")
        with self.upload_lock:
            if reject:
                self.upload_rejections.add((sid, segment))
            else:
                self.upload_rejections.discard((sid, segment))

    def upload_proof(self):
        with self.upload_lock:
            return copy.deepcopy(self.upload_attempts)

    def insert_source(self, s, events, media=False, catalog=True):
        with closing(sqlite3.connect(self.data / "lab.sqlite3")) as con, con:
            con.execute("INSERT INTO sessions VALUES (?,?,?)", (s["id"], s["started"], dumps(s)))
            for e in events:
                con.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (s["id"], e["id"], e["ms"], e["kind"], e["pid"], dumps(e["payload"])))
            if catalog:
                for p in s["problems"]:
                    con.execute("INSERT OR REPLACE INTO problems VALUES (?,?)", (p["id"], dumps(p)))
        if media:
            for segment, start, end in (("a" * 32, 0, 1000), ("b" * 32, 5000, 6000)):
                self.fake_segment(s["id"], segment, start, end)

    def seed(self, **kwargs):
        s, events = source_state(self.now_ms, **kwargs)
        self.insert_source(s, events)
        return s

    def fake_segment(self, sid, segment, start, end):
        raw = ("P3 fake container " + segment).encode()
        folder = self.data / "sessions" / sid / "media" / "screen" / segment
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "000000.part").write_bytes(raw)
        with closing(sqlite3.connect(self.data / "lab.sqlite3")) as con, con:
            con.execute("INSERT INTO media VALUES (?,?,?,?,?,?,?)", (sid, "screen", segment, 0, start, "video/webm", len(raw)))
            for ms, kind, payload in ((start, "recording_start", dict(kind="screen", segment=segment)),
                                      (end, "recording_stop", dict(kind="screen", segment=segment, chunks=1, state="normal"))):
                con.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (sid, uuid.uuid4().hex, ms, kind, PID, dumps(payload)))

    def sql(self, command, args=()):
        with closing(sqlite3.connect(self.data / "lab.sqlite3", timeout=20)) as con, con:
            return con.execute(command, args).fetchall()

    def raw_snapshot(self, sid=None):
        result = {"tables": {}, "media_hashes": {}}
        with closing(sqlite3.connect(self.data / "lab.sqlite3")) as con:
            for table in LEGACY_TABLES:
                clause = " WHERE id=?" if sid and table in {"sessions", "jobs"} else " WHERE sid=?" if sid and table in {"events", "media"} else ""
                # jobs have id=j-id, sid=session-id.
                if sid and table == "jobs":
                    clause = " WHERE sid=?"
                if sid and table == "problems":
                    continue
                result["tables"][table] = con.execute("SELECT * FROM " + table + clause + " ORDER BY rowid", (sid,) if clause else ()).fetchall()
            result["legacy_schema"] = con.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?,?) ORDER BY name", LEGACY_TABLES).fetchall()
        root = self.data / "sessions" / sid if sid else self.data / "sessions"
        for file in root.rglob("*.part"):
            result["media_hashes"][str(file.relative_to(self.data))] = hashlib.sha256(file.read_bytes()).hexdigest()
        # JSON normalize tuples to make local and HTTP proof values identical.
        return json.loads(dumps(result))

    def pending_submission(self, sid, ms=1000):
        rid, jid = uuid.uuid4().hex, uuid.uuid4().hex
        self.st.event(sid, "submit", dict(run_id=rid, code="fixture pending"), PID, ms=ms)
        job = dict(id=jid, sid=sid, kind="java", status="queued", created=self.now_ms,
                   text="", error=None, result=None)
        self.sql("INSERT INTO jobs VALUES (?,?,?)", (jid, sid, dumps(job)))
        return jid

    def close(self):
        self.java_gate.open()
        self.admission_gate.open()
        if self.http:
            self.http.shutdown()
            self.http.server_close()
        if self.http_thread:
            self.http_thread.join(2)
        if self.lab:
            self.lab.close()
        for worker in self.workers:
            worker.join(5)
        self.stack.close()

    def __exit__(self, *exc):
        self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--browser", action="store_true", help="Omit non-decodable old fake media in DOM scenarios")
    args = parser.parse_args()
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *unused: stop.set())
    with RetryFixture(port=args.port, media=not args.browser) as fixture:
        print(dumps(fixture.handshake()), flush=True)
        stop.wait()


if __name__ == "__main__":
    main()
