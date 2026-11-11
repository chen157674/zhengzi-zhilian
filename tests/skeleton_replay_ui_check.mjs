// 骨架回放和删除使用真实记录API；关节为明确合成工程数据，不启用相机/分类器。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fixture=new BrowserFixture('round3-skeleton-ui'),errors=[];
const mode=process.env.FITNESS_SKELETON_UI_SCOPE||'full';
try{
 const seeded=JSON.parse(fixture.seed(`
import os,sys,json
from sqlalchemy import select
from fitness_app.paths import ROOT,configure_environment
configure_environment();sys.path.insert(0,str(ROOT/'tests'))
from fitness_app.app import create_app
from fitness_app.auth import initialize_admin
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.posture_rules import rules_for
from fitness_app.skeleton_store import SkeletonBuffer
from test_skeleton_store import frame
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']});db=app.extensions['database'];initialize_admin(db,'Skeleton_Admin','Skeleton-fixture-2026')
client=app.test_client();token=client.get('/api/v1/auth/csrf').json['data']['csrf_token'];assert client.post('/api/v1/auth/login',json={'username':'Skeleton_Admin','password':'Skeleton-fixture-2026'},headers={'X-CSRFToken':token}).status_code==200
with client.session_transaction() as state:auth=state['_user_id']
with db.transaction(False) as c:uid=c.scalar(select(s.auth_sessions.c.user_id).where(s.auth_sessions.c.id==auth))
store=app.extensions['session_store'];sid=store.create(uid,auth,{'action_id':'barbell_overhead_press','capability_version':get_action('barbell_overhead_press')['capability_version'],'source':'camera','mode':'free','target':None,'save_skeleton':True},s.new_id())['session_id'];store.set_state(uid,sid,'running',s.new_id(),auth_id=auth)
rule=rules_for('barbell_overhead_press')[0];item={k:rule[k] for k in ('code','rule_version','body_parts','suggestion')};item.update(status='issue',side='none',start_ms=0,end_ms=800,message='肘腕参考位置（工程夹具）',evidence_level='reference',evidence={'view':'front','affected_sides':['right']})
store.append_posture(uid,sid,1,[item],auth,5000)
buffer=SkeletonBuffer(app.extensions['skeleton_store'],uid,sid)
for i in range(51):
 f=frame(i*100,i)
 if 30<=i<=35:f['identity_accepted']=False
 if i>=40:f.update(width=480,height=640)
 buffer.push(f,{'state':'needs_adjustment','primary_cue':item} if i<9 else None)
buffer.close();store.set_state(uid,sid,'ended_early',s.new_id(),reason='fixture_end',auth_id=auth);print(json.dumps({'sid':sid}));db.dispose()
`));
 fixture.start();const browser=await fixture.browser('Edge',{width:390,height:844});await fixture.apiLogin(browser,'Skeleton_Admin','Skeleton-fixture-2026');
 await browser.addInitScript(()=>{window.cameraRequests=0;if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{window.cameraRequests++;throw new Error('Physical camera not used');};});
 const page=await browser.newPage();page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(fixture.origin+'/records/'+seeded.sid);const panel=page.locator('#record-skeleton-replay');await panel.waitFor();assert.equal(await panel.evaluate(node=>node.open),false);await panel.locator('summary').click();await panel.getByText('回放实际保存的关节轨迹，不会重新分析。',{exact:false}).waitFor();
 if(mode==='layout'){
  await page.waitForFunction(()=>document.querySelector('#record-skeleton-replay canvas').width===640);await page.evaluate(async()=>await Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{}))));await panel.evaluate(node=>node.scrollIntoView({block:'start',behavior:'instant'}));await page.screenshot({path:path.join(root,'第四轮优化/实现核对/骨架复盘-窄屏.png'),animations:'disabled'});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.setViewportSize({width:320,height:740});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);console.log('Final skeleton replay button layout and 320/390 width: passed.');
 }else{
 await panel.getByRole('button',{name:'播放骨架',exact:true}).click();await page.waitForFunction(()=>Number(document.querySelector('[aria-label="骨架回放位置"]').value)>150);await panel.getByRole('button',{name:'暂停骨架',exact:true}).click();
 const seek=async value=>{await page.locator('[aria-label="骨架回放位置"]').evaluate((input,time)=>{input.value=String(time);input.dispatchEvent(new Event('input',{bubbles:true}));},value);};
 await seek(3200);await panel.getByText(/此处没有可回放骨架，不补造动作/).waitFor();
 await seek(4200);await page.waitForFunction(()=>document.querySelector('#record-skeleton-replay canvas').height===640);await panel.getByRole('checkbox',{name:'镜像展示',exact:true}).check();
 await panel.getByRole('combobox',{name:'骨架提示位置',exact:true}).selectOption('0');await page.waitForFunction(()=>document.querySelector('#record-skeleton-replay canvas').width===640);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await panel.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(root,'第四轮优化/实现核对/骨架复盘-窄屏.png')});
 await page.setViewportSize({width:320,height:740});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await panel.getByRole('button',{name:'删除本条骨架',exact:true}).click();await page.locator('#confirm-dialog').getByRole('button',{name:'删除骨架',exact:true}).click();await panel.getByText('本记录的骨架已删除，训练摘要保留。',{exact:true}).waitFor();
 await page.reload();await page.locator('#record-skeleton-replay>summary').click();await page.locator('#record-skeleton-replay').getByText('本记录的骨架已删除，训练摘要保留。',{exact:true}).waitFor();
 const saved=(await(await browser.request.get(fixture.origin+'/api/v1/records/'+seeded.sid)).json()).data;assert.equal(saved.status,'ended_early');assert.equal(saved.saved_skeleton.frame_count,0);
 await page.goto(fixture.origin+'/realtime');assert.equal(await page.locator('#live-save-skeleton').isChecked(),false);await page.goto(fixture.origin+'/video');assert.equal(await page.locator('#video-save-skeleton').isChecked(),false);
 assert.equal(await page.evaluate(()=>window.cameraRequests),0);assert.deepEqual(errors,[]);
 console.log('Saved-only replay/play/seek/gap/mirror/portrait/issue location/delete/reload/default-off; 320/390 layout; no physical camera: passed.');
 }
}finally{await fixture.close();}
