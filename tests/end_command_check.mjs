// 19：实际结束函数的未知回执、控制变化、有限重试及原请求收尾边界；无模型调用。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import crypto from 'node:crypto';
const source=fs.readFileSync(new URL('../fitness_app/static/realtime.js',import.meta.url),'utf8');
const begin=source.indexOf('function savedTerminal(data){'),end=source.indexOf('\nstart.onclick=',begin);
assert(begin>=0&&end>begin);
class RequestError extends Error{constructor(message,status=0,fields={},code='UNKNOWN'){super(message);Object.assign(this,{status,fields,code,unknown:status===0||status>=500})}}
function fixture(mode){
  const state={reads:0,sends:[],holds:0,stops:0,cleanup:[],navigations:[],messages:[]};
  const context={RequestError,crypto,sid:'isolated-session',terminal:new Set(['completed','ended_early']),stopped:false,
    current:{status:'running',save_state:'clean',state_version:1,started_at_ms:1,control:{controller_epoch:4,stream_epoch:7}},
    credentials:()=>({controller_epoch:4,stream_epoch:7}),owns:()=>true,
    cameraControl:{hold(){state.holds++},async suspend(){state.stops++}},
    apply(value){context.current=value},render(){},showMessage(message){state.messages.push(message)},
    location:{assign(url){state.navigations.push(url)}},confirmDialog(options){state.dialog=options;return false},
    payload:(command,extra)=>({command,...extra,expected_version:context.current.state_version}),
    async refresh(){state.reads++;if(mode==='control')context.current.control.stream_epoch=8;
      if(mode==='cleanup'&&state.reads>1)throw new RequestError('storage',503,{},'STORAGE_UNAVAILABLE');return context.current},
    async api(url,options){state.sends.push(options);throw new RequestError('fixture',mode==='bounded'?409:0,{},mode==='bounded'?'VERSION_CONFLICT':'UNKNOWN')},
    async cleanup(body,key){state.cleanup.push({body,key});return {stopped:true,save_state:'save_failed',state_version:1,terminal_persisted:false}},
  };
  vm.createContext(context);vm.runInContext(source.slice(begin,end)+'\nglobalThis.invoke=endDialog;',context);
  return {context,state};
}
{
  const {context,state}=fixture('unknown');await context.invoke();assert.equal(state.holds,0,'打开／取消模态不应暂停');
  await assert.rejects(()=>state.dialog.onConfirm(),/结束结果尚未确认/);
  assert.equal(state.sends.length,1);assert.equal(state.navigations.length,0);assert.equal(context.stopped,false);
  context.current={...context.current,status:'ended_early'};
  assert.equal(await state.dialog.onConfirm(),true);assert.equal(state.sends.length,1,'再次点击只查询原结果');assert.equal(state.navigations.length,1);
}
{
  const {context,state}=fixture('control');await context.invoke();
  await assert.rejects(()=>state.dialog.onConfirm(),error=>error.code==='CONTROL_CONFLICT');
  assert.equal(state.sends.length,0);assert.equal(state.stops,1);
}
{
  const {context,state}=fixture('bounded');await context.invoke();
  await assert.rejects(()=>state.dialog.onConfirm(),error=>error.code==='VERSION_CONFLICT');
  assert.equal(state.sends.length,3);assert.equal(new Set(state.sends.map(s=>s.commandId)).size,3);assert.equal(state.stops,1);
}
{
  const {context,state}=fixture('cleanup');await context.invoke();
  assert.equal(await state.dialog.onConfirm(),false);assert.equal(state.sends.length,1);assert.equal(state.cleanup.length,1);
  assert.equal(state.cleanup[0].key,state.sends[0].commandId);assert.deepEqual(state.cleanup[0].body,state.sends[0].body);
  assert.equal(state.navigations.length,0);assert.equal(context.current.save_state,'save_failed');
}
console.log(JSON.stringify({checks:'passed',cases:4,scope:'end modal has no side effect before confirmation; unknown result queried without resend; changed stream rejected; three-attempt bound; storage fallback retains original request',modelCalls:0}));
