import {api,keepDialogFocus} from './app.js';
import {el,privateError} from './record_common.js';
import {loadFoodAssets,resolveFoodAsset,foodPicture} from './food_assets.js';
const $=id=>document.getElementById(id),library=$('knowledge-page'),dialog=$('knowledge-ai-dialog'),root=library||dialog,guide=$('action-guide-page'),nutrition=$('nutrition-page'),nutritionChat=Boolean(nutrition);
let lastSearch=null,displayedTask=null,validating=false,selectedAction=null,modalActive=false,modalOpener=null,releaseModalFocus=null,savedScroll=0,previousTask=null,activeReply=null,activeQuestion=null,turnTasks=new Set();
let generation=0,task=null,pending=null,disposed=false,timer=null,catalog=null,lastResult=null,actionPage=1,lessonPage=1,searching=false,lastSearchAI=false,contextLoading=false,analysisMode=false;
function tutorialLink(text,actionId,cls='text-link'){const a=el('a',text,cls);a.href='/actions/'+encodeURIComponent(actionId);a.dataset.nav='';return a;}
function tutorialFigure(lesson){
 const figure=el('figure',undefined,'learn-sequence'),views=lesson.views||[];let selected=views.find(x=>x.id==='side')||views[0];
 const images=el('div',undefined,views.length?'learn-phase-grid':'learn-image-panel'),caption=el('figcaption');
 function draw(host,cap){
  host.replaceChildren();cap.replaceChildren();
  if(selected){for(const item of selected.images){const img=el('img');img.src=item.url;img.alt=item.alt;img.width=item.width;img.height=item.height;img.loading='lazy';host.append(img);}}
  else{const img=el('img');img.src=lesson.image;img.alt=lesson.image_alt;img.width=1536;img.height=1024;img.loading='lazy';host.append(img);}
  for(const [index,label]of lesson.phases.entries()){const span=el('span');span.append(el('b',String(index+1).padStart(2,'0')),document.createTextNode(label));cap.append(span);}
 }
 draw(images,caption);figure.append(images,caption);
 const toolbar=el('div',undefined,'learn-figure-tools');
 if(views.length>1){const switches=el('div',undefined,'learn-view-switch');for(const view of views){const button=el('button',view.label);button.type='button';button.dataset.view=view.id;button.setAttribute('aria-pressed',String(view===selected));button.onclick=()=>{selected=view;for(const item of switches.children)item.setAttribute('aria-pressed',String(item===button));draw(images,caption);};switches.append(button);}toolbar.append(switches);}
 const zoom=el('button','放大图解','text-link');zoom.type='button';zoom.onclick=()=>{
  const modal=el('dialog',undefined,'learn-lightbox'),head=el('div',undefined,'approved-dialog-heading'),close=el('button','×','approved-close'),copy=el('div',undefined,images.className),labels=el('div',undefined,'learn-lightbox-labels');close.type='button';close.setAttribute('aria-label','关闭图解');head.append(el('h2',(lesson.name||'动作')+' · 动作图解'),close);draw(copy,labels);modal.append(head,copy,labels);document.body.append(modal);modal.showModal();const release=keepDialogFocus(modal);const dismiss=()=>{release();modal.close();modal.remove();zoom.focus({preventScroll:true});};close.onclick=dismiss;modal.addEventListener('cancel',event=>{event.preventDefault();dismiss();});
 };toolbar.append(zoom);figure.append(toolbar);return figure;
}
function learningIcon(name){const image=el('img');image.src='/static/icons/hugeicons/'+name+'Icon.svg';image.alt='';image.width=20;image.height=20;return image;}
function tutorialContent(lesson,{compact=false,practice=null}={}){
 const section=el('section',undefined,'learn-tutorial');
 if(compact){section.append(tutorialFigure(lesson));if(lesson.action_id)section.append(tutorialLink('看完整图解 →',lesson.action_id));return section;}
 const layout=el('div',undefined,'learn-detail-layout'),main=el('div',undefined,'learn-main'),aside=el('aside',undefined,'learn-aside');
 if(lesson.name)main.append(el('h1',lesson.name),el('p',lesson.category+' · '+lesson.level,'field-help'));main.append(tutorialFigure(lesson));
 const steps=el('ol',undefined,'learn-steps');for(const [index,step]of lesson.steps.entries()){const item=el('li'),head=el('div',undefined,'learn-step-heading');head.append(el('b',String(index+1).padStart(2,'0')),el('strong',step.title));item.append(head,el('p',step.text));steps.append(item);}
 aside.append(el('h2','怎么做'),steps);const cues=el('div',undefined,'learn-reminders');cues.append(el('h3','记住这几点'));for(const text of lesson.reminders)cues.append(el('span',text));aside.append(cues);
 const faq=el('details',undefined,'learn-faq');faq.append(el('summary','常见问题'));const tabs=el('div',undefined,'approved-tabs'),answer=el('div',undefined,'learn-faq-answer');
 const questions=lesson.steps.map((step,index)=>({label:index===0?'开始准备':index===1?'动作过程':'回程控制',title:step.title,text:step.text}));
 for(const [index,item]of questions.entries()){const button=el('button',item.label);button.type='button';button.setAttribute('aria-pressed',String(index===0));button.onclick=()=>{for(const other of tabs.children)other.setAttribute('aria-pressed',String(other===button));answer.replaceChildren(el('h3',item.title),el('p',item.text));};tabs.append(button);}answer.append(el('h3',questions[0].title),el('p',questions[0].text));faq.append(tabs,answer);aside.append(faq);
 if(practice){
  const actions=el('div',undefined,'button-row learn-practice-actions'),start=el('a','开始练习','button');start.href='/actions/'+practice.action.id+'/practice';start.dataset.nav='';
  const join=el('a','加入训练计划','text-link');join.href='/training/plan?'+new URLSearchParams({add_action:practice.action.id});join.dataset.nav='';actions.append(start,join);aside.append(actions);
  const video=el('a','上传视频','text-link');video.href=practice.links.video;video.dataset.nav='';aside.append(video,framingNotes(practice.view_guidance.steps||[]));
  const ask=el('button','还想问？问 AI 教练 →','text-link learn-ask');ask.type='button';ask.dataset.actionName=practice.action.name;ask.onclick=()=>openKnowledgeDialog(practice.action.id,ask);aside.append(ask);
 }else if(lesson.action_id)aside.append(tutorialLink('看完整图解 →',lesson.action_id));
 layout.append(main,aside);section.append(layout);return section;
}
function framingNotes(steps){
 const section=el('section',undefined,'learn-framing'),heading=el('h3');heading.append(learningIcon('Target01'),document.createTextNode('拍摄要点'));section.append(heading);
 const list=el('ul',undefined,'learn-framing-list');
 for(const step of steps)for(const part of step.split(/[；;]/).map(x=>x.trim()).filter(Boolean))list.append(el('li',part));
 section.append(list);return section;
}
function compactSources(){const host=$('knowledge-sources');host.replaceChildren();host.hidden=true;}

