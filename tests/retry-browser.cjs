/* Retry-v1 DOM/recording acceptance. Always starts its own disposable fixture.
 * Run in the latest isolated source snapshot after `npm run build`:
 *   RETRY_TEST_OUTPUT=/tmp/p3-retry-browser node tests/retry-browser.cjs
 * `--harness-smoke` verifies only the disposable browser/capture environment.
 * It cannot satisfy V4/V8/V9/V12. Java/IDE/coach/STT/media subprocesses are mocked.
 * No existing browser/profile/server, external AI, Docker, or network origin used.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {createHash} = require('node:crypto');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const output = process.env.RETRY_TEST_OUTPUT || fs.mkdtempSync(path.join(os.tmpdir(), 'retry-p3-browser-'));
const harnessOnly = process.argv.includes('--harness-smoke');
const fault = process.argv.find(x=>x.startsWith('--fault='))?.slice(8) || null;
assert([null,'delete-rejected-item','block-current-finish'].includes(fault),'Unknown isolated fault');
fs.mkdirSync(output, {recursive:true});

async function fixture() {
  const child = spawn(process.env.RETRY_TEST_PYTHON || 'python3', ['-u', path.join(__dirname, 'retry_fixture.py'), '--browser', '--port', '0'],
    {cwd:root, env:{...process.env, PYTHONDONTWRITEBYTECODE:'1'}, stdio:['ignore','pipe','pipe']});
  let stderr = '', stdout = '';
  child.stderr.on('data', b => {stderr += b;});
  const ready = new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('FIXTURE_ERROR: handshake timeout: ' + stderr)), 15000);
    child.once('error', e => {clearTimeout(timeout); reject(e);});
    child.once('exit', code => {clearTimeout(timeout); reject(new Error(`FIXTURE_ERROR: exit ${code}: ${stderr}`));});
    child.stdout.on('data', b => {
      stdout += b;
      const newline = stdout.indexOf('\n');
      if (newline < 0) return;
      clearTimeout(timeout);
      try {resolve(JSON.parse(stdout.slice(0,newline)));} catch (e) {reject(e);}
    });
  });
  const stop = async () => {
    if (child.exitCode !== null || child.signalCode !== null) return;
    const ended = once(child, 'exit');
    child.kill('SIGTERM');
    const forced = setTimeout(() => child.kill('SIGKILL'), 8000);
    await ended; clearTimeout(forced);
  };
  try {
    const h = await ready;
    assert.equal(h.fixture, 'retry-v1-p3');
    assert.match(h.base_url, /^http:\/\/127\.0\.0\.1:\d+$/);
    assert.equal(h.pid_process, child.pid);
    assert.equal(h.external_ai_calls, 0);
    return {h, stop};
  } catch (e) {await stop(); throw e;}
}

function captureFixture({outboxFault=null}={}) {
  let handle;
  window.p3CaptureCalls = 0;
  window.p3Tracks = [];
  window.p3RecorderEvents = [];
  const NativeRecorder=window.MediaRecorder;
  window.MediaRecorder=class extends NativeRecorder {
    constructor(...args) {
      super(...args);const id=window.p3CaptureCalls;
      this.addEventListener('dataavailable',e=>window.p3RecorderEvents.push({capture:id,event:'dataavailable',bytes:e.data.size}));
      this.addEventListener('stop',()=>window.p3RecorderEvents.push({capture:id,event:'stop'}));
    }
  };
  // Mutations live only in this disposable profile. No product file is edited.
  if(outboxFault){
    const nativeFetch=window.fetch.bind(window);
    window.fetch=async(...args)=>{
      const url=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href);
      const response=await nativeFetch(...args);
      if(outboxFault==='delete-rejected-item' && window.p3HeldOld && response.status===400 &&
         url.pathname==='/api/v2/upload' && url.searchParams.get('sid')===window.p3HeldOld.sid){
        const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('cote-lab-outbox',1);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
        await new Promise((resolve,reject)=>{const tx=db.transaction('items','readwrite');tx.objectStore('items').delete(window.p3HeldOld.id);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);});
        db.close();
      }
      return response;
    };
  }
  window.p3NotificationCalls = 0;
  if (window.Notification) Notification.requestPermission = async () => {
    window.p3NotificationCalls++; throw new Error('Retry MVP must not ask for notifications');
  };
  if (!navigator.mediaDevices) return;
  navigator.mediaDevices.setCaptureHandleConfig = config => {handle = config;};
  navigator.mediaDevices.getDisplayMedia = async () => {
    window.p3CaptureCalls++;
    if (window.p3CancelCapture) {window.p3CancelCapture=false; throw new DOMException('P3 synthetic cancellation', 'NotAllowedError');}
    const canvas=document.createElement('canvas'); canvas.width=640; canvas.height=360;
    const ctx=canvas.getContext('2d');
    const paint=()=>{ctx.fillStyle='#182131';ctx.fillRect(0,0,640,360);ctx.fillStyle='#fff';ctx.font='25px sans-serif';ctx.fillText('P3 ISOLATED RETRY '+Date.now(),25,100);};
    paint(); const interval=setInterval(paint,100);
    const stream=canvas.captureStream(5), track=stream.getVideoTracks()[0], settings=track.getSettings.bind(track);
    track.getSettings=()=>({...settings(),displaySurface:'browser'});
    track.getCaptureHandle=()=>handle;
    track.addEventListener('ended',()=>clearInterval(interval),{once:true});
    window.p3Tracks.push(track); return stream;
  };
  navigator.mediaDevices.getUserMedia=async()=>{
    const audio=new AudioContext(), oscillator=audio.createOscillator(), gain=audio.createGain(), destination=audio.createMediaStreamDestination();
    gain.gain.value=.001;oscillator.connect(gain);gain.connect(destination);oscillator.start();await audio.resume();
    window.p3Tracks.push(...destination.stream.getTracks());return destination.stream;
  };
  window.p3Interrupt=()=>{
    const track=window.p3Tracks.find(t=>t.kind==='video' && t.readyState==='live');
    if (!track) throw new Error('No synthetic capture to interrupt');
    track.stop(); track.dispatchEvent(new Event('ended'));
  };
}

function noPast(h, value, label) {
  const raw=typeof value==='string'?value:JSON.stringify(value);
  for (const [name,sentinel] of Object.entries(h.sentinels)) assert(!raw.includes(sentinel), `${label}: leaked past ${name}`);
}

async function outboxItem(page,id){
 return page.evaluate(async id=>{
  const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('cote-lab-outbox',1);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
  const item=await new Promise((resolve,reject)=>{const r=db.transaction('items').objectStore('items').get(id);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});db.close();
  if(!item)return null;
  const bytes=await item.body.arrayBuffer(),hash=await crypto.subtle.digest('SHA-256',bytes);
  return {id:item.id,url:item.url,type:item.type,order:item.order,bytes:bytes.byteLength,
   sha256:Array.from(new Uint8Array(hash),x=>x.toString(16).padStart(2,'0')).join('')};
 },id);
}

function recordingStop(page,sid,segment){
 return page.waitForResponse(r=>{
  if(!r.url().endsWith('/api/v2/events') || r.request().method()!=='POST' || r.status()!==200)return false;
  const body=r.request().postDataJSON();
  return body?.sid===sid && body.events?.some(e=>e.kind==='recording_stop' && e.payload?.segment===segment);
 });
}

async function runRetryBrowser(){
  let owner, browser, context, page;
  const proof={status:'running',harness_only:harnessOnly,injected_fault:fault,tests:[],page_errors:[],console_errors:[],expected_http_errors:[],unexecuted:[],
    synthetic_capture:true,java_mocked:true,ide_mocked:true,model_and_stt_mocked:true,external_ai_calls:0,
    real_browser_profile_used:false,real_java_verified:false,real_media_decode_verified:false};
  const passed=name=>proof.tests.push({name,status:'passed'});
  const expectedConsole=[];
  const expectFailure=(url,status,network=false)=>expectedConsole.push({url,status,network,consumed:false});
  const captureFailure=async(name,e,p)=>{
    proof.tests.push({name,status:'failed',error:e.stack||String(e)});
    await p.screenshot({path:path.join(output,name+'.png'),fullPage:true});
    fs.writeFileSync(path.join(output,name+'.html'),await p.locator('html').evaluate(e=>e.outerHTML));
  };
  try {
    owner=await fixture();const h=owner.h;
    const control=async(name,data)=>{
      const response=await fetch(h.base_url+'/_retry_fixture/'+name,{method:data?'POST':'GET',headers:{'X-Retry-Fixture':h.token,'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});
      assert.equal(response.status,200);return response.json();
    };
    const api=async(url,data)=>{
      const response=await fetch(h.base_url+url,{method:data?'POST':'GET',headers:{'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});
      const value=await response.json();
      if (response.status!==200 && value.error==='경로 없음') throw new Error('EXPECTED_API_NOT_IMPLEMENTED: '+url);
      assert.equal(response.status,200,JSON.stringify(value));return value;
    };
    const before=await control('proof');
    if (!harnessOnly) {
      // Feature absence fails before UI launch; it is not a fake browser PASS.
      const dto=await api('/api/v2/retries');
      for(const key of ['active','due','grading','next_refresh_at','server_now','unavailable','upcoming'])assert(Object.hasOwn(dto,key),'Missing v1 list field '+key);
      noPast(h,dto,'initial retries API');passed('retry API preflight');
      proof.anchor_capability=dto.capabilities??null;
      try{
        assert.equal(dto.capabilities?.retry_plan_expected_anchor_date,true,'CONTRACT-R2-003 backend guard capability is not exact true');
        passed('V9-AD2 actual backend anchor capability');
      }catch(e){
        // Keep this product failure, but execute recording/outbox and every
        // independent later gate rather than stopping at mixed-version support.
        proof.tests.push({name:'V9-AD2 actual backend anchor capability',status:'failed',error:e.stack||String(e)});
      }
    }
    browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required']});
    proof.chromium_version=browser.version();
    const freshContext=async()=>{
     const c=await browser.newContext({baseURL:h.base_url,viewport:{width:1450,height:1000},timezoneId:'Asia/Seoul'});
     c.setDefaultTimeout(10000);
     await c.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if((url.protocol==='http:'||url.protocol==='https:') && url.origin!==h.base_url) {
        proof.page_errors.push('Blocked non-fixture origin: '+url.origin);
        await route.abort();return;
      }
      await route.continue();
    });
     await c.addInitScript(captureFixture,{outboxFault:fault});
     c.on('page',p=>{
      p.on('pageerror',e=>proof.page_errors.push(e.message));
      p.on('console',m=>{
        if (m.type()!=='error') return;
        const match=expectedConsole.find(x=>!x.consumed && x.url===m.location().url &&
          (x.network?/net::ERR_FAILED/.test(m.text()):new RegExp('\\b'+x.status+'\\b').test(m.text())));
        if(match){match.consumed=true;proof.expected_http_errors.push({url:match.url,status:match.status,network:match.network,text:m.text()});}
        else proof.console_errors.push({url:m.location().url,text:m.text()});
      });
     });
     return c;
    };
    context=await freshContext();
    page=await context.newPage();
    if (harnessOnly) {
      // Scratch HTML is served by a page route, never written into product sources.
      await page.route('**/p3-capture-harness',r=>r.fulfill({contentType:'text/html',body:'<!doctype html><html lang="ko"><title>P3 fixture harness</title><h1>격리 캡처 환경 확인</h1></html>'}));
      await page.goto('/p3-capture-harness');
      const bytes=await page.evaluate(async()=>{
        navigator.mediaDevices.setCaptureHandleConfig({handle:'p3-harness'});
        const stream=await navigator.mediaDevices.getDisplayMedia();
        const recorder=new MediaRecorder(stream,{mimeType:'video/webm'});
        const chunk=new Promise(resolve=>recorder.ondataavailable=e=>{if(e.data.size)resolve(e.data.size);});
        recorder.start(100);
        const size=await chunk, stopped=new Promise(resolve=>recorder.addEventListener('stop',resolve,{once:true}));
        recorder.stop();await stopped;stream.getTracks().forEach(t=>t.stop());return size;
      });
      assert(bytes>0,'Synthetic MediaRecorder produced no bytes');
      assert.deepEqual(proof.page_errors,[]);assert.deepEqual(proof.console_errors,[]);
      await page.screenshot({path:path.join(output,'harness.png')});
      passed('owned fixture + fresh headless browser + synthetic MediaRecorder bytes');
      proof.status='harness_only_passed';proof.synthetic_bytes=bytes;
      proof.unverified=['V4/V8/V9 product behavior','V12 actual Java and media decode'];
      return;
    }

    const requests=[];context.on('request',r=>requests.push({method:r.method(),url:r.url(),body:r.postData()}));
    await page.goto('/');
    await page.locator(`[data-session="${h.source_sid}"]`).first().click();
    const registration=page.locator('[data-retry-registration]').first();
    await registration.waitFor();
    const reason=registration.getByLabel(/실패 이유/);
    await reason.waitFor();
    const save=registration.getByRole('button',{name:'1·3·7일 뒤 예약',exact:true});
    const tz=registration.getByLabel('일정 기준 시간대',{exact:true});
    await tz.fill('Asia/Seoul');
    const confirmed=registration.getByRole('checkbox');
    assert.equal(await confirmed.isChecked(),false,'Timezone edit must clear confirmation');
    await confirmed.check();
    await reason.fill('🧠'.repeat(201));
    const countBefore=requests.filter(r=>r.url.endsWith('/api/v2/retry-plan')).length;
    if (await save.isEnabled()) {
      await save.focus();await save.press('Space');
      await page.getByRole('status').filter({hasText:/200/}).first().waitFor();
    }
    assert.equal(requests.filter(r=>r.url.endsWith('/api/v2/retry-plan')).length,countBefore,'201 codepoints sent to server');
    await reason.fill('🧠'.repeat(200));assert(await save.isEnabled(),'200 Unicode codepoints rejected as UTF-16 length');
    const savedReason=`<img src=x onerror="window.p3Xss=1"> ${h.sentinels.reason}`;
    await reason.fill(savedReason);
    await tz.fill('Etc/UTC');assert.equal(await confirmed.isChecked(),false);
    await tz.fill('Asia/Seoul');await confirmed.check();
    // One rejected save must leave input and retry possible, with explicit status.
    await page.route('**/api/v2/retry-plan',async route=>{
      expectFailure(route.request().url(),400);
      await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'P3 격리 저장 실패 · 다시 시도하세요',code:'INVALID_INPUT'})});
      await page.unroute('**/api/v2/retry-plan');
    });
    const rejectedSave=page.waitForResponse(r=>r.url().endsWith('/api/v2/retry-plan') && r.status()===400);
    await save.focus();await save.press('Space');await rejectedSave;
    await registration.getByRole('status').filter({hasText:'입력 내용을 확인해 주세요.'}).waitFor();
    assert.equal(await reason.inputValue(),savedReason);
    assert.equal(await tz.inputValue(),'Asia/Seoul');assert(await confirmed.isChecked());
    assert(await save.isEnabled());
    const registered=page.waitForResponse(r=>r.url().endsWith('/api/v2/retry-plan') && r.status()===200);
    await save.focus();await save.press('Space');
    const registeredResponse=await registered,saved=await registeredResponse.json();
    assert(saved.created);assert.equal(saved.plan.reason,savedReason);
    assert.deepEqual(saved.plan.items.map(i=>i.due_date),['2026-10-02','2026-10-04','2026-10-08']);
    assert.equal(await page.evaluate(()=>window.p3Xss),undefined);passed('V9 registration: keyboard, error/retry, 200 codepoints, escaped reason');
    proof.anchor_registration_request=registeredResponse.request().postDataJSON();
    try{
      assert.equal(proof.anchor_registration_request.expected_anchor_date,'2026-10-01','CONTRACT-R2-003 new UI omitted the confirmed anchor');
      assert.equal(proof.anchor_registration_request.timezone,'Asia/Seoul');
      passed('V9-AD1 confirmed tuple included in actual registration');
    }catch(e){await captureFailure('V9-AD1-confirmed-anchor-missing',e,page);}
    await page.getByRole('button',{name:'대시보드',exact:true}).click();
    await page.getByRole('heading',{name:'오늘 다시 풀 문제',exact:true}).waitFor();
    assert.equal((await api('/api/v2/retries')).upcoming.length,1);passed('V9 upcoming safe list');
    await control('advance',{ms:7*86400000});
    await page.reload();
    await page.getByRole('heading',{name:'오늘 다시 풀 문제',exact:true}).waitFor();
    const retryRegion=p=>p.locator('[data-retry-view="list"]');
    await retryRegion(page).locator('[data-retry-state="due"]').waitFor();
    noPast(h,await retryRegion(page).evaluate(e=>e.outerHTML),'due retry region DOM');
    const dueDto=await api('/api/v2/retries');
    assert.equal(dueDto.due[0].outstanding_due_count,3);
    const sourceFetches=()=>requests.filter(r=>r.url.endsWith('/api/v2/session/'+h.source_sid)).length;
    const priorFetches=sourceFetches();
    await page.getByRole('button',{name:'다시 풀기',exact:true}).first().click();
    await page.getByRole('button',{name:/녹화 준비 후 시작|재도전 시작/}).waitFor();
    await page.locator('.model-box summary').click();
    await page.locator('#setup-model').selectOption('fixture-model');
    noPast(h,await page.locator('html').evaluate(e=>e.outerHTML),'retry setup DOM');
    assert.equal(sourceFetches(),priorFetches,'Retry defaults fetched full source session');
    await page.evaluate(()=>{window.p3CancelCapture=true;});
    await page.getByRole('button',{name:/녹화 준비 후 시작|재도전 시작/}).click();
    await page.getByRole('status').filter({hasText:/synthetic cancellation/}).first().waitFor();
    assert.equal(requests.filter(r=>r.url.endsWith('/api/v2/retry-start')).length,0,'Capture cancellation started a retry');
    assert(await page.evaluate(()=>p3Tracks.every(t=>t.readyState==='ended')));
    assert.equal((await api('/api/v2/retries')).due[0].session_id,null);
    passed('V9 capture cancellation preserves item and cleans tracks');
    const starting=page.waitForResponse(r=>r.url().endsWith('/api/v2/retry-start') && r.status()===200);
    const firstUpload=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/v2/upload' && r.status()===200);
    await page.getByRole('button',{name:/녹화 준비 후 시작|재도전 시작/}).click();
    const startDto=await (await starting).json(),sid=startDto.session.id;
    assert.notEqual(sid,h.source_sid);assert.equal(startDto.session.codes[h.pid],h.starter);
    await page.locator('#editor .monaco-editor').waitFor();
    noPast(h,await page.locator('html').evaluate(e=>e.outerHTML),'active retry DOM');
    noPast(h,await api('/api/v2/session/'+sid),'active retry JSON');
    assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('lab-active'))),sid);
    assert.equal(sourceFetches(),priorFetches);
    await page.getByRole('button',{name:'힌트 받기',exact:true}).click();
    await page.getByText('격리 모의 새 힌트',{exact:true}).waitFor();
    const hinted=await control('proof');
    noPast(h,hinted.coach_calls,'actual hint prompt');
    assert.equal(hinted.coach_calls[0].thread,null);
    assert.deepEqual(hinted.coach_calls[0].prompt.evidence.past_reports,[]);
    await page.screenshot({path:path.join(output,'active-blind.png'),fullPage:true});passed('V4 safe DOM/JSON/setup/fresh actual hint prompt');

    // Status "recording" and the server mock clock say nothing about real bytes.
    // Synchronize on a genuine nonempty upload before stopping each capture.
    const firstResponse=await firstUpload;
    const firstQuery=new URL(firstResponse.url()).searchParams;
    assert.equal(firstQuery.get('sid'),sid);
    const originalSegment=firstQuery.get('segment');
    assert((await control('proof')).upload_attempts.some(x=>x.sid===sid && x.segment===originalSegment && x.status===200 && x.bytes>0));
    await control('advance',{ms:15000});
    const firstStop=recordingStop(page,sid,originalSegment);
    await page.evaluate(()=>p3Interrupt());await firstStop;
    await page.locator('[data-action="reconnect"]:enabled').waitFor();
    let view=await api('/api/v2/session/'+sid);assert.equal(view.media.length,1);
    assert.equal(view.media[0].segment,originalSegment);assert(view.media[0].bytes>0 && view.media[0].playable);
    proof.first_capture=await page.evaluate(()=>p3RecorderEvents);
    await page.reload();
    await page.locator(`[data-session="${sid}"]`).first().click();
    await page.locator('#editor .monaco-editor').waitFor();
    assert.equal(await page.evaluate(()=>p3CaptureCalls),0,'Reentry captured without user action');
    // The fixture really receives the old-sid POST and returns 400. This route
    // forwards it only to classify its expected browser console error exactly.
    const oldId='p3-old-outbox',oldSegment='e'.repeat(32),oldBytes='P3 old delayed fake bytes';
    await control('upload-rejection',{sid:h.source_sid,segment:oldSegment,reject:true});
    await page.route('**/api/v2/upload?**',async route=>{
      const query=new URL(route.request().url()).searchParams;
      if(query.get('sid')===h.source_sid && query.get('segment')===oldSegment){
        const response=await route.fetch();assert.equal(response.status(),400);
        expectFailure(route.request().url(),400);return route.fulfill({response});
      }
      await route.continue();
    });
    await page.evaluate(async({sourceSid,oldId,oldSegment,oldBytes})=>{
      const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('cote-lab-outbox',1);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
      await new Promise((resolve,reject)=>{const tx=db.transaction('items','readwrite');tx.objectStore('items').put({id:oldId,order:1,url:`/api/v2/upload?sid=${sourceSid}&kind=screen&segment=${oldSegment}&seq=0&start=0&mime=video%2Fwebm`,type:'application/octet-stream',body:new Blob([oldBytes])});tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);});db.close();
      window.p3HeldOld={sid:sourceSid,id:oldId};
    },{sourceSid:h.source_sid,oldId,oldSegment,oldBytes});
    const oldBefore=await outboxItem(page,oldId);
    assert.equal(oldBefore.sha256,createHash('sha256').update(oldBytes).digest('hex'));
    const rejectedUpload=page.waitForResponse(r=>r.url()===h.base_url+oldBefore.url && r.status()===400);
    await page.evaluate(()=>window.dispatchEvent(new Event('online')));await rejectedUpload;
    await page.getByText('미전송 기록 1개: P3 old upload held',{exact:true}).first().waitFor();
    const oldAfterRejection=await outboxItem(page,oldId);
    assert.deepEqual(oldAfterRejection,oldBefore,'R3-003 rejected old item lost or changed');
    const rejectedProof=await control('proof');
    assert(rejectedProof.upload_attempts.filter(x=>x.sid===h.source_sid && x.segment===oldSegment && x.status===400).length>=1,
      'R3-003 requires an actual rejected old-sid request');
    assert.deepEqual(before.source_snapshot,rejectedProof.source_snapshot);

    await control('advance',{ms:15000});
    const secondUpload=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/v2/upload' &&
      new URL(r.url()).searchParams.get('sid')===sid && r.status()===200);
    await page.locator('[data-action="reconnect"]:enabled').click();
    await page.locator('#record-status.on').waitFor();
    noPast(h,await page.locator('html').evaluate(e=>e.outerHTML),'resumed retry DOM');
    const secondSegment=new URL((await secondUpload).url()).searchParams.get('segment');
    assert.notEqual(secondSegment,originalSegment);
    const secondStop=recordingStop(page,sid,secondSegment);
    await page.evaluate(()=>p3Interrupt());await secondStop;
    view=await api('/api/v2/session/'+sid);
    assert.equal(view.media.length,2);assert(view.media.every(m=>m.bytes>0 && m.playable));
    assert(view.media.some(m=>m.segment===originalSegment));assert(view.recording_gaps.length>0);
    assert.deepEqual(before.source_snapshot,(await control('proof')).source_snapshot,'Source rows/media changed by registration/start/hint/resume');
    const oldDuringCurrent=await outboxItem(page,oldId);
    assert.deepEqual(oldDuringCurrent,oldBefore,'Rejected old item changed while current bytes were delivered');
    proof.second_capture=await page.evaluate(()=>p3RecorderEvents);
    proof.retry_media=view.media;
    passed('V5/V8 source preservation, nonempty captures, same-sid resume, two segments + gap');

    if(fault==='block-current-finish')await page.route('**/api/v2/finish',async route=>{
      expectFailure(route.request().url(),400);
      await route.fulfill({status:400,contentType:'application/json',body:'{"error":"FAULT: old sid blocks current finish"}'});
    });
    // The rejection remains enabled through current-session upload and finish.
    const finished=page.waitForResponse(r=>r.url().endsWith('/api/v2/finish'));
    await page.locator('[data-action="finish"]').click();
    await page.locator('[data-confirm="ok"]').click();
    assert.equal((await finished).status(),200,'R3-003 current session finish blocked by old sid');
    await page.locator('[data-retry-view="comparison"] .retry-reason').filter({hasText:savedReason}).waitFor();
    const comparison=await api('/api/v2/retry-comparison/'+sid);
    assert.equal(comparison.state,'ready');assert.equal(comparison.reason,savedReason);
    assert.equal(comparison.source_sid,h.source_sid);
    await page.getByRole('button',{name:/원본.*복기|원본.*시도|이전.*복기/}).or(
      page.getByRole('link',{name:/원본.*복기|원본.*시도|이전.*복기/})).first().waitFor();
    await page.getByText(/문제 선택 구간의 경과 시간|탭을 닫은 시간/).first().waitFor();
    assert.equal(await page.evaluate(()=>window.p3Xss),undefined);
    const oldAfterFinish=await outboxItem(page,oldId);
    assert.deepEqual(oldAfterFinish,oldBefore,'Old item must survive current-session finish');
    const held=await control('proof');
    const rejected=held.upload_attempts.filter(x=>x.sid===h.source_sid && x.segment===oldSegment);
    assert(rejected.length>=1 && rejected.every(x=>x.status===400 && x.sha256===oldBefore.sha256));
    assert(held.upload_attempts.some(x=>x.sid===sid && x.segment===secondSegment && x.status===200 && x.bytes>0));
    assert.equal((await api('/api/v2/session/'+sid)).status,'completed');
    const completedRetry=held.snapshot.tables.media.filter(x=>x[0]===sid);
    await control('upload-rejection',{sid:h.source_sid,segment:oldSegment,reject:false});
    await page.unroute('**/api/v2/upload?**');
    await page.evaluate(()=>{window.p3HeldOld=null;});
    const delivered=page.waitForResponse(r=>r.url()===h.base_url+oldBefore.url && r.status()===200);
    await page.evaluate(()=>window.dispatchEvent(new Event('online')));await delivered;
    await page.waitForFunction(async id=>{
      const db=await new Promise(resolve=>{const r=indexedDB.open('cote-lab-outbox',1);r.onsuccess=()=>resolve(r.result);});
      const item=await new Promise(resolve=>{const r=db.transaction('items').objectStore('items').get(id);r.onsuccess=()=>resolve(r.result);});db.close();return item===undefined;
    },oldId);
    assert.equal(await outboxItem(page,oldId),null);
    const final=await control('proof');
    const successfulOld=final.upload_attempts.filter(x=>x.sid===h.source_sid && x.segment===oldSegment && x.status===200);
    assert.equal(successfulOld.length,1,'Old bytes must be acknowledged exactly once');
    assert.equal(successfulOld[0].sha256,oldBefore.sha256);
    assert.equal(final.snapshot.tables.media.filter(m=>m[0]===h.source_sid && m[2]===oldSegment).length,1);
    assert(!final.snapshot.tables.media.some(m=>m[0]===sid && m[2]===oldSegment));
    assert.equal(final.snapshot.media_hashes[`sessions/${h.source_sid}/media/screen/${oldSegment}/000000.part`],oldBefore.sha256);
    assert.deepEqual(final.snapshot.tables.media.filter(x=>x[0]===sid),completedRetry);
    const sourceAfterDelivery=structuredClone(final.source_snapshot);
    sourceAfterDelivery.tables.media=sourceAfterDelivery.tables.media.filter(x=>x[2]!==oldSegment);
    delete sourceAfterDelivery.media_hashes[`sessions/${h.source_sid}/media/screen/${oldSegment}/000000.part`];
    // Product may append recording_chunk/recovery metadata for this accepted late
    // upload. Existing source rows and files remain byte identical.
    for(const table of ['sessions','events','jobs'])for(const row of before.source_snapshot.tables[table])
      assert(sourceAfterDelivery.tables[table].some(x=>JSON.stringify(x)===JSON.stringify(row)),'Existing source '+table+' row changed');
    proof.outbox={old_before:oldBefore,old_after_rejection:oldAfterRejection,old_during_current:oldDuringCurrent,old_after_finish:oldAfterFinish,rejected_requests:rejected.length,
      accepted_requests:successfulOld.length,attempts:final.upload_attempts.filter(x=>x.segment===oldSegment),current_finish_status:200};
    await page.screenshot({path:path.join(output,'comparison.png'),fullPage:true});passed('V4/V8 completed-only reason/link and old-sid outbox delivery');

    // Nine list/error states use only the frozen public DTO; no render helper called.
    const baseCard={...dueDto.due[0],title:'<img src=x onerror="window.p3Xss=1"> 안전한 제목'};
    const empty={server_now:final.now,next_refresh_at:final.now+60000,capabilities:{retry_plan_expected_anchor_date:true},due:[],active:[],grading:[],unavailable:[],upcoming:[]};
    let list=empty, refreshes=0, failList=false;
    await page.route('**/api/v2/retries',async route=>{
      refreshes++;
      if(failList)expectFailure(route.request().url(),400);
      await route.fulfill({status:failList?400:200,contentType:'application/json',body:JSON.stringify(failList?{error:'P3 목록 조회 실패',code:'INVALID_INPUT'}:list)});
    });
    for (const state of ['empty','upcoming','due','active','grading','unavailable']) {
      list=structuredClone(empty);
      if(state==='unavailable')list.unavailable=[{plan_id:baseCard.plan_id,item_id:baseCard.item_id,pid:baseCard.pid,
        state:'unavailable',code:'SOURCE_UNAVAILABLE',can_start:false}];
      else if(state!=='empty')list[state]=[{...baseCard,state:state==='upcoming'?'scheduled':state,
        can_start:state==='due',session_id:state==='active'||state==='grading'?sid:null}];
      const listResponse=page.waitForResponse(r=>r.url().endsWith('/api/v2/retries') && r.status()===200);
      await page.goto('/');
      await listResponse;
      const region=retryRegion(page);
      await region.waitFor();
      if(state==='empty')await region.getByText(/없어요|없습니다|없음/).first().waitFor();
      else {
        if(state==='upcoming')await region.locator('summary').first().click();
        if(state==='unavailable')await region.getByRole('heading',{name:'확인할 수 없는 예약',exact:true}).waitFor();
        else await region.getByText(baseCard.title,{exact:true}).waitFor();
        if(state==='due')assert(await region.getByRole('button',{name:'다시 풀기',exact:true}).isEnabled());
        if(state==='active')await region.getByRole('button',{name:/이어\s*하기/}).waitFor();
        if(state==='grading')await region.getByText('채점 중',{exact:true}).waitFor();
        if(state==='unavailable'){
          await region.getByRole('status').filter({hasText:'원본 문제를 확인할 수 없어 시작할 수 없어요.'}).waitFor();
          assert.equal(await region.locator('[data-retry-action="prepare"]:enabled').count(),0);
        }
      }
      assert.equal(await page.evaluate(()=>window.p3Xss),undefined);
      noPast(h,await region.evaluate(e=>e.outerHTML),'list '+state);passed('V9 list '+state);
    }
    // Independent cases preserve failures and still execute the later gates.
    // A failed first load never turns into a PASS by matching its loading text.
    for(const kind of ['network','http400']){
      const name='R3-004-first-'+kind,errorContext=await freshContext(),errorPage=await errorContext.newPage();
      let attempts=0,allowRecovery=false,recoveryCompleted=false;
      const recovered={...structuredClone(empty),due:[{...baseCard,can_start:true,state:'due'}],
        upcoming:[{...baseCard,item_id:'recovered-upcoming',title:'조회 회복 후 예정 문제',state:'scheduled',can_start:false}]};
      await errorPage.route('**/api/v2/retries',async route=>{
        attempts++;
        if(!allowRecovery){
          expectFailure(route.request().url(),kind==='network'?null:400,kind==='network');
          if(kind==='network')return route.abort('failed');
          return route.fulfill({status:400,contentType:'application/json',body:'{"error":"Injected initial list failure","code":"INVALID_INPUT"}'});
        }
        await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(recovered)});
      });
      try{
        const failedRequest=kind==='network'?errorPage.waitForEvent('requestfailed',{predicate:r=>r.url().endsWith('/api/v2/retries')}):
          errorPage.waitForResponse(r=>r.url().endsWith('/api/v2/retries') && r.status()===400);
        await errorPage.goto('/');await failedRequest;
        const region=retryRegion(errorPage),text=kind==='network'?'서버에 연결하지 못했어요. 입력은 유지했으니 다시 시도해 주세요.':'입력 내용을 확인해 주세요.';
        await region.getByRole('status').filter({hasText:text}).waitFor();
        assert.equal(await region.getByText('재도전 목록을 불러오는 중이에요.',{exact:true}).count(),0,'Failed request remained loading');
        const button=region.getByRole('button',{name:'다시 조회',exact:true});
        assert(await button.isEnabled());assert.equal(attempts,1);
        allowRecovery=true;
        const reloaded=errorPage.waitForResponse(r=>r.url().endsWith('/api/v2/retries') && r.status()===200);
        await button.focus();await button.press('Space');await reloaded;
        await region.locator('[data-retry-state="due"]').getByRole('heading',{name:baseCard.title,exact:true}).waitFor();
        await region.locator('summary').click();
        await region.getByRole('heading',{name:'조회 회복 후 예정 문제',exact:true}).waitFor();
        assert.equal(await region.getByRole('button',{name:'다시 조회',exact:true}).count(),0);
        assert.equal(attempts,2);noPast(h,await region.evaluate(e=>e.outerHTML),'Recovered '+kind+' list');
        recoveryCompleted=true;passed(name);
      }catch(e){await captureFailure(name,e,errorPage);}
      finally{
        if(!recoveryCompleted)proof.unexecuted.push(name+': keyboard refresh and recovered cards blocked by initial error UI');
        await errorContext.close();
      }
    }
    // A successful DTO followed by an error exercises the renderer's other branch.
    list={...structuredClone(empty),due:[{...baseCard,state:'due',can_start:true}]};
    await page.goto('/');await retryRegion(page).getByRole('heading',{name:baseCard.title,exact:true}).waitFor();
    try{
      failList=true;
      const failed=page.waitForResponse(r=>r.url().endsWith('/api/v2/retries') && r.status()===400);
      await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await failed;
      const region=retryRegion(page);
      await region.getByRole('status').filter({hasText:'입력 내용을 확인해 주세요.'}).waitFor();
      assert.equal(await region.getByText('재도전 목록을 불러오는 중이에요.',{exact:true}).count(),0);
      failList=false;
      const reloaded=page.waitForResponse(r=>r.url().endsWith('/api/v2/retries') && r.status()===200);
      const button=region.getByRole('button',{name:'다시 조회',exact:true});
      await button.focus();await button.press('Space');await reloaded;
      await region.getByRole('heading',{name:baseCard.title,exact:true}).waitFor();
      assert.equal(await region.getByRole('button',{name:'다시 조회',exact:true}).count(),0);
      passed('V9 successful DTO then HTTP error, keyboard refresh and recovered due card');
    }catch(e){await captureFailure('V9-previous-DTO-error-refresh',e,page);}
    finally{failList=false;}

    // A wrong PC date cannot make a future server card due. Timers use server_now.
    list={...structuredClone(empty),upcoming:[{...baseCard,state:'scheduled',can_start:false}],next_refresh_at:final.now+60000};
    await page.clock.install({time:new Date('2030-01-01T00:00:00Z')});
    const futureResponse=page.waitForResponse(r=>r.url().endsWith('/api/v2/retries') && r.status()===200);
    await page.goto('/');await futureResponse;
    await retryRegion(page).locator('.retry-upcoming summary').waitFor();
    assert.equal(await retryRegion(page).locator('[data-retry-action="prepare"]:enabled').count(),0,'Wrong PC date enabled future card');
    const initialRefresh=refreshes;
    await page.clock.runFor(30000);assert.equal(refreshes,initialRefresh,'Unexpected background polling');
    list.server_now=final.now+30000;
    const focusResponse=page.waitForResponse(r=>r.url().endsWith('/api/v2/retries'));
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await focusResponse;
    assert(refreshes>initialRefresh,'Focus did not refresh retry list');
    list={...structuredClone(empty),server_now:final.now+60000,next_refresh_at:final.now+86400000,due:[{...baseCard,state:'due',can_start:true}]};
    const midnightResponse=page.waitForResponse(r=>r.url().endsWith('/api/v2/retries'));
    await page.clock.runFor(30001);await midnightResponse;
    await retryRegion(page).locator('[data-retry-state="due"]').getByRole('heading',{name:baseCard.title,exact:true}).waitFor();
    assert(await retryRegion(page).getByRole('button',{name:'다시 풀기',exact:true}).isEnabled());
    assert.equal(await page.evaluate(()=>p3NotificationCalls),0);passed('V2/V9 server date authority, focus + midnight refresh, no notifications/polling');
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:path.join(output,'mobile-list.png'),fullPage:true});
    passed('V9 mobile retry list has no horizontal overflow');
    assert.deepEqual(proof.page_errors,[]);assert.deepEqual(proof.console_errors,[]);
    proof.retry_sid=sid;
    proof.unverified=['V12 real Java execution and media decode; fixture uses mock Java and no decoder'];
    const failures=proof.tests.filter(x=>x.status==='failed');
    assert.equal(failures.length,0,'Unresolved product review gates: '+failures.map(x=>x.name).join(', '));
    proof.status='passed';
  } catch(e) {
    proof.status=String(e).includes('EXPECTED_API_NOT_IMPLEMENTED')?'expected_product_failure':String(e).includes('FIXTURE_ERROR')?'fixture_error':'failed';
    proof.error=e.stack||String(e);
    if(!proof.tests.some(x=>x.status==='failed'))proof.tests.push({name:'Acceptance failure after '+(proof.tests.at(-1)?.name||'fixture startup'),
      status:'failed',error:proof.error});
    if(page && !page.isClosed()) {
      await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{});
      fs.writeFileSync(path.join(output,'failure-dom.html'),await page.locator('html').evaluate(e=>e.outerHTML).catch(()=>''));
    }
    console.error(proof.status+': '+e.message);process.exitCode=1;
  } finally {
    if(context)await context.close();if(browser)await browser.close();if(owner)await owner.stop();
    fs.writeFileSync(path.join(output,'browser-verification.json'),JSON.stringify(proof,null,2)+'\n');
    console.log(JSON.stringify({status:proof.status,passed:proof.tests.filter(x=>x.status==='passed').length,
      failed:proof.tests.filter(x=>x.status==='failed').length,skipped:0,unexecuted:proof.unexecuted,proof:path.join(output,'browser-verification.json'),harness_only:harnessOnly}));
  }
}
module.exports={fixture,captureFixture,noPast,outboxItem,recordingStop,runRetryBrowser};
if(require.main===module)runRetryBrowser().catch(e=>{console.error(e);process.exitCode=1;});
