import 'monaco-editor/esm/vs/editor/editor.all.js';
import {memberBoundary,memberTemplates,rankCompletion} from './java-completion.js';
import {normalizeCompletionList,textRange,completionRange,markdown} from './lsp-completion.js';
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api.js';
import 'monaco-editor/esm/vs/basic-languages/java/java.contribution.js';
self.MonacoEnvironment={getWorker(){return new Worker('/dist/editor.worker.js',{type:'module'});}};
const suggestDark={'editorSuggestWidget.background':'#16181d','editorSuggestWidget.border':'#2b2f38',
 'editorSuggestWidget.foreground':'#c9cdd6','editorSuggestWidget.selectedBackground':'#262a33',
 'editorSuggestWidget.selectedForeground':'#eef0f4','editorSuggestWidget.highlightForeground':'#f2b544',
 'editorSuggestWidget.focusHighlightForeground':'#f2b544','editorSuggestWidgetStatus.foreground':'#8b919d',
 'editorHoverWidget.background':'#16181d','editorHoverWidget.border':'#2b2f38'};
monaco.editor.defineTheme('practice-dark',{base:'vs-dark',inherit:true,rules:[{token:'comment',foreground:'6b7280',fontStyle:'italic'},{token:'keyword',foreground:'f2a65a'},{token:'string',foreground:'9ece6a'},{token:'number',foreground:'e0af68'},{token:'type',foreground:'7dcfff'}],colors:{
 ...suggestDark,'editor.background':'#0f1114','editorGutter.background':'#0f1114','editor.lineHighlightBackground':'#161920','editor.lineHighlightBorder':'#00000000',
 'editorLineNumber.foreground':'#454b56','editorLineNumber.activeForeground':'#c4c9d2','editorCursor.foreground':'#f2b544',
 'editor.selectionBackground':'#f2b5442e','editor.inactiveSelectionBackground':'#f2b5441a','editorIndentGuide.background1':'#1e2229','editorIndentGuide.activeBackground1':'#343944',
 'editorBracketMatch.background':'#f2b54422','editorBracketMatch.border':'#f2b54466','scrollbarSlider.background':'#ffffff14','scrollbarSlider.hoverBackground':'#ffffff22'
}});
monaco.editor.defineTheme('practice-light',{base:'vs',inherit:true,rules:[{token:'comment',foreground:'8a8f98',fontStyle:'italic'},{token:'keyword',foreground:'b4540a'},{token:'string',foreground:'2f7d32'},{token:'number',foreground:'9a6700'},{token:'type',foreground:'0b6aa2'}],colors:{
 'editor.background':'#fffefb','editorGutter.background':'#fffefb','editor.lineHighlightBackground':'#f6f3ec','editor.lineHighlightBorder':'#00000000',
 'editorLineNumber.foreground':'#b9b5ab','editorLineNumber.activeForeground':'#3b3d42','editorCursor.foreground':'#b77905',
 'editor.selectionBackground':'#e9a52933','editorIndentGuide.background1':'#eeebe3','editorBracketMatch.background':'#e9a52922','editorBracketMatch.border':'#e9a52977',
 'editorSuggestWidget.highlightForeground':'#a86b00','editorSuggestWidget.focusHighlightForeground':'#a86b00','editorSuggestWidget.selectedBackground':'#f1ece1'
}});
const themeName=()=>document.documentElement.dataset.theme==='light'?'practice-light':'practice-dark';
export function syncEditorTheme(){monaco.editor.setTheme(themeName());}
let active=null;
const completionStates=new WeakMap();
function snapshot(model,token){
 const owner=active,version=model.getVersionId(),pid=owner?.pid;
 return {owner,pid,model,version,token,code:model.getValue(),valid:()=>!token?.isCancellationRequested&&active===owner&&!model.isDisposed()&&owner?.pid===pid&&model===owner?.editor.getModel()&&model.getVersionId()===version};
}
async function request(op,model,position,state,extra={}){
 const owner=state.owner;if(!state.valid()||!owner?.training)return null;
 const controller=new AbortController();owner.requests.add(controller);
 const subscription=state.token?.onCancellationRequested(()=>controller.abort());
 try{return await owner.api('/api/v2/ide',{sid:owner.sid,pid:state.pid,op,code:state.code,line:(position?.lineNumber||1)-1,character:(position?.column||1)-1,...extra},{signal:controller.signal});}
 finally{subscription?.dispose();owner.requests.delete(controller);}
}
const kindMap={1:'Text',2:'Method',3:'Function',4:'Constructor',5:'Field',6:'Variable',7:'Class',8:'Interface',9:'Module',10:'Property',11:'Unit',12:'Value',13:'Enum',14:'Keyword',15:'Snippet',16:'Color',17:'File',18:'Reference',19:'Folder',20:'EnumMember',21:'Constant',22:'Struct',23:'Event',24:'Operator',25:'TypeParameter'};
function suggestion(x,state,pos,word,member=false){
 const fallback=new monaco.Range(pos.lineNumber,word.startColumn,pos.lineNumber,word.endColumn);
 const label=x.labelDetails?{label:x.label,detail:x.labelDetails.detail,description:x.labelDetails.description}:x.label;
 const value={label,filterText:x.filterText?.trim(),sortText:rankCompletion(x,member),preselect:x.preselect,
  kind:monaco.languages.CompletionItemKind[kindMap[x.kind]||'Text'],
  insertText:x.textEdit?.newText??x.insertText??x.label,
  insertTextRules:(x.insertTextFormat===2?monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet:0)|(x.insertTextMode===1?monaco.languages.CompletionItemInsertTextRule.KeepWhitespace:0),
  detail:x.detail,documentation:markdown(x.documentation),tags:x.tags|| (x.deprecated?[1]:undefined),
  range:completionRange(x.textEdit,fallback),
  additionalTextEdits:(x.additionalTextEdits||[]).map(e=>({range:textRange(e.range),text:e.newText}))
 };
 completionStates.set(value,{state,item:x});return value;
}
monaco.languages.registerCompletionItemProvider('java',{
 triggerCharacters:['.','@','#'],
 async provideCompletionItems(model,pos,context,token){
  const owner=active,pid=owner?.pid;
  let state=snapshot(model,token);
  try{
   // Monaco may keep a pending request alive while its prefix grows. Recompute
   // against the latest buffer instead of losing the list after a cold start.
   while(!token.isCancellationRequested&&active===owner&&owner?.pid===pid&&owner.editor.getModel()===model){
    state=snapshot(model,token);
    const word=model.getWordUntilPosition(pos);
    const before=state.code.slice(0,model.getOffsetAt({lineNumber:pos.lineNumber,column:word.startColumn}));
    const templates=owner.training?memberTemplates(before,word.word):[];
    const lspContext={triggerKind:context.triggerKind===monaco.languages.CompletionTriggerKind.TriggerCharacter?2:context.triggerKind===monaco.languages.CompletionTriggerKind.TriggerForIncompleteCompletions?3:1};
    if(lspContext.triggerKind===2)lspContext.triggerCharacter=context.triggerCharacter;
    const result=normalizeCompletionList(await request('completion',model,pos,state,{context:lspContext}));
    if(state.valid())return {incomplete:!!result.isIncomplete,suggestions:result.items.map(x=>suggestion(x,state,pos,word,memberBoundary(before))).concat(templates.filter(x=>!result.items.some(i=>i.label===x.label)).map(x=>({...x,kind:monaco.languages.CompletionItemKind.Snippet,label:{label:x.label,description:x.detail},sortText:'1'+x.label,filterText:x.label,insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,range:new monaco.Range(pos.lineNumber,word.startColumn,pos.lineNumber,word.endColumn)})))};
    pos=owner.editor.getPosition();context={triggerKind:monaco.languages.CompletionTriggerKind.Invoke};
   }
  }catch(e){if(state.valid()&&e.name!=='AbortError')owner?.error(e.message);}
  return {suggestions:[]};
 },
 async resolveCompletionItem(item,token){
  const entry=completionStates.get(item);if(!entry?.item._resolve_id||!entry.state.valid()||token.isCancellationRequested)return item;
  const state={...entry.state,token};
  try{
   const resolved=await request('resolve',state.model,null,state,{resolve_id:entry.item._resolve_id});
   if(!resolved||!state.valid()||token.isCancellationRequested)return item;
   // Primary insertion and imports are eager; resolving never rewrites an accepted completion.
   return {...item,documentation:markdown(resolved.documentation)||item.documentation,detail:resolved.detail||item.detail};
  }catch{return item;}
 }
});
monaco.languages.registerSignatureHelpProvider('java',{
 signatureHelpTriggerCharacters:['(',','],signatureHelpRetriggerCharacters:[',',')'],
 async provideSignatureHelp(model,pos,token){
  const state=snapshot(model,token);
  try{const r=await request('signature',model,pos,state);if(!r||!state.valid())return null;
   return {value:{...r,activeSignature:r.activeSignature??0,activeParameter:r.activeParameter??0,signatures:(r.signatures||[]).map(s=>({...s,documentation:markdown(s.documentation),parameters:(s.parameters||[]).map(p=>({...p,documentation:markdown(p.documentation)}))}))},dispose(){}};
  }catch{return null;}
 }
});
monaco.languages.registerHoverProvider('java',{
 async provideHover(model,pos,token){const state=snapshot(model,token);try{
  const r=await request('hover',model,pos,state);if(!r||!state.valid())return null;
  const contents=(Array.isArray(r.contents)?r.contents:[r.contents]).filter(Boolean).map(c=>c.language?{value:'```'+c.language+'\n'+c.value+'\n```',isTrusted:false}:markdown(c));
  return {range:textRange(r.range),contents};
 }catch{return null;}}
});
export function createEditor(el,{sid,pid,code,training,api,onChange,onCursor,error,commands={}}){
 const models=new Map(),views=new Map(),instance=crypto.randomUUID();
 const modelFor=(id,value)=>{if(!models.has(id))models.set(id,monaco.editor.createModel(value,'java',monaco.Uri.parse('inmemory://practice/'+instance+'/'+id+'/Solution.java')));return models.get(id);};
 // Monaco 0.55.1 #5294: its pending word-highlighter rejects on model disposal.
 // No semantic highlight provider is registered here; selection highlighting stays available.
 const editor=monaco.editor.create(el,{model:modelFor(pid,code),editContext:false,language:'java',theme:themeName(),fontSize:14,fontFamily:'"JetBrains Mono", "D2Coding", Consolas, monospace',fontLigatures:false,lineHeight:23,automaticLayout:true,minimap:{enabled:false},scrollBeyondLastLine:false,tabSize:4,insertSpaces:true,quickSuggestions:training?{other:true,comments:false,strings:false}:false,wordBasedSuggestions:'off',suggestOnTriggerCharacters:training,acceptSuggestionOnEnter:'off',parameterHints:{enabled:training},snippetSuggestions:'inline',suggestFontSize:14,suggestLineHeight:24,suggest:{insertMode:'replace',showInlineDetails:true,localityBonus:true,shareSuggestSelections:false,showStatusBar:true},suggestSelection:'first',quickSuggestionsDelay:120,autoClosingBrackets:'always',autoSurround:'brackets',autoIndent:'full',padding:{top:18,bottom:18},renderLineHighlight:'line',fixedOverflowWidgets:true,contextmenu:false,occurrencesHighlight:'off',stickyScroll:{enabled:false},ariaLabel:'Java 코드 편집기'});
 // Run/submit shortcuts must win over Monaco's own bindings while typing.
 if(commands.run)editor.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.Quote,commands.run);
 if(commands.submit)editor.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyMod.Shift|monaco.KeyCode.Enter,commands.submit);
 let changing=false,diagTimer,lastError='';
 const owner={editor,sid,pid,training,api,requests:new Set(),error:m=>{if(m!==lastError){lastError=m;error(m);}}};active=owner;
 editor.onDidChangeModelContent(e=>{
  if(changing)return;onChange(editor.getValue(),e.changes.map(c=>({offset:c.rangeOffset,length:c.rangeLength,text:c.text})));clearTimeout(diagTimer);
  monaco.editor.setModelMarkers(editor.getModel(),'java',[]);
  if(training)diagTimer=setTimeout(async()=>{const model=editor.getModel();if(!model)return;const state=snapshot(model);try{const r=await request('diagnostics',model,null,state);if(!state.valid())return;monaco.editor.setModelMarkers(model,'java',(r?.diagnostics||[]).map(d=>({startLineNumber:d.range.start.line+1,startColumn:d.range.start.character+1,endLineNumber:d.range.end.line+1,endColumn:d.range.end.character+1,message:d.message,severity:d.severity===1?monaco.MarkerSeverity.Error:monaco.MarkerSeverity.Warning})));}catch(err){if(state.valid())owner.error(err.message);}},1300);
 });
 let lastCursor=0;editor.onDidChangeCursorPosition(e=>{if(Date.now()-lastCursor>1500){lastCursor=Date.now();onCursor?.(e.position);}});
 return {editor,value:()=>editor.getValue(),set(pid,code){
  clearTimeout(diagTimer);for(const r of owner.requests)r.abort();views.set(owner.pid,editor.saveViewState());changing=true;
  owner.pid=pid;editor.trigger('problem-switch','hideSuggestWidget',{});editor.setModel(modelFor(pid,code));
  if(views.has(pid))editor.restoreViewState(views.get(pid));changing=false;editor.focus();
 },readonly(){owner.training=false;for(const r of owner.requests)r.abort();editor.updateOptions({readOnly:true,quickSuggestions:false,suggestOnTriggerCharacters:false});},dispose(){clearTimeout(diagTimer);for(const r of owner.requests)r.abort();if(active===owner)active=null;editor.dispose();for(const model of models.values())model.dispose();}};
}
