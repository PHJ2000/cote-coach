import './retries.css';
import {esc,date} from './ui.js';

// retry-v1. Rendering is pure; the host owns navigation, capture and workspace state.
export const RETRY_REASON_LIMIT=200;
const offsets=[1,3,7];
const control=/[\u0000-\u0009\u000b-\u001f\u007f-\u009f]/u;
const modes={training:'훈련',exam:'실전'};
const resources={none:'자료 금지','java-docs':'Java 공식 문서',search:'자유 검색'};
const outcomes={passed:'통과',failed:'실패',grader_error:'채점 오류 · 결과 확인 불가',ungraded:'채점 미완료',not_submitted:'미제출'};
const resultBases={submit_events:'전체 제출 접수 순서',events:'전체 제출 접수 순서',event_order:'전체 제출 접수 순서',legacy_runs:'이전 기록의 실행 시각과 저장 순서',runs:'이전 기록의 실행 시각과 저장 순서',legacy_fallback:'이전 기록의 실행 시각과 저장 순서'};
const messages={
 ANCHOR_DATE_CHANGED:'기준 날짜가 바뀌어 예약하지 않았습니다. 새 예약 날짜를 확인해 주세요.',
 CAPABILITY_UNAVAILABLE:'서버의 날짜 확인 기능을 확인할 수 없어 예약하지 않았습니다. 새로고침 후 다시 시도해 주세요.',
 INVALID_INPUT:'입력 내용을 확인해 주세요.',INVALID_RESPONSE:'서버 응답을 확인할 수 없어요. 다시 조회해 주세요.',
 NETWORK_ERROR:'서버에 연결하지 못했어요. 입력은 유지했으니 다시 시도해 주세요.',
 PLAN_EXISTS:'이 문제에 진행 중인 재도전 예약이 있어요. 오늘 다시 풀 문제 목록을 확인해 주세요.',
 SOURCE_ACTIVE:'연습을 종료한 뒤 예약할 수 있어요.',GRADING_PENDING:'채점 중이에요. 채점이 끝나면 다시 확인해 주세요.',
 NOT_ELIGIBLE:'시간 내 통과한 문제는 다시 풀기 예약 대상이 아니에요.',
 GRADE_UNAVAILABLE:'채점 오류 또는 판정 불명으로 예약할 수 없어요.',
 NOT_DUE:'아직 예정일이 아니에요. 목록을 다시 조회해 주세요.',
 PREVIOUS_PENDING:'앞선 재도전 회차를 먼저 마쳐 주세요.',
 ACTIVE_SESSION_EXISTS:'진행 중인 연습을 먼저 마쳐 주세요.',
 SOURCE_UNAVAILABLE:'원본 문제를 확인할 수 없어 시작할 수 없어요.',
 RETRY_ACTIVE:'재도전을 종료한 뒤 이전 시도와 비교할 수 있어요.',
 NOT_RETRY:'재도전 연습에서만 비교를 확인할 수 있어요.'
};

