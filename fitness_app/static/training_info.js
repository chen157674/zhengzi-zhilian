import {api,showMessage,confirmDialog} from './app.js';
const $=id=>document.getElementById(id),profileForm=$('training-profile-form'),noteForm=$('training-note-form'),form=profileForm||noteForm;
if(form){
 const profile=Boolean(profileForm),fields=$(profile?'training-profile-fields':'training-note-fields'),retry=$(profile?'training-profile-retry':'training-note-retry'),error=$('training-info-error');
 const path=profile?'/api/v1/me/training-profile':'/api/v1/records/'+encodeURIComponent(noteForm.dataset.sessionId)+'/training-note';
 let saved=null,busy=false,pending=null,initial='';
 function values(){if(profile)return {experience:$('training-experience').value||null,available_minutes:$('training-minutes').value?Number($('training-minutes').value):null,goals:[...form.querySelectorAll('[name=goals]:checked')].map(x=>x.value),equipment:[...form.querySelectorAll('[name=equipment]:checked')].map(x=>x.value)};const has=$('note-load').value!=='';return {load_value:has?Number($('note-load').value):null,load_unit:has?$('note-unit').value:null,load_basis:has?$('note-basis').value:null,difficulty:$('note-difficulty').value||null,note:$('note-text').value||null};}
 function render(data){saved=data;if(profile){$('training-experience').value=data.experience||'';$('training-minutes').value=data.available_minutes??'';for(const name of ['goals','equipment'])for(const input of form.querySelectorAll('[name='+name+']'))input.checked=data[name].includes(input.value);}else{$('note-load').value=data.load_value??'';$('note-unit').value=data.load_unit||'kg';$('note-basis').value=data.load_basis||'total';$('note-difficulty').value=data.difficulty||'';$('note-text').value=data.note||'';}initial=JSON.stringify(values());fields.disabled=false;retry.hidden=true;}
 const dirty=()=>Boolean(saved&&JSON.stringify(values())!==initial);
 function fail(e){error.textContent=e.message;error.hidden=false;}
 async function load(){error.hidden=true;try{render(await api(path));}catch(e){fail(e);retry.hidden=false;}}
 async function mutate(data,method){if(busy||!saved)return;busy=true;fields.disabled=true;error.hidden=true;try{const body={expected_version:saved.version,...data},signature=JSON.stringify({body,method});if(!pending||pending.signature!==signature)pending={id:crypto.randomUUID(),signature};render(await api(path,{method,body,commandId:pending.id}));pending=null;showMessage(profile?'训练档案已保存':'训练备注已保存');}catch(e){fail(e);if(e.status===409){const current=await api(path).catch(()=>null);if(current){saved=current;error.textContent+=' 已保留输入，请核对后重新保存。';}}}finally{busy=false;fields.disabled=!saved;}}
 form.onsubmit=e=>{e.preventDefault();mutate(values(),profile?'PATCH':'PUT');};
 $(profile?'training-profile-cancel':'training-note-cancel').onclick=()=>{if(saved)render(saved);error.hidden=true;pending=null;};
 retry.onclick=load;
 if(profile)$('training-profile-clear').onclick=async()=>{if(!await confirmDialog({title:'清除训练档案？',message:'清除这些可选资料，已经保存的动作记录不受影响。',confirmLabel:'清除档案',danger:true}))return;await mutate({},'DELETE');};
 else $('note-unit').onchange=()=>{if($('note-unit').value==='plate_index'){$('note-basis').value='device_setting';$('note-load').step='1';$('note-load').min='1';}else{if($('note-basis').value==='device_setting')$('note-basis').value='total';$('note-load').step='0.01';$('note-load').min='0';}};
 const prior=window.fitnessLeaveGuard;window.fitnessLeaveGuard=async target=>!busy&&(!prior||await prior(target))&&(!dirty()||await confirmDialog({title:'放弃未保存的训练信息？',message:'尚未保存的输入将丢失。',confirmLabel:'放弃修改'}));
 window.addEventListener('beforeunload',event=>{if(dirty()||busy){event.preventDefault();event.returnValue='';}});
 load();
}
