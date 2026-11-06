"""Retry-v1 persistence and submission classification, without Lab or IO clients.

Mutations use the supplied connection inside the caller's BEGIN IMMEDIATE.
They never open connections or commit. Local savepoints undo partial inserts
even if the caller catches an error. P4 owns the lifecycle RLock, IDE preparation
outside the transaction, safe session views, and timer registration after commit.
Comparison minutes/hints must come from the existing Lab.metrics(session).
"""

import contextlib
import copy
import json
import re
import unicodedata
import uuid
from datetime import datetime, time, timedelta, timezone as utc_timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .problems import validate_problem


OFFSETS = (1, 3, 7)
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS retry_plans (
        id TEXT PRIMARY KEY NOT NULL, source_sid TEXT NOT NULL,
        pid TEXT NOT NULL, reason TEXT NOT NULL, created_at INTEGER NOT NULL,
        anchor_date TEXT NOT NULL, timezone TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('open','completed')),
        UNIQUE (source_sid,pid))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS retry_open_pid
        ON retry_plans(pid) WHERE status='open'""",
    """CREATE TABLE IF NOT EXISTS retry_items (
        id TEXT PRIMARY KEY NOT NULL,
        plan_id TEXT NOT NULL REFERENCES retry_plans(id),
        offset_days INTEGER NOT NULL CHECK (offset_days IN (1,3,7)),
        due_date TEXT NOT NULL,
        session_id TEXT UNIQUE REFERENCES sessions(id),
        completed_at INTEGER, UNIQUE (plan_id,offset_days))""",
    """CREATE INDEX IF NOT EXISTS retry_due_date
        ON retry_items(due_date,plan_id)""",
)


class RetryError(ValueError):
    """P4 can return ``exc.as_dict()`` with the existing HTTP 400 convention."""

    def __init__(self, code, error, **details):
        super().__init__(error)
        self.code = code
        self.details = details

    def as_dict(self):
        return {"error": str(self), "code": self.code, **self.details}


def init_schema(con):
    # executescript would commit the caller's transaction implicitly.
    for statement in SCHEMA:
        con.execute(statement)


@contextlib.contextmanager
def _atomic(con):
    if not con.in_transaction:
        raise RuntimeError("재도전 쓰기는 호출자의 BEGIN IMMEDIATE가 필요합니다.")
    name = "retry_" + uuid.uuid4().hex
    con.execute("SAVEPOINT " + name)
    try:
        yield
    except Exception:
        con.execute("ROLLBACK TO " + name)
        raise
    finally:
        con.execute("RELEASE " + name)


def _rows(con, sql, args=()):
    cursor = con.execute(sql, args)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _one(con, sql, args=()):
    rows = _rows(con, sql, args)
    return rows[0] if rows else None


def _identifier(value):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= 100
            and not any(unicodedata.category(c).startswith("C") for c in value))


def _reason(value):
    if not isinstance(value, str):
        raise RetryError("INVALID_INPUT", "실패 이유를 1~200자로 입력하세요.")
    # Reject control characters before trimming, including leading NUL/tab.
    if any(unicodedata.category(c) in {"Cc", "Cs"} and c != "\n" for c in value):
        raise RetryError("INVALID_INPUT", "실패 이유에 제어문자를 사용할 수 없습니다.")
    value = value.strip()
    if not 1 <= len(value) <= 200:
        raise RetryError("INVALID_INPUT", "실패 이유를 1~200자로 입력하세요.")
    return value


def _local_time(timezone, now_ms):
    if not isinstance(timezone, str) or not timezone or len(timezone) > 200:
        raise RetryError("INVALID_INPUT", "유효한 IANA 시간대를 선택하세요.")
    if type(now_ms) is not int or now_ms < 0:
        raise RetryError("INVALID_INPUT", "서버 시각을 확인하세요.")
    try:
        return datetime.fromtimestamp(now_ms / 1000, utc_timezone.utc).astimezone(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError, OverflowError, OSError):
        raise RetryError("INVALID_INPUT", "시간대 또는 서버 시각을 확인하세요.") from None


def _calendar(timezone, now_ms):
    local = _local_time(timezone, now_ms)
    try:
        due = [(local.date() + timedelta(days=n)).isoformat() for n in OFFSETS]
        midnight = datetime.combine(local.date() + timedelta(days=1), time.min, local.tzinfo)
        return local.date().isoformat(), due, int(midnight.timestamp() * 1000)
    except (ValueError, OverflowError, OSError):
        raise RetryError("INVALID_INPUT", "예약 날짜 범위를 확인하세요.") from None


def _expected_anchor(value):
    if value is None:  # Internal legacy omission; the request layer rejects JSON null.
        return
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise RetryError("INVALID_INPUT", "확인한 기준 날짜를 YYYY-MM-DD 형식으로 보내세요.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise RetryError("INVALID_INPUT", "확인한 기준 날짜가 유효하지 않습니다.") from None


def _unavailable(cause):
    return RetryError("SOURCE_UNAVAILABLE", "저장된 연습 자료를 확인할 수 없습니다.", cause=cause)


def _session(con, sid):
    row = _one(con, "SELECT state FROM sessions WHERE id=?", (sid,))
    if row is None:
        raise _unavailable("SESSION_MISSING")
    try:
        session = json.loads(row["state"])
    except (TypeError, ValueError):
        raise _unavailable("SESSION_CORRUPT") from None
    if (not isinstance(session, dict) or session.get("id") != sid
            or not isinstance(session.get("status"), str)
            or session["status"] not in {"active", "completed"}
            or not isinstance(session.get("problems"), list)
            or not isinstance(session.get("runs"), list)
            or not isinstance(session.get("messages"), list)
            or type(session.get("started")) is not int or session["started"] < 0):
        raise _unavailable("SESSION_CORRUPT")
    ended = session.get("ended")
    if (ended is not None and (type(ended) is not int or ended < session["started"])) or (session["status"] == "completed" and ended is None):
        raise _unavailable("SESSION_CORRUPT")
    return session


@lru_cache(maxsize=32)
def _validate_snapshot(raw):
    # Generated performance fixtures can contain 100,000 inputs. Validate an
    # identical snapshot once; any changed field gets a different cache key.
    problem = json.loads(raw)
    validate_problem(problem)
    # Import validation normalizes a copy. A saved snapshot must already have
    # the fields read directly by public_problem and the Java runner; otherwise
    # starting it can commit a session that cannot be viewed or graded.
    for test in problem["tests"]:
        if type(test.get("public")) is not bool:
            raise ValueError("저장된 테스트의 공개 여부가 필요합니다.")
        if "generate" in test and test["public"]:
            raise ValueError("생성형 테스트는 비공개여야 합니다.")
        if "time_limit_ms" in test and type(test["time_limit_ms"]) is not int:
            raise ValueError("저장된 테스트의 시간 제한을 확인하세요.")


def _problem(session, pid):
    matches = [p for p in session["problems"] if isinstance(p, dict) and p.get("id") == pid]
    if len(matches) != 1:
        raise _unavailable("PROBLEM_MISSING")
    try:
        # Reuse the current runner's validator; discard its normalized copy/new id.
        _validate_snapshot(json.dumps(matches[0], ensure_ascii=False, sort_keys=True))
    except (ValueError, TypeError, KeyError, OverflowError):
        raise _unavailable("PROBLEM_CORRUPT") from None
    return matches[0]


def _defaults(session):
    if (not isinstance(session.get("mode"), str)
            or session["mode"] not in {"training", "exam"}
            or not isinstance(session.get("resources"), str)
            or session["resources"] not in {"none", "java-docs", "search"}
            or type(session.get("duration")) is not int
            or not 5 <= session["duration"] <= 300):
        raise _unavailable("SETTINGS_CORRUPT")
    return {key: session[key] for key in ("mode", "duration", "resources")}


def _attempt_records(con, sid):
    events = _rows(con, "SELECT rowid AS rowid,* FROM events WHERE sid=? ORDER BY ms,rowid", (sid,))
    jobs = _rows(con, "SELECT state FROM jobs WHERE sid=?", (sid,))
    try:
        for event in events:
            event["payload"] = json.loads(event["payload"])
        jobs = [json.loads(job["state"]) for job in jobs]
    except (TypeError, ValueError):
        raise _unavailable("ATTEMPT_CORRUPT") from None
    return events, jobs


def classify_attempt(session, events, jobs, pid):
    """Return outcomes/eligibility without codes, reports, or elapsed-time maths.

    ``events`` should be ordered by (ms,rowid), or include integer ``rowid``.
    st.events already preserves submit arrival order and may omit rowid.
    ``result_basis`` is submit_events or legacy_runs. P4 merges Lab.metrics
    minutes/hints and the session conditions into comparison DTOs.
    """
    if (not isinstance(session, dict) or not _identifier(pid)
            or type(session.get("duration")) is not int or session["duration"] <= 0
            or not isinstance(session.get("runs"), list)
            or not isinstance(session.get("problems"), list)
            or not isinstance(session.get("status"), str)
            or session["status"] not in {"active", "completed"}
            or not isinstance(events, (list, tuple)) or not isinstance(jobs, (list, tuple))):
        raise _unavailable("ATTEMPT_CORRUPT")
    if not any(isinstance(p, dict) and p.get("id") == pid for p in session.get("problems", [])):
        raise _unavailable("PROBLEM_MISSING")
    pending = False
    for job in jobs:
        if not isinstance(job, dict):
            raise _unavailable("JOB_CORRUPT")
        if job.get("kind") == "java":
            if not isinstance(job.get("status"), str):
                raise _unavailable("JOB_CORRUPT")
            if job.get("status") in {"queued", "running"}:
                pending = True
            elif job.get("status") not in {"completed", "failed", "cancelled", "canceled"}:
                raise _unavailable("JOB_CORRUPT")
    runs = {}
    legacy = []
    for index, run in enumerate(session["runs"]):
        if not isinstance(run, dict):
            raise _unavailable("RUN_CORRUPT")
        if run.get("pid") != pid or run.get("submit") is not True:
            continue
        if type(run.get("ms")) is not int or run["ms"] < 0:
            raise _unavailable("RUN_CORRUPT")
        rid = run.get("id")
        if rid is not None and not isinstance(rid, str):
            raise _unavailable("RUN_CORRUPT")
        if rid in runs:
            raise _unavailable("RUN_DUPLICATE")
        if isinstance(rid, str):
            runs[rid] = run
        legacy.append((run["ms"], index, rid, run))
    submits = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise _unavailable("EVENT_CORRUPT")
        if event.get("pid") != pid or event.get("kind") != "submit":
            continue
        if type(event.get("ms")) is not int or event["ms"] < 0 or not isinstance(event.get("payload"), dict):
            raise _unavailable("EVENT_CORRUPT")
        rowid = event.get("rowid", index)
        if type(rowid) is not int:
            raise _unavailable("EVENT_CORRUPT")
        rid = event["payload"].get("run_id")
        submits.append((event["ms"], rowid, rid, runs.get(rid) if isinstance(rid, str) else None))
    basis = "submit_events" if submits else "legacy_runs"
    submissions = sorted(submits or legacy, key=lambda item: item[:2])
    deadline = session["duration"] * 60000
    on_time = [item for item in submissions if item[0] <= deadline]
    overtime = [item for item in submissions if item[0] > deadline]

    def outcome(items):
        if not items:
            return "not_submitted", None, None, None
        _, _, rid, run = items[-1]
        result = run.get("result") if run else None
        status = result.get("status") if isinstance(result, dict) else None
        if not isinstance(status, str):
            status = None
        classification = {"passed": "passed", "failed": "failed", "error": "grader_error"}.get(status, "ungraded")
        counts = [result.get(key) if isinstance(result, dict) else None for key in ("passed", "total")]
        counts = [value if type(value) is int and value >= 0 else None for value in counts]
        return classification, rid if isinstance(rid, str) else None, *counts

    result, rid, passed, total = outcome(on_time)
    overtime_result, _, _, _ = outcome(overtime)
    if session.get("status") != "completed":
        code = "SOURCE_ACTIVE"
    elif pending:
        code = "GRADING_PENDING"
    elif result == "passed":
        code = "NOT_ELIGIBLE"
    elif result in {"grader_error", "ungraded"}:
        code = "GRADE_UNAVAILABLE"
    else:
        code = None
    return {"outcome": result, "overtime_outcome": overtime_result, "run_id": rid,
            "passed": passed, "total": total, "result_basis": basis,
            "grading_pending": pending, "eligible": code is None, "code": code}


def _plan(con, plan_id):
    plan = _one(con, "SELECT * FROM retry_plans WHERE id=?", (plan_id,))
    if plan is None:
        raise RetryError("INVALID_INPUT", "재도전 예약을 찾을 수 없습니다.")
    return plan


def _items(con, plan):
    items = _rows(con, "SELECT * FROM retry_items WHERE plan_id=? ORDER BY offset_days,id", (plan["id"],))
    if tuple(item["offset_days"] for item in items) != OFFSETS:
        raise _unavailable("PLAN_CORRUPT")
    try:
        anchor = datetime.strptime(plan["anchor_date"], "%Y-%m-%d").date()
        if anchor.isoformat() != plan["anchor_date"]:
            raise ValueError()
        if any(item["due_date"] != (anchor + timedelta(days=item["offset_days"])).isoformat() for item in items):
            raise ValueError()
    except (TypeError, ValueError, OverflowError):
        raise _unavailable("PLAN_CORRUPT") from None
    return items


def _linked_session(con, plan, item):
    session = _session(con, item["session_id"])
    expected = {"plan_id": plan["id"], "item_id": item["id"], "source_sid": plan["source_sid"], "pid": plan["pid"]}
    if session.get("retry") != expected or len(session["problems"]) != 1:
        raise _unavailable("RELATION_CORRUPT")
    _problem(session, plan["pid"])
    _defaults(session)
    return session


def _item_state(con, plan, item):
    if item["session_id"] is None:
        if item["completed_at"] is not None:
            raise _unavailable("RELATION_CORRUPT")
        return "scheduled"
    session = _linked_session(con, plan, item)
    events, jobs = _attempt_records(con, session["id"])
    attempt = classify_attempt(session, events, jobs, plan["pid"])
    if session["status"] == "active":
        return "active"
    return "grading" if attempt["grading_pending"] else "completed"


def first_open_item(con, plan_id):
    """First unfinished row plus authoritative state; unlinked rows are scheduled.

    No writes, no implicit reconciliation. Missing/corrupt linked records remain
    the first unavailable item and block later rounds, irrespective of caches.
    """
    plan = _plan(con, plan_id)
    for item in _items(con, plan):
        try:
            state = _item_state(con, plan, item)
        except RetryError as exc:
            return {**item, "state": "unavailable", "code": exc.code, **exc.details}
        if state != "completed":
            return {**item, "state": state}
    return None


def reconcile(con, plan_id, now_ms):
    """Refresh only retry caches in the caller's lifecycle/write boundary."""
    with _atomic(con):
        plan = _plan(con, plan_id)
        _local_time(plan["timezone"], now_ms)
        items = _items(con, plan)
        states = []
        for item in items:
            try:
                state = _item_state(con, plan, item)
            except RetryError:
                state = "unavailable"
            states.append(state)
            if state == "completed" and item["completed_at"] is None:
                con.execute("UPDATE retry_items SET completed_at=? WHERE id=?", (now_ms, item["id"]))
            elif state != "completed" and item["completed_at"] is not None:
                con.execute("UPDATE retry_items SET completed_at=NULL WHERE id=?", (item["id"],))
        status = "completed" if all(state == "completed" for state in states) else "open"
        if status != plan["status"]:
            # The partial unique index also guards stale-cache reopening.
            other = _one(con, "SELECT id FROM retry_plans WHERE pid=? AND status='open' AND id<>?", (plan["pid"], plan_id))
            if status == "open" and other:
                raise RetryError("PLAN_EXISTS", "이 문제의 열린 예약이 있습니다.", plan_id=other["id"])
            con.execute("UPDATE retry_plans SET status=? WHERE id=?", (status, plan_id))
        return {**plan, "status": status}


