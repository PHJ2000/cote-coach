const assert=require('node:assert/strict');
(async()=>{
 const {formatSessionClock:f}=await import('../client/session-clock.js');
 assert.equal(f(2400000),'40:00');assert.equal(f(0),'00:00');assert.equal(f(-51000),'+00:51');
 assert.equal(f(-(39*3600+1*60+51)*1000),'+1일 15:01:51');
 assert.equal(f(-86400000),'+1일 00:00:00');
 console.log('PASS session clock: normal, deadline, overtime and multi-day elapsed display without truncation.');
})().catch(e=>{console.error(e);process.exitCode=1;});
