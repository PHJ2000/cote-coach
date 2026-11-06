/* Review regressions on disposable fixtures and fresh Chromium profiles.
 * Run after npm run build: node tests/retry-review-coverage.cjs
 * Negative outbox oracle probes: node tests/retry-review-coverage.cjs --fault-probes
 * Anchor-date-only diagnostics: node tests/retry-review-coverage.cjs --anchor-only
 * Every product failure remains a failure; cases continue independently, skip=0.
 * No external AI, existing server/profile, real recording, or product edit.
 */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {createHash}=require('node:crypto');
const {chromium}=require('playwright');
const {buildSync}=require('esbuild');
const {fixture,captureFixture,noPast}=require('./retry-browser.cjs');
const root=path.resolve(__dirname,'..');
const output=process.env.RETRY_REVIEW_OUTPUT||fs.mkdtempSync(path.join(os.tmpdir(),'retry-review-'));
fs.mkdirSync(output,{recursive:true});
const proof={status:'running',tests:[],page_errors:[],console_errors:[],synthetic_capture:true,
  expected_http_errors:[],contract:'retry-v1.1-anchor-date',
  mocked:['Java','IDE','coach','STT','media_subprocess'],external_ai_calls:0,skipped:0};

function deferred(){let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
async function bounded(promise,label){
  let timer;
  try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label)),10000);})]);}
  finally{clearTimeout(timer);}
}
// Wait for browser response handlers to complete their rendering turn, not a
// fixed wall-clock delay. Request/gate completion is always awaited first.
const renderTurn=p=>p.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));

