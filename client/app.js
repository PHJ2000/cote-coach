import './style.css';
import {formatSessionClock} from './session-clock.js';
import {createWorkspaceCoach} from './workspace-coach.js';
import {createEditor,syncEditorTheme} from './editor.js';
import {prepareCapture,makeRecorder,stash,pending,drain,uid,retryUnstored} from './recording.js';
import {createSegmentPlayback} from './segment-playback.js';
import {createRetryApi,createRetryListController,renderRetryList,renderRetryRegistration,bindRetryRegistration,renderRetryComparison,retryErrorMessage,buildRetryStartPayload} from './retries.js';
import {$,$$,esc,fmt,date,day,getSaved,save,icon,btn,tag,level,empty,toast,confirmBox,theme,setTheme} from './ui.js';

let workspaceCoach=null,playback=null,captureTask=null,captureGeneration=0,finishing=false,stopTask=null;
let dashboard,health,modelInfo={models:[]},route='home',session=null,pid='',editor=null,recorder=null,voice=null,clock=null,serverOffset=0,eventQueue=Promise.resolve(),currentJob=null,viewGeneration=0,reviewTime=0,modelError='',deadlineHandled=false,poll=null,perfStart=0,elapsedBase=0;
let library={query:'',level:'all',tag:''},historyFilter='all';
let retryApi=null,retryHomeController=null,retryCards=null,retrySetupCard=null,retryBindings=[],navigationGeneration=0;
function retriesApi(){return retryApi??=createRetryApi();}
function disposeRetryBindings(){for(const entry of retryBindings)entry.binding.dispose();retryBindings=[];}

async function api(url,data,options={}){const r=await fetch(url,data===undefined?options:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),...options});const body=await r.json();if(!r.ok)throw new Error(body.error||'요청 실패');return body;}

const routes=[['home','home','대시보드'],['library','library','문제'],['history','history','기록'],['settings','settings','설정']];
const resourceLabel={none:'자료 금지','java-docs':'Java 공식 문서',search:'자유 검색'};
const modeLabel=m=>m==='exam'?'실전':'훈련';

/* ───────────── App shell ───────────── */
function healthSummary(){const items=[health?.java,health?.ide,health?.speech,health?.codex];const ok=items.filter(Boolean).length;return {ok,total:items.length,all:ok===items.length};}
function rail(){
 const h=healthSummary();
 return `<aside class="rail"><a class="brand" href="#" data-route="home" aria-label="코테 연습실 홈"><span class="mark">c<b>/</b></span><span class="brand-text">코테 연습실<small>Cote Practice Lab</small></span></a>
 <button type="button" class="btn primary rail-cta" data-action="setup">${icon('plus')} 새 연습<kbd>N</kbd></button>
 <nav class="rail-nav" aria-label="주요 메뉴">${routes.map(([r,i,t])=>`<button type="button" class="nav ${route===r?'selected':''}" data-route="${r}" ${route===r?'aria-current="page"':''}>${icon(i,17)}<span>${t}</span></button>`).join('')}</nav>
 <div class="rail-foot"><button type="button" class="env ${h.all?'ok':'warn'}" data-route="settings"><span class="env-dot"></span>${h.all?'모든 환경 준비됨':`환경 ${h.ok}/${h.total} 준비`}<small>기록은 이 컴퓨터에만 저장돼요</small></button>
 <button type="button" class="icon-btn" data-action="theme" aria-label="테마 전환" title="테마 전환">${icon(theme()==='light'?'moon':'sun',17)}</button></div></aside>
 <nav class="tabbar" aria-label="주요 메뉴">${routes.map(([r,i,t])=>`<button type="button" class="${route===r?'selected':''}" data-route="${r}">${icon(i,19)}<span>${t}</span></button>`).join('')}</nav>`;
}
function shell(body){
 $('#app').innerHTML=`<div class="app">${rail()}<header class="mobile-top"><a class="brand" href="#" data-route="home"><span class="mark">c<b>/</b></span><span class="brand-text">코테 연습실</span></a><div><button type="button" class="icon-btn" data-action="theme" aria-label="테마 전환">${icon(theme()==='light'?'moon':'sun',17)}</button><button type="button" class="btn primary sm" data-action="setup">${icon('plus',14)} 새 연습</button></div></header><main class="main" id="main"><div class="content">${body}</div></main></div>`;
}
function pageHead(eyebrow,title,sub,action=''){return `<header class="page-head"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1>${sub?`<p class="sub">${sub}</p>`:''}</div>${action?`<div class="page-actions">${action}</div>`:''}</header>`;}
function cleanup(){navigationGeneration++;captureGeneration++;retryHomeController?.dispose();retryHomeController=null;disposeRetryBindings();retrySetupCard=null;playback?.dispose();playback=null;workspaceCoach?.dispose();workspaceCoach=null;clearInterval(clock);clearTimeout(poll);editor?.dispose();editor=null;viewGeneration++;document.body.classList.remove('in-workspace','in-review');}
async function navigate(next){
 if(session?.status==='active'&&route==='workspace'&&next!=='workspace'){toast('진행 중인 연습을 먼저 종료해 주세요.');return;}
 cleanup();const generation=navigationGeneration;route=next;
 try{const d=await api('/api/v2/dashboard');if(generation!==navigationGeneration)return;dashboard=d;render();window.scrollTo(0,0);}
 catch(error){if(generation===navigationGeneration)throw error;}
}
function render(){({home,library:libraryView,history:historyView,settings})[route]?.();}

/* ───────────── Shared pieces ───────────── */
function recommendationReason({problem,reason=''}){
 // Recommendation text can name the answer technique even without a tag badge.
 return problem.tags.some(t=>reason.toLowerCase().includes(t.toLowerCase()))?'복기 기록을 바탕으로 추천한 문제예요.':reason;
}
function problemCard(p){
 const first=p.description.split('\n')[0];
 return `<article class="problem-card"><div class="pc-top">${level(p.level)}<span class="pc-source">${esc(p.source)}</span></div><h3>${esc(p.title)}</h3><p>${esc(first).slice(0,110)}${first.length>110?'…':''}</p><div class="pc-foot"><button type="button" class="btn sm" data-practice="${esc(p.id)}">연습 ${icon('right',13)}</button></div></article>`;
}
function sessionStatus(s){return s.status==='active'?['진행 중','live']:s.report?['복기 완료','pass']:['복기 대기','wait'];}
function sessionRows(sessions){
 return `<ul class="session-list">${sessions.map(s=>{const [label,tone]=sessionStatus(s);return `<li><button type="button" class="session-row" data-session="${s.id}"><span class="sr-mode ${s.mode}">${s.mode==='exam'?icon('target',16):icon('spark',16)}</span><span class="sr-main"><strong>${esc(s.title)}</strong><small>${modeLabel(s.mode)} · ${s.duration}분 · ${s.problem_count}문제 · 실행 ${s.runs}회</small></span><span class="sr-date">${date(s.started)}</span><span class="status ${tone}">${label}</span><span class="sr-go">${s.status==='active'?'이어 하기':'열기'} ${icon('right',13)}</span></button></li>`;}).join('')}</ul>`;
}
function heatmap(sessions){
 // 12 weeks of practice days, Monday-first columns.
 const counts=new Map();for(const s of sessions){const k=new Date(s.started).toDateString();counts.set(k,(counts.get(k)||0)+1);}
 const today=new Date();today.setHours(0,0,0,0);const start=new Date(today);start.setDate(start.getDate()-((today.getDay()+6)%7)-7*11);
 let cells='';for(let i=0;i<84;i++){const d=new Date(start);d.setDate(start.getDate()+i);const n=counts.get(d.toDateString())||0,future=d>today;cells+=`<i class="h${future?'x':Math.min(3,n)}" title="${d.toLocaleDateString('ko-KR')} · ${n}회"></i>`;}
 return `<div class="heatmap" role="img" aria-label="최근 12주 연습 기록">${cells}</div><div class="heat-legend"><span>12주 전</span><span class="scale"><i class="h0"></i><i class="h1"></i><i class="h2"></i><i class="h3"></i></span><span>오늘</span></div>`;
}

/* ───────────── Home ───────────── */
function home(){
 const s=dashboard.stats,active=dashboard.sessions.find(s=>s.status==='active'),recs=dashboard.recommendations,top=recs[0];
 const waiting=dashboard.sessions.filter(s=>s.status!=='active'&&!s.report).length;
 shell(`${pageHead(day(Date.now()),'정답까지의 과정을, 실력으로.','풀고, 돌아보고, 다음 문제에 적용해요. 코드와 실행, 목소리와 화면이 같은 시간축에 남습니다.')}
 ${active?`<div class="banner live"><span class="pulse"></span><div><strong>진행 중인 연습이 있어요</strong><span>${esc(active.title)} · ${modeLabel(active.mode)} ${active.duration}분</span></div><button type="button" class="btn primary sm" data-session="${active.id}">이어 하기 ${icon('right',13)}</button></div>`:''}
 <div id="retry-home"></div>
 ${!active&&waiting?`<div class="banner"><span class="banner-icon">${icon('chat',16)}</span><div><strong>복기를 기다리는 연습 ${waiting}개</strong><span>막힌 순간을 Codex와 짚어 보면 다음 연습이 선명해져요.</span></div><button type="button" class="btn sm" data-route="history">기록 보기 ${icon('right',13)}</button></div>`:''}
 <div class="home-grid">
  <section class="card hero">${top?`<p class="eyebrow accent">${icon('bolt',13)} 오늘의 추천</p><div class="hero-title">${level(top.problem.level)}<h2>${esc(top.problem.title)}</h2></div><p class="hero-reason">${esc(recommendationReason(top))}</p><p class="hero-desc">${esc(top.problem.description.split('\n')[0])}</p>
   <div class="hero-actions"><button type="button" class="btn primary" data-practice="${esc(top.problem.id)}" data-mode="training">${icon('spark',15)} 훈련으로 풀기</button><button type="button" class="btn" data-practice="${esc(top.problem.id)}" data-mode="exam">${icon('target',15)} 실전으로 풀기</button></div>
   <div class="hero-modes"><div><b>훈련</b> 자동완성 · 요청 시 힌트 · 마감 후 추가 풀이</div><div><b>실전</b> 기본 편집 · 개입 없음 · 마감 시 자동 제출</div></div>`:empty('추천할 문제가 없어요','문제 라이브러리에서 문제를 가져와 보세요.')}</section>
  <section class="card stats-card"><div class="card-head"><h2>나의 기록</h2><span class="muted">첫 풀이 기준</span></div>
   <div class="kpis">${[['완료한 연습',s.sessions,'회'],['풀어본 문제',s.problems,'문제'],['독립 해결',s.independent,'문제'],['연습한 날',s.days,'일']].map(([t,v,u])=>`<div class="kpi"><span>${t}</span><strong>${v}<small>${u}</small></strong></div>`).join('')}</div>
   ${heatmap(dashboard.sessions)}<p class="fine">독립 해결은 시간 안에 힌트 없이 제출까지 통과한 문제예요.</p></section>
 </div>
 <div class="split-grid">
  <section class="card"><div class="card-head"><h2>다음에 풀어볼 문제</h2><button type="button" class="text-btn" data-route="library">전체 ${icon('right',13)}</button></div>
   ${recs.slice(1,5).map(x=>`<button type="button" class="rec-row" data-practice="${esc(x.problem.id)}">${level(x.problem.level)}<span><strong>${esc(x.problem.title)}</strong><small>${esc(recommendationReason(x))}</small></span>${icon('right',14)}</button>`).join('')||'<p class="muted">추천이 더 없어요.</p>'}</section>
  <section class="card routine"><div class="card-head"><h2>하루 3시간 루틴</h2>${tag('제안','accent')}</div>
   ${[['90','새 문제 집중 풀이','Lv.1 확인 + Lv.2 도전'],['30','Codex와 구간별 복기','막힌 이유를 함께 확인'],['60','약점 교정과 재적용','작은 연습 → 다른 문제']].map(([m,t,d],i)=>`<div class="routine-row"><span class="step">${i+1}</span><div><strong>${t}</strong><small>${d}</small></div><span class="mins">${m}분</span></div>`).join('')}
   <div class="routine-bar"><i style="flex:90"></i><i style="flex:30"></i><i style="flex:60"></i></div><p class="fine">의무 일정이 아니에요. 기록과 컨디션에 맞춰 조정하세요.</p></section>
 </div>
 <section class="card"><div class="card-head"><h2>최근 연습</h2><button type="button" class="text-btn" data-route="history">모두 ${icon('right',13)}</button></div>${dashboard.sessions.length?sessionRows(dashboard.sessions.slice(0,5)):empty('첫 번째 연습을 시작해 보세요','완성된 코드뿐 아니라, 그 코드에 도착한 과정까지 남겨요.',btn(`${icon('plus',14)} 새 연습`,'setup','primary sm'))}</section>
 ${dashboard.cohorts?.length||Object.keys(dashboard.weaknesses).length?`<div class="split-grid">
  ${dashboard.cohorts?.length?`<section class="card"><div class="card-head"><h2>난이도별 첫 풀이</h2><span class="muted">Java</span></div><div class="table-wrap"><table><thead><tr><th>난이도</th><th>문제</th><th>독립 해결률</th><th>시간 중앙값</th><th>평균 힌트</th></tr></thead><tbody>${dashboard.cohorts.map(c=>`<tr><td>${level(c.level)}</td><td>${c.count}개</td><td><div class="rate"><i style="width:${Math.min(100,c.independent_rate)}%"></i></div>${c.independent_rate}%</td><td>${c.median_minutes}분</td><td>${c.average_hints}회</td></tr>`).join('')}</tbody></table></div><p class="fine">문제 구성과 자료 조건에 따라 달라져요. 시간이 줄었다고 실력 상승을 단정하지 않아요.</p></section>`:''}
  ${Object.keys(dashboard.weaknesses).length?`<section class="card"><div class="card-head"><h2>복기에서 반복된 관찰</h2></div>${(()=>{const e=Object.entries(dashboard.weaknesses).sort((a,b)=>b[1]-a[1]),max=Math.max(...e.map(x=>x[1]));return e.map(([t,c])=>`<div class="bar-row"><span>${esc(t)}</span><div class="bar"><i style="width:${c/max*100}%"></i></div><strong>${c}회</strong></div>`).join('');})()}<p class="fine">Codex 보고서의 관찰 횟수예요. 능력 점수가 아닙니다.</p></section>`:''}
 </div>`:''}`);
 mountRetryHome();
}