def _plan_dto(con, plan_id, now_ms):
    plan = _plan(con, plan_id)
    today = _local_time(plan["timezone"], now_ms).date().isoformat()
    plan["items"] = []
    for item in _items(con, plan):
        state = _item_state(con, plan, item)
        if state == "scheduled" and item["due_date"] <= today:
            state = "due"
        plan["items"].append({key: item[key] for key in ("id", "offset_days", "due_date", "session_id")} | {"state": state})
    return plan


def create_plan(con, source, pid, reason, timezone, now_ms, *, expected_anchor_date=None):
    """Register or replay a natural key inside the caller's write transaction.

    Legacy helpers may pass an integer now_ms. Production passes its clock
    callable, sampled for the date guard after the write lock and eligibility
    checks. Replays do not revalidate the original or compare the expected date.
    """
    source_sid = source.get("id") if isinstance(source, dict) else source
    if not _identifier(source_sid) or not _identifier(pid):
        raise RetryError("INVALID_INPUT", "원본 연습과 문제를 확인하세요.")
    reason = _reason(reason)
    _local_time(timezone, 0)  # Validate the timezone without sampling the guard clock.
    _expected_anchor(expected_anchor_date)
    if not callable(now_ms):
        _local_time(timezone, now_ms)
    clock_value = lambda: now_ms() if callable(now_ms) else now_ms
    with _atomic(con):
        existing = _one(con, "SELECT * FROM retry_plans WHERE source_sid=? AND pid=?", (source_sid, pid))
        if existing:
            response = {"created": False, "plan": _plan_dto(con, existing["id"], clock_value())}
            if existing["reason"] != reason or existing["timezone"] != timezone:
                response["payload_mismatch"] = True
            return response
        # Recover stale completion hooks before the other-source conflict check.
        # These cache writes share the savepoint and roll back on a date mismatch.
        previous = _rows(con, "SELECT id FROM retry_plans WHERE pid=?", (pid,))
        if previous:
            reconcile_now = clock_value()
            for plan in previous:
                reconcile(con, plan["id"], reconcile_now)
        existing = _one(con, "SELECT id FROM retry_plans WHERE pid=? AND status='open'", (pid,))
        if existing:
            raise RetryError("PLAN_EXISTS", "이 문제의 열린 예약이 있습니다.", plan_id=existing["id"])
        if _one(con, "SELECT id FROM sessions WHERE id=?", (source_sid,)) is None:
            raise RetryError("INVALID_INPUT", "원본 연습을 찾을 수 없습니다.")
        session = _session(con, source_sid)
        if not any(isinstance(p, dict) and p.get("id") == pid for p in session["problems"]):
            raise RetryError("INVALID_INPUT", "원본 연습에 없는 문제입니다.")
        _problem(session, pid)
        _defaults(session)
        events, jobs = _attempt_records(con, source_sid)
        attempt = classify_attempt(session, events, jobs, pid)
        if not attempt["eligible"]:
            raise RetryError(attempt["code"], "완료된 실패 또는 미제출 시도만 예약할 수 있습니다.")
        guard_now = clock_value()
        anchor, dates, _ = _calendar(timezone, guard_now)
        if expected_anchor_date is not None and expected_anchor_date != anchor:
            raise RetryError("ANCHOR_DATE_CHANGED", "기준 날짜가 바뀌어 예약하지 않았습니다. 새 예약 날짜를 확인해 주세요.",
                             expected_anchor_date=expected_anchor_date, current_anchor_date=anchor,
                             timezone=timezone, server_now=guard_now,
                             preview=[{"offset_days":offset,"due_date":date} for offset,date in zip(OFFSETS,dates)])
        plan_id = uuid.uuid4().hex
        con.execute("INSERT INTO retry_plans VALUES (?,?,?,?,?,?,?,?)", (plan_id, source_sid, pid, reason, guard_now, anchor, timezone, "open"))
        for offset, date in zip(OFFSETS, dates):
            con.execute("INSERT INTO retry_items VALUES (?,?,?,?,NULL,NULL)", (uuid.uuid4().hex, plan_id, offset, date))
        return {"created": True, "plan": _plan_dto(con, plan_id, guard_now)}