function renderLessons(page=1){const host=$('learn-lesson-list');host.replaceChildren();const selected=document.querySelector('[data-learn-equipment][aria-pressed=true]')?.dataset.learnEquipment||'',query=$('learn-search').value.trim().toLowerCase(),lessons=(catalog.lessons||[]).filter(x=>(!selected||x.category===selected)&&(!query||[x.name,x.focus,x.category].some(v=>v.toLowerCase().includes(query))));const pages=Math.max(1,Math.ceil(lessons.length/9));lessonPage=Math.min(Math.max(1,page),pages);for(const lesson of lessons.slice((lessonPage-1)*9,lessonPage*9)){const card=el('a',undefined,'learn-lesson-card');card.href=lesson.url;card.dataset.nav='';const visual=el('div',undefined,'learn-card-image'),image=el('img');image.src=lesson.image;image.alt=lesson.image_alt;image.width=1536;image.height=1024;image.loading='lazy';visual.append(image);const copy=el('div',undefined,'learn-card-copy');copy.append(el('span',lesson.category+' · '+lesson.focus+' · '+lesson.level,'learn-card-meta'),el('h3',lesson.name),el('p',lesson.tagline),el('span','看图学动作 →','learn-card-action'));card.append(visual,copy);host.append(card);}if(!host.children.length)host.append(el('p','没有找到这个动作，换个名称或训练部位试试。','field-help'));$('learn-page-state').textContent='共 '+lessons.length+' 个图解动作 · 第 '+lessonPage+' / '+pages+' 页';$('learn-prev').hidden=lessonPage<=1;$('learn-next').hidden=lessonPage>=pages;}