async function runCase(browser,id,test){
  let owner,context,page;
  const started=new Date().toISOString(),details={},releases=[],expectedErrors=[];
  const expectError=(url,status)=>expectedErrors.push({url,status,consumed:false});
  try{
    owner=await fixture();const h=owner.h;
    const control=async(name,data)=>{
      const response=await fetch(h.base_url+'/_retry_fixture/'+name,{method:data?'POST':'GET',
        headers:{'X-Retry-Fixture':h.token,'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});
      assert.equal(response.status,200);return response.json();
    };
    const api=async(url,data)=>{
      const response=await fetch(h.base_url+url,{method:data?'POST':'GET',headers:{'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});
      const body=await response.json();assert.equal(response.status,200,JSON.stringify(body));return body;
    };
    context=await browser.newContext({baseURL:h.base_url,viewport:{width:1450,height:1000},timezoneId:'Asia/Seoul'});
    context.setDefaultTimeout(10000);
    await context.addInitScript(captureFixture);
    await context.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(['http:','https:'].includes(url.protocol) && url.origin!==h.base_url){
        proof.page_errors.push(id+': blocked non-fixture origin '+url.origin);return route.abort();
      }
      await route.continue();
    });
    context.on('page',p=>{
      p.on('pageerror',e=>proof.page_errors.push(id+': '+e.message));
      p.on('console',m=>{
        if(m.type()!=='error')return;
        const expected=expectedErrors.find(e=>!e.consumed && e.url===m.location().url && new RegExp('\\b'+e.status+'\\b').test(m.text()));
        if(expected){expected.consumed=true;proof.expected_http_errors.push({case:id,url:expected.url,status:expected.status,text:m.text()});}
        else proof.console_errors.push({case:id,url:m.location().url,text:m.text()});
      });
    });
    page=await context.newPage();
    await test({h,page,context,control,api,details,releases,expectError});
    assert(expectedErrors.every(e=>e.consumed),'An injected HTTP error was not observed at its exact URL/status');
    proof.tests.push({id,status:'passed',started_utc:started,ended_utc:new Date().toISOString(),details});
  }catch(e){
    proof.tests.push({id,status:'failed',started_utc:started,ended_utc:new Date().toISOString(),error:e.stack||String(e),details});
    if(page && !page.isClosed()){
      await page.screenshot({path:path.join(output,id+'.png'),fullPage:true}).catch(()=>{});
      fs.writeFileSync(path.join(output,id+'.html'),await page.locator('html').evaluate(e=>e.outerHTML).catch(()=>''));
    }
  }finally{
    details.expected_error_observations=expectedErrors;
    if(details.planned_steps)details.unexecuted_steps=details.planned_steps.filter(step=>!details.completed_steps?.includes(step));
    for(const release of releases)release();
    if(context)await context.close();if(owner)await owner.stop();
  }
}

async function preparePlan({h,api,control}){
  const saved=await api('/api/v2/retry-plan',{source_sid:h.source_sid,pid:h.pid,reason:h.sentinels.reason,timezone:'Asia/Seoul'});
  await control('advance',{ms:7*86400000});return saved.plan;
}
async function startItem(api,item){
  return (await api('/api/v2/retry-start',{item_id:item.id,kind:'single',mode:'training',duration:40,
    resources:'none',model:'fixture-model',effort:'low'})).session;
}

async function navigationRace(env,targetRead,intent){
  const {h,page,api,control,details,releases}=env;
  const plan=await preparePlan(env),s=intent==='resume'?await startItem(api,plan.items[0]):null;
  const held=deferred(),arrived=deferred();releases.push(held.resolve);
  let reads=0;
  await page.addInitScript(sourceSid=>{
    window.p3SourcePending=0;
    const native=window.fetch.bind(window);
    window.fetch=async(...args)=>{
      const url=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href);
      const source=url.pathname==='/api/v2/session/'+sourceSid;
      if(source)window.p3SourcePending++;
      let response;
      try{response=await native(...args);}catch(e){if(source)window.p3SourcePending--;throw e;}
      if(source){const json=response.json.bind(response);response.json=async()=>{try{return await json();}finally{window.p3SourcePending--;}};}
      return response;
    };
  },h.source_sid);
  await page.route('**/api/v2/session/'+h.source_sid,async route=>{
    reads++;const response=await route.fetch();
    if(reads===targetRead){arrived.resolve();await held.promise;}
    await route.fulfill({response});
  });
  await page.goto('/');
  const region=page.locator('[data-retry-view="list"]');
  await region.getByRole('button',{name:intent==='resume'?'이어 하기':'다시 풀기',exact:true}).waitFor();
  await page.locator(`[data-session="${h.source_sid}"]`).first().click();
  await bounded(arrived.promise,'Expected source read '+targetRead+' did not reach its gate');
  await region.getByRole('button',{name:intent==='resume'?'이어 하기':'다시 풀기',exact:true}).click();
  if(intent==='resume')await page.locator('#editor .monaco-editor').waitFor();
  else await page.getByRole('heading',{name:'재도전 준비',exact:true}).waitFor();
  noPast(h,await page.locator('html').evaluate(e=>e.outerHTML),'Before stale response '+intent);
  const activeBefore=await page.evaluate(()=>localStorage.getItem('lab-active'));
  const response=page.waitForResponse(r=>r.url().endsWith('/api/v2/session/'+h.source_sid));
  held.resolve();await (await response).finished();
  await page.waitForFunction(()=>p3SourcePending===0);await renderTurn(page);
  details.source_reads=reads;details.latest_intent=intent;
  details.workspace_count=await page.locator('#editor .monaco-editor').count();
  details.active_before=activeBefore;details.active_after=await page.evaluate(()=>localStorage.getItem('lab-active'));
  details.source_report_visible=await page.getByText(h.sentinels.report,{exact:true}).count();
  details.capture_calls=await page.evaluate(()=>p3CaptureCalls);
  if(intent==='resume')assert.equal(details.workspace_count,1,'R2-001 late source response replaced resumed retry');
  else assert.equal(await page.getByRole('heading',{name:'재도전 준비',exact:true}).count(),1,'R2-001 late source response replaced retry setup');
  noPast(h,await page.locator('html').evaluate(e=>e.outerHTML),'After stale response '+intent);
  assert.equal(details.active_after,activeBefore);assert.equal(details.capture_calls,0);
  if(s)assert.equal((await api('/api/v2/session/'+s.id)).status,'active');
}

async function draftDuringJob(env,pendingSave){
  const {h,page,details,releases}=env;
  const terminal=deferred(),terminalEntered=deferred(),saveGate=deferred(),saveEntered=deferred();
  releases.push(terminal.resolve,saveGate.resolve);
  let reads=0,saveRequests=0;
  await page.route('**/api/v2/session/'+h.source_sid,async route=>{
    reads++;const response=await route.fetch(),body=await response.json();
    body.jobs=reads<=2?[{id:'review-pending',kind:'codex',status:'running'}]:[];
    if(reads===3){terminalEntered.resolve();await terminal.promise;}
    await route.fulfill({response,json:body});
  });
  if(pendingSave)await page.route('**/api/v2/retry-plan',async route=>{
    saveRequests++;saveEntered.resolve();await saveGate.promise;
    await route.fulfill({response:await route.fetch()});
  });
  await page.goto('/');await page.locator(`[data-session="${h.source_sid}"]`).first().click();
  const form=page.locator('[data-retry-registration]'),reason=form.getByLabel(/실패 이유/),confirmed=form.getByRole('checkbox');
  await reason.fill('등록 중 입력 보존 검증');await confirmed.check();await reason.focus();
  if(pendingSave){
    const save=form.getByRole('button',{name:'1·3·7일 뒤 예약',exact:true});
    await save.focus();await save.press('Space');await bounded(saveEntered.promise,'Registration did not start');
  }
  await bounded(terminalEntered.promise,'Review job poll did not reach terminal gate');
  const polled=page.waitForResponse(r=>r.url().endsWith('/api/v2/session/'+h.source_sid));
  terminal.resolve();await (await polled).finished();await renderTurn(page);
  details.source_reads=reads;details.reason_after_terminal=await reason.inputValue();
  details.confirmed_after_terminal=await confirmed.isChecked();
  details.focus_after_terminal=await reason.evaluate(e=>e===document.activeElement);
  details.pending_after_terminal=await form.getAttribute('aria-busy');
  if(pendingSave){
    const saved=page.waitForResponse(r=>r.url().endsWith('/api/v2/retry-plan') && r.status()===200);
    saveGate.resolve();await saved;
    await page.locator('[data-retry-view="registration"]').getByRole('status').filter({hasText:'1·3·7일 뒤 예약을 저장했어요.'}).waitFor();
    assert.equal(saveRequests,1,'R2-002 job refresh duplicated registration');
  }else{
    assert.equal(details.reason_after_terminal,'등록 중 입력 보존 검증','R2-002 terminal job erased reason');
    assert(details.confirmed_after_terminal,'R2-002 terminal job erased timezone confirmation');
    assert(details.focus_after_terminal,'R2-002 terminal job stole input focus');
  }
}

const anchorBefore=Date.parse('2026-10-01T14:59:59Z');
const anchorAfter=anchorBefore+2000;
const oldDates=['2026-10-02','2026-10-04','2026-10-08'];
const newDates=['2026-10-03','2026-10-05','2026-10-09'];
const snapshotHash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const step=(details,name)=>details.completed_steps.push(name);
function anchorError(now=anchorAfter,expected='2026-10-01',current='2026-10-02',dates=newDates){
  return {error:'기준 날짜가 바뀌어 예약하지 않았습니다. 새 예약 날짜를 확인해 주세요.',code:'ANCHOR_DATE_CHANGED',
    expected_anchor_date:expected,current_anchor_date:current,timezone:'Asia/Seoul',server_now:now,
    preview:[1,3,7].map((offset_days,i)=>({offset_days,due_date:dates[i]}))};
}
async function anchorForm(env,{capability='true',clockSkew=false}={}){
  const {page,control,details,h,expectError}=env;
  await control('clock',{now:anchorBefore});
  if(clockSkew)await page.clock.install({time:new Date('2042-12-31T06:00:00Z')});
  const trace={posts:[],responses:[],responseTasks:[],gets:[],getMode:capability};
  details.transport={capability_mode:capability,capability_mocked:true,guard_mocked:false,clock_skew:clockSkew};
  await page.addInitScript(()=>{
    window.p3AnchorPending=0;
    const native=window.fetch.bind(window);
    window.fetch=async(...args)=>{
      const url=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href),tracked=['/api/v2/retry-plan','/api/v2/retries'].includes(url.pathname);
      if(tracked)window.p3AnchorPending++;
      let response;
      try{response=await native(...args);}catch(e){if(tracked)window.p3AnchorPending--;throw e;}
      if(tracked){const json=response.json.bind(response);response.json=async()=>{try{return await json();}finally{window.p3AnchorPending--;}};}
      return response;
    };
  });
  await page.route('**/api/v2/retries',async route=>{
    trace.gets.push({mode:trace.getMode,after_mismatch:trace.errorDelivered===true});
    if(trace.getMode==='failure'){
      expectError(route.request().url(),400);
      return route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Fixture capability/clock read failed',code:'INVALID_INPUT'})});
    }
    const response=await route.fetch(),body=await response.json();
    if(trace.getMode==='missing')delete body.capabilities;
    else body.capabilities={retry_plan_expected_anchor_date:trace.getMode==='true'?true:trace.getMode==='false'?false:trace.getMode==='number'?1:'true'};
    await route.fulfill({response,json:body});
  });
  page.on('request',request=>{
    if(request.method()==='POST' && request.url()===h.base_url+'/api/v2/retry-plan')trace.posts.push(request.postDataJSON());
  });
  page.on('response',response=>{
    if(response.request().method()==='POST' && response.url()===h.base_url+'/api/v2/retry-plan')
      trace.responseTasks.push(response.json().then(body=>trace.responses.push({status:response.status(),body})));
  });
  await page.goto('/');await page.locator(`[data-session="${h.source_sid}"]`).first().click();
  const form=page.locator('[data-retry-registration]').first();await form.waitFor();
  await form.getByLabel(/실패 이유/).fill('자정 뒤 날짜 재확인');
  await form.getByLabel('일정 기준 시간대',{exact:true}).fill('Asia/Seoul');
  await form.getByRole('checkbox').check();
  const dates=await form.locator('[data-retry-date-preview] li span').allTextContents();
  details.confirmed_preview=dates;details.before_proof=await control('proof');
  details.pc_date=await page.evaluate(()=>new Date(Date.now()).toISOString());
  assert.deepEqual(dates,oldDates,'V2-AD6 registration preview trusted the PC date instead of the fixture server');
  return {form,trace};
}
async function anchorSave(form,required=true){
  const save=form.locator('button[type="submit"]');
  const enabled=await save.isEnabled();
  if(required)assert(enabled,'V9-AD expected a user-enabled save control');
  if(enabled){await save.focus();await save.press('Space');}
  return enabled;
}
async function anchorSettled(page,trace){
  await page.waitForFunction(()=>p3AnchorPending===0);await renderTurn(page);
  await Promise.all(trace.responseTasks);
}
async function anchorObservation(env,form,trace){
  const {page,details,control}=env;
  const exists=await form.count(),host=page.locator('[data-retry-view="registration"]').first();
  const after=await control('proof');
  const observation={post_count:trace.posts.length,requests:structuredClone(trace.posts),responses:structuredClone(trace.responses),get_count:trace.gets.length,
    form_exists:exists>0,reason:exists?await form.getByLabel(/실패 이유/).inputValue():null,
    timezone:exists?await form.getByLabel('일정 기준 시간대',{exact:true}).inputValue():null,
    confirmed:exists?await form.getByRole('checkbox').isChecked():null,
    dates:exists?await form.locator('[data-retry-date-preview] li span').allTextContents():[],
    status:await host.count()?await host.getByRole('status').allTextContents():[],
    saved_notice:await page.getByRole('status').filter({hasText:/예약을 저장했어요|이미 저장된 예약이에요/}).count(),
    retry_counts:after.retry_counts,source_hash:snapshotHash(after.source_snapshot),
    source_unchanged:snapshotHash(after.source_snapshot)===snapshotHash(details.before_proof.source_snapshot)};
  (details.observations??=[]).push(observation);return observation;
}
async function midnightRegistration(env,{mockError=false,malformed=false,clockFailure=false,repeatMidnight=false}={}){
  const {page,control,details,h,expectError}=env;
  details.planned_steps=['confirmed preview','first rejected save','reason/tuple preserved and confirmation reset','no automatic retry',
    ...(malformed?['authoritative clock fallback']:[]),...(clockFailure?['failed clock blocks save']:['explicit reconfirmation',repeatMidnight?'second user save':'second user save matches preview'])];
  if(repeatMidnight)details.planned_steps.push('second mismatch resets confirmation again','no third automatic save');
  details.completed_steps=[];
  const {form,trace}=await anchorForm(env);step(details,'confirmed preview');
  details.transport.guard_mocked=mockError;
  await page.route('**/api/v2/retry-plan',async route=>{
    const number=trace.posts.length;
    if(number===1){
      await control('advance',{ms:2000});
      if(mockError){
        let body=anchorError();
        if(malformed)body={...body,server_now:'invalid-clock',preview:[{offset_days:1,due_date:'2026-02-30'}]};
        if(clockFailure)trace.getMode='failure';
        trace.errorDelivered=true;
        expectError(route.request().url(),400);
        return route.fulfill({status:400,contentType:'application/json',body:JSON.stringify(body)});
      }
    }else if(repeatMidnight && number===2){
      await control('advance',{ms:86400000});
      expectError(route.request().url(),400);
      return route.fulfill({status:400,contentType:'application/json',body:JSON.stringify(anchorError(anchorAfter+86400000,'2026-10-02','2026-10-03',['2026-10-04','2026-10-06','2026-10-10']))});
    }
    const response=await route.fetch();
    if(response.status()===400)expectError(route.request().url(),400);
    await route.fulfill({response});
  });
  const firstResponse=page.waitForResponse(r=>r.url()===h.base_url+'/api/v2/retry-plan' && r.request().method()==='POST');
  await anchorSave(form);await firstResponse;await anchorSettled(page,trace);
  const first=await anchorObservation(env,form,trace);
  assert.equal(first.post_count,1,'V9-AD1 automatic POST retry after mismatch');
  assert.equal(first.responses[0].status,400,'CONTRACT-R2-003 real guard silently saved changed dates');
  assert.equal(first.responses[0].body.code,'ANCHOR_DATE_CHANGED');
  assert.deepEqual(first.retry_counts,details.before_proof.retry_counts,'V9-AD1 mismatch wrote a plan/items');
  assert(first.source_unchanged);step(details,'first rejected save');
  assert.equal(first.reason,'자정 뒤 날짜 재확인');assert.equal(first.timezone,'Asia/Seoul');
  assert.equal(first.confirmed,false,'V9-AD1 mismatch left the old date confirmed');
  assert.equal(first.saved_notice,0,'V9-AD1 failed save showed a success notice');
  assert(first.status.some(s=>/날짜|재확인|확인.*예약|예약.*확인/.test(s)),'V9-AD1 date change requires an explicit explanation');
  assert.equal(first.requests[0].expected_anchor_date,'2026-10-01','V9-AD1 UI omitted/replaced the user-confirmed anchor');
  step(details,'reason/tuple preserved and confirmation reset');
  await anchorSave(form,false);await anchorSettled(page,trace);
  assert.equal(trace.posts.length,1,'V9-AD1 posted without explicit reconfirmation');step(details,'no automatic retry');
  if(malformed){assert(trace.gets.some(get=>get.after_mismatch),'V9-AD1 malformed error did not refetch server clock');step(details,'authoritative clock fallback');}
  if(clockFailure){
    if(await form.getByRole('checkbox').isEnabled())await form.getByRole('checkbox').check();
    await anchorSave(form,false);await anchorSettled(page,trace);
    const blocked=await anchorObservation(env,form,trace);
    assert.equal(blocked.post_count,1);assert.equal(blocked.reason,'자정 뒤 날짜 재확인');
    assert(blocked.status.some(s=>/서버|시각|조회|확인/.test(s)));step(details,'failed clock blocks save');return;
  }
  assert.deepEqual(first.dates,newDates,'V9-AD1 new preview did not match the authoritative mismatch date');
  await form.getByRole('checkbox').check();step(details,'explicit reconfirmation');
  const secondResponse=page.waitForResponse(r=>r.url()===h.base_url+'/api/v2/retry-plan' && r.request().method()==='POST');
  await anchorSave(form);await secondResponse;await anchorSettled(page,trace);
  const second=await anchorObservation(env,form,trace);
  assert.equal(second.post_count,2,'V9-AD1 explicit user reconfirmation did not produce exactly one POST');
  assert.equal(second.requests[1].expected_anchor_date,'2026-10-02');
  if(repeatMidnight){
    step(details,'second user save');
    assert.equal(second.responses[1].status,400);assert.equal(second.responses[1].body.code,'ANCHOR_DATE_CHANGED');
    assert.equal(second.confirmed,false);assert.equal(second.reason,'자정 뒤 날짜 재확인');
    assert.deepEqual(second.dates,['2026-10-04','2026-10-06','2026-10-10']);
    assert.deepEqual(second.retry_counts,details.before_proof.retry_counts);step(details,'second mismatch resets confirmation again');
    await anchorSave(form,false);await anchorSettled(page,trace);assert.equal(trace.posts.length,2);step(details,'no third automatic save');
  }else{
    assert.equal(second.responses[1].status,200);assert.deepEqual(second.responses[1].body.plan.items.map(i=>i.due_date),first.dates);
    assert.equal(second.responses[1].body.plan.anchor_date,'2026-10-02');step(details,'second user save matches preview');
  }
}
async function anchorCapability(env,mode){
  const {page,details}=env;
  details.planned_steps=['draft and confirmed preview','save attempt','capability guard and draft preservation'];details.completed_steps=[];
  const {form,trace}=await anchorForm(env,{capability:mode,clockSkew:mode==='true'});step(details,'draft and confirmed preview');
  details.save_control_enabled=await anchorSave(form,false);await anchorSettled(page,trace);
  const observed=await anchorObservation(env,form,trace);step(details,'save attempt');
  if(mode==='true'){
    assert.equal(observed.post_count,1,'V9-AD2 exact true must enable registration');
    assert.equal(observed.responses[0].status,200);
    assert.equal(observed.requests[0].expected_anchor_date,'2026-10-01','V2-AD6 new UI used no confirmed server anchor');
    assert.deepEqual(observed.responses[0].body.plan.items.map(i=>i.due_date),oldDates);
    assert.equal(observed.responses[0].body.plan.anchor_date,'2026-10-01');
  }else{
    assert.equal(observed.post_count,0,'V9-AD2 unsupported capability allowed a registration POST');
    assert.equal(observed.reason,'자정 뒤 날짜 재확인');assert.equal(observed.timezone,'Asia/Seoul');
    assert(observed.status.some(s=>/서버.*날짜|날짜.*확인|날짜.*예약/.test(s)),'V9-AD2 missing explicit server date capability status');
    assert.deepEqual(observed.retry_counts,details.before_proof.retry_counts);
  }
  step(details,'capability guard and draft preservation');
}
async function lateAnchorError(env,target){
  const {page,control,details,h,expectError,releases}=env;
  details.planned_steps=['pending original date save','later route/sid intent','old mismatch cannot alter latest context'];details.completed_steps=[];
  const other=target==='sid'?await control('seed-source',{pid:'fixture-anchor-second'}):null;
  const {form,trace}=await anchorForm(env);
  const gate=deferred(),arrived=deferred();releases.push(gate.resolve);
  details.transport.guard_mocked=true;
  await page.route('**/api/v2/retry-plan',async route=>{
    arrived.resolve();await gate.promise;expectError(route.request().url(),400);
    await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify(anchorError())});
  });
  await anchorSave(form);await bounded(arrived.promise,'V9-AD3 original POST did not enter the gate');step(details,'pending original date save');
  await page.getByRole('button',{name:'대시보드',exact:true}).click();
  if(other){
    await page.locator(`[data-session="${other.source_sid}"]`).first().click();
    const latest=page.locator('[data-retry-registration]').first();await latest.waitFor();
    await latest.getByLabel(/실패 이유/).fill('새 원본의 보존할 초안');await latest.getByRole('checkbox').check();await latest.getByLabel(/실패 이유/).focus();
  }else await page.locator('[data-retry-view="list"]').getByRole('status').filter({hasText:'오늘 다시 풀 문제가 없어요.'}).waitFor();
  const latestHtml=await page.locator('#app').evaluate(e=>e.outerHTML);
  details.latest_context_before_hash=snapshotHash(latestHtml);step(details,'later route/sid intent');
  const response=page.waitForResponse(r=>r.url()===h.base_url+'/api/v2/retry-plan');
  gate.resolve();await response;await anchorSettled(page,trace);
  details.latest_context_after_hash=snapshotHash(await page.locator('#app').evaluate(e=>e.outerHTML));
  details.posts=trace.posts;details.responses=trace.responses;
  assert.equal(details.latest_context_after_hash,details.latest_context_before_hash,'V9-AD3 old mismatch replaced/changed the latest route or sid');
  assert.equal(trace.posts.length,1,'V9-AD3 stale form automatically retried its POST');
  if(other){
    const latest=page.locator('[data-retry-registration]').first();
    assert.equal(await latest.getByLabel(/실패 이유/).inputValue(),'새 원본의 보존할 초안');
    assert(await latest.getByRole('checkbox').isChecked());assert(await latest.getByLabel(/실패 이유/).evaluate(e=>e===document.activeElement));
  }
  step(details,'old mismatch cannot alter latest context');
}

