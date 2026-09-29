const assert=require('node:assert/strict');
(async()=>{
 const {normalizeCompletionList,completionRange,markdown}=await import('../client/lsp-completion.js');
 const {memberBoundary,memberTemplates,rankCompletion}=await import('../client/java-completion.js');
 const insert={start:{line:1,character:3},end:{line:1,character:6}},replace={start:{line:1,character:3},end:{line:1,character:10}};
 const list=normalizeCompletionList({isIncomplete:true,itemDefaults:{insertTextFormat:2,editRange:{insert,replace},data:{request:1}},items:[{label:'substring',textEditText:'substring(${1:beginIndex})'},{label:'length',insertTextFormat:1,insertText:'length()'}]});
 assert.equal(list.items[0].textEdit.newText,'substring(${1:beginIndex})');assert.equal(list.items[1].insertTextFormat,1);assert.deepEqual(completionRange(list.items[0].textEdit).replace,{startLineNumber:2,startColumn:4,endLineNumber:2,endColumn:11});
 assert.equal(normalizeCompletionList([{label:'x',additionalTextEdits:[{range:insert,newText:''}]}]).items[0].additionalTextEdits[0].newText,'');
 assert.equal(markdown({kind:'markdown',value:'[run](command:deleteAll)'}).isTrusted,false);assert.equal(markdown({kind:'markdown',value:'<img src=x>'}).supportHtml,false);assert.match(markdown({kind:'plaintext',value:'<script>'}).value,/\\</);
 for(const s of ['class A {\n','class Outer { class Inner {\n','class A { void run() {}\n'])assert(memberBoundary(s),s);
 for(const s of ['class A { void run() {\n','class A { // ','class A { /* ','class A { String x = "','class A { String x="""\n','interface A {\n','class A { Runnable r=()->{\n'])assert(!memberBoundary(s),s);
 assert.equal(memberTemplates('class A { // ','p').length,0);assert(memberTemplates('class A {\n','psvm').length);
 assert.equal(rankCompletion({kind:6,label:'result',sortText:'0001'}),'0001');
 console.log('PASS: LSP defaults, insert/replace, empty edits, untrusted docs, comment/string/text-block/lambda/nested-class contexts, server relevance.');
})().catch(e=>{console.error(e);process.exitCode=1});
