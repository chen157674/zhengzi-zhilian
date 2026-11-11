// 14 新能力展示：默认复用后撤事件；--plank／--jack 运行各自新增真实 Full 回放场景。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step14');const errors=[];
const plank=process.argv.includes('--plank');
const jack=process.argv.includes('--jack'),row=process.argv.includes('--row'),reps=jack||row,replay=plank||reps;
const highKneesDraft=process.argv.includes('--high-knees-draft');
try{
  if(replay)await fixture.startTestService('tests/formal_plank_service.py');
  else{
  fixture.seed(`
import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Movement_Admin','movement-fixture-password-123');db.dispose()
`);
  fixture.start();
  }
  const context=await fixture.browser('Edge',{width:1280,height:900});await fixture.apiLogin(context,'Movement_Admin','movement-fixture-password-123');
  if(replay)await context.addInitScript(options=>{
    window.fixtureCamera={calls:0,video:null,playing:false,samples:[],timings:[],drawChecks:[],blobs:new WeakMap(),bitmaps:new WeakMap()};
    const originalBitmap=window.createImageBitmap.bind(window),originalDraw=CanvasRenderingContext2D.prototype.drawImage;
    window.createImageBitmap=(source,...args)=>originalBitmap(source,...args).then(bitmap=>{const tag=fixtureCamera.blobs.get(source);if(tag)fixtureCamera.bitmaps.set(bitmap,tag);return bitmap});
    CanvasRenderingContext2D.prototype.drawImage=function(source,...args){
      if(fixtureCamera.playing&&this.canvas.id==='camera-canvas'){
        const tag=fixtureCamera.bitmaps.get(source);fixtureCamera.drawChecks.push({matched:Boolean(tag&&tag.frame_seq===Number(this.canvas.dataset.frameSeq)&&tag.captured_ms===Number(this.canvas.dataset.capturedMs)),seq:Number(this.canvas.dataset.frameSeq),stamp:Number(this.canvas.dataset.capturedMs),usable:this.canvas.dataset.framingUsable==='1'});
        if(fixtureCamera.drawChecks.length>600)fixtureCamera.drawChecks.shift();
      }
      return originalDraw.call(this,source,...args);
    };
    const originalFetch=window.fetch.bind(window);
    window.fetch=(input,options)=>{
      const began=performance.now();
      if(typeof input==='string'&&input.includes('/api/v1/sessions/')&&input.endsWith('/frames')&&options?.body instanceof FormData){
        const metadata=JSON.parse(options.body.get('metadata'));fixtureCamera.blobs.set(options.body.get('frame'),metadata);
        if(fixtureCamera.playing){fixtureCamera.samples.push({seq:metadata.frame_seq,stamp:metadata.captured_ms});if(fixtureCamera.samples.length>600)fixtureCamera.samples.shift()}
      }
      return originalFetch(input,options).then(response=>{
        if(fixtureCamera.playing&&typeof input==='string'&&input.includes('/api/v1/sessions/')&&(input.endsWith('/frames')||input.includes('/updates'))){
          fixtureCamera.timings.push({kind:input.endsWith('/frames')?'submit':'update',elapsed:performance.now()-began});
          if(fixtureCamera.timings.length>1200)fixtureCamera.timings.shift();
        }
        return response;
      });
    };
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{
      fixtureCamera.calls++;const source=document.createElement('video');source.muted=true;source.playsInline=true;source.preload='auto';source.src=options.url;
      await new Promise((resolve,reject)=>{source.onloadeddata=resolve;source.onerror=()=>reject(new Error('开发原片未加载'))});fixtureCamera.video=source;
      const canvas=document.createElement('canvas');canvas.width=options.width;canvas.height=options.height;const draw=()=>canvas.getContext('2d').drawImage(source,0,0,options.width,options.height);draw();
      const stream=canvas.captureStream(30),timer=setInterval(draw,33),track=stream.getVideoTracks()[0],stop=track.stop.bind(track);
      Object.defineProperty(track,'getSettings',{value:()=>({width:options.width,height:options.height,frameRate:30,deviceId:'licensed-replay'})});
      Object.defineProperty(track,'stop',{value:()=>{clearInterval(timer);source.pause();stop()}});return stream;
    }});
    Object.defineProperty(navigator.mediaDevices,'enumerateDevices',{value:async()=>[{kind:'videoinput',deviceId:'licensed-replay',label:'公开视频回放 · 非现场相机'}]});
  },row?{url:'/__fixture/barbell-row.mp4',width:516,height:290}:jack?{url:'/__fixture/jumping-jack.mp4',width:640,height:480}:{url:'/__fixture/plank.mp4',width:524,height:288});
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  if(replay){
    const action=row?'barbell_bent_over_row':jack?'jumping_jack':'forearm_plank',prefix=row?'barbell-row':jack?'jumping-jack':'plank';
    await page.goto(fixture.origin+'/prepare?action='+action);assert.equal(await page.evaluate(()=>fixtureCamera.calls),0);
    await page.getByText(reps?'可记录可观察的完整周期；独立计数准确性尚未验证，具体纠错尚未开放。':'可累计可确认的前臂支撑时间；保持时间准确性尚未独立验证，具体纠错尚未开放。',{exact:true}).waitFor();
    await page.locator('#camera-toggle').click();await page.waitForFunction(()=>!document.querySelector('#realtime-start').disabled);
    await page.locator('#realtime-start').click();await page.waitForURL('**/realtime/*');const sid=new URL(page.url()).pathname.split('/').at(-1);
    await page.locator('#live-state').filter({hasText:'分析中'}).waitFor();await page.evaluate(()=>{fixtureCamera.playing=true;return fixtureCamera.video.play()});
    await page.waitForFunction(()=>fixtureCamera.video.ended,{},{timeout:row?45000:25000});await page.waitForTimeout(500);
    const state=(await(await context.request.get(fixture.origin+'/api/v1/sessions/'+sid)).json()).data;
    const samples=await page.evaluate(()=>{fixtureCamera.playing=false;return fixtureCamera.samples});
    const draws=await page.evaluate(()=>fixtureCamera.drawChecks);
    const intervals=draws.slice(1).map((sample,i)=>sample.stamp-draws[i].stamp),span=draws.length>1?draws.at(-1).stamp-draws[0].stamp:0;
    const processedFps=span>0?(draws.length-1)*1000/span:0,fps=span>0?draws.slice(1).filter(frame=>frame.usable).length*1000/span:0;
    const timings=await page.evaluate(()=>fixtureCamera.timings),timingSummary=Object.fromEntries(['submit','update'].map(kind=>{const values=timings.filter(v=>v.kind===kind).map(v=>v.elapsed);return [kind,{count:values.length,meanMs:values.reduce((a,b)=>a+b,0)/(values.length||1)}]}));
    if(reps){
      console.log(JSON.stringify({stage:'camera_observed',count:state.total_count,fps,samples:samples.length,processedFrames:draws.length,frameAligned:draws.every(d=>d.matched),maxGapMs:Math.max(...intervals),timingSummary,lastModelMs:state.latest_frame?.processing_ms,display:await page.locator('#submission-settings').textContent()}));
      assert.equal(state.total_count,3,JSON.stringify({count:state.total_count,analysis:state.latest_frame?.analysis,fps}));assert(fps>=15,'短片有效处理采样不足15fps');
      assert(draws.length>0&&draws.every(d=>d.matched),'显示图像与骨架结果没有按相同帧绑定');
      assert(intervals.every(gap=>gap>0&&gap<=(row?500:200)),'处理采样包含过长间隙');assert.equal(await page.locator('#live-unit').textContent(),'次');
    }else{
    assert(state.hold_ms>=5000&&state.hold_ms<=11000,JSON.stringify({hold:state.hold_ms,analysis:state.latest_frame?.analysis}));
    assert.equal(state.total_count,0);assert((await page.locator('#live-counting-note').textContent()).includes('保持时间'));
    assert.equal(await page.locator('#live-unit').textContent(),'秒');
    }
    await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(root,'data/verification/step-14/'+prefix+'-realtime.png'),fullPage:true});
    await page.locator('#live-end').click();await page.locator('[data-dialog-confirm]').click();await page.waitForURL('**/records/*');
    await page.locator('#detail-count').filter({hasText:reps?'3 次':'秒'}).waitFor();
    const detail=await page.locator('#detail-side').textContent();assert(detail.includes(reps?'次数仅供参考':'保持时间仅供参考'));
    const record=(await(await context.request.get(fixture.origin+'/api/v1/records/'+sid)).json()).data;
    if(reps){assert.equal(record.total_count,3);assert(record.events.every(e=>e.algorithm_version===(row?'barbell-row-development-1':'jumping-jack-development-2')))}
    else{assert.equal(record.hold_ms,state.hold_ms);assert(record.active_ms>=record.hold_ms);assert(record.intervals.every(i=>i.kind==='hold'))}
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
    await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(root,'data/verification/step-14/'+prefix+'-record.png'),fullPage:true});
    const result={checks:'passed',browser:await context.browser().version(),scope:action+' real Full camera replay and saved result',freshPoseInference:true,capabilityVersion:record.capability_version,count:record.total_count,holdMs:record.hold_ms,activeMs:record.active_ms,sourceSamples:samples.length,displayedProcessedFrames:draws.length,usableProcessedFrames:draws.filter(d=>d.usable).length,processedFps,effectiveFps:fps,maxGapMs:Math.max(...intervals),frameAlignmentChecked:draws.every(d=>d.matched),independentAccuracyTruth:false,fiveMinutePerformanceAcceptance:false};
    if(reps)fs.writeFileSync(path.join(root,'data/verification/step-14/'+prefix+'-camera.json'),JSON.stringify(result,null,2)+'\n');
    console.log(JSON.stringify(result));
  }else if(highKneesDraft){
    await page.goto(fixture.origin+'/prepare?action=high_knees');
    await page.locator('input[name="training-mode"][value="target"]').check();
    await page.locator('#target-sets').fill('1');await page.locator('#target-value').fill('6');await page.locator('#target-rest').fill('0');
    assert.equal(await page.locator('[for="target-value"]').textContent(),'每组总次数');
    assert((await page.locator('#target-side-help').textContent()).includes('左 3 次＋右 3 次＝总共 6 次'));
    assert.equal(await page.locator('#camera-preparation').getAttribute('data-counting-available'),'0');assert(await page.locator('#realtime-start').isDisabled());
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:path.join(root,'data/verification/step-14/high-knees-preparation.png'),fullPage:true});
    const sid=fixture.seed(`
import os,sys
from sqlalchemy import select
from fitness_app.database import Database
from fitness_app.records import SessionStore
from fitness_app.catalogue import get_action
from fitness_app import db_schema as s
sys.path.insert(0,'tests')
from test_high_knees import sequence,consume
frames=[]
for side in ('left','right')*3:frames.extend(sequence(side,len(frames)))
events,_=consume(frames)
db=Database(os.environ['FITNESS_DATABASE_PATH']);store=SessionStore(db)
with db.transaction(False) as c:
    uid=c.scalar(select(s.users.c.id).where(s.users.c.username_key=='movement_admin'))
    auth=c.scalar(select(s.auth_sessions.c.id).where(s.auth_sessions.c.user_id==uid,s.auth_sessions.c.revoked_at_ms.is_(None)))
created=store.create(uid,auth,dict(action_id='high_knees',capability_version=get_action('high_knees')['capability_version'],source='camera',mode='target',target={'sets':1,'value':6,'unit':'reps','rest_seconds':0}),s.new_id())
sid=created['session_id'];store.set_state(uid,sid,'running',s.new_id())
for i,event in enumerate(events):store.append_event(uid,sid,{**event,'event_seq':i+1,'set_index':1,'active_ms':event['end_ms']})
db.dispose();print(sid)
`).trim();
    await page.goto(fixture.origin+'/records/'+sid);await page.locator('#detail-count').filter({hasText:'6 次'}).waitFor();
    assert(/左侧 3 次.*右侧 3 次/.test(await page.locator('#detail-side').textContent()));
    assert((await page.locator('#group-rows').textContent()).includes('6 次（左 3 · 右 3）'));
    const record=(await(await context.request.get(fixture.origin+'/api/v1/records/'+sid)).json()).data;
    assert.equal(record.status,'completed');assert.equal(record.counting_available,false);
    await page.evaluate(()=>{const note=document.createElement('p');note.className='notice';note.textContent='工程验证：已知几何输入，仅检查总目标与分侧展示；当前动作尚未开放，不能当作真实识别效果。';document.querySelector('.record-detail').prepend(note)});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
    await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(root,'data/verification/step-14/high-knees-record.png'),fullPage:true});
    console.log(JSON.stringify({checks:'passed',scope:'high knees total target and side details from known geometry only',freshPoseInference:false,realInputVerified:false,productionAvailable:false}));
  }else{
  await page.goto(fixture.origin+'/prepare?action=reverse_lunge');
  await page.getByText('左右交替后撤弓步',{exact:true}).first().waitFor();
  await page.getByText('拍摄要求：斜前方或斜侧面，双腿、足跟与脚尖连续可见；正面深度不可辨或侧别遮挡时不计',{exact:true}).waitFor();
  await page.getByText('可记录可观察的完整周期；独立计数准确性尚未验证，具体纠错尚未开放。',{exact:true}).waitFor();
  assert(await page.locator('#realtime-start').isDisabled());
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(root,'data/verification/step-14/reverse-lunge-preparation.png'),fullPage:true});
  const sid=fixture.seed(`
import os,json
from sqlalchemy import select
from fitness_app.database import Database
from fitness_app.records import SessionStore
from fitness_app.catalogue import get_action
from fitness_app.paths import ROOT
from fitness_app import db_schema as s
db=Database(os.environ['FITNESS_DATABASE_PATH']);store=SessionStore(db)
with db.transaction(False) as c:
    uid=c.scalar(select(s.users.c.id).where(s.users.c.username_key=='movement_admin'))
    auth=c.scalar(select(s.auth_sessions.c.id).where(s.auth_sessions.c.user_id==uid,s.auth_sessions.c.revoked_at_ms.is_(None)))
events=json.loads((ROOT/'data/verification/step-14/reverse-lunge-development.json').read_text(encoding='utf-8'))['events']
created=store.create(uid,auth,dict(action_id='reverse_lunge',capability_version=get_action('reverse_lunge')['capability_version'],source='camera',mode='free',target=None),s.new_id())
sid=created['session_id'];store.set_state(uid,sid,'running',s.new_id())
for i,event in enumerate(events):store.append_event(uid,sid,{**event,'event_seq':i+1,'set_index':1,'active_ms':event['end_ms']})
store.set_state(uid,sid,'ended_early',s.new_id());db.dispose();print(sid)
`).trim();
  await page.goto(fixture.origin+'/records/'+sid);await page.locator('#detail-count').filter({hasText:'4 次'}).waitFor();
  const detail=await page.locator('#detail-side').textContent();assert(/左.*2.*右.*2/.test(detail),detail);assert(detail.includes('独立准确性尚未验证'));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(root,'data/verification/step-14/reverse-lunge-record.png'),fullPage:true});
  console.log(JSON.stringify({checks:'passed',browser:await context.browser().version(),scope:'new action preparation and saved bilateral result',freshPoseInference:false,independentAccuracyTruth:false}));
  }
}finally{await fixture.close()}