def _active_sessions(con):
    active = []
    for row in _rows(con, "SELECT id,state FROM sessions"):
        try:
            state = json.loads(row["state"])
        except (TypeError, ValueError):
            continue  # An unrelated damaged archive does not hide healthy plans.
        if isinstance(state, dict) and state.get("status") == "active":
            active.append(row["id"])
    return active


def link_started_session(con, item_id, session):
    """Insert the fresh state, one start event, and link atomically; return raw state.

    P4 prepares IDE and a fresh single-problem candidate before BEGIN IMMEDIATE,
    with started sampled after preparation. It must call Lab.view on the returned
    sid after commit; raw state includes hidden tests/tags and is never an API view.
    Existing linked sids are returned before due/order/other-active checks.
    """
    if not _identifier(item_id) or not isinstance(session, dict):
        raise RetryError("INVALID_INPUT", "재도전 회차를 확인하세요.")
    with _atomic(con):
        item = _one(con, "SELECT * FROM retry_items WHERE id=?", (item_id,))
        if not item:
            raise RetryError("INVALID_INPUT", "재도전 회차를 찾을 수 없습니다.")
        plan = _plan(con, item["plan_id"])
        if item["session_id"] is not None:
            existing = _linked_session(con, plan, item)
            reconcile(con, plan["id"], session.get("started", existing["started"]))
            response = {"created": False, "session": existing}
            keys = ("mode", "kind", "duration", "resources", "model", "effort")
            if any(key in session and session[key] != existing.get(key, "") for key in keys):
                response["payload_mismatch"] = True
            return response
        now_ms = session.get("started")
        reconcile(con, plan["id"], now_ms)
        source = _session(con, plan["source_sid"])
        problem = _problem(source, plan["pid"])
        _defaults(source)
        if source["status"] != "completed":
            raise _unavailable("SOURCE_ACTIVE")
        today = _local_time(plan["timezone"], now_ms).date().isoformat()
        if item["due_date"] > today:
            raise RetryError("NOT_DUE", "예약 날짜가 아직 오지 않았습니다.")
        first = first_open_item(con, plan["id"])
        if first and first["state"] == "unavailable":
            raise _unavailable(first.get("cause", "RELATION_CORRUPT"))
        if first is None or first["id"] != item_id:
            raise RetryError("PREVIOUS_PENDING", "먼저 이전 회차를 완료하세요.")
        active = _active_sessions(con)
        if active:
            raise RetryError("ACTIVE_SESSION_EXISTS", "진행 중인 연습을 먼저 이어서 완료하세요.", session_id=active[0])
        _defaults(session)
        expected = {"plan_id": plan["id"], "item_id": item_id, "source_sid": plan["source_sid"], "pid": plan["pid"]}
        if (not _identifier(session.get("id")) or session["id"] == source["id"]
                or session.get("status") != "active" or session.get("kind") != "single"
                or session.get("ended") is not None or session.get("retry") != expected
                or session.get("problems") != [problem]
                or session.get("codes") != {plan["pid"]: problem["starter"]}
                or session.get("deadline") != now_ms + session["duration"] * 60000
                or any(session.get(key) != [] for key in ("runs", "messages", "transcripts"))
                or any(session.get(key) is not None for key in ("report", "thread", "deadline_codes"))
                or any(session.get(key) for key in ("review_thread", "frames", "code_versions", "analysis_inputs",
                                                    "report_history", "transcription_state", "past_reports", "media", "events", "jobs"))):
            raise _unavailable("FRESH_SESSION_INVALID")
        raw = json.dumps(session, ensure_ascii=False)
        con.execute("INSERT INTO sessions(id,created,state) VALUES (?,?,?)", (session["id"], now_ms, raw))
        payload = json.dumps({"mode": session["mode"], "resources": session["resources"]}, ensure_ascii=False)
        con.execute("INSERT INTO events(sid,id,ms,kind,pid,payload) VALUES (?,?,0,'start','',?)", (session["id"], uuid.uuid4().hex, payload))
        linked = con.execute("UPDATE retry_items SET session_id=? WHERE id=? AND session_id IS NULL", (session["id"], item_id))
        if linked.rowcount != 1:
            raise _unavailable("RELATION_CORRUPT")
        return {"created": True, "session": copy.deepcopy(session)}