function failure(error){if(privateError(error))return;const box=$(root?'knowledge-error':'guide-error');box.textContent=error.message;box.hidden=false;$(root?'knowledge-retry':'guide-retry').hidden=false;if(root)$('knowledge-settings').hidden=!['AI_PERSONAL_KEY_MISSING','AI_SYSTEM_UNAVAILABLE','COACH_MODEL_UNAVAILABLE'].includes(error.code);}
function controls(){if(!root)return;const active=Boolean(task)||searching||Boolean(pending);$('knowledge-search').disabled=active||contextLoading||!catalog||!$('knowledge-query').value.trim();$('knowledge-search').firstChild.textContent=analysisMode?(previousTask?'继续分析':'开始分析'):'发送';$('knowledge-stop').hidden=!active;$('knowledge-clear').disabled=active;for(const b of document.querySelectorAll('[data-analysis-kind]'))b.disabled=active;for(const b of $('knowledge-examples').querySelectorAll('button'))b.disabled=active;}
function scrollChat(){const body=dialog.querySelector('.knowledge-dialog-body');body.scrollTop=body.scrollHeight;}
function chatMessage(role,text=''){
 $('knowledge-chat-welcome').hidden=true;const message=el('article',undefined,'knowledge-message knowledge-message--'+role);
 if(role==='assistant'){const author=el('div',undefined,'knowledge-message-author');author.append(learningIcon('AiMagic'),el('span','AI 教练'));message.append(author);}
 const content=el('div',undefined,'knowledge-message-content');if(text)content.append(el('p',text));message.append(content);$('knowledge-messages').append(message);scrollChat();return message;
}
function renderAnswer(message,text){
 const host=message.querySelector('.knowledge-message-content');host.replaceChildren();let list=null;
 for(const line of text.split(/\n+/).map(x=>x.trim()).filter(Boolean)){
  const match=line.match(/^(?:\d+[.、）)]|[-*])\s*(.+)$/);
  if(match){if(!list){list=el('ol');host.append(list);}list.append(el('li',match[1]));}
  else{list=null;host.append(el('p',line));}
 }
}
async function cancel(){const id=task;task=null;clearTimeout(timer);controls();if(id)await api('/api/v1/coach/tasks/'+id+'/cancel',{method:'POST',body:{}});}
function invalidate(){
 if(dialog)dialog.dataset.analysisState='empty';generation++;searching=false;const ids=new Set([...turnTasks,task,pending?.id].filter(Boolean));task=pending=null;clearTimeout(timer);for(const id of ids)api('/api/v1/coach/tasks/'+id+'/cancel',{method:'POST',body:{}}).catch(()=>{});
 turnTasks.clear();previousTask=displayedTask=lastResult=activeReply=activeQuestion=lastSearch=null;lastSearchAI=false;$('knowledge-messages').replaceChildren();$('knowledge-result').replaceChildren();$('knowledge-sources').replaceChildren();$('knowledge-reading').hidden=true;$('knowledge-chat-welcome').hidden=false;$('knowledge-copy').hidden=true;$('knowledge-clarification').hidden=true;$('knowledge-error').hidden=true;$('knowledge-retry').hidden=true;controls();
}
function scope(){if(nutritionChat)return {domain:'nutrition'};return selectedAction&&catalog?.actions.some(x=>x.action_id===selectedAction)?{domain:'movement',action_id:selectedAction}:{domain:'all'};}
async function search(event){
 event?.preventDefault();if(task||pending||searching||contextLoading)return;const text=$('knowledge-query').value.trim();if(!text)return;
 if(analysisMode){$('knowledge-messages').replaceChildren();dialog.dataset.analysisState='loading';}
 generation++;const own=generation;lastSearch={query:text,...scope(),limit:5};activeQuestion=chatMessage('user',text);activeReply=chatMessage('assistant');activeReply.dataset.state='pending';const thinking=el('p',analysisMode?'正在识别食物并分析搭配…':'正在思考…','knowledge-thinking');thinking.setAttribute('role','status');activeReply.querySelector('.knowledge-message-content').append(thinking);
 $('knowledge-query').value='';$('knowledge-error').hidden=true;$('knowledge-retry').hidden=true;$('knowledge-settings').hidden=true;$('knowledge-copy').hidden=true;searching=true;controls();
 try{const result=await api('/api/v1/knowledge/search',{method:'POST',body:analysisMode?{...lastSearch,query:'一餐搭配 主食 蛋白质 蔬菜 食物选择 '+text.slice(0,350)}:lastSearch});if(disposed||own!==generation)return;searching=false;
 if(result.status!=='matched'){renderAnswer(activeReply,'换个动作或训练问题问问吧。');activeReply.dataset.state='ready';$('knowledge-state').textContent='换个动作或训练问题问问吧。';controls();return;}
 await generate();
 }catch(error){if(own===generation){searching=false;renderAnswer(activeReply,'这次没能连接上，请重试。');failure(error);controls();}}
}
async function watch(id,own){
 clearTimeout(timer);if(disposed||own!==generation||task!==id)return;
 try{const row=await api('/api/v1/coach/tasks/'+id);if(disposed||own!==generation||task!==id)return;
 if(row.status==='ready'&&row.result){
  if(row.result.analysis){await loadFoodAssets();if(disposed||own!==generation||task!==id)return;}
  const chatBody=dialog.querySelector('.knowledge-dialog-body'),follow=chatBody.scrollHeight-chatBody.scrollTop-chatBody.clientHeight<90;displayedTask=previousTask=id;lastResult=row.result;renderAnswer(activeReply,row.result.summary);activeReply.dataset.state='ready';activeReply.dataset.taskId=id;
  const actions=el('div',undefined,'knowledge-message-actions'),copy=el('button','复制','knowledge-message-copy');copy.type='button';copy.onclick=async()=>{try{await navigator.clipboard.writeText([row.result.summary,...(row.result.analysis?['这一餐：'+row.result.analysis.foods.map(x=>x.name+(x.amount?' '+x.amount:'')).join('、'),'下一餐：'+row.result.analysis.next_meal,...row.result.analysis.swaps.map(x=>x.from+' → '+x.to+'：'+x.reason)]:[])].join('\n'));copy.textContent='已复制';}catch{failure(new Error('可选中文字复制'));}};actions.append(copy);
  if(row.result.memory_saved?.saved){const saved=el('a',row.result.memory_saved.journal_id?'已记入我的餐食 ↗':'已更新我的记忆 ↗','text-link');saved.href='/coach/meals'+(row.result.memory_saved.journal_id?'':'#memories');saved.dataset.nav='';actions.append(saved);}
  if(row.result.analysis){renderNutritionAnalysis(activeReply,row.result.analysis,row.result.analysis_kind,row.result.nutrition_reference||[]);dialog.dataset.analysisState='ready';activeReply.querySelector('.knowledge-message-author')?.remove();activeQuestion.hidden=true;}
  const literature=(row.result.sources||[]).filter(x=>x.source?.citation);
  if(literature.length){const link=el('a','参考文献 '+new Set(literature.map(x=>x.source.reference_id)).size+' 篇','text-link');link.href='/coach/references';link.target='_blank';link.rel='noopener noreferrer';actions.append(link);}
  if(row.result.adopted_saved_nutrition&&!row.result.analysis){
   const refs=el('div',undefined,'knowledge-nutrition-reference');
   for(const item of row.result.nutrition_reference||[])if(item.status==='calculated'){const card=el('div');card.append(el('span',item.item==='protein'?'每日蛋白质':'每日维持热量'),el('strong',item.display_lower+'—'+item.display_upper+' '+(item.item==='protein'?'克':'千卡')));refs.append(card);}
   if(refs.childElementCount)activeReply.append(refs);
  }
  if(row.result.meal_suggestion){const adopt=el('button','采用这份建议','button secondary small food-apply-suggestion');adopt.type='button';adopt.onclick=async()=>{adopt.disabled=true;try{const fresh=await api('/api/v1/coach/tasks/'+id);if(fresh.status!=='ready'||!fresh.result?.meal_suggestion)throw new Error('建议依据已变化，请重新生成后采用。');window.dispatchEvent(new CustomEvent('fitness-meal-suggestion',{detail:fresh.result.meal_suggestion}));}catch(error){failure(error);}finally{adopt.disabled=false;}};actions.append(adopt);}if(row.result.tutorial)actions.append(tutorialLink('查看动作图解 →',row.result.tutorial.action_id));activeReply.append(actions);
  $('knowledge-result').textContent=row.result.summary;$('knowledge-copy').hidden=false;$('knowledge-state').textContent='';task=pending=null;controls();if(analysisMode){const header=activeReply.querySelector('.nutrition-report-heading');if(matchMedia('(max-width:767px)').matches){const offset=dialog.scrollTop+header.getBoundingClientRect().top-dialog.getBoundingClientRect().top-dialog.querySelector('.knowledge-dialog-header').getBoundingClientRect().height-12;dialog.scrollTo({top:offset,behavior:'auto'});}else chatBody.scrollTop=0;}else if(follow)scrollChat();return;
 }
 if(row.status==='awaiting_answer'){renderAnswer(activeReply,row.result?.question||row.result?.summary||'请补充一个条件');$('knowledge-question').textContent=row.result?.question||'补充信息';$('knowledge-clarification').hidden=false;controls();return;}
 if(['queued','running','cancelling'].includes(row.status)){timer=setTimeout(()=>watch(id,own),900);return;}
 task=pending=null;renderAnswer(activeReply,{cancelled:'已停止回答',source_invalid:'内容已变化，请重新提问',expired:'请重新提问',no_match:'换个动作或训练问题问问吧。'}[row.status]||'请重试生成回答。');activeReply.dataset.state='failed';controls();
 }catch(error){if(own===generation){failure(error);controls();}}
}
async function generate(){
 if(task&&!pending)return;if(!pending)pending={id:crypto.randomUUID(),body:{text:lastSearch.query,mode:'knowledge',profile_usage:'none',knowledge_scope:scope(),consent:true,history_consent:false,...(nutritionChat?{use_saved_nutrition:Boolean($('knowledge-use-profile')?.checked)}:{}),...(analysisMode?{analysis_kind:document.querySelector('[data-analysis-kind][aria-pressed=true]').dataset.analysisKind}:{}),...(previousTask?{previous_task_id:previousTask}:{})}};
 const original=pending,own=generation;turnTasks.add(original.id);controls();
 try{const row=await api('/api/v1/coach/tasks',{method:'POST',commandId:original.id,body:original.body});if(disposed||own!==generation){api('/api/v1/coach/tasks/'+row.task_id+'/cancel',{method:'POST',body:{}}).catch(()=>{});return;}task=row.task_id;pending=null;watch(task,own);}
 catch(error){if(disposed||own!==generation){api('/api/v1/coach/tasks/'+original.id+'/cancel',{method:'POST',body:{}}).catch(()=>{});return;}if(error.unknown){task=original.id;watch(task,own);}else{pending=null;failure(error);renderAnswer(activeReply,'这次没能完成回答，请重试。');}}
 finally{controls();}
}
async function loadCatalog(){
 const initial=$('knowledge-catalog-data');
 if(library&&!initial){$('catalog-loading').hidden=false;library.setAttribute('aria-busy','true');$('knowledge-directory').hidden=true;$('knowledge-query-area').hidden=true;$('catalog-error').hidden=$('catalog-retry').hidden=$('catalog-return').hidden=true;}
 catalog=initial?JSON.parse(initial.textContent):await api('/api/v1/knowledge/catalog');initial?.remove();if(disposed)return;if(guide)renderGuideRail();
 if(library){
  $('catalog-loading').hidden=true;library.setAttribute('aria-busy','false');$('catalog-return').hidden=true;$('catalog-error').hidden=true;$('catalog-retry').hidden=true;$('knowledge-query-area').hidden=false;
  if(library.dataset.view==='actions'){$('knowledge-directory').hidden=false;$('knowledge-query-area').hidden=true;renderActions();}
  else{renderIntro('home');for(const button of document.querySelectorAll('[data-learn-scene]'))button.disabled=false;for(const button of document.querySelectorAll('[data-learn-scene]'))button.onclick=()=>{for(const other of document.querySelectorAll('[data-learn-scene]'))other.setAttribute('aria-selected',String(other===button));renderIntro(button.dataset.learnScene);};}
 }
 $('knowledge-examples').replaceChildren();for(const text of (nutritionChat?['食堂午餐怎么搭配？','训练前后怎么吃？','不喜欢鸡胸肉可以吃什么？']:['第一次深蹲怎么做？','弯举时手臂怎么放？','器械推胸怎么调座椅？'])){const b=el('button',text,'button ghost small');b.type='button';b.onclick=()=>{if(task||pending)return;$('knowledge-query').value=text;$('knowledge-query').focus();controls();};$('knowledge-examples').append(b);}
 const intent=library&&new URLSearchParams(location.search).get('action_id');if(intent&&catalog.actions.some(x=>x.action_id===intent)&&!modalActive)openKnowledgeDialog(intent,document.querySelector('[data-knowledge-open]'));
 if(nutritionChat){$('knowledge-dialog-title').textContent='AI 营养分析师';dialog.querySelector('.knowledge-chat-welcome h3').textContent='今天想怎么吃？';$('knowledge-query').placeholder='问吃什么、怎么搭配，或食材怎么换…';if(new URLSearchParams(location.search).get('view')==='ask'&&!modalActive)openKnowledgeDialog(null,document.querySelector('[data-knowledge-open]'));}
 controls();
}
function catalogFailure(error){if(privateError(error))return;if(library){$('catalog-loading').hidden=true;library.setAttribute('aria-busy','false');$('catalog-return').hidden=false;$('catalog-error').textContent=error.message;$('catalog-error').hidden=false;$('catalog-retry').hidden=false;}failure(error);}
function openKnowledgeDialog(action=null,opener=null){
 if(modalActive||document.querySelector('dialog[open]'))return;invalidate();analysisMode=Boolean(opener?.hasAttribute('data-nutrition-analysis'));selectedAction=action;modalOpener=opener||document.activeElement;savedScroll=scrollY;modalActive=true;
 const item=catalog?.actions.find(x=>x.action_id===action),name=nutritionChat?'饮食搭配':item?.name||opener?.dataset.actionName||'';
 $('knowledge-topic').textContent=name;$('knowledge-topic').hidden=!name;$('knowledge-query').value=opener?.dataset.foodPrompt||(nutritionChat?'':name?name+'怎么练？':'');
  $('nutrition-analysis-choice').hidden=!analysisMode;$('analysis-input-heading').hidden=!analysisMode;dialog.classList.toggle('knowledge-dialog--analysis',analysisMode);dialog.dataset.analysisState='empty';
 if(nutritionChat)$('knowledge-dialog-title').textContent='AI 营养分析师';
 if(nutritionChat){dialog.querySelector('.knowledge-chat-welcome h3').textContent=analysisMode?'下一顿，吃得更合适':'今天想怎么吃？';$('knowledge-query').placeholder=analysisMode?'例如：午餐吃了一碗米饭、两个鸡蛋和一杯奶茶。':'问吃什么、怎么搭配，或食材怎么换…';}
 if(nutritionChat){for(const button of document.querySelectorAll('[data-nutrition-mode]'))button.setAttribute('aria-selected',String((button.dataset.nutritionMode==='analysis')===analysisMode));if($('knowledge-use-profile'))$('knowledge-use-profile').checked=false;} dialog.showModal();dialog.scrollTop=0;document.documentElement.classList.add('knowledge-modal-open');releaseModalFocus=keepDialogFocus(dialog);$('knowledge-query').focus({preventScroll:true});if(nutritionChat)refreshNutritionContext();
 controls();
}
async function refreshNutritionContext(){
 const own=generation;contextLoading=true;const node=$('knowledge-profile-context');node.hidden=false;node.textContent='正在读取我的资料…';controls();
 try{const state=await api('/api/v1/nutrition/coach-context');if(!modalActive||own!==generation)return;node.textContent=state.has_training_profile||state.has_nutrition_profile?'已连接我的资料'+(state.calculated_items?' · '+state.calculated_items+' 项摄入参考':''):'暂无已存资料 · 可以先问怎么搭配';}
 catch(error){if(modalActive&&own===generation){node.textContent='资料暂时未读到';failure(error);}}
 finally{if(own===generation){contextLoading=false;controls();}}
}
function closeKnowledgeDialog(){
 if(!modalActive)return;modalActive=false;contextLoading=false;invalidate();$('knowledge-query').value='';$('knowledge-state').textContent='';$('knowledge-error').hidden=true;$('knowledge-settings').hidden=true;$('knowledge-run').hidden=true;selectedAction=null;
 releaseModalFocus?.();releaseModalFocus=null;dialog.close();document.documentElement.classList.remove('knowledge-modal-open');scrollTo({top:savedScroll,behavior:'instant'});if(modalOpener?.isConnected)modalOpener.focus({preventScroll:true});modalOpener=null;
}

