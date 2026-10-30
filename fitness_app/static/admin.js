import {api, RequestError, confirmDialog, keepDialogFocus, showMessage} from './app.js';

const search=document.querySelector('#admin-search');
const status=document.querySelector('#admin-status');
const rows=document.querySelector('#account-rows');
const table=document.querySelector('#account-table');
const errorBox=document.querySelector('#admin-error');
const retry=document.querySelector('#admin-retry');
const pending=new Set();
let requestSequence=0, debounce=null, page=1, requestedPage=1, total=0, users=[], redirecting=false;
const pageSize=20;
const dateFormat=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'short'});

function redirectIfUnauthorized(error) {
  if(error.status===401 || (error.status===403 && error.code==='FORBIDDEN')) {
    redirecting=true;
    document.documentElement.dataset.privateHidden='1';
    location.replace(error.status===401?'/login':'/');
    return true;
  }
  return false;
}
function element(tag,text,className) {
  const el=document.createElement(tag);
  if(text!==undefined)el.textContent=text;
  if(className)el.className=className;
  return el;
}
function updatePending() {
  for(const row of rows.querySelectorAll('tr[data-user-id]')) {
    const data=users.find(item=>item.id===row.dataset.userId);
    if(!data)continue;
    for(const button of row.querySelectorAll('button')) {
      const allowed=button.dataset.action==='admin.reset-password'?data.can_reset:data.can_disable;
      button.disabled=pending.has(data.id)||!allowed;
    }
  }
}
function renderRows() {
  rows.replaceChildren();
  for(const user of users) {
    const row=element('tr');row.dataset.userId=user.id;
    const name=element('td');const full=element('span',user.username,'username-cell');full.title=user.username;name.append(full);
    const role=element('td',user.role==='admin'?'管理员':'普通用户');
    const state=element('td');state.append(element('span',user.enabled?'启用':'停用','status-badge '+(user.enabled?'enabled':'disabled')));

    const created=element('td',user.enabled?(user.must_change_password?'需修改密码':'已设置'):'—');
    const actions=element('td');const group=element('div',undefined,'row-actions');
    const reset=element('button','重置密码','button secondary small');reset.type='button';reset.dataset.action='admin.reset-password';
    const disable=element('button','停用账号','danger-link');disable.type='button';disable.dataset.action='admin.disable';
    reset.addEventListener('click',()=>perform(user,'reset').catch(actionError));
    disable.addEventListener('click',()=>perform(user,'disable').catch(actionError));
    group.append(reset,disable);actions.append(group);
    const reason=!user.can_reset?user.reset_reason:(!user.can_disable?user.disable_reason:'');
    if(reason)actions.append(element('span',reason,'row-note'));
    row.append(name,role,state,created,actions);rows.append(row);
  }
  if(!users.length){const row=element('tr');const cell=element('td','没有匹配账号','table-empty');cell.colSpan=5;row.append(cell);rows.append(row)}
  updatePending();
  document.querySelector('#account-total').textContent=`共 ${total} 个账号`;
  const pages=Math.max(1,Math.ceil(total/pageSize));
  document.querySelector('#admin-page-label').textContent=`第 ${page} / ${pages} 页`;
  document.querySelector('#admin-prev').disabled=page<=1;
  document.querySelector('#admin-next').disabled=page>=pages;
}
async function loadUsers() {
  const sequence=++requestSequence;
  const parameters=new URLSearchParams({search:search.value,enabled:status.value,page:String(requestedPage),page_size:String(pageSize)});
  table.setAttribute('aria-busy','true');
  document.querySelector('#admin-loading').textContent='正在更新账号…';
  document.querySelector('#admin-prev').disabled=document.querySelector('#admin-next').disabled=true;
  errorBox.hidden=retry.hidden=true;
  try {
    const data=await api('/api/v1/admin/users?'+parameters);
    if(sequence!==requestSequence)return;
    users=data.users;total=data.total;page=requestedPage=data.page;renderRows();
  } catch(error) {
    if(sequence!==requestSequence||redirectIfUnauthorized(error))return;
    errorBox.textContent=error.message+'；保留上次已读取的结果。';errorBox.hidden=retry.hidden=false;
    if(!users.length)rows.querySelector('.table-empty').textContent='账号列表读取失败';
  } finally {
    if(sequence===requestSequence){table.removeAttribute('aria-busy');document.querySelector('#admin-loading').textContent=''}
  }
}
function actionError(error){if(!redirectIfUnauthorized(error))showMessage(error.message||'操作未完成，请先核对状态。')}
function restoreFocus(id,action) {
  const button=rows.querySelector(`[data-user-id="${id}"] [data-action="admin.${action==='reset'?'reset-password':'disable'}"]`);
  if(button&&!button.disabled)button.focus();else table.focus();
}
async function perform(user,operation) {
  if(pending.has(user.id))return;
  const commandId=crypto.randomUUID();let uncertain=false;
  const title=operation==='reset'?'重置密码':'停用账号';
  const result=await confirmDialog({title,message:`账号：${user.username}。`+(operation==='reset'?'旧密码与登录会话将失效，临时密码首次登录后必须修改。':'旧登录会话将失效，个人摘要保留。'),confirmLabel:title,danger:operation==='disable',onConfirm:async()=>{
    pending.add(user.id);updatePending();
    try {
      if(uncertain) {
        const receipt=await api('/api/v1/operations/'+commandId);
        if(receipt.status==='succeeded')return {...receipt.result,credential_available:false};
        throw new RequestError('原操作仍未完成，请稍后核对；不会自动再发重置。',409);
      }
      return await api(`/api/v1/admin/users/${user.id}/${operation==='reset'?'password-reset':'disable'}`,{method:'POST',commandId,body:{expected_version:user.version,confirm:true}});
    } catch(error) {
      if(redirectIfUnauthorized(error))throw error;
      if(error.unknown) {
        uncertain=true;
        try {const receipt=await api('/api/v1/operations/'+commandId);if(receipt.status==='succeeded')return {...receipt.result,credential_available:false}}catch(queryError){redirectIfUnauthorized(queryError)}
        document.querySelector('[data-dialog-confirm]').textContent='核对原操作';
        throw new RequestError('执行结果尚未确认。请核对原操作；不会自动生成另一份临时密码。',0);
      }
      if(uncertain&&error.status===404)throw new RequestError('尚未查到原操作结果，请稍后核对或取消。不会自动再次重置。',0);
      if(error.code==='VERSION_CONFLICT'){await loadUsers();throw new RequestError('账号状态已变化，列表已刷新。请取消后重新确认。',409)}
      throw error;
    } finally {pending.delete(user.id);updatePending()}
  }});
  if(!result){restoreFocus(user.id,operation);return}
  await loadUsers();
  if(redirecting){delete result.temporary_password;return}
  if(operation==='reset') {
    const again=await showResetResult(result);
    restoreFocus(user.id,operation);
    if(again) {
      let latest=users.find(item=>item.id===user.id);
      if(!latest){const data=await api('/api/v1/admin/users?'+new URLSearchParams({search:user.username}));latest=data.users.find(item=>item.id===user.id)}
      if(latest?.can_reset)await perform(latest,'reset');else showMessage('该账号当前不可重置，请刷新列表核实。');
    }
  } else {showMessage('账号已停用，旧登录会话已失效。');restoreFocus(user.id,operation)}
}
function showResetResult(data) {
  const dialog=document.querySelector('#reset-result');
  const input=dialog.querySelector('#temporary-password');
  const reveal=dialog.querySelector('[data-action="admin.reveal-temporary"]');
  const copy=dialog.querySelector('[data-action="admin.copy-temporary"]');
  const again=dialog.querySelector('#reset-again');
  const message=dialog.querySelector('#reset-result-message');
  const opener=document.activeElement;
  let secret=data.credential_available?data.temporary_password:'';
  delete data.temporary_password;
  let expired=false,copyTimer=null,closed=false;
  input.type='password';input.value=secret;reveal.setAttribute('aria-pressed','false');reveal.setAttribute('aria-label','显示临时密码');copy.textContent='复制';
  dialog.querySelector('#reset-account').textContent='账号：'+data.username;
  dialog.querySelector('#temporary-section').hidden=!secret;
  dialog.querySelector('#reset-title').textContent=secret?'密码已重置':'已重置，但未取得临时密码';
  dialog.querySelector('#reset-mark').textContent=secret?'✓':'!';
  dialog.querySelector('#reset-mark').classList.toggle('warning',!secret);
  message.className='notice';message.textContent=secret?'临时密码只在此处显示一次，请安全交给账号本人。':'原操作已执行，不能从回执取回明文。如需新凭据，请明确重新确认一次重置。';
  again.hidden=Boolean(secret);
  const expire=()=>{if(data.temporary_expires_at_ms&&Date.now()>=data.temporary_expires_at_ms){expired=true;secret='';input.value='';reveal.disabled=copy.disabled=true;again.hidden=false;message.textContent='临时密码已过期。如需新凭据，请重新确认重置。'}else dialog.querySelector('#temporary-expiry').textContent='临时密码有效 30 分钟（至 '+dateFormat.format(data.temporary_expires_at_ms)+'）；首次登录需先修改密码。'};
  reveal.disabled=copy.disabled=!secret;expire();const interval=setInterval(expire,1000);const releaseFocus=keepDialogFocus(dialog);
  document.documentElement.classList.add('modal-open');dialog.showModal();
  return new Promise(resolve=>{
    const close=retryReset=>{
      closed=true;
      clearInterval(interval);clearTimeout(copyTimer);secret='';input.value='';input.type='password';
      reveal.onclick=copy.onclick=again.onclick=null;dialog.oncancel=dialog.onkeydown=null;
      for(const closeButton of dialog.querySelectorAll('[data-action="admin.close-reset-result"]'))closeButton.onclick=null;
      releaseFocus();dialog.close();document.documentElement.classList.remove('modal-open');opener?.focus();resolve(retryReset);
    };
    for(const button of dialog.querySelectorAll('[data-action="admin.close-reset-result"]'))button.onclick=()=>close(false);
    dialog.oncancel=event=>{event.preventDefault();close(false)};
    reveal.onclick=()=>{expire();if(expired||!secret)return;const visible=input.type==='password';input.type=visible?'text':'password';reveal.setAttribute('aria-pressed',String(visible));reveal.setAttribute('aria-label',visible?'隐藏临时密码':'显示临时密码')};
    copy.onclick=async()=>{expire();if(expired||!secret)return;try{await navigator.clipboard.writeText(secret);if(closed)return;copy.textContent='已复制';clearTimeout(copyTimer);copyTimer=setTimeout(()=>copy.textContent='复制',2000)}catch{if(closed)return;message.className='notice error';message.textContent='复制失败。请点击显示后手动选择复制，不会自动读取或清空剪贴板。'}};
    again.onclick=()=>close(true);
    dialog.querySelector('.result-close').focus();
  });
}
search.addEventListener('input',()=>{clearTimeout(debounce);requestSequence++;requestedPage=1;debounce=setTimeout(loadUsers,250)});
status.addEventListener('change',()=>{clearTimeout(debounce);requestSequence++;requestedPage=1;loadUsers()});
retry.addEventListener('click',loadUsers);
document.querySelector('#admin-prev').addEventListener('click',()=>{if(page>1){requestedPage=page-1;loadUsers()}});
document.querySelector('#admin-next').addEventListener('click',()=>{if(page*pageSize<total){requestedPage=page+1;loadUsers()}});
loadUsers();
