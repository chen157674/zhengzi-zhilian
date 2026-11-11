// 19仅补Chrome 1280px对18新增素材／语音区的组合显示；不重跑推理或旧交互矩阵。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step19-layout');const errors=[];
try{
  fixture.seed(`
import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Layout_Admin','layout-fixture-password-123');db.dispose()
`);fixture.start();const context=await fixture.browser('Chrome',{width:1280,height:900});
  await context.addInitScript(()=>{window.layoutCameraCalls=0;Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{window.layoutCameraCalls++;throw new Error('组合显示检查不应开相机')}})});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));const out=path.join(root,'data/verification/step-19');
  const shot=async name=>{assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo({top:0,behavior:'instant'})});await page.screenshot({path:path.join(out,name),fullPage:true})};
  await page.goto(fixture.origin+'/');await page.waitForFunction(()=>document.querySelector('#showcase-canvas')?.dataset.frameSeq!==undefined);
  await page.locator('#showcase-video').evaluate(v=>v.pause());await shot('chrome-home.png');
  await fixture.apiLogin(context,'Layout_Admin','layout-fixture-password-123');await page.goto(fixture.origin+'/actions');
  for(let i=0;i<2;i++){await page.waitForFunction(()=>[...document.querySelectorAll('.action-card:not([hidden]) img')].every(x=>x.complete&&x.naturalWidth>0));assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));if(!i)await page.locator('#next-page').click()}
  await shot('chrome-covers.png');
  await page.goto(fixture.origin+'/video?demo=1');await page.waitForFunction(()=>!document.querySelector('#video-start').disabled);await page.locator('#video-demo-credit summary').click();await shot('chrome-video-preparation.png');
  await page.locator('#video-remove').click();
  const sid=fixture.seed(`
import os
from sqlalchemy import select
from fitness_app.database import Database
from fitness_app.records import SessionStore
from fitness_app.catalogue import get_action
from fitness_app import db_schema as s
db=Database(os.environ['FITNESS_DATABASE_PATH']);store=SessionStore(db)
with db.transaction(False) as c:
 uid=c.scalar(select(s.users.c.id).where(s.users.c.username_key=='layout_admin'))
 auth=c.scalar(select(s.auth_sessions.c.id).where(s.auth_sessions.c.user_id==uid,s.auth_sessions.c.revoked_at_ms.is_(None)))
d=store.create(uid,auth,dict(action_id='bodyweight_squat',capability_version=get_action('bodyweight_squat')['capability_version'],source='camera',mode='free',target=None),s.new_id())
store.set_state(uid,d['session_id'],'running',s.new_id());db.dispose();print(d['session_id'])
`).trim();
  await page.goto(fixture.origin+'/realtime/'+sid);await page.locator('#live-voice-status').filter({hasText:'未准备或未核对'}).waitFor();assert(await page.locator('#live-voice').isDisabled());await shot('chrome-voice-missing.png');
  assert.equal(await page.evaluate(()=>layoutCameraCalls),0);assert.deepEqual(errors,[]);
  const result={checks:'passed',browser:await context.browser().version(),viewport:{width:1280,height:900},scope:'new home/covers/demo preparation/absent voice layout only',cameraCalls:0,modelInference:false,realAudioPlayback:false};
  fs.writeFileSync(path.join(out,'chrome-layout.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
}finally{await fixture.close()}
