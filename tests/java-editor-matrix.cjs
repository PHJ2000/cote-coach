const {chromium}=require('playwright');const {expect}=require('playwright/test');const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage({baseURL:'http://localhost:8876',viewport:{width:1500,height:950},permissions:['clipboard-read','clipboard-write']});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const response=await page.request.post('/api/v2/start',{data:{problem_ids:['brackets','pair'],kind:'mock',mode:'training',resources:'none',duration:40,model:'test-model',effort:'high'}});const s=await response.json();assert(response.ok());
 await page.goto('/');await page.locator(`[data-session="${s.id}"]`).first().click();await page.locator('#editor .monaco-editor').waitFor();
 const rows=page.locator('.suggest-widget.visible .monaco-list-row');
 async function replace(code){await page.locator('#editor').click();await page.keyboard.press('Escape');await page.keyboard.press('Control+A');await page.evaluate(text=>navigator.clipboard.writeText(text),code);await page.keyboard.press('Control+V');await expect(page.locator('#editor .view-lines')).toContainText(code.trim().split('\n').at(-1).trim());}
 async function saved(contains,pid='brackets'){
  await expect.poll(async()=> (await (await page.request.get('/api/v2/session/'+s.id)).json()).codes[pid],{timeout:15000}).toContain(contains);return (await (await page.request.get('/api/v2/session/'+s.id)).json()).codes[pid];
 }
 async function choose(text){await page.keyboard.press('Control+Space');const row=rows.filter({hasText:text}).first();await row.waitFor({timeout:30000});const target=Number(await row.getAttribute('data-index'));const current=Number(await page.locator('.suggest-widget.visible .monaco-list-row.focused').getAttribute('data-index'));for(let i=0;i<Math.abs(target-current);i++)await page.keyboard.press(target>current?'ArrowDown':'ArrowUp');await page.keyboard.press('Tab');}
 // Type import + method snippet + argument tabstop produce compilable code.
 await replace('import java.util.HashMap;\nclass Solution {\n    public boolean solution(String text) {\n        ArrayL');
 await choose('ArrayList');let code=await saved('ArrayList');assert.match(code,/import java.util.ArrayList;/);assert.match(code,/import java.util.HashMap;/);
 await page.keyboard.insertText('<String> items = new ArrayList<>();\n        items.ad');await choose('add(String');
 await page.keyboard.type('"abc"',{delay:40});await page.keyboard.press('Tab');await page.keyboard.insertText(';\n        return text.length() == 6;\n    }\n}');
 code=await saved('return text.length() == 6');assert.match(code,/items\.add\("abc"\)/);assert(!code.includes('${'));assert.match(code,/import java.util.HashMap;/);
 const run=await (await page.request.post('/api/v2/run',{data:{sid:s.id,pid:'brackets',submit:false}})).json();
 await expect.poll(async()=> (await (await page.request.get('/api/v2/job/'+run.job_id)).json()).status,{timeout:30000}).toMatch(/completed|failed/);
 const job=await (await page.request.get('/api/v2/job/'+run.job_id)).json();assert.equal(job.status,'completed');assert.equal(job.result.result.status,'passed',JSON.stringify(job.result));console.log('PASS import preservation + method arguments + Tab placeholders + real javac/run');
 // Complete in the middle of an existing identifier: suffix must not be duplicated.
 await replace('class Solution { void run(String text) { text.substring(1); } }');
 await page.keyboard.press('Control+Home');for(let i=0;i<'class Solution { void run(String text) { text.sub'.length;i++)await page.keyboard.press('ArrowRight');
 await choose('substring');code=(await page.locator('#editor .view-lines').innerText()).replaceAll('\u00a0',' ');assert(!code.includes('substringstring'));console.log('PASS completion in middle of identifier');
 // Problem switching preserves separate buffers, undo and caret.
 await replace('class Solution {\n    int original;\n}');await saved('int original;');
 await page.keyboard.press('Control+End');await page.keyboard.press('Enter');await page.keyboard.type('// marker');await saved('// marker');
 await page.locator('[data-problem="pair"]').click();await replace('class Solution { int other; }');await saved('int other;','pair');
 await page.locator('[data-problem="brackets"]').click();await page.keyboard.insertText(' continued');await saved('// marker continued');
 await page.keyboard.press('Control+z');await expect.poll(async()=> (await (await page.request.get('/api/v2/session/'+s.id)).json()).codes.brackets,{timeout:15000}).not.toContain('continued');
 assert.match((await (await page.request.get('/api/v2/session/'+s.id)).json()).codes.pair,/int other;/);console.log('PASS per-problem caret and undo isolation');
 // A real JDT response delayed by the network must refresh after more typing.
 let release,seen;const started=new Promise(r=>seen=r);let delayed=false;
 await page.route('**/api/v2/ide',async route=>{if(route.request().postDataJSON()?.op!=='completion'||delayed)return route.continue();delayed=true;const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});});
 await replace('class Solution { void run(String text) { text.');await page.keyboard.press('Control+Space');await started;
 await page.keyboard.type('sub',{delay:20});release();await rows.filter({hasText:'substring'}).first().waitFor({timeout:30000});
 assert.equal(await rows.filter({hasText:/^length\(/}).count(),0);await page.unroute('**/api/v2/ide');console.log('PASS fast typing while real completion response is delayed');
 // Manual completion inside a comment must not offer the app's class-member aliases.
 await replace('class Solution {\n    // p');const commentResponse=page.waitForResponse(r=>r.url().endsWith('/api/v2/ide')&&r.request().postDataJSON()?.op==='completion');await page.keyboard.press('Control+Space');await commentResponse;await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 assert.equal(await rows.filter({hasText:'psvm'}).count(),0);console.log('PASS no member template in comment');
 assert.deepEqual(errors,[]);console.log('PASS browser insertion matrix (real JDT and Docker, no Codex inference).');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
