// Pure rendering/event regression checks. No browser, network, or user records.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
(async()=>{
 const ui=await import('../client/ui.js');
 const nodes=new Map(),listeners={};
 function node(key){if(!nodes.has(key))nodes.set(key,{innerHTML:'',textContent:'',open:false,dataset:{},attributes:{},classList:{toggle(){},remove(){}},addEventListener(){},setAttribute(k,v){this.attributes[k]=v;},showModal(){this.open=true;},close(){this.open=false;}});return nodes.get(key);}
 const buttons=['','해시','배열·문자열','DP'].map(t=>Object.assign(node('tag-'+t),{dataset:{libTag:t}}));
 const problem=(id,title,tags)=>({id,title,tags,level:1,description:'두 입력의 결과를 구하세요.',source:'자체 문제',url:'',returns:'int',method:'solution',params:[{type:'int',name:'n'}],tests:[{args:[1],expected:2,public:true}],test_count:1});
 const problems=[problem('a','손님 명단',['해시','배열·문자열']),problem('b','교환권',['DP']),problem('c','다른 명단',['해시'])];
 const dashboard={problems,stats:{sessions:0,problems:0,independent:0,days:0},sessions:[],cohorts:[],weaknesses:{},recommendations:problems.map(problem=>({problem,reason:'복기에서 확인한 '+problem.tags.join(', ')+' 연습'}))};
 const context=vm.createContext({...ui,theme:()=>"light",$:node,$$:s=>s==='[data-lib-tag]'?buttons:[],console,
  document:{addEventListener:(n,f)=>listeners[n]=f,body:{classList:{remove(){}}}},
  window:{addEventListener(){},scrollTo(){}},setTimeout,clearTimeout,setInterval,clearInterval,
  fetch:async()=>({ok:true,json:async()=>dashboard})});
 const source=fs.readFileSync(path.join(__dirname,'../client/app.js'),'utf8').replace(/^import .*;\n/gm,'').replace(/\nboot\(\);\s*$/,'\n');
 vm.runInContext(source,context);
 vm.runInContext('dashboard='+JSON.stringify(dashboard)+';health={};',context);
 const run=code=>vm.runInContext(code,context);
 const hasTypes=html=>/해시|배열·문자열|DP/.test(html);
 run('home()');assert(!hasTypes(node('#app').innerHTML),'home recommendations expose technique');
 assert.equal(run("recommendationReason({problem:dashboard.problems[0],reason:'3일 이상 경과'})"),'3일 이상 경과');
 run('libraryView()');
 assert.match(node('#app').innerHTML,/<details class="type-filter" id="lib-type-filter">/);
 assert(!/<details[^>]*id="lib-type-filter"[^>]*\bopen\b/.test(node('#app').innerHTML));
 assert(!hasTypes(node('#lib-grid').innerHTML),'cards expose type metadata');
 assert.equal((node('#lib-grid').innerHTML.match(/class="problem-card"/g)||[]).length,3);
 const click=async tag=>listeners.click({target:{closest:()=>({dataset:{libTag:tag}})}});
 await click('해시');assert.equal((node('#lib-grid').innerHTML.match(/class="problem-card"/g)||[]).length,2);
 assert.equal(buttons[1].attributes['aria-pressed'],'true');assert.equal(node('#lib-type-status').textContent,'필터 적용 중');
 assert(!hasTypes(node('#lib-grid').innerHTML),'filtered cards expose type metadata');
 listeners.input({target:{id:'lib-search',value:'손님'}});assert.match(node('#lib-grid').innerHTML,/손님 명단/);assert(!node('#lib-grid').innerHTML.includes('다른 명단'));
 await click('DP');assert.match(node('#lib-grid').innerHTML,/조건에 맞는 문제가 없어요/);
 await click('');assert.match(node('#lib-grid').innerHTML,/손님 명단/);assert.equal(node('#lib-type-status').textContent,'');
 listeners.input({target:{id:'lib-search',value:''}});assert.equal((node('#lib-grid').innerHTML.match(/class="problem-card"/g)||[]).length,3);
 await click('해시');await click('해시');assert.equal(run('library.tag'),'');
 await click('DP');await run("navigate('home')");assert(!hasTypes(node('#app').innerHTML));
 await run("navigate('library')");assert.equal(run('library.tag'),'DP');assert(!/<details[^>]*id="lib-type-filter"[^>]*\bopen\b/.test(node('#app').innerHTML));assert(!hasTypes(node('#lib-grid').innerHTML));
 node('#lib-type-filter').open=true;
 run('fillEfforts=()=>{};updateSetupSummary=()=>{};setup("b")');
 assert.equal(node('#lib-type-filter').open,false);assert(!hasTypes(node('#modal-body').innerHTML),'picker exposes tags');assert.match(node('#modal-body').innerHTML,/교환권/);
 for(const p of problems){run('session={problems:dashboard.problems};pid='+JSON.stringify(p.id)+';statement()');assert(!hasTypes(node('#statement').innerHTML),'statement exposes tags');assert(node('#statement').innerHTML.includes(p.title));assert.match(node('#statement').innerHTML,/Lv.1/);}
 console.log('PASS tag visibility: home reasons/cards/picker/statements, closed filter, text+type search, empty result, toggle/clear, aria-pressed, route transitions.');
})().catch(e=>{console.error(e);process.exitCode=1});
