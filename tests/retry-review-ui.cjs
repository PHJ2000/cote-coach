/* F2 review regressions. Uses the unchanged disposable Python retry fixture,
 * a fresh Chromium context per case and only synthetic records. Build first.
 * Browser/fixture unavailability is a failure, never a skipped acceptance gate.
 * No real capture, user profile, AI, IDE, Java or STT subprocess is used here.
 */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const vm=require('node:vm');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const output=process.env.RETRY_REVIEW_UI_OUTPUT||fs.mkdtempSync(path.join(os.tmpdir(),'retry-f2-review-ui-'));
fs.mkdirSync(output,{recursive:true});
const records=[];
const atomicDateOnly=process.argv.includes('--atomic-date-only');
const contractOnly=process.argv.includes('--anchor-contract-only');
const mockContract=!process.argv.includes('--real-contract');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};

async function navigationTests(){
 const source=fs.readFileSync(path.join(root,'client/app.js'),'utf8');
 const pick=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end));
 function scope(overrides={}){
  const opened=[];
  const state={navigationGeneration:0,drain:async()=>{},api:async url=>({id:url.split('/').at(-1),status:'active'}),applyUnsentCode:async()=>{},opened,
   cleanup(){state.navigationGeneration++;},openWorkspace(s){opened.push(s.id);state.navigationGeneration++;},...overrides};
  vm.createContext(state);
  vm.runInContext(pick('async function loadSession(id){','async function applyUnsentCode(s){'),state);
  vm.runInContext(pick('async function openReview(id,','function refreshMarkers(){'),state);
  return state;
 }
 async function check(name,run){
  const record={name,phase:'navigation VM',started_at_utc:new Date().toISOString()};records.push(record);
  try{await run();record.status='passed';console.log('PASS '+name);}catch(e){record.status='failed';record.error=e.stack;console.error('FAIL '+name+'\n'+e.stack);}
  record.ended_at_utc=new Date().toISOString();
 }
 await check('R2-001: superseded outbox drain cannot issue a source GET',async()=>{
  const gate=deferred();let reads=0;const s=scope({drain:()=>gate.promise,api:async()=>{reads++;}});
  const pending=s.loadSession('old');s.navigationGeneration++;gate.resolve();await pending;
  assert.equal(reads,0);assert.deepEqual(s.opened,[]);
 });
 await check('R2-001: superseded unsent-code recovery cannot open the old workspace',async()=>{
  const entered=deferred(),gate=deferred();const s=scope({applyUnsentCode:async value=>{if(value.id==='old'){entered.resolve();await gate.promise;}}});
  const old=s.loadSession('old');await entered.promise;await s.loadSession('retry');gate.resolve();await old;
  assert.deepEqual(s.opened,['retry']);
 });
 await check('R2-001: duplicate resume intents mount only the newest editor',async()=>{
  const entered=deferred(),gate=deferred();let reads=0;
  const s=scope({api:async()=>{if(++reads===1){entered.resolve();await gate.promise;}return {id:'retry',status:'active'};}});
  const old=s.loadSession('retry');await entered.promise;await s.loadSession('retry');gate.resolve();await old;
  assert.deepEqual(s.opened,['retry']);assert.equal(reads,2);
 });
 await check('R2-001: late source errors are discarded while current GET errors propagate',async()=>{
  const entered=deferred(),gate=deferred();const error=Error('synthetic source read failed');
  const s=scope({api:async url=>{if(url.endsWith('/old')){entered.resolve();await gate.promise;throw error;}return {id:'retry',status:'active'};}});
  const old=s.loadSession('old');await entered.promise;await s.loadSession('retry');gate.resolve();await old;assert.deepEqual(s.opened,['retry']);
  const current=scope({api:async()=>{throw error;}});await assert.rejects(current.loadSession('current'),e=>e===error);
 });
 await check('R2-001: current review GET errors survive the nested navigation generation',async()=>{
  let reads=0;const error=Error('synthetic review read failed');
  const s=scope({api:async()=>{if(++reads===1)return {id:'source',status:'completed'};throw error;}});
  await assert.rejects(s.loadSession('source'),e=>e===error);assert.equal(reads,2);
 });
 await check('R2-001: current editor initialization errors still reach the caller',async()=>{
  const error=Error('synthetic editor initialization failed');
  const s=scope({openWorkspace(){s.navigationGeneration++;throw error;}});
  await assert.rejects(s.loadSession('retry'),e=>e===error);
 });
}

