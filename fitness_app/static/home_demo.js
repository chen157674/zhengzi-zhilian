// 首页播放已登记素材的真实预处理展示；实际分析走独立视频入口。
import {validFeedback,renderFeedback} from './home_demo_feedback.js';
const root=document.querySelector('#home-demo');
if(root){
  const video=root.querySelector('#showcase-video'),canvas=root.querySelector('#showcase-canvas'),wrap=root.querySelector('#showcase-player');
  const status=root.querySelector('#showcase-status'),play=root.querySelector('#showcase-play'),error=root.querySelector('#showcase-error'),fullscreen=root.querySelector('#showcase-fullscreen');
  const connections=[[11,12],[11,13],[13,15],[12,14],[14,16],[11,23],[12,24],[23,24],[23,25],[25,27],[24,26],[26,28],[27,29],[29,31],[28,30],[30,32]];
  const toggle=root.querySelector('#showcase-toggle'),reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const feedback=root.querySelector('#showcase-feedback');
  const reviewRanges=JSON.parse(root.querySelector('#saved-example-review-ranges')?.textContent||'[]');
  const quickFeedback=root.querySelector('#showcase-quick-feedback');
  let userPaused=false,initialPositioned=false;
  const playlist=JSON.parse(root.querySelector('#home-demo-playlist')?.textContent||'[]');
  let playlistIndex=0;
  function selectDemo(index){
    const item=playlist[index];if(!item)return;
    playlistIndex=index;root.dataset.demoId=item.id;root.dataset.videoUrl=item.video_url;root.dataset.trackUrl=item.track_url;root.dataset.sourceSha=item.source_sha;
    video.poster=item.poster_url;video.setAttribute('aria-label',item.title);
    root.querySelector('.showcase-badges strong').textContent=item.title;
    root.querySelector('.demo-caption a').href=(root.dataset.savedExample==='true'?'/video?demo=1&fresh=1&demo_id=':'/video?demo=1&demo_id=')+encodeURIComponent(item.id);
    load();
  }
  let track=null,generation=0,controller=null,callback=null,disposed=false,inView=true;
  function showFeedback(data){
    const stamp=video.currentTime*1000;
    const review=track&&root.dataset.savedExample==='true'?reviewRanges.find(r=>r.source==='user_confirmed_demo_annotation'&&r.start_ms<=stamp&&stamp<r.end_ms):null;
    renderFeedback(feedback,data,review);
    if(!quickFeedback)return;
    const state=validFeedback(data)?data.state:'unassessable';
    const label=state==='issue'&&data?.display_basis==='user_confirmed_demo_annotation'?'查看调整建议':{clear:'当前检查项正常',issue:'发现偏差 · 查看调整建议',unassessable:'',observing:'正在分析'}[state];
    if(quickFeedback.textContent!==label)quickFeedback.textContent=label;
    quickFeedback.dataset.state=state;
  }
  function cancelFrame(){if(callback!==null){video.cancelVideoFrameCallback?.(callback);callback=null}}
  function fail(message){if(toggle)toggle.disabled=true;cancelFrame();video.pause();track=null;canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);showFeedback(null);wrap.hidden=true;play.hidden=true;fullscreen.disabled=true;error.hidden=false;status.textContent=message}
  function validTrack(data){
    if(data?.schema_version!==1||data.source_sha256!==root.dataset.sourceSha||!['preprocessed_public_source_not_live','preprocessed_user_source_not_live'].includes(data.mode)||!Array.isArray(data.frames)||!data.frames.length||!Array.isArray(data.events))return false;
    if(!Number.isInteger(data.width)||!Number.isInteger(data.height)||data.width<=0||data.height<=0)return false;
    if(data.feedback_schema_version!==undefined&&data.feedback_schema_version!==1)return false;
    if(!data.frames.every(f=>(data.feedback_schema_version!==1&&f.feedback===undefined)||validFeedback(f.feedback)))return false;
    return data.frames.every((f,i)=>Number.isInteger(f.t)&&f.t>=0&&(!i||f.t>data.frames[i-1].t)&&Array.isArray(f.p)&&f.p.length===33&&f.p.every(p=>p===null||Array.isArray(p)&&p.length===2&&p.every(v=>Number.isFinite(v)&&v>=0&&v<=1)))&&
      data.events.every(e=>Number.isInteger(e.start_ms)&&Number.isInteger(e.end_ms)&&0<=e.start_ms&&e.start_ms<e.end_ms&&e.end_ms<=data.frames.at(-1).t);
  }
  function paint(mediaTime=video.currentTime){
    if(!track||!video.videoWidth||disposed)return;
    if(video.videoWidth!==track.width||video.videoHeight!==track.height){fail('视频与骨架尺寸不一致，请重新加载。');return}
    const box=video.getBoundingClientRect(),dpr=window.devicePixelRatio||1,w=Math.max(1,Math.round(box.width*dpr)),h=Math.max(1,Math.round(box.height*dpr));
    if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h}
    const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,box.width,box.height);
    const scale=Math.min(box.width/track.width,box.height/track.height),cw=track.width*scale,ch=track.height*scale,ox=(box.width-cw)/2,oy=(box.height-ch)/2;
    const stamp=Math.round(mediaTime*1000);let left=0,right=track.frames.length-1;
    while(left<right){const mid=Math.ceil((left+right)/2);if(track.frames[mid].t<=stamp)left=mid;else right=mid-1}
    const frame=track.frames[left];canvas.dataset.frameSeq=String(left);canvas.dataset.sourceMs=String(frame.t);canvas.dataset.mediaMs=String(stamp);
    // 超出已核帧范围时清图，不沿用最后一副骨架。
    if(stamp<frame.t||stamp-frame.t>100){showFeedback(null);return;}
    const currentFeedback=stamp<2000&&root.dataset.savedExample!=='true'?{schema_version:1,state:'observing',title:'正在分析',description:'等待连续动作。',instruction:'',scope_note:'',checks:[],highlight:null}:frame.feedback;
    showFeedback(currentFeedback);
    const xy=p=>[ox+p[0]*cw,oy+p[1]*ch];ctx.strokeStyle='#42f0ad';ctx.fillStyle='#dcfff0';ctx.lineWidth=Math.max(2,box.width/360);
    for(const [a,b] of connections){if(!frame.p[a]||!frame.p[b])continue;ctx.beginPath();ctx.moveTo(...xy(frame.p[a]));ctx.lineTo(...xy(frame.p[b]));ctx.stroke()}
    for(const point of frame.p){if(!point)continue;ctx.beginPath();ctx.arc(...xy(point),Math.max(2.5,box.width/270),0,Math.PI*2);ctx.fill()}
    const highlight=currentFeedback?.state==='issue'?currentFeedback.highlight:null;
    if(highlight){
      const selected=new Set(highlight.joint_indices.filter(i=>frame.p[i]));
      ctx.strokeStyle='#d84336';ctx.fillStyle='#fce4df';ctx.lineWidth=Math.max(4,box.width/230);
      for(const [a,b] of connections){if(!selected.has(a)||!selected.has(b))continue;ctx.beginPath();ctx.moveTo(...xy(frame.p[a]));ctx.lineTo(...xy(frame.p[b]));ctx.stroke()}
      for(const i of selected){ctx.beginPath();ctx.arc(...xy(frame.p[i]),Math.max(5,box.width/150),0,Math.PI*2);ctx.fill();ctx.stroke()}
    }
    root.querySelector('#showcase-count').textContent=String(track.events.filter(e=>e.end_ms<=stamp).length);
  }
  function schedule(){cancelFrame();if(video.requestVideoFrameCallback&&!video.paused&&!disposed)callback=video.requestVideoFrameCallback((now,meta)=>{callback=null;paint(meta.mediaTime);schedule()})}
  async function attemptPlay(manual=false){
    if(manual)userPaused=false;
    if(!track||disposed||document.hidden||!inView||(!manual&&(userPaused||reduced.matches)))return;
    try{await video.play();status.textContent='正在播放视频';play.hidden=true}
    catch{if(!disposed&&track){status.textContent='已准备好，点击播放。';if(toggle){toggle.textContent='播放';toggle.dataset.state='play';}play.hidden=Boolean(toggle)}}
  }
  async function load(){
    const own=++generation;controller?.abort();const requestController=new AbortController();controller=requestController;cancelFrame();video.pause();track=null;initialPositioned=false;
    canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);delete canvas.dataset.sourceMs;delete canvas.dataset.mediaMs;delete canvas.dataset.frameSeq;
    error.hidden=true;wrap.hidden=false;play.hidden=true;fullscreen.disabled=true;status.textContent='正在加载视频…';if(toggle)toggle.disabled=true;
    showFeedback({schema_version:1,state:'observing',title:'正在分析',description:'姿态结论会随视频画面同步更新。',instruction:'',scope_note:'',checks:[],highlight:null});
    const timeout=setTimeout(()=>requestController.abort(),10000);
    try{
      const response=await fetch(root.dataset.trackUrl,{signal:controller.signal,credentials:'same-origin'});if(!response.ok)throw Error('请重新加载动作轨迹');const data=await response.json();
      if(!validTrack(data))throw Error('展示轨迹与视频不匹配');if(disposed||own!==generation)return;
      track=data;video.src=root.dataset.videoUrl;video.load();
    }catch(e){if(!disposed&&own===generation)fail(e.name==='AbortError'?'视频加载超时，请重新加载。':e instanceof TypeError?'展示资源连接失败，请重新加载。':'请重新加载视频与动作轨迹。')}finally{clearTimeout(timeout)}
  }
  video.addEventListener('loadeddata',()=>{if(track&&root.dataset.savedExample==='true'&&!initialPositioned){initialPositioned=true;const stamp=Number(root.dataset.initialMs);if(Number.isFinite(stamp)&&stamp>0)video.currentTime=Math.min(stamp/1000,Math.max(0,video.duration-.05));}paint();if(track){fullscreen.disabled=false;if(toggle){toggle.disabled=false;toggle.textContent=video.paused?'播放':'暂停';toggle.dataset.state=video.paused?'play':'pause';}if(!matchMedia('(prefers-reduced-motion: reduce)').matches)attemptPlay();else play.hidden=Boolean(toggle);}});
  video.addEventListener('play',()=>{play.hidden=true;status.textContent='正在播放视频';schedule()});
  video.addEventListener('pause',()=>{cancelFrame();if(track&&!disposed){paint();play.hidden=Boolean(toggle);status.textContent='视频已暂停'}});
  video.addEventListener('seeked',()=>paint());video.addEventListener('timeupdate',()=>{if(!video.requestVideoFrameCallback||video.paused)paint()});
  video.addEventListener('ended',()=>{if(!disposed&&!userPaused&&root.dataset.savedExample!=='true')selectDemo((playlistIndex+1)%playlist.length);});
  for(const button of root.querySelectorAll('[data-example-seek],[data-review-seek]'))button.onclick=()=>{userPaused=true;video.pause();video.currentTime=Number(button.dataset.exampleSeek??button.dataset.reviewSeek)/1000;wrap.scrollIntoView({block:'center',behavior:'instant'});};
  video.addEventListener('error',()=>{if(track)fail('请重新加载视频。')});
  play.addEventListener('click',()=>attemptPlay(true));root.querySelector('#showcase-retry').addEventListener('click',load);
  toggle?.addEventListener('click',()=>{if(video.paused)attemptPlay(true);else{userPaused=true;video.pause();}});
  video.addEventListener('play',()=>{if(toggle){toggle.textContent='暂停';toggle.dataset.state='pause';}});
  video.addEventListener('pause',()=>{if(toggle){toggle.textContent='播放';toggle.dataset.state='play';}});
  root.querySelector('#showcase-fullscreen').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await wrap.requestFullscreen()}catch{status.textContent='请在当前页面播放。'}});
  root.querySelector('#showcase-exit-fullscreen').addEventListener('click',()=>document.exitFullscreen().catch(()=>{status.textContent='可按Esc退出全屏。'}));
  document.addEventListener('fullscreenchange',()=>{requestAnimationFrame(()=>paint());});window.addEventListener('resize',()=>paint());
  document.addEventListener('visibilitychange',()=>{if(document.hidden)video.pause();else attemptPlay()});
  reduced.addEventListener('change',()=>{if(reduced.matches)video.pause();else attemptPlay()});
  const observer=new IntersectionObserver(entries=>{inView=entries[0].isIntersecting;if(!inView)video.pause();else attemptPlay()},{threshold:.15});observer.observe(wrap);
  window.addEventListener('pagehide',()=>{disposed=true;generation++;controller?.abort();cancelFrame();observer.disconnect();video.pause();video.removeAttribute('src');video.load();track=null},{once:true});
  window.addEventListener('pageshow',event=>{if(event.persisted){disposed=false;observer.observe(wrap);load();}});
  if(root.dataset.savedExample==='true')userPaused=true;
  if(playlist.length)selectDemo(0);else load();
}