async function zeroByteStop(env){
  const {h,page,api,control,details}=env,plan=await preparePlan(env),s=await startItem(api,plan.items[0]);
  await control('advance',{ms:1000});
  await page.route('**/retry-review-scratch',r=>r.fulfill({contentType:'text/html',body:'<!doctype html><title>Isolated zero-byte stop</title>'}));
  await page.goto('/retry-review-scratch');
  const bundle=buildSync({entryPoints:[path.join(root,'client/recording.js')],bundle:true,write:false,format:'iife',globalName:'RetryRecording'}).outputFiles[0].text;
  await page.addScriptTag({content:bundle});
  const result=await page.evaluate(async({sid,pid})=>{
    const stream=await navigator.mediaDevices.getDisplayMedia();
    const recorder=RetryRecording.makeRecorder(stream,{sid,event:async(kind,payload)=>{
      const response=await fetch('/api/v2/events',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({sid,events:[{id:crypto.randomUUID(),pid,kind,ms:Math.round(payload.end_ms||0),payload}]})});
      if(!response.ok)throw new Error('Zero-byte event transport failed');
    }});
    // Same task: no encoder frame has been produced yet. Zero bytes are valid.
    await recorder.stop();await RetryRecording.drain();
    return {segment:recorder.segment,events:p3RecorderEvents,pending:(await RetryRecording.pending()).length,
      tracks_ended:stream.getTracks().every(t=>t.readyState==='ended')};
  },{sid:s.id,pid:h.pid});
  details.recorder=result;
  const view=await api('/api/v2/session/'+s.id),media=view.media.find(x=>x.segment===result.segment);
  details.media=media;details.gaps=view.recording_gaps;details.uploads=(await control('proof')).upload_attempts;
  assert(result.events.some(x=>x.event==='dataavailable' && x.bytes===0),'Native immediate stop did not reproduce zero bytes');
  assert.equal(details.uploads.length,0,'R3-002 zero bytes must not be uploaded');
  assert.equal(result.pending,0);assert(result.tracks_ended);
  assert.equal(media.bytes,0);assert.equal(media.expected_chunks,0);assert.equal(media.chunks,0);
  assert.equal(media.playable,false);assert.equal(media.url,null);assert(view.recording_gaps.length>0);
  await api('/api/v2/finish',{sid:s.id});
}

