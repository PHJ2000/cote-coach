const {chromium}=require('playwright');const assert=require('node:assert/strict');
const base='http://localhost:8876';
(async()=>{const browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required']});try{const page=await browser.newPage({viewport:{width:1500,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.addInitScript(()=>{
 let handle;
 navigator.mediaDevices.setCaptureHandleConfig=c=>{handle=c;};
 navigator.mediaDevices.getDisplayMedia=async()=>{const c=document.createElement('canvas');c.width=1280;c.height=720;const x=c.getContext('2d');x.fillStyle='#20283b';x.fillRect(0,0,c.width,c.height);x.fillStyle='white';x.font='32px sans-serif';x.fillText('코테 연습실 · 브라우저 녹화 테스트',80,120);setInterval(()=>{x.fillStyle='#20283b';x.fillRect(80,180,800,80);x.fillStyle='white';x.fillText(String(Date.now()),80,220);},200);const stream=c.captureStream(5),track=stream.getVideoTracks()[0];const old=track.getSettings.bind(track);track.getSettings=()=>({...old(),displaySurface:'browser'});track.getCaptureHandle=()=>handle;return stream;};
 navigator.mediaDevices.getUserMedia=async()=>{const audio=new AudioContext();const source=audio.createOscillator(),gain=audio.createGain(),dest=audio.createMediaStreamDestination();gain.gain.value=0;source.connect(gain);gain.connect(dest);source.start();await audio.resume();return dest.stream;};
});
await page.goto(base);await page.getByRole('heading',{name:'정답까지의 과정을, 실력으로.'}).waitFor();
await page.locator('[data-action="setup"]').first().click();await page.locator('[name="kind"][value="mock"]').check({force:true});await page.locator('[name="mode"][value="training"]').check({force:true});
for(const c of await page.locator('[name="problem"]').all())await c.uncheck({force:true});await page.locator('[name="problem"][value="pair"]').check({force:true});await page.locator('[name="problem"][value="brackets"]').check({force:true});
await page.locator('#setup-model').selectOption('test-model');await page.locator('#setup-effort').selectOption('high');
await page.locator('[data-action="start"]').click();await page.locator('#editor .monaco-editor').waitFor();
await page.screenshot({path:'/tmp/lab-workspace.png',fullPage:true});
await page.locator('#editor').click({position:{x:180,y:40}});await page.keyboard.press('Control+A');await page.keyboard.insertText('import java.util.*;\nclass Solution { void f() { ArrayList<String> list = new ArrayList<>(); list.');await page.keyboard.press('Control+Space');await page.locator('.suggest-widget.visible .monaco-list-row').first().waitFor({timeout:90000});await page.keyboard.press('Tab');
await page.waitForFunction(()=>document.querySelector('#editor .view-lines').textContent.includes('list.add'));
assert.match(await page.locator('#editor .view-lines').textContent(),/import/);

