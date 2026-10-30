export class RequestError extends Error{constructor(message,status=0,fields={},code='UNKNOWN'){super(message);this.status=status;this.fields=fields;this.code=code;this.unknown=status===0||status>=500}}
export async function api(path,{method='GET',body,commandId}={}){const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),15000);try{const headers={};if(method!=='GET'){const csrf=await fetch('/api/v1/auth/csrf',{credentials:'same-origin',cache:'no-store',signal:controller.signal});const token=await csrf.json();if(!csrf.ok)throw new RequestError(token.error?.message||'无法读取表单凭据',csrf.status);headers['X-CSRFToken']=token.data.csrf_token;headers['Content-Type']='application/json';if(commandId)headers['Idempotency-Key']=commandId}const response=await fetch(path,{method,headers,body:body===undefined?undefined:JSON.stringify(body),credentials:'same-origin',cache:'no-store',signal:controller.signal});let result;try{result=await response.json()}catch{throw new RequestError('请求结果尚未确认，请先核对状态',response.status>=500?response.status:0)}if(!response.ok)throw new RequestError(result.error?.message||'操作未完成',response.status,result.error?.fields||{},result.error?.code);return result.data}catch(error){if(error instanceof RequestError)throw error;throw new RequestError('请求结果尚未确认，请先核对状态，不要连续重试')}finally{clearTimeout(timeout)}}
export function authChanged(){localStorage.setItem('fitness-auth-change',crypto.randomUUID())}
export function showMessage(text){const el=document.querySelector('#global-message');el.textContent=text;el.hidden=false;setTimeout(()=>{el.hidden=true},5000)}
export async function beforeLeave(target){return window.fitnessLeaveGuard?await window.fitnessLeaveGuard(target):true}
export function keepDialogFocus(dialog) {
  dialog.setAttribute('tabindex', '-1');
  const handler = event => {
    if (event.key !== 'Tab') return;
    const items = [...dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href],[tabindex]:not([tabindex="-1"])')].filter(item => item.getClientRects().length);
    if (!items.length) { event.preventDefault(); dialog.focus(); return; }
    const first = items[0], last = items.at(-1), active = document.activeElement;
    if (event.shiftKey && (active === first || !dialog.contains(active))) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && (active === last || !dialog.contains(active))) { event.preventDefault(); first.focus(); }
  };
  dialog.addEventListener('keydown', handler);
  return () => dialog.removeEventListener('keydown', handler);
}
export function confirmDialog({title,message,confirmLabel,danger=false,onConfirm}) {
  if (!confirmLabel) throw new Error('确认操作必须有明确名称');
  const dialog = document.querySelector('#confirm-dialog');
  if (dialog.open) throw new Error('已有确认操作');
  const opener = document.activeElement;
  dialog.querySelector('#confirm-title').textContent = title;
  dialog.querySelector('#confirm-message').textContent = message;
  const yes = dialog.querySelector('[data-dialog-confirm]'), no = dialog.querySelector('[data-dialog-cancel]');
  yes.textContent = confirmLabel;
  yes.classList.toggle('danger', danger);
  let busy = false;
  const releaseFocus = keepDialogFocus(dialog);
  document.documentElement.classList.add('modal-open');
  return new Promise(resolve => {
    const finish = result => {
      dialog.close(); releaseFocus();
      document.documentElement.classList.remove('modal-open');
      yes.disabled = no.disabled = false;
      yes.onclick = no.onclick = null; dialog.oncancel = null;
      opener?.focus(); resolve(result);
    };
    no.onclick = () => { if (!busy) finish(false); };
    dialog.oncancel = event => { event.preventDefault(); if (!busy) finish(false); };
    yes.onclick = async () => {
      if (busy) return;
      busy = true; yes.disabled = no.disabled = true; dialog.setAttribute('aria-busy', 'true');
      try { finish(onConfirm ? await onConfirm() : true); }
      catch (error) { dialog.querySelector('#confirm-message').textContent = error.message; busy = false; yes.disabled = no.disabled = false; }
      finally { dialog.removeAttribute('aria-busy'); }
    };
    dialog.showModal(); no.focus();
  });
}
let navigating=false;
document.addEventListener('click',async e=>{
  const link=e.target.closest('a[data-nav]');
  if(link&&!e.ctrlKey&&!e.metaKey&&!e.shiftKey&&!e.altKey&&e.button===0&&window.fitnessLeaveGuard){
    e.preventDefault();if(navigating)return;navigating=true;link.setAttribute('aria-busy','true');
    let leaving=false;
    try{if(await beforeLeave(link.href)){leaving=true;location.href=link.href;}}
    catch(error){showMessage(error.message||'暂时无法离开，请重试');}
    finally{if(!leaving){navigating=false;link.removeAttribute('aria-busy');}}
  }
  const logout=e.target.closest('[data-action="auth.logout"]');
  if(logout){if(logout.disabled)return;logout.disabled=true;try{if(!(await beforeLeave('/login')))return;await api('/api/v1/auth/logout',{method:'POST',body:{}});authChanged();location.href='/login'}catch(error){showMessage(error.message)}finally{logout.disabled=false}}
  if(!e.target.closest('#account-menu'))document.querySelector('#account-menu')?.removeAttribute('open');
});
window.addEventListener('pageshow',()=>{navigating=false;});
window.addEventListener('storage',async e=>{if(e.key!=='fitness-auth-change')return;window.dispatchEvent(new Event('fitness-auth-revoked'));document.documentElement.dataset.privateHidden='1';try{await api('/api/v1/auth/me');location.reload()}catch{location.replace('/login')}});
window.addEventListener('pagehide',()=>{if(document.body.dataset.userId)document.documentElement.dataset.privateHidden='1'});window.addEventListener('pageshow',e=>{if(e.persisted&&document.body.dataset.userId){location.reload()}else delete document.documentElement.dataset.privateHidden});
