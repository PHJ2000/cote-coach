import json
import os
import re
import selectors
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

IMAGE='eclipse-temurin:21-jdk'
RUN_LOCK=threading.Semaphore(2)

def literal(value,kind):
    if kind.endswith('[]'):return 'new '+kind+'{'+','.join(literal(v,kind[:-2]) for v in value)+'}'
    if kind=='String':return json.dumps(value,ensure_ascii=True)
    if kind=='boolean':return 'true' if value else 'false'
    if kind=='long':return str(value)+'L'
    return str(value)

SERIALIZER=r'''
static String json(Object v) {
 if(v==null)return "null";
 if(v instanceof String || v instanceof Character) {
  String s=v.toString(); StringBuilder b=new StringBuilder("\"");
  for(int i=0;i<s.length();i++){char c=s.charAt(i); switch(c){case '"':b.append("\\\"");break;case '\\':b.append("\\\\");break;case '\n':b.append("\\n");break;case '\r':b.append("\\r");break;case '\t':b.append("\\t");break;default:if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c);}}
  return b.append('"').toString();
 }
 if(v.getClass().isArray()){StringBuilder b=new StringBuilder("[");for(int i=0;i<java.lang.reflect.Array.getLength(v);i++){if(i>0)b.append(',');b.append(json(java.lang.reflect.Array.get(v,i)));}return b.append(']').toString();}
 return v.toString();
}
'''

def run_java(code,problem,submit=False):
    if not isinstance(code,str) or not code.strip() or len(code)>60000:raise ValueError('Java 코드는 1~60,000자로 입력하세요.')
    tests=problem['tests'] if submit else [t for t in problem['tests'] if t['public']]
    marker='RESULT_'+uuid.uuid4().hex
    calls=[]
    for i,test in enumerate(tests):
        args=','.join(literal(v,p['type']) for v,p in zip(test['args'],problem['params']))
        calls.append('try { System.out.println("'+marker+str(i)+':"+json(new Solution().'+problem['method']+'('+args+'))); } catch(Throwable e) { System.out.println("'+marker+str(i)+':ERROR:"+e.getClass().getSimpleName()+": "+e.getMessage()); }')
    harness='public class CoachHarness {\n'+SERIALIZER+'\npublic static void main(String[] args) {\n'+'\n'.join(calls)+'\n}}'
    start=time.monotonic()
    with RUN_LOCK, tempfile.TemporaryDirectory(prefix='cote-java-') as temp:
        folder=Path(temp);folder.chmod(0o755)
        (folder/'Solution.java').write_text(code);(folder/'CoachHarness.java').write_text(harness)
        name='cote-run-'+uuid.uuid4().hex[:12]
        command=['docker','run','--rm','--name',name,'--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','96','--memory','512m','--cpus','1','--user','65534:65534','--tmpfs','/work:rw,exec,nosuid,size=32m,mode=1777','--tmpfs','/tmp:rw,noexec,nosuid,size=16m','-v',temp+':/src:ro','-w','/work',IMAGE,'sh','-c','javac -encoding UTF-8 -d /work /src/Solution.java /src/CoachHarness.java && java -Xmx192m -Xss512k -XX:ActiveProcessorCount=1 -cp /work CoachHarness']
        proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        output=bytearray();failure=None
        sel=selectors.DefaultSelector();sel.register(proc.stdout,selectors.EVENT_READ)
        try:
            while True:
                if time.monotonic()-start>20:failure='실행 제한 시간 초과 (컴파일 포함 20초)';break
                ready=sel.select(.2)
                if ready:
                    chunk=os.read(proc.stdout.fileno(),8192)
                    if not chunk:break
                    output.extend(chunk)
                    if len(output)>128000:failure='출력 제한 초과 (128KB)';break
                elif proc.poll() is not None:break
            if failure:
                subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
                proc.kill()
            proc.wait(timeout=5)
        finally:
            sel.close();proc.stdout.close()
            if proc.poll() is None:proc.kill()
        text=output.decode('utf-8',errors='replace');results=[];plain=[]
        found={}
        for line in text.splitlines():
            if line.startswith(marker):
                index,_,value=line[len(marker):].partition(':');found[int(index)]=value
            else:plain.append(line)
        for i,test in enumerate(tests):
            raw=found.get(i,'');actual=None
            try:actual=json.loads(raw);passed=actual==test['expected'] and not failure
            except (ValueError,TypeError):passed=False
            case={'index':i+1,'passed':bool(passed),'public':test['public'],'error':raw[6:] if raw.startswith('ERROR:') else (failure or ('결과 없음' if not raw else ''))}
            if test['public']:case.update(args=test['args'],expected=test['expected'],actual=actual)
            results.append(case)
        return {'status':'passed' if results and all(t['passed'] for t in results) else 'failed','tests':results,'passed':sum(t['passed'] for t in results),'total':len(results),'output':'\n'.join(plain)[-12000:],'error':failure,'duration_ms':round((time.monotonic()-start)*1000),'submit':submit,'java':'21'}