async function fixture(){
 const child=spawn(process.env.RETRY_TEST_PYTHON||'python3',['-u',path.join(__dirname,'retry_fixture.py'),'--browser','--port','0'],
  {cwd:root,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
 let stderr='',stdout='';child.stderr.on('data',b=>stderr+=b);
 const ready=new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture handshake timeout: '+stderr)),15000);
  child.once('error',e=>{clearTimeout(timer);reject(e);});
  child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code+': '+stderr));});
  child.stdout.on('data',b=>{stdout+=b;if(!stdout.includes('\n'))return;clearTimeout(timer);try{resolve(JSON.parse(stdout.split('\n')[0]));}catch(e){reject(e);}});
 });
 async function stop(){
  if(child.exitCode!==null||child.signalCode!==null)return;
  const ended=once(child,'exit');child.kill('SIGTERM');
  const timer=setTimeout(()=>child.kill('SIGKILL'),8000);await ended;clearTimeout(timer);
 }
 try{
  const h=await ready;assert.equal(h.pid_process,child.pid);assert.equal(h.external_ai_calls,0);
  assert.match(h.base_url,/^http:\/\/127\.0\.0\.1:\d+$/);
  async function request(url,data,control=false){
   const response=await fetch(h.base_url+url,{method:data?'POST':'GET',headers:{'Content-Type':'application/json',...(control?{'X-Retry-Fixture':h.token}:{})},body:data?JSON.stringify(data):undefined});
   const body=await response.json();assert.equal(response.status,200,JSON.stringify(body));return body;
  }
  return {h,stop,api:request,control:(name,data)=>request('/_retry_fixture/'+name,data,true)};
 }catch(e){await stop();throw e;}
}

async function test(browser,name,run){
 if(atomicDateOnly&&!name.startsWith('R2-003: date change between'))return;
 if(contractOnly&&!name.startsWith('R2-003:'))return;
 const record={name,started_at_utc:new Date().toISOString(),status:'running'};records.push(record);
 let owner,context,page;
 try{
  owner=await fixture();record.source_before=(await owner.control('proof')).source_snapshot;
  context=await browser.newContext({baseURL:owner.h.base_url,viewport:{width:1440,height:1000},timezoneId:'Asia/Seoul'});
  context.setDefaultTimeout(5000);
  const errors=[],consoleErrors=[];let expectedErrors=0;
  await context.route('**/*',r=>{
   const url=new URL(r.request().url());assert.equal(url.origin,owner.h.base_url,'Unexpected external request');return r.continue();
  });
  record.contract_transport=mockContract?'mock capability + mock anchor guard on unchanged fixture':'actual merged server';
  if(mockContract){
   await context.route('**/api/v2/retries',async r=>{
    const response=await r.fetch(),dto=await response.json();await r.fulfill({response,json:{...dto,capabilities:{retry_plan_expected_anchor_date:true}}});
   });
   await context.route('**/api/v2/retry-plan',async r=>{
    const input=JSON.parse(r.request().postData()),dto=await owner.api('/api/v2/retries'),now=dto.server_now;
    const parts=Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:input.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now).map(p=>[p.type,p.value]));
    const anchor=`${parts.year}-${parts.month}-${parts.day}`,preview=[1,3,7].map(offset_days=>{const day=new Date(anchor+'T00:00:00Z');day.setUTCDate(day.getUTCDate()+offset_days);return {offset_days,due_date:day.toISOString().slice(0,10)};});
    if(input.expected_anchor_date!==undefined&&input.expected_anchor_date!==anchor){
     await r.fulfill({status:400,json:{code:'ANCHOR_DATE_CHANGED',error:'Synthetic mock anchor guard',expected_anchor_date:input.expected_anchor_date,current_anchor_date:anchor,timezone:input.timezone,server_now:now,preview}});return;
    }
    await r.continue();
   });
  }
  // Observe JSON consumption so delayed-response assertions run after the app
  // receives the response; this observer does not change its payload or timing.
  await context.addInitScript(()=>{
   window.f2Reads={};window.f2Events=[];
   const original=window.fetch;
   window.fetch=async(...args)=>{
    const response=await original(...args),json=response.json.bind(response);
    response.json=async()=>{const value=await json(),url=String(args[0]);window.f2Reads[url]=(window.f2Reads[url]||0)+1;return value;};
    return response;
   };
  });
  page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{
   if(m.type()!=='error')return;
   if([...(record.injectedErrors||[]),...(record.injectedError?[record.injectedError]:[])].some(url=>m.location().url.includes(url))){expectedErrors++;return;}
   consoleErrors.push(m.text());
  });
  await run({owner,context,page,record});
  assert.deepEqual(errors,[]);assert.deepEqual(consoleErrors,[]);
  record.page_errors=errors;record.console_errors=consoleErrors;record.expected_console_errors=expectedErrors;
  assert.deepEqual((await owner.control('proof')).source_snapshot,record.source_before,'Synthetic original changed');
  record.source_preserved=true;delete record.source_before;record.status='passed';console.log('PASS '+name);
 }catch(e){
  record.status='failed';record.error=e.stack;console.error('FAIL '+name+'\n'+e.stack);
  if(page)await page.screenshot({path:path.join(output,'failure-'+records.length+'.png'),fullPage:true}).catch(()=>{});
 }finally{
  if(owner&&record.source_before){
   try{assert.deepEqual((await owner.control('proof')).source_snapshot,record.source_before,'Synthetic original changed');record.source_preserved=true;}
   catch(e){record.source_preserved=false;record.status='failed';record.error+='\n'+e.stack;}
   delete record.source_before;
  }
  record.ended_at_utc=new Date().toISOString();await context?.close();await owner?.stop();
 }
}