async function negativeFaultProbes(){
  for(const [fault,oracle] of [['delete-rejected-item','R3-003 rejected old item lost or changed'],['block-current-finish','R3-003 current session finish blocked by old sid']]){
    const folder=path.join(output,fault),started=new Date().toISOString();fs.mkdirSync(folder,{recursive:true});
    const argv=[path.join(__dirname,'retry-browser.cjs'),'--fault='+fault];
    const child=spawn(process.execPath,argv,{cwd:root,env:{...process.env,RETRY_TEST_OUTPUT:folder},stdio:['ignore','pipe','pipe']});
    let log='';child.stdout.on('data',b=>{log+=b;});child.stderr.on('data',b=>{log+=b;});
    const [exitcode]=await once(child,'exit');fs.writeFileSync(path.join(folder,'command.log'),log);
    const result=JSON.parse(fs.readFileSync(path.join(folder,'browser-verification.json'),'utf8'));
    assert.equal(exitcode,1,'Injected fault incorrectly passed');assert.equal(result.injected_fault,fault);
    assert(result.error.includes(oracle),'Failed at an unrelated gate: '+result.error);
    proof.tests.push({id:'R3-003-negative-'+fault,status:'passed',started_utc:started,ended_utc:new Date().toISOString(),
      child_argv:[process.execPath,...argv],child_exitcode:exitcode,failed_oracle:oracle,
      child_log_sha256:createHash('sha256').update(log).digest('hex')});
  }
}

