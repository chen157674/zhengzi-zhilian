import {api,confirmDialog,keepDialogFocus} from './app.js';
import {el,privateError} from './record_common.js';
import {mountMealEditor} from './meal_entry.js';

const $=id=>document.getElementById(id),page=$('nutrition-page');
let catalog=null,meal='lunch',scene='home',plate=null,selected=null,slot='protein',generation=0,busy=false;
let avoid=new Set(),draftAvoid=new Set(),manuallyChanged=false,priorPlate=null;
const undo=el('button','恢复采用前搭配','text-link');undo.type='button';undo.hidden=true;
document.querySelector('.food-main-actions').append(undo);
const names={staple:'主食',protein:'蛋白质',vegetable:'蔬菜'},dialogs=new Map();
const editor=mountMealEditor({onSaved:row=>{$('food-restriction-note').textContent='这一餐已记录，可到“我的餐食”查看。';}});
function error(message){$('food-page-error').textContent=message;$('food-page-error').hidden=false;if($('knowledge-ai-dialog')?.open){$('knowledge-error').textContent=message;$('knowledge-error').hidden=false;}}
function food(id){return catalog?.foods.find(x=>x.id===id);}
function allowed(item,excluded=avoid){return !excluded.has(item.id);}
function candidates(kind,excluded=avoid){return catalog.foods.filter(x=>x.slot===kind&&allowed(x,excluded));}
function validPlate(item,excluded=avoid){return item.foods.every(id=>food(id)&&allowed(food(id),excluded));}
function setBusy(value){busy=value;for(const b of document.querySelectorAll('[data-plate-meal]'))b.disabled=value;$('food-scene').disabled=value;$('food-change-plate').disabled=value||!plate;$('food-record-meal').disabled=value||!plate;}
function openDialog(dialog,trigger){
  if(dialog.open)return;
  const state={trigger:trigger||document.activeElement,scroll:scrollY};dialog.showModal();state.release=keepDialogFocus(dialog);dialogs.set(dialog,state);
}
function closeDialog(dialog){
  const state=dialogs.get(dialog);dialog.close();state?.release();dialogs.delete(dialog);
  if(state){scrollTo({top:state.scroll,behavior:'instant'});state.trigger?.focus({preventScroll:true});}
}
for(const dialog of [$('food-swap-dialog'),$('food-portion-dialog')]){
  dialog.addEventListener('cancel',event=>{event.preventDefault();closeDialog(dialog);});
  dialog.addEventListener('close',()=>{dialogs.get(dialog)?.release();dialogs.delete(dialog);});
}
function choosePlate(next=false){
  const plates=catalog.plates.filter(x=>validPlate(x)),key=plate&&Object.values(plate.slots).join(',');
  const distinct=plates.filter(x=>x.foods.join(',')!==key);
  const choice=(next?distinct:plates)[0];
  if(choice){
    plate={title:choice.title,method:choice.method,slots:Object.fromEntries(choice.foods.map(id=>[food(id).slot,id]))};
  }else if(!plate||!Object.values(plate.slots).every(id=>food(id)&&allowed(food(id)))){
    const slots={};for(const kind of Object.keys(names)){const first=candidates(kind)[0];if(!first){plate=null;error('当前条件下没有可用的'+names[kind]+'，可以调整不吃的食物。');return;}slots[kind]=first.id;}
    plate={slots,title:'搭好这一餐',method:'主食、蛋白质食物和蔬菜，选自己吃得惯的。'};
  }else if(next){$('food-restriction-note').textContent='当前条件下没有其他整套餐食，可以单独替换其中一样。';}
}
function render(){
  const host=$('food-three-plate');host.replaceChildren();host.setAttribute('aria-busy','false');
  for(const [kind,label]of Object.entries(names)){
    const item=food(plate?.slots[kind]);if(!item)continue;
    const section=el('section',undefined,'food-slot'),button=el('button'),image=el('img'),swap=el('button','换一个','food-swap-link');
    image.src=item.image;image.alt=item.name+'，搭配示意';image.width=960;image.height=720;image.decoding='async';
    button.type=swap.type='button';button.setAttribute('aria-label','替换'+label+'：'+item.name);button.append(image,el('strong',item.name));button.onclick=()=>openSwap(kind,button);swap.onclick=()=>openSwap(kind,swap);
    section.append(el('h2',label),button,swap);host.append(section);
  }
  $('food-combination-title').textContent=plate?.title||'先选一种适合的搭配';
  $('food-combination-method').textContent=plate?.method||'';
  for(const b of document.querySelectorAll('[data-plate-meal]'))b.setAttribute('aria-selected',String(b.dataset.plateMeal===meal));
  $('food-scene').value=scene;setBusy(busy);
}
async function load({preserve=false}={}){
  const own=++generation;setBusy(true);$('food-page-error').hidden=true;$('food-page-retry').hidden=true;
  try{
    const initial=$('food-catalog-data'),cached=initial?JSON.parse(initial.textContent):null;initial?.remove();const data=cached&&cached.meal_type===meal&&cached.scene===scene?cached:await api('/api/v1/nutrition/meal-options?'+new URLSearchParams({meal_type:meal,scene}));
    if(own!==generation)return;catalog=data;if(!preserve)plate=null;choosePlate();render();return true;
  }catch(e){if(own===generation&&!privateError(e)){error(e.message);$('food-page-retry').hidden=false;}}
  finally{if(own===generation){setBusy(false);$('food-three-plate').setAttribute('aria-busy','false');}}
}
function renderAvoid(){
  const host=$('food-avoid-choices');host.replaceChildren();
  for(const item of catalog.foods){
    const label=el('label'),input=el('input');input.type='checkbox';input.checked=draftAvoid.has(item.id);input.onchange=()=>{if(input.checked)draftAvoid.add(item.id);else draftAvoid.delete(item.id);if(!candidates(slot,draftAvoid).some(x=>x.id===selected))selected=candidates(slot,draftAvoid)[0]?.id||null;renderSwap();};
    label.append(input,document.createTextNode('不吃'+item.name));host.append(label);
  }
}
function renderSwap(){
  const host=$('food-swap-options');host.replaceChildren();
  for(const item of candidates(slot,draftAvoid)){
    const b=el('button',undefined,'food-substitution-option'),img=el('img');b.type='button';b.dataset.foodId=item.id;b.setAttribute('aria-pressed',String(item.id===selected));img.src=item.image;img.alt='';img.width=240;img.height=180;b.append(img,el('span',item.name));b.onclick=()=>{selected=item.id;renderSwap();};host.append(b);
  }
  if(!host.children.length)host.append(el('p','这些条件下没有可替换的食物，可以调整不吃的食物。','field-help'));
  $('food-swap-confirm').disabled=!selected;
}
function openSwap(kind,trigger){
  if(!catalog||busy)return;slot=kind;selected=plate?.slots[kind]||null;draftAvoid=new Set(avoid);$('food-swap-title').textContent='替换'+names[kind];$('food-swap-error').textContent='';renderAvoid();renderSwap();openDialog($('food-swap-dialog'),trigger);
}
$('food-swap-confirm').onclick=()=>{
  if(!selected||!candidates(slot,draftAvoid).some(x=>x.id===selected))return;
  const next={...plate.slots,[slot]:selected};
  for(const kind of Object.keys(names)){
    if(!allowed(food(next[kind]),draftAvoid)){
      const replacement=candidates(kind,draftAvoid)[0];
      if(!replacement){$('food-swap-error').textContent='当前限制下没有可用的'+names[kind]+'，请先调整条件。';return;}
      next[kind]=replacement.id;
    }
  }
  avoid=new Set(draftAvoid);manuallyChanged=true;plate={slots:next,title:Object.values(next).map(id=>food(id).name).join(' · '),method:'照自己的口味烹调，把这三样搭在一起。'};render();closeDialog($('food-swap-dialog'));
};
$('food-use-avoid').onclick=async()=>{
  const button=$('food-use-avoid');button.disabled=true;
  try{
    const data=await api('/api/v1/account/memories');let found=0;
    for(const row of data.memories.filter(x=>x.kind==='food_dislike')){
      for(const item of catalog.foods)if(item.aliases.some(alias=>row.value.includes(alias)||alias.includes(row.value))){draftAvoid.add(item.id);found++;}
    }
    selected=candidates(slot,draftAvoid).find(x=>x.id===selected)?.id||candidates(slot,draftAvoid)[0]?.id||null;renderAvoid();renderSwap();$('food-swap-error').textContent=found?'已采用已保存的不吃食物，确认后用于本次搭配。':'没有找到与当前食材对应的忌口，可以手动勾选。';
  }catch(e){if(!privateError(e))$('food-swap-error').textContent=e.message;}finally{button.disabled=false;}
};
for(const id of ['food-swap-close','food-swap-cancel'])$(id).onclick=()=>closeDialog($('food-swap-dialog'));
for(const id of ['food-portion-close','food-portion-ok'])$(id).onclick=()=>closeDialog($('food-portion-dialog'));
$('food-portion-open').onclick=()=>openDialog($('food-portion-dialog'),$('food-portion-open'));
$('food-portion-calculator').onclick=()=>{closeDialog($('food-portion-dialog'));document.querySelector('[data-calculator-open]').click();};
$('food-change-plate').onclick=()=>{if(!busy){choosePlate(true);render();}};
$('food-record-meal').onclick=()=>{if(plate&&!busy)editor.open({meal_type:meal,foods:Object.values(plate.slots).map(id=>({name:food(id).name,group:food(id).group,amount:null}))},$('food-record-meal'));};
async function changeContext(nextMeal,nextScene){
  if(nextMeal===meal&&nextScene===scene)return;
  if(manuallyChanged&&!await confirmDialog({title:'切换这餐的条件？',message:'当前搭配已手动调整，切换后会按新条件重新搭配。',confirmLabel:'切换并重新搭配'})){$('food-scene').value=scene;return;}
  meal=nextMeal;scene=nextScene;manuallyChanged=false;await load();
}
for(const button of document.querySelectorAll('[data-plate-meal]'))button.onclick=()=>changeContext(button.dataset.plateMeal,scene);
$('food-scene').onchange=()=>changeContext(meal,$('food-scene').value);
$('food-page-retry').onclick=()=>load({preserve:true});

