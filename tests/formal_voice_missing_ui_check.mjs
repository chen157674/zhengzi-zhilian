// 18-D真实缺文件界面；不伪造声音或开启摄像头。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step18-voice');const errors=[];
try{
  fixture.seed(`
import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Voice_Admin','voice-fixture-password-123');db.dispose()
`);fixture.start();const context=await fixture.browser('Edge',{width:1440,height:1000});await fixture.apiLogin(context,'Voice_Admin','voice-fixture-password-123');
  const sid=fixture.seed(`
import os
from sqlalchemy import select
from fitness_app.database import Database
from fitness_app.records import SessionStore
from fitness_app.catalogue import get_action
from fitness_app import db_schema as s
db=Database(os.environ['FITNESS_DATABASE_PATH']);store=SessionStore(db)
with db.transaction(False) as c:
 uid=c.scalar(select(s.users.c.id).where(s.users.c.username_key=='voice_admin'))
 auth=c.scalar(select(s.auth_sessions.c.id).where(s.auth_sessions.c.user_id==uid,s.auth_sessions.c.revoked_at_ms.is_(None)))
data=store.create(uid,auth,dict(action_id='bodyweight_squat',capability_version=get_action('bodyweight_squat')['capability_version'],source='camera',mode='free',target=None),s.new_id())
store.set_state(uid,data['session_id'],'running',s.new_id());db.dispose();print(data['session_id'])
`).trim();
  await context.addInitScript(()=>{window.voiceMediaCalls=0;window.voiceAudioCreations=0;const OriginalAudio=window.Audio;window.Audio=function(...args){window.voiceAudioCreations++;return new OriginalAudio(...args)};Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{window.voiceMediaCalls++;throw new Error('只读缺文件页面不应开相机')}})});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(fixture.origin+'/realtime/'+sid);
  await page.locator('#live-voice-status').filter({hasText:'语音文件未准备或未核对'}).waitFor();assert(await page.locator('#live-voice').isDisabled());assert(!(await page.locator('#live-voice').isChecked()));
  assert(await page.locator('#live-feedback').isVisible());assert.equal(await page.evaluate(()=>voiceMediaCalls),0);assert.equal(await page.evaluate(()=>voiceAudioCreations),0);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
  await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo({top:0,behavior:'instant'})});const out=path.join(root,'data/verification/step-18');
  await page.screenshot({path:path.join(out,'voice-missing.png'),fullPage:true});
  const result={checks:'passed',scope:'actual absent-audio fallback and readonly realtime page',audioFilesPresent:false,playbackVerified:false,cameraCalls:0,audioElementsCreated:0,modelInference:false};
  fs.writeFileSync(path.join(out,'voice-missing-ui.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
}finally{await fixture.close()}