function mountRetryHome(){
 // Existing rendering-only VM tests remove imports. In the app this module is present.
 if(typeof createRetryListController!=='function')return;
 retryHomeController?.dispose();
 retryHomeController=createRetryListController({api:retriesApi(),focusTarget:window,visibilityTarget:document,onChange:({dto,loading,error})=>{
  if(route!=='home')return;const host=$('#retry-home');if(!host)return;
  retryCards=dto;host.innerHTML=renderRetryList(dto,{loading,error});
 }});
 retryHomeController.activate();
}
function prepareRetry(itemId){
 const card=retryCards?.due?.find(c=>c.item_id===itemId);
 if(!card||card.can_start!==true)throw new Error('목록을 다시 조회해 시작 가능한 회차를 확인하세요.');
 cleanup();route='retry-setup';session=null;
 retrySetupCard={item_id:card.item_id,title:card.title,level:card.level,due_date:card.due_date,timezone:card.timezone,offset_days:card.offset_days,defaults:{...card.defaults}};
 $('#modal').close();$('#modal-body').innerHTML='';
 const d=retrySetupCard.defaults;
 shell(`${pageHead('Retry practice','재도전 준비','이전 코드·해설·태그와 등록 이유를 보지 않고 다시 풀어요.',btn('대시보드','retry-home','sm'))}
  <section class="card"><h2>${esc(card.title)}</h2><p class="fine">Lv.${esc(card.level)} · 등록일 +${esc(card.offset_days)}일 회차 · 예정일 ${esc(card.due_date)} · ${esc(card.timezone)}</p>
  <p class="fine">한 문제로 고정해요. 원본의 연습 조건이 기본값이며 바꾸면 완료 후 비교에 표시해요.</p>
  <div class="field"><span>코치 개입</span>${seg('mode',[['training','훈련'],['exam','실전']],d.mode)}</div>
  <div class="field"><span>제한 시간</span><label class="custom-min"><input id="duration" type="number" min="5" max="300" value="${esc(d.duration)}" aria-label="제한 시간(분)">분</label></div>
  <div class="field"><span>외부 자료</span>${seg('resources',[['none','사용 금지'],['java-docs','Java 공식 문서'],['search','자유 검색']],d.resources)}</div>
  <details class="model-box"><summary>분석·힌트 모델 <span id="model-summary" class="muted"></span></summary><div id="setup-model-fields">${modelFields('setup-')}</div>${btn('모델 목록 새로고침','setup-models','sm')}</details>
  <p id="retry-setup-summary" class="fine"></p><p class="fine">시작하면 화면·마이크 권한을 요청해요. 현재 코테 연습실 탭을 선택하세요.</p>
  ${btn('녹화 준비 후 재도전 시작','retry-start','primary lg')}<p id="retry-start-status" role="status" aria-live="polite"></p></section>`);
 fillEfforts('setup-');updateSetupSummary();$('#main').setAttribute('tabindex','-1');$('#main').focus();
}
async function startRetry(){
 const card=retrySetupCard,target=$('[data-action="retry-start"]'),generation=captureGeneration;
 if(!card||!target||target.disabled)return;
 const d=buildRetryStartPayload({item_id:card.item_id,kind:'single',mode:radio('mode'),duration:Number($('#duration').value),resources:radio('resources'),...selectedModel('setup-')});
 if(!health?.java)throw new Error('Docker Java 실행 환경을 먼저 준비해 주세요.');
 target.disabled=true;const label=target.innerHTML;let capture;
 try{
  capture=await prepareCapture();
  if(route!=='retry-setup'||generation!==captureGeneration){capture.stream.getTracks().forEach(t=>t.stop());return;}
  target.textContent='재도전 시작 중…';const result=await retriesApi().start(d),s=result.session;
  if(route!=='retry-setup'||generation!==captureGeneration){capture.stream.getTracks().forEach(t=>t.stop());return;}
  if(s.status==='completed'){capture.stream.getTracks().forEach(t=>t.stop());await openReview(s.id);return;}
  if(!result.created)await applyUnsentCode(s);
  if(route!=='retry-setup'||generation!==captureGeneration){capture.stream.getTracks().forEach(t=>t.stop());return;}
  openWorkspace(s,capture);
  if(result.payload_mismatch)toast('이미 시작한 회차예요. 처음 저장한 연습 조건으로 이어갑니다.');
  if(result.ide_warning)toast('Java 편집 지원 준비: '+result.ide_warning);
 }catch(error){
  capture?.stream.getTracks().forEach(t=>t.stop());
  const status=$('#retry-start-status');if(status){status.textContent=error.code?retryErrorMessage(error):error.message;target.focus();}
  else toast(error.code?retryErrorMessage(error):error.message);
 }finally{target.disabled=false;target.innerHTML=label;}
}
async function refreshRetryReview(){
 if(route!=='review'||!session)return;
 const sid=session.id,generation=viewGeneration,previous=retryBindings;retryBindings=[];
 for(const [index,p] of session.problems.entries()){
  const host=$('#retry-registration-'+index);if(!host)continue;
  const c=session.retry_candidates?.find(c=>c.pid===p.id);
  const summary={status:session.status,source_sid:sid,pid:p.id,title:p.title,eligible:c?.eligible===true,code:c?.code,existing_plan_id:c?.existing_plan_id,defaults:c?.defaults,server_now:session.now};
  const key=JSON.stringify([sid,p.id,summary.status,summary.eligible,summary.code,summary.existing_plan_id]);
  const entry=previous.find(entry=>entry.host===host&&entry.sid===sid&&entry.pid===p.id);
  // Metrics/comparison refreshes must keep the form, focus and live save intact.
  if(entry&&(entry.key===key||entry.binding.pending||entry.binding.uncertain||(entry.binding.saved&&entry.binding.saved.plan.id===summary.existing_plan_id))){
   entry.binding.update(summary);retryBindings.push(entry);
  }else{
   entry?.binding.dispose();host.innerHTML=renderRetryRegistration(summary);
   retryBindings.push({host,sid,pid:p.id,key,binding:bindRetryRegistration(host,summary,{api:retriesApi()})});
  }
 }
 for(const entry of previous)if(!retryBindings.includes(entry))entry.binding.dispose();
 const host=$('#retry-comparison');if(!host||!session.retry)return;
 host.innerHTML=renderRetryComparison(null,{loading:true});
 try{const dto=await retriesApi().comparison(sid);if(route==='review'&&session.id===sid&&viewGeneration===generation)host.innerHTML=renderRetryComparison(dto);}
 catch(error){if(route==='review'&&session.id===sid&&viewGeneration===generation)host.innerHTML=renderRetryComparison(null,{error});}
}
async function retryAction(name,target){
 if(name==='prepare')return prepareRetry(target.dataset.retryItem);
 if(name==='resume')return loadSession(target.dataset.retrySession);
 if(name==='refresh')return retryHomeController?.refresh();
 if(name==='comparison-refresh')return refreshRetryReview();
 if(name==='source-review')return openReview(target.dataset.retrySession);
}

/* ───────────── Library ───────────── */
function libraryView(){
 const tags=[...new Set(dashboard.problems.flatMap(p=>p.tags))],levels=[...new Set(dashboard.problems.map(p=>p.level))].sort();
 shell(`${pageHead('Problem library','어떤 문제로 연습할까요?','자체 문제와 직접 가져온 문제를 함께 연습해요.',btn(`${icon('plus',15)} 문제 가져오기`,'import'))}
 <div class="toolbar"><label class="search">${icon('search')}<input id="lib-search" type="search" placeholder="문제 이름, 설명 검색" value="${esc(library.query)}" aria-label="문제 검색"></label>
  <div class="seg" role="group" aria-label="난이도">${['all',...levels].map(l=>`<button type="button" class="${String(library.level)===String(l)?'on':''}" data-lib-level="${l}">${l==='all'?'전체':'Lv.'+l}</button>`).join('')}</div></div>
 <details class="type-filter" id="lib-type-filter">
  <summary>유형으로 찾기 <span id="lib-type-status"></span></summary>
  <div class="type-filter-body"><p class="fine">유형을 선택하면 해당하는 문제만 볼 수 있어요.</p>
   <div class="chips" role="group" aria-label="문제 유형 필터"><button type="button" class="chip" data-lib-tag="">전체 유형</button>${tags.map(t=>`<button type="button" class="chip" data-lib-tag="${esc(t)}">${esc(t)}</button>`).join('')}</div>
  </div>
 </details>
 <div id="lib-grid" class="problem-grid"></div>
 <p class="fine center">채점은 이 앱에 준비된 테스트 기준이에요. 프로그래머스 공식 비공개 채점과 다를 수 있어요.</p>`);
 renderLibrary();
}
function renderLibrary(){
 $('#lib-type-status').textContent=library.tag?'필터 적용 중':'';
 $$('[data-lib-tag]').forEach(b=>{const selected=b.dataset.libTag===library.tag;b.classList.toggle('on',selected);b.setAttribute('aria-pressed',String(selected));});
 const q=library.query.trim().toLowerCase();
 const list=dashboard.problems.filter(p=>(library.level==='all'||String(p.level)===String(library.level))&&(!library.tag||p.tags.includes(library.tag))&&(!q||(p.title+' '+p.description).toLowerCase().includes(q)));
 $('#lib-grid').innerHTML=list.map(p=>problemCard(p)).join('')||empty('조건에 맞는 문제가 없어요','검색어나 필터를 바꿔 보세요.');
}

/* ───────────── History ───────────── */
function historyView(){
 const filters=[['all','전체'],['wait','복기 대기'],['pass','복기 완료'],['live','진행 중']];
 shell(`${pageHead('Practice archive','나의 풀이 과정이 쌓이는 곳','같은 문제의 재풀이와 새 문제에서의 독립 해결을 구분해 살펴보세요.')}
 <div class="toolbar"><div class="seg" role="group" aria-label="상태">${filters.map(([k,t])=>`<button type="button" class="${historyFilter===k?'on':''}" data-history="${k}">${t}</button>`).join('')}</div></div>
 <section class="card flush" id="history-list"></section>`);
 renderHistory();
}
function renderHistory(){
 const list=dashboard.sessions.filter(s=>historyFilter==='all'||sessionStatus(s)[1]===historyFilter);
 $('#history-list').innerHTML=list.length?sessionRows(list):empty(dashboard.sessions.length?'해당하는 기록이 없어요':'아직 기록이 없어요','연습을 종료하면 녹화와 코드 이력이 이곳에 남아요.');
}

