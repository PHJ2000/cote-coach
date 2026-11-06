// P2 retry-v1 fixture tests. No app server, real records, model/STT or user profile.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const vm=require('node:vm');
const {buildSync}=require('esbuild');
const root=path.resolve(__dirname,'..');
const evidence=process.env.RETRY_UI_OUTPUT||fs.mkdtempSync(path.join(os.tmpdir(),'cote-retries-ui-'));
fs.mkdirSync(evidence,{recursive:true});
const source=path.join(root,'client/retries.js');
const bundle=buildSync({entryPoints:[source],bundle:true,write:false,platform:'node',format:'cjs',loader:{'.css':'empty'}}).outputFiles[0].text;
const moduleStub={exports:{}};
let automaticListeners=0;
vm.runInNewContext(bundle,{module:moduleStub,exports:moduleStub.exports,console,Intl,Date,AbortController,URL,
 document:{addEventListener(){automaticListeners++;}},window:{addEventListener(){automaticListeners++;}},setTimeout,clearTimeout});
const ui=moduleStub.exports;
const now=Date.parse('2026-10-01T12:00:00Z');
const empty=()=>({server_now:now,next_refresh_at:now+43200000,due:[],active:[],grading:[],unavailable:[],upcoming:[]});
const card=(state='due')=>({plan_id:'p1',item_id:'i1',pid:'pair',title:'짝 찾기',level:2,offset_days:1,due_date:'2026-10-02',timezone:'Asia/Seoul',today:'2026-10-04',state,overdue_days:2,outstanding_due_count:2,can_start:state==='due',defaults:{mode:'training',duration:40,resources:'none'}});
const summary=()=>({status:'completed',source_sid:'source-1',pid:'pair',title:'짝 찾기',eligible:true,code:'ELIGIBLE',defaults:card().defaults,server_now:now,timezone:'Asia/Seoul'});
const input=()=>({source_sid:'source-1',pid:'pair',reason:' 경계값을 놓쳤다 ',timezone:'Asia/Seoul',timezone_confirmed:true});
const plan=()=>({created:true,plan:{id:'p1',source_sid:'source-1',pid:'pair',reason:'경계값을 놓쳤다',timezone:'Asia/Seoul',anchor_date:'2026-10-01',items:[1,3,7].map(offset_days=>({id:'i'+offset_days,offset_days,due_date:'2026-10-'+String(1+offset_days).padStart(2,'0'),state:'scheduled',session_id:null}))}});
const attempt=(overrides={})=>({minutes:12.3,outcome:'failed',overtime_outcome:'passed',run_id:'r1',passed:2,total:3,mode:'training',duration:40,resources:'none',hints:0,result_basis:'submit_events',...overrides});
const comparison=()=>({state:'ready',plan_id:'p1',item_id:'i1',source_sid:'source-1',current_sid:'retry-1',pid:'pair',reason:'경계값을 놓쳤다',computed_at:now,source:attempt(),current:attempt({minutes:11.1,outcome:'grader_error',overtime_outcome:'not_submitted',hints:1}),delta_minutes:-1.2,time_basis:'문제 선택 구간의 경과 시간(탭을 닫은 시간 포함)',conditions_differ:true,limitations:['모의 fixture 결과']});
const sentinels={reason:'REASON_SENTINEL_DP',tags:['TAG_SENTINEL'],code:'CODE_SENTINEL',report:'REPORT_SENTINEL',source_sid:'SOURCE_SID_SENTINEL',messages:['MESSAGE_SENTINEL'],media:['MEDIA_SENTINEL']};
const blind=html=>{for(const key of Object.values(sentinels).flat())assert(!html.includes(key),'historical sentinel leaked: '+key);};
const records=[];
async function check(name,fn){
 try{await fn();records.push({name,result:'PASS'});console.log('PASS '+name);}
 catch(error){records.push({name,result:'FAIL',error:error.stack});console.error('FAIL '+name+'\n'+error.stack);}
}
const plain=value=>JSON.parse(JSON.stringify(value));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
class Target extends EventTarget{hidden=false;fire(type){this.dispatchEvent(new Event(type));}}

