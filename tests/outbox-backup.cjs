const assert=require('node:assert/strict');
(async()=>{
 const {backupBlob}=await import('../client/outbox-backup.js');
 const raw=Buffer.from([0,255,128,35,10,0]),text=JSON.stringify({sid:'test',events:[{ms:144000000,payload:{code:'// 한글'}}]});
 const items=[{id:'event',url:'/api/v2/events',type:'application/json',body:text,order:1},{id:'chunk',url:'/api/v2/upload?start=144000000',type:'application/octet-stream',body:new Blob([raw],{type:'video/webm'}),order:2}];
 const backup=JSON.parse(await (await backupBlob(items,'http://localhost:8766')).text());
 assert.equal(backup.count,2);assert.equal(backup.items[0].body.data,text);
 assert.deepEqual(Buffer.from(backup.items[1].body.data,'base64'),raw);assert.equal(backup.items[1].body.size,raw.length);
 assert.equal(items[0].body,text);assert(items[1].body instanceof Blob);
 assert.equal(backup.items[1].url,items[1].url);
 console.log('PASS read-only backup serialization: binary bytes, Unicode code, long timestamp, original IDs/order/URLs unchanged.');
})().catch(e=>{console.error(e);process.exitCode=1;});