export class RetryApiError extends Error{
 constructor(code='REQUEST_FAILED',message='',details={}){
  super(message||(Object.hasOwn(messages,code)?messages[code]:'요청을 처리하지 못했어요. 다시 시도해 주세요.'));
  this.name='RetryApiError';this.code=code;this.details=details;
 }
}
export function retryErrorMessage(error){return Object.hasOwn(messages,error?.code)?messages[error.code]:'요청을 처리하지 못했어요. 다시 시도해 주세요.';}
const fail=message=>{throw new RetryApiError('INVALID_INPUT',message);};
const count=value=>Array.from(String(value??'').trim()).length;
const integer=value=>typeof value==='number'&&Number.isInteger(value)&&value>=0;
const finite=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0;
function validText(value){return typeof value==='string'&&!control.test(value)&&!Array.from(value).some(c=>{const n=c.codePointAt(0);return n>=0xd800&&n<=0xdfff;});}
function identifier(value,label){
 if(!validText(value)||/[\r\n]/.test(value)||!value.trim()||value!==value.trim()||count(value)>200)fail(`${label}을 확인해 주세요.`);
 return value;
}
export function normalizeRetryReason(value){
 if(typeof value!=='string')fail('실패 이유를 입력해 주세요.');
 if(!validText(value))fail('실패 이유에서 제어문자를 지워 주세요.');
 const reason=value.trim();
 if(!validText(reason))fail('실패 이유에서 제어문자를 지워 주세요.');
 if(!reason||count(reason)>RETRY_REASON_LIMIT)fail('실패 이유를 1~200자로 입력해 주세요.');
 return reason;
}
export function isRetryTimezone(value){
 if(!validText(value)||!value.trim()||value!==value.trim()||value.length>100||!/^\w[\w.+-]*(?:\/[\w.+-]+)*$/.test(value))return false;
 try{new Intl.DateTimeFormat('en',{timeZone:value}).format(0);return true;}catch{return false;}
}
export function getRetryTimezone(){
 try{const value=Intl.DateTimeFormat().resolvedOptions().timeZone;return isRetryTimezone(value)?value:'';}catch{return '';}
}
export function previewRetryDates(serverNow,timezone){
 if(!finite(serverNow)||!isRetryTimezone(timezone))return [];
 try{
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(serverNow).map(p=>[p.type,p.value]));
  const day=new Date(0);day.setUTCFullYear(Number(parts.year),Number(parts.month)-1,Number(parts.day));day.setUTCHours(0,0,0,0);
  return offsets.map(offset_days=>{
   const next=new Date(day);next.setUTCDate(next.getUTCDate()+offset_days);
   return {offset_days,due_date:next.toISOString().slice(0,10)};
  });
 }catch{return [];}
}
export function isRetryAnchorDate(value){
 if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))return false;
 const [year,month,day]=value.split('-').map(Number);if(year<1||year>9999)return false;
 const d=new Date(0);d.setUTCFullYear(year,month-1,day);d.setUTCHours(0,0,0,0);
 return d.getUTCFullYear()===year&&d.getUTCMonth()===month-1&&d.getUTCDate()===day;
}
function anchorFromDates(dates){
 const next=dates[0]?.due_date;if(!isRetryAnchorDate(next))return '';
 const day=new Date(next+'T00:00:00Z');day.setUTCDate(day.getUTCDate()-1);return day.toISOString().slice(0,10);
}
function anchorErrorPreview(error,input){
 const d=error.details||{},dates=previewRetryDates(d.server_now,input.timezone);
 if(d.expected_anchor_date!==input.expected_anchor_date||d.timezone!==input.timezone||!isRetryAnchorDate(d.current_anchor_date)||d.current_anchor_date!==anchorFromDates(dates)||dates.length!==3||!Array.isArray(d.preview)||d.preview.length!==3)return null;
 if(!dates.every(expected=>d.preview.filter(p=>p?.offset_days===expected.offset_days&&p?.due_date===expected.due_date).length===1))return null;
 return {server_now:d.server_now,preview:dates};
}
export function buildRetryPlanPayload(input={}){
 const source_sid=identifier(input.source_sid,'원본 연습'),pid=identifier(input.pid,'문제');
 const reason=normalizeRetryReason(input.reason);
 if(!isRetryTimezone(input.timezone))fail('유효한 IANA 시간대를 입력해 주세요.');
 if(input.timezone_confirmed!==true)fail('일정을 계산할 시간대를 확인해 주세요.');
 const payload={source_sid,pid,reason,timezone:input.timezone};
 if(Object.hasOwn(input,'expected_anchor_date')){
  if(!isRetryAnchorDate(input.expected_anchor_date))fail('확인한 기준 날짜를 YYYY-MM-DD 형식으로 보내 주세요.');
  payload.expected_anchor_date=input.expected_anchor_date;
 }
 return payload;
}
export function buildRetryStartPayload(input={}){
 const item_id=identifier(input.item_id,'재도전 회차');
 if(!Object.hasOwn(modes,input.mode)||!Object.hasOwn(resources,input.resources))fail('연습 조건을 확인해 주세요.');
 if(input.kind!==undefined&&input.kind!=='single')fail('재도전은 한 문제씩 시작해 주세요.');
 const duration=typeof input.duration==='string'&&/^\d+$/.test(input.duration)?Number(input.duration):input.duration;
 if(!integer(duration)||duration<5||duration>300)fail('제한 시간은 5~300분으로 입력해 주세요.');
 const payload={item_id,mode:input.mode,kind:'single',duration,resources:input.resources};
 for(const [key,limit] of [['model',100],['effort',30]])if(input[key]!==undefined){
  if(!validText(input[key])||count(input[key])>limit)fail('모델과 추론 수준을 확인해 주세요.');
  payload[key]=input[key];
 }
 return payload;
}