(async()=>{
 await check('import installs no global events or automatic requests',()=>assert.equal(automaticListeners,0));
 await check('empty and loading states',()=>{assert.match(ui.renderRetryList(empty()),/오늘 다시 풀 문제가 없어요/);assert.match(ui.renderRetryList(null),/불러오는 중/);});
 await check('first and later list errors end loading and expose the mapped error and retry control',()=>{
  for(const dto of [null,empty()])for(const code of ['NETWORK_ERROR','INVALID_INPUT']){
   const html=ui.renderRetryList(dto,{loading:false,error:new ui.RetryApiError(code)});
   assert(!html.includes('불러오는 중'));assert(html.includes(ui.retryErrorMessage({code})));
   assert(html.includes('data-retry-action="refresh"'));assert(html.includes('다시 조회'));
  }
 });
 await check('due, active, grading, unavailable, collapsed upcoming',()=>{
  const dto=empty();for(const state of ['due','active','grading','unavailable','upcoming'])dto[state].push({...card(state),session_id:'retry-1'});
  const html=ui.renderRetryList(dto);for(const state of Object.keys(dto).filter(k=>Array.isArray(dto[k])))assert(html.includes('data-retry-state="'+state+'"'));
  assert.match(html,/data-retry-action="prepare"/);assert.match(html,/data-retry-action="resume"/);assert.match(html,/미완료 2회차/);assert.match(html,/2일 늦음/);
  assert.match(html,/<details class="retry-upcoming">/);assert(!/<details[^>]+\bopen\b/.test(html));
 });
 await check('server decides due; PC dates do not alter card state',()=>{
  const dto=empty();dto.due=[{...card(),due_date:'2999-01-01'}];dto.upcoming=[{...card('upcoming'),due_date:'1999-01-01'}];
  assert.match(ui.renderRetryList(dto),/data-retry-action="prepare"/);assert.match(ui.renderRetryList(dto),/2999-01-01/);
 });
 await check('list and non-ready comparison hide all historical sentinels',()=>{
  const dto=empty();for(const state of ['due','active','grading','unavailable','upcoming'])dto[state]=[{...card(state),...sentinels,session_id:'retry-1'}];
  blind(ui.renderRetryList(dto));for(const state of ['active','grading','unavailable','unknown'])blind(ui.renderRetryComparison({state,...sentinels,current:attempt()}));
  blind(ui.renderRetryRegistration({...summary(),status:'active',...sentinels}));
 });
 await check('HTML text, attribute, textarea and comparison escaping',()=>{
  const evil='</textarea><img src=x onerror=alert(1)>"\'&';
  const dto=empty();dto.due=[{...card(),title:evil,item_id:evil,timezone:evil}];
  for(const html of [ui.renderRetryList(dto),ui.renderRetryRegistration(summary(),{reason:evil}),ui.renderRetryComparison({...comparison(),reason:evil,source_sid:evil,limitations:[evil]})]){
   assert(!html.includes('<img'));assert(html.includes('&lt;'));assert(html.includes('&quot;'));
  }
 });
 await check('disabled start and busy state do not imply completion',()=>{
  const dto=empty();dto.due=[{...card(),can_start:false}];assert.match(ui.renderRetryList(dto),/data-retry-item="i1" disabled/);
  dto.due[0].can_start=true;assert.match(ui.renderRetryList(dto,{pending_item_id:'i1'}),/시작 중/);
  assert(!ui.renderRetryList(dto).includes('성공'));
 });
 await check('server eligibility prevents passed/error/ungraded/active registration',()=>{
  for(const code of ['NOT_ELIGIBLE','GRADE_UNAVAILABLE','GRADING_PENDING']){
   const html=ui.renderRetryRegistration({...summary(),eligible:false,code,reason:'PRIVATE'});assert(!html.includes('<form'));assert(!html.includes('PRIVATE'));
  }
  assert(!ui.renderRetryRegistration({...summary(),eligible:undefined}).includes('<form'));
  assert(!ui.renderRetryRegistration({...summary(),existing_plan_id:'p1'}).includes('<form'));
 });
 await check('registration has labels, counter, dates and explicit timezone confirmation',()=>{
  const html=ui.renderRetryRegistration(summary());assert.match(html,/for="retry-/);assert.match(html,/0\/200자/);
  for(const date of ['2026-10-02','2026-10-04','2026-10-08'])assert(html.includes(date));
  assert(!html.includes('maxlength='));assert.match(html,/name="timezone_confirmed"/);assert(!html.includes(' checked'));
 });
 await check('200 Unicode codepoints, trimming, newline and unsafe controls',()=>{
  assert.equal(ui.normalizeRetryReason(' 😀\n '),'😀');assert.equal(ui.normalizeRetryReason('a\nb'),'a\nb');
  assert.equal(ui.normalizeRetryReason('😀'.repeat(200)),'😀'.repeat(200));
  for(const reason of ['😀'.repeat(201),'e\u0301'.repeat(101),'   ','x\u0000','x\u0009y','\tx','x\r','x\u007f','x\u0085','\ud800'])assert.throws(()=>ui.normalizeRetryReason(reason),error=>error.code==='INVALID_INPUT');
 });
 await check('calendar preview handles UTC, Asia/Seoul, midnight, leap day and DST',()=>{
  const preview=(time,zone)=>plain(ui.previewRetryDates(Date.parse(time),zone)).map(x=>x.due_date);
  assert.deepEqual(preview('2026-10-01T23:59:00Z','Etc/UTC'),['2026-10-02','2026-10-04','2026-10-08']);
  assert.deepEqual(preview('2026-10-01T23:59:00Z','Asia/Seoul'),['2026-10-03','2026-10-05','2026-10-09']);
  assert.deepEqual(preview('2028-02-28T23:59:00Z','UTC'),['2028-02-29','2028-03-02','2028-03-06']);
  assert.deepEqual(preview('2026-03-07T17:00:00Z','America/New_York'),['2026-03-08','2026-03-10','2026-03-14']);
  assert.deepEqual(preview('2026-10-31T16:00:00Z','America/New_York'),['2026-11-01','2026-11-03','2026-11-07']);
  assert.deepEqual(plain(ui.previewRetryDates(null,'UTC')),[]);
 });
 await check('unknown or missing timezone is not silently replaced by UTC',()=>{
  for(const zone of ['',undefined,'Invalid/Zone','+01:00','Asia/Seoul\n'])assert.equal(ui.isRetryTimezone(zone),false);
  assert.equal(ui.isRetryTimezone('Etc/UTC'),true);assert.throws(()=>ui.buildRetryPlanPayload({...input(),timezone_confirmed:false}),error=>error.code==='INVALID_INPUT');
  assert.match(ui.renderRetryRegistration({...summary(),timezone:'Invalid/Zone'}),/<button type="submit" class="btn primary" disabled>/);
 });
 await check('plan/start payload allowlists; single item lock and 5..300 minutes',()=>{
  const clean=plain(ui.buildRetryPlanPayload({...input(),tags:['x'],code:'x'}));assert.deepEqual(clean,{source_sid:'source-1',pid:'pair',reason:'경계값을 놓쳤다',timezone:'Asia/Seoul'});
  const payload=plain(ui.buildRetryStartPayload({...card().defaults,item_id:'i1',duration:'40',source_sid:'bad',reason:'bad',codes:{pair:'bad'},problem_ids:['bad']}));
  assert.deepEqual(payload,{item_id:'i1',mode:'training',kind:'single',duration:40,resources:'none'});
  for(const duration of [4,301,40.5,true,'40x'])assert.throws(()=>ui.buildRetryStartPayload({...payload,duration}));
  assert.throws(()=>ui.buildRetryStartPayload({...payload,kind:'mock'}));assert.throws(()=>ui.buildRetryStartPayload({...payload,mode:'__proto__'}));
 });
 await check('CONTRACT-R2-003: expected anchor is retained and only real ASCII Gregorian dates pass',()=>{
  for(const date of ['0001-01-01','2000-02-29','2026-10-01','9999-12-31'])assert.equal(ui.buildRetryPlanPayload({...input(),expected_anchor_date:date}).expected_anchor_date,date);
  for(const value of [null,0,'',' 2026-10-01','2026-10-01 ','2026-1-01','0000-01-01','10000-01-01','2026-02-29','2026-04-31','２０２６-１０-０１'])assert.throws(()=>ui.buildRetryPlanPayload({...input(),expected_anchor_date:value}),e=>e.code==='INVALID_INPUT');
  assert(!Object.hasOwn(ui.buildRetryPlanPayload(input()),'expected_anchor_date'),'Legacy direct caller changed');
 });
 await check('CONTRACT-R2-003: registration stays disabled until capability is confirmed',()=>{
  assert.match(ui.renderRetryRegistration(summary()),/<button type="submit" class="btn primary" disabled>/);
 });
 await check('CONTRACT-R2-003: anchor error retains safe clock/preview fields without arbitrary server data',async()=>{
  const details={expected_anchor_date:'2026-10-01',current_anchor_date:'2026-10-02',timezone:'Asia/Seoul',server_now:Date.parse('2026-10-01T15:00:01Z'),preview:[1,3,7].map(offset_days=>({offset_days,due_date:'2026-10-'+String(2+offset_days).padStart(2,'0')}))};
  const api=ui.createRetryApi({fetchImpl:async()=>({ok:false,json:async()=>({code:'ANCHOR_DATE_CHANGED',error:'REPORT_SENTINEL',...details,reason:'REASON_SENTINEL_DP',source_sid:'SOURCE_SID_SENTINEL'})})});
  await assert.rejects(api.register({...input(),expected_anchor_date:'2026-10-01'}),e=>{
   assert.deepEqual(plain(e.details),details);blind(e.message+JSON.stringify(e.details));return e.code==='ANCHOR_DATE_CHANGED';
  });
 });
 await check('ready comparison keeps outcome, overtime, null minutes and conditions distinct',()=>{
  const html=ui.renderRetryComparison({...comparison(),source:attempt({minutes:null,outcome:'not_submitted'})});
  for(const text of ['채점 오류','미제출','통과','확인 불가','-1.2분','힌트','실력 상승','기록 복구','원본 복기 열기','등록한 실패 이유'])assert(html.includes(text));
  assert(!html.includes('0.0분'));assert(!html.includes('실패</td>'));
  assert(html.includes('전체 제출 접수 순서'));assert(!html.includes('submit_events'));
 });
 await check('duplicate registration preserves first saved reason and dates',()=>{
  const dto=plan();dto.created=false;dto.payload_mismatch=true;const html=ui.renderRetryRegistration(summary(),{saved:dto,reason:'SHOULD_NOT_REPLACE'});
  assert.match(html,/처음 저장한 이유와 시간대를 유지/);assert(!html.includes('SHOULD_NOT_REPLACE'));
 });
 await check('API methods, natural identity, encoded sid, optional model and effort',async()=>{
  const calls=[],responses=[empty(),plan(),{created:false,payload_mismatch:true,session:{id:'retry-1',status:'completed'}},comparison()];
  const api=ui.createRetryApi({fetchImpl:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>responses.shift()};}});
  await api.list();await api.register(input());await api.start({...card().defaults,item_id:'i1',model:'model-fixture',effort:'high'});await api.comparison('retry/1');
  assert.deepEqual(calls.map(c=>c.url),['/api/v2/retries','/api/v2/retry-plan','/api/v2/retry-start','/api/v2/retry-comparison/retry%2F1']);
  assert.deepEqual(calls.map(c=>c.options.method),['GET','POST','POST','GET']);
  assert(!JSON.parse(calls[1].options.body).timezone_confirmed);assert.equal(JSON.parse(calls[2].options.body).item_id,'i1');assert.equal(JSON.parse(calls[2].options.body).model,'model-fixture');
 });
 await check('API errors retain code and safe conflict details without leaking server text',async()=>{
  const api=ui.createRetryApi({fetchImpl:async()=>({ok:false,json:async()=>({code:'PLAN_EXISTS',plan_id:'existing',error:'REASON_SENTINEL_DP'})})});
  await assert.rejects(api.register(input()),e=>e.code==='PLAN_EXISTS'&&e.details.plan_id==='existing'&&!e.message.includes('SENTINEL'));
  const html=ui.renderRetryList(empty(),{error:{code:'PLAN_EXISTS',message:'REASON_SENTINEL_DP'}});blind(html);assert.match(html,/다시 조회/);
  assert.equal(ui.retryErrorMessage({code:'constructor'}),'요청을 처리하지 못했어요. 다시 시도해 주세요.');
 });
 await check('API invalid response, network failure and abort stay failures',async()=>{
  await assert.rejects(ui.createRetryApi({fetchImpl:async()=>({ok:true,json:async()=>null})}).list(),e=>e.code==='INVALID_RESPONSE');
  await assert.rejects(ui.createRetryApi({fetchImpl:async()=>({ok:true,json:async()=>({...empty(),due:[null]})})}).list(),e=>e.code==='INVALID_RESPONSE');
  const malformed=plan();malformed.plan.items[1].offset_days=1;
  await assert.rejects(ui.createRetryApi({fetchImpl:async()=>({ok:true,json:async()=>malformed})}).register(input()),e=>e.code==='INVALID_RESPONSE');
  await assert.rejects(ui.createRetryApi({fetchImpl:async()=>({ok:true,json:async()=>{throw Error('bad json');}})}).list(),e=>e.code==='INVALID_RESPONSE');
  await assert.rejects(ui.createRetryApi({fetchImpl:async()=>{throw Error('offline');}}).list(),e=>e.code==='NETWORK_ERROR');
  const abort=Object.assign(Error('stop'),{name:'AbortError'});await assert.rejects(ui.createRetryApi({fetchImpl:async()=>{throw abort;}}).list(),e=>e===abort);
 });
 await check('host api injection preserves code and response validation',async()=>{
  let request;const api=ui.createRetryApi({api:async(...args)=>{request=args;return empty();}});await api.list();assert.equal(request[0],'/api/v2/retries');
  await assert.rejects(ui.createRetryApi({api:async()=>{throw {code:'RETRY_ACTIVE',message:'PRIVATE'};}}).comparison('s1'),e=>e.code==='RETRY_ACTIVE');
 });
 await check('explicit home refresh uses server interval, focus/online and visibility cleanup',async()=>{
  const target=new Target(),visibility=new Target(),timers=new Map(),states=[];let calls=0,nextId=0;
  const control=ui.createRetryListController({api:{list:async()=>{calls++;return empty();}},onChange:s=>states.push(s),focusTarget:target,visibilityTarget:visibility,setTimer:(fn,delay)=>{const id=++nextId;timers.set(id,{fn:()=>{timers.delete(id);fn();},delay});return id;},clearTimer:id=>timers.delete(id)});
  assert.equal(calls,0);await control.activate();assert.equal(calls,1);assert.equal([...timers.values()][0].delay,43200000);
  target.fire('focus');await tick();assert.equal(calls,2);target.fire('online');await tick();assert.equal(calls,3);
  visibility.hidden=true;visibility.fire('visibilitychange');assert.equal(timers.size,0);target.fire('focus');await tick();assert.equal(calls,3);
  visibility.hidden=false;visibility.fire('visibilitychange');await tick();assert.equal(calls,4);
  [...timers.values()][0].fn();await tick();assert.equal(calls,5);control.dispose();assert.equal(timers.size,0);
  target.fire('focus');target.fire('online');await tick();assert.equal(calls,5);assert.equal(states.filter(s=>!s.loading).length,5);
 });
 await check('refresh deduplicates clicks, ignores late data and renders errors with retry',async()=>{
  let resolve,calls=0;const changes=[];const control=ui.createRetryListController({api:{list:()=>{calls++;return new Promise(r=>resolve=r);}},onChange:s=>changes.push(s)});
  const first=control.activate();const same=control.refresh();assert.equal(first,same);await tick();assert.equal(calls,1);control.deactivate();resolve(empty());await first;assert.equal(changes.length,1);
  const errors=[];const failed=ui.createRetryListController({api:{list(){throw new ui.RetryApiError('NETWORK_ERROR');}},onChange:s=>errors.push(s)});await failed.activate();assert.equal(errors.at(-1).error.code,'NETWORK_ERROR');failed.dispose();control.dispose();
 });
 await check('controller first failure renders an error, then retry replaces it with a due card',async()=>{
  let calls=0,html='';const control=ui.createRetryListController({api:{list:async()=>{if(++calls===1)throw new ui.RetryApiError('INVALID_INPUT');return {...empty(),due:[card()]};}},onChange:({dto,loading,error})=>html=ui.renderRetryList(dto,{loading,error})});
  await control.activate();assert(!html.includes('불러오는 중'));assert(html.includes('입력 내용을 확인해 주세요.'));assert(html.includes('data-retry-action="refresh"'));
  await control.refresh();assert.equal(calls,2);assert(html.includes('data-retry-state="due"'));assert(!html.includes('data-retry-action="refresh"'));control.dispose();
 });

 let browser;
 try{
  const {chromium}=require('playwright');browser=await chromium.launch({headless:true});
 }catch(error){
  records.push({name:'isolated Chromium keyboard/DOM/visual checks',result:'SKIP',error:String(error.message)});
  console.log('SKIP isolated Chromium: '+String(error.message).split('\n')[0]);
 }
 if(browser){
  const context=await browser.newContext({viewport:{width:1280,height:900}});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  try{
   await page.setContent('<!doctype html><html lang="ko"><head><title>P2 retry fixture</title></head><body style="padding:24px"><main id="fixture"></main></body></html>');
   await page.addStyleTag({content:fs.readFileSync(path.join(root,'client/style.css'),'utf8')+'\n'+fs.readFileSync(path.join(root,'client/retries.css'),'utf8')});
   const client=buildSync({entryPoints:[source],bundle:true,write:false,platform:'browser',format:'iife',globalName:'RetryUI',loader:{'.css':'empty'}}).outputFiles[0].text;
   await page.addScriptTag({content:client});
   const mount=async(config={})=>page.evaluate(({summary,config,plan})=>{
    window.retryBinding?.dispose();window.retryCalls=[];window.savedCallbacks=0;
    const root=document.querySelector('#fixture');root.innerHTML=RetryUI.renderRetryRegistration(summary,config);
    window.retryBinding=RetryUI.bindRetryRegistration(root,summary,{api:{list:async()=>({server_now:summary.server_now,capabilities:{retry_plan_expected_anchor_date:true}}),register:async input=>{window.retryCalls.push(input);if(window.retryReject)throw new RetryUI.RetryApiError(window.retryReject);if(window.retryHold)return new Promise(resolve=>window.retryResolve=()=>resolve(plan));return plan;}},onSaved:()=>{window.savedCallbacks++;}});
   },{summary:summary(),config,plan:plan()});
   await check('browser: all labels resolve, astral 200-char keyboard submit and success focus',async()=>{
    await mount();await page.locator('[name="reason"]').fill('😀'.repeat(200));assert.equal(await page.locator('[data-retry-counter]').textContent(),'200/200자');
    const labels=await page.locator('label[for]').evaluateAll(labels=>labels.every(l=>!!document.getElementById(l.htmlFor)));assert(labels);
    await page.locator('[name="timezone_confirmed"]').focus();await page.keyboard.press('Space');await page.keyboard.press('Tab');await page.keyboard.press('Enter');
    await page.locator('[data-retry-view="registration"] [role="status"]').filter({hasText:'저장했어요'}).waitFor();
    await page.waitForFunction(()=>window.savedCallbacks===1);assert.equal(await page.evaluate(()=>window.retryCalls.length),1);
    assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('role')),'status');
   });
   await check('browser: 201 codepoints, whitespace and controls render errors without request',async()=>{
    for(const reason of ['😀'.repeat(201),'   ','x\u0000']){
     await mount({timezone_confirmed:true});await page.locator('[name="reason"]').fill(reason);await page.locator('[type="submit"]').click();
     assert.equal(await page.evaluate(()=>window.retryCalls.length),0);assert(await page.locator('[data-retry-registration-status]').textContent());assert.equal(await page.evaluate(()=>document.activeElement.name),'reason');
    }
   });
   await check('browser: timezone edit resets confirmation and previews calendar dates',async()=>{
    await mount({timezone_confirmed:true});await page.locator('[name="reason"]').fill('경계값을 놓쳤다');await page.locator('[name="timezone"]').fill('Etc/UTC');
    assert.equal(await page.locator('[name="timezone_confirmed"]').isChecked(),false);assert.match(await page.locator('[data-retry-date-preview]').textContent(),/2026-10-02/);
    await page.locator('[type="submit"]').click();assert.equal(await page.evaluate(()=>window.retryCalls.length),0);assert.equal(await page.evaluate(()=>document.activeElement.name),'timezone_confirmed');
    await page.locator('[name="timezone"]').fill('Bad/Timezone');assert(await page.locator('[type="submit"]').isDisabled());
   });
   await check('browser: pending blocks double submit and failure keeps input with focus return',async()=>{
    await page.evaluate(()=>{window.retryHold=true;window.retryReject='';});await mount({timezone_confirmed:true});await page.locator('[name="reason"]').fill('다시 시도할 이유');
    await page.locator('[type="submit"]').click();assert(await page.locator('[type="submit"]').isDisabled());assert(await page.locator('[name="reason"]').isDisabled());
    await page.locator('form').evaluate(form=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));assert.equal(await page.evaluate(()=>window.retryCalls.length),1);
    await page.evaluate(()=>window.retryResolve());await page.waitForFunction(()=>window.savedCallbacks===1);
    await page.evaluate(()=>{window.retryHold=false;window.retryReject='NETWORK_ERROR';});await mount({timezone_confirmed:true});await page.locator('[name="reason"]').fill('입력 유지');await page.locator('[type="submit"]').click();
    assert.equal(await page.locator('[name="reason"]').inputValue(),'입력 유지');assert.match(await page.locator('[data-retry-registration-status]').textContent(),/다시 시도/);assert.equal(await page.evaluate(()=>document.activeElement.type),'submit');
    await page.evaluate(()=>window.retryReject='');await page.keyboard.press('Enter');await page.waitForFunction(()=>window.savedCallbacks===1);assert.equal(await page.evaluate(()=>window.retryCalls.length),2);
   });
   await check('browser: conflict message does not claim saved and disposed response has no callback',async()=>{
    await page.evaluate(()=>window.retryReject='PLAN_EXISTS');await mount({timezone_confirmed:true});await page.locator('[name="reason"]').fill('동일 문제');await page.locator('[type="submit"]').click();
    assert.match(await page.locator('[data-retry-registration-status]').textContent(),/진행 중인 재도전 예약/);assert.equal(await page.evaluate(()=>window.savedCallbacks),0);
    await page.evaluate(()=>{window.retryReject='';window.retryHold=true;});await mount({timezone_confirmed:true});await page.locator('[name="reason"]').fill('늦은 응답');await page.locator('[type="submit"]').click();
    await page.evaluate(()=>{window.retryBinding.dispose();document.querySelector('#fixture').textContent='다른 화면';window.retryResolve();});await tick();
    assert.equal(await page.locator('#fixture').textContent(),'다른 화면');assert.equal(await page.evaluate(()=>window.savedCallbacks),0);await page.evaluate(()=>window.retryHold=false);
   });
   await check('browser: server clock schedules local DST midnight and cleans up on dispose',async()=>{
    const delays=await page.evaluate(async summary=>{
     window.retryBinding?.dispose();const result=[];
     for(const now of ['2026-03-08T05:00:00Z','2026-11-01T04:00:00Z']){
      const root=document.querySelector('#fixture'),s={...summary,server_now:Date.parse(now),timezone:'America/New_York'};
      root.innerHTML=RetryUI.renderRetryRegistration(s);let timer=null,clock=0,calls=0;
      const focus=new EventTarget(),visibility=new EventTarget();visibility.hidden=false;
      const binding=RetryUI.bindRetryRegistration(root,s,{api:{list:async()=>{calls++;return {server_now:s.server_now+clock};}},focusTarget:focus,visibilityTarget:visibility,
       monotonicNow:()=>clock,setTimer:(fn,delay)=>{timer={fn,delay};return 1;},clearTimer:()=>timer=null});
      result.push(timer.delay);
      root.querySelector('[name="reason"]').value='시간 유지';root.querySelector('[name="timezone_confirmed"]').checked=true;
      const delay=timer.delay;clock+=delay;timer.fn();
      if(root.querySelector('[name="timezone_confirmed"]').checked)throw Error('Midnight did not invalidate confirmation');
      if(root.querySelector('[name="reason"]').value!=='시간 유지')throw Error('Midnight erased reason');
      binding.dispose();if(timer!==null)throw Error('Disposed timer remains');focus.dispatchEvent(new Event('focus'));await Promise.resolve();await Promise.resolve();
      result.push(calls);
     }
     return result;
    },summary());
    assert.deepEqual(delays,[23*3600000,0,25*3600000,0]);
   });
   await check('browser: blind list, collapsed upcoming and keyboard start/resume actions',async()=>{
    const dto=empty();for(const state of ['due','active','grading','unavailable','upcoming'])dto[state]=[{...card(state),...sentinels,session_id:'retry-1'}];
    await page.evaluate(dto=>document.querySelector('#fixture').innerHTML=RetryUI.renderRetryList(dto),dto);blind(await page.locator('#fixture').innerHTML());
    assert.equal(await page.locator('details').evaluate(el=>el.open),false);
    await page.evaluate(()=>{window.actions=[];document.querySelector('#fixture').onclick=e=>{const button=e.target.closest('[data-retry-action]');if(button)window.actions.push(button.dataset.retryAction);};});
    await page.locator('[data-retry-action="prepare"]').focus();await page.keyboard.press('Enter');await page.locator('[data-retry-action="resume"]').focus();await page.keyboard.press('Space');assert.deepEqual(await page.evaluate(()=>window.actions),['prepare','resume']);
    await page.locator('summary').focus();await page.keyboard.press('Enter');assert.equal(await page.locator('details').evaluate(el=>el.open),true);
    await page.screenshot({path:path.join(evidence,'retry-list-dark.png'),fullPage:true});
   });
   await check('browser: comparison ready and grading concealment plus mobile/light visual checks',async()=>{
    await page.evaluate(dto=>document.querySelector('#fixture').innerHTML=RetryUI.renderRetryComparison(dto),comparison());
    assert.equal(await page.locator('th[scope="row"]').count(),9);assert.equal(await page.locator('[data-retry-action="source-review"]').count(),1);
    await page.screenshot({path:path.join(evidence,'retry-comparison-dark.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});await page.evaluate(()=>document.documentElement.dataset.theme='light');await mount();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
    await page.screenshot({path:path.join(evidence,'retry-registration-mobile-light.png'),fullPage:true});
    await page.evaluate(dto=>document.querySelector('#fixture').innerHTML=RetryUI.renderRetryComparison(dto),{...comparison(),state:'grading',...sentinels});blind(await page.locator('#fixture').innerHTML());assert.equal(await page.locator('[data-retry-action="source-review"]').count(),0);
   });
   await check('browser: zero page/console errors',()=>assert.deepEqual(errors,[]));
  }finally{await context.close();await browser.close();}
 }
 const counts={passed:records.filter(r=>r.result==='PASS').length,failed:records.filter(r=>r.result==='FAIL').length,skipped:records.filter(r=>r.result==='SKIP').length};
 fs.writeFileSync(path.join(evidence,'results.json'),JSON.stringify({contract:'retry-v1',at_utc:new Date().toISOString(),counts,records},null,2)+'\n');
 console.log(`RESULT retries UI: passed=${counts.passed} failed=${counts.failed} skipped=${counts.skipped}; evidence=${evidence}`);
 if(counts.failed)process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;});
