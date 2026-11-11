// 12 默认 Full + 默认周期资源的摄像头回放链，不是现场摄像头或准确率验收。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step12'),evidence=path.join(root,'data/verification/step-12');
const recordOnly=process.argv.includes('--record-only');
const endVersionChange=process.argv.includes('--end-version-change');
const finishOnly=process.argv.includes('--finish-only')||recordOnly||endVersionChange;
const staleVideo=process.argv.includes('--stale-video');
let context,page,sid,expectedCount;const errors=[];
try{
  await fixture.startTestService('tests/formal_counting_service.py');context=await fixture.browser('Edge',{width:1440,height:1000});await fixture.apiLogin(context,'Count_User','count-fixture-password-123');
  await context.addInitScript(()=>{
    window.fixtureCamera={calls:0,video:null};
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{
      fixtureCamera.calls++;const source=document.createElement('video');source.muted=true;source.playsInline=true;source.preload='auto';source.src='/__fixture/counting.mp4';
      await new Promise((resolve,reject)=>{source.onloadeddata=resolve;source.onerror=()=>reject(new Error('回放源未加载'))});fixtureCamera.video=source;
      const canvas=document.createElement('canvas');canvas.width=960;canvas.height=540;const draw=()=>canvas.getContext('2d').drawImage(source,0,0,960,540);draw();
      const stream=canvas.captureStream(30),timer=setInterval(draw,33),track=stream.getVideoTracks()[0],stop=track.stop.bind(track);
      Object.defineProperty(track,'getSettings',{value:()=>({width:960,height:540,frameRate:30,deviceId:'licensed-replay'})});
      Object.defineProperty(track,'stop',{value:()=>{clearInterval(timer);source.pause();stop()}});return stream;
    }});
    Object.defineProperty(navigator.mediaDevices,'enumerateDevices',{value:async()=>[{kind:'videoinput',deviceId:'licensed-replay',label:'MIT 视频回放 · 非现场相机'}]});
  });
  page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  if(staleVideo){
    const encoded=fixture.seed("import sys,base64\nsys.path.insert(0,'tests')\nfrom test_formal_media import sample_mp4\nprint(base64.b64encode(sample_mp4()).decode())").trim();
    await page.goto(fixture.origin+'/video');await page.locator('#video-action').selectOption('bodyweight_squat');
    await page.locator('#video-file').setInputFiles({name:'D-MEDIA.mp4',mimeType:'video/mp4',buffer:Buffer.from(encoded,'base64')});
    await page.waitForFunction(()=>!document.querySelector('#video-start').disabled);const originalSource=await page.locator('#video-player').getAttribute('src');
    const current=(await(await context.request.get(fixture.origin+'/api/v1/actions/bodyweight_squat')).json()).data;
    await page.locator('#video-action').evaluate(select=>{select.selectedOptions[0].dataset.capability='obsolete-before-upgrade'});
    await page.locator('#video-start').click();await page.locator('#video-error').filter({hasText:'动作设置已更新'}).waitFor();
    await page.waitForFunction(()=>!document.querySelector('#video-start').disabled);
    assert.equal(await page.locator('#video-player').getAttribute('src'),originalSource);assert.equal(await page.locator('#video-action').evaluate(select=>select.selectedOptions[0].dataset.capability),current.capability_version);
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('fitness-video-pending:'+document.body.dataset.userId)),null);
    assert.equal((await(await context.request.get(fixture.origin+'/api/v1/records')).json()).data.total,0);
    assert.equal(await page.evaluate(()=>fixtureCamera.calls),0);assert.deepEqual(errors,[]);
    fs.mkdirSync(evidence,{recursive:true});await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(evidence,'video-preparation.png'),fullPage:true});
    console.log(JSON.stringify({checks:'passed',scope:'stale video capability refresh without losing file or auto-starting',freshPoseInference:false}));
  }else{
  if(finishOnly){
    sid=fixture.seed(`
import os,json
from sqlalchemy import select
from fitness_app.database import Database
from fitness_app.records import SessionStore
from fitness_app.catalogue import get_action
from fitness_app.paths import ROOT
from fitness_app import db_schema as s
db=Database(os.environ['FITNESS_DATABASE_PATH']);db.inspect_identity();store=SessionStore(db)
with db.transaction(False) as c:
    uid=c.scalar(select(s.users.c.id).where(s.users.c.username_key=='count_user'))
    auth=c.scalar(select(s.auth_sessions.c.id).where(s.auth_sessions.c.user_id==uid,s.auth_sessions.c.revoked_at_ms.is_(None)))
diagnosis=json.loads((ROOT/'data/verification/step-12/development-cycles.json').read_text(encoding='utf-8'))
event=next(case['events'][0] for case in diagnosis['actions']['bodyweight_squat']['cases'] if case['events'])
created=store.create(uid,auth,dict(action_id='bodyweight_squat',capability_version=get_action('bodyweight_squat')['capability_version'],source='camera',mode='free',target=None),s.new_id())
sid=created['session_id'];store.set_state(uid,sid,'running',s.new_id())
store.append_event(uid,sid,{**event,'event_seq':1,'set_index':1,'active_ms':event['end_ms']-event['start_ms']})
store.set_state(uid,sid,'${recordOnly?'ended_early':'paused'}',s.new_id());db.dispose();print(sid)
`).trim();
    expectedCount=1;
    if(recordOnly)await page.goto(fixture.origin+'/records/'+sid);
    else{await page.goto(fixture.origin+'/realtime/'+sid);await page.locator('#live-claim').click();await page.waitForFunction(()=>!document.querySelector('#live-end').disabled)}
  }else{
  await page.goto(fixture.origin+'/prepare?action=bodyweight_squat');assert.equal(await page.evaluate(()=>fixtureCamera.calls),0);
  await page.getByText('可记录可观察的完整周期；独立计数准确性尚未验证，具体纠错尚未开放。',{exact:true}).waitFor();
  await page.locator('#camera-toggle').click();await page.waitForFunction(()=>!document.querySelector('#realtime-start').disabled);
  assert.equal((await(await context.request.get(fixture.origin+'/api/v1/records')).json()).data.total,0);
  await page.locator('#realtime-start').click();await page.waitForURL('**/realtime/*');sid=new URL(page.url()).pathname.split('/').at(-1);
  await page.locator('#live-state').filter({hasText:'分析中'}).waitFor();await page.evaluate(()=>fixtureCamera.video.play());
  await page.waitForFunction(()=>fixtureCamera.video.ended,{},{timeout:35000});await page.waitForTimeout(800);
  const state=(await(await context.request.get(fixture.origin+'/api/v1/sessions/'+sid)).json()).data;
  assert(state.counting_available);assert(state.total_count>0,JSON.stringify({count:state.total_count,state:state.status,framing:state.latest_frame?.framing,analysis:state.latest_frame?.analysis}));
  expectedCount=state.total_count;
  await page.locator('#live-counting-note').waitFor();assert(!/尚未接入/.test(await page.locator('#live-feedback').textContent()));
  fs.mkdirSync(evidence,{recursive:true});await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(evidence,'realtime.png'),fullPage:true});
  }
  if(!recordOnly){
    await page.locator('#live-end').click();
    if(endVersionChange)fixture.seed(`
import os
from sqlalchemy import select
from fitness_app.database import Database
from fitness_app.records import SessionStore
from fitness_app import db_schema as s
db=Database(os.environ['FITNESS_DATABASE_PATH']);store=SessionStore(db)
with db.transaction(False) as c:uid=c.scalar(select(s.users.c.id).where(s.users.c.username_key=='count_user'))
store.set_state(uid,'${sid}','paused',s.new_id(),reason='D-FAULT version advanced while dialog open');db.dispose()
`);
    await page.locator('[data-dialog-confirm]').click();await page.waitForURL('**/records/*');
  }
  await page.locator('#detail-count').filter({hasText:expectedCount+' 次'}).waitFor();
  await page.locator('#detail-side').filter({hasText:'独立准确性尚未验证'}).waitFor();
  const saved=(await(await context.request.get(fixture.origin+'/api/v1/records/'+sid)).json()).data;
  assert.equal(saved.total_count,expectedCount);assert.equal(saved.count_accuracy_status,'evidence_insufficient');assert(saved.events.every(event=>event.algorithm_version.startsWith('cycles-four-actions-')));assert.deepEqual(errors,[]);
  await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(evidence,'record-result.png'),fullPage:true});
  console.log(JSON.stringify({browser:await context.browser().version(),checks:'passed',scope:endVersionChange?'end after version advances inside dialog':recordOnly?'record render only':finishOnly?'end-and-record-only, cached real event':'existing MIT demo via camera replay',freshPoseInference:!finishOnly,observedCount:saved.total_count,accuracyTruth:false}));
  }
}catch(error){console.log(JSON.stringify({errors,diagnostic:page?await page.locator('#live-error').textContent({timeout:1000}).catch(()=>null):null}));throw error}
finally{await fixture.close()}
