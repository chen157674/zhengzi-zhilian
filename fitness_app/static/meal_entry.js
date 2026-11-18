import {api,confirmDialog,keepDialogFocus} from './app.js';
import {el,privateError} from './record_common.js';

const labels={staple:'主食',protein:'鱼肉蛋豆奶',vegetable:'蔬菜',fruit:'水果',drink:'饮品',other:'其他食物'};
export const mealToday=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
export function mountMealEditor({onSaved=()=>{}}={}){
  const $=id=>document.getElementById(id),dialog=$('meal-editor-dialog');
  if(!dialog)return null;
  const form=$('meal-form'),storageKey='fitness-meal-pending:'+document.body.dataset.userId;
  let editing=null,baseline='',pending=null,busy=false,uncertain=false,opener=null,release=null,epoch=0;
  function data(){return {consumed_date:$('meal-date').value,meal_type:$('meal-type').value,foods:[...$('meal-foods').children].map(row=>({name:row.querySelector('[data-food-name]').value.trim(),group:row.querySelector('[data-food-group]').value,amount:row.querySelector('[data-food-amount]').value.trim()||null})),note:$('meal-note').value.trim(),...(editing?{expected_version:editing.version}:{})};}
  function addFood(food={},focus=false){
    const row=el('div',undefined,'journal-food-input'),name=el('input'),group=el('select'),amount=el('input'),remove=el('button','×','journal-remove-food');
    name.required=true;name.maxLength=40;name.placeholder='食物名称';name.value=food.name||'';name.dataset.foodName='';name.setAttribute('aria-label','食物名称');
    amount.maxLength=40;amount.placeholder='实际分量，可不填';amount.value=food.amount||'';amount.dataset.foodAmount='';amount.setAttribute('aria-label','实际分量');
    for(const [value,label]of Object.entries(labels))group.append(new Option(label,value));group.value=food.group||'other';group.dataset.foodGroup='';group.setAttribute('aria-label','食物类别');
    remove.type='button';remove.setAttribute('aria-label','移除这项食物');remove.onclick=()=>{if(busy||uncertain)return;if($('meal-foods').childElementCount===1){name.value=amount.value='';name.focus();}else row.remove();};
    row.append(name,group,amount,remove);$('meal-foods').append(row);if(focus)name.focus();
  }
  function lock(value){for(const field of form.elements)field.disabled=value;$('meal-save').disabled=busy;$('meal-save').textContent=busy?'正在保存…':uncertain?'核对并重试':editing?'保存修改':'确认记录';}
  function close(){dialog.close();release?.();release=null;opener?.focus({preventScroll:true});opener=null;}
  async function requestClose(){
    if(busy)return;
    if(uncertain){$('meal-status').textContent='请先查看这餐的保存状态。';return;}
    if(JSON.stringify(data())!==baseline&&!await confirmDialog({title:'放弃本次输入？',message:'已保存的餐食不会受影响。',confirmLabel:'放弃输入'}))return;
    close();
  }
  async function open(values={},trigger=null){
    if(busy||dialog.open)return;
    editing=values.id?values:null;epoch++;opener=trigger||document.activeElement;form.reset();form.querySelector('details').open=Boolean(values.note);$('meal-foods').replaceChildren();$('meal-date').max=mealToday();$('meal-date').value=values.consumed_date||mealToday();$('meal-type').value=values.meal_type||'lunch';$('meal-note').value=values.note||'';
    for(const food of values.foods?.length?values.foods:[{}])addFood(food);
    $('meal-editor-title').textContent=editing?'修改餐食':'我吃了这餐';$('meal-status').textContent='';pending=null;uncertain=false;lock(false);baseline=JSON.stringify(data());
    dialog.showModal();release=keepDialogFocus(dialog);$('meal-date').focus({preventScroll:true});
    try{
      const saved=JSON.parse(sessionStorage.getItem(storageKey)||'null');
      if(saved){
        if(!['POST','PATCH'].includes(saved.method)||!/^\/api\/v1\/nutrition\/journal(?:\/[a-f0-9-]{36})?$/.test(saved.url)||typeof saved.id!=='string'||!Array.isArray(saved.body?.foods))throw new Error('回执记录格式无效');
        pending=saved;editing=saved.method==='PATCH'?{id:saved.url.split('/').at(-1),version:saved.body.expected_version}:null;
        $('meal-date').value=saved.body.consumed_date;$('meal-type').value=saved.body.meal_type;$('meal-note').value=saved.body.note||'';$('meal-foods').replaceChildren();for(const food of saved.body.foods)addFood(food);
        $('meal-editor-title').textContent='核对上次保存';baseline=JSON.stringify(data());uncertain=true;$('meal-status').textContent='请先查看上次保存结果。';lock(true);
      }
    }catch{ $('meal-status').textContent='请启用会话存储后继续保存。';}
  }
  $('meal-add').onclick=()=>{if(!busy&&!uncertain&&$('meal-foods').childElementCount<24)addFood({},true);};
  $('meal-cancel').onclick=$('meal-editor-close').onclick=requestClose;
  dialog.addEventListener('cancel',event=>{event.preventDefault();requestClose();});
  dialog.addEventListener('close',()=>{release?.();release=null;});
  form.onsubmit=async event=>{
    event.preventDefault();if(busy)return;
    const own=epoch;
    if(!uncertain){
      const body=data();if(!body.foods.length)return;
      pending={id:crypto.randomUUID(),body,url:'/api/v1/nutrition/journal'+(editing?'/'+editing.id:''),method:editing?'PATCH':'POST'};
      try{sessionStorage.setItem(storageKey,JSON.stringify(pending));}catch{pending=null;$('meal-status').textContent='请启用本页会话存储后重试保存。';return;}
    }
    busy=true;lock(true);$('meal-status').textContent='正在保存…';
    try{
      const receipt=uncertain?await api('/api/v1/nutrition/journal/commands/'+pending.id):null;
      let row;
      if(receipt?.status==='succeeded'){
        if(receipt.deleted)throw new Error('该餐食已经删除，请返回餐食列表核对。');
        row=await api('/api/v1/nutrition/journal/'+receipt.record_id);
      }else row=await api(pending.url,{method:pending.method,commandId:pending.id,body:pending.body});
      if(own!==epoch)return;sessionStorage.removeItem(storageKey);pending=null;uncertain=false;baseline=JSON.stringify(data());close();await onSaved(row);
    }catch(error){
      if(own!==epoch||privateError(error))return;
      uncertain=Boolean(error.unknown);
      if(!uncertain){pending=null;sessionStorage.removeItem(storageKey);}
      $('meal-status').textContent=error.status===409?'餐食已在其他页面更新，本地输入已保留。请回列表核对最新内容。':uncertain?'请查看保存状态后重试。':error.message;
    }finally{busy=false;if(own===epoch)lock(uncertain);}
  };
  window.addEventListener('fitness-auth-revoked',()=>{sessionStorage.removeItem(storageKey);epoch++;close();form.reset();});
  window.addEventListener('beforeunload',event=>{if(busy||uncertain||dialog.open&&JSON.stringify(data())!==baseline){event.preventDefault();event.returnValue='';}});
  window.addEventListener('click',async event=>{
    const link=event.target.closest('a[data-nav]');if(!link||!dialog.open)return;
    if(busy||uncertain){event.preventDefault();$('meal-status').textContent='请先核对当前保存结果。';}
  },true);
  return {open};
}
