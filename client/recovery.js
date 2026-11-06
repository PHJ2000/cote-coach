import {readOutbox,backupBlob} from './outbox-backup.js';
const status=document.querySelector('#status'),button=document.querySelector('#backup');
async function inspect(){try{const items=await readOutbox();status.textContent=`이 주소의 브라우저 저장소에 미전송 기록 ${items.length}개가 있어요.`;}catch(e){status.textContent='저장소 확인 실패: '+e.message;}}
document.querySelector('#inspect').addEventListener('click',inspect);
button.addEventListener('click',async()=>{
 button.disabled=true;let url;
 try{status.textContent='이미 저장된 미전송 기록의 사본을 만들고 있어요…';const items=await readOutbox(),blob=await backupBlob(items,location.origin);url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='cote-unsent-'+new Date().toISOString().replaceAll(':','-')+'.json';a.click();status.textContent=`미전송 기록 ${items.length}개의 백업 다운로드를 요청했어요. 다운로드 폴더에서 파일을 확인해 주세요. 이 시점 이후의 녹화 조각은 포함되지 않아요.`;}
 catch(e){status.textContent='백업 실패: '+e.message;}
 finally{button.disabled=false;if(url)setTimeout(()=>URL.revokeObjectURL(url),60000);}
});
inspect();
