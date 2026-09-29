const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage({baseURL:'http://localhost:8876',viewport:{width:1500,height:950}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const response=await page.request.post('/api/v2/start',{data:{problem_ids:['brackets'],kind:'single',mode:'training',resources:'none',duration:40,model:'test-model',effort:'high'}});const session=await response.json();assert(response.ok());
 await page.goto('/');await page.locator(`[data-session="${session.id}"]`).first().click();await page.locator('#editor .monaco-editor').waitFor();
 async function code(prefix,typed){await page.locator('#editor').click();await page.keyboard.press('Control+A');await page.keyboard.insertText(prefix);await page.keyboard.type(typed,{delay:50});}
 await code('class Solution {\n    ','p');
 const rows=page.locator('.suggest-widget.visible .monaco-list-row');await rows.filter({hasText:'private'}).first().waitFor({timeout:90000});
 const labels=await rows.locator('.label-name').allTextContents();assert.deepEqual(labels.slice(0,3),['private','protected','public']);
 assert(labels.includes('psvm'));
 const rowBox=await rows.first().boundingBox(),labelBox=await rows.first().locator('.label-name').boundingBox();assert(labelBox.x-rowBox.x<50,'App layout CSS must not shift completion labels');
 await page.screenshot({path:'/tmp/cote-completion-keywords.png'});
 require('node:fs').mkdirSync('artifacts',{recursive:true});
 const popup=await page.locator('.suggest-widget.visible').boundingBox();
 await page.screenshot({path:'artifacts/autocomplete-ui.png',clip:{x:Math.max(0,popup.x-90),y:Math.max(0,popup.y-65),width:popup.width+112,height:popup.height+90}});
 await rows.filter({has:page.locator('.label-name', {hasText:/^public$/})}).click();await page.keyboard.press('Tab');await page.waitForFunction(()=>document.querySelector('#editor .view-lines').textContent.includes('public')); 
 await code('class Solution {\n    ','psvm');await rows.filter({hasText:'psvm'}).first().waitFor({timeout:30000});await page.keyboard.press('Tab');
 await page.waitForFunction(()=>document.querySelector('#editor .view-lines').textContent.includes('main'));
 const expanded=(await page.locator('#editor .view-lines').textContent()).replaceAll('\u00a0',' ');assert.match(expanded,/public static void main\(String\[\] args\)/);assert(!expanded.includes('$0'));
 const bodyCompletion=page.waitForResponse(r=>r.url().endsWith('/api/v2/ide')&&r.request().postDataJSON()?.op==='completion'&&r.request().postDataJSON()?.code.includes('public void run()'));
 await code('class Solution {\n public void run() {\n    ','p');await page.keyboard.press('Control+Space');await bodyCompletion;
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const bodyLabels=await rows.locator('.label-name').allTextContents();for(const invalid of ['private','protected','public','psvm'])assert(!bodyLabels.includes(invalid),invalid+' leaked into method body');
 assert.deepEqual(errors,[]);console.log('PASS: automatic p → private/protected/public first; public insertion; psvm expansion with live tab stops; class templates excluded from method body. Real JDT LS.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
