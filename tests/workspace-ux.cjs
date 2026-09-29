const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({baseURL:'http://localhost:8876',viewport:{width:1440,height:900}});
  const errors=[];page.on('pageerror',e=>errors.push(e.stack||e.message));
  // Synthetic media; the real recording, upload, hint backend and editor stay in use.
  await page.addInitScript(()=>{
   let handle;navigator.mediaDevices.setCaptureHandleConfig=c=>handle=c;
   navigator.mediaDevices.getDisplayMedia=async()=>{const c=document.createElement('canvas');c.width=320;c.height=180;c.getContext('2d').fillRect(0,0,320,180);const s=c.captureStream(5),t=s.getVideoTracks()[0];t.getSettings=()=>({displaySurface:'browser'});t.getCaptureHandle=()=>handle;return s;};
   window.micRequests=0;navigator.mediaDevices.getUserMedia=async()=>{window.micRequests++;const a=new AudioContext(),o=a.createOscillator(),g=a.createGain(),d=a.createMediaStreamDestination();g.gain.value=0;o.connect(g);g.connect(d);o.start();await a.resume();return d.stream;};
  });
  await page.goto('/');await page.locator('[data-action="setup"]').first().click();
  await page.locator('#kind').selectOption('mock');await page.locator('#mode').selectOption('training');
  for(const c of await page.locator('[name="problem"]').all())await c.uncheck();
  await page.locator('[name="problem"][value="brackets"]').check();await page.locator('[name="problem"][value="pair"]').check();
  await page.locator('#setup-model').selectOption('test-model');await page.locator('#setup-effort').selectOption('high');
  await page.locator('[data-action="start"]').click();await page.locator('#editor .monaco-editor').waitFor();
  await page.locator('[data-problem="brackets"]').click();
  assert.equal(await page.locator('.example-inputs dd code').first().textContent(),'([]){}');
  let releaseHint,requestSeen;const requestReady=new Promise(r=>requestSeen=r);
  await page.route('**/api/v2/chat',async route=>{const data=route.request().postDataJSON();if(data.kind!=='hint')return route.continue();assert.equal(data.pid,'brackets');assert.equal(data.message,'');assert.equal(data.model,'test-model');const response=await route.fetch();requestSeen();await new Promise(r=>releaseHint=r);await route.fulfill({response});});
  await page.locator('[data-coach="hint"]').click();await requestReady;
  // A pending hint must not block typing or leak into another problem.
  await page.locator('[data-problem="pair"]').click();
  await page.locator('#editor').click();await page.keyboard.press('Control+End');await page.keyboard.insertText('\n// 계속 풀이 중');
  assert.match(await page.locator('#editor .view-lines').textContent(),/계속/);
  releaseHint();await page.getByText('다른 문제의 힌트가 도착했어요. 해당 문제에서 확인할 수 있어요.',{exact:true}).waitFor();
  assert.equal(await page.locator('.inline-coach-feed').getByText(/테스트용 코치 응답/).count(),0);
  await page.locator('[data-problem="brackets"]').click();await page.locator('.inline-coach-feed').getByText(/테스트용 코치 응답/).waitFor();
  await page.unroute('**/api/v2/chat');
  // Only speech recognition is mocked here to deterministically test automatic voice-to-hint submission.
  await page.route('**/api/v2/transcribe',route=>route.fulfill({json:{job_id:'voice-ui-check'}}));
  await page.route('**/api/v2/job/voice-ui-check',route=>route.fulfill({json:{status:'completed',result:{text:'닫는 괄호에서 막혔어요.',segments:[]}}}));
  await page.locator('[data-coach="voice"]').click();await page.getByRole('button',{name:'■ 말 마침 · 힌트 받기'}).waitFor();
  await page.waitForTimeout(400); // Produce a valid audio chunk.
  await page.locator('[data-coach="voice"]').click();
  await page.locator('.inline-coach-question').getByText('닫는 괄호에서 막혔어요.',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>window.micRequests),1);assert.equal(await page.locator('dialog[open]').count(),0);
  // Late completion responses must never insert into a newer document.
  let releaseCompletion,completionSeen;const completionReady=new Promise(r=>completionSeen=r);
  await page.route('**/api/v2/ide',async route=>{const d=route.request().postDataJSON();if(d.op!=='completion')return route.fulfill({json:{diagnostics:[]}});completionSeen();await new Promise(r=>releaseCompletion=r);await route.fulfill({json:{items:[{label:'STALE_SUGGESTION',insertText:'STALE_SUGGESTION',kind:6}]}});});
  await page.locator('#editor').click();await page.keyboard.press('Control+A');await page.keyboard.insertText('class Solution { void test(){ System.');await page.keyboard.press('Control+Space');await completionReady;
  await page.locator('[data-problem="pair"]').click();releaseCompletion();
  await page.locator('#editor').click();await page.keyboard.press('Control+End');await page.keyboard.press('Enter');
  assert.equal(await page.locator('#editor .view-lines').getByText('STALE_SUGGESTION').count(),0);
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  assert.deepEqual(errors,[]);
  console.log('PASS: no hint dialog, no-text hint, editing while pending, per-problem replies, voice auto-submit with mic reuse, stale completion rejection, mobile width. Speech text and delayed completion mocked.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