def list_cards(con, now_ms):
    """Read-only, safe cards from authoritative rows, never source review content."""
    _local_time("Etc/UTC", now_ms)
    response = {"server_now": now_ms, "next_refresh_at": None,
                "capabilities": {"retry_plan_expected_anchor_date": True},
                **{key: [] for key in ("due", "active", "grading", "unavailable", "upcoming")}}
    active = _active_sessions(con)
    refreshes = []
    # Include closed caches too: actual states may have changed after a missed hook.
    plans = _rows(con, "SELECT * FROM retry_plans ORDER BY created_at,id")
    for plan in plans:
        item = None
        try:
            item = first_open_item(con, plan["id"])
            if item is None:
                continue
            if item["state"] == "unavailable":
                raise _unavailable(item.get("cause", "RELATION_CORRUPT"))
            source = _session(con, plan["source_sid"])
            problem = _problem(source, plan["pid"])
            defaults = _defaults(source)
            if source["status"] != "completed":
                raise _unavailable("SOURCE_ACTIVE")
            today, _, refresh = _calendar(plan["timezone"], now_ms)
            refreshes.append(refresh)
            state = item["state"]
            if state == "scheduled" and item["due_date"] <= today:
                state = "due"
            outstanding = 0
            for other in _items(con, plan):
                if other["due_date"] <= today and _item_state(con, plan, other) != "completed":
                    outstanding += 1
            card = {"plan_id": plan["id"], "item_id": item["id"], "pid": plan["pid"],
                    "title": problem["title"], "level": problem["level"],
                    "offset_days": item["offset_days"], "due_date": item["due_date"],
                    "timezone": plan["timezone"], "today": today, "state": state,
                    "overdue_days": max(0, (datetime.strptime(today, "%Y-%m-%d").date() - datetime.strptime(item["due_date"], "%Y-%m-%d").date()).days),
                    "outstanding_due_count": outstanding, "session_id": item["session_id"],
                    "can_start": state == "active" or (state == "due" and not active),
                    "defaults": defaults}
            response["upcoming" if state == "scheduled" else state].append(card)
        except RetryError as exc:
            response["unavailable"].append({"plan_id": plan["id"], "item_id": item["id"] if item else None,
                                            "pid": plan["pid"], "state": "unavailable", "can_start": False,
                                            "code": "SOURCE_UNAVAILABLE", **exc.details})
    if refreshes:
        response["next_refresh_at"] = min(refreshes)
    by_id = {plan["id"]: plan for plan in plans}
    for key in ("due", "active", "grading", "upcoming"):
        response[key].sort(key=lambda card: (card["due_date"], by_id[card["plan_id"]]["created_at"], card["item_id"]))
    return response