async function review(page,h){
 await page.goto('/');await page.locator('[data-session="'+h.source_sid+'"]').first().click();
 await page.locator('[data-retry-registration]').waitFor();
}
async function draft(page){
 await page.locator('[name="reason"]').fill('등록 중 입력 보존 검증');
 await page.locator('[name="timezone_confirmed"]').check();await page.locator('[name="reason"]').focus();
}
async function settled(page){await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
async function assertDraft(page){
 assert.equal(await page.locator('[name="reason"]').inputValue(),'등록 중 입력 보존 검증');
 assert.equal(await page.locator('[name="timezone_confirmed"]').isChecked(),true);
 assert.equal(await page.evaluate(()=>document.activeElement.name),'reason');
}

(async()=>{
 let browser;
 try{
  if(!atomicDateOnly&&!contractOnly)await navigationTests();if(process.argv.includes('--navigation-only'))return;
  browser=await chromium.launch({headless:true});
  for(const destination of ['resume','prepare'])for(const stage of [1,2]){
   await test(browser,`R2-001: late source GET ${stage} preserves ${destination}`,async({owner,page,record})=>{
    const {h,api,control}=owner;
    const plan=(await api('/api/v2/retry-plan',{source_sid:h.source_sid,pid:h.pid,reason:h.sentinels.reason,timezone:'Asia/Seoul'})).plan;
    await control('advance',{ms:86400000});let sid;
    if(destination==='resume')sid=(await api('/api/v2/retry-start',{item_id:plan.items[0].id,kind:'single',mode:'training',duration:40,resources:'none'})).session.id;
    await page.goto('/');await page.locator('[data-retry-action="'+destination+'"]').waitFor();
    const entered=deferred(),released=deferred();let reads=0;
    const url='/api/v2/session/'+h.source_sid;
    await page.route('**'+url,async r=>{
     const response=await r.fetch();if(++reads===stage){entered.resolve();await released.promise;}
     await r.fulfill({response});
    });
    await page.locator('[data-session="'+h.source_sid+'"]').first().click();await entered.promise;
    await page.locator('[data-retry-action="'+destination+'"]').click();
    if(destination==='resume')await page.locator('.ws .monaco-editor').waitFor();
    else await page.getByRole('heading',{name:'재도전 준비',exact:true}).waitFor();
    const activeBefore=await page.evaluate(()=>localStorage.getItem('lab-active'));
    released.resolve();await page.waitForFunction(({url,stage})=>(window.f2Reads[url]||0)>=stage,{url,stage});await settled(page);
    if(destination==='resume'){
     // On the broken first-GET path a second source read completes the overwrite.
     if(stage===1&&reads>1){await page.waitForFunction(url=>(window.f2Reads[url]||0)>=2,url);await settled(page);}
     assert.equal(await page.locator('.ws').count(),1,'Late original response stole retry workspace');
     assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('lab-active'))),sid);
     assert.equal(await page.locator('.monaco-editor').count(),1);
    }else assert.equal(await page.getByRole('heading',{name:'재도전 준비',exact:true}).count(),1);
    assert.equal(await page.evaluate(()=>localStorage.getItem('lab-active')),activeBefore);
    for(const sentinel of Object.values(h.sentinels))assert(!(await page.locator('#app').innerHTML()).includes(sentinel),'Historical sentinel leaked');
    record.observed={source_reads:reads,destination,stage,active_sid:destination==='resume'?sid:null};
   });
  }
  await test(browser,'R2-002: terminal background job preserves draft, checkbox and focus',async({owner,page,record})=>{
   const url='/api/v2/session/'+owner.h.source_sid;let reads=0;
   await page.route('**'+url,async r=>{const response=await r.fetch(),s=await response.json();s.jobs=[{id:'f2-job',kind:'codex',status:++reads<=2?'running':'completed'}];await r.fulfill({response,json:s});});
   await review(page,owner.h);await draft(page);
   await page.waitForFunction(url=>(window.f2Reads[url]||0)>=3,url);await settled(page);await assertDraft(page);
   record.observed={source_reads:reads,reason:await page.locator('[name="reason"]').inputValue()};
  });
  for(const outcome of ['success','failure'])await test(browser,`R2-002: job update during ${outcome} save keeps one live request`,async({owner,page,record})=>{
   const url='/api/v2/session/'+owner.h.source_sid;let reads=0,posts=0;
   await page.route('**'+url,async r=>{const response=await r.fetch(),s=await response.json();s.jobs=[{id:'f2-job',kind:'codex',status:++reads<=2?'running':'completed'}];await r.fulfill({response,json:s});});
   const entered=deferred(),released=deferred();
   if(outcome==='failure')record.injectedError='/api/v2/retry-plan';
   await page.route('**/api/v2/retry-plan',async r=>{
    posts++;entered.resolve();await released.promise;
    if(outcome==='failure')await r.fulfill({status:400,json:{code:'INVALID_INPUT',error:'synthetic fixture failure'}});
    else await r.fulfill({response:await r.fetch()});
   });
   await review(page,owner.h);await draft(page);await page.locator('[type="submit"]').click();await entered.promise;
   await page.waitForFunction(url=>(window.f2Reads[url]||0)>=3,url);await settled(page);
   assert.equal(await page.locator('form').getAttribute('aria-busy'),'true','Job update orphaned save');
   await page.locator('form').evaluate(f=>f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));assert.equal(posts,1);
   released.resolve();
   if(outcome==='success')await page.getByRole('status').filter({hasText:'예약을 저장했어요'}).waitFor();
   else{
    await page.locator('[data-retry-registration-status]').filter({hasText:'입력 내용을 확인'}).waitFor();
    assert.equal(await page.locator('[name="reason"]').inputValue(),'등록 중 입력 보존 검증');
    assert.equal(await page.locator('[name="timezone_confirmed"]').isChecked(),true);
   }
   record.observed={posts,outcome,source_reads:reads};
  });
  await test(browser,'R2-003: server midnight blocks stale confirmation before POST',async({owner,page,record})=>{
   await owner.control('clock',{now:Date.parse('2026-10-01T14:59:59Z')});
   // An intentionally wrong PC wall clock must not define the registration day.
   await page.addInitScript(()=>{Date.now=()=>Date.parse('2038-01-01T00:00:00Z');});
   await review(page,owner.h);await draft(page);
   const before=await page.locator('[data-retry-date-preview]').textContent();assert(before.includes('2026-10-02'));
   let posts=0;page.on('request',r=>{if(r.url().endsWith('/api/v2/retry-plan'))posts++;});
   await owner.control('advance',{ms:2000});await page.locator('[type="submit"]').click();
   await page.waitForFunction(()=>!document.querySelector('form')||document.querySelector('form').getAttribute('aria-busy')!=='true');
   assert.equal(posts,0,'Unconfirmed new server dates were registered');
   assert.equal(await page.locator('[name="timezone_confirmed"]').isChecked(),false);
   assert.equal(await page.locator('[name="reason"]').inputValue(),'등록 중 입력 보존 검증');
   const after=await page.locator('[data-retry-date-preview]').textContent();assert(after.includes('2026-10-03'));
   await page.locator('[name="timezone_confirmed"]').check();
   const savedResponse=page.waitForResponse(r=>r.url().endsWith('/api/v2/retry-plan')&&r.status()===200);
   await page.locator('[type="submit"]').click();const saved=await (await savedResponse).json();
   assert.deepEqual(saved.plan.items.map(i=>i.due_date),['2026-10-03','2026-10-05','2026-10-09']);
   assert.equal(posts,1);record.observed={before,after,posts,saved_dates:saved.plan.items.map(i=>i.due_date)};
  });
  await test(browser,'R2-003: background focus updates dates without losing reason',async({owner,page,record})=>{
   await owner.control('clock',{now:Date.parse('2026-10-01T14:59:59Z')});await review(page,owner.h);await draft(page);
   await owner.control('advance',{ms:2000});
   await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
   await page.locator('[data-retry-date-preview]').filter({hasText:'2026-10-03'}).waitFor();
   assert.equal(await page.locator('[name="timezone_confirmed"]').isChecked(),false);
   assert.equal(await page.locator('[name="reason"]').inputValue(),'등록 중 입력 보존 검증');record.observed={confirmation:false,preview:await page.locator('[data-retry-date-preview]').textContent()};
  });
  await test(browser,'R2-003: date change between preflight and POST must not save unconfirmed dates',async({owner,page,record})=>{
   await owner.control('clock',{now:Date.parse('2026-10-01T14:59:59Z')});await review(page,owner.h);await draft(page);
   const expected=await page.locator('[data-retry-date-preview] li span').allTextContents();
   // The new UI must always send the confirmed day. The mock guard proves UI
   // handling only; SQLite transaction correctness remains the F1/F3 gate.
   record.injectedError='/api/v2/retry-plan';let posts=0;
   await page.route('**/api/v2/retry-plan',async r=>{if(++posts===1)await owner.control('advance',{ms:2000});await r.fallback();});
   const responsePromise=page.waitForResponse(r=>r.url().endsWith('/api/v2/retry-plan'));
   await page.locator('[type="submit"]').click();const response=await responsePromise,dto=await response.json();
   assert.equal(response.status(),400);assert.equal(dto.code,'ANCHOR_DATE_CHANGED');assert.equal(dto.expected_anchor_date,'2026-10-01');
   await page.locator('[data-retry-date-preview]').filter({hasText:'2026-10-03'}).waitFor();
   assert.equal(await page.locator('[name="timezone_confirmed"]').isChecked(),false);
   assert.equal(await page.locator('[name="reason"]').inputValue(),'등록 중 입력 보존 검증');
   assert.equal(await page.locator('[name="timezone"]').inputValue(),'Asia/Seoul');
   assert.equal(await page.locator('[data-retry-view="registration"]').getByText('예약을 저장했어요',{exact:false}).count(),0);
   assert.equal((await owner.api('/api/v2/retries')).upcoming.length,0,'Guard mock wrote a plan');
   await page.locator('[type="submit"]').click();await settled(page);assert.equal(posts,1,'Automatic/unconfirmed retry was sent');
   await page.locator('[name="timezone_confirmed"]').check();
   const savedResponse=page.waitForResponse(r=>r.url().endsWith('/api/v2/retry-plan')&&r.status()===200);
   await page.locator('[type="submit"]').click();const saved=await(await savedResponse).json();
   assert.equal(posts,2);assert.deepEqual(saved.plan.items.map(i=>i.due_date),['2026-10-03','2026-10-05','2026-10-09']);
   record.observed={preview_dates:expected,http_status:response.status(),first_code:dto.code,first_expected:dto.expected_anchor_date,posts,saved_dates:saved.plan.items.map(i=>i.due_date)};
  });
  for(const capability of ['missing',false,'true','network-error'])await test(browser,`R2-003: capability ${capability} blocks POST and keeps draft`,async({owner,page,record})=>{
   if(capability==='network-error')record.injectedError='/api/v2/retries';
   await page.route('**/api/v2/retries',async r=>{
    if(capability==='network-error'){await r.abort('failed');return;}
    const response=await r.fetch(),dto=await response.json();
    if(capability==='missing')delete dto.capabilities;
    else dto.capabilities={retry_plan_expected_anchor_date:capability};
    await r.fulfill({response,json:dto});
   });
   let posts=0;page.on('request',r=>{if(r.url().endsWith('/api/v2/retry-plan'))posts++;});
   await review(page,owner.h);await draft(page);
   await page.locator('[data-retry-registration-status]').filter({hasText:'서버의 날짜 확인 기능'}).waitFor();
   assert.equal(await page.locator('[type="submit"]').isDisabled(),true);
   await page.locator('form').evaluate(f=>f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));await settled(page);
   assert.equal(posts,0);assert.equal(await page.locator('[name="reason"]').inputValue(),'등록 중 입력 보존 검증');
   record.observed={capability,posts,reason_preserved:true};
  });
  for(const committed of [true,false])await test(browser,`R2-003: response loss ${committed?'after':'before'} commit retries the identical confirmed body`,async({owner,page,record})=>{
   await owner.control('clock',{now:Date.parse('2026-10-01T14:59:59Z')});let saved=null;const bodies=[];
   record.injectedErrors=['/api/v2/retry-plan'];
   await page.route('**/api/v2/retry-plan',async r=>{
    const input=JSON.parse(r.request().postData());bodies.push(input);
    if(bodies.length===1){if(committed)saved=await owner.api('/api/v2/retry-plan',input);await r.abort('failed');return;}
    if(committed){await r.fulfill({json:{...saved,created:false}});return;}
    await r.fallback();
   });
   await review(page,owner.h);await draft(page);await page.locator('[type="submit"]').click();
   await page.locator('[data-retry-registration-status]').filter({hasText:'서버에 연결하지 못했어요'}).waitFor();
   assert.equal(bodies[0].expected_anchor_date,'2026-10-01');
   await owner.control('advance',{ms:2000});await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
   await page.locator('[data-retry-date-preview]').filter({hasText:'2026-10-03'}).waitFor();
   await page.locator('[type="submit"]').click();
   if(committed){
    await page.getByRole('status').filter({hasText:'이미 저장된 예약이에요'}).waitFor();
    assert.deepEqual(bodies[1],bodies[0]);assert.equal(bodies.length,2);
    assert((await page.locator('[data-retry-view="registration"]').textContent()).includes('2026-10-02'));
   }else{
    await page.locator('[data-retry-registration-status]').filter({hasText:'기준 날짜가 바뀌어'}).waitFor();
    assert.deepEqual(bodies[1],bodies[0]);assert.equal(await page.locator('[name="timezone_confirmed"]').isChecked(),false);
    assert.equal((await owner.api('/api/v2/retries')).upcoming.length,0);
    await page.locator('[name="timezone_confirmed"]').check();
    await page.locator('[type="submit"]').click();await page.getByRole('status').filter({hasText:'예약을 저장했어요'}).waitFor();
    assert.equal(bodies[2].expected_anchor_date,'2026-10-02');assert.equal(bodies.length,3);
   }
   record.observed={committed,bodies,same_retry_body:true,plan_id:saved?.plan.id};
  });
  for(const fallbackFails of [false,true])await test(browser,`R2-003: malformed date error uses ${fallbackFails?'blocked':'valid'} clock fallback`,async({owner,page,record})=>{
   await owner.control('clock',{now:Date.parse('2026-10-01T14:59:59Z')});let failClock=false,posts=0,clockGets=0;
   record.injectedErrors=['/api/v2/retry-plan',...(fallbackFails?['/api/v2/retries']:[])];
   await page.route('**/api/v2/retries',async r=>{clockGets++;if(failClock)await r.abort('failed');else await r.fallback();});
   await page.route('**/api/v2/retry-plan',async r=>{
    if(++posts>1){await r.fallback();return;}
    await owner.control('advance',{ms:2000});failClock=fallbackFails;
    await r.fulfill({status:400,json:{code:'ANCHOR_DATE_CHANGED',server_now:'INVALID_CLOCK',preview:[{offset_days:1,due_date:'2026-99-99'}]}});
   });
   await review(page,owner.h);await draft(page);const before=clockGets;await page.locator('[type="submit"]').click();
   if(fallbackFails){
    await page.locator('[data-retry-registration-status]').filter({hasText:'서버의 날짜 확인 기능'}).waitFor();
    assert.equal(await page.locator('[type="submit"]').isDisabled(),true);
    failClock=false;await page.locator('[data-retry-clock-refresh]').click();
   }
   await page.locator('[data-retry-date-preview]').filter({hasText:'2026-10-03'}).waitFor();
   assert(clockGets>=before+2,'Malformed error did not request a fresh clock');
   assert.equal(await page.locator('[name="reason"]').inputValue(),'등록 중 입력 보존 검증');assert.equal(await page.locator('[name="timezone_confirmed"]').isChecked(),false);assert.equal(posts,1);
   await page.locator('[name="timezone_confirmed"]').check();await page.locator('[type="submit"]').click();
   await page.getByRole('status').filter({hasText:'예약을 저장했어요'}).waitFor();assert.equal(posts,2);
   record.observed={fallbackFails,clockGets,posts,reason_preserved:true};
  });
  await test(browser,'R2-003: a second date change requires another explicit confirmation',async({owner,page,record})=>{
   await owner.control('clock',{now:Date.parse('2026-10-01T14:59:59Z')});let posts=0;const expected=[];
   record.injectedError='/api/v2/retry-plan';
   await page.route('**/api/v2/retry-plan',async r=>{expected.push(JSON.parse(r.request().postData()).expected_anchor_date);if(++posts<=2)await owner.control('advance',{ms:posts===1?2000:86400000});await r.fallback();});
   await review(page,owner.h);await draft(page);await page.locator('[type="submit"]').click();
   await page.locator('[data-retry-date-preview]').filter({hasText:'2026-10-03'}).waitFor();assert.equal(await page.locator('[name="timezone_confirmed"]').isChecked(),false);
   await page.locator('[name="timezone_confirmed"]').check();await page.locator('[type="submit"]').click();
   await page.locator('[data-retry-date-preview]').filter({hasText:'2026-10-04'}).waitFor();assert.equal(await page.locator('[name="timezone_confirmed"]').isChecked(),false);
   assert.equal(posts,2);assert.equal(await page.locator('[name="reason"]').inputValue(),'등록 중 입력 보존 검증');
   await page.locator('[type="submit"]').click();await settled(page);assert.equal(posts,2);
   await page.locator('[name="timezone_confirmed"]').check();await page.locator('[type="submit"]').click();await page.getByRole('status').filter({hasText:'예약을 저장했어요'}).waitFor();
   assert.deepEqual(expected,['2026-10-01','2026-10-02','2026-10-03']);assert.equal(posts,3);record.observed={posts,expected};
  });
  await test(browser,'R2-003: late anchor error cannot replace a resumed retry workspace',async({owner,page,record})=>{
   const entered=deferred(),released=deferred();let posts=0;record.injectedError='/api/v2/retry-plan';
   await page.route('**/api/v2/retry-plan',async r=>{posts++;const input=JSON.parse(r.request().postData());entered.resolve();await released.promise;
    await r.fulfill({status:400,json:{code:'ANCHOR_DATE_CHANGED',expected_anchor_date:input.expected_anchor_date,current_anchor_date:'2026-10-02',timezone:input.timezone,server_now:Date.parse('2026-10-01T15:00:01Z'),preview:[1,3,7].map(offset_days=>({offset_days,due_date:'2026-10-'+String(2+offset_days).padStart(2,'0')}))}});
   });
   await review(page,owner.h);await draft(page);await page.locator('[type="submit"]').click();await entered.promise;
   const plan=(await owner.api('/api/v2/retry-plan',{source_sid:owner.h.source_sid,pid:owner.h.pid,reason:owner.h.sentinels.reason,timezone:'Asia/Seoul'})).plan;
   await owner.control('advance',{ms:86400000});const sid=(await owner.api('/api/v2/retry-start',{item_id:plan.items[0].id,kind:'single',mode:'training',duration:40,resources:'none'})).session.id;
   await page.locator('[data-action="review-home"]').click();await page.locator('[data-retry-action="resume"]').click();await page.locator('.ws .monaco-editor').waitFor();
   released.resolve();await page.waitForFunction(()=>(window.f2Reads['/api/v2/retry-plan']||0)>=1);await settled(page);
   assert.equal(await page.locator('.ws').count(),1);assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('lab-active'))),sid);
   for(const sentinel of Object.values(owner.h.sentinels))assert(!(await page.locator('#app').innerHTML()).includes(sentinel));assert.equal(posts,1);record.observed={posts,sid,workspace_preserved:true};
  });
  for(const failure of ['NETWORK_ERROR','INVALID_INPUT'])await test(browser,`R3-004: first ${failure} list failure offers keyboard recovery`,async({owner,page,record})=>{
   const {h,api,control}=owner;
   await api('/api/v2/retry-plan',{source_sid:h.source_sid,pid:h.pid,reason:'목록 복구',timezone:'Asia/Seoul'});await control('advance',{ms:86400000});
   let fail=true,requests=0;record.injectedError='/api/v2/retries';
   await page.route('**/api/v2/retries',r=>{
    requests++;if(fail)return failure==='NETWORK_ERROR'?r.abort('failed'):r.fulfill({status:400,json:{code:'INVALID_INPUT',error:'synthetic failure'}});
    return r.continue();
   });
   await page.goto('/');
   await page.getByRole('button',{name:'다시 조회',exact:true}).waitFor();
   const region=page.locator('[data-retry-view="list"]');assert(!(await region.textContent()).includes('불러오는 중'));
   assert((await region.textContent()).includes(failure==='NETWORK_ERROR'?'서버에 연결하지 못했어요':'입력 내용을 확인해 주세요'));
   fail=false;const previous=requests;await page.getByRole('button',{name:'다시 조회',exact:true}).focus();await page.keyboard.press('Enter');
   await page.locator('[data-retry-state="due"]').waitFor();assert.equal(requests,previous+1);
   // A subsequent failed refresh after a successful DTO also remains recoverable.
   fail=true;await page.evaluate(()=>window.dispatchEvent(new Event('online')));
   await page.getByRole('button',{name:'다시 조회',exact:true}).waitFor();assert(!(await region.textContent()).includes('불러오는 중'));
   record.observed={failure,requests,due_recovered:true};
  });
 }catch(e){
  records.push({name:'F2 browser/fixture harness',phase:'environment or harness',status:'failed',error:e.stack});console.error(e);process.exitCode=1;
 }finally{
  await browser?.close();
  const counts={passed:records.filter(r=>r.status==='passed').length,failed:records.filter(r=>r.status==='failed').length,skipped:0};
  fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({counts,records,selection:atomicDateOnly?'atomic-date-only':contractOnly?'anchor-contract-only':process.argv.includes('--navigation-only')?'navigation-only':'all',server_contract_mocked:mockContract,mocks:['java','ide','coach','stt','media_subprocess'],external_ai_calls:0},null,2)+'\n');
  console.log('RESULT F2 review UI '+JSON.stringify(counts)+'; evidence='+output);
  if(counts.failed)process.exitCode=1;
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
