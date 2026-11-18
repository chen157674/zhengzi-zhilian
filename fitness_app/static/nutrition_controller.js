import {api,confirmDialog,keepDialogFocus} from './app.js';
import {el,privateError} from './record_common.js';
const $=id=>document.getElementById(id),form=$('diet-form');let generation=0,inputVersion=0,profileVersion=null,autoSaveTimer=null,disposed=false,busy=false,checkedProfile=null;
const calculator=$('nutrition-calculator-dialog');let opened=false,opener=null,releaseFocus=null,savedScroll=0,openEpoch=0;
const labels={adult:'成年情况',age_years:'年龄',weight:'体重及单位',formula_sex:'公式类别',activity_category:'整体活动',exercise_status:'运动情况'};
function fail(e){if(privateError(e))return;$('diet-error').textContent=e.message;$('diet-error').hidden=false;}
function fields(){const data={subject:'self'};for(const name of ['age_years','weight','height','formula_sex','exercise_status','activity_category','goal','special_context']){const value=form.elements[name].value;if(value!=='')data[name]=value;}if(data.weight)data.weight_unit=form.elements.weight_unit.value;if(data.height)data.height_unit=form.elements.height_unit.value;return data;}
function fill(data,quiet=false){checkedProfile=null;$('diet-profile-check').hidden=true;form.reset();for(const [key,value]of Object.entries(data)){if(form.elements[key]){if(form.elements[key].type==='checkbox')form.elements[key].checked=value===true;else form.elements[key].value=value??'';}}invalidate(quiet);}
function invalidate(quiet=false){clearTimeout(autoSaveTimer);generation++;inputVersion++;$('diet-results').replaceChildren();$('diet-output').hidden=true;if(!quiet)$('diet-status').textContent='';}
function clear(){invalidate(true);busy=false;form.hidden=false;form.reset();$('diet-estimate').disabled=false;$('diet-estimate').removeAttribute('aria-busy');$('diet-estimate').textContent='查看我的摄入';checkedProfile=null;$('diet-profile-check').hidden=true;$('diet-profile-state').textContent='';$('diet-status').textContent='';}
function profileCheck(row,message){checkedProfile=row;$('diet-profile-check').hidden=false;$('diet-profile-check-fields').replaceChildren(el('p',message));const names={age_years:'年龄',weight:'体重',weight_unit:'体重单位',height:'身高',height_unit:'身高单位',adult:'已成年',formula_sex:'公式类别',activity_category:'整体活动',exercise_status:'运动情况',goal:'目标',special_context:'适用情况',subject:'资料对象'};for(const [key,value]of Object.entries(row.inputs))$('diet-profile-check-fields').append(el('p',(names[key]||key)+'：'+({kg:'公斤',jin:'市斤',lb:'磅',cm:'厘米',m:'米',male:'男',female:'女',regular:'规律运动',none:'不规律运动',light:'久坐或轻活动',moderate:'活跃或中等活动',vigorous:'长期高度活跃',maintenance:'维持体重',muscle_gain:'增肌',general:'一般参考',none_declared:'一般健康成年人',not_applicable:'需专业管理',unknown:'请选择',self:'本人',true:'是',false:'否'}[String(value)]||String(value)),'field-help'));if(!Object.keys(row.inputs).length)$('diet-profile-check-fields').append(el('p','当前没有已存营养字段。'));}
$('diet-profile-check-adopt').onclick=()=>{if(!checkedProfile)return;const row=checkedProfile;profileVersion=row.version;fill(row.inputs);$('diet-profile-check').hidden=true;checkedProfile=null;$('diet-profile-state').textContent='已采用当前营养资料';};$('diet-profile-check-keep').onclick=()=>{$('diet-profile-check').hidden=true;checkedProfile=null;$('diet-status').textContent='已保留本次输入，可继续编辑。';};
function render(data){
 const output=$('diet-output'),host=$('diet-results');output.hidden=false;host.replaceChildren();
 const valid=data.items.filter(item=>item.status==='calculated'),primary=valid.find(x=>x.item==='energy')||valid[0];
 if(!primary||data.items.some(x=>x.status==='input_required')){
  for(const item of data.items.filter(x=>x.status!=='calculated'))host.append(el('p',item.status==='input_required'?'还需填写：'+item.missing.map(x=>labels[x]||x).join('、'):item.message,'field-help'));return;
 }
 form.hidden=true;const card=el('section',undefined,'diet-approved-result');card.append(el('h2',primary.item==='energy'?'每日能量参考':'每日蛋白质参考'));
 const quantity=el('p',undefined,'diet-reference-number');quantity.append(el('strong',Number(primary.display_lower).toLocaleString()+'—'+Number(primary.display_upper).toLocaleString()),el('span',primary.item==='energy'?' 千卡/天':' 克/天'));card.append(quantity,el('p','按本次填写资料估算','field-help'));
 for(const item of valid.filter(x=>x!==primary))card.append(el('p',(item.item==='protein'?'每日蛋白质参考：':'每日能量参考：')+item.display_lower+'—'+item.display_upper+(item.item==='protein'?' 克/天':' 千卡/天'),'field-help'));
 card.append(el('hr',undefined,'approved-rule'),el('p','这是一整天的参考，不是一顿饭的固定配量。'));
 const method=el('details',undefined,'food-quiet-guide');method.append(el('summary','估算方法与适用条件'));
 for(const item of valid){const source=item.source;method.append(el('p',typeof source?.citation==='string'?source.citation:typeof source?.title==='string'?source.title:'使用当前服务的估算方法，活动水平与填写资料变化后需要重新计算。','field-help'));}
 card.append(method);const actions=el('div',undefined,'approved-dialog-actions'),back=el('button','返回这一餐','button'),modify=el('button','修改资料','text-link');back.type=modify.type='button';back.onclick=closeCalculator;modify.onclick=()=>{form.hidden=false;output.hidden=true;form.elements.age_years.focus();};actions.append(back,modify);card.append(actions);host.append(card);
}
async function autoSave(){
 if(disposed||!opened||busy||checkedProfile)return false;const values=fields(),own=generation;
 try{const old=profileVersion===null?await saved():{version:profileVersion};if(disposed||own!==generation)return false;const result=await api('/api/v1/nutrition/profile',{method:'PUT',body:{expected_version:old.version,inputs:values}});if(disposed||own!==generation)return false;profileVersion=result.version;$('diet-delete').hidden=false;$('diet-profile-state').textContent='已保存本次采用的资料';return true;}
 catch(error){if(own!==generation)return false;if(error.unknown||error.status===409){try{const row=await saved();if(own===generation)profileCheck(row,'资料保存尚待核对，本次输入保留。');}catch(readError){fail(readError);}}else fail(error);return false;}
}
async function estimate(event){
 event?.preventDefault();if(busy)return;$('diet-error').hidden=true;const own=generation,version=inputVersion;busy=true;$('diet-estimate').disabled=true;$('diet-estimate').setAttribute('aria-busy','true');$('diet-estimate').textContent='正在计算';
 try{const data=await api('/api/v1/nutrition/estimate',{method:'POST',body:{inputs:fields(),input_version:version,items:['protein','energy']}});if(disposed||own!==generation||version!==inputVersion)return;render(data);const missing=data.items.find(x=>x.status==='input_required'&&x.missing.length)?.missing[0];
  if(missing)form.elements[missing==='weight'?'weight':missing]?.focus();else{busy=false;const remember=$('diet-remember')?.checked;$('diet-status').textContent=remember?(await autoSave()?'参考已计算，本次资料已保存。':'参考已计算，资料保存需要核对。'):'参考已计算，未另行保存本次资料。';}
 }catch(error){if(own===generation)fail(error);}finally{if(own===generation){busy=false;$('diet-estimate').disabled=false;$('diet-estimate').removeAttribute('aria-busy');$('diet-estimate').textContent='看参考';}}
}
async function saved(){const own=generation;const data=await api('/api/v1/nutrition/profile');if(!disposed&&own===generation)profileVersion=data.version;return data;}
async function initProfile(){
 const own=generation,epoch=openEpoch;$('diet-profile-state').textContent='正在核对资料…';
 try{const row=await saved();if(disposed||!opened||own!==generation)return;$('diet-use-saved').hidden=!Object.keys(row.inputs||{}).length;$('diet-profile-state').textContent=Object.keys(row.inputs||{}).length?'有已保存资料，点击“采用已存资料”可填入。':'可以手动填写，也可以主动采用健身档案。';}
 catch(error){if(opened&&own===generation){$('diet-profile-state').textContent='资料暂时未读到，可以手动填写。';fail(error);}}
}
form.addEventListener('input',()=>invalidate());form.onsubmit=estimate;$('diet-clear').onclick=clear;
$('diet-use-training').onclick=async()=>{
 const button=$('diet-use-training');if(button.disabled)return;const own=generation;button.disabled=true;button.setAttribute('aria-busy','true');
 try{const data=await api('/api/v1/me/training-profile');if(disposed||!opened||own!==generation)return;const values={};for(const [a,b]of [['height_cm','height'],['weight_kg','weight'],['age_years','age_years']])if(data[a]!=null)values[b]=data[a];if(values.weight)values.weight_unit='kg';if(values.height)values.height_unit='cm';fill(values,true);$('diet-profile-state').textContent=Object.keys(values).length?'已同步健身档案':'暂无身体资料';$('diet-status').textContent='已填入本次资料，请保存。';}
 catch(error){if(opened&&own===generation)fail(error);}
 finally{button.disabled=false;button.removeAttribute('aria-busy');}
};
if($('diet-use-saved'))$('diet-use-saved').onclick=async()=>{const own=generation;try{const data=await saved();if(disposed||own!==generation)return;fill(data.inputs,true);$('diet-profile-state').textContent=Object.keys(data.inputs).length?'已采用营养资料':'暂无已保存营养资料';}catch(error){fail(error);}};
if($('diet-save'))$('diet-save').onclick=async()=>{if(busy)return;if(checkedProfile){fail(new Error('请先核对服务器资料，选择采用或保留本地输入。'));return;}busy=true;const own=generation;try{const old=profileVersion===null?await saved():{version:profileVersion};if(disposed||own!==generation)return;const data=await api('/api/v1/nutrition/profile',{method:'PUT',body:{expected_version:old.version,inputs:fields()}});if(disposed||own!==generation)return;profileVersion=data.version;invalidate(true);$('diet-status').textContent='营养资料已保存。';}catch(error){if(disposed||own!==generation)return;if(error.unknown||error.status===409){try{const row=await saved();if(disposed||own!==generation)return;profileCheck(row,error.status===409?'资料已在其他页面变化，本地输入仍保留。':'请重新读取资料保存状态。');}catch(readError){fail(readError);}}else fail(error);}finally{busy=false;}};
$('diet-delete').onclick=async()=>{if(busy)return;releaseFocus?.();releaseFocus=null;let confirmed;try{confirmed=await confirmDialog({title:'清除已存营养资料',message:'清除已保存的营养资料及当前输入，不影响健身档案或训练记录。',confirmLabel:'清除资料',danger:true});}finally{if(opened)releaseFocus=keepDialogFocus(calculator);}if(!confirmed)return;busy=true;const own=generation;try{const old=await saved();if(disposed||own!==generation)return;const data=await api('/api/v1/nutrition/profile',{method:'DELETE',body:{expected_version:old.version}});if(disposed||own!==generation)return;profileVersion=data.version;clear();$('diet-delete').hidden=true;$('diet-status').textContent='已清除营养资料。';}catch(error){if(disposed||own!==generation)return;if(error.unknown||error.status===409){try{const row=await saved();if(disposed||own!==generation)return;if(error.unknown&&!Object.keys(row.inputs).length){clear();profileVersion=row.version;$('diet-delete').hidden=true;$('diet-status').textContent='已核对服务器：营养资料已清空。';}else profileCheck(row,'请重新读取资料状态后继续。');}catch(readError){fail(readError);}}else fail(error);}finally{busy=false;}};
window.addEventListener('pagehide',()=>{clear();disposed=true;});window.addEventListener('pageshow',e=>{disposed=false;if(e.persisted)clear();});window.addEventListener('fitness-auth-revoked',()=>{clear();disposed=true;});

