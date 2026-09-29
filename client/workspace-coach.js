import {makeRecorder,drain} from './recording.js';
import {icon} from './ui.js';

// A coach stays beside the editor; requests always belong to their original problem.
export function createWorkspaceCoach({el,session,api,flush,record,waitJob,getPid,focusEditor,models=[]}) {
 const training=session.mode==='training';
 const drafts=new Map(),messages=[...(session.messages||[]).filter(m=>m.kind==='hint'),...(session.events||[]).filter(e=>e.kind==='note'&&e.payload.text).map(e=>({pid:e.pid,question:e.payload.text,answer:{message:'생각을 기록했어요.'}}))];
 let shown=getPid(),capture=null,voice=null,busy=false,disposed=false,voiceTask=null;
 const $=s=>el.querySelector(s);
 el.innerHTML=`<header class="inline-coach-header"><div class="coach-title"><span class="coach-avatar">${icon(training?'spark':'pen',15)}</span><div><strong>${training?'풀이 코치':'생각 기록'}</strong><span>${training?'막히면 바로 물어보세요':'말한 생각은 녹음에 남아요'}</span></div></div></header>
 <div class="inline-coach-feed" role="log" aria-label="${training?'문제별 힌트 대화':'생각 메모'}"></div>
 <div class="inline-coach-compose"><label for="workspace-question" class="sr-only">${training?'코드를 보면서 질문하기':'지금 떠오른 생각'}</label><div class="compose-box"><textarea id="workspace-question" rows="3" maxlength="5000" placeholder="${training?'무엇이 막혔나요? 비워 두고 힌트를 받아도 돼요.':'접근법, 막힌 이유를 짧게 남겨 보세요.'}"></textarea><div class="compose-foot"><button type="button" class="text-btn inline-note" data-coach="note">${icon('pen',13)} 메모만 남기기</button><span class="kbd-hint"><kbd>Ctrl</kbd><kbd>Enter</kbd></span></div></div><p class="inline-coach-status" role="status" aria-live="polite"></p><div class="inline-coach-buttons"><button type="button" class="btn" data-coach="voice">${training?'◉ 말로 질문':'◉ 말로 메모'}</button>${training?'<button type="button" class="btn primary" data-coach="hint">힌트 받기</button>':''}</div><small class="compose-note">${training?'현재 문제의 코드·실행 결과를 함께 전달해요.':'실전 중에는 힌트를 제공하지 않아요.'}</small></div>`;
 const modelSettings=document.createElement('details');modelSettings.className='inline-model-settings';
 const summary=document.createElement('summary'),modelSelect=document.createElement('select'),effortSelect=document.createElement('select');
 modelSelect.setAttribute('aria-label','힌트 모델');effortSelect.setAttribute('aria-label','힌트 추론 수준');
 modelSelect.add(new Option('모델 선택',''));for(const m of models)modelSelect.add(new Option(m.displayName||m.model,m.model));modelSelect.value=session.model||'';
 function setEfforts(){const m=models.find(m=>m.model===modelSelect.value);effortSelect.replaceChildren();for(const e of m?.supportedReasoningEfforts||[])effortSelect.add(new Option(e.reasoningEffort,e.reasoningEffort));if([...effortSelect.options].some(o=>o.value===session.effort))effortSelect.value=session.effort;else effortSelect.value=m?.defaultReasoningEffort||effortSelect.options[0]?.value||'';updateModel();}
 function updateModel(){session.model=modelSelect.value;session.effort=effortSelect.value;summary.textContent=session.model?session.model+' · '+session.effort:'힌트 모델 선택';}
 modelSelect.onchange=setEfforts;effortSelect.onchange=updateModel;modelSettings.append(summary,modelSelect,effortSelect);if(training)$('.inline-coach-header').append(modelSettings);modelSettings.open=training&&!session.model;setEfforts();
 const text=$('textarea'),status=$('.inline-coach-status'),feed=$('.inline-coach-feed');
 function say(message,error=false){if(disposed)return;status.textContent=message;status.classList.toggle('error-text',error);}
 function render(){
  if(disposed)return;
  feed.replaceChildren();
  const list=messages.filter(m=>m.pid===shown);
  if(!list.length){const p=document.createElement('div');p.className='inline-coach-empty';p.innerHTML=training?`${icon('bulb',20)}<strong>막히면 여기서 힌트를 받아요</strong><span>코드와 최근 실행 결과가 자동으로 함께 전달돼요. 답을 주기보다 다음 한 걸음을 짚어줘요.</span>`:`${icon('mic',20)}<strong>풀이에 집중하세요</strong><span>접근법을 정할 때, 막혔을 때 생각을 말하면 화면과 함께 기록돼요.</span>`;feed.append(p);}
  for(const m of list){const item=document.createElement('article');item.className='inline-coach-message';if(m.question){const q=document.createElement('p');q.className='inline-coach-question';q.textContent=m.question;item.append(q);}const answer=document.createElement('p');answer.className='inline-coach-answer'+(m.answer.message==='생각을 기록했어요.'?' is-note':'');answer.textContent=m.answer.message;item.append(answer);feed.append(item);}
  feed.scrollTop=feed.scrollHeight;
 }
 function controls(){if(disposed)return;for(const b of el.querySelectorAll('[data-coach]'))b.disabled=busy;const b=$('[data-coach="voice"]');b.classList.toggle('recording',!!voice);b.textContent=voice?(training?'■ 말 마침 · 힌트 받기':'■ 말 마침 · 메모 저장'):(training?'◉ 말로 질문':'◉ 말로 메모');if(voice){$('[data-coach="hint"]')?.setAttribute('disabled','');$('[data-coach="note"]').disabled=true;}}
 function saveNote(message,problem){record('note',{text:message},problem);messages.push({pid:problem,question:message,answer:{message:'생각을 기록했어요.'}});render();}
 async function ask(message=text.value.trim(),problem=shown){
  if(busy||voice||!training)return;
  if(!session.model||!session.effort){modelSettings.open=true;say('위에서 사용할 모델을 선택해 주세요.',true);return;}
  busy=true;controls();say('코드와 실행 결과를 살펴보고 있어요. 계속 풀어도 돼요.');
  try{
   await flush();
   const {job_id}=await api('/api/v2/chat',{sid:session.id,pid:problem,kind:'hint',message,model:session.model,effort:session.effort});
   const entry=await waitJob(job_id);messages.push(entry);if((drafts.get(problem)||'').trim()===message)drafts.set(problem,'');if(shown===problem&&text.value.trim()===message)text.value='';render();say(shown===problem?'힌트가 도착했어요.':'다른 문제의 힌트가 도착했어요. 해당 문제에서 확인할 수 있어요.');
  }catch(e){say(e.message+' 다시 요청할 수 있어요.',true);}finally{busy=false;controls();}
 }
 async function stopVoice(sendHint=true){
  if(voiceTask)return voiceTask;
  if(!voice)return;
  const current=voice;voice=null;busy=true;controls();say('말한 내용을 인식하고 있어요. 코드는 계속 작성할 수 있어요.');
  voiceTask=(async()=>{try{
   const segment=await current.recorder.stop();await drain();
   const {job_id}=await api('/api/v2/transcribe',{sid:session.id,kind:'voice',segment});
   const result=await waitJob(job_id);const message=result.text.trim();
   if(!message){say('음성을 알아듣지 못했어요. 다시 말하거나 글로 질문해 주세요.',true);return;}
   const combined=[current.draft,message].filter(Boolean).join('\n');drafts.set(current.pid,combined);if(shown===current.pid)text.value=combined;
   if(training&&sendHint)record('note',{text:message,source:'voice',segment},current.pid);
   busy=false;
   if(training&&sendHint&&!disposed)await ask(combined,current.pid);
   else{saveNote(combined,current.pid);drafts.set(current.pid,'');if(shown===current.pid)text.value='';say('말한 생각을 기록했어요.');}
  }catch(e){say(e.message,true);}finally{busy=false;voiceTask=null;controls();}})();
  return voiceTask;
 }
 async function toggleVoice(){
  if(voice)return stopVoice();if(busy)return;
  busy=true;controls();let stream;
  try{
   const tracks=capture?.mic?.getAudioTracks().filter(t=>t.readyState==='live')||[];
   stream=tracks.length?new MediaStream(tracks.map(t=>t.clone())):await navigator.mediaDevices.getUserMedia({audio:true});
   if(disposed){stream.getTracks().forEach(t=>t.stop());return;}
   voice={pid:shown,draft:text.value.trim(),recorder:makeRecorder(stream,{sid:session.id,kind:'voice',start:Math.max(0,Date.now()-session.started)})};
   say(training?'말하고 있어요. 마치면 아래 버튼을 누르세요.':'생각을 말해 주세요. 마치면 아래 버튼을 누르세요.');
  }catch(e){stream?.getTracks().forEach(t=>t.stop());say(e.message,true);}finally{busy=false;controls();}
 }
 text.addEventListener('input',()=>drafts.set(shown,text.value));
 text.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'&&!e.isComposing){e.preventDefault();if(training)ask();else note();}});
 function note(){const message=text.value.trim();if(!message||busy||voice)return;saveNote(message,shown);text.value='';drafts.set(shown,'');say('메모를 남겼어요.');focusEditor();}
 el.addEventListener('click',e=>{const action=e.target.closest('[data-coach]')?.dataset.coach;if(action==='hint')ask();if(action==='voice')toggleVoice();if(action==='note')note();});
 render();controls();
 return {setCapture(value){capture=value;},switchProblem(){drafts.set(shown,text.value);shown=getPid();text.value=drafts.get(shown)||'';render();},async finish(){await stopVoice(false);},dispose(){disposed=true;if(voice){voice.recorder.stop().catch(()=>{});voice=null;}}};
}