// A transport can use the host's api(url,data,options). It must retain error.code.
export function createRetryApi({api,fetchImpl=globalThis.fetch}={}){
 async function request(url,data,options={}){
  let body;
  try{
   if(api)body=await api(url,data,{signal:options.signal});
   else{
    const response=await fetchImpl(url,{method:data===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',signal:options.signal,
     ...(data===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})});
    try{body=await response.json();}catch{throw new RetryApiError('INVALID_RESPONSE');}
    if(!response.ok){
     const code=typeof body?.code==='string'?body.code:'REQUEST_FAILED';let details;
     if(code==='ANCHOR_DATE_CHANGED')details={
      ...(isRetryAnchorDate(body.expected_anchor_date)?{expected_anchor_date:body.expected_anchor_date}:{}),
      ...(isRetryAnchorDate(body.current_anchor_date)?{current_anchor_date:body.current_anchor_date}:{}),
      ...(isRetryTimezone(body.timezone)?{timezone:body.timezone}:{}),
      ...(finite(body.server_now)?{server_now:body.server_now}:{}),
      ...(Array.isArray(body.preview)?{preview:body.preview.map(p=>({offset_days:p?.offset_days,due_date:p?.due_date}))}:{})
     };else details={...(typeof body?.plan_id==='string'?{plan_id:body.plan_id}:{}),...(typeof body?.session_id==='string'?{session_id:body.session_id}:{})};
     throw new RetryApiError(code,'',details);
    }
   }
  }catch(error){
   if(error?.name==='AbortError'||error instanceof RetryApiError)throw error;
   if(typeof error?.code==='string')throw new RetryApiError(error.code,'',error.details||{});
   throw new RetryApiError('NETWORK_ERROR');
  }
  if(!body||typeof body!=='object'||Array.isArray(body))throw new RetryApiError('INVALID_RESPONSE');
  return body;
 }
 const invalid=()=>{throw new RetryApiError('INVALID_RESPONSE');};
 return {
  async list(options){
   const dto=await request('/api/v2/retries',undefined,options);
   if(!finite(dto.server_now)||!(dto.next_refresh_at===null||finite(dto.next_refresh_at))||!['due','active','grading','unavailable','upcoming'].every(k=>Array.isArray(dto[k])&&dto[k].every(c=>c&&typeof c==='object'&&!Array.isArray(c))))invalid();
   return dto;
  },
  async register(input,options){
   const dto=await request('/api/v2/retry-plan',buildRetryPlanPayload(input),options);
   const plan=dto.plan;
   if(typeof dto.created!=='boolean'||typeof plan?.id!=='string'||!plan.id||!validText(plan.reason)||!count(plan.reason)||count(plan.reason)>200||!isRetryTimezone(plan.timezone)||!Array.isArray(plan.items)||plan.items.length!==3||new Set(plan.items.map(i=>i?.offset_days)).size!==3||!plan.items.every(i=>i&&typeof i.id==='string'&&i.id&&offsets.includes(i.offset_days)&&/^\d{4}-\d{2}-\d{2}$/.test(i.due_date)))invalid();
   return dto;
  },
  async start(input,options){
   const dto=await request('/api/v2/retry-start',buildRetryStartPayload(input),options);
   if(typeof dto.created!=='boolean'||typeof dto.session?.id!=='string'||!['active','completed'].includes(dto.session.status))invalid();
   return dto;
  },
  async comparison(sid,options){
   const dto=await request('/api/v2/retry-comparison/'+encodeURIComponent(identifier(sid,'연습')),undefined,options);
   if(!['ready','grading','unavailable'].includes(dto.state))invalid();
   if(dto.state==='ready'&&(!dto.source||!dto.current||typeof dto.source_sid!=='string'||typeof dto.current_sid!=='string'))invalid();
   return dto;
  }
 };
}

