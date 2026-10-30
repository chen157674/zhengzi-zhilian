import {api,keepDialogFocus} from './app.js';
const $=id=>document.getElementById(id);
const panel=$('ai-assistant'),input=$('ai-chat-input'),form=$('ai-chat-form');
const content=panel.querySelector('.ai-assistant-content'),messages=$('ai-chat-messages');
let conversation=null,creation=null,contextKey='',busy=false,opened=false,request=null,pending=null;
let disposed=false,timer=null,releaseFocus=null,opener=null,inertNodes=[],polling=false,needsCheck=false;
let streamNode=null,streamFrame=null,streamTarget='';

function context(){
  const live=document.querySelector('#live-workspace');
  if(live?.dataset.previewId)return {context_kind:'live',context_id:live.dataset.previewId,history_scope:'related'};
  const record=document.querySelector('.record-detail[data-session-id]');
  if(record)return {context_kind:'record',context_id:record.dataset.sessionId,history_scope:'related'};
  return {context_kind:['records','overview'].includes(document.body.dataset.page)?'overview':'general',history_scope:'all'};
}
function fingerprint(){return JSON.stringify(context())+'|'+(document.querySelector('#live-workspace')?.dataset.aiSegment||'');}
function pictureSource(){return document.querySelector('#live-source, #camera-source, #video-player, #record-media video');}
async function capturePictures(){
  const video=pictureSource();
  if(!video||video.readyState<2||!video.videoWidth||!video.videoHeight)throw new Error('请先开启摄像头或加载视频，再附上当前画面。');
  const canvas=document.createElement('canvas'),scale=Math.min(1,640/Math.max(video.videoWidth,video.videoHeight));
  canvas.width=Math.max(1,Math.round(video.videoWidth*scale));canvas.height=Math.max(1,Math.round(video.videoHeight*scale));
  const paint=canvas.getContext('2d'),frames=[];
  const isLive=['live-source','camera-source'].includes(video.id),source=video.srcObject||video.currentSrc;
  try{
    for(let i=0;i<(video.paused&&!isLive?1:3);i++){
      if(i)await new Promise(resolve=>setTimeout(resolve,350));
      if(disposed||!opened||pictureSource()!==video||(video.srcObject||video.currentSrc)!==source||video.readyState<2)throw new Error('画面已切换，请重新发送。');
      paint.drawImage(video,0,0,canvas.width,canvas.height);
      const data_url=canvas.toDataURL('image/jpeg',.72);
      if(data_url.length>273090)throw new Error('画面过大，请调整取景后重试。');
      frames.push({data_url,source_ms:Math.round(video.currentTime*1000)});
    }
    return frames;
  }finally{canvas.width=canvas.height=0;}
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
  $('ai-chat-send').disabled=busy&&!needsCheck||!needsCheck&&!input.value.trim();
  $('ai-chat-stop').hidden=!request||!busy||needsCheck;
  $('ai-chat-new').disabled=busy;
  $('ai-assistant-refresh').disabled=busy;
  panel.dataset.busy=String(busy);
  $('ai-send-frame').disabled=busy;
}
function setBusy(value){busy=value;controls();}
function message(role,text){
  const stick=nearBottom();
  $('ai-chat-welcome').hidden=true;
  const node=document.createElement('div'),p=document.createElement('p');
  node.className='ai-chat-message '+role;p.textContent=text;node.append(p);messages.append(node);
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
function renderResult(result){
  if(streamFrame!==null)cancelAnimationFrame(streamFrame);
  streamFrame=null;streamTarget='';
  const stick=nearBottom(),node=streamNode||message('assistant','');
  streamNode=null;node.replaceChildren();delete node.dataset.streaming;node.removeAttribute('aria-live');
  const summary=document.createElement('p');summary.textContent=result.summary;node.append(summary);
  const copyText=[result.summary];
  for(const item of result.explanations||[]){
    const card=document.createElement('section');card.className='ai-evidence-card';
    const title=document.createElement('strong');title.textContent=item.evidence?.message||item.evidence?.title||'动作建议';
    const body=document.createElement('p');body.textContent=item.explanation;
    const tip=document.createElement('p');tip.textContent=item.adjustment;
    card.append(title,body,tip);node.append(card);copyText.push(title.textContent,item.explanation,item.adjustment);
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
    .then(value=>{conversation=value;contextKey=key;$('ai-assistant-refresh').hidden=true;return value;})
    .finally(()=>{creation=null;});
  return creation;
}
function modalMode(){
  for(const [node,previous]of inertNodes)node.inert=previous;
  inertNodes=[];releaseFocus?.();releaseFocus=null;
  if(!opened)return;
  if(matchMedia('(max-width:760px)').matches){
    panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','true');
    for(const node of document.body.children){if(node!==panel&&node.tagName!=='SCRIPT'){inertNodes.push([node,node.inert]);node.inert=true;}}
    releaseFocus=keepDialogFocus(panel);document.documentElement.classList.add('ai-modal-open');
  }else{panel.setAttribute('role','complementary');panel.removeAttribute('aria-modal');document.documentElement.classList.remove('ai-modal-open');}
}
async function open(event){
  opener=event?.currentTarget||document.activeElement;
  if(document.fullscreenElement)try{await document.exitFullscreen();}catch{}
  opened=true;panel.hidden=false;document.body.classList.add('ai-assistant-open');modalMode();
  $('ai-send-frame-label').hidden=!pictureSource();
  input.focus({preventScroll:true});resizeInput();
  try{
    if(!conversation)await create();
    else if(contextKey!==fingerprint()){$('ai-assistant-refresh').hidden=false;state('训练已变化，可更新摘要');}
  }catch(error){fail(error);}
}
function close(){
  opened=false;panel.hidden=true;document.body.classList.remove('ai-assistant-open');
  document.documentElement.classList.remove('ai-modal-open');modalMode();opener?.focus({preventScroll:true});
}
async function newConversation(){
  if(busy)return;
  setBusy(true);$('ai-chat-error').hidden=true;
  try{
    if(conversation)try{await api('/api/v1/ai/conversations/'+conversation.conversation_id,{method:'DELETE'});}catch(error){if(error.status!==404)throw error;}
    conversation=null;contextKey='';await create();discardStream();messages.replaceChildren();
    input.value='';resizeInput();$('ai-chat-welcome').hidden=false;state();
  }catch(error){fail(error);}finally{setBusy(false);}
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
      renderResult(row.result);state();
      if(!row.result.facts?.visual_input&&row.live_context?.matches_current_segment===false){state('训练已变化，可更新摘要');$('ai-assistant-refresh').hidden=false;}
    }else{
      discardStream();
      if(row.context_current===false){state('训练依据已更新，请更新摘要');$('ai-assistant-refresh').hidden=false;}
      else if(row.status==='cancelled')state('已停止');
      else fail(new Error(row.message||'本次未能完成回复，请重试。'));
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
    const frameConsent=$('ai-send-frame').checked;
    if(frameConsent)state('正在读取画面…');
    const frames=frameConsent?await capturePictures():null;
    pending={id:crypto.randomUUID(),text,body:{text,consent:true,expected_context_revision:conversation.context_revision,...(frames?{include_current_frame:true,frames}:{})},displayed:false};
    request=await api('/api/v1/ai/conversations/'+conversation.conversation_id+'/messages',{method:'POST',commandId:pending.id,body:pending.body});
    message('user',pending.text);pending.displayed=true;
    if(input.value.trim()===text){input.value='';resizeInput();}
    stream();controls();poll();
  }catch(error){
    if(error.code==='AI_TOPIC_OUT_OF_SCOPE'){
      message('user',text);message('assistant',error.message);
      if(input.value.trim()===text){input.value='';resizeInput();}
      state();pending=null;request=null;
    }else{
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
$('ai-send-frame').onchange=()=>{$('ai-frame-hint').hidden=!$('ai-send-frame').checked;};
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
$('ai-assistant-refresh').onclick=newConversation;
$('ai-assistant-close').onclick=close;
for(const button of document.querySelectorAll('[data-ai-open]'))button.onclick=open;
for(const button of document.querySelectorAll('[data-ai-prompt]'))button.onclick=()=>{input.value=button.dataset.aiPrompt;resizeInput();controls();input.focus({preventScroll:true});};
panel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close();}});
window.addEventListener('resize',modalMode);
window.visualViewport?.addEventListener('resize',()=>{panel.style.setProperty('--ai-viewport-height',window.visualViewport.height+'px');panel.style.setProperty('--ai-viewport-top',window.visualViewport.offsetTop+'px');});
window.addEventListener('fitness-auth-revoked',()=>{
  conversation=request=pending=null;needsCheck=false;clearTimeout(timer);discardStream();messages.replaceChildren();
  input.value='';setBusy(false);close();
  $('ai-send-frame').checked=false;$('ai-frame-hint').hidden=true;
});
window.addEventListener('pagehide',()=>{disposed=true;clearTimeout(timer);if(streamFrame!==null)cancelAnimationFrame(streamFrame);streamFrame=null;});
window.addEventListener('pageshow',event=>{if(event.persisted){disposed=false;polling=false;if(request)poll();else controls();}});
controls();
if(new URLSearchParams(location.search).get('assistant')==='1'&&document.body.dataset.page==='record-detail')document.querySelector('[data-ai-open]')?.click();
