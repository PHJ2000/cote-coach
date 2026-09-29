import copy
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
from pathlib import Path
from . import store as st
from .problems import BUILTINS, public_problem, validate_problem
from .java import run_java
from .codex import CodexCoach
from .speech import Speech
from .ide import JavaIDE

class Lab:
    def __init__(self):
        st.init();self.codex=CodexCoach(st.DATA);self.speech=Speech(st.ROOT);self.ide=JavaIDE(st.ROOT,st.DATA)
        self.jobs={};self.jobs_lock=threading.Lock();self.timers={};self.deadline_lock=threading.Lock();self.media_lock=threading.Lock()
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
    def new_session(self,d):
        ids=d.get('problem_ids');problems={p['id']:p for p in self.all_problems()}
        if not isinstance(ids,list) or not 1<=len(ids)<=5 or len(set(ids))!=len(ids) or any(i not in problems for i in ids):raise ValueError('문제를 1~5개 선택하세요.')
        if d.get('mode') not in {'exam','training'} or d.get('kind') not in {'single','mock'} or d.get('resources') not in {'none','java-docs','search'}:raise ValueError('연습 설정을 확인하세요.')
        if d['kind']=='single' and len(ids)!=1:raise ValueError('한 문제 연습은 문제 하나만 선택하세요.')
        duration=d.get('duration')
        if type(duration) is not int or not 5<=duration<=300:raise ValueError('제한 시간은 5~300분입니다.')
        sid=uuid.uuid4().hex;ide_warning=None
        if d['mode']=='training':
            try:
                for pid in ids:self.ide.prepare('s'+sid+'_'+pid,problems[pid]['starter'])
            except (ValueError,RuntimeError,OSError) as exc:
                # Editing/recording remain available if optional IDE support fails.
                ide_warning=str(exc)[:300]
        start=st.now() # Preparation must never consume practice time.
        s={'id':sid,'title':('모의 코테' if len(ids)>1 else problems[ids[0]]['title']),'started':start,'ended':None,'status':'active','mode':d['mode'],'kind':d['kind'],'resources':d['resources'],'duration':duration,'deadline':start+duration*60000,'deadline_codes':None,'problems':[problems[i] for i in ids],'codes':{i:problems[i]['starter'] for i in ids},'runs':[],'messages':[],'report':None,'transcripts':[],'thread':None,'model':str(d.get('model',''))[:100],'effort':str(d.get('effort',''))[:30],'media_expected':True}
        with st.db(True) as con:con.execute('INSERT INTO sessions VALUES (?,?,?)',(sid,start,st.dump(s)))
        st.event(sid,'start',{'mode':s['mode'],'resources':s['resources']},ms=0);self.schedule(s)
        result=self.view(sid)
        if ide_warning:result['ide_warning']=ide_warning
        return result
    def schedule(self,s):
        if s.get('deadline_codes') is not None:return
        timer=threading.Timer(max(0,(s['deadline']-st.now())/1000),self.expire,args=(s['id'],));timer.daemon=True;timer.start();self.timers[s['id']]=timer
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
    def view(self,sid):
        s=st.session(sid)
        if s['status']=='active' and st.now()>=s['deadline']:self.expire(sid);s=st.session(sid)
        s['metrics']=self.metrics(s);s['problems']=[public_problem(p) for p in s['problems']];s['events']=st.events(sid);s['media']=self.media_groups(sid);s['now']=st.now()
        with st.db() as con:s['jobs']=[json.loads(r['state']) for r in con.execute('SELECT state FROM jobs WHERE sid=?',(sid,))]
        return s
    def record(self,sid,events):
        if not isinstance(events,list) or len(events)>200:raise ValueError('이벤트 묶음은 최대 200개입니다.')
        self.expire(sid)
        for e in events:
            if not isinstance(e,dict) or not isinstance(e.get('id'),str) or len(e['id'])>100 or e.get('kind') not in {'code','focus','blur','switch','paste','cursor','scroll','recording_start','recording_stop','recording_gap','note','resource','reconnect'}:raise ValueError('이벤트 형식 오류')
            if type(e.get('ms')) is not int or e['ms']<0 or e['ms']>24*3600000:raise ValueError('이벤트 시각 오류')
            payload=e.get('payload',{});pid=e.get('pid','')
            if not isinstance(payload,dict) or len(st.dump(payload))>200000:raise ValueError('이벤트 크기 오류')
            with st.db(True) as con:
                if con.execute('SELECT 1 FROM events WHERE sid=? AND id=?',(sid,e['id'])).fetchone():continue
                row=con.execute('SELECT state FROM sessions WHERE id=?',(sid,)).fetchone()
                if not row:raise ValueError('세션 없음')
                s=json.loads(row['state'])
                if e['kind']=='code':
                    if pid not in s['codes'] or not isinstance(payload.get('code'),str) or len(payload['code'])>60000:raise ValueError('코드 기록 오류')
                    if s['status']=='active':
                        s['codes'][pid]=payload['code'];con.execute('UPDATE sessions SET state=? WHERE id=?',(st.dump(s),sid))
                    else:
                        payload['recovered_after_finish']=True
                con.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',(sid,e['id'],e['ms'],e['kind'],pid,st.dump(payload)))
        return {'saved':len(events),'now':st.now()}
    def finish(self,sid):
        self.expire(sid)
        def end(s):
            if s['status']=='active':s['ended']=st.now();s['status']='completed'
        s=st.mutate(sid,end);st.event(sid,'finish',{},ms=max(0,s['ended']-s['started']))
        if sid in self.timers:self.timers.pop(sid).cancel()
        return self.view(sid)
    def job(self,sid,kind,fn):
        jid=uuid.uuid4().hex;cancel=threading.Event();j={'id':jid,'sid':sid,'kind':kind,'status':'queued','created':st.now(),'text':'','error':None,'result':None}
        with st.db(True) as con:con.execute('INSERT INTO jobs VALUES (?,?,?)',(jid,sid,st.dump(j)))
        def update(**change):
            j.update(change)
            with st.db(True) as con:con.execute('UPDATE jobs SET state=? WHERE id=?',(st.dump(j),jid))
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
    def run(self,sid,pid,submit,forced=False):
        s=st.session(sid);p=next((p for p in s['problems'] if p['id']==pid),None)
        if not p:raise ValueError('문제를 찾지 못했습니다.')
        if not forced and s['status']!='active':raise ValueError('연습이 종료되었습니다.')
        code=(s['deadline_codes'] if forced else s['codes'])[pid];ms=s['duration']*60000 if forced else max(0,st.now()-s['started']);rid=uuid.uuid4().hex
        st.event(sid,'submit' if submit else 'run',{'run_id':rid,'code':code,'automatic':forced},pid,ms)
        def execute(progress,cancel):
            if cancel.is_set():raise ValueError('취소됨')
            r=run_java(code,p,submit);entry={'id':rid,'pid':pid,'ms':ms,'submit':submit,'automatic':forced,'overtime':ms>s['duration']*60000,'code':code,'result':r}
            st.mutate(sid,lambda s:s['runs'].append(entry));st.event(sid,'result',{'run_id':rid,'passed':r['passed'],'total':r['total'],'output':r['output'],'error':r['error']},pid)
            return entry
        return self.job(sid,'java',execute)
    def media_groups(self,sid):
        with st.db() as con:rows=con.execute('SELECT kind,segment,start_ms,mime,COUNT(*) AS chunks,SUM(size) AS bytes,MAX(seq) AS last FROM media WHERE sid=? GROUP BY kind,segment ORDER BY start_ms',(sid,)).fetchall()
        return [dict(r)|{'complete_sequence':r['chunks']==r['last']+1,'url':f'/api/v2/media/{sid}/{r["kind"]}/{r["segment"]}'} for r in rows]
    def upload(self,sid,kind,segment,seq,start,mime,raw):
        s=st.session(sid)
        if kind not in {'screen','voice'} or not re.fullmatch('[a-f0-9]{32}',segment) or not 0<=seq<100000 or not 0<=start<=86400000 or len(raw)>8_000_000:raise ValueError('미디어 조각 형식 오류')
        if mime not in {'video/webm','audio/webm','audio/ogg','video/mp4','audio/mp4'}:raise ValueError('지원하지 않는 녹화 형식입니다.')
        folder=st.DATA/'sessions'/sid/'media'/kind/segment;folder.mkdir(parents=True,exist_ok=True);file=folder/f'{seq:06d}.part'
        with self.media_lock:
            with st.db() as con:r=con.execute('SELECT size FROM media WHERE sid=? AND kind=? AND segment=? AND seq=?',(sid,kind,segment,seq)).fetchone()
            if r:
                if file.read_bytes()!=raw:raise ValueError('기존 미디어 조각과 내용이 다릅니다.')
                return {'saved':len(raw)}
            temp=file.with_suffix('.tmp');temp.write_bytes(raw);temp.replace(file)
            with st.db(True) as con:con.execute('INSERT INTO media VALUES (?,?,?,?,?,?,?)',(sid,kind,segment,seq,start,mime,len(raw)))
        return {'saved':len(raw)}
    def media_file(self,sid,kind,segment):
        st.session(sid)
        if kind not in {'screen','voice'} or not re.fullmatch('[a-f0-9]{32}',segment):raise ValueError('잘못된 미디어 경로')
        with st.db() as con:rows=con.execute('SELECT * FROM media WHERE sid=? AND kind=? AND segment=? ORDER BY seq',(sid,kind,segment)).fetchall()
        if not rows or [r['seq'] for r in rows]!=list(range(len(rows))):raise ValueError('녹화 조각이 누락되었습니다. 브라우저의 미전송 기록을 복구해 주세요.')
        folder=st.DATA/'sessions'/sid/'media'/kind/segment;out=folder/'joined.webm'
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
            replay=folder/'replay.webm';stamp=folder/'replay.size'
            if not replay.exists() or not stamp.exists() or stamp.read_text()!=str(size):
                with self.media_lock:
                    temp=folder/'replay-tmp.webm'
                    result=subprocess.run([ffmpeg(),'-y','-v','error','-i',str(out),'-c','copy',str(temp)],capture_output=True,timeout=120)
                    if result.returncode==0:temp.replace(replay);stamp.write_text(str(size))
            if replay.exists() and stamp.exists() and stamp.read_text()==str(size):return replay,rows[0]['mime']
        return out,rows[0]['mime']
    def frame(self,sid,ms):
        s=st.session(sid)
        if s['status']!='completed' or type(ms) is not int or not 0<=ms<=86400000:raise ValueError('종료된 연습의 유효한 시각을 선택하세요.')
        groups=[g for g in self.media_groups(sid) if g['kind']=='screen' and g['start_ms']<=ms]
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
    def transcribe(self,sid,kind='screen',segment=None):
        s=st.session(sid)
        if kind=='screen' and s['status']=='active':raise ValueError('연습 종료 후 전사할 수 있습니다.')
        groups=[g for g in self.media_groups(sid) if g['kind']==kind and (segment is None or g['segment']==segment)]
        if not groups:raise ValueError('녹음이 없습니다. 녹화 업로드가 완료됐는지 확인하세요.')
        def work(progress,cancel):
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
            if e['kind'] in {'cursor','scroll'}:continue
            if e['kind']=='code':
                key=(e['pid'],e['ms']//15000)
                bucket=edit_bins.setdefault(key,{'ms':e['ms'],'kind':'edit_activity','pid':e['pid'],'payload':{'changes':0,'last_ms':e['ms'],'characters':0}})
                bucket['payload']['changes']+=1;bucket['payload']['last_ms']=e['ms'];bucket['payload']['characters']=len(e['payload']['code']);continue
            payload=dict(e['payload'])
            if e['kind'] in {'run','submit'}:payload.pop('code',None)
            timeline.append({'ms':e['ms'],'kind':e['kind'],'pid':e['pid'],'payload':payload})
        timeline.extend(edit_bins.values());timeline.sort(key=lambda e:e['ms'])
        past=[{'title':x['title'],'report':x['report']} for x in st.sessions() if x['id']!=sid and x.get('report')][:5]
        return {'settings':{k:s[k] for k in ['mode','kind','duration','resources','started','ended']},'problems':[public_problem(p) for p in s['problems']],'current_code':s['codes'],'deadline_code':s['deadline_codes'],'runs':s['runs'],'timeline':timeline,'metrics':self.metrics(s),'coverage':{'raw_event_count':len(events),'sent_event_count':len(timeline),'edit_detail':'수정 활동은 15초 단위로 집계. 실제 실행·제출 코드 스냅샷과 최종 코드를 제공. 모든 개별 수정 이력은 로컬에서 재생 가능.'}, 'transcripts':s['transcripts'],'recording':self.media_groups(sid),'past_reports':past,'attached_frame_times':[x['ms'] for x in s.get('frames',[])],'note':'영상 원본은 직접 입력되지 않음. 코드 이력·전사문·이벤트 기반 분석. 읽지 않은 영상을 봤다고 말하지 말 것.'}
    def chat(self,sid,d):
        s=st.session(sid);kind=d.get('kind');message=d.get('message','')
        if kind not in {'analyze','reply','report','hint'} or not isinstance(message,str) or len(message)>20000:raise ValueError('복기 요청 형식을 확인하세요.')
        if kind=='hint' and (s['mode']!='training' or s['status']!='active'):raise ValueError('힌트는 훈련 모드 풀이 중에만 요청할 수 있습니다.')
        if kind!='hint' and s['status']!='completed':raise ValueError('풀이를 종료한 뒤 복기하세요.')
        model=d.get('model');effort=d.get('effort')
        if not isinstance(model,str) or not isinstance(effort,str) or not model or not effort:raise ValueError('모델과 추론 수준을 선택하세요.')
        prompt={'task':{'analyze':'근거를 확인하고 첫 복기 질문을 해줘. 아직 최종 보고서는 만들지 마.','reply':'학습자의 복기 답변을 반영해서 필요하면 추가 확인 질문을 해줘.','report':'복기 답변을 반영하여 최종 보고서를 작성해줘. report 필드를 채워줘.','hint':'현재 문제에 대해 정답 코드 없이 작은 힌트 하나만 줘.'}[kind],'user_answer':message,'current_problem':d.get('pid')}
        if kind in {'analyze','hint'} or not s['thread']:prompt['evidence']=self.evidence(sid,kind=='hint')
        if kind=='report':prompt['evidence']=self.evidence(sid)
        # All raw history remains on disk; exceptionally large sessions are explicitly limited.
        raw=st.dump(prompt)
        if len(raw)>500000:
            evidence=prompt.get('evidence',{});timeline=evidence.get('timeline',[]);code_events=[e for e in timeline if e['kind']=='code'];evidence['timeline']=[e for e in timeline if e['kind']!='code']+code_events[-40:];evidence['coverage_note']='긴 세션: 최근 코드 변경 40개와 실행 스냅샷을 포함. 나머지 코드 변경은 원본 기록에 보존됨.';raw=st.dump(prompt)
        if len(raw)>750000:raise ValueError('분석 자료가 너무 큽니다. 문제별로 더 짧은 세션을 분석해 주세요. 원본은 보존됩니다.')
        def work(progress,cancel):
            answer,usage,thread=self.codex.chat(s['thread'],model,effort,raw,progress,cancel,lambda thread:st.mutate(sid,lambda s:s.update(thread=thread)),images=[str(st.DATA/x['path']) for x in s.get('frames',[])])
            entry={'id':uuid.uuid4().hex,'kind':kind,'pid':d.get('pid'),'question':message,'answer':answer,'at':st.now(),'model':model,'effort':effort,'usage':usage}
            def save(s):
                s['messages'].append(entry);s['model']=model;s['effort']=effort
                if kind=='report' and answer.get('report'):s['report']=answer['report']
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
