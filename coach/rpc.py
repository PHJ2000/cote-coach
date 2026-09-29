"""Bounded JSON-RPC transport for local Codex and Java language servers."""
import json
import queue
import subprocess
import threading

class RPC:
    def __init__(self,command,log,framed=False,cwd=None,env=None):
        self.framed=framed; self.pending={};self.lock=threading.Lock();self.counter=0;self.on_event=lambda m,p:None
        self.log=open(log,'ab')
        self.proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,cwd=cwd,env=env)
        threading.Thread(target=self._read,daemon=True).start()
    def send(self,obj):
        raw=json.dumps(obj,ensure_ascii=False).encode()
        with self.lock:
            if self.proc.poll() is not None:raise RuntimeError('연결 프로세스가 종료되었습니다.')
            self.proc.stdin.write((f'Content-Length: {len(raw)}\r\n\r\n'.encode()+raw) if self.framed else raw+b'\n')
            self.proc.stdin.flush()
    def notify(self,method,params):self.send({'jsonrpc':'2.0','method':method,'params':params})
    def call(self,method,params,timeout=40):
        with self.lock:
            self.counter+=1; n=self.counter;q=queue.Queue(1);self.pending[n]=q
        try:
            self.send({'jsonrpc':'2.0','id':n,'method':method,'params':params})
            try:r=q.get(timeout=timeout)
            except queue.Empty:raise RuntimeError(method+' 응답 시간이 초과되었습니다.') from None
            if 'error' in r:raise RuntimeError(r['error'].get('message','도구 오류'))
            return r.get('result')
        finally:self.pending.pop(n,None)
    def _read(self):
        try:
            while True:
                line=self.proc.stdout.readline()
                if not line:break
                if self.framed:
                    if not line.lower().startswith(b'content-length:'):
                        self.log.write(line);self.log.flush();continue
                    headers={}
                    while line.strip():
                        k,v=line.decode().split(':',1);headers[k.lower()]=v.strip();line=self.proc.stdout.readline()
                    raw=self.proc.stdout.read(int(headers['content-length']))
                else:raw=line
                try:obj=json.loads(raw)
                except (ValueError,UnicodeError):continue
                if 'id' in obj and 'method' not in obj:
                    q=self.pending.get(obj['id'])
                    if q:q.put(obj)
                elif 'method' in obj:
                    if 'id' in obj:
                        method=obj['method']
                        if method=='workspace/configuration':result=[None for _ in obj.get('params',{}).get('items',[])]
                        elif 'requestApproval' in method:result={'decision':'decline'}
                        elif method=='window/workDoneProgress/create' or method=='client/registerCapability':result=None
                        else:
                            self.send({'jsonrpc':'2.0','id':obj['id'],'error':{'code':-32601,'message':'Interactive tool access disabled in practice coach'}});continue
                        self.send({'jsonrpc':'2.0','id':obj['id'],'result':result})
                    else:self.on_event(obj['method'],obj.get('params',{}))
        finally:
            for q in list(self.pending.values()):
                try:q.put_nowait({'error':{'message':'도구 프로세스와 연결이 끊겼습니다.'}})
                except queue.Full:pass
    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:self.proc.kill()
        self.log.close()
