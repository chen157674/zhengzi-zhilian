import {api,showMessage,keepDialogFocus} from './app.js';
import {privateError} from './record_common.js';
const list=document.querySelector('#sessions-list'),open=document.querySelector('#revoke-others'),dialog=document.querySelector('#revoke-dialog'),form=document.querySelector('#revoke-form'),password=document.querySelector('#revoke-password'),submit=document.querySelector('#revoke-submit'),cancel=document.querySelector('#revoke-cancel'),error=document.querySelector('#sessions-error'),retry=document.querySelector('#sessions-retry');
let busy=false,release=null;
const date=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
async function load(){
 const panel=document.querySelector('.session-list-panel');open.disabled=true;retry.hidden=true;retry.disabled=true;panel.setAttribute('aria-busy','true');
 try{
  const data=await api('/api/v1/account/sessions');list.replaceChildren();
  for(const s of data.sessions){
   const tr=document.createElement('tr');tr.dataset.current=String(s.current);
   for(const [index,text]of [date.format(s.created_at_ms),date.format(s.expires_at_ms),s.current?'当前登录':'其他登录'].entries()){
    const td=document.createElement('td');td.dataset.label=['登录时间','有效至','状态'][index];
    if(index===2){const badge=document.createElement('span');badge.className='session-status';badge.textContent=text;td.append(badge);}else td.textContent=text;
    tr.append(td);
   }
   list.append(tr);
  }
  if(!data.sessions.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=3;td.className='table-empty';td.textContent='没有有效登录记录';tr.append(td);list.append(tr);}
  open.disabled=!data.sessions.some(s=>!s.current);open.title=open.disabled?'没有其他有效登录':'';error.hidden=true;
 }catch(e){
  if(privateError(e)){list.replaceChildren();return;}
  error.textContent=e.message;error.hidden=false;retry.hidden=false;
  if(!list.querySelector('tr[data-current]')){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=3;td.className='table-empty';td.textContent='请重新读取登录记录';tr.append(td);list.replaceChildren(tr);}
 }finally{panel.setAttribute('aria-busy','false');retry.disabled=false;}
}
function close(){dialog.close();password.value='';release?.();document.documentElement.classList.remove('modal-open');open.focus();}
open.onclick=()=>{document.querySelector('#revoke-error').textContent='';release=keepDialogFocus(dialog);document.documentElement.classList.add('modal-open');dialog.showModal();password.focus();};cancel.onclick=()=>{if(!busy)close();};dialog.oncancel=e=>{e.preventDefault();if(!busy)close();};
form.onsubmit=async e=>{e.preventDefault();if(busy)return;busy=true;submit.disabled=cancel.disabled=true;try{const data=await api('/api/v1/account/sessions/revoke-others',{method:'POST',body:{current_password:password.value}});close();showMessage('已退出'+data.revoked_count+'个其他登录');await load();}catch(err){if(privateError(err)){password.value='';return;}document.querySelector('#revoke-error').textContent=err.message;}finally{busy=false;submit.disabled=cancel.disabled=false;}};
window.addEventListener('pagehide',()=>{password.value='';});retry.onclick=load;load();
