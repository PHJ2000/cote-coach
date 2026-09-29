const {chromium}=require('playwright');
const {expect}=require('playwright/test');
const assert=require('node:assert/strict');

(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({baseURL:'http://localhost:8876',permissions:['clipboard-read','clipboard-write']});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const response=await page.request.post('/api/v2/start',{data:{problem_ids:['brackets'],kind:'single',mode:'exam',resources:'none',duration:40,model:'test-model',effort:'high'}});
  assert(response.ok());const session=await response.json();
  await page.goto('/');await page.locator(`[data-session="${session.id}"]`).first().click();
  await page.locator('#editor .monaco-editor').waitFor();
  const prefix='class Solution {\n    boolean solution(String text) {\n';
  async function replace(code){
   await page.locator('#editor').click();await page.keyboard.press('Escape');await page.keyboard.press('Control+A');
   await page.evaluate(text=>navigator.clipboard.writeText(text),code);await page.keyboard.press('Control+V');
   await saved(code);
   await page.keyboard.press('Control+End');
  }
  async function saved(expected){
   await expect.poll(async()=> (await (await page.request.get('/api/v2/session/'+session.id)).json()).codes.brackets,{timeout:10000}).toBe(expected);
  }
  for(const header of ['if (text.isEmpty())','if (text.isEmpty() || text.charAt(0) == \')\')',"if (text.charAt(0) == '{')", "if (text.charAt(0) == '[')",'if (text.equals("//"))','else if (text.length() > 1)','else','for (int i = 0; i < 3; i++)','for (char c : text.toCharArray())','while (!text.isEmpty())','do','if (text.isEmpty()) // empty input','if (text.isEmpty()) // { example','if (text.isEmpty()) /* empty input */']){
   const code=prefix+'        '+header;
   await replace(code);await page.keyboard.press('Enter');await page.keyboard.type('return false;',{delay:20});
   await page.keyboard.press('Enter');await page.keyboard.type('// next');
   await saved(code+'\n            return false;\n        // next');
   console.log('PASS indent and restore: '+header);
  }
  for(const line of ['if (text.isEmpty()) return false;','while (text.isEmpty());','// if (text.isEmpty())','String value = "if (true)";','return text.isEmpty();']){
   const code=prefix+'        '+line;
   await replace(code);await page.keyboard.press('Enter');await page.keyboard.type('// next');
   await saved(code+'\n        // next');
  }
  console.log('PASS no extra indent for complete statements, comments and strings');
  const nested=prefix+'        if (text != null)\n            if (text.isEmpty())';
  await replace(nested);await page.keyboard.press('Enter');await page.keyboard.type('return false;',{delay:20});await page.keyboard.press('Enter');await page.keyboard.type('// next');
  await saved(nested+'\n                return false;\n        // next');
  const block=prefix+'        if (text.isEmpty()) {}';
  await replace(block);await page.keyboard.press('ArrowLeft');await page.keyboard.press('Enter');await page.keyboard.type('return false;');
  await saved(prefix+'        if (text.isEmpty()) {\n            return false;\n        }');
  console.log('PASS nested conditions and existing brace enter behavior');
  const undo=prefix+'        if (text.isEmpty())';
  await replace(undo);await saved(undo);await page.keyboard.press('Enter');await saved(undo+'\n            ');
  await page.keyboard.press('Control+z');await saved(undo);
  assert.deepEqual(errors,[]);console.log('PASS Enter undo and no browser errors');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