const message=(text,extra='')=>`<p class="retry-status" role="status" aria-live="polite">${esc(text)}</p>${extra}`;
const reload=()=>'<button type="button" class="btn sm" data-retry-action="refresh">다시 조회</button>';
function renderCard(card={},state,pendingItem){
 if(!card||typeof card!=='object')card={};
 const labels={due:'다시 풀기',active:'이어 하기',grading:'채점 중',upcoming:'예정',unavailable:'시작 불가'};
 const title=state==='unavailable'?'확인할 수 없는 예약':String(card.title??'재도전 문제');
 const due=String(card.due_date??'확인 불가'),zone=String(card.timezone??'확인 불가');
 const late=integer(card.overdue_days)&&card.overdue_days>0?` · ${card.overdue_days}일 늦음`:'';
 const outstanding=integer(card.outstanding_due_count)&&card.outstanding_due_count>1?`<p class="retry-note">오늘 기준 미완료 ${card.outstanding_due_count}회차 · 앞선 회차부터 순서대로</p>`:'';
 const offset=offsets.includes(card.offset_days)?`등록일 +${card.offset_days}일 회차`:'회차 확인 불가';
 const busy=pendingItem===card.item_id;
 let action;
 if(state==='active'&&typeof card.session_id==='string'&&card.session_id){
  action=`<button type="button" class="btn sm" data-retry-action="resume" data-retry-session="${esc(card.session_id)}">이어 하기</button>`;
 }else if(state==='due'){
  const available=card.can_start===true&&typeof card.item_id==='string'&&!!card.item_id;
  action=`<button type="button" class="btn primary sm" data-retry-action="prepare" data-retry-item="${esc(card.item_id)}"${!available||busy?' disabled':''}${busy?' aria-busy="true"':''}>${busy?'시작 중…':'다시 풀기'}</button>`;
  if(!available)action+='<span class="retry-note">진행 중인 연습과 앞선 회차를 먼저 마쳐 주세요.</span>';
 }else action=`<span class="retry-state retry-state-${state}"${state==='grading'?' role="status"':''}>${labels[state]}</span>`;
 // Deliberate allowlist: reason, tags, source_sid, code, reports and metadata never enter this DOM.
 return `<li class="retry-card" data-retry-state="${state}"><div class="retry-card-main"><div class="retry-card-title"><h3>${esc(title)}</h3>${state!=='unavailable'&&card.level!==undefined?`<span class="tag">Lv.${esc(card.level)}</span>`:''}</div><p class="retry-note">${esc(offset)} · 예정일 ${esc(due)}${late}</p><p class="retry-note">기준 시간대 ${esc(zone)}${typeof card.today==='string'?` · 서버 기준 오늘 ${esc(card.today)}`:''}</p>${outstanding}${state==='unavailable'?message(Object.hasOwn(messages,card.code)?messages[card.code]:messages.SOURCE_UNAVAILABLE):''}</div><div class="retry-card-actions">${action}</div></li>`;
}
export function renderRetryList(dto={},options={}){
 const heading='<header class="retry-heading"><div><h2>오늘 다시 풀 문제</h2><p class="retry-note">등록일의 1·3·7일 뒤, 날짜 기준으로 다시 풀어요.</p></div></header>';
 const wrap=body=>`<section class="retry-panel" data-retry-view="list" aria-label="오늘 다시 풀 문제">${heading}${body}</section>`;
 if(options.error)return wrap(message(retryErrorMessage(options.error),reload()));
 if(options.loading||dto===null)return wrap(message('재도전 목록을 불러오는 중이에요.'));
 const groups=['active','grading','unavailable','due'];
 const visible=groups.flatMap(state=>(Array.isArray(dto[state])?dto[state]:[]).map(card=>renderCard(card,state,options.pending_item_id)));
 const upcoming=Array.isArray(dto.upcoming)?dto.upcoming:[];
 return wrap(`${visible.length?`<ul class="retry-cards">${visible.join('')}</ul>`:message('오늘 다시 풀 문제가 없어요.')}${upcoming.length?`<details class="retry-upcoming"><summary>예정된 재도전 ${upcoming.length}개</summary><ul class="retry-cards">${upcoming.map(card=>renderCard(card,'upcoming')).join('')}</ul></details>`:''}`);
}

function datesHTML(items){return `<ul class="retry-dates">${items.map(item=>`<li><strong>${item.offset_days===1?'내일':`${esc(item.offset_days)}일 뒤`}</strong><span>${esc(item.due_date)}</span></li>`).join('')}</ul>`;}
function registrationAllowed(summary){return summary.status==='completed'&&summary.eligible===true&&!summary.existing_plan_id;}
export function renderRetryRegistration(sourceSummary={},options={}){
 const s=sourceSummary||{};
 const heading=`<h3>다시 풀기 예약${s.title?` · ${esc(s.title)}`:''}</h3>`;
 const wrap=body=>`<section class="retry-panel retry-registration" data-retry-view="registration">${heading}${body}</section>`;
 // Do not leak even an accidentally supplied reason into an active view.
 if(s.status!=='completed')return wrap(message(messages.SOURCE_ACTIVE));
 if(options.saved?.plan){
  const saved=options.saved,plan=saved.plan;
  const text=saved.payload_mismatch?'이미 저장된 예약이 있어요. 처음 저장한 이유와 시간대를 유지했어요.':saved.created?'1·3·7일 뒤 예약을 저장했어요.':'이미 저장된 예약이에요.';
  return wrap(message(text)+`<p class="retry-reason">저장한 이유: ${esc(plan.reason)}</p><p class="retry-note">기준 시간대 ${esc(plan.timezone)}</p>${datesHTML(Array.isArray(plan.items)?plan.items:[])}`);
 }
 if(!registrationAllowed(s))return wrap(message(s.existing_plan_id?messages.PLAN_EXISTS:retryErrorMessage({code:s.code})));
 const timezone=options.timezone??s.timezone??getRetryTimezone();
 const reason=options.reason??s.reason??'';
 const dates=previewRetryDates(s.server_now??s.now,timezone);
 const prefix='retry-'+Array.from(String(s.source_sid)+'-'+String(s.pid)).map(c=>c.codePointAt(0).toString(16)).join('-');
 const busy=options.pending===true;
 return wrap(`<form data-retry-registration novalidate aria-busy="${busy}"><div class="retry-field"><label for="${prefix}-reason">실패 이유 <span class="retry-note">1~200자</span></label><textarea id="${prefix}-reason" name="reason" rows="3" required aria-describedby="${prefix}-counter ${prefix}-status"${busy?' disabled':''}>${esc(reason)}</textarea><span class="retry-counter" id="${prefix}-counter" data-retry-counter aria-live="polite">${count(reason)}/200자</span></div><div class="retry-field"><label for="${prefix}-timezone">일정 기준 시간대</label><input id="${prefix}-timezone" name="timezone" type="text" value="${esc(timezone)}" placeholder="Asia/Seoul" autocomplete="off" required aria-describedby="${prefix}-timezone-help"${busy?' disabled':''}><p class="retry-note" id="${prefix}-timezone-help">등록할 때의 현지 날짜를 기준으로 일정을 고정해요.</p><label class="retry-confirm"><input type="checkbox" name="timezone_confirmed"${options.timezone_confirmed===true?' checked':''}${busy?' disabled':''} required> 이 시간대로 세 날짜를 확인했어요.</label></div><div data-retry-date-preview>${dates.length?datesHTML(dates):message('시간대와 서버 시각을 확인하면 예정일을 표시해요.')}</div><p class="retry-note">날짜 기준 예약이에요. 등록 시각이 자정에 가까우면 내일 일정이 곧 표시될 수 있어요. 통과해도 남은 일정은 유지하고, 밀린 회차는 순서대로 같은 날 풀 수 있어요.</p><p class="retry-status" role="status" aria-live="polite" id="${prefix}-status" data-retry-registration-status>${options.error?esc(retryErrorMessage(options.error)):''}</p><button type="submit" class="btn primary" disabled>${busy?'저장 중…':'1·3·7일 뒤 예약'}</button> <button type="button" class="btn sm" data-retry-clock-refresh${busy?' disabled':''}>서버 날짜 다시 조회</button></form>`);
}

