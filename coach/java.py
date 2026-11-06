import json
import os
import selectors
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .problems import DEFAULT_TIME_LIMIT_MS, PERF_BUDGET_MS, PERF_LIMIT_MS, expand_test

IMAGE='eclipse-temurin:21-jdk'
RUN_LOCK=threading.Semaphore(2)
TOTAL_LIMIT_S=20 # Whole run including container start and compilation.
OUTPUT_LIMIT=128000

# Test data never enters Java source: it is streamed to the harness over stdin
# as big-endian binary (DataInputStream). Strings are UTF-16 code units so lone
# surrogates and escapes survive exactly as the old Java literals did.
READERS={'int':'in.readInt()','long':'in.readLong()','double':'in.readDouble()','boolean':'in.readBoolean()','String':'str()',
         'int[]':'ia()','long[]':'la()','String[]':'sa()','int[][]':'iaa()','boolean[]':'ba()'}
CASTS={'int':'Integer','long':'Long','double':'Double','boolean':'Boolean'}

def encode(value,kind,out):
    if kind=='int[]':out.append(struct.pack(f'>i{len(value)}i',len(value),*value))
    elif kind=='long[]':out.append(struct.pack(f'>i{len(value)}q',len(value),*value))
    elif kind=='boolean[]':out.append(struct.pack('>i',len(value))+bytes(1 if v else 0 for v in value))
    elif kind.endswith('[]'):
        out.append(struct.pack('>i',len(value)))
        for v in value:encode(v,kind[:-2],out)
    elif kind=='String':
        raw=value.encode('utf-16-be','surrogatepass');out.append(struct.pack('>i',len(raw)//2)+raw)
    elif kind=='boolean':out.append(b'\x01' if value else b'\x00')
    elif kind=='int':out.append(struct.pack('>i',value))
    elif kind=='long':out.append(struct.pack('>q',value))
    elif kind=='double':out.append(struct.pack('>d',float(value)))
    else:raise ValueError('지원하지 않는 타입: '+str(kind))

def stream(marker,cases,params,budget=PERF_BUDGET_MS):
    """marker, case count, shared performance budget, then per case: limit ms,
    performance flag, arguments."""
    out=[];encode(marker,'String',out);out.append(struct.pack('>ii',len(cases),budget))
    for args,limit,performance in cases:
        out.append(struct.pack('>i?',limit,bool(performance)))
        for value,param in zip(args,params):encode(value,param['type'],out)
    return b''.join(out)

HARNESS=r'''import java.io.*;
public class CoachHarness {
static DataInputStream in;
static volatile Object value; static volatile Throwable thrown;
static int[] ia() throws IOException {int n=in.readInt();int[] r=new int[n];for(int i=0;i<n;i++)r[i]=in.readInt();return r;}
static long[] la() throws IOException {int n=in.readInt();long[] r=new long[n];for(int i=0;i<n;i++)r[i]=in.readLong();return r;}
static boolean[] ba() throws IOException {int n=in.readInt();boolean[] r=new boolean[n];for(int i=0;i<n;i++)r[i]=in.readBoolean();return r;}
static int[][] iaa() throws IOException {int n=in.readInt();int[][] r=new int[n][];for(int i=0;i<n;i++)r[i]=ia();return r;}
static String str() throws IOException {int n=in.readInt();char[] c=new char[n];for(int i=0;i<n;i++)c[i]=in.readChar();return new String(c);}
static String[] sa() throws IOException {int n=in.readInt();String[] r=new String[n];for(int i=0;i<n;i++)r[i]=str();return r;}
static String json(Object v) {
 if(v==null)return "null";
 if(v instanceof String || v instanceof Character) {
  String s=v.toString(); StringBuilder b=new StringBuilder("\"");
  for(int i=0;i<s.length();i++){char c=s.charAt(i); switch(c){case '"':b.append("\\\"");break;case '\\':b.append("\\\\");break;case '\n':b.append("\\n");break;case '\r':b.append("\\r");break;case '\t':b.append("\\t");break;default:if(c<32||c>126)b.append(String.format("\\u%04x",(int)c));else b.append(c);}}
  return b.append('"').toString();
 }
 if(v.getClass().isArray()){StringBuilder b=new StringBuilder("[");for(int i=0;i<java.lang.reflect.Array.getLength(v);i++){if(i>0)b.append(',');b.append(json(java.lang.reflect.Array.get(v,i)));}return b.append(']').toString();}
 return v.toString();
}
static String error(Throwable e){return "ERROR:"+e.getClass().getSimpleName()+": "+String.valueOf(e.getMessage()).replace('\n',' ').replace('\r',' ');}
static Object call(Object[] a) throws Throwable { return new Solution().@METHOD@(@ARGS@); }
public static void main(String[] args) throws Exception {
 PrintStream out=System.out;
 in=new DataInputStream(new BufferedInputStream(System.in,1<<16));
 String mark=str();int count=in.readInt();long budget=in.readInt()*1000000L;int[] limits=new int[count];boolean[] shared=new boolean[count];Object[][] tests=new Object[count][];
 for(int t=0;t<count;t++){limits[t]=in.readInt();shared[t]=in.readBoolean();tests[t]=new Object[]{@READS@};}
 // All input is consumed before learner code runs, so it cannot read later cases.
 in=null;System.setIn(new ByteArrayInputStream(new byte[0]));
 out.println(mark+"START");out.flush();
 long used=0; // Nanoseconds spent in performance cases; they share one budget.
 for(int t=0;t<count;t++){
  long wait=limits[t];String expired="TIMEOUT";
  if(shared[t]){long left=(budget-used)/1000000L;if(left<=0){out.println(mark+t+":0:BUDGET");out.println(mark+"END");out.flush();Runtime.getRuntime().halt(0);}if(wait==0||left<wait){wait=left;expired="BUDGET";}}
  final Object[] a=tests[t];tests[t]=null;value=null;thrown=null;
  Thread worker=new Thread(()->{try{value=call(a);}catch(Throwable e){thrown=e;}},"solution");
  worker.setDaemon(true);long begin=System.nanoTime();worker.start();worker.join(wait);
  long spent=System.nanoTime()-begin,ms=spent/1000000L;if(shared[t])used+=spent;
  if(worker.isAlive()){out.println(mark+t+":"+ms+":"+expired);out.println(mark+"END");out.flush();Runtime.getRuntime().halt(0);}
  String line;
  if(thrown!=null)line=error(thrown);
  else{try{line=json(value);}catch(Throwable e){line=error(e);}}
  out.println(mark+t+":"+ms+":"+line);out.flush();
 }
 out.println(mark+"END");out.flush();Runtime.getRuntime().halt(0);
}}
'''

def harness_source(problem):
    """Constant-size harness: depends only on the signature, never on test data."""
    params=problem['params']
    args=','.join(f'({CASTS.get(p["type"],p["type"])})a[{i}]' for i,p in enumerate(params))
    return HARNESS.replace('@METHOD@',problem['method']).replace('@ARGS@',args).replace('@READS@',','.join(READERS[p['type']] for p in params))

def outcome(tests,results,submit,start,output='',failure=None,grader=None,compiled=True):
    status='error' if grader else ('passed' if results and all(t['passed'] for t in results) else 'failed')
    return {'status':status,'tests':results,'passed':sum(t['passed'] for t in results),'total':len(tests),'output':output[-12000:],
            'error':grader or failure,'grader_error':grader,'compile_error':not compiled,'duration_ms':round((time.monotonic()-start)*1000),
            'submit':submit,'java':'21','limits':{'total_s':TOTAL_LIMIT_S,'per_test_ms':DEFAULT_TIME_LIMIT_MS,'performance_test_ms':PERF_LIMIT_MS,'performance_budget_ms':PERF_BUDGET_MS,'output_bytes':OUTPUT_LIMIT}}

def unrun(tests,error):
    return [{'index':i+1,'passed':False,'public':t['public'],'error':error,'time_ms':None}|({'label':t['label']} if t.get('label') else {}) for i,t in enumerate(tests)]

def run_java(code,problem,submit=False):
    if not isinstance(code,str) or not code.strip() or len(code)>60000:raise ValueError('Java 코드는 1~60,000자로 입력하세요.')
    tests=problem['tests'] if submit else [t for t in problem['tests'] if t['public']]
    marker='RESULT_'+uuid.uuid4().hex
    start=time.monotonic()
    try:
        cases=[expand_test(t,problem) for t in tests]
        data=stream(marker,[(args,t.get('time_limit_ms',DEFAULT_TIME_LIMIT_MS),t.get('performance',False)) for t,(args,_) in zip(tests,cases)],problem['params'])
    except Exception as exc:
        return outcome(tests,unrun(tests,'채점기 오류'),submit,start,grader='채점 데이터 준비 실패: '+str(exc)[:300])
    with RUN_LOCK, tempfile.TemporaryDirectory(prefix='cote-java-') as temp:
        folder=Path(temp);folder.chmod(0o755)
        (folder/'Solution.java').write_text(code);(folder/'CoachHarness.java').write_text(harness_source(problem))
        name='cote-run-'+uuid.uuid4().hex[:12]
        command=['docker','run','--rm','-i','--name',name,'--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','96','--memory','512m','--cpus','1','--user','65534:65534','--tmpfs','/work:rw,exec,nosuid,size=32m,mode=1777','--tmpfs','/tmp:rw,noexec,nosuid,size=16m','-v',temp+':/src:ro','-w','/work',IMAGE,'sh','-c','javac -encoding UTF-8 -d /work /src/Solution.java /src/CoachHarness.java && java -Xmx192m -Xss512k -XX:ActiveProcessorCount=1 -cp /work CoachHarness']
        try:proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        except OSError as exc:
            return outcome(tests,unrun(tests,'채점기 오류'),submit,start,grader='채점기 실행 실패: Docker를 시작하지 못했습니다 ('+str(exc)[:200]+')')
        def feed():
            try:proc.stdin.write(data)
            except (OSError,ValueError):pass # The container may stop early (compile error, limit).
            finally:
                try:proc.stdin.close()
                except OSError:pass
        writer=threading.Thread(target=feed,daemon=True);writer.start()
        output=bytearray();failure=None
        sel=selectors.DefaultSelector();sel.register(proc.stdout,selectors.EVENT_READ)
        try:
            while True:
                if time.monotonic()-start>TOTAL_LIMIT_S:failure=f'실행 제한 시간 초과 (컴파일 포함 {TOTAL_LIMIT_S}초)';break
                ready=sel.select(.2)
                if ready:
                    chunk=os.read(proc.stdout.fileno(),8192)
                    if not chunk:break
                    output.extend(chunk)
                    if len(output)>OUTPUT_LIMIT:failure=f'출력 제한 초과 ({OUTPUT_LIMIT//1000}KB)';break
                elif proc.poll() is not None:break
            if failure:
                subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
                proc.kill()
            proc.wait(timeout=5)
        finally:
            sel.close();proc.stdout.close()
            if proc.poll() is None:proc.kill()
            writer.join(5)
    text=output.decode('utf-8',errors='replace');plain=[];found={};started=ended=False
    for line in text.splitlines():
        at=line.find(marker)
        if at<0:plain.append(line);continue
        if at:plain.append(line[:at]) # Learner output printed without a trailing newline.
        token=line[at+len(marker):]
        if token=='START':started=True
        elif token=='END':ended=True
        else:
            index,_,rest=token.partition(':');ms,_,value=rest.partition(':')
            try:found[int(index)]=(int(ms),value)
            except ValueError:plain.append(line)
    console='\n'.join(plain)
    if not started:
        # Exit code 1 is javac rejecting the learner's code; anything else before the
        # harness starts (Docker 125, missing tool 126/127, OOM kill, compile-phase
        # timeout) is an infrastructure failure, not a wrong answer.
        if failure and failure.startswith('출력'):grader=None
        elif failure:grader='채점기 시간 초과: 컴파일 단계에서 제한 시간을 넘었습니다.'
        elif proc.returncode==1:grader=None
        else:grader=f'채점기 오류: 실행 환경이 비정상 종료되었습니다 (종료 코드 {proc.returncode}).'
        return outcome(tests,unrun(tests,'채점기 오류' if grader else '컴파일 실패'),submit,start,console,failure,grader,compiled=bool(grader))
    timed_out=next((i for i,(_,v) in sorted(found.items()) if v in {'TIMEOUT','BUDGET'}),None)
    grader=None
    if ended and timed_out is None and not failure and any(i not in found for i in range(len(tests))):grader='채점기 오류: 일부 테스트 결과가 누락되었습니다.'
    results=[]
    for i,(test,(_,expected)) in enumerate(zip(tests,cases)):
        ms,raw=found.get(i,(None,''));actual=None;passed=False;error=''
        if raw=='TIMEOUT':error=f'시간 초과 (테스트당 {test.get("time_limit_ms",DEFAULT_TIME_LIMIT_MS)/1000:g}초 제한)'
        elif raw=='BUDGET':error=f'시간 초과 (대입력 성능 테스트 합계 {PERF_BUDGET_MS/1000:g}초 제한)'
        elif raw.startswith('ERROR:'):error=raw[6:]
        elif raw:
            try:actual=json.loads(raw);passed=actual==expected and not failure
            except ValueError:error='반환값을 해석하지 못했습니다.'
            if failure:error=failure
        elif failure:error=failure
        elif timed_out is not None and i>timed_out:error='이전 테스트 시간 초과로 실행하지 않음'
        elif grader:error='채점기 오류'
        else:error='결과 없음 (프로그램이 테스트 도중 종료됨)'
        case={'index':i+1,'passed':bool(passed),'public':test['public'],'error':error,'time_ms':ms}
        if raw in {'TIMEOUT','BUDGET'}:case['timeout']=True
        if raw=='BUDGET':case['budget_exceeded']=True
        if test.get('performance'):case['performance']=True
        if test.get('label'):case['label']=test['label']
        if test['public'] and 'generate' not in test:case.update(args=test['args'],expected=expected,actual=actual)
        results.append(case)
    return outcome(tests,results,submit,start,console,failure,grader)
