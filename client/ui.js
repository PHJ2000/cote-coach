// Small DOM/format helpers and an inline icon set shared by every screen.
export const $=(s,e=document)=>e.querySelector(s);
export const $$=(s,e=document)=>[...e.querySelectorAll(s)];
export const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const fmt=ms=>{const n=Math.max(0,Math.floor(ms/1000));return `${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;};
export const date=ms=>new Date(ms).toLocaleString('ko-KR',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
export const day=ms=>new Date(ms).toLocaleDateString('ko-KR',{month:'long',day:'numeric',weekday:'short'});
export const getSaved=(k,f)=>{try{return JSON.parse(localStorage.getItem(k))??f}catch{return f}};
export const save=(k,v)=>{try{localStorage.setItem(k,JSON.stringify(v))}catch{}};

const paths={
 home:'<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
 library:'<path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5z"/><path d="M5 19.5A1.5 1.5 0 0 0 6.5 21H19v-3"/><path d="M9 7h6"/>',
 history:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
 settings:'<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
 plus:'<path d="M12 5v14M5 12h14"/>',
 play:'<path d="M7.5 5v14l11-7z" fill="currentColor" stroke="none"/>',
 send:'<path d="m4 12 16-8-5.5 16-3-6.5z"/><path d="m11.5 13.5 3-3"/>',
 mic:'<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
 stop:'<rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor" stroke="none"/>',
 spark:'<path d="M12 3.5 13.9 9 19.5 11l-5.6 2L12 18.5 10.1 13 4.5 11l5.6-2z"/>',
 check:'<path d="m5 12.5 4.5 4.5L19 7.5"/>',
 x:'<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
 right:'<path d="M5 12h14M13 6l6 6-6 6"/>',
 left:'<path d="M19 12H5M11 6l-6 6 6 6"/>',
 search:'<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.3-4.3"/>',
 sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
 moon:'<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.8 6.8 0 0 0 10.5 10.5z"/>',
 download:'<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
 refresh:'<path d="M20 11.5a8 8 0 1 1-2.4-5.7L20 8M20 3.5V8h-4.5"/>',
 panelLeft:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9.5 4v16"/>',
 panelRight:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M14.5 4v16"/>',
 flag:'<path d="M5 21V4M5 4h12l-2.5 4L17 12H5"/>',
 pen:'<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
 bulb:'<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.7.5 1.1 1.3 1.1 2.2h5c0-.9.4-1.7 1.1-2.2A6 6 0 0 0 12 3z"/>',
 video:'<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3z"/>',
 file:'<path d="M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3v5h5M10 12.5 8 14.5l2 2M14 12.5l2 2-2 2"/>',
 terminal:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3.5 3L7 15M13 15h4"/>',
 alert:'<path d="M12 3.5 2.5 20h19z"/><path d="M12 10v4.5M12 17.2v.3"/>',
 camera:'<path d="M4 8h3.2l1.8-2.5h6L16.8 8H20v11H4z"/><circle cx="12" cy="13.5" r="3.2"/>',
 chat:'<path d="M4 5h16v11H9.5L5 20v-4H4z"/>',
 code:'<path d="m8 7-5 5 5 5M16 7l5 5-5 5"/>',
 timer:'<circle cx="12" cy="13.5" r="7.5"/><path d="M12 9.5v4l2.5 2M9.5 2.5h5"/>',
 target:'<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".6" fill="currentColor"/>',
 bolt:'<path d="M13 2.5 4.5 13.5H11l-1 8 8.5-11H12z"/>',
 layers:'<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
 dot:'<circle cx="12" cy="12" r="4" fill="currentColor" stroke="none"/>',
 external:'<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
 menu:'<path d="M4 7h16M4 12h16M4 17h16"/>'
};
export function icon(name,size=16){return `<svg class="i" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]||''}</svg>`;}

export function btn(text,action,kind='',more=''){return `<button type="button" class="btn ${kind}" data-action="${action}" ${more}>${text}</button>`;}
export function tag(text,tone=''){return `<span class="tag ${tone}">${esc(text)}</span>`;}
export function level(n){return `<span class="lv lv-${Math.min(5,Math.max(0,Number(n)||0))}">Lv.${esc(n)}</span>`;}
export function empty(title,text,extra=''){return `<div class="empty"><div class="empty-mark">${icon('layers',22)}</div><h3>${title}</h3><p>${text}</p>${extra}</div>`;}

let toastTimer;
export function toast(t){const el=$('#toast');el.textContent=t;el.hidden=false;el.classList.remove('show');void el.offsetWidth;el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>{el.classList.remove('show');el.hidden=true;},6500);}

// In-app confirmation; native confirm() interrupts the practice screen.
export function confirmBox({title,body='',ok='확인',cancel='취소',tone='primary'}){
 return new Promise(resolve=>{
  const d=$('#confirm');
  d.innerHTML=`<form method="dialog" class="confirm"><h2>${title}</h2>${body?`<p>${body}</p>`:''}<div class="confirm-actions"><button type="button" class="btn ghost" data-confirm="cancel">${cancel}</button><button type="button" class="btn ${tone}" data-confirm="ok">${ok}</button></div></form>`;
  const done=value=>{d.onclick=null;d.oncancel=null;if(d.open)d.close();resolve(value);};
  d.onclick=e=>{const b=e.target.closest('[data-confirm]');if(b)done(b.dataset.confirm==='ok');else if(e.target===d)done(false);};
  d.oncancel=e=>{e.preventDefault();done(false);};
  d.showModal();$('[data-confirm="ok"]',d).focus();
 });
}

export function theme(){return document.documentElement.dataset.theme==='light'?'light':'dark';}
export function setTheme(value){document.documentElement.dataset.theme=value;save('lab-theme',value);$('meta[name="theme-color"]')?.setAttribute('content',value==='light'?'#f6f5f1':'#0c0d10');}