function renderActions(page=1){
 const query=$('guide-action-filter').value.trim().toLocaleLowerCase(),category=document.querySelector('[data-guide-equipment][aria-pressed=true]')?.dataset.guideEquipment||'';
 const matched=(catalog.lessons||[]).filter(x=>(!category||x.category===category)&&(!query||[x.name,x.focus,x.category].some(value=>value.toLocaleLowerCase().includes(query))));
 const pages=Math.max(1,Math.ceil(matched.length/6));actionPage=Math.min(Math.max(1,page),pages);const host=$('guide-action-list');host.replaceChildren();
 for(const lesson of matched.slice((actionPage-1)*6,actionPage*6)){
  const card=el('a',undefined,'learn-lesson-card guide-action-card');card.href=lesson.url;card.dataset.nav='';const image=el('img');image.src=lesson.image;image.alt=lesson.image_alt;image.width=1536;image.height=1024;image.loading='lazy';card.append(image,el('h2',lesson.name),el('p',lesson.category+' · '+lesson.focus,'field-help'),el('span','看图学动作 →','text-link'));host.append(card);
 }
 if(!matched.length){const empty=el('div',undefined,'learn-directory-empty'),clear=el('button','清空搜索','button'),all=el('button','浏览全部动作','text-link');clear.type=all.type='button';clear.onclick=()=>{$('guide-action-filter').value='';renderActions();};all.onclick=()=>{$('guide-action-filter').value='';for(const button of document.querySelectorAll('[data-guide-equipment]'))button.setAttribute('aria-pressed',String(button.dataset.guideEquipment===''));renderActions();};empty.append(el('h2','没找到这个动作'),el('p','试试动作名称，或换个器械条件。','field-help'),clear,all);host.append(empty);}
 $('guide-action-prev').hidden=actionPage<=1;$('guide-action-next').hidden=actionPage>=pages;$('guide-action-page').textContent='共 '+matched.length+' 个动作 · '+actionPage+' / '+pages;
}
function renderIntro(scene='home'){
 const id=scene==='gym'?'machine_chest_press':'bodyweight_squat',lesson=catalog.lessons.find(x=>x.action_id===id),host=$('learn-intro');if(host.dataset.scene===scene&&host.children.length)return;host.dataset.scene=scene;host.replaceChildren();
 if(!lesson){host.append(el('p','动作暂时未读到，请稍后重新读取。','field-help'));return;}
 const visual=el('div',undefined,'learn-intro-visual');
 if(scene==='home'&&lesson.preview_frames?.length){
  for(const [index,item]of lesson.preview_frames.entries()){const figure=el('figure'),image=el('img');image.src=item.url;image.alt=item.alt;image.width=600;image.height=900;figure.append(image,el('figcaption',index===0?'准备':'下蹲'));visual.append(figure);}
 }else{const image=el('img');image.src=lesson.image;image.alt=lesson.image_alt;image.width=1536;image.height=1024;visual.classList.add('learn-intro-machine');visual.append(image);}
 const copy=el('div',undefined,'learn-intro-copy');copy.append(el('h2',lesson.name),el('p',lesson.category+' · '+lesson.level,'field-help'));
 const list=el('ol');for(const line of scene==='home'?['双脚站稳','屈髋屈膝下蹲','稳定起身']:['调好座椅，握住把手','平稳向前推','控制回程'])list.append(el('li',line));copy.append(list);
 const actions=el('div',undefined,'button-row'),read=el('a','看懂这个动作','button'),start=el('a','开始练习','button secondary');read.href=lesson.url;start.href=lesson.url+'/practice';read.dataset.nav=start.dataset.nav='';actions.append(read,start);const faq=el('a','常见问题 ＋','learn-intro-faq');faq.href=lesson.url+'?section=faq';faq.dataset.nav='';copy.append(faq,actions);host.append(visual,copy);
 const next=$('learn-next-lessons');next.replaceChildren();const ids=scene==='home'?['bodyweight_glute_bridge','forearm_plank']:['lat_pulldown','seated_cable_row'];
 for(const other of catalog.lessons.filter(x=>ids.includes(x.action_id)).slice(0,2)){const link=el('a',undefined,'learn-next-lesson'),image=el('img');link.href=other.url;link.dataset.nav='';image.src=other.image;image.alt=other.image_alt;image.width=600;image.height=400;link.append(image,el('strong',other.name),el('span','看图学动作 →','text-link'));next.append(link);}
}
function renderGuideRail(){
 if(!guide||!catalog)return;const query=$('guide-side-search').value.trim().toLowerCase(),category=$('guide-side-category').value,host=$('guide-side-actions');host.replaceChildren();
 for(const item of catalog.lessons.filter(x=>(!category||x.category===category)&&(!query||[x.name,x.category].some(value=>value.toLowerCase().includes(query))))){const link=el('a',item.name);link.href=item.url;link.dataset.nav='';if(item.action_id===guide.dataset.actionId)link.setAttribute('aria-current','page');host.append(link);}
 if(!host.children.length)host.append(el('p','没有匹配的动作','field-help'));
}