(async()=>{
  let browser;
  try{
    if(process.argv.includes('--fault-probes'))await negativeFaultProbes();
    else{
      browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required']});
      proof.chromium_version=browser.version();
      proof.anchor_only=process.argv.includes('--anchor-only');
      if(!proof.anchor_only){
        await runCase(browser,'R3-002-zero-byte-stop',zeroByteStop);
        for(const read of [1,2])for(const intent of ['resume','prepare'])
          await runCase(browser,`R2-001-source-read${read}-${intent}`,env=>navigationRace(env,read,intent));
        await runCase(browser,'R2-002-terminal-job-draft',env=>draftDuringJob(env,false));
        await runCase(browser,'R2-002-terminal-job-pending-save',env=>draftDuringJob(env,true));
      }
      await runCase(browser,'R2-003-midnight-confirmed-calendar',midnightRegistration);
      await runCase(browser,'V9-AD1-structured-mismatch-reconfirmation',env=>midnightRegistration(env,{mockError:true}));
      await runCase(browser,'V9-AD1-malformed-mismatch-clock-fallback',env=>midnightRegistration(env,{mockError:true,malformed:true}));
      await runCase(browser,'V9-AD1-malformed-mismatch-clock-failure',env=>midnightRegistration(env,{mockError:true,malformed:true,clockFailure:true}));
      for(const capability of ['missing','false','failure','number','string','true'])
        await runCase(browser,'V9-AD2-capability-'+capability,env=>anchorCapability(env,capability));
      for(const target of ['route','sid'])await runCase(browser,'V9-AD3-late-mismatch-'+target,env=>lateAnchorError(env,target));
      await runCase(browser,'V9-AD3-second-midnight-needs-reconfirmation',env=>midnightRegistration(env,{mockError:true,repeatMidnight:true}));
    }
    assert.deepEqual(proof.page_errors,[]);assert.deepEqual(proof.console_errors,[]);
    assert.equal(proof.tests.filter(x=>x.status==='failed').length,0,'Unresolved product review regressions');
    proof.status='passed';
  }catch(e){proof.status='failed';proof.error=e.stack||String(e);process.exitCode=1;}
  finally{
    if(browser)await browser.close();
    proof.unverified=['Actual Java execution, OS screen/microphone capture, model/STT inference and media decode'];
    fs.writeFileSync(path.join(output,'review-verification.json'),JSON.stringify(proof,null,2)+'\n');
    console.log(JSON.stringify({status:proof.status,passed:proof.tests.filter(x=>x.status==='passed').length,
      failed:proof.tests.filter(x=>x.status==='failed').length,skipped:0,proof:path.join(output,'review-verification.json')}));
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
