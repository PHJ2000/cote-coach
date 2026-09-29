// LSP 3.17 -> Monaco. Keep protocol data separate from display labels and snippets.
export function normalizeCompletionList(response){
 const list=Array.isArray(response)?{items:response}:response||{items:[]};
 const defaults=list.itemDefaults||{};
 return {...list,items:(list.items||[]).map(item=>{
  const out={...defaults,...item};delete out.editRange;
  if(!out.textEdit&&defaults.editRange){
   const newText=item.textEditText??item.insertText??(typeof item.label==='string'?item.label:item.label.label);
   out.textEdit=defaults.editRange.insert?{...defaults.editRange,newText}:{range:defaults.editRange,newText};
  }
  return out;
 })};
}
export function textRange(r){return r?{startLineNumber:r.start.line+1,startColumn:r.start.character+1,endLineNumber:r.end.line+1,endColumn:r.end.character+1}:undefined;}
export function completionRange(edit,fallback){
 if(edit?.insert&&edit?.replace)return {insert:textRange(edit.insert),replace:textRange(edit.replace)};
 return textRange(edit?.range)||fallback;
}
export function markdown(value){
 if(!value)return undefined;
 const text=typeof value==='string'?value:value.value||'';
 // Plaintext must stay plaintext even when the editor accepts MarkdownString.
 return {value:typeof value==='object'&&value.kind==='markdown'?text:text.replace(/[\\`*_{}[\]()<>#+\-.!|]/g,'\\$&'),isTrusted:false,supportHtml:false};
}