/* ───────────── Models & settings ───────────── */
function modelFields(prefix=''){const saved=getSaved('lab-model',{});return `<div class="two-fields"><label class="field"><span>코치 모델</span><select id="${prefix}model" class="model-select">${modelInfo.models.length?'<option value="">모델을 선택하세요</option>'+modelInfo.models.map(m=>`<option value="${esc(m.model)}" ${m.model===(session?.model||saved.model)?'selected':''}>${esc(m.displayName||m.model)}</option>`).join(''):'<option value="">모델 목록을 불러오세요</option>'}</select></label><label class="field"><span>추론 수준</span><select id="${prefix}effort" class="effort-select"></select></label></div>`;}
function fillEfforts(prefix=''){const select=$('#'+prefix+'model'),out=$('#'+prefix+'effort');if(!select||!out)return;const m=modelInfo.models.find(m=>m.model===select.value),saved=getSaved('lab-model',{});const desired=session?.model===select.value?session.effort:saved.model===select.value?saved.effort:m?.defaultReasoningEffort;out.innerHTML=(m?.supportedReasoningEfforts||[]).map(e=>`<option value="${esc(e.reasoningEffort)}" ${e.reasoningEffort===desired?'selected':''}>${esc(e.reasoningEffort)}</option>`).join('');updateModelSummary();}
function selectedModel(prefix=''){const value={model:$('#'+prefix+'model')?.value||'',effort:$('#'+prefix+'effort')?.value||''};if(value.model&&value.effort)save('lab-model',value);return value;}
async function loadModels(){try{modelInfo=await api('/api/v2/models');modelError='';}catch(e){modelError=e.message;modelInfo={models:[]};}return modelInfo;}
function settings(){
 const rows=[['Java 21 격리 실행','Docker · 네트워크 차단 · 메모리 제한',health?.java],['Java IDE 자동완성','Eclipse JDT 언어 서버',health?.ide],['로컬 한국어 음성 전사','Whisper small · CPU',health?.speech],['Codex CLI','ChatGPT 로그인으로 코칭',health?.codex]];
 shell(`${pageHead('Settings','연결과 보관','기록은 이 컴퓨터에 보관하고, 코칭은 연결된 Codex 계정을 사용해요.')}
 <div class="settings-grid">
  <section class="card"><div class="card-head"><h2>실행 환경</h2>${tag('LOCAL','pass')}</div><ul class="checklist">${rows.map(([t,d,v])=>`<li class="${v?'ok':'warn'}"><span class="check-icon">${icon(v?'check':'alert',14)}</span><div><strong>${t}</strong><small>${d}</small></div><span class="status ${v?'pass':'wait'}">${v?'준비됨':'확인 필요'}</span></li>`).join('')}</ul></section>
  <section class="card"><div class="card-head"><h2>화면 테마</h2></div><div class="theme-pick">${[['dark','다크','moon'],['light','라이트','sun']].map(([k,t,i])=>`<button type="button" class="theme-opt ${theme()===k?'on':''}" data-set-theme="${k}"><span class="swatch ${k}"><i></i><i></i><i></i></span>${icon(i,15)} ${t}</button>`).join('')}</div><p class="fine">에디터 색상도 함께 바뀌어요. 이 브라우저에만 저장돼요.</p></section>
  <section class="card wide"><div class="card-head"><div><h2>Codex 모델 · 추론 수준</h2><p class="muted">선택한 설정을 임의로 다른 모델로 바꾸지 않아요.</p></div>${btn(`${icon('refresh',14)} 새로고침`,'models','sm')}</div>${modelFields('settings-')}<p class="fine ${modelError?'error-text':''}">${esc(modelError||'기존 ChatGPT 로그인에 연결돼요. 분석 요청마다 Codex 사용량을 소모하며, 별도 API로 자동 전환하지 않아요.')}</p><div class="row-end">${btn('기본값으로 저장','save-model','primary')}</div><details class="plain"><summary>계정이 제공한 사용 한도 정보</summary><pre class="code-block">${esc(JSON.stringify(modelInfo.limits||'확인할 수 없음',null,2))}</pre></details></section>
  <section class="card wide"><div class="card-head"><h2>기록 보관과 백업</h2></div><p class="muted">원본 영상·녹음·코드·보고서는 자동 삭제하지 않아요. ZIP에는 데이터베이스와 원본 녹화 조각이 들어 있어요.</p><code class="path">${esc(health?.data_dir||'data/lab')}</code><div class="row-gap"><a class="btn" href="/api/v2/backup" download>${icon('download',15)} 전체 기록 백업</a>${btn(`${icon('refresh',14)} 미전송 기록 다시 보내기`,'retry-outbox')}<a class="btn" href="/dist/recovery.html" target="_blank" rel="noopener">미전송 기록 백업</a></div><p class="fine">복원 방법은 README의 ‘데이터와 백업’을 참고하세요.</p></section>
 </div>`);
 fillEfforts('settings-');
}

/* ───────────── Setup sheet ───────────── */
function showModal(html,cls=''){$('#modal').className=cls;$('#modal-body').innerHTML=html;if(!$('#modal').open)$('#modal').showModal();}
const radio=name=>$(`[name="${name}"]:checked`)?.value;
function seg(name,options,value){return `<div class="seg-choice" role="radiogroup">${options.map(([v,t,d])=>`<label class="${d?'rich':''}"><input type="radio" name="${name}" value="${v}" ${v===value?'checked':''}><span><b>${t}</b>${d?`<small>${d}</small>`:''}</span></label>`).join('')}</div>`;}
function setup(preselected,presetMode){
 // A previously opened filter must not remain visible behind the problem picker.
 const filter=$('#lib-type-filter');if(filter)filter.open=false;
 const ids=preselected?[preselected]:[dashboard.recommendations[0]?.problem.id].filter(Boolean);const pref=getSaved('lab-setup',{});
 showModal(`<div class="sheet-head"><div><p class="eyebrow">New practice</p><h2>연습 준비</h2></div><button type="button" class="icon-btn" data-action="close" aria-label="닫기">${icon('x',18)}</button></div>
 <div class="sheet-body"><div class="sheet-col">
  <div class="field"><span>코치 개입</span>${seg('mode',[['training','훈련','Java 자동완성 · 요청 시 힌트 · 마감 후 추가 풀이'],['exam','실전','기본 편집 · 풀이 중 개입 없음 · 마감 시 자동 제출']],presetMode||pref.mode||'exam')}</div>
  <div class="field"><span>연습 단위</span>${seg('kind',[['single','한 문제 집중'],['mock','모의 코테 (최대 5문제)']],'single')}</div>
  <div class="field"><span>제한 시간</span><div class="duration">${[20,30,40,60,90].map(m=>`<button type="button" class="chip ${m===(pref.duration||40)?'on':''}" data-minutes="${m}">${m}분</button>`).join('')}<label class="custom-min"><input id="duration" type="number" min="5" max="300" value="${pref.duration||40}" aria-label="제한 시간(분)">분</label></div></div>
  <div class="field"><span>외부 자료</span>${seg('resources',[['none','사용 금지'],['java-docs','Java 공식 문서'],['search','자유 검색']],pref.resources||'none')}</div>
  <details class="model-box" ${modelInfo.models.length&&!getSaved('lab-model',{}).model?'open':''}><summary><span>${icon('spark',14)} 분석·힌트 모델</span><span id="model-summary" class="muted"></span></summary><div id="setup-model-fields">${modelFields('setup-')}</div><button type="button" class="text-btn" data-action="setup-models">${icon('refresh',13)} 목록 새로고침</button></details>
 </div><div class="sheet-col">
  <div class="field"><span>연습할 문제 <em id="pick-count"></em></span><label class="search small">${icon('search',14)}<input id="pick-search" type="search" placeholder="문제 검색" aria-label="문제 검색"></label><div class="problem-picker">${dashboard.problems.map(p=>`<label class="pick" data-title="${esc(p.title.toLowerCase())}"><input type="checkbox" name="problem" value="${p.id}" ${ids.includes(p.id)?'checked':''}><span class="pick-box">${icon('check',12)}</span><span class="pick-title">${esc(p.title)}</span>${level(p.level)}</label>`).join('')}</div></div>
 </div></div>
 <div class="sheet-foot"><div class="rec-note">${icon('video',16)}<p>시작하면 화면·마이크 권한을 요청해요. <strong>현재 코테 연습실 탭</strong>을 선택하세요. 접근법을 정할 때, 막혔을 때 생각을 짧게 말해 보세요.</p></div><div class="foot-actions"><span id="setup-summary" class="setup-summary"></span>${btn(`녹화 준비 후 시작 ${icon('right',15)}`,'start','primary lg')}</div></div>`,'sheet');
 fillEfforts('setup-');updateSetupSummary();
}
function updateModelSummary(){const el=$('#model-summary');if(!el)return;const m=$('#setup-model')?.value,e=$('#setup-effort')?.value;el.textContent=m?`${modelInfo.models.find(x=>x.model===m)?.displayName||m} · ${e}`:'나중에 선택해도 돼요';}
function updateSetupSummary(){
 if(route==='retry-setup'&&retrySetupCard){const el=$('#retry-setup-summary');if(el)el.textContent=`${modeLabel(radio('mode'))} · ${Number($('#duration').value)||0}분 · 한 문제 · ${resourceLabel[radio('resources')]}`;return;}
 const el=$('#setup-summary');if(!el)return;const n=$$('[name="problem"]:checked').length;
 $('#pick-count').textContent=`${n}개 선택`;
 el.innerHTML=`${modeLabel(radio('mode'))} · ${Number($('#duration').value)||0}분 · ${n}문제 · ${resourceLabel[radio('resources')]}`;
 $$('[data-minutes]').forEach(b=>b.classList.toggle('on',b.dataset.minutes===$('#duration').value));
}
async function start(){const target=$('[data-action="start"]');const ids=$$('[name="problem"]:checked').map(x=>x.value);if(!ids.length)throw new Error('문제를 선택하세요.');if(radio('kind')==='single'&&ids.length!==1)throw new Error('한 문제 연습은 하나만 선택하세요.');if(ids.length>5)throw new Error('모의 코테는 최대 5문제까지 선택할 수 있어요.');if(!health?.java)throw new Error('Docker Java 실행 환경을 먼저 준비해 주세요.');const d={problem_ids:ids,kind:radio('kind'),mode:radio('mode'),resources:radio('resources'),duration:Number($('#duration').value),...selectedModel('setup-')};save('lab-setup',{mode:d.mode,duration:d.duration,resources:d.resources});target.disabled=true;const originalLabel=target.innerHTML;let capture;try{capture=await prepareCapture();target.textContent=d.mode==='training'?'Java 편집기 준비 중…':'연습 시작 중…';session=await api('/api/v2/start',d);serverOffset=session.now-Date.now();$('#modal').close();openWorkspace(session,capture);if(session.ide_warning)toast('Java 편집 지원 준비: '+session.ide_warning);}catch(e){capture?.stream.getTracks().forEach(t=>t.stop());throw e;}finally{target.disabled=false;target.innerHTML=originalLabel;}}

