'use strict';
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const escapeHtml = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const localDate = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
const statusNames = {solved:'정답',partial:'일부 통과',stuck:'미해결'};
const pageNames = {dashboard:'대시보드',new:'풀이 기록하기',reviews:'복습 플래너',history:'나의 풀이 노트',settings:'설정 · 백업'};
let state, page = 'dashboard', toastTimeout, retry = null, timerStart = null, elapsed = 0, activeDetail = null;
let storageAvailable = true;
function readLocal(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } }
function writeLocal(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { if (storageAvailable) toast('브라우저 임시 저장을 사용할 수 없습니다. 저장 버튼으로 기록을 남겨 주세요.'); storageAvailable = false; } }
async function api(path, data) {
  const res = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result = await res.json();
  if (!res.ok) throw new Error(result.error || '요청을 처리하지 못했습니다.');
  return result;
}
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimeout); toastTimeout = setTimeout(() => $('#toast').hidden = true, 5500); }
const button = (label, action, primary=false, extra='') => `<button type="button" class="btn ${primary?'primary':''}" data-action="${action}" ${extra}>${label}</button>`;
function heading(eyebrow, title, subtitle, action='') { return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div>${action}</div>`; }
function empty(title, text, action='') { return `<div class="empty"><div class="empty-icon">⌁</div><h3>${title}</h3><p>${text}</p>${action}</div>`; }
function tags(a) { return a.tags.map(t=>`<span class="pill">${escapeHtml(t)}</span>`).join(''); }
function status(a) { return `<span class="badge ${a.status==='solved'?'green':'orange'}">${statusNames[a.status]}</span>`; }
async function refresh() { state = await api('/api/state'); $('#review-count').textContent = state.reviews.filter(r=>r.days_left<=0).length; }
function switchPage(next) {
  if (page==='new' && next!=='new' && $('#attempt-form')) persistDraft();
  page = pageNames[next] ? next : 'dashboard';
  $$('.nav').forEach(n => { n.classList.toggle('active', n.dataset.page===page); n.setAttribute('aria-current', n.dataset.page===page?'page':'false'); });
  $('#breadcrumb').textContent = `나의 학습 / ${pageNames[page]}`;
  render(); window.scrollTo(0,0);
}
function render() {
  $('#content').innerHTML = ({dashboard:dashboardPage,new:newPage,reviews:reviewsPage,history:historyPage,settings:settingsPage})[page]();
  if (page==='new') hydrateForm();
  if (page==='history') renderHistory();
}
function dashboardPage() {
  const s=state.summary, due=state.reviews.filter(r=>r.days_left<=0), weak=s.weaknesses.find(w=>w.needs_help>0);
  const insight = !s.total ? '오늘의 한 문제,<br>성장의 시작으로 남겨볼까요?' : weak ? `${escapeHtml(weak.tag)},<br>다시 풀면서 내 것으로 만들어요.` : '정답에서 한 걸음 더,<br>혼자 다시 풀 수 있는 실력으로.';
  const insightText = !s.total ? '맞았는지보다 어떻게 풀었는지가 중요해요.<br>첫 풀이를 기록하면 나만의 학습 지도가 시작됩니다.' : weak ? `이 유형의 첫 풀이 ${weak.count}개 중 ${weak.needs_help}개에서 힌트를 사용했거나 해결하지 못했어요. ${weak.count<3?'아직 표본이 적으니 더 풀어보며 확인해요.':'막혔던 이유를 확인하고 새 문제에도 적용해 보세요.'}` : '풀이 기록을 남기고 간격을 두어 복습해요. 힌트 없이 해결하는 경험을 조금씩 늘려가세요.';
  const stats=[['기록한 문제',s.problems,'문제','재풀이를 제외한 고유 문제 수','▤'],['독립 해결률',s.independent_rate??'—',s.independent_rate===null?'':'%','첫 풀이 · 정답 & 힌트 0회','↗'],['오늘 복습',due.length,'문제','복습일이 지났거나 오늘인 문제','↻'],['공부한 날',s.practice_days,'일',`총 ${s.total}번의 풀이를 기록했어요`,'◷']];
  return heading('YOUR LEARNING JOURNEY','어제보다 한 걸음 더','정답 너머의 실력을, 매일의 기록으로 쌓아가요.',button('＋ 풀이 기록하기','new',true))+
  `<div class="stats">${stats.map(([name,n,unit,note,icon])=>`<div class="stat"><div class="stat-top">${name}<span class="stat-icon">${icon}</span></div><strong>${n}<small>${unit}</small></strong><div class="stat-note">${note}</div></div>`).join('')}</div>
  <div class="dashboard-grid"><section class="card coach-card"><div class="coach-orbit"></div><div class="coach-label">✧ TODAY'S FOCUS</div><h2>${insight}</h2><p>${insightText}</p>${button(s.total?'나의 복습 확인하기 →':'첫 풀이 남기기 →',s.total?'reviews':'new',true)}</section>
  <section class="card"><div class="card-header"><div><h2>이번 주 복습</h2><p>기억이 흐려지기 전에 한 번 더</p></div><button class="btn quiet" data-page="reviews">전체 보기 →</button></div>${state.reviews.length?reviewList(state.reviews.filter(r=>r.days_left<=7).slice(0,3))||empty('이번 주 복습을 마쳤어요','복습 플래너에서 다음 일정을 확인하세요.'):empty('아직 복습할 문제가 없어요','풀이를 기록하면 결과와 힌트 사용에 맞춰<br>다음 복습일을 제안해 드려요.')}</section>
  <section class="card"><div class="card-header"><div><h2>도움이 필요했던 유형</h2><p>첫 풀이에서 미해결 또는 힌트를 사용한 비율</p></div><span class="badge">기록 기반</span></div>${s.weaknesses.length?s.weaknesses.slice(0,5).map(w=>`<div class="weak-row"><div class="weak-top"><span>${escapeHtml(w.tag)} <small>· ${w.count}문제${w.count<3?' · 관찰 중':''}</small></span><span>${w.needs_help}/${w.count} <small>(${w.rate}%)</small></span></div><div class="track"><div class="fill" style="width:${w.rate}%"></div></div></div>`).join(''):empty('취약점은 기록에서 발견돼요','어떤 유형에서 막혔는지 함께 기록해 주세요.<br>표본이 적을 때는 실력을 단정하지 않아요.')}</section>
  <section class="card"><div class="card-header"><div><h2>독립 해결의 흐름</h2><p>최근 4주 · 첫 풀이에서 힌트 없이 맞힌 비율</p></div><span class="badge green">4 WEEKS</span></div><div class="chart">${s.weeks.map(w=>`<div class="chart-col"><span>${w.rate===null?'기록 없음':`${w.rate}% · ${w.count}문제`}</span><div class="chart-bar" style="height:${w.rate===null?3:Math.max(3,w.rate)}%"></div></div>`).join('')}</div><div class="chart-labels">${s.weeks.map(w=>`<span>${w.label} ~</span>`).join('')}</div><p class="chart-note">문제 난이도·유형에 따라 달라질 수 있어요. 아래에서 같은 언어·난이도의 기록도 함께 확인해 주세요.</p></section>
  <section class="card wide"><div class="card-header"><div><h2>최근 풀이 노트</h2><p>나의 고민과 배움을 차곡차곡</p></div><button class="btn quiet" data-page="history">모두 보기 →</button></div>${state.attempts.length?attemptTable(state.attempts.slice(0,4)):empty('첫 번째 풀이 노트를 기다리고 있어요','완벽하게 풀지 못했어도 괜찮아요. 막힌 지점이 다음 연습의 출발점이에요.',button('＋ 기록 시작하기','new'))}</section>
  ${s.total?`<section class="card wide"><div class="card-header"><div><h2>같은 조건으로 살펴보기</h2><p>언어·난이도별 첫 풀이 집계 · 시간이 짧아졌다는 이유만으로 성장을 판정하지 않아요.</p></div></div><div class="table-wrap"><table><thead><tr><th>언어 / 난이도</th><th>문제 수</th><th>독립 해결률</th><th>풀이 시간 중앙값</th><th>평균 힌트</th></tr></thead><tbody>${s.cohorts.map(c=>`<tr><td>${escapeHtml(c.language)} · ${c.level}</td><td>${c.count}개</td><td>${c.rate}%</td><td>${c.minutes}분</td><td>${c.hints}회</td></tr>`).join('')}</tbody></table></div><p class="chart-note">자주 기록한 막힘: ${s.blockers.filter(b=>b.count).map(b=>`${b.name} ${b.count}회`).join(' · ')||'아직 없어요'}</p></section>`:''}</div>`;
}
function reviewList(items) { return items.map((r,i)=>`<div class="review-item"><span class="review-index">${String(i+1).padStart(2,'0')}</span><div class="review-info"><strong>${escapeHtml(r.title)}</strong><small>${r.due} · ${r.attempts}회 풀이</small></div><span class="badge ${r.days_left<=0?'orange':''}">${r.days_left<0?`${-r.days_left}일 지남`:r.days_left===0?'오늘':`D−${r.days_left}`}</span><button class="btn quiet" data-retry="${r.id}">다시 풀기 →</button></div>`).join(''); }
function reviewsPage() { return heading('SPACED PRACTICE','다시 풀 때, 진짜 내 실력이 돼요','이전 코드를 보지 않고 다시 풀어보세요. 새 풀이로 기록하면 복습 일정이 갱신돼요.')+`<div class="notice">미해결·힌트 사용 → 1일 뒤 / 독립 해결 → 3일, 7일, 14일, 30일 간격. 같은 날 여러 번 풀어도 복습 간격은 늘어나지 않아요.</div><section class="card">${state.reviews.length?reviewList(state.reviews):empty('복습 일정을 준비하고 있어요','문제 하나를 기록하면 첫 복습일이 자동으로 생겨요.',button('풀이 기록하기','new',true))}</section>`; }
function attemptTable(items) { return `<div class="table-wrap"><table><thead><tr><th>문제</th><th>유형</th><th>결과</th><th>시간 / 힌트</th><th>날짜</th></tr></thead><tbody>${items.map(a=>`<tr><td><button class="title-button" data-detail="${a.id}">${escapeHtml(a.title)}</button><div class="small muted">${escapeHtml(a.language)} · ${a.level}</div></td><td>${tags(a)}</td><td>${status(a)}</td><td>${a.minutes}분 / ${a.hints}회</td><td class="muted">${a.date}</td></tr>`).join('')}</tbody></table></div>`; }
function historyPage() { return heading('PRACTICE ARCHIVE','나의 풀이 노트','정답, 시행착오, 작은 깨달음까지. 모든 풀이가 성장의 근거가 돼요.',button('＋ 새 기록','new',true))+`<div class="search-row"><input id="search" aria-label="문제 검색" placeholder="문제 이름, 유형, 언어로 검색"><select id="status-filter" aria-label="결과 필터"><option value="">모든 결과</option><option value="solved">정답</option><option value="partial">일부 통과</option><option value="stuck">미해결</option></select></div><section class="card" id="history-results"></section>`; }
function renderHistory() { const q=$('#search').value.toLowerCase(), f=$('#status-filter').value; const items=state.attempts.filter(a=>(!f||a.status===f)&&[a.title,a.language,...a.tags].join(' ').toLowerCase().includes(q)); $('#history-results').innerHTML=items.length?attemptTable(items):empty('해당하는 기록이 없어요','새 풀이를 남기거나 검색 조건을 바꿔보세요.'); }
function options(items, chosen) { return items.map(v=>`<option ${v===chosen?'selected':''}>${escapeHtml(v)}</option>`).join(''); }
function chips(name, items) { return `<div class="chips">${items.map(v=>`<label class="chip"><input type="checkbox" name="${name}" value="${escapeHtml(v)}"><span>${escapeHtml(v)}</span></label>`).join('')}</div>`; }
function newPage() { return heading('ONE PROBLEM, ONE STEP','오늘의 풀이를 남겨요','막힌 지점까지 기록하면, 다음에는 어디에 집중할지 보이기 시작해요.')+`<form id="attempt-form"><input type="hidden" name="edit_id" value=""><div class="form-layout"><div class="form-stack"><section class="card"><h2>01. 어떤 문제를 풀었나요?</h2><label class="field">문제 이름 *<input name="title" required maxlength="200" placeholder="예: 완주하지 못한 선수"></label><label class="field">문제 링크<input name="url" type="url" maxlength="2000" placeholder="https://school.programmers.co.kr/learn/courses/30/lessons/..."><small>같은 링크의 기록은 하나의 문제로 묶여요. 링크가 없으면 문제 이름으로 묶어요.</small></label><div class="row"><label class="field">난이도<select name="level">${options(['미지정','Lv.0','Lv.1','Lv.2','Lv.3','Lv.4','Lv.5'],'Lv.1')}</select></label><label class="field">풀이 날짜<input name="date" type="date" required value="${localDate()}" max="${localDate()}"></label></div><p class="section-label">문제 유형 * <span class="muted small">중복 선택 가능</span></p>${chips('tags',state.config.tags)}<label class="field">문제 설명 · 제한 조건<textarea name="description" rows="6" maxlength="40000" placeholder="문제 설명, 입출력 예시, 제한 조건을 붙여 넣어 주세요."></textarea><small>AI 피드백에는 설명이 필요해요. 링크만으로 문제를 자동 수집하지는 않아요.</small></label></section><section class="card"><div class="card-header"><div><h2>02. 나의 풀이 코드</h2><p>실행·채점은 프로그래머스에서 하고 결과를 기록해 주세요.</p></div><span class="badge">CODE</span></div><label class="field">사용 언어<select name="language">${options(state.config.languages,readLocal('coach-language','Java'))}</select></label><label class="field">코드 *<textarea class="code" name="code" rows="14" required maxlength="60000" spellcheck="false" placeholder="class Solution {&#10;    public int solution(...) {&#10;        // 오늘의 고민을 담은 코드를 붙여 넣어 주세요.&#10;    }&#10;}"></textarea></label></section></div><div class="form-stack"><section class="card"><h2>03. 풀이 과정 돌아보기</h2><p class="small muted">결과보다 과정이 취약점을 더 잘 알려줘요.</p><div class="timer"><strong id="timer-text">00:00</strong><button class="btn" type="button" data-action="timer" id="timer-button">타이머 시작</button></div><div class="row"><label class="field">풀이 시간 (분)<input name="minutes" type="number" min="0" max="1440" step="1" required value="0"></label><label class="field">사용한 힌트 (회)<input name="hints" type="number" min="0" max="100" step="1" required value="0"></label></div><label class="field">채점 결과<select name="status"><option value="stuck">미해결</option><option value="partial">일부 통과</option><option value="solved">정답</option></select></label><p class="section-label">어디에서 막혔나요?</p>${chips('blockers',state.config.blockers)}<label class="field">생각한 접근법 · 배운 점<textarea name="notes" rows="6" maxlength="10000" placeholder="어떤 접근을 시도했나요?&#10;왜 막혔고, 다음에는 무엇을 확인할 건가요?"></textarea></label></section><section class="card coach-card"><div class="coach-label">✧ FEEDBACK, YOUR WAY</div><h2>정답 대신, 다음 생각의 실마리</h2><p>저장 후 작은 힌트부터 피드백받을 수 있어요. ${state.config.ai_ready?'AI 피드백을 요청하면 문제 설명과 코드, 최근 기록을 OpenAI로 전송해요.':'API 키가 없어도 피드백 요청문을 복사해 이 대화에서 함께 검토할 수 있어요.'}</p><span class="badge">${state.config.ai_ready?'AI 연결 설정됨':'대화에 복사해서 피드백받기'}</span></section></div></div><div class="form-actions"><span class="small muted" id="draft-status">입력 중인 내용은 이 브라우저에 임시 저장돼요</span>${button('입력 초기화','clear-draft')}<button type="submit" class="btn primary">풀이 저장하기 →</button></div></form>`; }
function formData() {
  const f=new FormData($('#attempt-form')), result=Object.fromEntries(f.entries());
  result.tags=f.getAll('tags'); result.blockers=f.getAll('blockers'); result.minutes=Number(result.minutes); result.hints=Number(result.hints); return result;
}
function persistDraft() { if ($('#attempt-form')) writeLocal('coach-draft',formData()); }
function hydrateForm() {
  const draft=retry||readLocal('coach-draft',null); retry=null;
  if (draft) for (const [key,val] of Object.entries(draft)) { const fields=$$(`[name="${key}"]`,$('#attempt-form')); fields.forEach(f=>{if(f.type==='checkbox') f.checked=Array.isArray(val)&&val.includes(f.value); else f.value=val;}); }
  updateTimer();
}
function updateTimer() {
  if (!$('#timer-text')) return;
  const seconds=Math.floor((elapsed+(timerStart?Date.now()-timerStart:0))/1000);
  $('#timer-text').textContent=`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
  $('#timer-button').textContent=timerStart?'일시 정지':'타이머 시작';
}
setInterval(updateTimer,1000);
function settingsPage() { return heading('YOUR WORKSPACE','내 페이스에 맞게','기본 언어, 피드백 연결, 소중한 기록의 백업을 관리해요.')+`<div class="settings-grid"><section class="card"><h2>기본 언어</h2><p>새 풀이를 기록할 때 자동으로 선택할 언어예요.</p><label class="field">언어<select id="default-language">${options(state.config.languages,readLocal('coach-language','Java'))}</select></label><p class="chart-note">${state.attempts.length}개의 풀이가 내 컴퓨터에 저장되어 있어요.</p></section><section class="card"><div class="card-header"><h2>기록 백업</h2><span class="badge green">LOCAL FIRST</span></div><p>문제, 코드, 피드백을 JSON 파일로 보관하세요. 복원은 기존 기록을 유지하고 없는 기록만 추가해요. 같은 ID의 내용은 덮어쓰지 않아요.</p>${button('↓ 백업 다운로드','export')} ${button('↑ 백업 복원','import')}<input id="import-file" class="file-input" type="file" accept="application/json,.json"><p class="chart-note">기록 파일: <code>data/coach.sqlite3</code><br>다른 기기로 옮길 때도 백업 파일을 사용할 수 있어요.</p></section><section class="card wide"><div class="card-header"><div><h2>AI 피드백 연결</h2><p>${state.config.ai_ready?'키가 설정되어 있어요. 실제 연결은 피드백 요청 시 확인돼요.':'API 키 없이도 기록·분석·복습·피드백 요청문 복사를 사용할 수 있어요.'}</p></div><span class="badge ${state.config.ai_ready?'green':''}">${state.config.ai_ready?'키 설정됨':'선택 기능'}</span></div><p>프로젝트 폴더에서 <code>.env.example</code>을 <code>.env</code>로 복사하고 본인의 API 키와 사용할 모델을 설정한 뒤 서버를 다시 실행하세요. 키는 서버에서만 읽어요.</p><pre>OPENAI_API_KEY=본인의_API_키\nOPENAI_MODEL=gpt-6-astra</pre><p>‘AI 피드백 받기’를 누르면 해당 문제 설명·코드·메모와 최근 20개 풀이 요약이 OpenAI로 전송되며 API 사용료가 발생할 수 있어요. AI의 코드 검토는 실제 채점 결과와 다를 수 있어요.</p><p><a href="https://developers.openai.com/api/docs/guides/text" target="_blank" rel="noopener noreferrer">OpenAI 공식 API 문서 ↗</a></p></section></div>`; }
async function openDetail(id) {
  const a=state.attempts.find(a=>a.id===id); if(!a) return;
  activeDetail=id;
  const prior=state.attempts.filter(p=>p.problem_key===a.problem_key&&p.id!==a.id).sort((x,y)=>x.date.localeCompare(y.date)||x.created_at.localeCompare(y.created_at));
  const first=prior.find(p=>p.date<a.date||(p.date===a.date&&p.created_at<a.created_at));
  $('#detail-body').innerHTML=`<div class="detail-heading"><div><div class="eyebrow">MY PRACTICE NOTE</div><h2>${escapeHtml(a.title)}</h2></div><button class="close" data-action="close" aria-label="닫기">×</button></div><div class="detail-meta">${status(a)}<span class="pill">${a.language} · ${a.level}</span><span class="pill">${a.minutes}분 · 힌트 ${a.hints}회</span><span class="pill">${a.date}</span>${tags(a)}</div>${a.url?`<a href="${escapeHtml(a.url)}" target="_blank" rel="noopener noreferrer">문제 원문 열기 ↗</a>`:''}${first?`<div class="notice">첫 풀이 대비: ${first.minutes}분 → ${a.minutes}분 / 힌트 ${first.hints}회 → ${a.hints}회 / ${statusNames[first.status]} → ${statusNames[a.status]}<br>같은 문제 재풀이는 기억의 영향을 받을 수 있어요. 새 문제에서도 확인해 보세요.</div>`:''}<details class="detail-section"><summary>문제 설명 확인</summary><p class="feedback-view">${escapeHtml(a.description||'문제 설명을 기록하지 않았어요.')}</p></details><div class="detail-section"><h3>나의 코드</h3><pre class="detail-code">${escapeHtml(a.code)}</pre><p class="small muted">막힌 지점: ${a.blockers.join(' · ')||'기록 없음'}</p><p class="feedback-view">${escapeHtml(a.notes||'남긴 메모가 없어요.')}</p></div><div class="detail-section"><h3>피드백</h3><p class="small muted">정확성 → 복잡도 → 가독성 순서로, 정답 코드보다 작은 힌트부터.</p><div class="detail-actions">${button('피드백 요청문 복사','copy-prompt')}${state.config.ai_ready?button('✧ AI 피드백 받기','analyze',true):button('AI 연결 안내','ai-settings')}</div>${state.config.ai_ready?'<p class="small muted">AI 요청 시 문제·코드·메모와 최근 풀이 요약이 전송돼요. API 비용이 발생할 수 있어요.</p>':''}<div id="feedback-output">${a.feedback?`<span class="badge">${a.feedback_source==='ai'?'AI 피드백':'직접 저장한 피드백'}</span><div class="feedback-view">${escapeHtml(a.feedback)}</div>`:''}</div><label class="field">대화에서 받은 피드백 · 나의 정리<textarea id="manual-feedback" rows="5" maxlength="60000" placeholder="이 대화에서 받은 피드백을 여기에 붙여 넣고 저장할 수 있어요.">${escapeHtml(a.feedback)}</textarea></label><div class="detail-actions">${button('피드백 저장','save-feedback')}</div></div><div class="detail-section detail-actions">${button('이 문제 다시 풀기','retry-detail',true)}${button('기록 수정','edit-detail')}${button('기록 삭제','delete')}</div>`;
  if(!$('#detail').open) $('#detail').showModal();
}
async function startRetry(id) {
  const a=state.attempts.find(a=>a.id===id);
  if(!a) return;
  const draft=readLocal('coach-draft',null);
  if(draft&&(draft.title||draft.code)&&!confirm('임시 작성 중인 내용을 이 문제의 새 풀이 양식으로 바꿀까요?')) return;
  retry={title:a.title,url:a.url,description:a.description,language:a.language,level:a.level,tags:a.tags,date:localDate(),code:'',notes:'',hints:0,minutes:0,status:'stuck',blockers:[]};
  elapsed=0;timerStart=null;$('#detail').close();switchPage('new');persistDraft();
  toast('이전 코드를 보지 않고 새롭게 풀어보세요.');
}
async function exportBackup() { const data=await api('/api/export'), url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})); const link=document.createElement('a');link.href=url;link.download=`cote-coach-${localDate()}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000); }
async function action(name, target) {
  if(pageNames[name]) return switchPage(name);
  if(name==='close') return $('#detail').close();
  if(name==='timer') { if(timerStart){elapsed+=Date.now()-timerStart;timerStart=null;$('[name="minutes"]').value=Math.min(1440,Math.ceil(elapsed/60000));persistDraft();}else timerStart=Date.now();updateTimer();return; }
  if(name==='clear-draft') { if(confirm('작성 중인 입력 내용을 초기화할까요?')){writeLocal('coach-draft',null);elapsed=0;timerStart=null;render();}return; }
  if(name==='export') return exportBackup();
  if(name==='import') return $('#import-file').click();
  if(name==='ai-settings'){ $('#detail').close();switchPage('settings');return; }
  const id=activeDetail;
  if(name==='retry-detail') return startRetry(id);
  if(name==='edit-detail') {
    if(!confirm('기록을 수정할까요? 수정 저장 시 이전 피드백은 초기화돼요. 작성 중인 임시 입력도 교체돼요.')) return;
    const a=state.attempts.find(a=>a.id===id);retry={...a,edit_id:id};elapsed=0;timerStart=null;
    $('#detail').close();switchPage('new');persistDraft();toast('기록 수정 중이에요. 저장하면 기존 기록에 반영돼요.');return;
  }
  if(name==='copy-prompt') {
    const {prompt}=await api(`/api/prompt/${id}`);
    try { await navigator.clipboard.writeText(prompt); toast('요청문을 복사했어요. 이 대화에 붙여 넣어 주세요.'); }
    catch { const area=document.createElement('textarea');area.value=prompt;area.rows=10;area.setAttribute('aria-label','복사할 피드백 요청문');$('#feedback-output').replaceChildren(area);area.focus();area.select();toast('복사 권한이 없어 요청문을 표시했어요. Ctrl+C로 복사하세요.'); }
  }
  if(name==='save-feedback') { await api('/api/feedback',{id,feedback:$('#manual-feedback').value});await refresh();await openDetail(id);toast('피드백을 저장했어요.'); }
  if(name==='analyze') {
    if($('#manual-feedback').value.trim()&&!confirm('새 AI 피드백으로 저장된 피드백을 바꿀까요?'))return;
    target.disabled=true;target.textContent='피드백 작성 중…';
    try { await api('/api/analyze',{id});await refresh();if($('#detail').open&&activeDetail===id) await openDetail(id);toast('AI 피드백을 저장했어요.'); }
    finally {target.disabled=false;target.textContent='✧ AI 피드백 받기';}
  }
  if(name==='delete') { if(!confirm('이 풀이 기록과 피드백을 삭제할까요? 필요하면 먼저 백업해 주세요.'))return;await api('/api/delete',{id});$('#detail').close();await refresh();render();toast('기록을 삭제했어요.'); }
}
document.addEventListener('click',async e=>{const t=e.target.closest('button');if(!t)return;try {if(t.dataset.page)switchPage(t.dataset.page);else if(t.dataset.detail)await openDetail(t.dataset.detail);else if(t.dataset.retry)await startRetry(t.dataset.retry);else if(t.dataset.action)await action(t.dataset.action,t);}catch(err){toast(err.message);} });
document.addEventListener('input',e=>{if(e.target.closest('#attempt-form'))persistDraft();if(e.target.id==='search')renderHistory();});
document.addEventListener('change',async e=>{
  if(e.target.id==='status-filter')renderHistory();
  if(e.target.id==='default-language'){writeLocal('coach-language',e.target.value);toast('기본 언어를 저장했어요.');}
  if(e.target.id==='import-file'&&e.target.files[0]){try{const file=e.target.files[0];if(file.size>10_000_000)throw new Error('10MB 이하 백업 파일을 선택해 주세요.');const data=JSON.parse(await file.text());const r=await api('/api/import',data);await refresh();render();toast(`${r.imported}개 기록을 복원했어요. 기존 기록은 유지했어요.`);}catch(err){toast(err.message);} }
});
document.addEventListener('submit',async e=>{
  if(e.target.id!=='attempt-form')return;e.preventDefault();const submit=$('[type="submit"]',e.target);submit.disabled=true;
  try {if(timerStart){elapsed+=Date.now()-timerStart;timerStart=null;$('[name="minutes"]').value=Math.min(1440,Math.ceil(elapsed/60000));}const data=formData();if(!data.tags.length)throw new Error('문제 유형을 하나 이상 선택해 주세요.');const a=await api(data.edit_id?'/api/update':'/api/attempts',data);writeLocal('coach-draft',null);elapsed=0;timerStart=null;await refresh();page='dashboard';switchPage('dashboard');await openDetail(a.id);toast('풀이를 저장하고 복습 일정을 만들었어요.');}catch(err){toast(err.message);submit.disabled=false;}
});
document.addEventListener('keydown',e=>{if(e.key==='Tab'&&e.target.matches('textarea.code')){e.preventDefault();const t=e.target,start=t.selectionStart,end=t.selectionEnd;t.setRangeText('    ',start,end,'end');persistDraft();}});
window.addEventListener('beforeunload',()=>{if(page==='new')persistDraft();});
$('#content').innerHTML='<div class="loading">학습 기록을 불러오고 있어요…</div>';
refresh().then(()=>switchPage('dashboard')).catch(err=>{$('#content').innerHTML=empty('기록을 불러오지 못했어요',escapeHtml(err.message));});
