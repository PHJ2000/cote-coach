const assert=require('node:assert/strict');
(async()=>{
 const {createSegmentPlayback}=await import('../client/segment-playback.js');
 const segment=(id,start,end,playable=true)=>({kind:'screen',segment:id,start_ms:start,end_ms:end,playable,url:`/${id}`});
 function fixture(segments){
  const states=[],times=[],durations=[];
  const video={dataset:{},readyState:0,duration:NaN,currentTime:0,paused:true,hidden:false,
   pause(){this.paused=true;},play(){this.paused=false;return Promise.resolve();}};
  Object.defineProperty(video,'src',{set(value){this.source=value;this.readyState=0;}});
  const player=createSegmentPlayback({video,segments,onState:s=>states.push(s),onTime:t=>times.push(t),onDuration:(...x)=>durations.push(x)});
  const metadata=duration=>{video.readyState=1;video.duration=duration;video.onloadedmetadata?.();};
  return {player,video,states,times,durations,metadata};
 }
 let f=fixture([segment('b',6000,8000),segment('a',1000,3000)]);
 f.player.seek(0);assert.equal(f.states.at(-1).state,'gap');assert(f.video.hidden);
 f.player.first();f.metadata(2);assert.equal(f.video.source,'/a');assert(!f.video.paused);
 f.video.currentTime=1;f.video.ontimeupdate();assert.equal(f.times.at(-1),2000);
 f.video.onended();assert.equal(f.video.source,'/b');assert.equal(f.times.at(-1),6000);assert.equal(f.states.at(-1).state,'transition');
 f.metadata(2);assert(!f.video.paused);f.video.onended();assert.equal(f.states.at(-1).state,'finished');
 f.player.seek(4000);assert(f.video.hidden);assert.equal(f.states.at(-1).next.segment,'b');
 f.player.seek(1500,false);f.metadata(2);assert(!f.video.hidden);assert(f.video.paused);assert.equal(f.video.currentTime,.5);
 f.player.dispose();assert.equal(f.video.onended,null);assert.equal(f.video.ontimeupdate,null);
 // A legacy segment with unknown duration cannot cover a later missing segment.
 f=fixture([segment('a',0,null),segment('missing',5000,null,false),segment('b',9000,11000)]);
 f.player.seek(6000);assert.equal(f.states.at(-1).state,'gap');assert.equal(f.states.at(-1).next.segment,'b');
 f.player.seek(4000);f.metadata(2);assert(f.video.hidden);assert.equal(f.states.at(-1).state,'gap');
 f.player.seek(1000,false);assert(!f.video.hidden);assert.equal(f.video.currentTime,1);
 f.player.next(6000);f.video.onerror();assert.equal(f.states.at(-1).state,'error');
 // Ignore metadata from a superseded request after scrubbing to another capture.
 f=fixture([segment('a',0,2000),segment('b',5000,7000)]);
 f.player.seek(0);const stale=f.video.onloadedmetadata;f.player.seek(5000);f.video.duration=2;stale();assert.equal(f.durations.length,0);
 f.metadata(2);assert.equal(f.durations[0][0],'b');
 console.log('PASS segment playback: chronological switching, gaps, missing containers, legacy unknown tail, seek back, paused seek, errors, stale metadata, dispose.');
})().catch(e=>{console.error(e);process.exitCode=1;});