/* ───────────── Workspace ───────────── */
function elapsed(){return Math.max(0,elapsedBase+performance.now()-perfStart);}
// Explicit order for events with the same ms; based on wall time so it keeps increasing across reloads.
let eventSeq=0;const nextSeq=()=>eventSeq=Math.max(eventSeq+1,Date.now()*1000);
function record(kind,payload={},problem=pid,scope){
 if(!session&&!scope)return;const sid=scope?.sid??session.id,e={id:uid(),ms:scope?.ms??Math.round(elapsed()),seq:nextSeq(),kind,pid:problem,payload};
 // Durable writes never wait behind a slow upload. flush() still waits for both.
 const stored=eventQueue.then(()=>stash({id:e.id,url:'/api/v2/events',type:'application/json',body:JSON.stringify({sid,events:[e]})}));
 eventQueue=stored.catch(e=>{setSaveStatus('기록 저장 실패','error');toast(e.message);});
 void stored.then(()=>drain().then(()=>setSaveStatus('기록 저장됨')).catch(()=>setSaveStatus('미전송 기록 있음','warn'))).catch(()=>{});
 return stored;
}
function setSaveStatus(text,tone=''){const el=$('#save-status');if(!el)return;el.textContent=text;el.dataset.tone=tone||(/실패|중단|미전송/.test(text)?'warn':'');}
async function flush(){await eventQueue;await retryUnstored();let error;try{await drain();}catch(e){error=e;}await eventQueue;await retryUnstored();const left=(await pending()).filter(x=>x.url.includes(session.id)||x.body?.includes?.(session.id));if(left.length)throw error||new Error('아직 전송하지 못한 기록이 있어요. 연결을 확인하고 다시 시도하세요.');}
function recordingState(state,reason=''){
 const pill=$('#record-status'),help=$('#recording-help');if(!pill)return;
 pill.className='rec-pill '+(state==='recording'?'on':'off');
 pill.innerHTML=state==='recording'?'<span class="rec-dot"></span>녹화 중':`<button type="button" class="text-btn" data-action="reconnect" ${state==='connecting'||finishing||(deadlineHandled&&session?.mode==='exam')?'disabled':''}>${icon('video',13)} ${state==='connecting'?'화면공유 선택 중…':deadlineHandled&&session?.mode==='exam'?'마감됨':'녹화 이어하기'}</button>`;
 if(help){help.hidden=state==='recording';help.innerHTML=`<div><strong>${state==='connecting'?'현재 코테 연습실 탭을 선택해 주세요.':'지금은 화면과 음성이 녹화되지 않아요.'}</strong><p>${esc(reason||'앞서 저장된 녹화는 보존돼요. 녹화 이어하기를 누르고 화면공유를 다시 선택하면 같은 연습에 새 구간으로 저장해요. 중단된 시간은 복구되지 않아요.')}</p></div>`;}
}
function attachRecorder(capture){
 const sid=session.id,problem=pid,start=Math.round(elapsed()),began=performance.now();
 if(recorder?.recorder.state==='recording'){capture.stream.getTracks().forEach(t=>t.stop());return;}
 workspaceCoach?.setCapture(capture);
 recorder=makeRecorder(capture.stream,{sid,start,event:(kind,payload)=>record(kind,payload,problem,{sid,ms:Math.round(start+performance.now()-began)}),status:setSaveStatus,onEnded:()=>{if(route==='workspace'&&session?.id===sid){workspaceCoach?.setCapture(null);recordingState('stopped');}}});
 recordingState('recording');
}
function reconnectRecording(){
 if(captureTask)return captureTask;
 if(finishing||(deadlineHandled&&session?.mode==='exam')||route!=='workspace'||session?.status!=='active'||recorder?.recorder.state==='recording')return Promise.resolve();
 const sid=session.id,generation=captureGeneration;recordingState('connecting');
 captureTask=(async()=>{let capture;
  try{
   // This call stays in the user's click, before any await/network request.
   capture=await prepareCapture();
   const latest=await api('/api/v2/session/'+sid);
   if(finishing||generation!==captureGeneration||route!=='workspace'||session?.id!==sid||latest.status!=='active')return;
   if(recorder)await recorder.stop();
   if(finishing||generation!==captureGeneration||route!=='workspace'||session?.id!==sid)return;
   attachRecorder(capture);capture=null;record('reconnect',{segment:recorder.segment});
  }finally{capture?.stream.getTracks().forEach(t=>t.stop());captureTask=null;if(route==='workspace'&&session?.id===sid&&recorder?.recorder.state!=='recording')recordingState('stopped');}
 })();return captureTask;
}
const layout=()=>getSaved('lab-layout',{left:380,right:330,out:34,hideLeft:false,hideRight:false});
function applyLayout(){const l=layout(),ws=$('.ws');if(!ws)return;ws.style.setProperty('--left',l.left+'px');ws.style.setProperty('--right',l.right+'px');ws.style.setProperty('--out',l.out+'%');ws.classList.toggle('hide-left',!!l.hideLeft);ws.classList.toggle('hide-right',!!l.hideRight);}
function openWorkspace(s,capture){
 cleanup();finishing=false;stopTask=null;session=s;route='workspace';pid=s.problems[0].id;serverOffset=s.now-Date.now();perfStart=performance.now();elapsedBase=s.now-s.started;deadlineHandled=!!s.deadline_codes;document.body.classList.add('in-workspace');
 const training=s.mode==='training';
 $('#app').innerHTML=`<div class="ws" data-pane="code">
 <header class="ws-top">
  <div class="ws-left"><button type="button" class="icon-btn" data-action="toggle-left" aria-label="문제 패널 접기/펼치기" title="문제 패널">${icon('panelLeft',17)}</button><span class="mark small">c<b>/</b></span><span class="mode-chip ${s.mode}">${training?icon('spark',13):icon('target',13)}${modeLabel(s.mode)}</span></div>
  <nav class="problem-tabs" aria-label="문제">${s.problems.map((p,i)=>`<button type="button" class="problem-tab ${p.id===pid?'active':''}" data-problem="${p.id}"><span class="num">${i+1}</span><span class="t">${esc(p.title)}</span></button>`).join('')}</nav>
  <div class="ws-right"><span class="res-chip" title="외부 자료 사용 조건">${resourceLabel[s.resources]}</span><span id="record-status" class="rec-pill">녹화 재연결 필요</span><span id="save-status" class="save-status">기록 준비</span><div id="timer" class="timer" role="timer">${fmt(s.duration*60000)}</div>${btn('연습 종료','finish','sm finish-btn')}<button type="button" class="icon-btn" data-action="toggle-right" aria-label="코치 패널 접기/펼치기" title="코치 패널">${icon('panelRight',17)}</button></div>
  <div class="time-bar"><i id="time-progress"></i></div>
 </header>
 <aside id="recording-help" class="recording-help" role="status" hidden></aside>
 <nav class="ws-panes" aria-label="화면 전환"><button type="button" data-pane-tab="statement">${icon('file',15)} 문제</button><button type="button" class="on" data-pane-tab="code">${icon('code',15)} 코드</button><button type="button" data-pane-tab="coach">${icon(training?'spark':'pen',15)} ${training?'코치':'메모'}</button></nav>
 <div class="ws-body">
  <section class="ws-statement" aria-label="문제 설명"><div id="statement"></div></section>
  <div class="splitter v" data-split="left" role="separator" aria-orientation="vertical" aria-label="문제 패널 크기"></div>
  <section class="ws-code coding-pane">
   <div class="code-bar"><span class="file-name">${icon('file',14)} Solution.java <em>Java 21</em></span><span class="assist">${training?'자동완성 · 매개변수 안내 · 오류 표시':'기본 편집 지원'}</span><span id="code-status" class="code-status">준비됨</span>
    <div class="run-actions"><button type="button" class="btn sm" data-action="run" title="예제 실행 (Ctrl+')">${icon('play',13)} 실행<kbd>Ctrl '</kbd></button><button type="button" class="btn primary sm" data-action="submit" title="제출 · 전체 채점 (Ctrl+Shift+Enter)">${icon('send',13)} 제출</button></div></div>
   <div id="editor"></div>
   <div class="splitter h" data-split="out" role="separator" aria-orientation="horizontal" aria-label="결과 패널 크기"></div>
   <div id="run-output" class="run-output">${outputIdle('코드를 작성하고 실행해 보세요. 실행과 오류도 풀이 과정에 기록돼요.')}</div>
  </section>
  <div class="splitter v" data-split="right" role="separator" aria-orientation="vertical" aria-label="코치 패널 크기"></div>
  <aside id="workspace-coach" class="ws-coach inline-coach" aria-label="풀이 중 코치와 생각 기록"></aside>
 </div></div>`;
 applyLayout();
 editor=createEditor($('#editor'),{sid:s.id,pid,code:s.codes[pid],training,api,onChange:(code,changes)=>{s.codes[pid]=code;record('code',{code,changes});setCodeStatus('수정됨');},onCursor:position=>record('cursor',position),error:m=>toast('Java 편집 지원: '+m),commands:{run:()=>runSafe(false),submit:()=>runSafe(true)}});
 workspaceCoach=createWorkspaceCoach({el:$('#workspace-coach'),session:s,api,flush,record,waitJob:id=>watchJob(id,null,false),getPid:()=>pid,focusEditor:()=>editor?.editor.focus(),models:modelInfo.models});
 statement();$('#run-output').innerHTML=runOutput('코드를 작성하고 실행해 보세요. 실행과 오류도 풀이 과정에 기록돼요.');
 if(capture)attachRecorder(capture);
 else{recordingState('stopped');record('recording_gap',{reason:'새로고침 또는 연습 재진입 · 재연결 전까지 미디어 없음'});}
 clock=setInterval(updateClock,250);updateClock();save('lab-active',s.id);
}
function setCodeStatus(text,tone=''){const el=$('#code-status');if(el){el.textContent=text;el.dataset.tone=tone;}}
function outputIdle(text){return `<div class="out-head"><span>${icon('terminal',14)} 실행 결과</span></div><div class="out-idle"><p>${text}</p><p class="keys"><kbd>Ctrl</kbd><kbd>'</kbd> 예제 실행 · <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>Enter</kbd> 제출</p></div>`;}
function exampleValue(value){return typeof value==='string'?(value||'빈 문자열 (길이 0)'):JSON.stringify(value);}
function exampleInputs(params,args){return `<dl class="example-inputs">${params.map((p,i)=>`<dt>${esc(p.name)} <small>${esc(p.type)}</small></dt><dd><code>${esc(exampleValue(args[i]))}</code></dd>`).join('')}</dl>`;}
function statement(){
 const p=session.problems.find(p=>p.id===pid);
 $('#statement').innerHTML=`<div class="st-head">${level(p.level)}</div><h1>${esc(p.title)}</h1><p class="st-source">${esc(p.source)}${p.url?` · <a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer" data-resource="problem">원문 ${icon('external',12)}</a>`:''}</p>
 <div class="statement-text">${esc(p.description)}</div>
 <div class="signature"><span class="sig-label">메서드</span><code><b>${esc(p.returns)}</b> ${esc(p.method)}(${p.params.map(x=>`<i>${esc(x.type)}</i> ${esc(x.name)}`).join(', ')})</code></div>
 <h3>입출력 예시</h3><div class="examples">${p.tests.map((t,i)=>`<div class="example"><div class="ex-label">예제 ${i+1}</div>${exampleInputs(p.params,t.args)}<div class="example-result"><span>반환값</span><code>${esc(exampleValue(t.expected))}</code></div></div>`).join('')}</div>
 <p class="fine">공개 예제 ${p.tests.length}개 / 전체 ${p.test_count}개 · 앱 자체 테스트 기준</p>`;
}
async function updateClock(){
 if(route!=='workspace'||!session)return;const total=session.duration*60000,left=total-elapsed();
 const timer=$('#timer');timer.textContent=formatSessionClock(left);timer.title=elapsedBase>=86400000?'이전 연습을 이어 열었어요. 탭을 닫아 둔 시간도 경과 시간에 포함됩니다. 기록 시각은 그대로 보존합니다.':'';timer.classList.toggle('overtime',left<0);timer.classList.toggle('warn',left>=0&&left<=5*60000);
 const bar=$('#time-progress');if(bar){bar.style.width=Math.min(100,(1-left/total)*100)+'%';bar.parentElement.classList.toggle('over',left<0);}
 if(left<=0&&!deadlineHandled){deadlineHandled=true;if(session.mode==='exam'){finishing=true;captureGeneration++;editor.readonly();$$('.run-actions button').forEach(b=>b.disabled=true);try{await eventQueue;await stopRecorder();session=await api('/api/v2/session/'+session.id);toast('제한 시간이 끝났어요. 마감 코드가 자동 제출됩니다.');await openReview(session.id);}catch(e){finishing=false;recordingState('stopped','마감 기록을 보내지 못했어요. 연결을 확인한 뒤 연습 종료를 다시 눌러주세요.');toast(e.message);}}else{toast('제한 시간이 끝났어요. 이후 풀이는 추가 시간으로 구분해 기록해요.');api('/api/v2/session/'+session.id).then(s=>session.deadline_codes=s.deadline_codes).catch(e=>toast(e.message));}}
}
function stopRecorder(){captureGeneration++;return stopTask||=(async()=>{await workspaceCoach?.finish();if(recorder){const r=recorder;await r.stop();if(recorder===r)recorder=null;}await flush();})().finally(()=>{stopTask=null;});}
async function finish(){
 if(finishing)return;finishing=true;
 const used=fmt(Math.min(elapsed(),session.duration*60000));
 if(!await confirmBox({title:'연습을 종료할까요?',body:`${used} 동안 풀었어요. 종료하면 편집이 잠기고 복기 화면으로 이동해요.`,ok:'종료하고 복기하기'})){finishing=false;return;}
 const b=$('[data-action="finish"]');b.disabled=true;captureGeneration++;editor.readonly();
 try{await stopRecorder();session=await api('/api/v2/finish',{sid:session.id});await openReview(session.id);}
 catch(e){finishing=false;b.disabled=false;recordingState('stopped');toast(e.message);if(session.status==='active'&&!(deadlineHandled&&session.mode==='exam'))editor.editor.updateOptions({readOnly:false});}
}
function renderRun(r,stale=false){
 const tests=r.tests||[],ok=r.status==='passed',grader=r.status==='error',name=t=>`${t.public?'예제':'추가 테스트'} ${t.index}${t.label?` · ${esc(t.label)}`:''}`;
 const limits=r.limits?`시간 제한: 전체 ${r.limits.total_s}초(컴파일 포함) · 테스트당 ${r.limits.per_test_ms/1000}초 · 대입력 성능 테스트 각 ${r.limits.performance_test_ms/1000}초${r.limits.performance_budget_ms?`, 합계 ${r.limits.performance_budget_ms/1000}초`:''}`:'';
 return `<div class="out-head"><span>${icon('terminal',14)} ${r.submit?'전체 채점':'예제 실행'}</span><span class="result-badge ${ok?'pass':'fail'}">${grader?`${icon('alert',13)} 채점기 오류`:`${icon(ok?'check':'x',13)} ${r.passed} / ${r.total} 통과`}</span><span class="out-meta">${(r.duration_ms/1000).toFixed(2)}초 · 컴파일 포함</span></div>
 <div class="out-body">${stale?`<p class="fine stale-note">${icon('alert',12)} 이전 코드로 실행한 결과예요. 지금 코드는 그 뒤에 수정됐어요.</p>`:''}
 ${grader?`<p class="error-text">채점 환경 문제로 결과를 확인하지 못했어요. 풀이가 틀렸다는 뜻이 아니에요. 다시 실행하거나 설정에서 Java 실행 환경을 확인하세요.</p>`:''}
 <div class="test-strip">${tests.map(t=>`<i class="${t.passed?'pass':'fail'}" title="${name(t)}"></i>`).join('')}</div>
 ${r.error?`<pre class="error-block">${esc(r.error)}</pre>`:''}
 <div class="test-results">${tests.map(t=>`<details ${!t.passed&&t.public?'open':''}><summary><span class="test-dot ${t.passed?'pass':'fail'}"></span>${name(t)}<strong class="${t.passed?'pass':'fail'}">${t.passed?'통과':t.timeout?'시간 초과':'실패'}</strong>${t.time_ms!=null?`<small class="muted">${t.time_ms}ms</small>`:''}</summary>${t.public&&t.args?`<div class="io"><div><span>입력</span><code>${esc(JSON.stringify(t.args))}</code></div><div><span>예상</span><code>${esc(JSON.stringify(t.expected))}</code></div><div><span>출력</span><code class="${t.passed?'':'fail'}">${esc(JSON.stringify(t.actual))}</code></div></div>`:'<p class="muted">추가 테스트의 입력은 공개하지 않아요.</p>'}${t.error?`<pre class="error-block">${esc(t.error)}</pre>`:''}</details>`).join('')}</div>
 ${r.output?`<div class="console-label">표준 출력</div><pre class="console">${esc(r.output)}</pre>`:''}${limits?`<p class="fine">${limits}</p>`:''}</div>`;
}
async function watchJob(id,onProgress,track=true){if(track)currentJob=id;while(true){const j=await api('/api/v2/job/'+id);onProgress?.(j);if(j.status==='completed'){if(currentJob===id)currentJob=null;return j.result;}if(j.status==='failed'){if(currentJob===id)currentJob=null;throw new Error(j.error);}await new Promise(resolve=>setTimeout(resolve,1200));}}
// Run results belong to the session+problem that started them; a late response
// never paints another problem's panel, and each problem keeps its last result.
const runResults=new Map(),runPending=new Map();let runLock=false,runRequest=0;
const runKey=(sid,p)=>sid+'/'+p;
function runProgress(submit){return `<div class="out-head"><span>${icon('terminal',14)} ${submit?'전체 채점':'예제 실행'}</span></div><div class="job-progress"><span class="spinner"></span>Java 컴파일·실행 중…</div>`;}
function runOutput(idle){
 const key=runKey(session.id,pid),busy=runPending.get(key),done=runResults.get(key);
 if(busy){setCodeStatus('실행 중');return runProgress(busy.submit);}
 if(done?.error){setCodeStatus('준비됨');return outputIdle(`<span class="error-text">${esc(done.error)}</span>`);}
 if(done){const stale=session.codes[pid]!==done.code,ok=done.result.status==='passed';setCodeStatus(stale?'수정됨':ok?'테스트 통과':'결과 확인',stale?'':ok?'pass':'fail');return renderRun(done.result,stale);}
 setCodeStatus('준비됨');return outputIdle(idle);
}
function runSafe(submit){const b=$(`[data-action="${submit?'submit':'run'}"]`);if(!b||b.disabled||runLock)return;runLock=true;run(submit).catch(e=>toast(e.message)).finally(()=>runLock=false);}
async function run(submit){
 const ctx={id:++runRequest,sid:session.id,pid,gen:viewGeneration,submit},key=runKey(ctx.sid,ctx.pid);
 const sameView=()=>route==='workspace'&&session?.id===ctx.sid&&viewGeneration===ctx.gen;
 const visible=()=>sameView()&&pid===ctx.pid;
 $$('.run-actions button').forEach(b=>b.disabled=true);runPending.set(key,ctx);$('#run-output').innerHTML=runOutput('');
 if(matchMedia('(max-width: 900px)').matches)$('#run-output').scrollIntoView({block:'nearest'});
 try{await flush();const {job_id}=await api('/api/v2/run',{sid:ctx.sid,pid:ctx.pid,submit});const entry=await watchJob(job_id);runResults.set(key,{result:entry.result,code:entry.code});}
 catch(e){runResults.set(key,{error:e.message});throw e;}
 finally{
  if(runPending.get(key)?.id===ctx.id)runPending.delete(key);
  if(visible())$('#run-output').innerHTML=runOutput('');
  if(sameView()&&session.status==='active'&&!(session.mode==='exam'&&deadlineHandled))$$('.run-actions button').forEach(b=>b.disabled=false);
 }
}
function switchProblem(next){record('switch',{from:pid,to:next});pid=next;editor.set(pid,session.codes[pid]);workspaceCoach?.switchProblem();$$('.problem-tab').forEach(b=>b.classList.toggle('active',b.dataset.problem===pid));statement();$('#run-output').innerHTML=runOutput('이 문제의 코드를 실행해 보세요.');}
function startSplit(e,handle){
 const ws=$('.ws'),kind=handle.dataset.split,rect=ws.getBoundingClientRect(),code=$('.ws-code').getBoundingClientRect();
 handle.setPointerCapture(e.pointerId);document.body.classList.add('resizing');
 const move=ev=>{const l=layout();if(kind==='left')l.left=Math.round(Math.min(640,Math.max(260,ev.clientX-rect.left)));if(kind==='right')l.right=Math.round(Math.min(560,Math.max(260,rect.right-ev.clientX)));if(kind==='out')l.out=Math.round(Math.min(75,Math.max(12,(code.bottom-ev.clientY)/code.height*100)));save('lab-layout',l);applyLayout();};
 const up=()=>{handle.removeEventListener('pointermove',move);document.body.classList.remove('resizing');};
 handle.addEventListener('pointermove',move);handle.addEventListener('pointerup',up,{once:true});handle.addEventListener('pointercancel',up,{once:true});
}

/* ───────────── Import problem ───────────── */
const javaTypes=['int','long','double','boolean','String','int[]','long[]','String[]','int[][]','boolean[]'];
function caseRow(){return `<div class="import-case"><label class="field"><span>입력 인자</span><textarea class="case-args mono" rows="2" placeholder='예: [[1,2,3], 3]'>[[1,2,3]]</textarea></label><label class="field"><span>기대 출력</span><textarea class="case-expected mono" rows="2" placeholder='예: 6 또는 "정답"'>6</textarea></label><div class="case-side"><label class="inline-check"><input type="checkbox" class="case-public" checked> 예제로 공개</label><button type="button" class="icon-btn" data-action="remove-case" aria-label="테스트 삭제">${icon('x',15)}</button></div></div>`;}
function importDialog(){showModal(`<div class="sheet-head"><div><p class="eyebrow">Bring your own problem</p><h2>문제 가져오기</h2></div><button type="button" class="icon-btn" data-action="close" aria-label="닫기">${icon('x',18)}</button></div>
 <div class="sheet-body"><div class="sheet-col">
  <p class="muted">프로그래머스 문제 설명과 예제를 옮겨서 이 연습실에서 풀 수 있어요.</p>
  <div class="two-fields"><label class="field"><span>문제 이름</span><input id="import-title" maxlength="200" placeholder="문제 이름"></label><label class="field"><span>난이도</span><select id="import-level">${[0,1,2,3,4,5].map(n=>`<option value="${n}" ${n===2?'selected':''}>Lv.${n}</option>`).join('')}</select></label></div>
  <label class="field"><span>원문 링크</span><input id="import-url" type="url" placeholder="https://school.programmers.co.kr/..."></label>
  <label class="field"><span>문제 설명 · 제한 조건</span><textarea id="import-description" rows="7" placeholder="문제 설명과 제한 조건을 붙여 넣으세요."></textarea></label>
  <label class="field"><span>문제 유형 <small>쉼표로 구분</small></span><input id="import-tags" value="해시" placeholder="해시, 배열·문자열"></label>
 </div><div class="sheet-col">
  <div class="two-fields"><label class="field"><span>반환형</span><select id="import-return">${javaTypes.map(t=>`<option>${t}</option>`).join('')}</select></label><label class="field"><span>메서드 이름</span><input id="import-method" value="solution"></label></div>
  <label class="field"><span>매개변수</span><input id="import-params" value="int[] numbers" placeholder="예: int[] numbers, int target"></label>
  <div class="field"><span class="field-row">연습 시작 코드<button type="button" class="text-btn" data-action="generate-starter">${icon('code',13)} 형식으로 만들기</button></span><textarea id="import-starter" class="mono" rows="7">class Solution {
    public int solution(int[] numbers) {
        return 0;
    }
}</textarea></div>
  <div class="field"><span class="field-row">예제와 추가 테스트<button type="button" class="text-btn" data-action="add-case">${icon('plus',13)} 테스트</button></span><p class="fine">인자를 순서대로 배열에 넣으세요. 배열 인자 하나는 [[1,2,3]]처럼 입력해요. 공개를 끄면 제출 때만 쓰는 추가 테스트가 돼요.</p><div id="import-cases">${caseRow()}</div></div>
 </div></div>
 <div class="sheet-foot"><p class="fine">기대 출력은 직접 확인해 주세요. 지원 타입: ${javaTypes.join(', ')}</p><div class="foot-actions">${btn('문제 등록','save-problem','primary lg')}</div></div>`,'sheet');}
function importParams(){return $('#import-params').value.split(',').map(x=>x.trim()).filter(Boolean).map(x=>{const m=x.match(/^(\S+)\s+([a-zA-Z_][a-zA-Z0-9_]*)$/);if(!m)throw new Error('매개변수를 Java 형식으로 입력하세요. 예: int[] numbers, int target');return {type:m[1],name:m[2]};});}
function importData(){const returns=$('#import-return').value;return {title:$('#import-title').value,url:$('#import-url').value,level:Number($('#import-level').value),description:$('#import-description').value,tags:$('#import-tags').value.split(',').map(x=>x.trim()).filter(Boolean),returns,method:$('#import-method').value,params:importParams(),starter:$('#import-starter').value,tests:$$('.import-case').map(row=>{const raw=$('.case-expected',row).value.trim();return {args:JSON.parse($('.case-args',row).value),expected:returns==='String'&&!raw.startsWith('"')?raw:JSON.parse(raw),public:$('.case-public',row).checked};})};}

/* ───────────── Review ───────────── */
const eventLabels={start:'연습 시작',code:'코드 수정',run:'예제 실행',submit:'제출',result:'실행 결과',switch:'문제 전환',hint:'힌트 사용',deadline:'제한 시간 종료',finish:'연습 종료',recording_gap:'기록 중단',note:'생각 메모',recording_start:'녹화 시작',recording_stop:'녹화 종료',paste:'붙여넣기',blur:'앱 포커스 이탈',focus:'앱 복귀',coach:'복기 대화'};
const markerKinds={run:'run',submit:'submit',result:'result',hint:'hint',note:'note',recording_gap:'gap',deadline:'deadline',switch:'switch',paste:'paste'};
function reportHtml(report){if(!report)return '';return `<section class="report card"><p class="eyebrow accent">${icon('flag',13)} Your next step</p><h2>이번 연습에서 다음 연습으로</h2><p class="report-summary">${esc(report.summary)}</p>
 ${(report.process_review||[]).length?`<h3>풀이 흐름에서 찾은 점</h3>${report.process_review.map(p=>`<article class="finding"><div class="finding-top"><button type="button" class="time-link" data-seek="${p.start_ms}">${fmt(p.start_ms)}–${fmt(p.end_ms)}</button><strong>${esc(p.stage)}</strong></div><p>${esc(p.observation)}</p><p class="muted">${esc(p.interpretation)}</p></article>`).join('')}`:''}
 ${(report.strengths||[]).length?`<h3>계속 유지할 점</h3><ul class="good-list">${report.strengths.map(x=>`<li>${icon('check',14)}<span>${esc(x)}</span></li>`).join('')}</ul>`:''}
 ${(report.weaknesses||[]).length?`<h3>우선 고칠 점</h3>`:''}${(report.weaknesses||[]).map(w=>`<article class="finding"><div class="finding-top">${w.priority?tag('우선순위 '+w.priority):''}${tag(w.skill||w.tag,'violet')}${tag('확신 '+w.confidence)}<button type="button" class="time-link" data-seek="${w.evidence_ms}">${icon('play',11)} ${fmt(w.evidence_ms)}</button></div><h4>${esc(w.observation)}</h4><dl><dt>가능한 원인</dt><dd>${esc(w.hypothesis)}</dd><dt>다음 행동</dt><dd>${esc(w.next_action)}</dd></dl></article>`).join('')}
 ${report.progress_comparison?`<h3>지난 연습과 비교</h3><p>${esc(report.progress_comparison)}</p>`:''}
 <h3>다음 훈련</h3>${(report.practice_plan||[]).length?report.practice_plan.map(p=>`<article class="finding"><div class="finding-top">${tag(p.minutes+'분')}<strong>${esc(p.target)}</strong></div><p>${esc(p.exercise)}</p><dl><dt>성공 기준</dt><dd>${esc(p.success_criteria)}</dd><dt>다른 문제에서 확인</dt><dd>${esc(p.transfer_check)}</dd></dl></article>`).join(''):`<ol class="next-list">${(report.next_practice||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ol>`}
 ${(report.limitations||[]).length?`<details class="usage"><summary>분석에 사용한 자료와 한계</summary><ul>${report.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details>`:''}<p class="fine">이번 세션의 관찰이에요. 재풀이와 비슷한 새 문제에서 다시 확인하세요.</p></section>`;}
function messagesHtml(){
 const list=session.messages.filter(m=>m.kind!=='hint');
 if(!list.length)return `<div class="chat-empty">${icon('chat',20)}<p><strong>분석 시작</strong>을 누르면 Codex가 기록을 보고 당시 판단을 묻는 질문부터 해요. 답변을 주고받은 뒤 <strong>최종 보고서</strong>를 만들어요.</p></div>`;
 return list.map(m=>`<article class="chat-entry">${m.question?`<div class="user-message">${esc(m.question)}</div>`:''}<div class="coach-message"><div class="chat-meta"><span class="coach-avatar sm">${icon('spark',12)}</span>Codex <span>${esc(m.model)} · ${esc(m.effort)}</span></div><div class="chat-text">${esc(m.answer.message)}</div>${(m.answer.questions||[]).map(q=>`<div class="coach-question"><button type="button" class="time-link" data-seek="${q.ms}">${icon('play',11)} ${fmt(q.ms)}</button><span>${esc(q.question)}</span></div>`).join('')}${m.usage?`<details class="usage"><summary>사용량</summary><pre>${esc(JSON.stringify(m.usage,null,2))}</pre></details>`:''}</div></article>`).join('');
}
function reviewEnd(){return Math.max(1000,(session.ended||session.started)-session.started,...session.events.filter(e=>e.kind!=='coach').map(e=>e.ms));}
function timelineMarkers(end){
 const pct=ms=>Math.min(100,Math.max(0,ms/end*100));
 const events=session.events.filter(e=>markerKinds[e.kind]).map(e=>{let tone=markerKinds[e.kind];if(e.kind==='result')tone=e.payload.passed===e.payload.total?'pass':'fail';const title=`${fmt(e.ms)} ${eventLabels[e.kind]||e.kind}${e.kind==='result'?` ${e.payload.passed}/${e.payload.total}`:''}${e.kind==='note'?' · '+(e.payload.text||''):''}`;return `<button type="button" class="mk ${tone}" style="left:${pct(e.ms)}%" data-seek="${e.ms}" data-event-pid="${esc(e.pid)}" title="${esc(title)}" aria-label="${esc(title)}"></button>`;});
 const hints=session.messages.filter(m=>m.kind==='hint').map(m=>{const ms=m.at-session.started;return `<button type="button" class="mk hint" style="left:${pct(ms)}%" data-seek="${ms}" title="${fmt(ms)} 힌트" aria-label="${fmt(ms)} 힌트"></button>`;});
 const speech=session.transcripts.map(t=>`<i class="speech" style="left:${pct(t.start_ms)}%;width:${Math.max(.4,pct(t.end_ms)-pct(t.start_ms))}%" title="${esc(t.text)}"></i>`);
 const gaps=recordingGaps().map(g=>`<button type="button" class="recording-gap-bar" style="left:${pct(g.start_ms)}%;width:${Math.max(.4,pct(g.end_ms)-pct(g.start_ms))}%" data-seek="${g.start_ms}" title="${gapTime(g.start_ms)}–${gapTime(g.end_ms)} ${gapLabels[g.kind]||'녹화 범위 확인 필요'}" aria-label="${gapTime(g.start_ms)}–${gapTime(g.end_ms)} ${gapLabels[g.kind]||'녹화 범위 확인 필요'}"></button>`);
 return `<div class="tl-lane events">${events.join('')}${hints.join('')}</div><div class="tl-lane speech-lane">${speech.join('')}</div><div class="tl-lane gap-lane">${gaps.join('')}</div>`;
}
const gapLabels={not_recorded:'녹화되지 않은 시간',missing:'녹화 조각 누락',unconfirmed:'녹화 범위 확인 필요'};
const gapTime=ms=>`${fmt(ms)}.${Math.floor(ms%1000/100)}`;
function screenRecordings(){return session.media.filter(m=>m.kind==='screen').sort((a,b)=>a.start_ms-b.start_ms);}
function recordingGaps(){
 if(session.recording_gaps)return session.recording_gaps;
 // Compatibility with an already running older server: no invented end time.
 return screenRecordings().filter(m=>m.status!=='complete').map((m,i,list)=>({start_ms:m.start_ms,end_ms:screenRecordings().find(x=>x.start_ms>m.start_ms)?.start_ms||reviewEnd(),kind:'unconfirmed'}));
}
function renderRecordingSegments(){
 const el=$('#recording-segments');if(!el)return;
 const screens=screenRecordings();
 el.innerHTML=`<div class="recording-toolbar"><strong>녹화 ${screens.length}구간</strong>${screens.some(m=>m.playable)?btn('처음부터 이어보기','replay-all','sm'):''}${btn('미전송 기록 다시 확인','refresh-recordings','sm ghost')}</div><p class="fine">각 구간을 시간순으로 이어 재생해요. 녹화되지 않은 시간은 건너뛰며 복구된 영상으로 표시하지 않아요.</p><div class="recording-segment-list">${screens.map((m,i)=>`<button type="button" class="chip" data-seek="${m.start_ms}" ${m.playable?'':'disabled'}>구간 ${i+1} · ${fmt(m.start_ms)}–${m.end_ms==null?'종료 미확인':fmt(m.end_ms)}${m.status==='complete'?'':' · '+esc(recordingStates[m.status]||m.status)}</button>`).join('')}</div>${recordingGaps().map(g=>`<button type="button" class="recording-gap-link" data-seek="${g.start_ms}">${gapTime(g.start_ms)}–${gapTime(g.end_ms)} · ${gapLabels[g.kind]||'녹화 범위 확인 필요'}</button>`).join('')}`;
}
function setupPlayback(){
 const video=$('#replay-video');if(!video)return;
 playback=createSegmentPlayback({video,segments:screenRecordings(),onTime:ms=>seek(ms,true),onDuration:(id,end)=>{const media=session.media.find(m=>m.segment===id);if(media){media.playback_end_ms=end;}},onState:info=>{
  const gap=$('#replay-gap'),state=$('#playback-state');if(!gap||!state)return;
  gap.hidden=!['gap','error'].includes(info.state);
  if(info.state==='gap'||info.state==='error')gap.innerHTML=`<div><strong>${info.state==='error'?'이 녹화 구간을 재생하지 못했어요.':'이 시각에 재생할 녹화가 없어요.'}</strong><p>코드와 사건 기록은 아래 시간축에서 계속 볼 수 있어요.</p>${info.next?btn('다음 녹화부터 보기','replay-next','sm'):''}</div>`;
  if(info.state==='transition')state.textContent=info.gapStart==null?`${fmt(info.next.start_ms)}의 다음 녹화로 이동했어요. 앞 구간의 종료 시각은 확인할 수 없어요.`:`${gapTime(info.gapStart)}–${gapTime(info.gapEnd)}의 녹화 없는 시간을 건너뛰고 다음 구간을 재생해요.`;
  else if(info.state==='finished')state.textContent='저장된 녹화 구간을 모두 재생했어요.';
  else if(info.state==='paused')state.textContent='재생 버튼을 눌러 이어서 보세요.';
 }});
}
const recordingStates={pending:'업로드 대기 중인 조각이 있어요',missing:'조각이 누락됐어요',unconfirmed:'종료 확인이 없어 끝부분 누락 여부를 알 수 없어요',recording:'녹화 종료가 아직 기록되지 않았어요'};
function recordingNotice(){
 const list=session.media.filter(m=>m.kind==='screen'&&(m.status!=='complete'||m.ended==='interrupted'));if(!list.length)return '';
 return `<div class="recording-notice">${list.map(m=>`<p class="fine error-text">${icon('alert',12)} ${fmt(m.start_ms)} 녹화: ${m.status==='complete'?'중간에 중단됐어요 (저장된 조각은 모두 있어요)':esc(recordingStates[m.status]||m.status)}${m.missing_count?` · 누락 ${m.missing_count}개${m.expected_chunks!=null?` / 전체 ${m.expected_chunks}개`:''}`:''}${m.playable?'':' · 재생할 수 없어요'}</p>`).join('')}<p class="fine">미전송 기록이 남은 브라우저에서 이 기록을 다시 열면 자동으로 보내고 상태를 다시 계산해요.</p></div>`;
}
function metricsHtml(){const grading=session.jobs?.some(j=>j.kind==='java'&&['queued','running'].includes(j.status));return (session.metrics||[]).map(m=>{const candidate=session.retry_candidates?.find(c=>c.pid===m.pid),unknown=candidate?.code==='GRADE_UNAVAILABLE';return `<div class="metric"><div class="metric-title"><strong>${esc(m.title)}</strong><span class="status ${m.passed_in_time?'pass':'fail'}">${grading?'채점 중':unknown?'채점 결과 확인 불가':m.passed_in_time?'시간 내 통과':'미통과 / 제출 없음'}</span></div><div class="metric-nums"><div><span>사용 시간</span><b>${m.minutes}<small>분</small></b></div><div><span>첫 실행</span><b>${m.first_run_ms===null?'—':fmt(m.first_run_ms)}</b></div><div><span>실행</span><b>${m.runs}<small>회</small></b></div><div><span>힌트</span><b>${m.hints}<small>회</small></b></div></div></div>`;}).join('');}
async function openReview(id,intent=navigationGeneration){
 if(intent!==navigationGeneration)return;
 cleanup();const generation=navigationGeneration;let s;
 try{s=await api('/api/v2/session/'+id);}catch(error){if(generation===navigationGeneration)throw error;return;}
 if(generation!==navigationGeneration)return;
 session=s;route='review';serverOffset=session.now-Date.now();save('lab-active',null);document.body.classList.add('in-review');
 const end=reviewEnd();pid=session.problems[0].id;reviewTime=0;const hints=session.messages.filter(m=>m.kind==='hint');const hasVideo=session.media.some(m=>m.kind==='screen'&&m.playable);
 $('#app').innerHTML=`<div class="review">
 <header class="rv-top"><button type="button" class="btn ghost sm" data-action="review-home">${icon('left',14)} 대시보드</button><div class="rv-title"><h1>${esc(session.title)}</h1><div class="rv-meta"><span class="mode-chip ${session.mode}">${modeLabel(session.mode)}</span><span>${date(session.started)}</span><span>${session.duration}분</span><span>${resourceLabel[session.resources]}</span></div></div><div class="rv-actions">${btn(`${icon('mic',14)} 음성 전사`,'transcribe','sm')}${btn(`${icon('download',14)} 기록 JSON`,'export-evidence','sm ghost')}<button type="button" class="icon-btn" data-action="theme" aria-label="테마 전환">${icon(theme()==='light'?'moon':'sun',17)}</button></div></header>
 <div class="rv-body">
  <div class="rv-main">
   <section class="metrics" aria-label="풀이 과정에서 확인한 사실">${metricsHtml()}</section>
   <div id="retry-registrations">${session.problems.map((p,index)=>`<div id="retry-registration-${index}"></div>`).join('')}</div>
   ${session.retry?'<div id="retry-comparison"></div>':''}
   <section class="card player"><div class="video-box" id="video-box">${hasVideo?'<video id="replay-video" controls preload="metadata"></video><div id="replay-gap" class="replay-gap" hidden></div>':empty('저장된 녹화가 없어요','브라우저의 미전송 기록을 복구하거나 코드 이력으로 복기할 수 있어요.')}</div>
    <div class="timeline"><div class="tl-head"><span id="replay-clock" class="tl-clock">00:00</span><span class="tl-end">/ ${fmt(end)}</span><div class="tl-legend"><span><i class="run"></i>실행</span><span><i class="submit"></i>제출</span><span><i class="pass"></i>통과</span><span><i class="fail"></i>실패</span><span><i class="hint"></i>힌트</span><span><i class="note"></i>메모</span><span><i class="speech"></i>발언</span></div></div>
     <div class="tl-track"><div id="tl-markers">${timelineMarkers(end)}</div><input type="range" id="timeline" min="0" max="${end}" value="0" step="1000" aria-label="연습 시간축"><div class="tl-fill" id="tl-fill"></div></div></div>
    <div class="replay-tools"><select id="replay-problem" aria-label="다시 볼 문제">${session.problems.map(p=>`<option value="${p.id}">${esc(p.title)}</option>`).join('')}</select>${btn(`${icon('camera',14)} 이 장면 첨부`,'frame','sm')}<div id="frame-list" class="frame-list"></div></div>
    <div id="recording-segments" class="recording-segments"></div><p id="playback-state" class="fine" role="status"></p>
    <div id="replay-caption" class="replay-caption">시각을 선택하면 그때의 코드와 발언을 확인할 수 있어요.</div><div id="recording-notice">${recordingNotice()}</div></section>
   <section class="card tabs-card"><div class="tabs" role="tablist">${[['code','그때의 코드','code'],['events','사건 기록','history'],['speech','말로 남긴 생각','mic'],...(hints.length?[['hints',`힌트 ${hints.length}`,'bulb']]:[])].map(([k,t,i],n)=>`<button type="button" role="tab" class="${n===0?'on':''}" data-tab="${k}">${icon(i,14)} ${t}</button>`).join('')}</div>
    <div data-panel="code"><pre id="replay-code" class="replay-code"></pre><div id="replay-result"></div></div>
    <div data-panel="events" hidden><div class="panel-tools"><label class="inline-check"><input type="checkbox" id="show-edits"> 코드 수정 포함</label></div><div id="event-list" class="event-list"></div></div>
    <div data-panel="speech" hidden><div class="panel-tools"><p class="muted">로컬 전사 결과예요. 틀린 인식은 직접 고칠 수 있어요.</p>${btn('전사문 저장','save-transcript','sm')}</div><div id="transcript-list"></div></div>
    ${hints.length?`<div data-panel="hints" hidden>${hints.map(m=>`<div class="hint-row"><button type="button" class="time-link" data-seek="${m.at-session.started}">${icon('play',11)} ${fmt(m.at-session.started)}</button><div>${m.question?`<p class="muted">${esc(m.question)}</p>`:''}<p>${esc(m.answer.message)}</p></div></div>`).join('')}</div>`:''}
   </section>
  </div>
  <aside class="rv-side">
   <div id="report-area">${reportHtml(session.report)}</div>
   <section class="card coach-card"><div class="card-head"><div class="coach-title"><span class="coach-avatar">${icon('spark',15)}</span><div><h2>Codex와 함께 복기</h2><span class="muted">질문 → 답변 → 보고서</span></div></div></div>
    <details class="model-box" ${session.model?'':'open'}><summary><span>${icon('spark',14)} 모델</span><span id="review-model-summary" class="muted">${esc(session.model?`${session.model} · ${session.effort}`:'선택 필요')}</span></summary>${modelFields('review-')}</details>
    <div class="coach-actions">${btn(`${icon('spark',14)} 분석 시작`,'analyze','primary')}${btn(`${icon('flag',14)} 최종 보고서`,'report')}</div>
    <div id="coach-progress"></div>
    <div id="chat-messages" class="chat">${messagesHtml()}</div>
    <div class="reply-box"><label for="reply" class="sr-only">나의 복기 답변</label><textarea id="reply" rows="3" maxlength="20000" placeholder="코치의 질문에 답하거나 당시 의도를 설명해 주세요."></textarea><div class="reply-actions">${btn(`${icon('mic',14)} 음성으로 답하기`,'voice','sm ghost')}${btn(`답변 보내기 ${icon('send',13)}`,'reply','primary sm')}</div></div>
    <p class="fine">분석할 때 코드·로그·전사문·첨부 장면이 Codex에 전달돼요. 영상 전체를 자동으로 해석하지는 않아요.</p></section>
  </aside>
 </div></div>`;
 fillEfforts('review-');renderEvents();renderTranscripts();setupPlayback();renderRecordingSegments();seek(0);
 await refreshRetryReview();
 if(generation!==navigationGeneration||route!=='review'||session?.id!==id)return;
 if(session.jobs.some(j=>j.status==='running'||j.status==='queued'))pollReviewJobs();
}
function refreshMarkers(){const el=$('#tl-markers');if(el)el.innerHTML=timelineMarkers(reviewEnd());}
async function pollReviewJobs(){
 const sid=session.id,jobs=session.jobs.filter(j=>j.status==='running'||j.status==='queued');if(!jobs.length)return;
 $('#coach-progress').innerHTML='<div class="job-progress"><span class="spinner"></span>마감 채점 또는 저장된 작업을 마무리하는 중이에요…</div>';const gen=viewGeneration;
 poll=setTimeout(async()=>{try{
  const s=await api('/api/v2/session/'+sid);if(route!=='review'||viewGeneration!==gen||session?.id!==sid)return;
  const hadJava=session.jobs.some(j=>j.kind==='java'&&['running','queued'].includes(j.status));session=s;
  const javaPending=s.jobs.some(j=>j.kind==='java'&&['running','queued'].includes(j.status));
  if(hadJava&&!javaPending){$('.metrics').innerHTML=metricsHtml();await refreshRetryReview();}
  if(route!=='review'||viewGeneration!==gen||session?.id!==sid)return;
  if(s.jobs.some(j=>j.status==='running'||j.status==='queued'))pollReviewJobs();
  else{$('#coach-progress').innerHTML='';$('.metrics').innerHTML=metricsHtml();if(!hadJava)await refreshRetryReview();if(route!=='review'||viewGeneration!==gen||session?.id!==sid)return;refreshMarkers();seek(reviewTime);}
 }catch(e){if(route==='review'&&viewGeneration===gen&&session?.id===sid)toast(e.message);}},1600);
}
function renderEvents(){const edits=$('#show-edits')?.checked;const events=session.events.filter(e=>eventLabels[e.kind]&&(edits||e.kind!=='code'));$('#event-list').innerHTML=events.map(e=>{const tone=e.kind==='result'?(e.payload.passed===e.payload.total?'pass':'fail'):markerKinds[e.kind]||'';return `<button type="button" class="event ${e.kind==='recording_gap'?'warning':''}" data-seek="${e.ms}" data-event-pid="${esc(e.pid)}"><time>${fmt(e.ms)}</time><i class="ev-dot ${tone}"></i><span>${eventLabels[e.kind]}</span><small>${esc(e.kind==='result'?`${e.payload.passed}/${e.payload.total} 통과`:e.kind==='note'?e.payload.text:session.problems.find(p=>p.id===e.pid)?.title||'')}</small></button>`;}).join('')||'<p class="muted">표시할 사건이 없어요.</p>';}
function renderTranscripts(){const t=session.transcripts;$('#transcript-list').innerHTML=t.length?t.map((s,i)=>`<div class="transcript-row"><button type="button" class="time-link" data-seek="${s.start_ms}">${icon('play',11)} ${fmt(s.start_ms)}</button><textarea data-transcript="${i}" rows="2" aria-label="${fmt(s.start_ms)} 전사문">${esc(s.text)}</textarea></div>`).join(''):'<div class="chat-empty">'+icon('mic',20)+'<p>위의 <strong>음성 전사</strong>를 누르면 녹음을 이 컴퓨터에서 텍스트로 바꿔요. 음성이 없으면 빈 결과가 나올 수 있어요.</p></div>';}
function showTab(name){$$('[data-tab]').forEach(b=>b.classList.toggle('on',b.dataset.tab===name));$$('[data-panel]').forEach(p=>p.hidden=p.dataset.panel!==name);}
function seek(ms,fromVideo=false){
 reviewTime=Number(ms);$('#timeline').value=String(ms);$('#replay-clock').textContent=fmt(ms);$('#tl-fill').style.width=(reviewTime/Number($('#timeline').max)*100)+'%';
 const problem=$('#replay-problem').value;let code=session.problems.find(p=>p.id===problem).starter;for(const e of session.events){if(e.ms>ms)break;if(e.pid===problem&&['code','run','submit'].includes(e.kind)&&e.payload.code!==undefined)code=e.payload.code;}$('#replay-code').textContent=code;
 const transcript=session.transcripts.filter(t=>t.start_ms<=ms&&t.end_ms>=ms);const caption=$('#replay-caption');caption.textContent=transcript.map(t=>t.text).join(' ')||'이 구간에 연결된 발언이 없어요.';caption.classList.toggle('has-speech',!!transcript.length);
 const runs=session.runs.filter(r=>r.pid===problem&&r.ms<=ms);$('#replay-result').innerHTML=runs.length?`<details class="plain"><summary>이 시각 이전의 최근 실행 결과</summary><div class="run-output inline">${renderRun(runs.at(-1).result)}</div></details>`:'';
 if(!fromVideo)playback?.seek(Number(ms));
}
function coachBusy(on){$$('.coach-actions button,.reply-actions button').forEach(b=>b.disabled=on);}
async function coach(kind){
 if(currentJob&&['analyze','reply','report'].includes(kind))throw new Error('진행 중인 작업이 끝난 뒤 요청하세요.');const settings=selectedModel('review-');if(!settings.model||!settings.effort){$('.coach-card .model-box').open=true;throw new Error('모델과 추론 수준을 선택하세요.');}if(kind==='reply'&&!$('#reply').value.trim())throw new Error('복기 답변을 입력해 주세요.');
 const message=['reply','report'].includes(kind)?$('#reply').value:'';const {job_id}=await api('/api/v2/chat',{sid:session.id,kind,message,...settings});coachBusy(true);
 $('#coach-progress').innerHTML=`<div class="job-progress"><span class="spinner"></span><span class="jp-text">Codex가 기록을 살펴보고 있어요…</span>${btn('중단','cancel','sm ghost')}</div>`;
 try{await watchJob(job_id,j=>{const t=$('#coach-progress .jp-text');if(t)t.textContent=j.text&&!j.text.trimStart().startsWith('{')?j.text:'복기 응답을 작성하고 있어요…';});session=await api('/api/v2/session/'+session.id);$('#chat-messages').innerHTML=messagesHtml();$('#report-area').innerHTML=reportHtml(session.report);if(['reply','report'].includes(kind))$('#reply').value='';renderEvents();renderTranscripts();const c=$('#chat-messages');c.scrollTop=c.scrollHeight;if(kind==='report')$('#report-area').scrollIntoView({behavior:'smooth',block:'start'});}
 finally{$('#coach-progress').innerHTML='';coachBusy(false);}
}
async function transcribe(){
 if(currentJob)throw new Error('진행 중인 작업이 끝난 뒤 실행하세요.');
 const b=$('[data-action="transcribe"]'),label=b.innerHTML;b.disabled=true;b.innerHTML='<span class="spinner"></span> 로컬 음성 전사 중…';coachBusy(true);
 try{const {job_id}=await api('/api/v2/transcribe',{sid:session.id});await watchJob(job_id);session=await api('/api/v2/session/'+session.id);renderTranscripts();refreshMarkers();showTab('speech');seek(reviewTime);toast(session.transcription_state?.detail||`전사 ${session.transcripts.length}구간을 저장했어요.`);}
 finally{b.disabled=false;b.innerHTML=label;coachBusy(false);}
}
async function voiceReply(){const b=$('[data-action="voice"]');if(voice){b.disabled=true;b.textContent='음성 인식 중…';try{const segment=await voice.stop();voice=null;await drain();const {job_id}=await api('/api/v2/transcribe',{sid:session.id,kind:'voice',segment});const result=await watchJob(job_id);$('#reply').value+=($('#reply').value?'\n':'')+result.text;toast('인식된 내용을 확인·수정한 뒤 답변을 보내세요.');}finally{b.disabled=false;b.classList.remove('recording');b.innerHTML=`${icon('mic',14)} 음성으로 답하기`;}}else{const stream=await navigator.mediaDevices.getUserMedia({audio:true});voice=makeRecorder(stream,{sid:session.id,kind:'voice',start:0,status:()=>{}});b.classList.add('recording');b.innerHTML=`${icon('stop',14)} 녹음 종료 · 텍스트로`;}}

/* ───────────── Actions & events ───────────── */
function toggleTheme(next=theme()==='light'?'dark':'light'){setTheme(next);syncEditorTheme();$$('[data-action="theme"]').forEach(b=>b.innerHTML=icon(next==='light'?'moon':'sun',17));$$('[data-set-theme]').forEach(b=>b.classList.toggle('on',b.dataset.setTheme===next));}
async function action(name,t){
 if(name==='retry-start')return startRetry();
 if(name==='retry-home')return navigate('home');
 if(name==='close'){if(voice)throw new Error('녹음을 먼저 종료해 주세요.');$('#modal').close();return;}
 if(name==='setup')return setup();if(name==='start')return start();if(name==='finish')return finish();if(name==='run')return runSafe(false);if(name==='submit')return runSafe(true);if(name==='import')return importDialog();
 if(name==='theme')return toggleTheme();
 if(name==='toggle-left'||name==='toggle-right'){const l=layout(),k=name==='toggle-left'?'hideLeft':'hideRight';l[k]=!l[k];save('lab-layout',l);applyLayout();return;}
 if(name==='add-case'){$('#import-cases').insertAdjacentHTML('beforeend',caseRow());return;}
 if(name==='remove-case'){t.closest('.import-case').remove();return;}
 if(name==='generate-starter'){const type=$('#import-return').value,params=importParams();const value=({int:'0',long:'0L',double:'0.0',boolean:'false',String:'""'})[type]||('new '+type.replace('[]','[0]'));$('#import-starter').value=`import java.util.*;\n\nclass Solution {\n    public ${type} ${$('#import-method').value}(${params.map(p=>p.type+' '+p.name).join(', ')}) {\n        return ${value};\n    }\n}`;return;}
 if(name==='save-problem'){await api('/api/v2/import-problem',importData());$('#modal').close();await navigate('library');toast('문제를 등록했어요.');return;}
 if(name==='models'){t.disabled=true;await loadModels();settings();return;}
 if(name==='setup-models'){t.disabled=true;await loadModels();$('#setup-model-fields').innerHTML=modelFields('setup-');fillEfforts('setup-');t.disabled=false;if(modelError)toast(modelError);return;}
 if(name==='save-model'){const m=selectedModel('settings-');if(!m.model)throw new Error('모델을 선택하세요.');toast('모델·추론 수준을 저장했어요.');return;}
 if(name==='retry-outbox'){await drain();toast('미전송 기록을 다시 보냈어요.');return;}
 if(name==='reconnect')return reconnectRecording();
 if(name==='replay-all')return playback?.first();
 if(name==='replay-next')return playback?.next(reviewTime);
 if(name==='refresh-recordings'){await drain();return openReview(session.id);}
 if(name==='review-home'){if(currentJob)throw new Error('진행 중인 작업을 마치거나 중단한 뒤 이동해 주세요.');if(voice)throw new Error('음성 녹음을 먼저 종료하세요.');session=null;return navigate('home');}
 if(name==='transcribe')return transcribe();
 if(name==='save-transcript'){const segments=session.transcripts.map((s,i)=>({...s,text:$(`[data-transcript="${i}"]`).value}));await api('/api/v2/transcript',{sid:session.id,segments});session.transcripts=segments;refreshMarkers();toast('수정한 전사문을 저장했어요.');return;}
 if(name==='export-evidence'){const data=await api('/api/v2/evidence/'+session.id);const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`practice-${session.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return;}
 if(name==='analyze'||name==='reply'||name==='report')return coach(name);
 if(name==='voice')return voiceReply();
 if(name==='cancel'){if(currentJob)await api('/api/v2/cancel',{job_id:currentJob});return;}
 if(name==='frame'){t.disabled=true;try{await api('/api/v2/frame',{sid:session.id,ms:reviewTime});$('#frame-list').insertAdjacentHTML('beforeend',`<span class="tag violet">${icon('camera',11)} ${fmt(reviewTime)} 장면 첨부됨</span>`);toast('다음 Codex 분석에 이 장면을 함께 전달해요.');}finally{t.disabled=false;}return;}
}
document.addEventListener('click',async e=>{
 const t=e.target.closest('button,a');if(!t)return;
 try{
  if(t.dataset.route){e.preventDefault();await navigate(t.dataset.route);}
  else if(t.dataset.retryAction)await retryAction(t.dataset.retryAction,t);
  else if(t.dataset.practice)setup(t.dataset.practice,t.dataset.mode);
  else if(t.dataset.session)await loadSession(t.dataset.session);
  else if(t.dataset.problem){if(t.dataset.problem!==pid)switchProblem(t.dataset.problem);if(matchMedia('(max-width: 900px)').matches)setPane('code');}
  else if(t.dataset.seek!==undefined){if(t.dataset.eventPid&&session.codes[t.dataset.eventPid]!==undefined)$('#replay-problem').value=t.dataset.eventPid;seek(Number(t.dataset.seek));if(!t.closest('.player'))$('#video-box').scrollIntoView({behavior:'smooth',block:'nearest'});}
  else if(t.dataset.tab)showTab(t.dataset.tab);
  else if(t.dataset.paneTab)setPane(t.dataset.paneTab);
  else if(t.dataset.minutes){$('#duration').value=t.dataset.minutes;updateSetupSummary();}
  else if(t.dataset.libLevel){library.level=t.dataset.libLevel;$$('[data-lib-level]').forEach(b=>b.classList.toggle('on',b===t));renderLibrary();}
  else if(t.dataset.libTag!==undefined){library.tag=library.tag===t.dataset.libTag?'':t.dataset.libTag;renderLibrary();}
  else if(t.dataset.history){historyFilter=t.dataset.history;$$('[data-history]').forEach(b=>b.classList.toggle('on',b===t));renderHistory();}
  else if(t.dataset.setTheme)toggleTheme(t.dataset.setTheme);
  else if(t.dataset.action)await action(t.dataset.action,t);
  else if(t.dataset.resource&&route==='workspace')record('resource',{url:t.href});
 }catch(err){toast(err.message);}
});
function setPane(name){const ws=$('.ws');if(!ws)return;ws.dataset.pane=name;$$('[data-pane-tab]').forEach(b=>b.classList.toggle('on',b.dataset.paneTab===name));if(name==='code')editor?.editor.layout();}
document.addEventListener('change',e=>{
 if(e.target.matches('.model-select'))fillEfforts(e.target.id.replace(/model$/,''));
 if(e.target.id==='review-effort'||e.target.id==='review-model'){const m=$('#review-model').value;$('#review-model-summary').textContent=m?`${m} · ${$('#review-effort').value}`:'선택 필요';}
 if(e.target.id==='setup-effort')updateModelSummary();
 if(e.target.id==='show-edits')renderEvents();
 if(e.target.id==='replay-problem')seek(reviewTime);
 if(e.target.name==='problem'&&e.target.checked&&radio('kind')==='single')$$('[name="problem"]:checked').forEach(x=>{if(x!==e.target)x.checked=false;});
 if(e.target.name==='kind'&&e.target.value==='single'){const c=$$('[name="problem"]:checked');c.slice(1).forEach(x=>x.checked=false);}
 if(($('#setup-summary')||$('#retry-setup-summary'))&&['mode','kind','resources','problem'].includes(e.target.name))updateSetupSummary();
});
document.addEventListener('input',e=>{
 if(e.target.id==='timeline')seek(Number(e.target.value));
 if(e.target.id==='duration')updateSetupSummary();
 if(e.target.id==='lib-search'){library.query=e.target.value;renderLibrary();}
 if(e.target.id==='pick-search'){const q=e.target.value.trim().toLowerCase();$$('.pick').forEach(p=>p.hidden=!!q&&!p.dataset.title.includes(q));}
});
document.addEventListener('pointerdown',e=>{const h=e.target.closest('.splitter');if(h&&route==='workspace'){e.preventDefault();startSplit(e,h);}});
document.addEventListener('keydown',e=>{
 const typing=e.target.closest('input,textarea,select,[contenteditable],.monaco-editor');
 if(route==='workspace'&&(e.ctrlKey||e.metaKey)&&!e.isComposing){if(e.key==="'"){e.preventDefault();runSafe(false);return;}if(e.key==='Enter'&&e.shiftKey&&!typing){e.preventDefault();runSafe(true);return;}}
 if(!typing&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&e.key.toLowerCase()==='n'&&['home','library','history','settings'].includes(route)&&!$('#modal').open){e.preventDefault();setup();}
});
async function loadSession(id){
 const generation=++navigationGeneration;
 // Send unsent edits before reading the server copy; if that fails, open with the newest local edit.
 try{await drain();}catch{}
 if(generation!==navigationGeneration)return;
 let s;
 try{
  s=await api('/api/v2/session/'+id);if(generation!==navigationGeneration)return;
  if(s.status==='active'){await applyUnsentCode(s);if(generation!==navigationGeneration)return;}
 }catch(error){if(generation===navigationGeneration)throw error;return;}
 if(s.status==='active')openWorkspace(s);else return openReview(id,generation);
}
async function applyUnsentCode(s){
 // Same rule as the server: (ms, seq) with seq -1 when absent; ties go to the later edit.
 const newer=(a,b)=>!b||a[0]>b[0]||(a[0]===b[0]&&a[1]>=b[1]),versions={...(s.code_versions||{})};
 // Older servers/sessions may not send versions: infer them from the stored events, never assume "none".
 for(const e of s.events||[]){const v=[e.ms,e.payload?.client_seq??-1];if(e.kind==='code'&&!(s.code_versions||{})[e.pid]&&newer(v,versions[e.pid]))versions[e.pid]=v;}
 for(const item of await pending()){
  if(item.url!=='/api/v2/events'||typeof item.body!=='string')continue;let body;try{body=JSON.parse(item.body);}catch{continue;}
  if(body.sid!==s.id)continue;
  for(const e of body.events||[]){const v=[e.ms,e.seq??-1];if(e.kind==='code'&&e.pid in s.codes&&typeof e.payload?.code==='string'&&newer(v,versions[e.pid])){s.codes[e.pid]=e.payload.code;versions[e.pid]=v;}}
 }
 s.code_versions=versions;
}
window.addEventListener('blur',()=>{if(route==='workspace')record('blur');});window.addEventListener('focus',()=>{if(route==='workspace')record('focus');});
document.addEventListener('paste',e=>{if(route==='workspace'&&e.target.closest('.coding-pane'))record('paste',{characters:e.clipboardData?.getData('text').length||0});});
window.addEventListener('online',()=>drain().catch(e=>toast(e.message)));
// Best effort only: browsers may terminate before the final chunk/stop is saved.
window.addEventListener('pagehide',()=>{captureGeneration++;if(recorder)void recorder.stop().catch(()=>{});});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&recorder?.recorder.state==='recording')try{recorder.recorder.requestData();}catch{}});
window.addEventListener('beforeunload',e=>{if(route==='workspace'||voice){e.preventDefault();e.returnValue='';}});
$('#modal').addEventListener('cancel',e=>{if(voice)e.preventDefault();});
$('#modal').addEventListener('click',e=>{if(e.target===$('#modal')&&!voice)$('#modal').close();});
async function boot(){try{[dashboard,health]=await Promise.all([api('/api/v2/dashboard'),api('/api/v2/health')]);home();loadModels().then(()=>{if(route==='settings')settings();if($('#setup-model-fields')){$('#setup-model-fields').innerHTML=modelFields('setup-');fillEfforts('setup-');}});drain().catch(()=>toast('미전송 기록이 있어요. 설정에서 다시 보낼 수 있어요.'));}catch(e){$('#app').innerHTML=`<div class="boot"><div class="mark">c<b>/</b></div><h2>연습실에 연결하지 못했어요</h2><p>${esc(e.message)}</p><p class="muted"><code>./run.sh</code>로 서버를 실행한 뒤 새로고침하세요.</p></div>`;}}
boot();