async function loadGuide(){
 const own=++generation,data=await api('/api/v1/actions/'+encodeURIComponent(guide.dataset.actionId)+'/guide');if(disposed||own!==generation)return;
 const content=$('action-guide-content');content.replaceChildren();content.setAttribute('aria-busy','false');
 if(data.tutorial){content.append(tutorialContent({...data.tutorial,name:data.action.name,action_id:data.action.id},{practice:data}));if(new URLSearchParams(location.search).get('section')==='faq'){const faq=content.querySelector('.learn-faq');if(faq)faq.open=true;}}
 else{
  const main=el('div');
  if(data.action.cover?.url){const image=el('img');image.src=data.action.cover.url;image.alt=data.action.name;image.className='guide-cover';main.append(image);}
  const methods=data.knowledge.chunks.filter(x=>x.source_type!=='project_rule');
  if(methods.length){main.append(el('h2','动作要点'));const list=el('ol',undefined,'learn-answer-steps');for(const row of methods)list.append(el('li',row.text));main.append(list);}
  else main.append(el('p','这个动作的分步教学还在整理，可以先看看拍摄位置和动作分析。','field-help'));
  const aside=el('aside',undefined,'learn-aside'),actions=el('div',undefined,'button-row learn-practice-actions');
  for(const [key,label]of [['realtime','开始实时分析'],['video','上传训练视频']]){const a=el('a',label,key==='realtime'?'button':'button secondary');a.href=data.links[key];a.dataset.nav='';actions.append(a);}
  const ask=el('button',undefined,'button secondary learn-ask');ask.type='button';ask.append(learningIcon('AiMagic'),document.createTextNode('问 AI 教练'));ask.dataset.actionName=data.action.name;ask.onclick=()=>openKnowledgeDialog(data.action.id,ask);aside.append(actions,ask,framingNotes(data.view_guidance.steps||[]));
  const layout=el('div',undefined,'learn-detail-layout');layout.append(main,aside);content.append(layout);
 }

}

