export function formatSessionClock(remaining){
 const seconds=Math.floor(Math.abs(remaining)/1000),prefix=remaining<0?'+':'';
 if(seconds<86400)return prefix+String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');
 const pad=n=>String(n).padStart(2,'0');
 return `${prefix}${Math.floor(seconds/86400)}일 ${pad(Math.floor(seconds/3600)%24)}:${pad(Math.floor(seconds/60)%60)}:${pad(seconds%60)}`;
}