const topics={
  portion:{title:'先认识分量，再慢慢调整',image:'/static/food/cutouts/rice.webp',steps:['用常用的碗理解主食分量','鱼肉蛋豆与蔬菜一起搭配','按自己的食欲、活动和已有安排调整'],note:''},
  canteen:{title:'食堂点餐，先凑齐三样',image:'/static/food/cafeteria-lunch.webp',steps:['选一份主食','选鱼肉蛋豆','再搭蔬菜'],note:'外卖同样适用：按套餐搭配，酱汁可以另放。'},
  training:{title:'训练前后，接上正常一餐',image:'/static/food/balanced-meal.webp',steps:['训练前选择自己消化舒服的食物','按时间留出消化空间','训练后接上主食、鱼肉蛋豆和蔬菜'],note:'不需要为了第一次训练临时换一套饮食，也不把补充剂当作入门必需品。'}
};
function showGuide(topic,update=true){
  const data=topics[topic]||topics.canteen;$('food-plate-page').hidden=true;$('food-reading').hidden=false;$('food-page-title').textContent='小白吃饭指南';
  const host=$('food-guide-content');host.replaceChildren();const layout=el('div',undefined,'food-guide-reading'),image=el('img'),copy=el('div'),list=el('ol');
  image.src=data.image;image.alt=data.title+'的食物搭配示意';image.width=1440;image.height=960;
  for(const step of data.steps)list.append(el('li',step));copy.append(el('h2',data.title),list);if(data.note)copy.append(el('p',data.note,'field-help'));layout.append(image,copy);host.append(layout);
  for(const [title,text]of [['饮料怎么选','含糖饮品可从频次、分量和替换入手，日常也可以选白水或无糖茶。'],['不想吃鸡胸肉','鱼、鸡蛋和豆腐也是日常可以轮换的蛋白质食物，结合自己明确的忌口来选。']]){
    const details=el('details',undefined,'food-quiet-guide');details.append(el('summary',title),el('p',text));host.append(details);
  }
  for(const button of document.querySelectorAll('#food-guide-topics button'))button.setAttribute('aria-selected',String(button.dataset.foodGuide===topic));
  if(update)history.replaceState(null,'','/coach/nutrition?'+new URLSearchParams({view:'guide',topic}));
}
for(const button of document.querySelectorAll('[data-food-guide]'))button.onclick=()=>showGuide(button.dataset.foodGuide);
$('food-guide-back').onclick=()=>{$('food-reading').hidden=true;$('food-plate-page').hidden=false;$('food-page-title').textContent='今天这一餐';history.replaceState(null,'','/coach/nutrition');};
window.addEventListener('fitness-meal-suggestion',async event=>{
  const suggestion=event.detail;
  if(!suggestion||suggestion.content_version!==catalog?.content_version)return error('建议版本已经变化，请重新生成。');
  if(Object.keys(suggestion.slots||{}).sort().join(',')!==Object.keys(names).sort().join(','))return error('这份建议还没有可采用的完整搭配。');
  for(const kind of Object.keys(names)){const item=food(suggestion.slots[kind]);if(!item||item.slot!==kind||!allowed(item))return error('这份建议与当前不吃的食物设置冲突，请重新调整。');}
  const snapshot={meal,scene,plate:structuredClone(plate)};
  meal=suggestion.meal_type;scene=suggestion.scene;const expected=generation+1,loaded=await load();
  if(!loaded||generation!==expected||!catalog||catalog.meal_type!==meal||catalog.scene!==scene)return;
  priorPlate=snapshot;undo.hidden=false;manuallyChanged=true;
  plate={slots:{...suggestion.slots},title:Object.values(suggestion.slots).map(id=>food(id).name).join(' · '),method:'已采用AI搭配建议，用餐后可记录这一餐。'};render();window.dispatchEvent(new CustomEvent('fitness-meal-adopted'));
});
undo.onclick=async()=>{if(!priorPlate||busy)return;const snapshot=priorPlate;meal=snapshot.meal;scene=snapshot.scene;const expected=generation+1;if(!await load()||generation!==expected)return;if(!Object.values(snapshot.plate.slots).every(id=>food(id)&&allowed(food(id))))return error('原搭配与当前忌口不符，请重新选择食物。');plate=snapshot.plate;priorPlate=null;undo.hidden=true;manuallyChanged=true;render();};
window.addEventListener('fitness-auth-revoked',()=>{generation++;catalog=plate=priorPlate=null;avoid.clear();page.replaceChildren();});
window.addEventListener('pagehide',()=>{generation++;for(const dialog of dialogs.keys())closeDialog(dialog);});
load();const intent=new URLSearchParams(location.search);if(intent.get('view')==='guide')showGuide(intent.get('topic')||'canteen',false);
