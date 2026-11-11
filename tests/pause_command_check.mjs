// 19：直接执行正式暂停函数，验证有限重试与未知结果／控制变化边界，不运行模型。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import crypto from 'node:crypto';
const source=fs.readFileSync(new URL('../fitness_app/static/realtime.js',import.meta.url),'utf8');
const begin=source.indexOf('async function command(name,extra={}){'),end=source.indexOf('\nasync function cleanup(',begin);
assert(begin>=0&&end>begin);
class RequestError extends Error{constructor(message,status,fields,code){super(message);Object.assign(this,{status,fields,code})}}
function fixture(mode){
  const state={reads:0,sends:0,holds:0,stops:0,keys:[]};
  const context={RequestError,crypto,sid:'isolated-session',terminal:new Set(['completed','ended_early']),
    current:{status:'running',state_version:1,control:{controller_epoch:4,stream_epoch:7}},
    credentials:()=>({controller_epoch:4,stream_epoch:7}),owns:()=>true,
    cameraControl:{hold(){state.holds++},async suspend(){state.stops++}},apply(){},
    payload:()=>({command:'pause',expected_version:context.current.state_version}),
    async refresh(){state.reads++;if(mode==='control')context.current.control.stream_epoch=8;return context.current},
    async api(url,options){state.sends++;state.keys.push(options.commandId);throw new RequestError('fixture',mode==='unknown'?0:409,{},mode==='unknown'?'UNKNOWN':'VERSION_CONFLICT')},
  };
  vm.createContext(context);vm.runInContext(source.slice(begin,end)+'\nglobalThis.invoke=command;',context);
  return {context,state};
}
for(const mode of ['unknown','control','bounded']){
  const {context,state}=fixture(mode);
  await assert.rejects(()=>context.invoke('pause'),error=>error.code===(mode==='unknown'?'UNKNOWN':mode==='control'?'CONTROL_CONFLICT':'VERSION_CONFLICT'));
  assert.equal(state.sends,mode==='unknown'?1:mode==='control'?0:3);
  assert.equal(state.reads,mode==='bounded'?3:1);
  assert.equal(new Set(state.keys).size,state.keys.length);
  assert.equal(state.holds,1);assert.equal(state.stops,1);
}
console.log(JSON.stringify({checks:'passed',cases:3,scope:'pause unknown result not resent; changed stream not controlled; at most three explicit-version-conflict attempts',modelCalls:0}));
