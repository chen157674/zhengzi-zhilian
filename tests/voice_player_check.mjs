// 18-E D-CLOCK／Audio替身，只验队列控制，不声称实际音频可播。
import assert from 'node:assert/strict';
import {VoicePlayer} from '../fitness_app/static/voice_player.js';

const catalogue={cooldown_ms:5000,static_confirm_ms:1000,max_event_age_ms:1000,max_audio_ms:15000,prompts:{}};
for(const [id,kind,priority] of [['training.started','state',10],['test.low','quality',20],['test.high','quality',90]])catalogue.prompts[id]={id,kind,priority,available:true,url:'/static/audio/'+id+'.mp3',text:id,duration_ms:2000};
function fixture(){
  let now=0,timerId=0;const timers=new Map(),audios=[],statuses=[];
  class StubAudio{
    constructor(url){this.url=url;this.listeners={};this.pauses=0;audios.push(this)}
    addEventListener(name,fn){this.listeners[name]=fn}
    play(){return new Promise((resolve,reject)=>{this.resolve=resolve;this.reject=reject})}
    pause(){this.pauses++}removeAttribute(){}load(){}
    emit(name){this.listeners[name]?.()}
  }
  const p=new VoicePlayer(structuredClone(catalogue),{audioFactory:url=>new StubAudio(url),clock:()=>now,
    setTimer:(fn,ms)=>{const id=++timerId;timers.set(id,{at:now+ms,fn});return id},clearTimer:id=>timers.delete(id),onStatus:s=>statuses.push(s)});
  p.setContext('s','e',true,['test.high','test.low']);p.setEnabled(true);
  return {p,audios,statuses,e:(id,sequence,kind='cycle',extra={})=>({prompt_id:id,sequence,kind,context:p.context,...extra}),
    advance(ms){now+=ms;for(const [id,t] of [...timers])if(t.at<=now){timers.delete(id);t.fn()}},close(){p.stop();assert.equal(timers.size,0)}};
}
let passed=0;
{
  const f=fixture();f.p.offerBatch([f.e('test.low',1),f.e('test.high',1)]);assert.equal(f.audios.length,1);assert(f.audios[0].url.includes('high'));
  f.audios[0].emit('playing');f.audios[0].emit('ended');assert.equal(f.p.offerBatch([f.e('test.low',1)]),false);
  assert.equal(f.p.offerBatch([f.e('test.high',2)]),false);f.advance(5000);assert.equal(f.p.offerBatch([f.e('test.high',3)]),true);f.close();passed++;
}
{
  const f=fixture();f.p.offerBatch([f.e('training.started',1,'state')]);f.audios[0].emit('playing');
  f.p.offerBatch([f.e('test.low',1)]);f.p.offerBatch([f.e('test.high',2)]);f.audios[0].emit('ended');
  assert.equal(f.audios.length,2);assert(f.audios[1].url.includes('high'));f.close();passed++;
}
{
  const f=fixture();f.p.offerBatch([f.e('training.started',1,'state')]);f.audios[0].emit('playing');f.p.offerBatch([f.e('test.high',1)]);
  f.advance(1001);f.audios[0].emit('ended');assert.equal(f.audios.length,1);f.close();passed++;
}
{
  const f=fixture();f.p.offerBatch([f.e('test.low',1)]);f.p.offerBatch([f.e('test.high',2)]);const old=f.audios[0];
  f.p.setEnabled(false);f.p.setEnabled(true);old.emit('playing');old.resolve();await Promise.resolve();
  assert.equal(f.statuses.filter(s=>s.state==='playing').length,0);assert.equal(f.p.offerBatch([f.e('test.high',2)]),false);
  assert(f.p.offerBatch([f.e('test.high',3)]));f.audios[1].emit('playing');assert.equal(f.statuses.filter(s=>s.state==='playing').length,1);f.close();passed++;
}
{
  const f=fixture(),event=end=>f.e('test.high',end,'static',{start_ms:0,end_ms:end});
  assert.equal(f.p.offerBatch([event(999)]),false);assert(f.p.offerBatch([event(1000)]));f.audios[0].emit('playing');f.audios[0].emit('ended');
  assert.equal(f.p.offerBatch([event(5999)]),false);f.advance(5000);assert(f.p.offerBatch([event(6000)]));f.close();passed++;
}
{
  const f=fixture();f.p.catalogue.prompts['training.started'].available=false;
  assert.equal(f.p.offerBatch([f.e('training.started',1,'state')]),false);assert.equal(f.audios.length,0);
  f.p.setContext('s','e',true,[]);assert.equal(f.p.offerBatch([f.e('test.high',1)]),false);assert.equal(f.audios.length,0);f.close();passed++;
}
{
  const f=fixture();f.p.offerBatch([f.e('test.high',1)]);const old=f.audios[0];f.advance(1001);old.emit('playing');
  assert.equal(f.statuses.filter(s=>s.state==='playing').length,0);assert.equal(f.p.offerBatch([f.e('test.low',2,'cycle',{age_ms:1001})]),false);
  assert(f.p.offerBatch([f.e('test.low',3)]));f.audios[1].reject(Error('D-FAULT playback'));await Promise.resolve();await Promise.resolve();
  assert.equal(f.statuses.filter(s=>s.state==='playing').length,0);assert(f.statuses.some(s=>s.state==='unavailable'));f.close();passed++;
}
{
  const f=fixture();const oldEvent=f.e('test.high',1);f.p.offerBatch([oldEvent]);f.audios[0].emit('playing');f.p.setContext('s','e',false,['test.high']);
  assert(f.audios[0].pauses>0);f.p.offerBatch([f.e('test.high',2)]);f.p.setContext('s','e',true,['test.high']);assert.equal(f.p.offerBatch([f.e('test.high',2)]),false);
  f.p.setContext('s','e2',true,['test.high']);assert.equal(f.p.offerBatch([oldEvent]),false);assert(f.p.offerBatch([f.e('test.high',1)]));f.close();passed++;
}
{
  const f=fixture();let made=0;f.p.audioFactory=()=>{made++;throw Error('D-FAULT factory')};
  f.p.offerBatch([f.e('test.high',1)]);f.p.setEnabled(false);f.p.setEnabled(true);
  f.p.offerBatch([f.e('test.high',1)]);assert.equal(made,1);f.p.offerBatch([f.e('test.high',2)]);assert.equal(made,2);f.close();passed++;
}
console.log(JSON.stringify({checks:'passed',scenarios:passed,scope:'clock-controlled Audio substitute; not actual audible playback'}));