await page.locator('#editor').click({position:{x:180,y:40}});await page.keyboard.press('Control+A');await page.keyboard.insertText('class Solution { public boolean solution(int[] a, int t) { java.util.Set<Integer> seen=new java.util.HashSet<>(); for(int x:a) { if(seen.contains(t-x)) return true; seen.add(x); } return false; } }');
await page.locator('[data-action="run"]').click();await page.getByText('2 / 2 통과',{exact:true}).waitFor({timeout:40000});
// Hold the completed submit-job response, switch problems, then deliver it late.
let held=false,release;const gate=new Promise(resolve=>release=resolve);
await page.route('**/api/v2/job/*',async route=>{const response=await route.fetch();const body=await response.json();if(body.kind==='java'&&body.status==='completed'&&!held){held=true;await gate;}await route.fulfill({response,json:body});});
await page.locator('[data-action="submit"]').click();
for(let i=0;!held;i++){if(i>600)throw new Error('submit job did not complete');await page.waitForTimeout(100);}
await page.locator('[data-problem="brackets"]').click();assert.match(await page.locator('#statement h1').textContent(),/괄호/);
assert.equal(await page.locator('[data-action="submit"]').isDisabled(),true); // A run is still in flight.
release();
await page.waitForFunction(()=>!document.querySelector('[data-action="submit"]').disabled,null,{timeout:20000});
assert.equal(await page.locator('#run-output').getByText(/통과/).count(),0); // pair's result must not paint brackets.
assert.match(await page.locator('#run-output').textContent(),/이 문제의 코드를 실행해 보세요/);
assert.equal(await page.locator('#code-status').textContent(),'준비됨');
await page.unroute('**/api/v2/job/*');
await page.locator('[data-problem="pair"]').click();
await page.locator('#run-output').getByText('16 / 16 통과',{exact:true}).waitFor(); // 8 examples + 8 max-size performance cases.
assert.equal(await page.locator('#code-status').textContent(),'테스트 통과');
await page.locator('[data-problem="brackets"]').click();assert.equal(await page.locator('#run-output').getByText(/통과/).count(),0);
await page.locator('[data-problem="pair"]').click();await page.locator('#run-output').getByText('16 / 16 통과',{exact:true}).waitFor();
assert.equal(await page.locator('dialog[open]').count(),0);
await page.locator('#workspace-question').fill('중복된 원소도 안전하게 처리했는지 궁금해요.');
await page.locator('[data-coach="hint"]').click();
await page.locator('.inline-coach-feed').getByText(/테스트용 코치 응답/).waitFor();
assert.equal(await page.locator('dialog[open]').count(),0);
await page.locator('[data-problem="brackets"]').click();
assert.equal(await page.locator('.example-inputs dd code').first().textContent(),'([]){}');
assert.equal(await page.locator('.inline-coach-feed').getByText(/테스트용 코치 응답/).count(),0);
await page.locator('#workspace-question').fill('스택으로 접근');await page.locator('[data-coach="note"]').click();
await page.locator('.inline-coach-feed').getByText('생각을 기록했어요.',{exact:true}).waitFor();
await page.locator('[data-problem="pair"]').click();
await page.locator('.inline-coach-feed').getByText(/테스트용 코치 응답/).waitFor();
await page.locator('[data-coach="voice"]').click();
await page.getByRole('button',{name:'■ 말 마침 · 힌트 받기',exact:true}).waitFor();
assert.equal(await page.locator('dialog[open]').count(),0);
await page.waitForTimeout(400); // Capture a non-empty audio interval before stopping.
await page.locator('[data-coach="voice"]').click();
await page.getByText('음성을 알아듣지 못했어요. 다시 말하거나 글로 질문해 주세요.',{exact:true}).waitFor({timeout:60000});
await page.screenshot({path:'/tmp/lab-inline-coach.png',fullPage:true});
await page.locator('[data-action="finish"]').click();await page.locator('[data-confirm="ok"]').click();await page.getByRole('heading',{name:'Codex와 함께 복기'}).waitFor({timeout:30000});
const sid=(await (await page.request.get(base+'/api/v2/dashboard')).json()).sessions[0].id;
const s=await (await page.request.get(base+'/api/v2/session/'+sid)).json();assert.equal(s.status,'completed');assert.equal(s.runs.length,2);assert(s.events.some(e=>e.kind==='code'));assert(s.events.some(e=>e.kind==='switch'));assert(s.media.length>0);assert(s.media[0].bytes>0);
const screen=s.media.find(m=>m.kind==='screen');assert.equal(screen.status,'complete');assert.equal(screen.expected_chunks,screen.chunks);assert.equal(screen.ended,'normal');
assert.equal(await page.locator('.recording-notice').count(),0);
assert(s.events.filter(e=>e.kind==='code').every(e=>Number.isInteger(e.payload.client_seq)));
await page.locator('#timeline').fill('1000');await page.locator('[data-action="frame"]').click();await page.getByText('00:01 장면 첨부됨',{exact:true}).waitFor({timeout:30000});
// Analysis automatically prepares the recorded audio before requesting coaching.
await page.locator('[data-action="analyze"]').click();await page.getByText('처음 어떤 접근을 생각했나요?',{exact:true}).waitFor({timeout:90000});
await page.locator('#reply').fill('중복 원소를 고려해서 이전에 본 값만 집합에 넣었어요.');await page.locator('[data-action="reply"]').click();await page.waitForFunction(()=>!document.querySelector('[data-action="reply"]').disabled);
await page.locator('[data-action="report"]').click();await page.getByRole('heading',{name:'이번 연습에서 다음 연습으로'}).waitFor();
await page.screenshot({path:'/tmp/lab-review.png',fullPage:true});
const checked=await (await page.request.get(base+'/api/v2/session/'+sid)).json();assert.equal(checked.report.version,2);assert(['completed','no_speech'].includes(checked.transcription_state.status));assert.equal(checked.messages.at(-1).effort,'high');assert.equal(checked.messages.at(-1).model,'test-model');assert.equal(checked.frames.length,1);
await page.reload();await page.locator(`[data-session="${sid}"]`).first().click();await page.getByRole('heading',{name:'이번 연습에서 다음 연습으로'}).waitFor();
await page.locator('[data-action="review-home"]').click();await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/lab-mobile.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
assert.deepEqual(errors,[]);console.log('PASS: start mock training, media capture+upload, Java edit/run/submit (incl. large hidden cases), late run result kept on its own problem across switching, hint, timeline, frame extraction, local STT, debrief+report, explicit model/effort, reload persistence, mobile. Codex inference mocked; Java/media/STT real.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
