// Persistent outbox: media and events remain in IndexedDB until acknowledged.
const captureHandle=crypto.randomUUID();
navigator.mediaDevices?.setCaptureHandleConfig?.({handle:captureHandle,exposeOrigin:true,permittedOrigins:[location.origin]});
let database;
const unstored=new Map();
function db(){return database ||= new Promise((resolve,reject)=>{const r=indexedDB.open('cote-lab-outbox',1);r.onupgradeneeded=()=>r.result.createObjectStore('items',{keyPath:'id'});r.onsuccess=()=>{const d=r.result;d.onversionchange=()=>{d.close();database=null;};d.onclose=()=>database=null;resolve(d);};r.onerror=()=>{database=null;reject(r.error);};});}
export async function stash(item){item={...item,order:item.order||Date.now()};unstored.set(item.id,item);const d=await db();await new Promise((resolve,reject)=>{const tx=d.transaction('items','readwrite');tx.objectStore('items').put(item);tx.oncomplete=resolve;tx.onerror=tx.onabort=()=>reject(tx.error||new Error('브라우저 기록 저장 실패'));});if(unstored.get(item.id)===item)unstored.delete(item.id);}
export async function retryUnstored(){for(const item of [...unstored.values()])await stash(item);}
async function remove(id){const d=await db();await new Promise((resolve,reject)=>{const tx=d.transaction('items','readwrite');tx.objectStore('items').delete(id);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);});}
export async function pending(){const d=await db();return new Promise((resolve,reject)=>{const r=d.transaction('items').objectStore('items').getAll();r.onsuccess=()=>resolve(r.result.sort((a,b)=>(a.order||0)-(b.order||0)));r.onerror=()=>reject(r.error);});}
let draining=null;
export function drain(status=()=>{}){
 if(draining)return draining;
 let task;
 task=(async()=>{const failed=new Map();try{
  while(true){const items=(await pending()).filter(item=>!failed.has(item.id));if(!items.length)break;
   for(let i=0;i<items.length;i++){
    const item=items[i],controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
     const res=await fetch(item.url,{method:'POST',headers:{'Content-Type':item.type},body:item.body,signal:controller.signal});
     if(!res.ok){let message=await res.text();try{message=JSON.parse(message).error}catch{}
      const error=new Error(message||`전송 오류 ${res.status}`);
      // Keep rejected records intact, but let valid records from other captures
      // and sessions through. Network/server failures still stop this attempt.
      if(res.status>=400&&res.status<500&&![408,429].includes(res.status)){failed.set(item.id,error);continue;}
      throw error;
     }
     await remove(item.id);status(items.length-i-1+failed.size);
    }finally{clearTimeout(timer);}
   }
  }
  if(failed.size)throw new Error(`미전송 기록 ${failed.size}개: ${failed.values().next().value.message}`);
 }finally{if(draining===task)draining=null;}})();
 draining=task;return task;
}
export const uid=()=>crypto.randomUUID().replaceAll('-','');
export async function prepareCapture(){
 if(!navigator.mediaDevices?.getDisplayMedia)throw new Error('화면 공유를 지원하는 Chrome/Edge에서 localhost로 접속해 주세요.');
 let screen,mic;
 try{
  screen=await navigator.mediaDevices.getDisplayMedia({video:{displaySurface:'browser',frameRate:5},audio:false,preferCurrentTab:true,selfBrowserSurface:'include',surfaceSwitching:'exclude',monitorTypeSurfaces:'exclude'});
  const surface=screen.getVideoTracks()[0].getSettings().displaySurface;
  if(surface!=='browser')throw new Error('앱 화면만 기록하도록 브라우저의 이 탭을 선택해 주세요. 전체 화면·창 녹화는 사용하지 않습니다.');
  const handle=screen.getVideoTracks()[0].getCaptureHandle?.();
  if(handle?.handle!==captureHandle)throw new Error('코테 연습실이 열린 현재 탭을 선택해 주세요. 다른 탭은 기록하지 않습니다.');
  mic=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false});
  return {screen,mic,stream:new MediaStream([...screen.getVideoTracks(),...mic.getAudioTracks()])};
 }catch(e){screen?.getTracks().forEach(t=>t.stop());mic?.getTracks().forEach(t=>t.stop());throw e;}
}
export function makeRecorder(stream,{sid,kind='screen',start=0,event=()=>{},status=()=>{},onEnded=()=>{}}){
 const type=(kind==='screen'?['video/webm;codecs=vp8,opus','video/webm','video/mp4']:['audio/webm;codecs=opus','audio/webm','audio/mp4']).find(t=>MediaRecorder.isTypeSupported(t));
 if(!type)throw new Error('이 브라우저의 녹화 형식을 지원하지 않습니다.');
 const segment=uid(),began=performance.now();let seq=0,chain=Promise.resolve(),stopping=false,stopTask=null,endMs=null,interrupted=false;
 const emit=(name,payload)=>Promise.resolve().then(()=>event(name,payload));
 const note=(name,payload)=>void emit(name,payload).catch(()=>status('브라우저 기록 저장 실패 · 탭을 닫지 마세요'));
 const time=()=>Math.round(start+performance.now()-began);
 const send=()=>drain(left=>status(left?`${left}개 전송 대기`:'기록 저장됨')).catch(()=>status('미전송 기록 있음 · 재연결 시 복구'));
 const recorder=new MediaRecorder(stream,{mimeType:type,...(kind==='screen'?{videoBitsPerSecond:450000,audioBitsPerSecond:64000}:{audioBitsPerSecond:64000})});
 // Persist every chunk before starting network delivery. A hung upload must not
 // leave later chunks waiting only in memory when the tab is closed/reloaded.
 recorder.ondataavailable=e=>{if(!e.data.size)return;const n=seq++,end=endMs??time();chain=chain.then(async()=>{const q=new URLSearchParams({sid,kind,segment,seq:String(n),start:String(start),end:String(end),mime:type.split(';')[0]});await stash({id:`${sid}-${segment}-${n}`,url:'/api/v2/upload?'+q,type:'application/octet-stream',body:e.data});status('녹화 저장 중');void send();}).catch(err=>{note('recording_gap',{segment,kind,reason:err.message});status('브라우저 저장 실패 · 탭을 닫지 마세요');});};
 const stopTracks=()=>stream.getTracks().forEach(t=>t.stop());
 function interrupt(reason){if(interrupted||stopping)return;interrupted=true;endMs=time();note('recording_gap',{segment,kind,reason,end_ms:endMs});status('녹화 중단 · 녹화 이어하기 필요');onEnded({segment,interrupted:true});if(recorder.state!=='inactive')recorder.stop();stopTracks();}
 recorder.onerror=e=>interrupt(e.error?.message||'녹화 오류');
 stream.getTracks().forEach(track=>track.addEventListener('ended',()=>interrupt('공유 또는 마이크 권한 중단')));
 // The final dataavailable precedes 'stop', so seq is the exact chunk count the server must hold.
 // Interrupted recordings (shared tab closed, permission revoked) report it too.
 const ended=new Promise(resolve=>recorder.addEventListener('stop',async()=>{endMs??=time();stopTracks();try{await chain;await emit('recording_stop',{segment,kind,chunks:seq,state:interrupted||!stopping?'interrupted':'stopped',end_ms:endMs});}catch{status('브라우저 기록 저장 실패 · 탭을 닫지 마세요');}finally{if(!interrupted)onEnded({segment,interrupted:!stopping});resolve();}},{once:true}));
 try{recorder.start(3000);note('recording_start',{segment,kind,offset_ms:start});}catch(err){stopTracks();throw err;}
 return {segment,recorder,stop:()=>stopTask||=(async()=>{stopping=true;endMs??=time();if(recorder.state!=='inactive')recorder.stop();stopTracks();await ended;await retryUnstored();return segment;})().catch(e=>{stopTask=null;throw e;})};
}