if(root){
 $('knowledge-form').onsubmit=e=>search(e).catch(failure);
 $('knowledge-query').addEventListener('input',controls);$('knowledge-query').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!$('knowledge-search').disabled)$('knowledge-form').requestSubmit();}});
 $('knowledge-clear').onclick=()=>{invalidate();$('knowledge-query').value='';$('knowledge-state').textContent='';$('knowledge-query').focus();if(nutritionChat)refreshNutritionContext();controls();};
 $('knowledge-stop').onclick=()=>{generation++;searching=false;const id=task||pending?.id;task=pending=null;clearTimeout(timer);if(id)api('/api/v1/coach/tasks/'+id+'/cancel',{method:'POST',body:{}}).catch(failure);if(activeReply)renderAnswer(activeReply,'已停止回答');$('knowledge-state').textContent='已停止本次回答';controls();};
 $('knowledge-copy').onclick=async()=>{if(lastResult)await navigator.clipboard.writeText(lastResult.summary);};$('knowledge-followup').onclick=()=>$('knowledge-query').focus();$('knowledge-generate').onclick=()=>generate().catch(failure);
 $('knowledge-retry').onclick=()=>{if(pending)return generate().catch(failure);if(task)return watch(task,generation);if(lastSearch){$('knowledge-query').value=lastSearch.query;search().catch(failure);}else loadCatalog().catch(catalogFailure);};
 $('knowledge-clarification').onsubmit=async event=>{event.preventDefault();if(!task)return;const id=task,own=generation;try{await api('/api/v1/coach/tasks/'+id+'/answer',{method:'POST',body:{answer:$('knowledge-answer-input').value.trim()}});if(own===generation){$('knowledge-clarification').hidden=true;watch(id,own);}}catch(error){failure(error);}};
 if(library){for(const button of document.querySelectorAll('[data-guide-equipment]'))button.onclick=()=>{for(const other of document.querySelectorAll('[data-guide-equipment]'))other.setAttribute('aria-pressed',String(other===button));renderActions();};$('guide-action-filter').oninput=()=>catalog&&renderActions();$('guide-action-prev').onclick=()=>renderActions(actionPage-1);$('guide-action-next').onclick=()=>renderActions(actionPage+1);$('catalog-retry').onclick=()=>loadCatalog().catch(catalogFailure);}loadCatalog().catch(catalogFailure);
}
function guideFailure(error){if(privateError(error))return;$('guide-error').textContent=error.message;$('guide-error').hidden=false;$('guide-retry').hidden=false;}
if(guide){$('guide-retry').onclick=()=>loadGuide().catch(guideFailure);loadGuide().catch(guideFailure);}
window.addEventListener('pagehide',()=>{disposed=true;generation++;clearTimeout(timer);for(const id of new Set([...turnTasks,task,pending?.id].filter(Boolean)))api('/api/v1/coach/tasks/'+id+'/cancel',{method:'POST',body:{}}).catch(()=>{});turnTasks.clear();task=pending=lastResult=previousTask=null;});
window.addEventListener('fitness-auth-revoked',()=>{closeKnowledgeDialog();disposed=true;generation++;clearTimeout(timer);task=pending=lastResult=null;root?.replaceChildren();guide?.replaceChildren();});