function openCalculator(button){
 if(opened||document.querySelector('dialog[open]'))return;openEpoch++;opened=true;disposed=false;opener=button||document.activeElement;savedScroll=scrollY;calculator.showModal();document.documentElement.classList.add('calculator-modal-open');releaseFocus=keepDialogFocus(calculator);$('calculator-close').focus({preventScroll:true});initProfile();
}
function closeCalculator(){
 if(!opened)return;openEpoch++;opened=false;generation++;busy=false;clearTimeout(autoSaveTimer);$('diet-estimate').disabled=false;$('diet-estimate').removeAttribute('aria-busy');$('diet-estimate').textContent='看参考';$('diet-error').hidden=true;releaseFocus?.();releaseFocus=null;calculator.close();document.documentElement.classList.remove('calculator-modal-open');scrollTo({top:savedScroll,behavior:'instant'});if(opener?.isConnected)opener.focus({preventScroll:true});opener=null;
}
for(const button of document.querySelectorAll('[data-calculator-open]'))button.onclick=()=>openCalculator(button);
$('calculator-close').onclick=closeCalculator;
calculator.addEventListener('cancel',event=>{event.preventDefault();closeCalculator();});
calculator.addEventListener('close',()=>{if(opened)closeCalculator();});
calculator.addEventListener('click',event=>{if(event.target!==calculator||document.querySelector('#confirm-dialog[open]'))return;const b=calculator.getBoundingClientRect();if(event.clientX<b.left||event.clientX>b.right||event.clientY<b.top||event.clientY>b.bottom)closeCalculator();});
window.addEventListener('fitness-auth-revoked',()=>{closeCalculator();clear();});
if(new URLSearchParams(location.search).get('calculator')==='1')openCalculator(document.querySelector('[data-calculator-open]'));

window.addEventListener('pagehide',()=>{clear();disposed=true;generation++;});
