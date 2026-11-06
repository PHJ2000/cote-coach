const assert=require('node:assert/strict');
const items=new Map();let rejectNext=false,rejectOpen=false,releaseUpload,uploadsHeld=true;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const database={transaction(){const tx={objectStore:()=>({
 put(item){queueMicrotask(()=>{if(rejectNext){rejectNext=false;tx.error=new Error('fixture quota');tx.onerror?.();}else{items.set(item.id,item);tx.oncomplete?.();}});},
 delete(id){queueMicrotask(()=>{items.delete(id);tx.oncomplete?.();});},
 getAll(){const r={};queueMicrotask(()=>{r.result=[...items.values()];r.onsuccess();});return r;}
 })};return tx;}};
global.indexedDB={open(){const r={};queueMicrotask(()=>{if(rejectOpen){rejectOpen=false;r.error=new Error('fixture open failure');r.onerror();}else{r.result=database;r.onsuccess();}});return r;}};
Object.defineProperty(global,'navigator',{value:{mediaDevices:{}},configurable:true});global.location={origin:'http://localhost:9999'};
global.fetch=()=>uploadsHeld?new Promise(resolve=>releaseUpload=()=>{uploadsHeld=false;resolve({ok:true});}):Promise.resolve({ok:true});
class Track extends EventTarget{constructor(){super();this.readyState='live';}stop(){this.readyState='ended';}}
class Recorder extends EventTarget{
 static isTypeSupported(){return true;}
 constructor(){super();this.state='inactive';}
 start(){this.state='recording';}
 data(text){this.ondataavailable({data:new Blob([text])});}
 stop(){this.state='inactive';queueMicrotask(()=>{this.data('tail');this.dispatchEvent(new Event('stop'));});}
}
global.MediaRecorder=Recorder;
(async()=>{
 const {makeRecorder,stash,pending,drain,retryUnstored}=await import('../client/recording.js');
 const tracks=[new Track(),new Track()],events=[],ended=[];
 const r=makeRecorder({getTracks:()=>tracks},{sid:'test',start:144000000,event:(kind,payload)=>events.push({kind,payload}),onEnded:e=>ended.push(e)});
 await tick();rejectNext=true;r.recorder.data('first');r.recorder.data('second');r.recorder.data('third');await tick();await tick();
 assert.equal(items.size,2,'Slow delivery and failed first write must not block later durable chunks');
 const stop1=r.stop(),stop2=r.stop();assert.strictEqual(stop1,stop2);await stop1;
 assert.equal(items.size,4,'Failed storage remains retryable, including the final chunk');assert.equal(ended.length,1);
 assert(tracks.every(t=>t.readyState==='ended'));
 const stop=events.filter(e=>e.kind==='recording_stop');assert.equal(stop.length,1);assert.equal(stop[0].payload.chunks,4);assert(stop[0].payload.end_ms>=144000000);
 for(const item of items.values()){const url=new URL(item.url,location.origin);assert(Number(url.searchParams.get('end'))>=144000000);}
 releaseUpload();await drain();assert.equal((await pending()).length,0);
 // Failed event persistence blocks retry until storage works; it is never discarded.
 rejectNext=true;await assert.rejects(stash({id:'code',url:'/events',type:'application/json',body:'fixture'}));
 await retryUnstored();assert.equal(items.get('code').body,'fixture');await drain();
 const secondTracks=[new Track(),new Track()],secondEvents=[],secondEnded=[];
 const second=makeRecorder({getTracks:()=>secondTracks},{sid:'test',event:(kind,payload)=>secondEvents.push({kind,payload}),onEnded:e=>secondEnded.push(e)});
 secondTracks[0].dispatchEvent(new Event('ended'));secondTracks[1].dispatchEvent(new Event('ended'));await second.stop();await drain();
 assert.equal(secondEnded.length,1);assert.equal(secondEvents.filter(e=>e.kind==='recording_stop').length,1);assert.equal(secondEvents.find(e=>e.kind==='recording_stop').payload.state,'interrupted');
 // A rejected old record is retained while valid later records are acknowledged.
 await stash({id:'bad',url:'/bad',body:'unchanged'});await stash({id:'good',url:'/good',body:'ok'});
 global.fetch=async url=>url==='/bad'?{ok:false,status:400,text:async()=>JSON.stringify({error:'fixture rejected'})}:{ok:true};
 await assert.rejects(drain(),/미전송 기록 1개/);assert.equal(items.get('bad').body,'unchanged');assert(!items.has('good'));
 global.fetch=async()=>({ok:true});await drain();
 // Re-open the database after a transient initial failure without reloading.
 database.onclose();rejectOpen=true;await assert.rejects(stash({id:'reopen',url:'/good',body:'retained'}),/open failure/);
 await retryUnstored();assert.equal(items.get('reopen').body,'retained');await drain();
 console.log('PASS recorder protocol: held upload, durable chunks, storage/open failure retry, rejected item isolation, long offsets, final tail, duplicate stop/track-ended, stopped tracks.');
})().catch(e=>{console.error(e);process.exitCode=1;});
