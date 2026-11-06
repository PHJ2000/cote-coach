import copy
import contextlib
import statistics
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from functools import wraps
from pathlib import Path
from . import store as st
from . import retries
from .timing import valid_offset
from .recordings import groups as recording_groups, gaps as recording_gaps
from .problems import BUILTINS, public_problem, validate_problem
from .java import run_java
from .codex import CodexCoach, SYSTEM, SCHEMA, validate_report
from .speech import Speech
from .ide import JavaIDE
from .evidence import code_evolution, conversations, hint_followups, transcript_evidence

def retry_lifecycle(fn):
    @wraps(fn)
    def locked(self,*args,**kwargs):
        with self.retry_lock:return fn(self,*args,**kwargs)
    return locked

class Lab:
    def __init__(self):
        st.init();self.codex=CodexCoach(st.DATA);self.speech=Speech(st.ROOT);self.ide=JavaIDE(st.ROOT,st.DATA)
        self.jobs={};self.jobs_lock=threading.Lock();self.timers={};self.deadline_lock=threading.Lock();self.media_lock=threading.Lock()
        # Acquisition order: lifecycle RLock -> deadline_lock -> SQLite.
        # IDE preparation and Java execution never run under these locks.
        self.retry_lock=threading.RLock();self.retry_reconcile_errors=[]
        self.transcription_lock=threading.Lock()
        with self.retry_lock,st.db(True) as con:
            for row in con.execute('SELECT id FROM retry_plans').fetchall():
                try:retries.reconcile(con,row['id'],st.now())
                except retries.RetryError as exc:self.retry_reconcile_errors.append({'plan_id':row['id'],'code':exc.code})
        for s in st.sessions():
            if s['status']=='active':self.schedule(s)
    def all_problems(self):
        with st.db() as con:return copy.deepcopy(BUILTINS)+[json.loads(r['payload']) for r in con.execute('SELECT payload FROM problems')]
    def metrics(self,s):
        with st.db() as con:events=[dict(r)|{'payload':json.loads(r['payload'])} for r in con.execute("SELECT ms,kind,pid,CASE WHEN kind='switch' THEN payload ELSE '{}' END AS payload FROM events WHERE sid=? AND kind IN ('switch','code') ORDER BY ms,rowid",(s['id'],))]
        end=max(0,(s['ended'] or st.now())-s['started']);spent={p['id']:0 for p in s['problems']};current=s['problems'][0]['id'];last=0
        for e in events:
            if e['kind']=='switch' and e['payload'].get('to') in spent:
                at=min(end,e['ms']);spent[current]+=max(0,at-last);last=at;current=e['payload']['to']
        spent[current]+=max(0,end-last)
        result=[]
        for p in s['problems']:
            runs=[r for r in s['runs'] if r['pid']==p['id']];submits=[r for r in runs if r['submit'] and not r['overtime']];submits.sort(key=lambda r:r['ms']);hints=sum(m['kind']=='hint' and m.get('pid')==p['id'] for m in s['messages'])
            edits=[e for e in events if e['kind']=='code' and e['pid']==p['id']]
            result.append({'pid':p['id'],'title':p['title'],'level':p['level'],'minutes':round(spent[p['id']]/60000,1),'first_edit_ms':edits[0]['ms'] if edits else None,'first_run_ms':runs[0]['ms'] if runs else None,'runs':len(runs),'hints':hints,'passed_in_time':bool(submits and submits[-1]['result']['status']=='passed'),'independent':bool(submits and submits[-1]['result']['status']=='passed' and not hints)})
        return result
    def dashboard(self):
        sessions=st.sessions();tags={};reports=[];completed=[]
        for s in sessions:
            if s.get('report'):
                reports.append({'id':s['id'],'title':s['title'],'report':s['report']})
                for w in s['report'].get('weaknesses',[]):tags[w['tag']]=tags.get(w['tag'],0)+1
            if s['status']=='completed':completed.append(s)
        problems=self.all_problems()
        seen={}
        for s in reversed(completed):
            metrics={x['pid']:x for x in self.metrics(s)}
            for p in s['problems']:
                metric=metrics[p['id']]
                seen.setdefault(p['id'],metric|{'session':s['id'],'date':s['started'],'passed':metric['passed_in_time']})
        recommendations=[]
        latest={}
        for s in reversed(completed):
            for p in s['problems']:latest[p['id']]=(s,p)
        for p in problems:
            weak=sum(tags.get(t,0) for t in p['tags']);reason='Lv.1 기본기 확인' if p['level']==1 else 'Lv.2 접근법 연습';score=weak*5+(2 if p['level']==2 else 1)
            if weak:reason='복기에서 확인한 '+', '.join(t for t in p['tags'] if tags.get(t))+' 연습'
            if p['id'] in latest:
                s,_=latest[p['id']];passed=any(r['pid']==p['id'] and r['submit'] and r['result']['status']=='passed' for r in s['runs']);days=(st.now()-s['ended'])/86400000
                if days>=(3 if passed else 1):score+=4;reason='간격을 두고 다시 풀 문제 · '+('3일' if passed else '1일')+' 이상 경과'
                else:score-=8;reason='최근에 푼 문제 · 새 문제와 함께 비교하세요'
            recommendations.append({'problem':public_problem(p),'score':score,'reason':reason})
        recommendations.sort(key=lambda x:-x['score'])
        cohorts=[]
        for level in range(6):
            g=[v for v in seen.values() if v['level']==level]
            if g:cohorts.append({'level':level,'count':len(g),'independent_rate':round(100*sum(v['independent'] for v in g)/len(g)),'median_minutes':round(statistics.median(v['minutes'] for v in g),1),'average_hints':round(statistics.mean(v['hints'] for v in g),1)})
        history=[{k:s.get(k) for k in ['id','title','started','ended','status','mode','kind','duration','resources','report','model','effort']}|{'problem_count':len(s['problems']),'runs':len(s['runs'])} for s in sessions]
        return {'cohorts':cohorts,'sessions':history,'problems':[public_problem(p) for p in problems],'recommendations':recommendations,'weaknesses':tags,'reports':reports[:5],'stats':{'sessions':len(completed),'problems':len(seen),'independent':sum(v['passed'] and not v['hints'] for v in seen.values()),'days':len({s['started']//86400000 for s in completed})}}
    def health(self):
        try:docker=subprocess.run(['docker','image','inspect',run_java.__globals__['IMAGE']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=4).returncode==0
        except Exception:docker=False
        return {'java':docker,'ide':(st.ROOT/'runtime/jdtls/plugins').exists() and (st.ROOT/'runtime/jdk25/bin/java').exists(),'speech':bool(importlib.util.find_spec('faster_whisper')) and any((st.ROOT/'runtime/whisper').glob('*/snapshots/*/model.bin')),'codex':bool(shutil.which('codex')),'data_dir':str(st.DATA)}
    @staticmethod
    def session_settings(d,count):
        if d.get('mode') not in {'exam','training'} or d.get('kind') not in {'single','mock'} or d.get('resources') not in {'none','java-docs','search'}:raise ValueError('연습 설정을 확인하세요.')
        if d['kind']=='single' and count!=1:raise ValueError('한 문제 연습은 문제 하나만 선택하세요.')
        duration=d.get('duration')
        if type(duration) is not int or not 5<=duration<=300:raise ValueError('제한 시간은 5~300분입니다.')
        return {k:d[k] for k in ('mode','kind','resources','duration')}|{'model':str(d.get('model',''))[:100],'effort':str(d.get('effort',''))[:30]}
    def prepare_session(self,sid,problems,settings):
        ide_warning=None
        if settings['mode']=='training':
            try:
                for p in problems:self.ide.prepare('s'+sid+'_'+p['id'],p['starter'])
            except (ValueError,RuntimeError,OSError) as exc:
                # Editing/recording remain available if optional IDE support fails.
                ide_warning=str(exc)[:300]
        return ide_warning
    @staticmethod
    def fresh_session(sid,problems,settings,start):
        return {'id':sid,'title':('모의 코테' if len(problems)>1 else problems[0]['title']),'started':start,'ended':None,'status':'active',**settings,'deadline':start+settings['duration']*60000,'deadline_codes':None,'problems':copy.deepcopy(problems),'codes':{p['id']:p['starter'] for p in problems},'runs':[],'messages':[],'report':None,'transcripts':[],'thread':None,'media_expected':True}
    def new_session(self,d):
        ids=d.get('problem_ids');problems={p['id']:p for p in self.all_problems()}
        if not isinstance(ids,list) or not 1<=len(ids)<=5 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids) or any(i not in problems for i in ids):raise ValueError('문제를 1~5개 선택하세요.')
        settings=self.session_settings(d,len(ids));sid=uuid.uuid4().hex;selected=[problems[i] for i in ids]
        ide_warning=self.prepare_session(sid,selected,settings)
        start=st.now() # Preparation must never consume practice time.
        s=self.fresh_session(sid,selected,settings,start)
        with st.db(True) as con:con.execute('INSERT INTO sessions VALUES (?,?,?)',(sid,start,st.dump(s)))
        st.event(sid,'start',{'mode':s['mode'],'resources':s['resources']},ms=0);self.schedule(s)
        result=self.view(sid)
        if ide_warning:result['ide_warning']=ide_warning
        return result
    def retry_plan(self,d):
        if 'expected_anchor_date' in d and d['expected_anchor_date'] is None:raise retries.RetryError('INVALID_INPUT','확인한 기준 날짜가 필요합니다.')
        with self.retry_lock,st.db(True) as con:
            return retries.create_plan(con,d.get('source_sid'),d.get('pid'),d.get('reason'),d.get('timezone'),st.now,expected_anchor_date=d.get('expected_anchor_date'))
    def retry_list(self):
        with self.retry_lock,st.db() as con:return retries.list_cards(con,st.now())
    def retry_start(self,d):
        item_id=d.get('item_id')
        if not retries._identifier(item_id):raise retries.RetryError('INVALID_INPUT','재도전 회차를 확인하세요.')
        try:
            if d.get('kind')!='single':raise ValueError('재도전은 한 문제 연습입니다.')
            settings=self.session_settings(d,1)
        except (ValueError,TypeError):raise retries.RetryError('INVALID_INPUT','재도전 설정을 확인하세요.') from None
        # Preflight and response-loss recovery. No IDE/network work in a DB transaction.
        with self.retry_lock,st.db(True) as con:
            item=retries._one(con,'SELECT * FROM retry_items WHERE id=?',(item_id,))
            if item is None:raise retries.RetryError('INVALID_INPUT','재도전 회차를 찾을 수 없습니다.')
            plan=retries._plan(con,item['plan_id']);now=st.now()
            if item['session_id'] is not None:
                result=retries.link_started_session(con,item_id,settings|{'started':now})
                problem=None
            else:
                retries.reconcile(con,plan['id'],now)
                source=retries._session(con,plan['source_sid']);problem=copy.deepcopy(retries._problem(source,plan['pid']))
                retries._defaults(source)
                if source['status']!='completed':raise retries._unavailable('SOURCE_ACTIVE')
                if item['due_date']>retries._local_time(plan['timezone'],now).date().isoformat():raise retries.RetryError('NOT_DUE','예약 날짜가 아직 오지 않았습니다.')
                first=retries.first_open_item(con,plan['id'])
                if first and first['state']=='unavailable':raise retries._unavailable(first.get('cause','RELATION_CORRUPT'))
                if not first or first['id']!=item_id:raise retries.RetryError('PREVIOUS_PENDING','먼저 이전 회차를 완료하세요.')
                active=retries._active_sessions(con)
                if active:raise retries.RetryError('ACTIVE_SESSION_EXISTS','진행 중인 연습을 먼저 완료하세요.',session_id=active[0])
        warning=None
        if problem is not None:
            sid=uuid.uuid4().hex;warning=self.prepare_session(sid,[problem],settings)
            with self.retry_lock,st.db(True) as con:
                candidate=self.fresh_session(sid,[problem],settings,st.now())
                candidate['retry']={'plan_id':plan['id'],'item_id':item_id,'source_sid':plan['source_sid'],'pid':plan['pid']}
                result=retries.link_started_session(con,item_id,candidate)
        stored=result['session'];sid=stored['id']
        with self.retry_lock:
            if stored['status']=='active' and sid not in self.timers:self.schedule(stored)
            result['session']=self.view(sid)
        if warning and result['created']:result['ide_warning']=warning
        return result
    def reconcile_session(self,con,sid):
        row=con.execute('SELECT state FROM sessions WHERE id=?',(sid,)).fetchone()
        if not row:return
        retry=json.loads(row['state']).get('retry')
        if retry:
            try:retries.reconcile(con,retry['plan_id'],st.now())
            except retries.RetryError as exc:self.retry_reconcile_errors.append({'plan_id':retry['plan_id'],'code':exc.code})
    def retry_candidates(self,con,s):
        events,jobs=retries._attempt_records(con,s['id']);candidates=[]
        for p in s['problems']:
            try:
                attempt=retries.classify_attempt(s,events,jobs,p['id']);defaults=retries._defaults(s)
                existing=retries._one(con,"SELECT id FROM retry_plans WHERE (source_sid=? AND pid=?) OR (pid=? AND status='open') ORDER BY source_sid=? DESC LIMIT 1",(s['id'],p['id'],p['id'],s['id']))
                candidate={'pid':p['id'],'eligible':attempt['eligible'] and not bool(existing),'code':'PLAN_EXISTS' if existing else attempt['code'],'defaults':defaults}
                if existing:candidate['existing_plan_id']=existing['id']
            except retries.RetryError as exc:candidate={'pid':p['id'],'eligible':False,'code':exc.code,'defaults':{}}
            candidates.append(candidate)
        return candidates
    def retry_attempt_summary(self,con,s,pid):
        events,jobs=retries._attempt_records(con,s['id']);attempt=retries.classify_attempt(s,events,jobs,pid)
        # Classification consumes submit events; metrics also consumes switches
        # and edit times. Validate those saved shapes before calling metrics,
        # so source damage is caught by the source-only comparison boundary.
        for event in events:
            if event['kind'] in {'switch','code'} and (type(event['ms']) is not int or event['ms']<0):raise retries._unavailable('EVENT_CORRUPT')
            if event['kind']=='switch':
                payload=event['payload']
                if not isinstance(payload,dict) or (payload.get('to') is not None and not isinstance(payload['to'],str)):raise retries._unavailable('EVENT_CORRUPT')
        try:metric=next(m for m in self.metrics(s) if m['pid']==pid)
        except (KeyError,TypeError,ValueError,StopIteration):metric={}
        return {k:attempt[k] for k in ('outcome','overtime_outcome','run_id','passed','total','result_basis')}|retries._defaults(s)|{'minutes':metric.get('minutes'),'hints':metric.get('hints')}
    def retry_comparison(self,sid):
        with self.retry_lock,st.db() as con:
            row=con.execute('SELECT state FROM sessions WHERE id=?',(sid,)).fetchone()
            if not row:raise retries.RetryError('NOT_RETRY','재도전 세션을 찾을 수 없습니다.')
            s=json.loads(row['state']);relation=s.get('retry')
            if not relation:raise retries.RetryError('NOT_RETRY','재도전 세션이 아닙니다.')
            if s['status']=='active':raise retries.RetryError('RETRY_ACTIVE','재도전을 완료한 뒤 비교할 수 있습니다.')
            plan=retries._plan(con,relation['plan_id']);item=retries._one(con,'SELECT * FROM retry_items WHERE id=?',(relation['item_id'],))
            if not item or item['session_id']!=sid or item['plan_id']!=plan['id']:raise retries._unavailable('RELATION_CORRUPT')
            s=retries._linked_session(con,plan,item);events,jobs=retries._attempt_records(con,sid);now=st.now()
            if retries.classify_attempt(s,events,jobs,plan['pid'])['grading_pending']:return {'state':'grading','current_sid':sid,'computed_at':now}
            current=self.retry_attempt_summary(con,s,plan['pid'])
            try:
                source=retries._session(con,plan['source_sid']);retries._problem(source,plan['pid'])
                previous=self.retry_attempt_summary(con,source,plan['pid'])
            except retries.RetryError:return {'state':'unavailable','current_sid':sid,'computed_at':now,'current':current,'code':'SOURCE_UNAVAILABLE'}
            delta=round(current['minutes']-previous['minutes'],1) if current['minutes'] is not None and previous['minutes'] is not None else None
            return {'state':'ready','plan_id':plan['id'],'item_id':item['id'],'source_sid':plan['source_sid'],'current_sid':sid,'pid':plan['pid'],'reason':plan['reason'],'computed_at':now,'source':previous,'current':current,'delta_minutes':delta,'time_basis':'문제 선택 구간의 경과 시간(탭을 닫은 시간 포함)','conditions_differ':any(previous[k]!=current[k] for k in ('mode','duration','resources','hints')),'limitations':['복구 중이면 늦은 기록으로 비교값이 갱신될 수 있음','시간 감소만으로 실력 상승을 판단할 수 없음']}
    def schedule(self,s):
        if s.get('deadline_codes') is not None:return
        timer=threading.Timer(max(0,(s['deadline']-st.now())/1000),self.expire,args=(s['id'],));timer.daemon=True;timer.start();self.timers[s['id']]=timer
    @retry_lifecycle
    def expire(self,sid):
        with self.deadline_lock:
            s=st.session(sid)
            if s['status']!='active' or s.get('deadline_codes') is not None:return
            if st.now()<s['deadline']:return
            def freeze(s):
                s['deadline_codes']=copy.deepcopy(s['codes'])
                if s['mode']=='exam':s['status']='completed';s['ended']=s['deadline']
            s=st.mutate(sid,freeze);st.event(sid,'deadline',{'mode':s['mode']},ms=s['duration']*60000)
            if s['mode']=='exam':
                for p in s['problems']:self.run(sid,p['id'],True,forced=True)
                with st.db(True) as con:self.reconcile_session(con,sid)
    @retry_lifecycle
    def view(self,sid):
        s=st.session(sid)
        if s['status']=='active' and st.now()>=s['deadline']:self.expire(sid);s=st.session(sid)
        s['code_versions']=self.code_versions(sid,s)
        s['metrics']=self.metrics(s);s['problems']=[public_problem(p) for p in s['problems']];s['events']=st.events(sid);s['media']=self.media_groups(sid,s);s['recording_gaps']=self.recording_timeline(sid,s,s['media']);s['now']=st.now()
        if s.get('retry') and s['status']=='active':
            for p in s['problems']:p.pop('tags',None)
        with st.db() as con:
            s['jobs']=[json.loads(r['state']) for r in con.execute('SELECT state FROM jobs WHERE sid=?',(sid,))]
            if s['status']=='completed':s['retry_candidates']=self.retry_candidates(con,s)
        return s
    def record(self,sid,events):
        if not isinstance(events,list) or len(events)>200:raise ValueError('이벤트 묶음은 최대 200개입니다.')
        self.expire(sid)
        clock_session=st.session(sid);clock_now=st.now()
        for e in events:
            if not isinstance(e,dict) or not isinstance(e.get('id'),str) or len(e['id'])>100 or e.get('kind') not in {'code','focus','blur','switch','paste','cursor','scroll','recording_start','recording_stop','recording_gap','note','resource','reconnect'}:raise ValueError('이벤트 형식 오류')
            if not valid_offset(clock_session,e.get('ms'),clock_now):raise ValueError('이벤트 시각 오류: 세션 경과 시간을 확인하세요.')
            seq=e.get('seq')
            if seq is not None and (type(seq) is not int or not 0<=seq<2**53):raise ValueError('이벤트 순서 오류')
            payload=e.get('payload',{});pid=e.get('pid','')
            if not isinstance(payload,dict) or len(st.dump(payload))>200000:raise ValueError('이벤트 크기 오류')
            with st.db(True) as con:
                if con.execute('SELECT 1 FROM events WHERE sid=? AND id=?',(sid,e['id'])).fetchone():continue
                row=con.execute('SELECT state FROM sessions WHERE id=?',(sid,)).fetchone()
                if not row:raise ValueError('세션 없음')
                s=json.loads(row['state'])
                if e['kind']=='code':
                    if pid not in s['codes'] or not isinstance(payload.get('code'),str) or len(payload['code'])>60000:raise ValueError('코드 기록 오류')
                    if seq is not None:payload['client_seq']=seq
                    if s['status']=='active':
                        # Events can arrive out of order (outbox retries, several tabs). Only
                        # the newest (ms, client sequence) becomes the current code; older
                        # events are still stored as history.
                        version=[e['ms'],-1 if seq is None else seq];current=self.code_version(con,sid,s,pid)
                        if current is None or version>=current:
                            s['codes'][pid]=payload['code'];s.setdefault('code_versions',{})[pid]=version
                            con.execute('UPDATE sessions SET state=? WHERE id=?',(st.dump(s),sid))
                        else:payload['superseded_on_arrival']=True
                    else:
                        payload['recovered_after_finish']=True
                con.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',(sid,e['id'],e['ms'],e['kind'],pid,st.dump(payload)))
        return {'saved':len(events),'now':st.now()}
    @staticmethod
    def code_version(con,sid,s,pid):
        if pid in s.get('code_versions',{}):return s['code_versions'][pid]
        # Sessions saved before versioning (or a pid never versioned): infer the version from
        # stored events with the same ordering as st.events: max ms, then client_seq (-1 when
        # absent), then the later arrival.
        rows=con.execute("SELECT ms,payload FROM events WHERE sid=? AND kind='code' AND pid=? AND ms=(SELECT MAX(ms) FROM events WHERE sid=? AND kind='code' AND pid=?) ORDER BY rowid",(sid,pid,sid,pid)).fetchall()
        if not rows:return None
        return [rows[0]['ms'],max(st.code_order({'payload':json.loads(r['payload'])}) for r in rows)]
    def code_versions(self,sid,s):
        """Current version per problem without writing anything (legacy sessions included)."""
        with st.db() as con:
            versions={pid:self.code_version(con,sid,s,pid) for pid in s['codes']}
        return {pid:v for pid,v in versions.items() if v is not None}
    @retry_lifecycle
    def finish(self,sid):
        self.expire(sid)
        def end(s):
            if s['status']=='active':s['ended']=st.now();s['status']='completed'
        s=st.mutate(sid,end);st.event(sid,'finish',{},ms=max(0,s['ended']-s['started']))
        if sid in self.timers:self.timers.pop(sid).cancel()
        with st.db(True) as con:self.reconcile_session(con,sid)
        return self.view(sid)
    def job(self,sid,kind,fn):
        jid=uuid.uuid4().hex;cancel=threading.Event();j={'id':jid,'sid':sid,'kind':kind,'status':'queued','created':st.now(),'text':'','error':None,'result':None}
        with st.db(True) as con:con.execute('INSERT INTO jobs VALUES (?,?,?)',(jid,sid,st.dump(j)))
        def update(**change):
            with self.retry_lock if kind=='java' else contextlib.nullcontext():
                j.update(change)
                with st.db(True) as con:
                    con.execute('UPDATE jobs SET state=? WHERE id=?',(st.dump(j),jid))
                    if kind=='java' and j['status'] in {'completed','failed'}:self.reconcile_session(con,sid)
        def work():
            update(status='running')
            try:
                result=fn(lambda text:update(text=text[-40000:]),cancel);update(status='completed',result=result)
            except Exception as exc:update(status='failed',error=str(exc)[:1000])
            finally:self.jobs.pop(jid,None)
        self.jobs[jid]=cancel;threading.Thread(target=work,daemon=True).start();return {'job_id':jid}
    def get_job(self,jid):
        with st.db() as con:r=con.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()
        if not r:raise ValueError('작업을 찾을 수 없습니다.')
        return json.loads(r['state'])
    @retry_lifecycle
    def run(self,sid,pid,submit,forced=False):
        if not forced:self.expire(sid)
        s=st.session(sid);p=next((p for p in s['problems'] if p['id']==pid),None)
        if not p:raise ValueError('문제를 찾지 못했습니다.')
        if not forced and s['status']!='active':raise ValueError('연습이 종료되었습니다.')
        code=(s['deadline_codes'] if forced else s['codes'])[pid];ms=s['duration']*60000 if forced else max(0,st.now()-s['started']);rid=uuid.uuid4().hex
        st.event(sid,'submit' if submit else 'run',{'run_id':rid,'code':code,'automatic':forced},pid,ms)
        def execute(progress,cancel):
            if cancel.is_set():raise ValueError('취소됨')
            r=run_java(code,p,submit);entry={'id':rid,'pid':pid,'ms':ms,'submit':submit,'automatic':forced,'overtime':ms>s['duration']*60000,'code':code,'result':r}
            with self.retry_lock:
                st.mutate(sid,lambda s:s['runs'].append(entry));st.event(sid,'result',{'run_id':rid,'passed':r['passed'],'total':r['total'],'output':r['output'],'error':r['error']},pid)
            return entry
        return self.job(sid,'java',execute)
    def media_groups(self,sid,s=None):
        s=s or st.session(sid)
        with st.db() as con:
            rows=con.execute('SELECT kind,segment,seq,start_ms,mime,size FROM media WHERE sid=? ORDER BY kind,segment,seq',(sid,)).fetchall()
            marks=con.execute("SELECT ms,kind,payload FROM events WHERE sid=? AND kind IN ('recording_start','recording_stop','recording_chunk','recording_gap') ORDER BY ms,rowid",(sid,)).fetchall()
        return recording_groups(rows,marks,s['status']=='active',sid)
    def recording_timeline(self,sid,s=None,groups=None):
        s=s or st.session(sid)
        return recording_gaps(groups if groups is not None else self.media_groups(sid,s),max(0,(s['ended'] or st.now())-s['started']))
    def upload(self,sid,kind,segment,seq,start,mime,raw,end=None):
        s=st.session(sid)
        if kind not in {'screen','voice'} or not re.fullmatch('[a-f0-9]{32}',segment) or not 0<=seq<100000 or not valid_offset(s,start,st.now()) or len(raw)>8_000_000:raise ValueError('미디어 조각 형식 오류')
        if end is not None and (not valid_offset(s,end,st.now()) or end<start):raise ValueError('미디어 종료 시각 오류')
        if mime not in {'video/webm','audio/webm','audio/ogg','video/mp4','audio/mp4'}:raise ValueError('지원하지 않는 녹화 형식입니다.')
        folder=st.DATA/'sessions'/sid/'media'/kind/segment;folder.mkdir(parents=True,exist_ok=True);file=folder/f'{seq:06d}.part'
        with self.media_lock:
            with st.db() as con:r=con.execute('SELECT size,start_ms,mime FROM media WHERE sid=? AND kind=? AND segment=? AND seq=?',(sid,kind,segment,seq)).fetchone()
            if r:
                if file.read_bytes()!=raw or r['start_ms']!=start or r['mime']!=mime:raise ValueError('기존 미디어 조각과 내용이 다릅니다.')
                if end is not None:st.event(sid,'recording_chunk',{'kind':kind,'segment':segment,'seq':seq,'end_ms':end},ms=end,eid=f'media-{kind}-{segment}-{seq}')
                return {'saved':len(raw)}
            temp=file.with_suffix('.tmp');temp.write_bytes(raw);temp.replace(file)
            with st.db(True) as con:con.execute('INSERT INTO media VALUES (?,?,?,?,?,?,?)',(sid,kind,segment,seq,start,mime,len(raw)))
        if end is not None:st.event(sid,'recording_chunk',{'kind':kind,'segment':segment,'seq':seq,'end_ms':end},ms=end,eid=f'media-{kind}-{segment}-{seq}')
        return {'saved':len(raw)}
    def media_file(self,sid,kind,segment):
        st.session(sid)
        if kind not in {'screen','voice'} or not re.fullmatch('[a-f0-9]{32}',segment):raise ValueError('잘못된 미디어 경로')
        with st.db() as con:rows=con.execute('SELECT * FROM media WHERE sid=? AND kind=? AND segment=? ORDER BY seq',(sid,kind,segment)).fetchall()
        if not rows or [r['seq'] for r in rows]!=list(range(len(rows))):raise ValueError('녹화 조각이 누락되었습니다. 브라우저의 미전송 기록을 복구해 주세요.')
        folder=st.DATA/'sessions'/sid/'media'/kind/segment
        extension='mp4' if 'mp4' in rows[0]['mime'] else 'ogg' if 'ogg' in rows[0]['mime'] else 'webm'
        out=folder/('joined.'+extension)
        with self.media_lock:
            size=sum(r['size'] for r in rows)
            if not out.exists() or out.stat().st_size!=size:
                temp=folder/'joined.tmp'
                with temp.open('wb') as w:
                    for r in rows:
                        with (folder/f'{r["seq"]:06d}.part').open('rb') as f:shutil.copyfileobj(f,w)
                temp.replace(out)
        if st.session(sid)['status']=='completed' and kind=='screen':
            from .speech import ffmpeg
            replay=folder/('replay.'+extension);stamp=folder/'replay.size'
            if not replay.exists() or not stamp.exists() or stamp.read_text()!=str(size):
                with self.media_lock:
                    temp=folder/('replay-tmp.'+extension)
                    result=subprocess.run([ffmpeg(),'-y','-v','error','-i',str(out),'-c','copy',str(temp)],capture_output=True,timeout=120)
                    if result.returncode==0:temp.replace(replay);stamp.write_text(str(size))
            if replay.exists() and stamp.exists() and stamp.read_text()==str(size):return replay,rows[0]['mime']
        return out,rows[0]['mime']
    def frame(self,sid,ms):
        s=st.session(sid)
        if s['status']!='completed' or not valid_offset(s,ms,st.now()):raise ValueError('종료된 연습의 유효한 시각을 선택하세요.')
        groups=[g for g in self.media_groups(sid,s) if g['kind']=='screen' and g['playable'] and g['start_ms']<=ms and (g.get('end_ms') is None or ms<g['end_ms'])]
        if not groups:raise ValueError('이 시각에 녹화가 없습니다.')
        if len(s.get('frames',[]))>=4:raise ValueError('한 세션에 최대 4장면을 첨부할 수 있습니다.')
        group=groups[-1];path,_=self.media_file(sid,'screen',group['segment'])
        folder=st.DATA/'sessions'/sid/'frames';folder.mkdir(parents=True,exist_ok=True);target=folder/(str(ms)+'.jpg')
        from .speech import ffmpeg
        r=subprocess.run([ffmpeg(),'-y','-v','error','-ss',str((ms-group['start_ms'])/1000),'-i',str(path),'-frames:v','1','-vf','scale=1280:-2',str(target)],capture_output=True,timeout=30)
        if r.returncode or not target.exists():raise ValueError('해당 시각의 영상 장면을 추출하지 못했습니다.')
        frame={'ms':ms,'path':str(target.relative_to(st.DATA))}
        st.mutate(sid,lambda s:s.setdefault('frames',[]).append(frame))
        return {'ms':ms}
    def prepare_transcripts(self,sid,progress,cancel,force=False):
        # One preparation at a time also prevents a manual transcription racing
        # the automatic analysis preparation and replacing the same transcript.
        with self.transcription_lock:
            s=st.session(sid)
            if s['status']=='active':raise ValueError('연습 종료 후 전사할 수 있습니다.')
            groups=[g for g in self.media_groups(sid,s) if g['kind']=='screen']
            gaps=self.recording_timeline(sid,s,groups)
            signature=hashlib.sha256(st.dump([(g['segment'],g['chunks'],g['bytes'],g['status'],g.get('end_ms'),g.get('capture_end_ms')) for g in groups]).encode()).hexdigest()
            state=s.get('transcription_state',{})
            if not force and (state.get('source')=='user' or (s['transcripts'] and not state)):
                return s['transcripts']
            if not force and state.get('signature')==signature and state.get('status') in {'completed','no_speech','partial'} and not state.get('errors'):
                return s['transcripts']
            state={'signature':signature,'source':'automatic','updated':st.now(),'gaps':gaps}
            output=[];skipped=[];errors=[];processed=[];partial_segments=[]
            for i,g in enumerate(groups):
                if cancel.is_set():raise InterruptedError('분석 준비가 취소되었습니다.')
                if not g['playable']:
                    skipped.append(g['segment']);continue
                progress(f'녹음 {i+1}/{len(groups)}개를 이 컴퓨터에서 전사하고 있어요…')
                try:
                    path,_=self.media_file(sid,'screen',g['segment'])
                    segments=self.speech.transcribe(path)
                    output.extend([x|{'start_ms':x['start_ms']+g['start_ms'],'end_ms':x['end_ms']+g['start_ms'],'segment':g['segment']} for x in segments])
                    processed.append(g['segment'])
                    if g['status'] in {'missing','pending'}:partial_segments.append(g['segment'])
                except InterruptedError:raise
                except Exception as exc:errors.append({'segment':g['segment'],'error':str(exc)[:400]})
            output.sort(key=lambda x:(x['start_ms'],x['end_ms']))
            unconfirmed=sum(g['status']!='complete' for g in groups)
            if not groups:state.update(status='missing',detail='녹음이 없어 코드·실행 기록으로 분석합니다.')
            elif not processed:
                lost=sum(g['missing_count'] for g in groups)
                state.update(status='failed' if errors else 'incomplete',recording='missing',
                    detail=('음성 전사 실패: '+errors[0]['error']) if errors else f'녹화 조각 {lost}개 누락 또는 저장된 녹화 없음. 미전송 기록이 복구되면 다시 준비합니다.')
            else:
                partial=bool(skipped or errors or partial_segments)
                state.update(status='partial' if partial else 'completed' if output else 'no_speech',
                    recording='missing' if partial else 'unconfirmed' if unconfirmed else 'gaps' if gaps else 'complete',
                    detail=f'녹화 {len(processed)}/{len(groups)}개에서 음성 전사 {len(output)}구간.'
                    +(f' 누락·실패 구간 {len(skipped)+len(errors)}개는 포함하지 않았습니다.' if skipped or errors else '')
                    +(f' 끝부분 조각이 누락된 녹화 {len(partial_segments)}개는 저장된 앞부분만 전사했습니다.' if partial_segments else '')
                    +(f' 녹화 {unconfirmed}개는 종료 확인이 없어 끝부분 누락 여부를 확인할 수 없습니다.' if unconfirmed else '')
                    +(f' 미녹화·확인 불가 시간 구간 {len(gaps)}개가 있습니다. 발화가 없었다는 뜻이 아닙니다.' if gaps else ''))
            state.update(processed_segments=processed,skipped_segments=skipped,partial_segments=partial_segments,errors=errors)
            if cancel.is_set():raise InterruptedError('분석 준비가 취소되었습니다.')
            def save(current):
                if current.get('transcription_state',{}).get('source')=='user':return
                current['transcription_state']=state
                current['transcripts']=output
            st.mutate(sid,save)
            return st.session(sid)['transcripts']
    def save_transcripts(self,sid,segments):
        st.mutate(sid,lambda s:s.update(transcripts=segments,transcription_state={
            'status':'completed' if segments else 'no_speech','source':'user','updated':st.now(),
            'detail':'사용자가 확인·수정한 전사문을 사용합니다.'}))
    def transcribe(self,sid,kind='screen',segment=None):
        s=st.session(sid)
        if kind=='screen' and s['status']=='active':raise ValueError('연습 종료 후 전사할 수 있습니다.')
        groups=[g for g in self.media_groups(sid,s) if g['kind']==kind and (kind=='screen' or g['playable']) and (segment is None or g['segment']==segment)]
        if not groups:raise ValueError('녹음이 없습니다. 녹화 업로드가 완료됐는지 확인하세요.')
        def work(progress,cancel):
            if kind=='screen':
                output=self.prepare_transcripts(sid,progress,cancel,force=True)
                state=st.session(sid).get('transcription_state',{})
                if state.get('status') in {'failed','incomplete'}:raise ValueError(state['detail'])
                return {'segments':output,'text':' '.join(x['text'] for x in output)}
            output=[]
            for g in groups:
                if cancel.is_set():raise ValueError('전사 취소됨')
                progress('한국어 음성을 이 컴퓨터에서 텍스트로 변환하고 있어요…')
                path,_=self.media_file(sid,kind,g['segment']);segments=self.speech.transcribe(path)
                output.extend([x|{'start_ms':x['start_ms']+g['start_ms'],'end_ms':x['end_ms']+g['start_ms'],'segment':g['segment']} for x in segments])
            if kind=='screen':st.mutate(sid,lambda s:s.update(transcripts=output))
            return {'segments':output,'text':' '.join(x['text'] for x in output)}
        return self.job(sid,'transcription',work)
    def evidence(self,sid,hint=False):
        s=st.session(sid);events=st.events(sid)
        timeline=[];edit_bins={}
        for e in events:
            if e['kind'] in {'cursor','scroll','recording_chunk'}:continue
            if e['kind']=='code':
                key=(e['pid'],e['ms']//15000)
                bucket=edit_bins.setdefault(key,{'ms':e['ms'],'kind':'edit_activity','pid':e['pid'],'payload':{'changes':0,'last_ms':e['ms'],'characters':0}})
                bucket['payload']['changes']+=1;bucket['payload']['last_ms']=e['ms'];bucket['payload']['characters']=len(e['payload']['code']);continue
            payload=dict(e['payload'])
            if e['kind'] in {'run','submit'}:payload.pop('code',None)
            timeline.append({'id':e['id'],'ms':e['ms'],'kind':e['kind'],'pid':e['pid'],'payload':payload})
        timeline.extend(edit_bins.values());timeline.sort(key=lambda e:e['ms'])
        blind=bool(s.get('retry') and (hint or s['status']=='active'))
        past=[] if blind else [{'title':x['title'],'report':x['report']} for x in st.sessions() if x['id']!=sid and x.get('report')][:5]
        problems=[public_problem(p) for p in s['problems']]
        if blind:
            for p in problems:p.pop('tags',None)
        evolution=[] if hint else code_evolution(s,events)
        return {'settings':{k:s[k] for k in ['mode','kind','duration','resources','started','ended']},'problems':problems,'current_code':s['codes'],'deadline_code':s['deadline_codes'],'runs':s['runs'],'timeline':timeline,'metrics':self.metrics(s),'code_evolution':evolution,'conversations':conversations(s),'hint_followups':hint_followups(s,events),'coverage':{'raw_event_count':len(events),'sent_event_count':len(timeline),'raw_code_events':sum(e['kind']=='code' for e in events),'code_checkpoints':len(evolution),'edit_detail':'처음·마지막, 2초 이상 입력 간격, 15초 구간 마지막, 실행·힌트 전후의 코드 차이를 전달. 중간 개별 편집은 원본 이벤트에 보존. 입력 간격은 사고 시간의 측정값이 아님.'}, 'transcripts':transcript_evidence(s),'transcription_state':s.get('transcription_state',{'status':'provided' if s['transcripts'] else 'not_prepared'}),'recording':self.media_groups(sid),'recording_gaps':self.recording_timeline(sid,s),'past_reports':past,'attached_frame_times':[x['ms'] for x in s.get('frames',[])],'note':'영상 원본은 직접 입력되지 않음. 코드 차이·전사문·이벤트 기반 분석. 실행 시각과 코드 수정 시각을 구분하고, 수정 간격을 사고 시간으로 단정하지 말 것.'}
    def export_evidence(self,sid):
        s=st.session(sid)
        inputs=[];root=(st.DATA/'sessions'/sid/'analysis').resolve()
        for meta in s.get('analysis_inputs',[]):
            path=(st.DATA/meta['path']).resolve()
            if path.is_relative_to(root) and path.is_file():inputs.append(json.loads(path.read_text(encoding='utf-8')))
            else:inputs.append(meta|{'error':'분석 입력 원본 파일 없음'})
        return self.evidence(sid)|{'export_version':2,'session_id':sid,'exported_at':st.now(),
            'model':s['model'],'effort':s['effort'],'report':s.get('report'),
            'messages':s['messages'],'report_history':s.get('report_history',[]),
            'analysis_inputs':inputs,'events':st.events(sid)}
    def chat(self,sid,d):
        s=st.session(sid);kind=d.get('kind');message=d.get('message','')
        if kind not in {'analyze','reply','report','hint'} or not isinstance(message,str) or len(message)>20000:raise ValueError('복기 요청 형식을 확인하세요.')
        if kind=='hint' and (s['mode']!='training' or s['status']!='active'):raise ValueError('힌트는 훈련 모드 풀이 중에만 요청할 수 있습니다.')
        if kind!='hint' and s['status']!='completed':raise ValueError('풀이를 종료한 뒤 복기하세요.')
        model=d.get('model');effort=d.get('effort')
        if not isinstance(model,str) or not isinstance(effort,str) or not model or not effort:raise ValueError('모델과 추론 수준을 선택하세요.')
        def work(progress,cancel):
            if kind!='hint':self.prepare_transcripts(sid,progress,cancel)
            if cancel.is_set():raise InterruptedError('분석이 취소되었습니다.')
            current=st.session(sid)
            prompt={'task':{'analyze':'근거를 확인하고 첫 복기 질문을 해줘. 아직 최종 보고서는 만들지 마.','reply':'학습자의 복기 답변을 반영해서 필요하면 추가 확인 질문을 해줘.','report':'복기 답변을 반영하여 최종 보고서를 작성해줘. report 필드를 채워줘. 풀이 과정과 교정 우선순위, 성공 기준이 있는 훈련 계획을 작성해줘.','hint':'현재 문제에 대해 정답 코드 없이 작은 힌트 하나만 줘.'}[kind],
                    'user_answer':message,'current_problem':d.get('pid'),'evidence':self.evidence(sid,kind=='hint')}
            if kind=='reply' and current.get('report'):prompt['current_report']=current['report']
            raw=st.dump(prompt)
            if len(raw)>750000:raise ValueError('분석 자료가 너무 큽니다. 원본은 보존되며, 이 요청에서는 일부 기록만 몰래 잘라 분석하지 않았습니다.')
            request_id=uuid.uuid4().hex
            folder=st.DATA/'sessions'/sid/'analysis';folder.mkdir(parents=True,exist_ok=True)
            path=folder/(request_id+'.json')
            audit={'id':request_id,'kind':kind,'created':st.now(),'model':model,'effort':effort,
                   'prompt_version':2,'input':prompt,'instructions':SYSTEM,'schema':SCHEMA,
                   'image_times':prompt['evidence']['attached_frame_times']}
            path.write_text(st.dump(audit),encoding='utf-8')
            metadata={k:audit[k] for k in ['id','kind','created','model','effort','prompt_version']}
            metadata['path']=str(path.relative_to(st.DATA))
            st.mutate(sid,lambda s:s.setdefault('analysis_inputs',[]).append(metadata))
            progress('코드 변화·실행 결과·힌트와 음성 기록을 함께 분석하고 있어요…')
            thread_key='thread' if kind=='hint' else 'review_thread'
            answer,usage,thread=self.codex.chat(current.get(thread_key),model,effort,raw,progress,cancel,
                lambda thread:st.mutate(sid,lambda s:s.update({thread_key:thread})),
                images=[str(st.DATA/x['path']) for x in current.get('frames',[])])
            (folder/(request_id+'-response.json')).write_text(st.dump({'answer':answer,'usage':usage}),encoding='utf-8')
            if kind=='report':validate_report(answer.get('report'),prompt['evidence'])
            entry={'id':uuid.uuid4().hex,'kind':kind,'pid':d.get('pid'),'question':message,'answer':answer,
                   'at':st.now(),'model':model,'effort':effort,'usage':usage,'analysis_input_id':request_id}
            def save(s):
                if kind=='report' and answer.get('report'):
                    if s.get('report'):s.setdefault('report_history',[]).append({'replaced_at':entry['at'],'report':s['report']})
                    s['report']=answer['report']
                s['messages'].append(entry);s['model']=model;s['effort']=effort
            st.mutate(sid,save);st.event(sid,'hint' if kind=='hint' else 'coach',{'message_id':entry['id'],'kind':kind},d.get('pid',''))
            return entry
        return self.job(sid,'codex',work)
    def import_problem(self,d):
        p=validate_problem(d)
        with st.db(True) as con:con.execute('INSERT INTO problems VALUES (?,?)',(p['id'],st.dump(p)))
        return public_problem(p)
    def close(self):
        for timer in self.timers.values():timer.cancel()
        for cancel in self.jobs.values():cancel.set()
        for tool in [self.codex,self.ide]:
            if tool.rpc:tool.rpc.close()
