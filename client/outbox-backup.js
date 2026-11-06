// Read-only snapshot. Never deliver, delete, normalize, or rewrite outbox items.
export async function readOutbox(){
 return new Promise((resolve,reject)=>{
  const request=indexedDB.open('cote-lab-outbox',1);let absent=false;
  request.onupgradeneeded=()=>{absent=true;request.transaction.abort();};
  request.onerror=()=>absent?resolve([]):reject(request.error);
  request.onsuccess=()=>{
   const db=request.result;
   if(!db.objectStoreNames.contains('items')){db.close();resolve([]);return;}
   const tx=db.transaction('items','readonly'),items=tx.objectStore('items').getAll();
   tx.oncomplete=()=>{db.close();resolve(items.result);};
   tx.onerror=()=>{db.close();reject(tx.error);};
  };
 });
}
export async function backupBlob(items,origin){
 const pieces=[JSON.stringify({format:'cote-lab-outbox-backup',version:1,created_at:new Date().toISOString(),origin,count:items.length}).slice(0,-1)+',"items":['];
 for(let i=0;i<items.length;i++){
  const item=items[i];let body;
  if(item.body instanceof Blob){
   const bytes=new Uint8Array(await item.body.arrayBuffer());let binary='';
   for(let j=0;j<bytes.length;j+=32768)binary+=String.fromCharCode(...bytes.subarray(j,j+32768));
   body={encoding:'base64',mime:item.body.type,size:item.body.size,data:btoa(binary)};
  }else if(typeof item.body==='string')body={encoding:'text',data:item.body};
  else throw new Error('알 수 없는 기록 형식이 있어 백업을 중단했어요. 원본은 그대로 있어요.');
  pieces.push((i?',':'')+JSON.stringify({...item,body}));
 }
 pieces.push(']}');return new Blob(pieces,{type:'application/json'});
}