// Explicit, local installation; dispose on host navigation or rerender.
export function bindRetryRegistration(root,sourceSummary,{api=createRetryApi(),onSaved,focusTarget=globalThis.window,visibilityTarget=globalThis.document,
 monotonicNow=()=>globalThis.performance?.now?.()??0,setTimer=globalThis.setTimeout,clearTimer=globalThis.clearTimeout}={}){
 const form=root.querySelector('[data-retry-registration]');
 if(!form||!registrationAllowed(sourceSummary))return {update(){},dispose(){}};
 const reason=form.elements.namedItem('reason'),timezone=form.elements.namedItem('timezone');
 const confirmed=form.elements.namedItem('timezone_confirmed'),button=form.querySelector('[type="submit"]');
 const clockButton=form.querySelector('[data-retry-clock-refresh]');
 const status=form.querySelector('[data-retry-registration-status]'),counter=form.querySelector('[data-retry-counter]');
 let pending=false,disposed=false,saved=null,timer=null,clockFlight=null,clockAbort=null,capability=false,clockReady=false,uncertainInput=null;
 let serverNow=sourceSummary.server_now??sourceSummary.now,observedAt=monotonicNow();
 let shownDates=JSON.stringify(previewRetryDates(serverNow,timezone.value));
 const tuple=()=>({timezone:timezone.value,anchor_date:anchorFromDates(JSON.parse(shownDates))});
 let confirmedTuple=confirmed.checked?tuple():null;
 const visible=()=>!visibilityTarget?.hidden;
 const estimatedNow=()=>finite(serverNow)?serverNow+Math.max(0,monotonicNow()-observedAt):serverNow;
 const cancelTimer=()=>{if(timer!==null)clearTimer(timer);timer=null;};
 function paintButton(){
  button.disabled=pending||!capability||!clockReady||JSON.parse(shownDates).length!==3;
  button.textContent=pending?'저장 중…':uncertainInput?'저장 결과 다시 확인':'1·3·7일 뒤 예약';
  if(clockButton)clockButton.disabled=pending;
 }
 function schedule(){
  cancelTimer();if(disposed||saved||pending||!visible())return;
  const now=estimatedNow(),dates=previewRetryDates(now,timezone.value);
  if(dates.length!==3)return;
  let low=0,high=48*3600000;
  while(high-low>1){const mid=Math.floor((low+high)/2);if(previewRetryDates(now+mid,timezone.value)[0]?.due_date===dates[0].due_date)low=mid;else high=mid;}
  timer=setTimer(()=>{timer=null;update();void refreshClock().catch(showClockError);},high);
 }
 function sameUncertainInput(){
  if(!uncertainInput)return false;
  try{return uncertainInput.source_sid===sourceSummary.source_sid&&uncertainInput.pid===sourceSummary.pid&&uncertainInput.reason===normalizeRetryReason(reason.value)&&uncertainInput.timezone===timezone.value;}catch{return false;}
 }
 function update(event,force=false){
  if((pending&&!force)||disposed||saved)return false;
  if(event?.target===timezone){confirmed.checked=false;confirmedTuple=null;}
  if(event?.target){event.target.removeAttribute('aria-invalid');status.textContent='';}
  if(uncertainInput&&!sameUncertainInput())uncertainInput=null;
  const length=count(reason.value);counter.textContent=`${length}/200자`;reason.setAttribute('aria-invalid',String(length>200));
  const dates=previewRetryDates(estimatedNow(),timezone.value),key=JSON.stringify(dates),changed=key!==shownDates;
  if(changed){confirmed.checked=false;confirmedTuple=null;status.textContent=messages.ANCHOR_DATE_CHANGED;}
  shownDates=key;
  if(event?.target===confirmed)confirmedTuple=confirmed.checked?tuple():null;
  if(confirmed.checked&&(!confirmedTuple||confirmedTuple.timezone!==timezone.value||confirmedTuple.anchor_date!==anchorFromDates(dates))){confirmed.checked=false;confirmedTuple=null;}
  form.querySelector('[data-retry-date-preview]').innerHTML=dates.length?datesHTML(dates):message('유효한 시간대와 서버 시각을 확인해 주세요.');
  if(!capability&&!clockFlight&&!pending)status.textContent=messages.CAPABILITY_UNAVAILABLE;
  paintButton();schedule();return changed;
 }
 function setServerNow(now){
  if(!finite(now))throw new RetryApiError('INVALID_RESPONSE');serverNow=now;observedAt=monotonicNow();
 }
 function showClockError(error){
  if(!disposed&&!saved&&!pending&&error?.name!=='AbortError'){status.textContent=messages.CAPABILITY_UNAVAILABLE;paintButton();}
 }
 function refreshClock(){
  if(disposed||saved)return Promise.resolve();if(clockFlight)return clockFlight;
  clockAbort=new AbortController();const signal=clockAbort.signal;
  const task=Promise.resolve().then(()=>{
   if(disposed||saved)return;
   if(typeof api.list!=='function')throw new RetryApiError('CAPABILITY_UNAVAILABLE');
   return api.list({signal});
  }).then(dto=>{
   if(disposed||saved)return;
   setServerNow(dto.server_now);capability=dto.capabilities?.retry_plan_expected_anchor_date===true;clockReady=true;
   if(capability&&status.textContent===messages.CAPABILITY_UNAVAILABLE)status.textContent='';
   update();if(!capability)status.textContent=messages.CAPABILITY_UNAVAILABLE;
  }).catch(error=>{
   if(!disposed&&!saved){capability=false;clockReady=false;paintButton();}
   throw error;
  }).finally(()=>{if(clockFlight===task){clockFlight=null;clockAbort=null;}});
  clockFlight=task;return task;
 }
 function busy(value){
  pending=value;form.setAttribute('aria-busy',String(value));cancelTimer();
  for(const input of [reason,timezone,confirmed])input.disabled=value;
  paintButton();if(!value)schedule();
 }
 async function changedAnchor(error,input){
  uncertainInput=null;confirmed.checked=false;confirmedTuple=null;
  const details=anchorErrorPreview(error,input);
  if(details){setServerNow(details.server_now);clockReady=true;}
  else{
   clockReady=false;
   try{await refreshClock();}catch{}
   if(disposed)return;
  }
  update(undefined,true);busy(false);
  status.textContent=capability&&clockReady?messages.ANCHOR_DATE_CHANGED:messages.CAPABILITY_UNAVAILABLE;
  (capability&&clockReady?confirmed:clockButton)?.focus();
 }
 async function submit(event){
  event.preventDefault();if(pending||disposed||saved)return;
  const retry=sameUncertainInput();let input;
  try{
   if(retry)input={...uncertainInput};
   else{
    const data={source_sid:sourceSummary.source_sid,pid:sourceSummary.pid,reason:reason.value,timezone:timezone.value,
     timezone_confirmed:confirmed.checked&&confirmedTuple?.timezone===timezone.value,expected_anchor_date:confirmedTuple?.anchor_date};
    input={...buildRetryPlanPayload(data),timezone_confirmed:true};
   }
  }catch(error){
   status.textContent=error.message;let reasonValid=true;try{normalizeRetryReason(reason.value);}catch{reasonValid=false;}
   const target=!reasonValid?reason:!isRetryTimezone(timezone.value)?timezone:confirmed;
   target.setAttribute('aria-invalid','true');target.focus();return;
  }
  const checkedDates=shownDates;let sent=false;
  busy(true);status.textContent='서버 기준 등록 날짜를 확인하는 중이에요.';
  try{
   await refreshClock();if(disposed)return;
   if(!capability||!clockReady){busy(false);status.textContent=messages.CAPABILITY_UNAVAILABLE;return;}
   update(undefined,true);
   if(!retry&&(shownDates!==checkedDates||!confirmed.checked||confirmedTuple?.anchor_date!==input.expected_anchor_date)){
    busy(false);status.textContent=messages.ANCHOR_DATE_CHANGED;confirmed.focus();return;
   }
   status.textContent=retry?'같은 요청의 저장 결과를 확인하는 중이에요.':'예약을 저장하는 중이에요.';
   sent=true;saved=await api.register(input);
  }catch(error){
   if(disposed)return;
   if(error?.code==='ANCHOR_DATE_CHANGED'){await changedAnchor(error,input);return;}
   if(sent)uncertainInput=['NETWORK_ERROR','INVALID_RESPONSE'].includes(error?.code)?{...input}:null;
   busy(false);status.textContent=sent?retryErrorMessage(error):messages.CAPABILITY_UNAVAILABLE;button.focus();return;
  }
  if(disposed)return;
  pending=false;uncertainInput=null;removeListeners();root.innerHTML=renderRetryRegistration(sourceSummary,{saved});
  const result=root.querySelector('[role="status"]');result?.setAttribute('tabindex','-1');result?.focus();
  try{await onSaved?.(saved);}catch{if(!disposed&&result)result.textContent+=' 목록을 다시 열어 확인해 주세요.';}
 }
 function focused(){if(!pending&&visible())void refreshClock().catch(showClockError);}
 function visibility(){if(visible())focused();else cancelTimer();}
 function reloadClock(){void refreshClock().catch(showClockError);}
 function removeListeners(){
  cancelTimer();clockAbort?.abort();
  focusTarget?.removeEventListener('focus',focused);focusTarget?.removeEventListener('online',focused);
  visibilityTarget?.removeEventListener('visibilitychange',visibility);
  form.removeEventListener('input',update);form.removeEventListener('change',update);form.removeEventListener('submit',submit);clockButton?.removeEventListener('click',reloadClock);
 }
 form.addEventListener('input',update);form.addEventListener('change',update);form.addEventListener('submit',submit);clockButton?.addEventListener('click',reloadClock);
 focusTarget?.addEventListener('focus',focused);focusTarget?.addEventListener('online',focused);visibilityTarget?.addEventListener('visibilitychange',visibility);
 update();void refreshClock().catch(showClockError);
 return {get pending(){return pending;},get saved(){return saved;},get uncertain(){return !!uncertainInput;},update(summary){
  sourceSummary=summary;if(disposed||saved)return;
  if(finite(summary.server_now??summary.now))setServerNow(summary.server_now??summary.now);update();
 },dispose(){disposed=true;removeListeners();}};
}

