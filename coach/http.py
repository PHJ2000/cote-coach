import json
import mimetypes
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from . import store as st
from .retries import RetryError


def stream_file(h,file,mime,download=None):
    total=file.stat().st_size;start=0;end=total-1;status=200
    range_header=h.headers.get('Range','')
    if range_header:
        m=re.fullmatch(r'bytes=(\d+)-(\d*)',range_header)
        if not m:h.send(416,{'error':'지원하지 않는 범위'});return
        start=int(m[1]);end=min(end,int(m[2])) if m[2] else end;status=206
        if start>end or start>=total:h.send(416,{'error':'범위 초과'});return
    h.send_response(status);h.send_header('Content-Type',mime);h.send_header('Content-Length',str(max(0,end-start+1)));h.send_header('Accept-Ranges','bytes');h.send_header('Cache-Control','no-store');h.send_header('X-Content-Type-Options','nosniff')
    if status==206:h.send_header('Content-Range',f'bytes {start}-{end}/{total}')
    if download:h.send_header('Content-Disposition',f'attachment; filename="{download}"')
    h.end_headers()
    try:
        with file.open('rb') as f:
            f.seek(start);remaining=end-start+1
            while remaining>0:
                chunk=f.read(min(262144,remaining))
                if not chunk:break
                h.wfile.write(chunk);remaining-=len(chunk)
    except (BrokenPipeError,ConnectionResetError):pass


def get(h,lab,path):
    if path.startswith('/dist/'):
        file=(st.ROOT/'static'/path.lstrip('/')).resolve();root=(st.ROOT/'static/dist').resolve()
        if not file.is_relative_to(root) or not file.is_file():h.send(404,{'error':'파일 없음'});return True
        mime=mimetypes.guess_type(file.name)[0] or 'application/octet-stream';stream_file(h,file,mime);return True
    if not path.startswith('/api/v2/'):return False
    try:
        if path=='/api/v2/retries':result=lab.retry_list()
        elif re.fullmatch(r'/api/v2/retry-comparison/[^/]+',path):result=lab.retry_comparison(path.rsplit('/',1)[-1])
        elif path=='/api/v2/dashboard':result=lab.dashboard()
        elif path=='/api/v2/health':result=lab.health()
        elif path=='/api/v2/models':result=lab.codex.info()
        elif path.startswith('/api/v2/session/'):result=lab.view(path.rsplit('/',1)[-1])
        elif path.startswith('/api/v2/job/'):result=lab.get_job(path.rsplit('/',1)[-1])
        elif path.startswith('/api/v2/media/'):
            _,_,_,_,sid,kind,segment=path.split('/');file,mime=lab.media_file(sid,kind,segment);stream_file(h,file,mime);return True
        elif path.startswith('/api/v2/evidence/'):
            sid=path.rsplit('/',1)[-1];result=lab.export_evidence(sid)
        elif path=='/api/v2/backup':
            with tempfile.TemporaryDirectory() as temp:
                import sqlite3
                target=Path(temp)/'lab.sqlite3'
                with st.db() as source:
                    dest=sqlite3.connect(target);source.backup(dest);dest.close()
                file=Path(temp)/'cote-lab.zip'
                with zipfile.ZipFile(file,'w',compression=zipfile.ZIP_STORED) as z:
                    z.write(target,'lab.sqlite3')
                    for f in (st.DATA/'sessions').rglob('*'):
                        if f.is_file() and f.name not in {'joined.webm','joined.tmp','replay.webm','replay-tmp.webm','replay.size'}:z.write(f,str(f.relative_to(st.DATA)))
                stream_file(h,file,'application/zip','cote-lab-backup.zip')
            return True
        else:h.send(404,{'error':'경로 없음'});return True
        h.send(200,result)
    except (BrokenPipeError,ConnectionResetError):pass # The editor cancels superseded requests.
    except RetryError as exc:h.send(400,exc.as_dict())
    except Exception as exc:h.send(400,{'error':str(exc)[:1200]})
    return True


