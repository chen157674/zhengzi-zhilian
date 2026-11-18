import {validFeedback} from './home_demo_feedback.js';

function aborted(signal){
  if(signal?.aborted)throw new DOMException('分析已取消','AbortError');
}
function nextFrame(signal){
  aborted(signal);
  return new Promise((resolve,reject)=>{
    const frame=requestAnimationFrame(()=>{signal?.removeEventListener('abort',cancel);resolve();});
    function cancel(){cancelAnimationFrame(frame);reject(new DOMException('分析已取消','AbortError'));}
    signal?.addEventListener('abort',cancel,{once:true});
  });
}

// 已登记视频复用同源姿态数据；进度对应实际读取与逐帧核对，不重复启动GPU推理。
export async function prepareDemoAnalysis(demo,{signal,onProgress=()=>{}}={}){
  if(!demo.analysis_track_url||!demo.source_sha256)throw new Error('请重新选择视频并开始分析。');
  const controller=new AbortController(),cancel=()=>controller.abort();
  signal?.addEventListener('abort',cancel,{once:true});
  const timeout=setTimeout(cancel,15000);
  try{
    aborted(signal);onProgress({phase:'正在准备视频分析',processed:0,total:0});
    const response=await fetch(demo.analysis_track_url,{credentials:'same-origin',signal:controller.signal});
    if(!response.ok)throw new Error('视频分析数据读取失败，请重试。');
    const track=await response.json();clearTimeout(timeout);aborted(signal);
    if(track.schema_version!==1||track.feedback_schema_version!==1||
       track.source_sha256!==demo.source_sha256||track.action_id!==demo.action_id||
       !['preprocessed_public_source_not_live','preprocessed_user_source_not_live'].includes(track.mode)||
       !Number.isInteger(track.width)||!Number.isInteger(track.height)||track.width<=0||track.height<=0||
       !Array.isArray(track.frames)||!track.frames.length||!Array.isArray(track.events)){
      throw new Error('姿态数据与当前视频不匹配，请重新分析。');
    }
    const frames=track.frames;
    for(let start=0;start<frames.length;start+=16){
      aborted(signal);
      const end=Math.min(start+16,frames.length);
      for(let i=start;i<end;i++){
        const frame=frames[i];
        if(!Number.isInteger(frame.t)||frame.t<0||(i&&frame.t<=frames[i-1].t)||
           !Array.isArray(frame.p)||frame.p.length!==33||
           !frame.p.every(p=>p===null||Array.isArray(p)&&p.length===2&&p.every(v=>Number.isFinite(v)&&v>=0&&v<=1))||
           !validFeedback(frame.feedback)){
          throw new Error('姿态数据不完整，请重新分析这段视频。');
        }
      }
      onProgress({phase:'正在读取姿态数据',processed:end,total:frames.length,percent:20*end/frames.length});
      await nextFrame(signal);
    }
    if(!track.events.every(event=>Number.isInteger(event.start_ms)&&Number.isInteger(event.end_ms)&&
      event.start_ms>=0&&event.end_ms>event.start_ms&&event.end_ms<=frames.at(-1).t)){
      throw new Error('动作时间区间无效，请重新分析这段视频。');
    }
    onProgress({phase:'正在准备动作分析',processed:frames.length,total:frames.length,percent:20});
    return track;
  }catch(error){
    if(controller.signal.aborted&&!signal?.aborted)throw new Error('视频分析数据读取超时，请重试。');
    throw error;
  }finally{
    clearTimeout(timeout);signal?.removeEventListener('abort',cancel);
  }
}

// 进度由实际视频播放时间推进，让使用者看到4倍速的姿态复核画面。
export async function reviewDemoVideo(track,video,{signal,onProgress=()=>{},onFrame=()=>{},speed=4}={}){
  aborted(signal);
  const duration=Math.round(video.duration*1000);
  if(!Number.isFinite(duration)||duration<=0)throw new Error('正在读取视频时长，请稍后重试。');
  const previous={rate:video.playbackRate,muted:video.muted,controls:video.controls};
  try{
    video.pause();video.currentTime=0;video.muted=true;video.controls=false;video.playbackRate=speed;
    await new Promise((resolve,reject)=>{
      let done=false,callback=null;
      const timeout=setTimeout(()=>finish(new Error('分析进行中，请稍后查看结果。')),Math.max(15000,duration/speed*3));
      function paint(stamp){
        let left=0,right=track.frames.length-1;
        while(left<right){const middle=Math.ceil((left+right)/2);if(track.frames[middle].t<=stamp)left=middle;else right=middle-1;}
        const frame=track.frames[left];
        onFrame(stamp>=frame.t&&stamp-frame.t<=120?frame:null);
        onProgress({phase:'正在分析动作片段',processed:left+1,total:track.frames.length,
          percent:20+80*Math.min(1,stamp/duration),media_ms:stamp,duration_ms:duration,speed});
      }
      function schedule(){
        if(done)return;
        if(video.requestVideoFrameCallback)callback=video.requestVideoFrameCallback((now,metadata)=>{callback=null;paint(metadata.mediaTime*1000);schedule();});
        else callback=requestAnimationFrame(()=>{callback=null;paint(video.currentTime*1000);schedule();});
      }
      function finish(error){
        if(done)return;done=true;clearTimeout(timeout);
        if(callback!==null){if(video.requestVideoFrameCallback)video.cancelVideoFrameCallback(callback);else cancelAnimationFrame(callback);}
        signal?.removeEventListener('abort',cancel);video.removeEventListener('ended',ended);video.removeEventListener('error',failed);
        video.pause();if(error)reject(error);else resolve();
      }
      function cancel(){finish(new DOMException('分析已取消','AbortError'));}
      function failed(){finish(new Error('请重新选择视频并播放。'));}
      function ended(){paint(duration);finish();}
      signal?.addEventListener('abort',cancel,{once:true});video.addEventListener('ended',ended,{once:true});video.addEventListener('error',failed,{once:true});
      paint(0);video.play().then(schedule).catch(()=>finish(new Error('请再次点击开始分析。')));
    });
    aborted(signal);onProgress({phase:'分析完成，正在展示结论',processed:track.frames.length,total:track.frames.length,percent:100});
    await nextFrame(signal);await nextFrame(signal);
  }finally{
    video.pause();video.playbackRate=previous.rate;video.muted=previous.muted;video.controls=previous.controls;onFrame(null);
  }
}