const minutes=value=>finite(value)?`${value.toFixed(1)}분`:'확인 불가';
const outcome=value=>Object.hasOwn(outcomes,value)?outcomes[value]:'확인 불가';
function resultValue(attempt,key){
 if(key==='minutes')return minutes(attempt.minutes);
 if(key==='outcome')return outcome(attempt.outcome);
 if(key==='overtime_outcome')return attempt.overtime_outcome===null?'추가 시간 제출 없음':outcome(attempt.overtime_outcome);
 if(key==='mode')return Object.hasOwn(modes,attempt.mode)?modes[attempt.mode]:'확인 불가';
 if(key==='resources')return Object.hasOwn(resources,attempt.resources)?resources[attempt.resources]:'확인 불가';
 if(key==='duration')return integer(attempt.duration)?`${attempt.duration}분`:'확인 불가';
 if(key==='hints')return integer(attempt.hints)?`${attempt.hints}회`:'확인 불가';
 if(key==='tests')return integer(attempt.passed)&&integer(attempt.total)?`${attempt.passed}/${attempt.total}`:'확인 불가';
 if(key==='result_basis')return Object.hasOwn(resultBases,attempt.result_basis)?resultBases[attempt.result_basis]:'확인 불가';
 return '확인 불가';
}
export function renderRetryComparison(dto={},options={}){
 dto=dto||{};
 const wrap=body=>`<section class="retry-panel retry-comparison" data-retry-view="comparison" aria-label="이전 시도와 비교"><h2>이전 시도와 비교</h2>${body}</section>`;
 if(options.loading)return wrap(message('비교 결과를 불러오는 중이에요.'));
 if(options.error)return wrap(message(retryErrorMessage(options.error),'<button type="button" class="btn sm" data-retry-action="comparison-refresh">다시 조회</button>'));
 if(dto.state==='grading')return wrap(message('채점 중이에요. 채점이 끝나면 비교 결과를 표시해요.','<button type="button" class="btn sm" data-retry-action="comparison-refresh">다시 조회</button>'));
 if(dto.state==='unavailable'){
  const current=dto.current||{};
  return wrap(message('원본 시도를 확인할 수 없어요. 현재 재도전 결과는 보존되어 있어요.')+`<dl class="retry-current"><dt>현재 경과 시간</dt><dd>${esc(minutes(current.minutes))}</dd><dt>현재 시간 내 제출</dt><dd>${esc(outcome(current.outcome))}</dd><dt>현재 추가 시간 제출</dt><dd>${esc(resultValue(current,'overtime_outcome'))}</dd></dl>`);
 }
 // A ready state is required even if a caller accidentally includes historical fields.
 if(dto.state!=='ready')return wrap(message(messages.RETRY_ACTIVE));
 const source=dto.source||{},current=dto.current||{};
 const rows=[['문제별 경과 시간','minutes'],['시간 내 마지막 전체 제출','outcome'],['추가 시간 마지막 전체 제출','overtime_outcome'],['시간 내 테스트 통과','tests'],['연습 모드','mode'],['제한 시간','duration'],['자료 조건','resources'],['새 힌트 사용','hints'],['결과 판정 근거','result_basis']];
 const delta=typeof dto.delta_minutes==='number'&&Number.isFinite(dto.delta_minutes)?`${dto.delta_minutes>0?'+':''}${dto.delta_minutes.toFixed(1)}분`:'확인 불가';
 const computed=finite(dto.computed_at)?date(dto.computed_at):'확인 불가';
 return wrap(`<p class="retry-note">재도전 완료 · 예약을 만든 원본 시도와 비교해요.</p><div class="retry-table-wrap"><table class="retry-table"><caption>원본과 현재 재도전의 제출 결과 및 조건</caption><thead><tr><th scope="col">항목</th><th scope="col">원본 시도</th><th scope="col">현재 재도전</th></tr></thead><tbody>${rows.map(([label,key])=>`<tr><th scope="row">${label}</th><td>${esc(resultValue(source,key))}</td><td>${esc(resultValue(current,key))}</td></tr>`).join('')}</tbody></table></div><p class="retry-note">경과 시간 차이(현재 − 원본): ${esc(delta)}</p><p class="retry-note">${esc(dto.time_basis||'문제 선택 구간의 경과 시간(탭을 닫은 시간 포함)')}</p>${dto.conditions_differ===true?message('연습 조건이 달라요. 모드·시간·자료·힌트 사용을 함께 확인해 주세요.'):''}<p class="retry-note">제출 결과를 비교해요. 이후 수정한 코드의 정답 여부를 뜻하지 않으며, 시간 감소만으로 실력 상승을 판단하지 않아요.</p><p class="retry-reason">등록한 실패 이유: ${esc(dto.reason)}</p>${typeof dto.source_sid==='string'&&dto.source_sid?`<button type="button" class="btn sm" data-retry-action="source-review" data-retry-session="${esc(dto.source_sid)}">원본 복기 열기</button>`:''}${Array.isArray(dto.limitations)&&dto.limitations.length?`<ul class="retry-limitations">${dto.limitations.map(value=>`<li>${esc(value)}</li>`).join('')}</ul>`:''}<p class="retry-note">계산 시각: ${esc(computed)} · 기록 복구 중이면 갱신될 수 있어요.</p>`);
}

