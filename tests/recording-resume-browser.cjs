const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.env.RECORDING_FIXTURE_URL||'http://localhost:8881';
const output=process.env.RECORDING_TEST_OUTPUT||'/tmp/cote-recording-resume-proof';
fs.mkdirSync(output,{recursive:true});
const captureFixture=()=>{
 if(!navigator.mediaDevices)return; // about:blank is not a secure media origin.
 let handle;window.captureCalls=0;window.fixtureTracks=[];window.cancelNext=false;
 navigator.mediaDevices.setCaptureHandleConfig=config=>handle=config;
 navigator.mediaDevices.getDisplayMedia=async()=>{
  window.captureCalls++;if(window.cancelNext){window.cancelNext=false;throw new DOMException('fixture cancelled','NotAllowedError');}
  if(window.captureGate)await new Promise(resolve=>window.releaseCapture=resolve);
  const c=document.createElement('canvas');c.width=640;c.height=360;const ctx=c.getContext('2d');
  const paint=()=>{ctx.fillStyle='#20283b';ctx.fillRect(0,0,640,360);ctx.fillStyle='white';ctx.font='25px sans-serif';ctx.fillText('ISOLATED RECORDING '+Date.now(),20,100);};paint();setInterval(paint,100);
  const stream=c.captureStream(5),track=stream.getVideoTracks()[0],settings=track.getSettings.bind(track);
  track.getSettings=()=>({...settings(),displaySurface:'browser'});track.getCaptureHandle=()=>handle;
  window.fixtureTracks.push(track);return stream;
 };
 navigator.mediaDevices.getUserMedia=async()=>{
  const audio=new AudioContext(),osc=audio.createOscillator(),gain=audio.createGain(),dest=audio.createMediaStreamDestination();
  gain.gain.value=.001;osc.connect(gain);gain.connect(dest);osc.start();await audio.resume();window.fixtureTracks.push(...dest.stream.getTracks());return dest.stream;
 };
 window.interruptCapture=()=>{const track=window.fixtureTracks.find(t=>t.kind==='video'&&t.readyState==='live');track.stop();track.dispatchEvent(new Event('ended'));};
};
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required']});
 const context=await browser.newContext({baseURL:base,viewport:{width:1450,height:1000}});const errors=[];
 await context.addInitScript(captureFixture);
 context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
 let page=await context.newPage();
 const state=async sid=>(await (await context.request.get('/api/v2/session/'+sid)).json());
 const open=async sid=>{await page.goto('/');await page.locator(`[data-session="${sid}"]`).first().click();await page.locator('#editor .monaco-editor').waitFor();};
 const resumed=()=>page.locator('#record-status.on').waitFor();
 const disconnected=()=>page.locator('[data-action="reconnect"]:enabled').waitFor();
 const chunkWait=async sid=>{const before=(await state(sid)).media.reduce((n,m)=>n+m.chunks,0);for(let i=0;i<60;i++){await page.waitForTimeout(100);if((await state(sid)).media.reduce((n,m)=>n+m.chunks,0)>before)return;}throw new Error('No recorded chunk');};
 try{
  const response=await context.request.post('/api/v2/start',{data:{problem_ids:['pair'],kind:'single',mode:'exam',duration:30,resources:'none',model:'test-model',effort:'low'}});assert(response.ok());const s=await response.json(),sid=s.id;
  await open(sid);assert.equal(await page.evaluate(()=>captureCalls),0);assert(await page.locator('#recording-help').isVisible());
  // Permission cancellation leaves the session usable, no recorder or capture leak.
  await page.evaluate(()=>cancelNext=true);await page.locator('[data-action="reconnect"]').click();await disconnected();assert.equal(await page.evaluate(()=>fixtureTracks.length),0);
  // Repeated click while the picker is pending produces exactly one capture.
  await page.evaluate(()=>captureGate=true);await page.locator('[data-action="reconnect"]').click();await page.evaluate(()=>document.querySelector('[data-action="reconnect"]').dispatchEvent(new MouseEvent('click',{bubbles:true})));
  assert.equal(await page.evaluate(()=>captureCalls),2);await page.evaluate(()=>{captureGate=false;releaseCapture();});await resumed();await chunkWait(sid);
  await page.screenshot({path:output+'/recording-desktop.png',fullPage:true});
  const first=(await state(sid)).media[0].segment;
  // Refresh loses permission by design and requires the explicit resume button.
  await page.reload();await page.locator(`[data-session="${sid}"]`).first().click();await disconnected();assert.equal(await page.evaluate(()=>captureCalls),0);
  await page.locator('[data-action="reconnect"]').click();await resumed();await chunkWait(sid);assert((await state(sid)).media.some(m=>m.segment===first));
  // A slow upload must not block durable persistence of later chunks.
  let held=false,release;const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/v2/upload?**',async route=>{held=true;await gate;try{await route.continue();}catch(e){if(!page.isClosed())throw e;}});
  await page.waitForFunction(()=>true);for(let i=0;!held&&i<50;i++)await page.waitForTimeout(100);assert(held);
  await page.waitForTimeout(3400);
  const pendingMedia=await page.evaluate(async()=>{const d=await new Promise(ok=>{const r=indexedDB.open('cote-lab-outbox',1);r.onsuccess=()=>ok(r.result);});return await new Promise(ok=>{const r=d.transaction('items').objectStore('items').getAll();r.onsuccess=()=>{d.close();ok(r.result.filter(x=>x.url.startsWith('/api/v2/upload?')).length);};});});
  assert(pendingMedia>=2,'Later chunks must persist while first upload is held');release();await page.unrouteAll({behavior:'wait'});
  // Repeated interruptions and resume, including duplicate stop signals.
  await page.evaluate(()=>{interruptCapture();fixtureTracks[0].dispatchEvent(new Event('ended'));});await disconnected();
  await page.locator('[data-action="reconnect"]').click();await resumed();await chunkWait(sid);
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>interruptCapture());await disconnected();
  assert(await page.evaluate(()=>Math.abs(document.querySelector('.ws-body').getBoundingClientRect().top-document.querySelector('.ws-panes').getBoundingClientRect().bottom)<2),'Mobile editor must directly follow pane tabs');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:output+'/resume-mobile.png',fullPage:true});
  // Tab close/reentry: same isolated context/IDB, no automatic capture.
  await page.close();page=await context.newPage();await open(sid);await disconnected();assert.equal(await page.evaluate(()=>captureCalls),0);
  await page.locator('[data-action="reconnect"]').click();await resumed();await chunkWait(sid);
  await page.locator('[data-action="finish"]').click();await page.locator('[data-confirm="ok"]').click();await page.locator('#replay-video').waitFor({state:'attached',timeout:30000});
  let saved=await state(sid);assert.equal(saved.status,'completed');assert(saved.media.length>=4);assert(saved.recording_gaps.length>0);assert(saved.media.some(m=>m.ended==='interrupted'));
  assert.equal(new Set(saved.media.map(m=>m.segment)).size,saved.media.length);assert(saved.media.every(m=>m.start_ms>=0));
  const playable=saved.media.filter(m=>m.playable).sort((a,b)=>a.start_ms-b.start_ms);
  await page.locator('[data-action="replay-all"]').click();await page.waitForFunction(id=>document.querySelector('#replay-video').dataset.segment===id,playable[0].segment);
  await page.waitForFunction(()=>document.querySelector('#replay-video').readyState>=1);await page.evaluate(()=>{const v=document.querySelector('#replay-video');v.currentTime=Math.max(0,v.duration-.08);v.play();});
  await page.waitForFunction(id=>document.querySelector('#replay-video').dataset.segment===id,playable[1].segment,{timeout:10000});
  // Actual ffmpeg decodes each capture separately. STT and model inference are fixture stubs.
  await page.locator('[data-action="transcribe"]').click();await page.waitForFunction(()=>!document.querySelector('[data-action="transcribe"]').disabled,null,{timeout:60000});
  saved=await state(sid);assert(saved.transcripts.length>=2);assert(saved.transcription_state.processed_segments.length>=2);
  await page.locator('[data-action="analyze"]').click();await page.getByText('중단 전후 접근을 설명해 주세요.',{exact:true}).waitFor({timeout:60000});
  await page.locator('[data-action="report"]').click();await page.getByRole('heading',{name:'이번 연습에서 다음 연습으로'}).waitFor({timeout:30000});
  saved=await state(sid);assert.equal(saved.report.version,2);await page.screenshot({path:output+'/recording-review.png',fullPage:true});
  // Finish while a permission picker is pending: no late capture can outlive the session.
  const race=await (await context.request.post('/api/v2/start',{data:{problem_ids:['pair'],kind:'single',mode:'exam',duration:30,resources:'none',model:'test-model',effort:'low'}})).json();
  await open(race.id);await page.evaluate(()=>captureGate=true);await page.locator('[data-action="reconnect"]').click();
  await page.locator('[data-action="finish"]').click();await page.locator('[data-confirm="ok"]').click();await page.locator('#recording-segments').waitFor();
  await page.evaluate(()=>{captureGate=false;releaseCapture();});await page.waitForTimeout(250);
  assert.equal((await state(race.id)).status,'completed');assert.equal((await state(race.id)).media.length,0);assert(await page.evaluate(()=>fixtureTracks.every(t=>t.readyState==='ended')));
  assert.deepEqual(errors,[]);
  const proof={status:'passed',sid,segments:saved.media.map(m=>({segment:m.segment,chunks:m.chunks,status:m.status,ended:m.ended,start_ms:m.start_ms,end_ms:m.end_ms})),gaps:saved.recording_gaps,transcripts:saved.transcripts.length,report_version:saved.report.version,slow_upload_persisted_chunks:pendingMedia,page_errors:errors,synthetic_capture:true,real_media_decode:true,model_and_stt_mocked:true,real_browser_profile_used:false};
  fs.writeFileSync(output+'/browser-verification.json',JSON.stringify(proof,null,2));console.log(JSON.stringify(proof));
 }catch(e){console.error('Browser scenario failed:',e);if(!page.isClosed()){await page.screenshot({path:output+'/failure.png',fullPage:true});fs.writeFileSync(output+'/failure.txt',await page.locator('body').innerText());}throw e;}finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