window.addEventListener('pageshow',e=>{if(e.persisted){disposed=false;task=pending=null;if(root){invalidate();$('knowledge-run').hidden=true;loadCatalog().catch(catalogFailure);if(guide)loadGuide().catch(guideFailure);}}});

async function validateDisplayedAnswer(){if(disposed||document.hidden||validating||task||pending||!displayedTask)return;validating=true;const own=generation;try{for(const id of turnTasks){const row=await api('/api/v1/coach/tasks/'+id);if(own!==generation)return;if(row.status==='source_invalid'||row.status==='expired'){invalidate();$('knowledge-state').textContent='内容已变化，请重新提问';break;}}}catch(error){if(own===generation){invalidate();failure(error);}}finally{validating=false;}}
window.addEventListener('focus',validateDisplayedAnswer);document.addEventListener('visibilitychange',validateDisplayedAnswer);
if(dialog){
 for(const button of document.querySelectorAll('[data-knowledge-open],[data-food-prompt],[data-nutrition-analysis]'))button.onclick=()=>openKnowledgeDialog(button.dataset.actionId||null,button);
 $('knowledge-dialog-close').onclick=closeKnowledgeDialog;
 dialog.addEventListener('cancel',event=>{event.preventDefault();closeKnowledgeDialog();});
 dialog.addEventListener('close',()=>{if(modalActive)closeKnowledgeDialog();});
 dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const b=dialog.getBoundingClientRect();if(event.clientX<b.left||event.clientX>b.right||event.clientY<b.top||event.clientY>b.bottom)closeKnowledgeDialog();});
}

