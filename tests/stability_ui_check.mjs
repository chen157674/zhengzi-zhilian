// 真实本人记录API和可视化；统计由合成工程输入生成，不作为真人效果或模型精度证明。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fixture=new BrowserFixture('round3-stability-ui'),errors=[];
const mode=process.env.FITNESS_STABILITY_UI_SCOPE||'full';
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
from test_movement_stability import windows
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']});db=app.extensions['database'];initialize_admin(db,'Stability_Admin','Stability-fixture-2026')
c=app.test_client();token=c.get('/api/v1/auth/csrf').json['data']['csrf_token'];assert c.post('/api/v1/auth/login',json={'username':'Stability_Admin','password':'Stability-fixture-2026'},headers={'X-CSRFToken':token}).status_code==200
with c.session_transaction() as state:auth=state['_user_id']
with db.transaction(False) as conn:uid=conn.scalar(select(s.auth_sessions.c.user_id).where(s.auth_sessions.c.id==auth))
store=app.extensions['session_store']
def record(enrolled):
 sid=store.create(uid,auth,{'action_id':'dumbbell_shoulder_press','capability_version':get_action('dumbbell_shoulder_press')['capability_version'],'source':'camera','mode':'free','target':None},s.new_id(),stability=enrolled)['session_id'];store.set_state(uid,sid,'running',s.new_id(),auth_id=auth)
 if enrolled:
  for i,w in enumerate(windows()):app.extensions['stability_store'].append(uid,sid,i,w)
  app.extensions['stability_store'].finish(uid,sid)
 store.set_state(uid,sid,'ended_early',s.new_id(),reason='synthetic_observation_fixture',auth_id=auth);return sid
print(json.dumps({'current':record(True),'old':record(False)}));db.dispose()
`));
 fixture.start();const browser=await fixture.browser('Edge',{width:390,height:844});await fixture.apiLogin(browser,'Stability_Admin','Stability-fixture-2026');
 await browser.addInitScript(()=>{window.cameraRequests=0;if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{window.cameraRequests++;throw new Error('No physical camera in this check');};});
 const page=await browser.newPage();page.on('pageerror',error=>errors.push(String(error)));
 await page.goto(fixture.origin+'/records/'+seeded.current);const panel=page.locator('#record-stability');await panel.getByText('后半段增大。',{exact:true}).waitFor();
 if(mode==='layout'){
  await page.evaluate(async()=>await Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{}))));await panel.evaluate(node=>node.scrollIntoView({block:'start',behavior:'instant'}));await page.screenshot({path:path.join(root,'第五轮优化/实现核对/动作变化-手机.png'),animations:'disabled'});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.setViewportSize({width:320,height:740});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);console.log('Refined movement cards/units at 320/390 widths: passed.');
 }else{
 const card=panel.locator('.stability-card').first();assert.match(await card.textContent(),/可比 3/);assert.equal(await card.locator('details').count(),0);assert(!(await card.textContent()).includes('区间原测量极值'));
 await panel.getByText('动作变化曲线',{exact:true}).click();await page.waitForFunction(()=>{const c=document.querySelector('#record-stability canvas');return c&&[...c.getContext('2d').getImageData(0,0,c.width,c.height).data].some((v,i)=>i%4===3&&v);});
 assert(await panel.locator('canvas').evaluate(c=>[...c.getContext('2d').getImageData(0,0,c.width,c.height).data].some((value,i)=>i%4===3&&value)));
 await page.evaluate(async()=>await Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{}))));await panel.evaluate(node=>node.scrollIntoView({block:'start',behavior:'instant'}));await page.screenshot({path:path.join(root,'第五轮优化/实现核对/动作变化-手机.png'),animations:'disabled'});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.setViewportSize({width:320,height:740});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.reload();await page.locator('#record-stability').getByText('后半段增大。',{exact:true}).waitFor();
 await page.goto(fixture.origin+'/records/'+seeded.old);await page.waitForFunction(()=>document.querySelector('#detail-action')?.textContent!=='—');assert.equal(await page.locator('#record-stability:visible').count(),0);
 assert.equal(await page.evaluate(()=>window.cameraRequests),0);assert.deepEqual(errors,[]);console.log('Owned saved statistics/change cards/raw range/chart/reload/old no-data; 320/390 layout, no camera/provider: passed.');
 }
}finally{await fixture.close();}