def post(h,lab,path):
    if not path.startswith('/api/v2/'):return False
    try:
        size=int(h.headers.get('Content-Length','0'))
        if not 0<size<=8_000_000:raise ValueError('요청 크기는 8MB 이하여야 합니다.')
        if path=='/api/v2/upload':
            q=parse_qs(urlsplit(h.path).query);one=lambda key:q[key][0]
            if h.headers.get('Content-Type')!='application/octet-stream':raise ValueError('미디어 전송 형식 오류')
            result=lab.upload(one('sid'),one('kind'),one('segment'),int(one('seq')),int(one('start')),one('mime'),h.rfile.read(size),int(one('end')) if 'end' in q else None)
        else:
            if h.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('JSON 요청이 필요합니다.')
            d=json.loads(h.rfile.read(size))
            if not isinstance(d,dict):raise ValueError('객체가 필요합니다.')
            if path=='/api/v2/retry-plan':result=lab.retry_plan(d)
            elif path=='/api/v2/retry-start':result=lab.retry_start(d)
            elif path=='/api/v2/start':result=lab.new_session(d)
            elif path=='/api/v2/events':result=lab.record(d['sid'],d['events'])
            elif path=='/api/v2/finish':result=lab.finish(d['sid'])
            elif path=='/api/v2/run':
                lab.expire(d['sid']);result=lab.run(d['sid'],d['pid'],bool(d.get('submit')))
            elif path=='/api/v2/frame':result=lab.frame(d['sid'],d['ms'])
            elif path=='/api/v2/transcribe':result=lab.transcribe(d['sid'],d.get('kind','screen'),d.get('segment'))
            elif path=='/api/v2/transcript':
                if not isinstance(d.get('segments'),list) or len(d['segments'])>20000:raise ValueError('전사문 형식 오류')
                from .timing import valid_offset
                clock_session=st.session(d['sid']);clock_now=st.now()
                for segment in d['segments']:
                    if not valid_offset(clock_session,segment.get('start_ms'),clock_now) or not valid_offset(clock_session,segment.get('end_ms'),clock_now) or segment['start_ms']>segment['end_ms'] or not isinstance(segment.get('text'),str) or len(segment['text'])>5000:raise ValueError('전사문 시간·내용을 확인하세요.')
                lab.save_transcripts(d['sid'],d['segments']);result={'saved':True}
            elif path=='/api/v2/chat':result=lab.chat(d['sid'],d)
            elif path=='/api/v2/cancel':
                job=lab.get_job(d['job_id']);cancel=lab.jobs.get(d['job_id'])
                if cancel:cancel.set()
                result={'cancel_requested':bool(cancel)}
            elif path=='/api/v2/import-problem':result=lab.import_problem(d)
            elif path=='/api/v2/ide':
                s=st.session(d['sid'])
                if s['mode']!='training' or s['status']!='active':raise ValueError('실전 또는 종료된 연습에서는 IDE 지원을 사용할 수 없습니다.')
                pid=d['pid']
                if pid not in s['codes'] or d.get('op') not in {'completion','signature','diagnostics','resolve','hover'}:raise ValueError('IDE 요청 형식 오류')
                code=d.get('code',s['codes'][pid])
                if not isinstance(code,str) or len(code)>60000:raise ValueError('코드 크기 오류')
                context=d.get('context')
                if context is not None and (not isinstance(context,dict) or context.get('triggerKind') not in {1,2,3} or ('triggerCharacter' in context and (not isinstance(context['triggerCharacter'],str) or len(context['triggerCharacter'])!=1))):raise ValueError('자동완성 요청 문맥 오류')
                result=lab.ide.request('s'+s['id']+'_'+pid,code,d['op'],max(0,int(d.get('line',0))),max(0,int(d.get('character',0))),context=context,resolve_id=d.get('resolve_id'))
            else:raise ValueError('경로 없음')
        h.send(200,result)
    except RetryError as exc:h.send(400,exc.as_dict())
    except Exception as exc:h.send(400,{'error':str(exc)[:1200]})
    return True
