// 真实页面/记录API，种入明确合成观察；不把本检查当作摄像头或姿态准确率证明。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fixture=new BrowserFixture('round3-retest-ui'),errors=[];
const mode=process.env.FITNESS_RETEST_UI_SCOPE||'record';
try{
 const ids=JSON.parse(fixture.seed(`
import os,json
from sqlalchemy import select
from fitness_app.paths import configure_environment
configure_environment()
from fitness_app.app import create_app
from fitness_app.auth import initialize_admin
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.posture_rules import rules_for
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Retest_Admin','Retest-fixture-2026')
client=app.test_client();token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
assert client.post('/api/v1/auth/login',json={'username':'Retest_Admin','password':'Retest-fixture-2026'},headers={'X-CSRFToken':token}).status_code==200
with client.session_transaction() as state:auth=state['_user_id']
with db.transaction(False) as c:uid=c.scalar(select(s.auth_sessions.c.user_id).where(s.auth_sessions.c.id==auth))
store=app.extensions['session_store'];rule=rules_for('barbell_overhead_press')[0]
def record(status,ref=None):
 cfg={'action_id':'barbell_overhead_press','capability_version':get_action('barbell_overhead_press')['capability_version'],'source':'camera','mode':'free','target':None}
 if ref:cfg['retest_origin']=ref
 sid=store.create(uid,auth,cfg,s.new_id())['session_id'];store.set_state(uid,sid,'running',s.new_id(),auth_id=auth)
 item={k:rule[k] for k in ('code','rule_version','body_parts','suggestion')}
 item.update(status=status,side='none',start_ms=0,end_ms=2500,message='肩推肘腕对齐工程观察',reason='',evidence_level='reference',evidence={'view':'front','value':.4})
 store.append_posture(uid,sid,1,[item],auth,2500);store.set_state(uid,sid,'ended_early',s.new_id(),reason='fixture_end',auth_id=auth)
 return sid
source=record('issue');ref=app.extensions['retests'].options(uid,source,auth)['options'][0]['origin'];target=record('not_observed',ref) if os.environ.get('FITNESS_RETEST_UI_SCOPE')!='live' else None
print(json.dumps({'source':source,'target':target}));db.dispose()
`));
 fixture.start();const browser=await fixture.browser('Edge',{width:390,height:844});await fixture.apiLogin(browser,'Retest_Admin','Retest-fixture-2026');
 await browser.addInitScript(()=>{window.cameraRequests=0;if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{window.cameraRequests++;throw new Error('No physical camera in this UI check');};});
 const page=await browser.newPage();page.on('pageerror',error=>errors.push(String(error)));
 if(mode==='live'){
  const option=(await(await browser.request.get(fixture.origin+'/api/v1/records/'+ids.source+'/retest-options')).json()).data.options[0];let packet=null;
  const preview={preview_id:'11111111-1111-4111-8111-111111111111',preview_token:'fixture',stream_epoch:'22222222-2222-4222-8222-222222222222',training_id:'33333333-3333-4333-8333-333333333333'};
  const training={save_state:'clean',saved_record_ids:[ids.source],saved_record_id:ids.source};
  await page.route('**/api/v1/previews',route=>route.fulfill({status:201,json:{data:preview}}));
  await page.route('**/api/v1/previews/*/frames',route=>{const body=route.request().postDataBuffer().toString('latin1');packet=JSON.parse(body.match(/name="metadata"\r\n\r\n([^\r]+)\r\n/)[1]);return route.fulfill({status:202,json:{data:{accepted:true,frame_seq:packet.frame_seq}}});});
  await page.route('**/api/v1/previews/*',route=>route.fulfill({json:{data:route.request().method()==='DELETE'?{released:true,training}:{...preview,status:'active',training,result:{...packet,skeleton:{available:false},recognition:{state:'recognized',available:true,action_id:'barbell_overhead_press'},automatic_analysis:{action_id:'barbell_overhead_press',segment_id:'fixture-segment',saved_record_id:ids.source},coaching:{state:'needs_adjustment',primary_cue:{code:option.code,side:option.side,rule_version:option.rule_version,evidence:{view:option.view},message:option.title,suggestion:option.suggestion}}}}}}));
  await page.goto(fixture.origin+'/realtime');
  await page.evaluate(()=>{const surface=document.createElement('canvas');surface.width=640;surface.height=480;const ctx=surface.getContext('2d');ctx.fillStyle='#ddd';ctx.fillRect(0,0,640,480);const stream=surface.captureStream(10);for(const track of stream.getTracks()){const stop=track.stop.bind(track);track.stop=()=>{sessionStorage.setItem('retest-fake-stream-stopped','yes');stop();};}window.fakeFrames=setInterval(()=>{ctx.fillRect(0,0,640,480);},80);navigator.mediaDevices.getUserMedia=async()=>{window.cameraRequests++;return stream;};navigator.mediaDevices.enumerateDevices=async()=>[];});
  await page.locator('#live-toggle').click();await page.locator('#live-retest-start').waitFor({state:'visible'});await page.locator('#live-retest-start').click();
  await page.waitForURL('**/realtime?retest_record=*');await page.locator('#live-retest-panel').filter({hasText:'尚未开始采集'}).waitFor();
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('retest-fake-stream-stopped')),'yes');assert.equal(await page.evaluate(()=>window.cameraRequests),0);assert.equal(await page.locator('#live-selection').inputValue(),'');assert.deepEqual(errors,[]);
  console.log('Live cue entry closes canvas-only synthetic stream, binds saved cue, returns to explicit start with auto recognition; no physical camera/provider: passed.');
 }else{
 await page.goto(fixture.origin+'/records/'+ids.source);await page.locator('#record-retest').getByRole('link',{name:'验证这项调整',exact:true}).click();
 await page.waitForURL('**/realtime?retest_record=*');await page.locator('#live-retest-panel').filter({hasText:'尚未开始采集'}).waitFor();
 assert.equal(await page.locator('#live-selection').inputValue(),'');assert.equal(await page.locator('#live-view').inputValue(),'auto');assert.equal(await page.evaluate(()=>window.cameraRequests),0);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.setViewportSize({width:320,height:740});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.goto(fixture.origin+'/records/'+ids.target);await page.locator('#record-retest').filter({hasText:'未再次观察到这一项偏差'}).waitFor();
 assert.equal(await page.locator('#record-retest').getByText('同项有效观察 2.5 秒；未知区间不算正常。',{exact:true}).count(),1);
 await page.setViewportSize({width:390,height:844});await page.locator('#record-retest').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(root,'第四轮优化/实现核对/复测结果-窄屏.png')});
 await page.reload();await page.locator('#record-retest').filter({hasText:'未再次观察到这一项偏差'}).waitFor();
 assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>window.cameraRequests),0);
 console.log('Real owned record API→one-item retest entry→auto action and view retained→reference result→reload; 320/390 layout, no camera opened: passed.');
 }
}finally{await fixture.close();}
