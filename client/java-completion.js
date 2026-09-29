// JDT decides semantic candidates. These templates only apply at a class-member boundary.
export function memberBoundary(before) {
 let clean='',mode='code';
 for(let i=0;i<before.length;i++){
  const c=before[i],next=before[i+1];
  if(mode==='line'){if(c==='\n'){mode='code';clean+='\n';}else clean+=' ';continue;}
  if(mode==='block'){if(c==='*'&&next==='/'){mode='code';clean+='  ';i++;}else clean+=c==='\n'?'\n':' ';continue;}
  if(mode==='text'){if(before.slice(i,i+3)==='"""'){mode='code';clean+='   ';i+=2;}else if(c==='\\'){clean+='  ';i++;}else clean+=c==='\n'?'\n':' ';continue;}
  if(mode==='string'||mode==='char'){if(c==='\\'){clean+='  ';i++;}else{if(c===(mode==='string'?'"':"'"))mode='code';clean+=' ';}continue;}
  if(c==='/'&&next==='/'){mode='line';clean+='  ';i++;}
  else if(c==='/'&&next==='*'){mode='block';clean+='  ';i++;}
  else if(before.slice(i,i+3)==='"""'){mode='text';clean+='   ';i+=2;}
  else if(c==='"'||c==="'"){mode=c==='"'?'string':'char';clean+=' ';}
  else clean+=c;
 }
 if(mode!=='code')return false;
 const stack=[];let boundary=0;
 for(let i=0;i<clean.length;i++){
  if(clean[i]==='{'){stack.push(/\b(?:class|record)\s+[\w$]+/.test(clean.slice(boundary,i))?'type':'block');boundary=i+1;}
  else if(clean[i]==='}'){stack.pop();boundary=i+1;}
  else if(clean[i]===';')boundary=i+1;
 }
 return stack.at(-1)==='type' && clean.slice(boundary).trim()==='';
}
const templates=[
 ['prsf','private static final','private static final ${1:int} ${2:NAME} = ${3:0};$0'],
 ['psf','public static final','public static final ${1:int} ${2:NAME} = ${3:0};$0'],
 ['psfi','public static final int','public static final int ${1:NAME} = ${2:0};$0'],
 ['psfs','public static final String','public static final String ${1:NAME} = "${2:value}";$0'],
 ['psvm','public static void main','public static void main(String[] args) {\n    $0\n}']
];
export function memberTemplates(before,prefix){
 if(!prefix||!memberBoundary(before))return [];
 return templates.filter(([label])=>label.startsWith(prefix.toLowerCase())).map(([label,detail,insertText])=>({label,detail,insertText}));
}
export function rankCompletion(item,member=false){
 if(!member)return item.sortText||item.label;
 const group=item.kind===14?'0':item.kind===15?'1':[6,5,2,3].includes(item.kind)?'2':'3';
 return group+(item.sortText||item.label);
}
