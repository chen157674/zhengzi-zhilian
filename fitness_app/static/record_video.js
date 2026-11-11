import {el,privateError} from './record_common.js';
import {sendVideo,localPlayer,demoAttribution} from './video_source.js';
import {api} from './app.js';
import {recordCue,paintCue} from './correction_cue.js';
import {reviewSampleTimes} from './ai_media_selection.js';

export function historyVideo(container,sid,isDemo=false,record=null,idPrefix='record-source'){
  const video=el('video',undefined,'record-video');video.controls=true;video.preload='metadata';video.playsInline=true;video.hidden=true;video.setAttribute('aria-label','已匹配原视频回看');
  const source=localPlayer(video),picker=el('input'),select=el('button','重新选择原片','button secondary'),message=el('p','选择原视频后，可回看问题时段。','muted');
  let disposed=false,hasSkeleton=false,matching=false;const aborter=new AbortController();const retained=el('p','','field-help');
  picker.type='file';picker.accept='.mp4,video/mp4';picker.hidden=true;select.type='button';select.id=idPrefix+'-select';picker.id=idPrefix+'-file';
  const actions=el('div',undefined,'record-video-actions'),label=el('label','显示骨骼关键点'),toggle=el('input');toggle.type='checkbox';toggle.disabled=true;label.prepend(toggle);actions.append(select,label);
  const videoWrap=el('div',undefined,'video-preview-stage'),cue=el('aside',undefined,'video-correction');cue.hidden=true;const cueTitle=el('strong'),cueText=el('p');cueTitle.dataset.cueTitle='';cueText.dataset.cueText='';cue.append(cueTitle,cueText);const missing=el('div',undefined,'source-missing'),missingTitle=el('h3','原片未加载'),missingCopy=el('p','已保存的纠正提示和反馈仍可查看。');missing.append(missingTitle,missingCopy,select,message);videoWrap.append(missing,video,cue);const update=()=>{if(record&&source.has()){const value=recordCue(record,video.currentTime*1000);if(value.state==='unassessable'){cue.hidden=true;return;}paintCue(cue,value);}};video.addEventListener('timeupdate',update);video.addEventListener('loadeddata',update);video.addEventListener('seeked',update);container.append(videoWrap,actions,picker,retained);function syncSource(){const matched=source.has();missing.hidden=matched;if(matched){actions.prepend(select);actions.append(message);}else missing.append(select,message);container.dispatchEvent(new CustomEvent('fitness-record-source',{bubbles:true}));}
  const credit=el('div');if(isDemo)container.append(credit);
  select.onclick=()=>picker.click();
  async function loadDemo(){
    select.disabled=true;message.textContent='正在核对登记演示原片…';
    try{const data=await api(`/api/v1/records/${sid}/demo-source`);if(disposed)return;demoAttribution(credit,data);source.setRemote(data.video_url);video.hidden=false;message.textContent='';syncSource()}
    catch(error){if(!privateError(error)){source.clear();video.hidden=true;message.textContent=error.message||'演示原片暂不可用';syncSource()}}finally{select.disabled=false}
  }
  if(isDemo){select.textContent='重新加载演示原片';select.onclick=loadDemo;video.addEventListener('error',()=>{source.clear();video.hidden=true;message.textContent='演示原片暂时无法播放，请重新加载。';syncSource();});loadDemo()}
  picker.onchange=async()=>{
    const file=picker.files[0];picker.value='';if(!file||matching||disposed)return;
    if(file.size>1024**3){message.textContent='单文件不能超过 1 GiB';return}
    matching=true;select.disabled=true;message.textContent='正在匹配原文件内容…';const matchController=new AbortController();const timeout=setTimeout(()=>matchController.abort(),30000);const abortMatch=()=>matchController.abort();aborter.signal.addEventListener('abort',abortMatch,{once:true});
    try{await sendVideo(`/api/v1/records/${sid}/source-match`,file,undefined,matchController.signal);if(disposed)return;source.set(file);syncSource();video.hidden=false;message.textContent='原文件匹配成功，可按已保存问题时间回看。';toggle.disabled=!hasSkeleton;video.dispatchEvent(new Event('fitness-source-matched'))}
    catch(error){if(!disposed&&!privateError(error))message.textContent=error.message||'匹配未确认，请重新选择。'}finally{clearTimeout(timeout);aborter.signal.removeEventListener('abort',abortMatch);matching=false;if(!disposed)select.disabled=false}
  };
  window.addEventListener('pagehide',()=>{disposed=true;aborter.abort();source.clear()},{once:true});
  window.addEventListener('fitness-auth-revoked',()=>{disposed=true;aborter.abort();source.clear();container.replaceChildren();},{once:true});
  function setRange(value){for(const [field,key] of [['source_start_ms','aiRangeStartMs'],['source_end_ms','aiRangeEndMs']]){
    const stamp=value?.config?.[field];if(Number.isFinite(stamp))video.dataset[key]=String(stamp);else delete video.dataset[key];}
    video.dataset.aiReviewTimesMs=JSON.stringify(reviewSampleTimes(value,value?.config?.source_start_ms||0,value?.config?.source_end_ms||Infinity));}
  setRange(record);
  return {video,toggle,dispose(){disposed=true;aborter.abort();source.clear();video.pause();},updateRecord(value){record=value;setRange(value);update();},hasMatched:()=>source.has(),setSkeletonAvailable(value){actions.dataset.skeletonAvailable=String(value);hasSkeleton=value;toggle.disabled=!value||!source.has();retained.textContent=value?'可显示已保存的骨架':'';},seek(milliseconds){if(!source.has()){message.textContent='请先重新选择并匹配原视频，再定位问题。';select.focus();return}video.pause();video.currentTime=milliseconds/1000;video.scrollIntoView({block:'center'})}};
}