for(const button of document.querySelectorAll('[data-analysis-kind]'))button.onclick=()=>{if(button.getAttribute('aria-pressed')==='true')return;const original=lastSearch?.query||$('knowledge-query').value;invalidate();$('knowledge-query').value=original;for(const b of document.querySelectorAll('[data-analysis-kind]'))b.setAttribute('aria-pressed',String(b===button));controls();};
function foodPhoto(file,alt){const image=el('img');image.src='/static/food/'+file+'.webp';image.alt=alt;image.width=1200;image.height=800;image.decoding='async';return image;}
function referenceRange(item){
 const card=el('article',undefined,'nutrition-range-card'),label=item.item==='protein'?'每日蛋白质':'每日维持热量',unit=item.item==='protein'?'克':'千卡',low=Number(item.display_lower),high=Number(item.display_upper);
 if(!Number.isFinite(low)||!Number.isFinite(high)||low<0||high<=low)return null;
 card.dataset.referenceItem=item.item;card.append(el('span',label,'nutrition-range-label'));
 const value=el('p',undefined,'nutrition-range-value');value.append(el('strong',low+'—'+high),el('span',unit));card.append(value);
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 240 34');svg.setAttribute('role','img');svg.setAttribute('aria-label',label+'参考区间 '+low+'至'+high+unit+'，非已摄入量');
 const axisMax=Math.ceil(high*1.2/10)*10,start=10+220*low/axisMax,end=10+220*high/axisMax;
 for(const [x1,x2,cls]of [[10,230,'range-track'],[start,end,'range-band']]){const line=document.createElementNS(ns,'line');for(const [k,v]of Object.entries({x1,x2,y1:14,y2:14}))line.setAttribute(k,String(v));line.setAttribute('class',cls);svg.append(line);}
 for(const x of [start,end]){const dot=document.createElementNS(ns,'circle');dot.setAttribute('cx',x);dot.setAttribute('cy','14');dot.setAttribute('r','3');dot.setAttribute('class','range-dot');svg.append(dot);}
 const baseline=el('div',undefined,'nutrition-range-axis');baseline.append(el('span','0'),el('span',axisMax+' '+unit));card.append(svg,baseline);return card;
}
function renderNutritionAnalysis(message,analysis,kind,references=[]){
 const block=el('section',undefined,'nutrition-analysis-result'),header=el('div',undefined,'nutrition-report-heading');header.append(el('span',kind==='day'?'今日饮食':'这一餐','analysis-section-label'),el('h3','你的饮食建议'));message.querySelector('.knowledge-message-content').before(header);
 const foods=el('section',undefined,'nutrition-meal-recap');foods.append(el('h4','这次吃了什么'));
 const table=el('table',undefined,'nutrition-food-table'),head=el('thead'),headrow=el('tr');for(const text of ['食物','分量']){const th=el('th',text);th.scope='col';headrow.append(th);}head.append(headrow);table.append(head);const body=el('tbody');
 for(const food of analysis.foods){const row=el('tr'),name=el('td'),amount=el('td',food.amount||'—');const picture=foodPicture(resolveFoodAsset(food.name,food.group),{lazy:false});
 const visual=el('span',undefined,'nutrition-food-thumb');if(picture)visual.append(picture);else visual.append(learningIcon('Apple01'));name.append(visual,el('span',food.name));row.append(name,amount);body.append(row);}
 table.append(body);foods.append(table);block.append(foods);
 const next=el('section',undefined,'nutrition-next-meal'),visual=el('figure',undefined,'nutrition-next-photo');visual.append(foodPicture(resolveFoodAsset(analysis.next_meal),{lazy:false})||foodPhoto('balanced-meal','下一餐食物搭配示意'));visual.append(el('figcaption','搭配示意'));const copy=el('div',undefined,'nutrition-next-copy');copy.append(el('span','下一餐这样搭','nutrition-result-label'),el('p',analysis.next_meal));next.append(visual,copy);block.append(next);
 if(analysis.swaps.length){const swaps=el('div',undefined,'nutrition-food-swaps');swaps.append(el('h4','换个选择，更好坚持'));for(const item of analysis.swaps){const row=el('div',undefined,'nutrition-swap-row nutrition-swap-row--photos'),pictures=el('div',undefined,'nutrition-swap-visual'),copy=el('div');for(const name of [item.from,item.to]){const image=foodPicture(resolveFoodAsset(name),{lazy:false});if(image)pictures.append(image);}if(pictures.childElementCount)row.append(pictures);else row.classList.remove('nutrition-swap-row--photos');copy.append(el('strong',item.from+' → '+item.to),el('p',item.reason));row.append(copy);swaps.append(row);}block.append(swaps);}
 const calculated=references.filter(x=>x.status==='calculated');if(calculated.length){const section=el('section',undefined,'nutrition-personal-ranges'),heading=el('div',undefined,'nutrition-ranges-heading');heading.append(el('h4','你的每日摄入参考'),el('span','来自我的资料'));section.append(heading);const grid=el('div',undefined,'nutrition-ranges-grid');for(const item of calculated){const card=referenceRange(item);if(card)grid.append(card);}section.append(grid);block.append(section);}
 message.querySelector('.knowledge-message-content').after(block);
}

const nutritionDrafts={plan:'',analysis:''};
for(const button of document.querySelectorAll('[data-nutrition-mode]'))button.onclick=()=>{
 const next=button.dataset.nutritionMode==='analysis';if(next===analysisMode)return;
 nutritionDrafts[analysisMode?'analysis':'plan']=$('knowledge-query').value;invalidate();analysisMode=next;
 $('nutrition-analysis-choice').hidden=!next;$('analysis-input-heading').hidden=!next;dialog.classList.toggle('knowledge-dialog--analysis',next);dialog.dataset.analysisState='empty';
 $('knowledge-query').value=nutritionDrafts[next?'analysis':'plan'];$('knowledge-query').placeholder=next?'写下实际吃过的食物和分量…':'问吃什么、怎么搭配，或食材怎么换…';
 dialog.querySelector('.knowledge-chat-welcome h3').textContent=next?'把吃过的食物写下来':'今天想怎么吃？';
 for(const other of document.querySelectorAll('[data-nutrition-mode]'))other.setAttribute('aria-selected',String(other===button));
 for(const range of document.querySelectorAll('[data-analysis-kind]'))range.setAttribute('aria-pressed',String(range.dataset.analysisKind==='meal'));
 controls();$('knowledge-query').focus();
};
window.addEventListener('fitness-meal-adopted',closeKnowledgeDialog);

if(guide){$('guide-side-search').oninput=$('guide-side-category').onchange=renderGuideRail;}

window.addEventListener('fitness-auth-revoked',()=>{nutritionDrafts.plan=nutritionDrafts.analysis='';});
