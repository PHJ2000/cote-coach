// Recovery: when the outbox cannot be delivered, the editor opens with a queued edit only if it
// is newer (ms, seq) than the server's code. Session responses are stripped of code_versions to
// simulate a pre-versioning server/session, so the browser must infer versions from events.
// Uses tests/lab_fixture.py (port 8876).
const {chromium}=require('playwright');
const assert=require('node:assert/strict');

async function scenario(browser,{server,queued,expectEditor,label}){
 const context=await browser.newContext({baseURL:'http://localhost:8876',viewport:{width:1440,height:1000}});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const s=await (await page.request.post('/api/v2/start',{data:{problem_ids:['pair','brackets'],kind:'mock',mode:'exam',resources:'none',duration:30,model:'test-model',effort:'high'}})).json();
 const post=events=>page.request.post('/api/v2/events',{data:{sid:s.id,events}});
 const codes=async()=>(await (await page.request.get('/api/v2/session/'+s.id)).json()).codes;
 await post([{id:'server',ms:server.ms,seq:server.seq,kind:'code',pid:'pair',payload:{code:server.code}}]);
 await page.goto('/');await page.locator(`[data-session="${s.id}"]`).first().waitFor();
 // A queued edit left in IndexedDB by an earlier tab, while the network is down.
 await page.evaluate(async ({sid,queued})=>{const d=await new Promise((ok,no)=>{const r=indexedDB.open('cote-lab-outbox',1);r.onupgradeneeded=()=>r.result.createObjectStore('items',{keyPath:'id'});r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error);});
  const e={id:'queued',ms:queued.ms,seq:queued.seq,kind:'code',pid:'pair',payload:{code:queued.code}};
  await new Promise((ok,no)=>{const tx=d.transaction('items','readwrite');tx.objectStore('items').put({id:e.id,url:'/api/v2/events',type:'application/json',body:JSON.stringify({sid,events:[e]}),order:1});tx.oncomplete=ok;tx.onerror=()=>no(tx.error);});d.close();},{sid:s.id,queued});
 await page.route('**/api/v2/events',route=>route.abort());
 await page.route('**/api/v2/session/'+s.id,async route=>{const response=await route.fetch();const body=await response.json();delete body.code_versions;await route.fulfill({response,json:body});});
 await page.locator(`[data-session="${s.id}"]`).first().click();await page.locator('#editor .monaco-editor').waitFor();
 await page.waitForFunction(text=>document.querySelector('#editor .view-lines').textContent.includes(text),expectEditor);
 const shown=await page.locator('#editor .view-lines').textContent();
 for(const other of [server.code,queued.code].filter(c=>c!==expectEditor))assert(!shown.includes(other),`${label}: editor must not show ${other}`);
 assert.equal((await codes()).pair,server.code);
 // Deliver the queue: the server applies the same rule (newer wins, older stays history).
 await page.unroute('**/api/v2/events');await page.evaluate(()=>dispatchEvent(new Event('online')));
 const final=queued.ms>server.ms||(queued.ms===server.ms&&queued.seq>=server.seq)?queued.code:server.code;
 for(let i=0;;i++){const view=await (await page.request.get('/api/v2/session/'+s.id)).json();if(view.events.some(e=>e.id==='queued'))break;if(i>100)throw new Error(label+': outbox was not delivered');await page.waitForTimeout(100);}
 assert.equal((await codes()).pair,final);
 await page.request.post('/api/v2/finish',{data:{sid:s.id}});
 assert.deepEqual(errors,[]);await context.close();
}

(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  // Newer queued edit (later ms) must open over the older server copy.
  // Codes have no spaces so Monaco's rendered text can be matched directly.
  await scenario(browser,{label:'newer queued',server:{ms:1000,seq:1,code:'//server_old'},queued:{ms:2000,seq:2,code:'//unsent_newest'},expectEditor:'//unsent_newest'});
  // Older queued edit must not reopen over newer server code.
  await scenario(browser,{label:'older queued',server:{ms:3000,seq:5,code:'//server_newer'},queued:{ms:2000,seq:9,code:'//stale_local'},expectEditor:'//server_newer'});
  // Same ms: the explicit sequence decides, in both directions.
  await scenario(browser,{label:'same ms lower seq',server:{ms:2000,seq:5,code:'//server_seq5'},queued:{ms:2000,seq:4,code:'//local_seq4'},expectEditor:'//server_seq5'});
  await scenario(browser,{label:'same ms higher seq',server:{ms:2000,seq:5,code:'//server_seq5'},queued:{ms:2000,seq:6,code:'//local_seq6'},expectEditor:'//local_seq6'});
  console.log('PASS recovery: queued edits overlay only when newer by (ms, seq), including sessions without code_versions; delivery follows the same rule.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
