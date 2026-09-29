const {chromium}=require('playwright');
const {expect}=require('playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');

(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({baseURL:'http://localhost:8876',viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const r=await page.request.post('/api/v2/start',{data:{problem_ids:['brackets'],kind:'single',mode:'exam',resources:'none',duration:20,model:'test-model',effort:'high'}});
  assert(r.ok());const s=await r.json();await page.request.post('/api/v2/finish',{data:{sid:s.id}});
  await page.goto('/');await page.locator(`[data-session="${s.id}"]`).first().click();
  await page.locator('#reply').fill('입력 표기에서 따옴표가 포함되는지 헷갈렸어요.');
  await page.locator('[data-action="report"]').click();
  await page.getByRole('heading',{name:'풀이 흐름에서 찾은 점'}).waitFor();
  assert.equal(await page.locator('dialog[open]').count(),0);
  await expect(page.getByText('세 입력의 결과를 정확히 예측',{exact:true})).toBeVisible();
  await expect(page.locator('#reply')).toHaveValue('');
  await page.screenshot({path:'/tmp/cote-report-desktop.png',fullPage:true});
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('[data-action="export-evidence"]').click()]);
  const exported=JSON.parse(await fs.readFile(await download.path(),'utf8'));
  assert.equal(exported.report.version,2);assert.equal(exported.messages.at(-1).kind,'report');
  assert.equal(exported.analysis_inputs.at(-1).input.user_answer,'입력 표기에서 따옴표가 포함되는지 헷갈렸어요.');
  assert.equal(exported.analysis_inputs.at(-1).input.evidence.transcription_state.status,'missing');
  assert(exported.events.some(e=>e.kind==='finish'));
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'/tmp/cote-report-mobile.png',fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.reload();await page.locator(`[data-session="${s.id}"]`).first().click();
  await page.getByRole('heading',{name:'풀이 흐름에서 찾은 점'}).waitFor();
  assert.deepEqual(errors,[]);
  console.log('PASS report v2 sections, direct request without dialog, draft answer, complete JSON download, mobile layout and reload.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
