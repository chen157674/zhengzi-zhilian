import {api,showMessage,confirmDialog,keepDialogFocus,RequestError} from './app.js';
import {el,dateFormat} from './record_common.js';
const form=document.querySelector('#profile-form'),nickname=form.elements.nickname,save=document.querySelector('#profile-save'),cancel=document.querySelector('#profile-cancel'),error=document.querySelector('#account-error'),retry=document.querySelector('#account-retry');
const dialog=document.querySelector('#avatar-dialog'),file=document.querySelector('#avatar-file'),upload=document.querySelector('#avatar-confirm'),close=document.querySelector('#avatar-cancel');
let profile=null,busy=false,objectURL=null,picked=null,releaseFocus=null;
function dirty(){return profile&&nickname.value.trim()!==profile.nickname;}
function update(){save.disabled=busy||!dirty()||nickname.value.trim().length<2;cancel.disabled=busy||!dirty();document.querySelector('#avatar-select').disabled=busy||!profile;}
function fail(e){error.textContent=e.message;error.hidden=false;if(e.status===401){window.fitnessLeaveGuard=null;location.replace('/login?returnTo=%2Faccount');}}
function showProfile(p,preserve=false){profile=p;document.querySelector('#profile-name').textContent=p.nickname;document.querySelector('#profile-avatar').src=p.avatar_url;document.querySelector('.account .avatar').src=p.avatar_url;document.querySelector('.account-name').textContent=p.nickname;document.querySelector('#profile-username').value=p.username;if(!preserve)nickname.value=p.nickname;nickname.disabled=false;update();}
async function load(){retry.hidden=true;try{showProfile(await api('/api/v1/account/profile'));error.hidden=true;}catch(e){fail(e);retry.hidden=false;}}
retry.onclick=load;nickname.oninput=update;cancel.onclick=()=>{nickname.value=profile.nickname;update();};
form.onsubmit=async event=>{event.preventDefault();if(save.disabled)return;busy=true;update();error.hidden=true;try{showProfile(await api('/api/v1/account/profile',{method:'POST',body:{nickname:nickname.value,expected_version:profile.profile_version}}));showMessage('昵称已更新');}catch(e){fail(e);if(e.status===409){try{showProfile(await api('/api/v1/account/profile'),true);error.textContent='资料已在其他页面更新。已保留输入，请核对后再保存。';}catch(readError){fail(readError);}}}finally{busy=false;update();}};
function finish(){dialog.close();releaseFocus?.();document.documentElement.classList.remove('modal-open');if(objectURL)URL.revokeObjectURL(objectURL);objectURL=null;picked=null;file.value='';document.querySelector('#avatar-select').focus();}
document.querySelector('#avatar-select').onclick=()=>file.click();
file.onchange=async()=>{picked=file.files[0];if(!picked)return;if(!['image/jpeg','image/png','image/webp'].includes(picked.type)||picked.size>4*1024*1024){fail(new Error('请选择不超过4 MiB的静态JPG、PNG或WebP图片。'));file.value='';return;}objectURL=URL.createObjectURL(picked);const img=document.querySelector('#avatar-preview');img.src=objectURL;try{await img.decode();}catch{URL.revokeObjectURL(objectURL);objectURL=null;file.value='';fail(new Error('请重新选择图片。'));return;}document.querySelector('#avatar-error').textContent='';releaseFocus=keepDialogFocus(dialog);document.documentElement.classList.add('modal-open');dialog.showModal();close.focus();};
close.onclick=()=>{if(!busy)finish();};dialog.oncancel=e=>{e.preventDefault();if(!busy)finish();};
upload.onclick=async()=>{if(busy||!picked)return;busy=true;upload.disabled=close.disabled=true;update();try{const token=await api('/api/v1/auth/csrf');const body=new FormData();body.append('file',picked);body.append('expected_version',String(profile.profile_version));const res=await fetch('/api/v1/account/avatar',{method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':token.csrf_token},body,signal:AbortSignal.timeout(15000)});const json=await res.json();if(!res.ok)throw new RequestError(json.error?.message||'头像上传未完成',res.status);showProfile(json.data,true);finish();showMessage('头像已更新');}catch(e){document.querySelector('#avatar-error').textContent=e.message;if(e.status===409){try{showProfile(await api('/api/v1/account/profile'),true);}catch(readError){fail(readError);}}}finally{busy=false;upload.disabled=close.disabled=false;update();}};
window.fitnessLeaveGuard=async()=>!busy&&(!dirty()||await confirmDialog({title:'放弃未保存的昵称？',message:'头像已确认的修改不受影响。',confirmLabel:'放弃修改'}));
window.addEventListener('beforeunload',event=>{if(dirty()||busy){event.preventDefault();event.returnValue='';}});
window.addEventListener('pagehide',()=>{if(objectURL)URL.revokeObjectURL(objectURL);});
async function recent(){
 const box=document.querySelector('#profile-recent');
 try{
  const data=await api('/api/v1/account/overview');box.replaceChildren();
  if(!data.recent_records.length){
   box.dataset.state='empty';
   const copy=el('div'),title=el('h3','还没有训练记录'),description=el('p','完成训练后，在这里回看动作与反馈。');copy.append(title,description);
   const link=el('a','开始实时分析','button secondary small');link.href='/realtime';link.dataset.nav='';box.append(copy,link);
  }else{
   box.dataset.state='ready';
   for(const record of data.recent_records.slice(0,3)){
    const link=el('a',undefined,'account-recent-row');link.href='/records/'+encodeURIComponent(record.session_id);link.dataset.nav='';
    const copy=el('div'),name=el('strong',record.action_name||record.action_id),metadata=[];
    if(record.started_at_ms)metadata.push(dateFormat.format(record.started_at_ms));
    metadata.push(record.source==='camera'?'实时分析':record.source==='video'?'视频分析':'训练记录');
    copy.append(name,el('p',metadata.join(' · ')));link.append(copy,el('span','查看反馈 →','account-recent-action'));box.append(link);
   }
  }
 }catch(error){box.dataset.state='error';box.textContent=error.message;}
}
load();recent();
