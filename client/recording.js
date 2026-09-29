// Persistent outbox: media and events remain in IndexedDB until acknowledged.
const captureHandle=crypto.randomUUID();
navigator.mediaDevices?.setCaptureHandleConfig?.({handle:captureHandle,exposeOrigin:true,permittedOrigins:[location.origin]});
let database;
function db(){return database ||= new Promise((resolve,reject)=>{const r=indexedDB.open('cote-lab-outbox',1);r.onupgradeneeded=()=>r.result.createObjectStore('items',{keyPath:'id'});r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});}
export async function stash(item){const d=await db();await new Promise((resolve,reject)=>{const tx=d.transaction('items','readwrite');tx.objectStore('items').put({...item,order:item.order||Date.now()});tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);});}
async function remove(id){const d=await db();await new Promise((resolve,reject)=>{const tx=d.transaction('items','readwrite');tx.objectStore('items').delete(id);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);});}
export async function pending(){const d=await db();return new Promise((resolve,reject)=>{const r=d.transaction('items').objectStore('items').getAll();r.onsuccess=()=>resolve(r.result.sort((a,b)=>(a.order||0)-(b.order||0)));r.onerror=()=>reject(r.error);});}
let draining=null;
export function drain(status=()=>{}){
 if(draining)return draining;
 draining=(async()=>{while(true){const items=await pending();if(!items.length)break;for(let i=0;i<items.length;i++){const item=items[i];const res=await fetch(item.url,{method:'POST',headers:{'Content-Type':item.type},body:item.body});if(!res.ok){let message=await res.text();try{message=JSON.parse(message).error}catch{}throw new Error(message);}await remove(item.id);status(items.length-i-1);}}})().finally(()=>draining=null);
 return draining;
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
export function makeRecorder(stream,{sid,kind='screen',start=0,event=()=>{},status=()=>{}}){
 const type=(kind==='screen'?['video/webm;codecs=vp8,opus','video/webm','video/mp4']:['audio/webm;codecs=opus','audio/webm','audio/mp4']).find(t=>MediaRecorder.isTypeSupported(t));
 if(!type)throw new Error('이 브라우저의 녹화 형식을 지원하지 않습니다.');
 const segment=uid();let seq=0,chain=Promise.resolve(),failure=null,stopping=false;
 const recorder=new MediaRecorder(stream,{mimeType:type,...(kind==='screen'?{videoBitsPerSecond:450000,audioBitsPerSecond:64000}:{audioBitsPerSecond:64000})});
 recorder.ondataavailable=e=>{if(!e.data.size)return;const n=seq++;chain=chain.then(async()=>{const q=new URLSearchParams({sid,kind,segment,seq:String(n),start:String(start),mime:type.split(';')[0]});await stash({id:`${sid}-${segment}-${n}`,url:'/api/v2/upload?'+q,type:'application/octet-stream',body:e.data});status('녹화 저장 중');try{await drain(left=>status(left?`${left}개 전송 대기`:'기록 저장됨'));}catch(err){status('미전송 기록 있음 · 재연결 시 복구');}}).catch(err=>{failure=err;event('recording_gap',{reason:err.message});status('브라우저 저장 실패 · 녹화를 확인하세요');});};
 recorder.onerror=e=>{failure=e.error||new Error('녹화 오류');event('recording_gap',{reason:failure.message});};
 stream.getTracks().forEach(track=>track.addEventListener('ended',()=>{if(!stopping){event('recording_gap',{reason:'공유 또는 마이크 권한 중단',track:track.kind});status('녹화 중단 · 기록 재연결 필요');if(recorder.state!=='inactive')recorder.stop();}}));
 recorder.start(5000);event('recording_start',{segment,kind,offset_ms:start});
 return {segment,recorder,stop:async()=>{stopping=true;if(recorder.state!=='inactive')await new Promise(resolve=>{recorder.addEventListener('stop',resolve,{once:true});recorder.stop();});await chain;stream.getTracks().forEach(t=>t.stop());event('recording_stop',{segment,kind,chunks:seq});if(failure)throw failure;return segment;}};
}
