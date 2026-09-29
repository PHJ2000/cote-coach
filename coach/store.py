import contextlib
import json
import os
import sqlite3
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
DATA=Path(os.environ.get('COACH_DATA_DIR',ROOT/'data/lab')).resolve()

def now():return int(time.time()*1000)
@contextlib.contextmanager
def db(write=False):
    con=sqlite3.connect(DATA/'lab.sqlite3',timeout=20);con.row_factory=sqlite3.Row
    try:
        if write:con.execute('BEGIN IMMEDIATE')
        yield con
        if write:con.commit()
    except Exception:
        if write:con.rollback()
        raise
    finally:con.close()

def init():
    DATA.mkdir(parents=True,exist_ok=True)
    with db() as con:con.execute('PRAGMA journal_mode=WAL')
    with db(True) as con:
        con.executescript('''CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,created INTEGER,state TEXT);
        CREATE TABLE IF NOT EXISTS events(sid TEXT,id TEXT,ms INTEGER,kind TEXT,pid TEXT,payload TEXT,PRIMARY KEY(sid,id));
        CREATE TABLE IF NOT EXISTS problems(id TEXT PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,sid TEXT,state TEXT);
        CREATE TABLE IF NOT EXISTS media(sid TEXT,kind TEXT,segment TEXT,seq INTEGER,start_ms INTEGER,mime TEXT,size INTEGER,PRIMARY KEY(sid,kind,segment,seq));''')
        for r in con.execute('SELECT id,state FROM jobs').fetchall():
            j=json.loads(r['state'])
            if j['status'] in {'queued','running'}:j.update(status='failed',error='서버가 재시작되어 작업이 중단되었습니다. 저장된 자료로 다시 요청하세요.');con.execute('UPDATE jobs SET state=? WHERE id=?',(dump(j),r['id']))

def dump(obj):return json.dumps(obj,ensure_ascii=False)
def session(sid):
    with db() as con:r=con.execute('SELECT state FROM sessions WHERE id=?',(sid,)).fetchone()
    if not r:raise ValueError('연습 세션을 찾지 못했습니다.')
    return json.loads(r['state'])
def mutate(sid,fn):
    with db(True) as con:
        r=con.execute('SELECT state FROM sessions WHERE id=?',(sid,)).fetchone()
        if not r:raise ValueError('연습 세션을 찾지 못했습니다.')
        s=json.loads(r['state']);fn(s);con.execute('UPDATE sessions SET state=? WHERE id=?',(dump(s),sid))
    return s

def sessions():
    with db() as con:return [json.loads(r['state']) for r in con.execute('SELECT state FROM sessions ORDER BY created DESC')]

def event(sid,kind,payload=None,pid='',ms=None,eid=None):
    import uuid
    if ms is None:ms=max(0,now()-session(sid)['started'])
    with db(True) as con:con.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)',(sid,eid or uuid.uuid4().hex,int(ms),kind,pid,dump(payload or {})))

def events(sid):
    with db() as con:return [dict(r)|{'payload':json.loads(r['payload'])} for r in con.execute('SELECT * FROM events WHERE sid=? ORDER BY ms,rowid',(sid,))]
