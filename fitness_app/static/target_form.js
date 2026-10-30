import {RequestError} from './app.js';

export function targetForm(root){
  const unit=root.dataset.unit,totalLegs=root.dataset.actionId==='high_knees',fields={sets:document.querySelector('#target-sets'),value:document.querySelector('#target-value'),rest_seconds:document.querySelector('#target-rest')};
  const modes=[...document.querySelectorAll('input[name="training-mode"]')],container=document.querySelector('#target-fields');
  const labels={sets:'组数',value:unit==='seconds'?'保持时间（秒）':unit==='per_side_reps'?'每侧次数':totalLegs?'每组总次数':'每组次数',rest_seconds:'组间休息（秒）'};
  document.querySelector('[for="target-value"]').textContent=labels.value;
  document.querySelector('#target-side-help').hidden=unit!=='per_side_reps'&&!totalLegs;
  if(totalLegs)document.querySelector('#target-side-help').textContent='每条腿完成一次起落计 1 次；左右相加，例如左 3 次＋右 3 次＝总共 6 次。';
  function paint(errors={},focus=false){
    let first=null;
    for(const [key,input] of Object.entries(fields)){
      const text=errors['target.'+key]||'';input.setAttribute('aria-invalid',String(Boolean(text)));
      const hint=document.querySelector('#'+input.id+'-error');hint.textContent=text;hint.hidden=!text;if(text&&!first)first=input;
    }
    if(focus)first?.focus();
  }
  function mode(){return modes.find(input=>input.checked)?.value||'free'}
  function toggle(){const target=mode()==='target';container.hidden=!target;for(const input of Object.values(fields))input.disabled=!target;paint()}
  function value(){
    if(mode()==='free')return {mode:'free',target:null};
    const parsed={},errors={};
    for(const [key,input] of Object.entries(fields)){
      const text=input.value.trim(),minimum=key==='rest_seconds'?0:1,maximum=key==='sets'?100:key==='value'?(unit==='seconds'?3600:1000):3600;
      const number=/^\d+$/.test(text)?Number(text):NaN;
      if(!Number.isSafeInteger(number)||number<minimum||number>maximum)errors['target.'+key]=`${labels[key]}请输入 ${minimum}–${maximum} 的整数`;
      else parsed[key]=number;
    }
    if(Object.keys(errors).length){paint(errors,true);throw new RequestError('请检查目标设置',422,errors,'VALIDATION_FAILED')}
    paint();return {mode:'target',target:{...parsed,unit}};
  }
  function restore(config){
    for(const input of modes)input.checked=input.value===config.mode;
    if(config.target)for(const [key,input] of Object.entries(fields))input.value=String(config.target[key]);
    toggle();
  }
  function matches(config,selected){return config.mode===selected.mode&&(selected.mode==='free'||['sets','value','unit','rest_seconds'].every(key=>config.target?.[key]===selected.target[key]))}
  for(const input of modes)input.addEventListener('change',toggle);
  for(const input of Object.values(fields))input.addEventListener('input',()=>{input.setAttribute('aria-invalid','false');document.querySelector('#'+input.id+'-error').hidden=true});
  toggle();return {value,restore,matches,paint};
}
