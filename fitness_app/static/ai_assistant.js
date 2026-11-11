import {api,keepDialogFocus,confirmDialog,beforeLeave} from './app.js';
const $=id=>document.getElementById(id);
const panel=$('ai-assistant'),input=$('ai-chat-input'),form=$('ai-chat-form');
const pageMode=panel.dataset.mode==='page';
const content=panel.querySelector('.ai-assistant-content'),messages=$('ai-chat-messages');
let conversation=null,creation=null,contextKey='',busy=false,opened=false,request=null,pending=null;
let disposed=false,timer=null,releaseFocus=null,opener=null,inertNodes=[],polling=false,needsCheck=false;
let streamNode=null,streamFrame=null,streamTarget='';
const frameChoices=new Map();
let frameChoiceKey='';

function context(){
  const coach=document.querySelector('.coach-main[data-record-id]');
  if(coach?.dataset.recordId)return {context_kind:'record',context_id:coach.dataset.recordId,history_scope:'related'};
  const live=document.querySelector('#live-workspace');
  if(live?.dataset.previewId)return {context_kind:'live',context_id:live.dataset.previewId,history_scope:'related'};
  const video=document.querySelector('#video-page[data-ai-record-id], #video-auto-page[data-ai-record-id]');if(video)return {context_kind:'record',context_id:video.dataset.aiRecordId,history_scope:'related'};
  if(document.querySelector('#video-auto-page[data-ai-segment-id]'))return {context_kind:'general',history_scope:'none'};
  const record=document.querySelector('.record-detail[data-session-id]');
  if(record)return {context_kind:'record',context_id:record.dataset.sessionId,history_scope:'related'};
  return {context_kind:['records','overview'].includes(document.body.dataset.page)?'overview':'general',history_scope:'all'};
}
function fingerprint(){return JSON.stringify(context())+'|'+(document.querySelector('#live-workspace')?.dataset.aiSegment||'')+'|'+(document.querySelector('#video-auto-page')?.dataset.aiSegmentId||'');}
function pictureSource(){return document.querySelector('#live-source, #camera-source, #video-player, #record-media video');}
async function capturePictures(text=''){
  const video=pictureSource();
  if(!video||video.readyState<2||video.seeking||!video.videoWidth||!video.videoHeight)throw new Error('请先开启摄像头或加载视频，再附上当前画面。');
  const canvas=document.createElement('canvas'),scale=Math.min(1,640/Math.max(video.videoWidth,video.videoHeight));
  canvas.width=Math.max(1,Math.round(video.videoWidth*scale));canvas.height=Math.max(1,Math.round(video.videoHeight*scale));
  const paint=canvas.getContext('2d'),frames=[];
  const isLive=['live-source','camera-source'].includes(video.id),source=video.srcObject||video.currentSrc;
  const binding=fingerprint();let decoder=null;
  const checkSource=()=>{if(disposed||!opened||fingerprint()!==binding||pictureSource()!==video||(video.srcObject||video.currentSrc)!==source||video.readyState<2||video.seeking)throw new Error('画面已切换，请重新发送。');};
  const save=(media,stamp)=>{checkSource();paint.drawImage(media,0,0,canvas.width,canvas.height);
    const data_url=canvas.toDataURL('image/jpeg',.72);
    if(data_url.length>273090)throw new Error('画面过大，请调整取景后重试。');
    frames.push({data_url,source_ms:Math.round(stamp*1000)});};
  function waitMedia(media,event,action){
    return new Promise((resolve,reject)=>{let timeout;
      const clear=()=>{clearTimeout(timeout);media.removeEventListener(event,ready);media.removeEventListener('error',failed);};
      const ready=()=>{clear();resolve();},failed=()=>{clear();reject(new Error('视频画面读取失败，请重新加载原片。'));};
      media.addEventListener(event,ready,{once:true});media.addEventListener('error',failed,{once:true});
      timeout=setTimeout(()=>{clear();reject(new Error('读取画面超时，请重试。'));},8000);
      try{action();}catch(error){clear();reject(error);}
    });
  }
  try{
    if(isLive||/当前|现在|这一帧|这帧|此刻/.test(text)){
      if(!isLive&&(video.currentTime<Number(video.dataset.aiRangeStartMs||0)/1000||
          video.dataset.aiRangeEndMs&&video.currentTime>=Number(video.dataset.aiRangeEndMs)/1000))
        throw new Error('当前画面已离开所选片段，请先定位该片段再提问。');
      for(let i=0;i<(isLive?3:1);i++){if(i)await new Promise(resolve=>setTimeout(resolve,350));save(video,video.currentTime);}
    }else{
      // 独立解码同一来源，读取所选动作区间，不在抽样时拖动主播放器。
      decoder=document.createElement('video');decoder.muted=true;decoder.preload='auto';decoder.playsInline=true;
      await waitMedia(decoder,'loadeddata',()=>{decoder.src=source;decoder.load();});checkSource();
      const start=Number(video.dataset.aiRangeStartMs||0)/1000;
      const end=Math.min(decoder.duration,video.dataset.aiRangeEndMs?Number(video.dataset.aiRangeEndMs)/1000:decoder.duration);
      if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start)throw new Error('所选视频片段已改变，请重新打开解读。');
      const important=JSON.parse(video.dataset.aiReviewTimesMs||'[]').filter(t=>Number.isFinite(t)&&t/1000>=start&&t/1000<end).map(t=>t/1000);
      const center=(start+end)/2,half=Math.min(1,(end-start)/2);
      const count=Math.max(1,Math.min(6,Math.floor(Number(conversation?.image_max_frames)||3)));
      const samples=important.length?Array.from({length:Math.min(count,important.length)},(_,i)=>important[Math.round(i*(important.length-1)/Math.max(1,Math.min(count,important.length)-1))]):
        Array.from({length:count},(_,i)=>center+half*(2*(i+.5)/count-1));
      for(const stamp of [...new Set(samples.map(t=>Math.min(end-.01,t)))].sort((a,b)=>a-b)){
        await waitMedia(decoder,'seeked',()=>{decoder.currentTime=stamp;});save(decoder,decoder.currentTime);
      }
    }
    return frames;
  }finally{if(decoder){decoder.pause();decoder.removeAttribute('src');decoder.load();}canvas.width=canvas.height=0;}
}
function state(text=''){$('ai-request-state').textContent=text;}
function fail(error){
  $('ai-chat-error').textContent=error.message||'暂时无法连接，请稍后重试。';
  $('ai-chat-error').hidden=false;
  if(error.status===401)close();
}
function nearBottom(){return content.scrollHeight-content.scrollTop-content.clientHeight<72;}
function follow(wasNear){if(wasNear&&opened)content.scrollTop=content.scrollHeight;}
function resizeInput(){input.style.height='auto';input.style.height=Math.min(156,Math.max(64,input.scrollHeight))+'px';}
function controls(){
  $('ai-chat-send').textContent=needsCheck?'核对请求':'发送';
  $('ai-chat-send').disabled=busy&&!needsCheck||!needsCheck&&!input.value.trim()||Boolean(pictureSource()?.seeking&&!pictureSource()?.error);
  $('ai-chat-stop').hidden=!request||!busy||needsCheck;
  $('ai-chat-new').disabled=busy;
  $('ai-assistant-refresh').disabled=busy;
  panel.dataset.busy=String(busy);
  const source=pictureSource(),frameReady=Boolean(source&&source.readyState>=2&&source.videoWidth&&source.videoHeight),imageSupported=conversation?.image_input_supported!==false;
  $('ai-send-frame-label').hidden=!source;
  $('ai-send-frame-label').title=!imageSupported?'当前模型仅支持训练摘要；查看画面请在设置中选择快速解读':frameReady?'勾选后，本次发送会附带当前画面':'开启摄像头或加载视频后，才能附带当前画面';
  $('ai-send-frame').disabled=busy||!frameReady||!imageSupported;
  if(frameReady&&imageSupported){
    const live=['live-source','camera-source'].includes(source.id);
    frameChoiceKey=fingerprint()+'|'+(source.srcObject?.id||source.currentSrc);
    if(!frameChoices.has(frameChoiceKey))frameChoices.set(frameChoiceKey,['record','live'].includes(context().context_kind)||Boolean(document.querySelector('#video-auto-page[data-ai-segment-id]')));
    $('ai-send-frame').checked=frameChoices.get(frameChoiceKey);
    $('ai-frame-label').textContent=live?'当前画面':'视频画面';
    $('ai-frame-hint').hidden=!$('ai-send-frame').checked;
  }else{frameChoiceKey='';$('ai-send-frame').checked=false;$('ai-frame-hint').hidden=true;}
  for(const button of document.querySelectorAll('[data-ai-new]'))button.disabled=busy;
}
function setBusy(value){busy=value;controls();}
function addAuthor(node){
  if(!pageMode)return;
  const author=document.createElement('div');author.className='ai-message-author';
  const icon=document.createElement('img');icon.src='/static/round4/'+encodeURIComponent('知形标志.svg');icon.alt='';icon.width=24;icon.height=24;
  const label=document.createElement('span');label.textContent='知练同行 AI教练';author.append(icon,label);node.append(author);
}
function message(role,text){
  const stick=nearBottom();
  $('ai-chat-welcome').hidden=true;
  const node=document.createElement('div'),p=document.createElement('p');
  node.className='ai-chat-message '+role;p.textContent=text;if(role==='assistant')addAuthor(node);node.append(p);messages.append(node);
  follow(stick||role==='user');return node;
}
function stream(text=''){
  if(!streamNode){
    streamNode=message('assistant','');
    streamNode.dataset.streaming='true';streamNode.setAttribute('aria-live','off');
    const dots=document.createElement('span');dots.className='ai-typing';dots.setAttribute('aria-label','正在回复');
    for(let i=0;i<3;i++)dots.append(document.createElement('i'));
    streamNode.append(dots);
  }
  streamTarget=text;
  if(streamFrame!==null)return;
  streamFrame=requestAnimationFrame(()=>{
    streamFrame=null;if(!streamNode)return;
    const stick=nearBottom();
    if(streamTarget){streamNode.querySelector('.ai-typing')?.remove();streamNode.querySelector('p').textContent=streamTarget;}
    follow(stick);
  });
}
function discardStream(){
  if(streamFrame!==null)cancelAnimationFrame(streamFrame);
  streamFrame=null;streamTarget='';streamNode?.remove();streamNode=null;
}
function renderResult(result,mediaBinding=null){
  if(streamFrame!==null)cancelAnimationFrame(streamFrame);
  streamFrame=null;streamTarget='';
  const stick=nearBottom(),node=streamNode||message('assistant','');
  streamNode=null;node.replaceChildren();addAuthor(node);delete node.dataset.streaming;node.removeAttribute('aria-live');
  const summary=document.createElement('p');summary.textContent=result.summary;node.append(summary);
  const copyText=[result.summary];
  for(const item of result.explanations||[]){
    const card=document.createElement('section');card.className='ai-evidence-card';
    const title=document.createElement('strong');title.textContent=item.evidence?.message||item.evidence?.title||'动作建议';
    const body=document.createElement('p');body.textContent=item.explanation;
    const tip=document.createElement('p');tip.textContent=item.adjustment;
    card.append(title,body,tip);node.append(card);copyText.push(title.textContent,item.explanation,item.adjustment);
  }
  if(result.visual_review){
    const review=result.visual_review,block=document.createElement('section');block.className='ai-visual-review';
    const label=document.createElement('p');label.className='field-help';label.textContent='AI画面复核 · 仅本次抽样';block.append(label);
    const checks=review.findings||[],issues=checks.filter(item=>item.status==='observed'),other=checks.filter(item=>item.status!=='observed');
    let otherBox=null;
    if(other.length){otherBox=document.createElement('details');const heading=document.createElement('summary');heading.textContent='其他检查（'+other.length+'）';otherBox.append(heading);}
    for(const item of [...issues,...other]){
      const card=document.createElement('section');card.className='ai-evidence-card';card.dataset.visualFinding=item.code;
      const title=document.createElement('strong');title.textContent=item.status==='observed'?item.title:item.status==='not_observed'?'抽样中未见：'+item.title:'暂无法判断：'+item.title;
      const observation=document.createElement('p');observation.textContent=item.observation;card.append(title,observation);
      copyText.push(title.textContent,item.observation);
      if(item.suggestion){const tip=document.createElement('p');tip.textContent=item.suggestion;card.append(tip);copyText.push(item.suggestion);}
      const source=pictureSource(),live=source&&['live-source','camera-source'].includes(source.id);
      if(source&&!live&&item.samples?.length&&mediaBinding){
        const stamp=item.samples[0].source_ms,binding=mediaBinding.fingerprint,media=mediaBinding.source;
        const locate=document.createElement('button');locate.type='button';locate.className='button ghost small';
        locate.textContent='查看抽样画面';locate.onclick=()=>{
          if(fingerprint()!==binding||pictureSource()!==source||source.currentSrc!==media){state('视频已切换，请重新复核');return;}
          const start=Number(source.dataset.aiRangeStartMs||0),end=Number(source.dataset.aiRangeEndMs||source.duration*1000);
          if(!Number.isFinite(stamp)||stamp<start||stamp>=end){state('抽样画面已离开所选片段');return;}
          source.pause();source.currentTime=stamp/1000;source.scrollIntoView({block:'center',behavior:'smooth'});
          if(matchMedia('(max-width:767px)').matches)close();
        };card.append(locate);
      }
      if(item.status==='observed')block.append(card);else otherBox.append(card);
    }
    if(otherBox)block.append(otherBox);
    if(!(review.findings||[]).length){const empty=document.createElement('p');empty.className='field-help';empty.textContent='本次未形成可定位的问题项';block.append(empty);}
    node.append(block);
  }
  if(result.next_focus?.length){
    const list=document.createElement('ul');list.className='ai-next-focus';
    for(const item of result.next_focus){const li=document.createElement('li');li.textContent=item.text;list.append(li);copyText.push(item.text);}
    node.append(list);
  }
  const copy=document.createElement('button');copy.type='button';copy.className='ai-copy-reply';copy.textContent='复制';copy.setAttribute('aria-label','复制这条回复');
  copy.onclick=async()=>{try{await navigator.clipboard.writeText(copyText.join('\n'));copy.textContent='已复制';setTimeout(()=>{copy.textContent='复制';},1500);}catch{state('请选中文字复制');}};
  node.append(copy);follow(stick);
}
async function create(){
  if(creation)return creation;
  const body=context(),key=fingerprint();
  creation=api('/api/v1/ai/conversations',{method:'POST',commandId:crypto.randomUUID(),body})
    .then(value=>{conversation=value;contextKey=key;$('ai-assistant-refresh').hidden=true;controls();return value;})
    .finally(()=>{creation=null;});
  return creation;
}
function modalMode(){
  for(const [node,previous]of inertNodes)node.inert=previous;
  inertNodes=[];releaseFocus?.();releaseFocus=null;
  if(pageMode){panel.setAttribute('role','region');panel.removeAttribute('aria-modal');document.documentElement.classList.remove('ai-modal-open');return;}
  if(!opened)return;
  if(matchMedia('(max-width:767px)').matches){
    panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','true');
    for(const node of document.body.children){if(node!==panel&&node.tagName!=='SCRIPT'){inertNodes.push([node,node.inert]);node.inert=true;}}
    releaseFocus=keepDialogFocus(panel);document.documentElement.classList.add('ai-modal-open');
  }else{panel.setAttribute('role','complementary');panel.removeAttribute('aria-modal');document.documentElement.classList.remove('ai-modal-open');}
}
async function open(event){
  opener=event?.currentTarget||document.activeElement;
  if(document.fullscreenElement)try{await document.exitFullscreen();}catch{}
  opened=true;panel.hidden=false;document.body.classList.add('ai-assistant-open');modalMode();
  const source=pictureSource();source?.addEventListener('loadeddata',controls);source?.addEventListener('emptied',controls);source?.addEventListener('seeked',controls);source?.addEventListener('error',controls);controls();
  input.focus({preventScroll:true});resizeInput();
  try{
    if(!conversation)await create();
    else if(contextKey!==fingerprint()&&!busy){
      messages.replaceChildren();$('ai-chat-welcome').hidden=false;await newConversation({preserveDraft:true});
    }
  }catch(error){fail(error);}
}
function close(){
  if(pageMode)return;
  opened=false;panel.hidden=true;document.body.classList.remove('ai-assistant-open');
  document.documentElement.classList.remove('ai-modal-open');modalMode();opener?.focus({preventScroll:true});
}
async function newConversation(options={}){
  if(busy)return false;
  const previousDraft=input.value;
  setBusy(true);$('ai-chat-error').hidden=true;state('正在准备对话…');
  try{
    if(conversation)try{await api('/api/v1/ai/conversations/'+conversation.conversation_id,{method:'DELETE'});}catch(error){if(error.status!==404)throw error;}
    conversation=null;contextKey='';await create();discardStream();messages.replaceChildren();
    if(options?.preserveDraft!==true&&input.value===previousDraft)input.value='';
    resizeInput();$('ai-chat-welcome').hidden=false;state();return true;
  }catch(error){state();fail(error);return false;}finally{setBusy(false);}
}
async function poll(){
  if(disposed||!request||polling)return;
  clearTimeout(timer);polling=true;const rid=request.request_id;
  try{
    const row=await api('/api/v1/ai/requests/'+rid);
    if(disposed||!request||request.request_id!==rid)return;
    request={...request,...row};needsCheck=false;
    if(['queued','running','cancelling'].includes(row.status)){
      busy=true;
      if(row.context_current===false){discardStream();state('训练依据已更新，请更新摘要');$('ai-assistant-refresh').hidden=false;}
      else if(row.status==='running'){stream(row.stream_text||'');state(row.stream_text?'正在回复…':'正在思考…');}
      else state(row.status==='queued'?'等待回复…':'正在停止…');
      controls();timer=setTimeout(poll,row.stream_text?220:450);return;
    }
    if(pending&&!pending.displayed){message('user',pending.text);pending.displayed=true;}
    if(row.status==='succeeded'&&row.result){
      renderResult(row.result,pending?.mediaBinding);state();
      if(!row.result.facts?.visual_input&&row.live_context?.matches_current_segment===false){state('训练已变化，可更新摘要');$('ai-assistant-refresh').hidden=false;}
    }else{
      discardStream();
      if(row.context_current===false){state('训练依据已更新，请更新摘要');$('ai-assistant-refresh').hidden=false;}
      else if(row.status==='cancelled')state('已停止');
      else {state();fail(new Error(row.message||'本次未能完成回复，请重试。'));}
    }
    pending=null;request=null;setBusy(false);
  }catch(error){
    fail(error);needsCheck=true;state('连接中断，可核对原请求');controls();
  }finally{polling=false;}
}
form.onsubmit=async event=>{
  event.preventDefault();
  if(needsCheck&&request){poll();return;}
  if(busy)return;
  const text=input.value.trim();if(!text)return;
  if(!$('ai-send-consent').checked){fail(new Error('请勾选训练摘要后发送。'));return;}
  setBusy(true);$('ai-chat-error').hidden=true;state('正在发送…');
  try{
    if(!conversation)await create();
    if(context().context_kind!=='live'&&contextKey!==fingerprint()){
      $('ai-assistant-refresh').hidden=false;throw new Error('所选训练已变化，请更新训练摘要后再发送。');
    }
    const frameConsent=$('ai-send-frame').checked;
    if(frameConsent)state('正在读取画面…');
    const frames=frameConsent?await capturePictures(text):null;
    const source=pictureSource(),mediaBinding=frames&&source?{fingerprint:fingerprint(),source:source.currentSrc}:null;
    pending={id:crypto.randomUUID(),text,body:{text,consent:true,expected_context_revision:conversation.context_revision,...(frames?{include_current_frame:true,frames}:{})},displayed:false,mediaBinding};
    request=await api('/api/v1/ai/conversations/'+conversation.conversation_id+'/messages',{method:'POST',commandId:pending.id,body:pending.body});
    message('user',pending.text);pending.displayed=true;
    if(input.value.trim()===text){input.value='';resizeInput();}
    stream();controls();poll();
  }catch(error){
    if(error.code==='AI_EPHEMERAL_COACH_REQUIRED'){
      window.dispatchEvent(new CustomEvent('fitness-open-coach',{detail:{text}}));input.value='';resizeInput();state('请在AI教练中确认本次发送');pending=null;request=null;
    }else if(error.code==='AI_TOPIC_OUT_OF_SCOPE'){
      message('user',text);message('assistant',error.message);
      if(input.value.trim()===text){input.value='';resizeInput();}
      state();pending=null;request=null;
    }else{
      if(error.code==='AI_CONTEXT_CHANGED')$('ai-assistant-refresh').hidden=false;
      fail(error);
      if(error.unknown&&pending){request={request_id:pending.id};needsCheck=true;state('发送结果待核对');}
      else{
        pending=null;
        if(error.status===409){conversation=null;contextKey='';$('ai-assistant-refresh').hidden=false;}
        if(error.status===404){conversation=null;contextKey='';state('对话已过期，可重新发送');}
      }
    }
    setBusy(false);
  }
};
input.addEventListener('input',()=>{resizeInput();controls();});
$('ai-send-frame').onchange=()=>{if(frameChoiceKey)frameChoices.set(frameChoiceKey,$('ai-send-frame').checked);$('ai-frame-hint').hidden=!$('ai-send-frame').checked;};
input.addEventListener('keydown',event=>{
  if(event.key!=='Enter'||event.shiftKey||event.isComposing||event.keyCode===229)return;
  event.preventDefault();if(!busy||needsCheck)form.requestSubmit();
});
$('ai-chat-stop').onclick=async()=>{
  if(!request)return;
  try{
    const row=await api('/api/v1/ai/requests/'+request.request_id);
    request=await api('/api/v1/ai/requests/'+request.request_id+'/cancel',{method:'POST',body:{expected_version:row.state_version}});
    discardStream();state('正在停止…');poll();
  }catch(error){fail(error);}
};
$('ai-chat-new').onclick=newConversation;
for(const button of document.querySelectorAll('[data-ai-new]'))button.onclick=newConversation;
$('ai-assistant-refresh').onclick=()=>newConversation({preserveDraft:true});
$('ai-assistant-close').onclick=close;
for(const button of document.querySelectorAll('[data-ai-open]'))button.onclick=open;
window.addEventListener('fitness-open-ai',event=>open({currentTarget:event.detail?.opener}));
for(const button of document.querySelectorAll('[data-ai-prompt]'))button.onclick=()=>{input.value=button.dataset.aiPrompt;resizeInput();controls();input.focus({preventScroll:true});};
panel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close();}});
window.addEventListener('resize',modalMode);
function refreshViewport(){
  const viewport=window.visualViewport;if(!viewport||disposed)return;
  panel.style.setProperty('--ai-viewport-height',viewport.height+'px');panel.style.setProperty('--ai-viewport-top',viewport.offsetTop+'px');
  const editing=panel.contains(document.activeElement)&&['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName);
  const gap=editing&&viewport.scale<=1.05?Math.max(0,innerHeight-viewport.height-viewport.offsetTop):0;
  const inset=pageMode&&gap>100?gap:0;panel.style.setProperty('--ai-keyboard-inset',inset+'px');panel.dataset.keyboardOpen=String(inset>0);
  if(inset)requestAnimationFrame(()=>{if(disposed||!panel.contains(document.activeElement))return;const overflow=form.getBoundingClientRect().bottom-(viewport.offsetTop+viewport.height-8);if(overflow>0)window.scrollBy({top:overflow,behavior:'auto'});});
}
window.visualViewport?.addEventListener('resize',refreshViewport);
panel.addEventListener('focusin',refreshViewport);panel.addEventListener('focusout',()=>requestAnimationFrame(refreshViewport));
window.addEventListener('fitness-auth-revoked',()=>{
  conversation=request=pending=null;needsCheck=false;clearTimeout(timer);discardStream();messages.replaceChildren();
  input.value='';setBusy(false);close();
  $('ai-send-frame').checked=false;$('ai-frame-hint').hidden=true;
  frameChoices.clear();frameChoiceKey='';
});
window.addEventListener('pagehide',()=>{disposed=true;clearTimeout(timer);if(streamFrame!==null)cancelAnimationFrame(streamFrame);streamFrame=null;});
window.addEventListener('pageshow',event=>{if(event.persisted){disposed=false;polling=false;if(request)poll();else controls();}});
controls();
if(pageMode){opened=true;modalMode();resizeInput();$('ai-send-frame-label').hidden=true;}
refreshViewport();
if(pageMode){
  let leavingPage=false;
  document.querySelector('#coach-nutrition-entry').onclick=()=>{location.assign('/coach/nutrition');};
  const priorLeave=window.fitnessLeaveGuard;
  window.fitnessLeaveGuard=async target=>{
    if(busy){state('请先停止或核对当前请求后再离开。');return false;}
    if(priorLeave&&!await priorLeave(target))return false;
    const allowed=!input.value.trim()||await confirmDialog({title:'离开当前对话？',message:'尚未发送的输入将被清除。',confirmLabel:'离开当前对话'});
    leavingPage=allowed;return allowed;
  };
  document.querySelector('#coach-context-form')?.addEventListener('submit',async event=>{
    event.preventDefault();
    const destination=new URL('/coach',location.origin),record=document.querySelector('#coach-context-record').value;
    if(record)destination.searchParams.set('record',record);
    if(destination.href!==location.href&&await beforeLeave(destination.href))location.assign(destination.href);
  });
  window.addEventListener('beforeunload',event=>{if(!leavingPage&&(busy||input.value.trim())){event.preventDefault();event.returnValue='';}});
  window.addEventListener('pageshow',()=>{leavingPage=false;});
}
if(new URLSearchParams(location.search).get('assistant')==='1'&&document.body.dataset.page==='record-detail')document.querySelector('[data-ai-open]')?.click();
