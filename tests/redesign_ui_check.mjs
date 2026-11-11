// 新前端增量浏览器检查；使用项目内独立数据库和浏览器目录。
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fx=new BrowserFixture('ui-step21');
const visualOnly=process.argv.includes('--visual');const liveOnly=process.argv.includes('--live-only');
const output=path.join(root,'前端重设计','实现核对');
fs.mkdirSync(output,{recursive:true});
try{
 fx.seed(`import os,json
from fitness_app import create_app
from fitness_app.auth import initialize_admin
from fitness_app import db_schema as s
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database']
initialize_admin(db,'UI_Admin','ui-redesign-test-password')
client=app.test_client()
token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
client.post('/api/v1/auth/register',json={'username':'ui_user@qq.com','password':'ui-redesign-test-password','password_confirmation':'ui-redesign-test-password'},headers={'X-CSRFToken':token})
client.post('/api/v1/auth/login',json={'username':'ui_user@qq.com','password':'ui-redesign-test-password'},headers={'X-CSRFToken':token})
with client.session_transaction() as state:auth=state['_user_id']
from sqlalchemy import select
with db.transaction(False) as c:uid=c.scalar(select(s.users.c.id).where(s.users.c.username_key=='ui_user@qq.com'))
from fitness_app.catalogue import get_action
from fitness_app.posture_rules import rules_for,describe
store=app.extensions['session_store']
rule=rules_for('bodyweight_squat')[0]
for i in range(3):
 sid=store.create(uid,auth,{'action_id':'bodyweight_squat','capability_version':get_action('bodyweight_squat')['capability_version'],'source':'camera','mode':'free','target':None},s.new_id())['session_id']
 store.set_state(uid,sid,'running',s.new_id())
 for j,status in enumerate(['issue','not_observed','unassessable'],1):
  message,suggestion=describe(rule['code'],status,'cycle_evidence_missing' if status=='unassessable' else '')
  item=dict(code=rule['code'],status=status,start_ms=(j-1)*1000,end_ms=j*1000,rule_version=rule['rule_version'],body_parts=rule['body_parts'],evidence_level='reference',message=message,suggestion=suggestion,evidence={},reason='cycle_evidence_missing' if status=='unassessable' else '')
  store.append_event(uid,sid,dict(event_seq=j,set_index=1,side='none',start_ms=(j-1)*1000,end_ms=j*1000,algorithm_version='UI-fixture',feature_schema_version='UI-fixture',assessments=[item]))
 store.set_state(uid,sid,'ended_early',s.new_id())
db.dispose()
`);
 fx.start();
 const context=await fx.browser('Edge',{width:1536,height:1024}),page=await context.newPage();page.setDefaultTimeout(20000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const shot=async name=>{await page.evaluate(()=>scrollTo(0,0));await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(250);await page.screenshot({path:path.join(output,name+'.png'),fullPage:true,animations:'disabled'});};
 if(!liveOnly){await page.goto(fx.origin+'/register');await page.locator('.auth-visual img').evaluate(img=>img.decode());await shot('注册');
 await page.goto(fx.origin+'/login');await page.locator('.auth-visual img').evaluate(img=>img.decode());await shot('登录');
 if(visualOnly){await fx.apiLogin(context,'ui_user@qq.com','ui-redesign-test-password');await page.goto(fx.origin+'/');}else{
 await page.locator('#username').fill('ui_user@qq.com');await page.locator('#password').fill('ui-redesign-test-password');await page.locator('.primary-submit').click();await page.waitForURL(fx.origin+'/');}
 await page.waitForTimeout(300);await shot('首页');
 const nav=await page.locator('.topbar nav').boundingBox();assert(Math.abs(nav.x+nav.width/2-768)<4);
 await page.goto(fx.origin+'/account');await page.locator('#nickname').waitFor({state:'visible'});await page.waitForFunction(()=>!document.querySelector('#nickname').disabled);
 if(!visualOnly){await page.locator('#nickname').fill('林一同学');await page.locator('#profile-save').click();await page.waitForFunction(()=>document.querySelector('#profile-save').disabled&&document.querySelector('#profile-name').textContent==='林一同学');
 await page.locator('#avatar-file').setInputFiles(path.join(root,'fitness_app/static/avatars/avatar-01.png'));await page.locator('#avatar-dialog').waitFor({state:'visible'});await page.locator('#avatar-confirm').click();await page.locator('#avatar-dialog').waitFor({state:'hidden'});}await shot('个人中心');
 await page.goto(fx.origin+'/account/sessions');await page.waitForFunction(()=>document.querySelector('#sessions-list').textContent.includes('当前登录'));await shot('登录管理');
 await page.goto(fx.origin+'/account/password');await shot('修改密码');
 await page.goto(fx.origin+'/records');await page.waitForFunction(()=>!document.querySelector('#overview-content').hidden);assert.equal(await page.locator('#overview-ratio').textContent(),'50%');await shot('训练概览');
 await page.goto(fx.origin+'/records?view=list');await page.waitForFunction(()=>document.querySelector('#record-rows a'));await shot('训练记录');
 await page.locator('#record-rows a').first().click();await page.waitForFunction(()=>!document.querySelector('#quality-panel').hidden);assert(await page.locator('#detail-quality').textContent());await shot('记录详情');
 await page.goto(fx.origin+'/prepare?action=bodyweight_squat');await page.waitForTimeout(250);await shot('固定动作准备');await page.locator('#prepare-settings summary').click();await page.locator('input[name="training-mode"][value="target"]').check();await page.locator('#target-sets').fill('2');await page.locator('#target-value').fill('5');await page.locator('#target-rest').fill('30');assert(await page.locator('#realtime-start').isDisabled());
 await page.goto(fx.origin+'/video');await page.waitForTimeout(300);await shot('视频选择');
 await page.goto(fx.origin+'/realtime');assert.equal(await page.locator('#live-toggle').textContent(),'开启摄像头');await shot('实时准备');

 }else{await fx.apiLogin(context,'ui_user@qq.com','ui-redesign-test-password');await page.goto(fx.origin+'/realtime');}
 // 合成媒体流和受控响应验证页面状态机，不请求本机硬件、不作为算法准确率证据。
 await page.addInitScript(()=>{window.syntheticTracks=[];navigator.mediaDevices.getUserMedia=async()=>{const c=document.createElement('canvas');c.width=960;c.height=540;const x=c.getContext('2d');x.fillStyle='#e5e9ec';x.fillRect(0,0,960,540);x.fillStyle='#43505e';x.font='28px sans-serif';x.fillText('合成输入 · 界面状态验证',300,250);const stream=c.captureStream(15);window.syntheticTracks.push(...stream.getTracks());return stream;};navigator.mediaDevices.enumerateDevices=async()=>[];});
 let lastFrame=null,modeBodies=[];
 await page.route('**/api/v1/previews',async route=>{modeBodies.push(route.request().postDataJSON());await route.fulfill({json:{data:{preview_id:'00000000-0000-4000-8000-000000000001',preview_token:'synthetic-test-only',training_id:'00000000-0000-4000-8000-000000000003',stream_epoch:'00000000-0000-4000-8000-000000000002',status:'active'}}});});
 await page.route('**/api/v1/previews/*/frames',async route=>{const raw=route.request().postDataBuffer().toString();const match=raw.match(/\{"stream_epoch"[^\r\n]+\}/);lastFrame=JSON.parse(match[0]);await route.fulfill({status:202,json:{data:{accepted:true}}});});
 await page.route('**/api/v1/previews/00000000-0000-4000-8000-000000000001',async route=>{if(route.request().method()==='DELETE'){await route.fulfill({json:{data:{released:true,training:{save_state:'clean',saved_record_ids:[]}}}});return;}await route.fulfill({json:{data:{status:'active',training:{save_state:'clean',saved_record_ids:[]},result:lastFrame?{...lastFrame,recognition:{state:'recognized',action_id:'bodyweight_squat'},framing:{message:'合成取景',usable:true},skeleton:{available:false,frame_seq:lastFrame.frame_seq},coaching:{state:'needs_adjustment',message:'姿态反馈',primary_cue:{message:'合成提示：下蹲幅度偏小',suggestion:'合成建议，仅用于界面验证。'}}}:null}}});});
 await page.reload();await page.locator('#live-toggle').click();await page.waitForFunction(()=>document.querySelector('#live-cue-title').textContent.includes('合成提示'));
 assert.equal(modeBodies[0].recognition_mode,'auto');assert.equal(modeBodies[0].save_training,true);assert.equal(modeBodies[0].action_id,undefined);
 await shot('实时训练-受控输入');await page.locator('#live-fullscreen').click();await page.waitForTimeout(200);await shot('实时全屏-受控输入');await page.keyboard.press('Escape');
 await page.locator('#live-toggle').click();await page.waitForFunction(()=>document.querySelector('#live-toggle').textContent==='继续训练');assert(await page.evaluate(()=>window.syntheticTracks.every(t=>t.readyState==='ended')));await shot('实时暂停-受控输入');
 await page.locator('#live-selection').selectOption('barbell_overhead_press');await page.locator('#live-toggle').click();await page.waitForFunction(()=>document.querySelector('#live-toggle').textContent==='暂停');assert.equal(modeBodies.at(-1).action_id,'barbell_overhead_press');assert.equal(modeBodies.at(-1).training_id,'00000000-0000-4000-8000-000000000003');
 await page.locator('#live-finish').click();await page.locator('[data-dialog-confirm]').click();await page.locator('#live-summary').waitFor({state:'visible'});assert(await page.evaluate(()=>window.syntheticTracks.every(t=>t.readyState==='ended')));
 await page.goto(fx.origin+'/realtime');
 if(!liveOnly){await page.setViewportSize({width:390,height:844});await shot('实时准备-窄屏');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await page.goto(fx.origin+'/account');await page.waitForFunction(()=>!document.querySelector('#nickname').disabled);await shot('个人中心-窄屏');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('#main').evaluate(el=>getComputedStyle(el).animationName),'none');
 await fx.apiLogin(context,'UI_Admin','ui-redesign-test-password');await page.setViewportSize({width:1536,height:1024});await page.goto(fx.origin+'/admin/users');await page.waitForFunction(()=>document.querySelector('#account-rows').textContent.includes('ui_user@qq.com'));await shot('账号管理');
 }
 assert.deepEqual(errors,[]);console.log('PASS: routes, centered navigation, nickname/avatar, overview denominator, record detail, viewport and reduced motion; physical camera never requested.');
}finally{await fx.close();}
