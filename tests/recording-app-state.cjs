const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('client/app.js','utf8');
const pick=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end));
(async()=>{
 const controls=[{disabled:false},{disabled:false}],timer={classList:{toggle(){}}},states=[];
 const scope={route:'workspace',session:{id:'exam',mode:'exam',duration:5,status:'active'},elapsed:()=>300100,elapsedBase:300100,
  formatSessionClock:()=>'-',deadlineHandled:false,finishing:false,captureGeneration:0,eventQueue:Promise.resolve(),
  editor:{readonly(){scope.readonly=true;}},$:id=>id==='#timer'?timer:null,$$:()=>controls,
  stopRecorder:async()=>{throw new Error('offline');},recordingState:(...x)=>states.push(x),toast(){}};
 vm.createContext(scope);vm.runInContext(pick('async function updateClock(){','function stopRecorder(){'),scope);
 await scope.updateClock();assert(scope.deadlineHandled);assert.equal(scope.finishing,false);assert(scope.readonly);assert(controls.every(x=>x.disabled));assert.match(states.at(-1)[1],/연습 종료를 다시/);
 // A failed old session must not block current-session finish, but its data remains queued.
 const old={url:'/api/v2/events',body:JSON.stringify({sid:'old-session'})};let queue=[old];
 const flushScope={session:{id:'current-session'},eventQueue:Promise.resolve(),retryUnstored:async()=>{},drain:async()=>{throw new Error('old failed');},pending:async()=>queue};
 vm.createContext(flushScope);vm.runInContext(pick('async function flush(){','function recordingState('),flushScope);
 await flushScope.flush();assert.strictEqual(queue[0],old);
 queue.push({url:'/api/v2/events',body:JSON.stringify({sid:'current-session'})});await assert.rejects(flushScope.flush(),/old failed/);
 // Recorder's late stop can persist with its captured session even after navigation.
 const saved=[];const recordScope={session:null,pid:'later-problem',uid:()=> 'event-id',nextSeq:()=>1,elapsed:()=>{throw Error('wrong clock');},eventQueue:Promise.resolve(),stash:async x=>saved.push(x),drain:async()=>{},setSaveStatus(){},toast(){}};
 vm.createContext(recordScope);vm.runInContext(pick('function record(','function setSaveStatus('),recordScope);
 await recordScope.record('recording_stop',{segment:'a',chunks:1},'original-problem',{sid:'original-session',ms:4500});
 const body=JSON.parse(saved[0].body);assert.equal(body.sid,'original-session');assert.equal(body.events[0].pid,'original-problem');assert.equal(body.events[0].ms,4500);
 console.log('PASS recording app state: deadline retry stays read-only, failed other-session outbox isolation, current-session blocking, captured late-stop identity.');
})().catch(e=>{console.error(e);process.exitCode=1;});