// The host activates this only while its home view is visible. No polling or listeners at import.
export function createRetryListController({api=createRetryApi(),onChange=()=>{},focusTarget,visibilityTarget,setTimer=globalThis.setTimeout,clearTimer=globalThis.clearTimeout}={}){
 let active=false,disposed=false,version=0,timer=null,flight=null,abort=null,dto=null;
 const visible=()=>!visibilityTarget?.hidden;
 const cancelTimer=()=>{if(timer!==null)clearTimer(timer);timer=null;};
 function schedule(){
  cancelTimer();if(!active||!visible()||!finite(dto?.server_now)||!finite(dto?.next_refresh_at))return;
  const delay=dto.next_refresh_at-dto.server_now;
  if(delay>0&&delay<=2147483647)timer=setTimer(()=>{timer=null;refresh();},delay);
 }
 function refresh(){
  if(!active||disposed||!visible())return Promise.resolve(null);
  if(flight)return flight;
  cancelTimer();const current=version;
  abort=new AbortController();const signal=abort.signal;
  onChange({dto,loading:true,error:null});
  const task=(async()=>{
   try{
    const response=await Promise.resolve().then(()=>api.list({signal}));
    if(active&&!disposed&&version===current){dto=response;onChange({dto,loading:false,error:null});schedule();}
    return response;
   }catch(error){
    if(active&&!disposed&&version===current&&error?.name!=='AbortError')onChange({dto,loading:false,error});
    return null;
   }finally{if(flight===task){flight=null;abort=null;}}
  })();
  flight=task;return task;
 }
 const focused=()=>{refresh();};
 const visibility=()=>{if(visible())refresh();else cancelTimer();};
 function deactivate(){
  active=false;version++;cancelTimer();abort?.abort();abort=null;flight=null;
  focusTarget?.removeEventListener('focus',focused);focusTarget?.removeEventListener('online',focused);
  visibilityTarget?.removeEventListener('visibilitychange',visibility);
 }
 return {
  activate(){
   if(disposed)return Promise.resolve(null);
   if(!active){active=true;version++;focusTarget?.addEventListener('focus',focused);focusTarget?.addEventListener('online',focused);visibilityTarget?.addEventListener('visibilitychange',visibility);}
   return refresh();
  },refresh,deactivate,
  dispose(){deactivate();disposed=true;}
 };
}
