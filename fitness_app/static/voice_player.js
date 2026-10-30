// 一条正在播放＋至多一条最新待播；关闭／暂停后迟到Promise不能复活语音。
export class VoicePlayer{
  constructor(catalogue,{audioFactory=url=>new Audio(url),clock=()=>performance.now(),setTimer=setTimeout,clearTimer=clearTimeout,onStatus=()=>{}}={}){
    this.catalogue=catalogue;this.audioFactory=audioFactory;this.clock=clock;this.setTimer=setTimer;this.clearTimer=clearTimer;this.onStatus=onStatus;
    this.enabled=false;this.allowed=false;this.context='';this.verified=new Set();this.watermarks=new Map();this.lastPlayed=new Map();this.failed=new Set();this.current=null;this.pending=null;this.generation=0;
  }
  setContext(sessionId,epoch,allowed,verifiedCodes=[]){
    const context=String(sessionId)+'/'+String(epoch);
    if(context!==this.context){this.stop();this.context=context;this.watermarks.clear();this.lastPlayed.clear()}
    this.verified=new Set(verifiedCodes);this.allowed=Boolean(allowed);if(!this.allowed)this.stop();
  }
  setEnabled(value){const next=Boolean(value);if(next&&!this.enabled)this.failed.clear();this.enabled=next;if(!this.enabled)this.stop()}
  stop(){
    this.generation++;this.pending=null;
    const current=this.current;this.current=null;
    if(current){this.clearTimer(current.timer);current.audio.pause();current.audio.removeAttribute?.('src');current.audio.load?.()}
    this.onStatus({state:'idle'});
  }
  offerBatch(events){
    if(!Array.isArray(events))return false;
    const marks=new Map(),eligible=[];
    for(const event of events){
      if(!event||typeof event!=='object')continue;
      const item=this.catalogue.prompts?.[event.prompt_id];
      if(!item||event.context!==this.context||!['state','cycle','static'].includes(event.kind)||!Number.isInteger(event.sequence)||event.sequence<0)continue;
      if(event.kind==='state'&&item.kind!=='state'||event.kind!=='state'&&(item.kind!=='quality'||!this.verified.has(event.prompt_id)))continue;
      let sequence=event.sequence,group=event.kind==='cycle'?'cycle':event.kind+':'+event.prompt_id;
      if(event.kind==='static'){
        if(!Number.isInteger(event.start_ms)||!Number.isInteger(event.end_ms)||event.start_ms<0||event.end_ms-event.start_ms<this.catalogue.static_confirm_ms)continue;
        sequence=event.start_ms+Math.floor((event.end_ms-event.start_ms-this.catalogue.static_confirm_ms)/this.catalogue.cooldown_ms)*this.catalogue.cooldown_ms;
      }
      if(sequence<=(this.watermarks.get(group)??-1))continue;
      marks.set(group,Math.max(sequence,marks.get(group)??-1));
      if(event.age_ms!==undefined&&(!Number.isFinite(event.age_ms)||event.age_ms<0))continue;
      const age=event.age_ms??0;
      if(!this.enabled||!this.allowed||age>this.catalogue.max_event_age_ms)continue;
      if(!item.available||this.failed.has(item.id)||typeof item.url!=='string'||!item.url.startsWith('/static/audio/')){this.onStatus({state:'unavailable',promptId:item.id,text:item.text});continue}
      eligible.push({event,item,group,sequence,receivedAt:this.clock()-age});
    }
    for(const [key,value] of marks)this.watermarks.set(key,value);
    const candidates=eligible.filter(c=>c.sequence===marks.get(c.group)).sort((a,b)=>b.item.priority-a.item.priority||a.item.id.localeCompare(b.item.id));
    if(!candidates.length)return false;
    const selected=candidates[0];
    if(this.current){this.pending=selected;return true}
    return this._play(selected);
  }
  _play(candidate){
    if(!this.enabled||!this.allowed||candidate.event.context!==this.context||this.clock()-candidate.receivedAt>this.catalogue.max_event_age_ms)return false;
    if(this.clock()-(this.lastPlayed.get(candidate.item.id)??-Infinity)<this.catalogue.cooldown_ms)return false;
    const token=++this.generation;let audio;
    try{audio=this.audioFactory(candidate.item.url)}catch{this.failed.add(candidate.item.id);this.onStatus({state:'unavailable',promptId:candidate.item.id});return false}
    const current={audio,token,candidate,timer:null,started:false};this.current=current;audio.preload='auto';
    const finish=failed=>{
      if(this.current?.token!==token)return;
      this.clearTimer(current.timer);this.current=null;audio.pause();audio.removeAttribute?.('src');audio.load?.();
      if(failed){this.failed.add(candidate.item.id);this.pending=null;this.onStatus({state:'unavailable',promptId:candidate.item.id});return}
      this.onStatus({state:'idle'});const next=this.pending;this.pending=null;if(next)this._play(next);
    };
    audio.addEventListener('playing',()=>{
      if(this.current?.token!==token||current.started)return;
      if(!this.enabled||!this.allowed||this.clock()-candidate.receivedAt>this.catalogue.max_event_age_ms){finish(true);return}
      current.started=true;this.clearTimer(current.timer);this.lastPlayed.set(candidate.item.id,this.clock());
      current.timer=this.setTimer(()=>finish(true),(candidate.item.duration_ms||this.catalogue.max_audio_ms)+1000);
      this.onStatus({state:'playing',promptId:candidate.item.id,text:candidate.item.text,startDelayMs:this.clock()-candidate.receivedAt});
    });
    audio.addEventListener('ended',()=>finish(false));audio.addEventListener('error',()=>finish(true));
    current.timer=this.setTimer(()=>finish(true),this.catalogue.max_event_age_ms);
    try{Promise.resolve(audio.play()).catch(()=>finish(true))}catch{finish(true)}
    return true;
  }
}
