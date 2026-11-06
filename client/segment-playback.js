// Every capture keeps its own container. Playback switches sources, never bytes.
export function createSegmentPlayback({video,segments,onTime=()=>{},onState=()=>{},onDuration=()=>{}}){
 const screens=segments.filter(s=>s.kind==='screen').map(s=>({...s})).sort((a,b)=>a.start_ms-b.start_ms||a.segment.localeCompare(b.segment));
 const list=screens.filter(s=>s.playable);
 let current=null,target=0,autoplay=false,disposed=false,version=0;
 const end=s=>s.actual_end_ms??s.end_ms??screens.find(next=>next.start_ms>s.start_ms)?.start_ms??null;
 const notify=(state,extra={})=>onState({state,segment:current,...extra});
 function load(segment,ms,play){
  if(disposed)return;target=Math.max(0,(ms-segment.start_ms)/1000);autoplay=play;
  if(current?.segment===segment.segment&&video.readyState>=1){video.hidden=false;video.currentTime=Math.min(target,Number.isFinite(video.duration)?Math.max(0,video.duration-.001):target);notify('ready');if(play)void video.play().catch(()=>notify('paused'));else video.pause();return;}
  const token=++version;current=segment;video.pause();video.hidden=false;video.src=segment.url;
  video.dataset.segment=segment.segment;video.dataset.start=String(segment.start_ms);notify('loading');
  video.onloadedmetadata=()=>{
   if(disposed||token!==version)return;
   if(Number.isFinite(video.duration)&&video.duration>=0){segment.actual_end_ms=segment.start_ms+Math.round(video.duration*1000);onDuration(segment.segment,segment.actual_end_ms);}
   if(Number.isFinite(video.duration)&&target>video.duration){video.pause();video.hidden=true;notify('gap',{ms:segment.start_ms+target*1000,next:list.find(s=>s.start_ms>segment.start_ms+target*1000)});return;}
   video.currentTime=Math.min(target,Number.isFinite(video.duration)?Math.max(0,video.duration-.001):target);notify('ready');
   if(autoplay)void video.play().catch(()=>notify('paused'));
  };
  video.onerror=()=>{if(!disposed&&token===version){video.pause();notify('error',{next:list[list.indexOf(segment)+1]});}};
 }
 video.ontimeupdate=()=>{if(current&&!video.paused&&!disposed)onTime(Math.round(current.start_ms+video.currentTime*1000));};
 video.onended=()=>{
  if(disposed||!current)return;
  const previous=current,next=list[list.indexOf(previous)+1];
  if(!next){notify('finished');return;}
  const gapStart=end(previous);load(next,next.start_ms,true);onTime(next.start_ms);
  if(gapStart===null||next.start_ms-gapStart>250)notify('transition',{previous,next,gapStart,gapEnd:next.start_ms});
 };
 return {
  seek(ms,play=!video.paused){
   if(disposed)return;
   const segment=list.filter(s=>s.start_ms<=ms&&(end(s)===null||ms<end(s))).at(-1);
   if(segment){load(segment,ms,play);return;}
   version++;video.pause();video.hidden=true;current=null;video.onloadedmetadata=null;video.onerror=null;
   notify('gap',{ms,next:list.find(s=>s.start_ms>=ms)});
  },
  first(){if(list.length){load(list[0],list[0].start_ms,true);onTime(list[0].start_ms);}},
  next(ms){const segment=list.find(s=>s.start_ms>ms);if(segment){load(segment,segment.start_ms,true);onTime(segment.start_ms);}},
  dispose(){disposed=true;version++;video.pause();video.onloadedmetadata=video.ontimeupdate=video.onended=video.onerror=null;}
 };
}
