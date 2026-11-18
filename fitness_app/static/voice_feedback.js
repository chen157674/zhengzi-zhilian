import {api} from './app.js';
import {VoicePlayer} from './voice_player.js';

export function createVoiceFeedback(actionId){
  const toggle=document.querySelector('#live-voice'),status=document.querySelector('#live-voice-status');
  let player=null,catalogue=null,snapshot=null,owner=false,blocked=true,disposed=false,lastState=null,lastSession=null;
  const say=text=>{if(status.textContent!==text)status.textContent=text};
  function readiness(){
    if(!catalogue)return;
    toggle.disabled=!catalogue.available_count;
    if(!catalogue.available_count){toggle.checked=false;say('语音文件未准备或未核对，文字反馈仍可用。')}
    else say(toggle.checked?`语音已开启 · ${catalogue.available_count}条可用`:`语音已关闭 · ${catalogue.available_count}条可用`);
  }
  api('/api/v1/voice?action_id='+encodeURIComponent(actionId)).then(data=>{
    if(disposed)return;
    if(data?.schema_version!==1||!data.prompts||typeof data.prompts!=='object'||!['cooldown_ms','static_confirm_ms','max_event_age_ms','max_audio_ms'].every(k=>Number.isSafeInteger(data[k])&&data[k]>0))throw Error('语音清单无效');
    catalogue=data;player=new VoicePlayer(data,{onStatus:event=>{
      if(disposed)return;
      if(event.state==='playing')say('正在播报：'+event.text);
      else if(event.state==='unavailable')say('请查看文字反馈。');
      else if(event.state==='idle'&&status.textContent.startsWith('正在播报'))readiness();
    }});readiness();if(snapshot)update(snapshot,owner,blocked);
  }).catch(()=>{player?.setEnabled(false);player=null;catalogue=null;if(!disposed){toggle.disabled=true;status.textContent='请查看文字反馈，或重试加载语音。'}});
  toggle.addEventListener('change',()=>{player?.setEnabled(toggle.checked);readiness()});
  function update(data,owns,isBlocked=false){
    snapshot=data;owner=owns;blocked=isBlocked;if(!player||!data)return;
    const active=owns&&!isBlocked&&!document.hidden&&!data.analysis_frozen&&data.save_state==='clean'&&['running','resting','waiting'].includes(data.status)&&
      (data.status!=='running'||data.latest_frame?.framing.usable===true);
    const verified=(data.supported_items||[]).filter(i=>i.status==='verified').map(i=>i.code);
    player.setContext(data.session_id,data.control?.stream_epoch??'no-stream',active,verified);
    if(lastSession!==data.session_id){lastSession=data.session_id;lastState=null}
    if(lastState!==data.status){
      player.stop();const id={running:'training.started',resting:'training.resting',waiting:'tracking.waiting'}[data.status];
      if(id)player.offerBatch([{prompt_id:id,kind:'state',sequence:data.status==='resting'?data.completed_sets:data.state_version,context:player.context}]);
      lastState=data.status;
    }
    if(catalogue.capability_version===data.capability_version&&data.feedback_epoch===data.control?.stream_epoch&&Array.isArray(data.feedback)){
      player.offerBatch(data.feedback.map(item=>({...item,context:player.context,age_ms:data.feedback_age_ms||0})));
    }
  }
  function suspend(){player?.setContext(snapshot?.session_id||'',snapshot?.control?.stream_epoch??'no-stream',false)}
  function problemText(data){
    if(!catalogue||catalogue.capability_version!==data?.capability_version||data.save_state!=='clean'||data.analysis_frozen||data.status!=='running'||data.latest_frame?.framing.usable!==true||data.feedback_epoch!==data.control?.stream_epoch)return '';
    const verified=new Set((data.supported_items||[]).filter(i=>i.status==='verified').map(i=>i.code));
    const candidates=(data.feedback||[]).filter(f=>verified.has(f.prompt_id)&&catalogue.prompts[f.prompt_id]?.kind==='quality').sort((a,b)=>catalogue.prompts[b.prompt_id].priority-catalogue.prompts[a.prompt_id].priority||a.prompt_id.localeCompare(b.prompt_id));
    const first=candidates[0];return first?`${first.side==='left'?'左侧 · ':first.side==='right'?'右侧 · ':''}${catalogue.prompts[first.prompt_id].text}`:'';
  }
  function disable(){toggle.checked=false;player?.setEnabled(false);suspend();readiness()}
  document.addEventListener('visibilitychange',()=>{if(document.hidden)suspend()});
  window.addEventListener('storage',event=>{if(event.key==='fitness-auth-change')disable()});
  document.addEventListener('click',event=>{if(event.target.closest('[data-action="auth.logout"]'))disable()},true);
  window.addEventListener('pagehide',()=>{disposed=true;player?.setEnabled(false)},{once:true});
  return {update,suspend,problemText};
}
