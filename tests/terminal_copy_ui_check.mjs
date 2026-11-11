// 仅复用已保存的真实弯举事件检查结束页；不重新播放、申请设备或运行模型。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const output=path.join(root,'data/verification/step-19/curl-target-browser.json'),report=JSON.parse(fs.readFileSync(output,'utf8'));
assert.equal(report.status,'passed');assert.equal(report.record.groups.length,1);assert(report.record.events.every(e=>e.set_id===report.record.groups[0].id));
const codeHash=crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'fitness_app/static/realtime.js'))).digest('hex');
assert(report.terminal_ui_check?.source_sha256!==codeHash,'当前文案已验证，不重复启动浏览器');
const failureOnly=process.argv.includes('--save-failure-only');if(failureOnly)assert.equal(report.terminal_ui_check?.status,'passed');
const fixture=new BrowserFixture('ui-step19-curl-target');let frames=0;
try{
  const sid=fixture.seed(`
import os,json,sys
from sqlalchemy import select
from fitness_app import create_app,db_schema as s
from fitness_app.auth import initialize_admin
from fitness_app.catalogue import get_action
from fitness_app.records import SessionStore
events=json.load(sys.stdin)
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Curl_Admin','curl-target-fixture-password-123')
client=app.test_client();csrf=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
assert client.post('/api/v1/auth/login',json={'username':'Curl_Admin','password':'curl-target-fixture-password-123'},headers={'X-CSRFToken':csrf}).status_code==200
with db.transaction(False) as c:
 uid=c.scalar(select(s.users.c.id).where(s.users.c.username_key=='curl_admin'))
 auth=c.scalar(select(s.auth_sessions.c.id).where(s.auth_sessions.c.user_id==uid,s.auth_sessions.c.revoked_at_ms.is_(None)))
store=SessionStore(db)
created=store.create(uid,auth,dict(action_id='standing_dumbbell_curl',capability_version=get_action('standing_dumbbell_curl')['capability_version'],source='camera',mode='target',target={'sets':1,'value':2,'unit':'per_side_reps','rest_seconds':0}),s.new_id())
sid=created['session_id'];store.set_state(uid,sid,'running',s.new_id())
for event in events:
 payload={key:event[key] for key in ('event_seq','side','start_ms','end_ms','algorithm_version','feature_schema_version','assessments')};payload['set_index']=1;payload['active_ms']=event['end_ms'];store.append_event(uid,sid,payload)
db.dispose();print(sid)
`,report.record.events).trim();
  fixture.start();const context=await fixture.browser('Edge',{width:1440,height:1000});await fixture.apiLogin(context,'Curl_Admin','curl-target-fixture-password-123');
  await context.addInitScript(()=>{window.cameraCalls=0;Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{cameraCalls++;throw Error('本检查禁止取景')}})});
  const page=await context.newPage();page.on('request',r=>{if(/\/api\/v1\/(previews|sessions\/[^/]+\/frames)/.test(new URL(r.url()).pathname))frames++});
  let state='completed',saveState='clean';
  await page.route('**/api/v1/sessions/'+sid,async route=>{const response=await route.fetch(),body=await response.json();body.data.status=state;body.data.save_state=saveState;await route.fulfill({response,json:body})});
  const previousCheck=report.terminal_ui_check;
  const cases=failureOnly?previousCheck.cases.filter(c=>c.save_state!=='save_failed'):[];
  for(const [value,saved] of (failureOnly?[['completed','save_failed']]:[['completed','clean'],['ended_early','clean'],['interrupted','clean'],['completed','save_failed']])){
    state=value;saveState=saved;await page.goto(fixture.origin+'/realtime/'+sid);
    await page.waitForFunction(({state,failed})=>document.querySelector('#live-state').textContent===(failed?'分析已冻结':state==='completed'?'已完成':state==='ended_early'?'提前结束':'已中断'),{state,failed:saved==='save_failed'});
    const visible=await page.evaluate(()=>({heading:document.querySelector('#camera-empty h2').textContent,placeholder:document.querySelector('#camera-empty p').textContent,feedback:document.querySelector('#live-feedback').textContent,message:document.querySelector('#camera-message').textContent,note:document.querySelector('#live-controller-note').textContent,cameraCalls,amount:document.querySelector('#live-amount').textContent,sides:document.querySelector('#live-sides').textContent}));
    assert.equal(visible.cameraCalls,0);assert.equal(await page.locator('#live-resume').isVisible(),false);assert.equal(await page.locator('#live-claim').isVisible(),false);
    if(saved==='save_failed'){assert(visible.placeholder.includes('处理保存'));assert(!visible.placeholder.includes('继续'));assert(visible.feedback.includes('处理保存'));assert(!visible.feedback.includes('结果已保存'));assert.equal(await page.locator('#live-result').isVisible(),false)}
    else{assert.equal(visible.heading,'本次分析已结束');assert(!/等待|继续分析|授权/.test(visible.feedback+visible.placeholder+visible.message));assert(visible.note.includes('不能继续'));assert.equal(await page.locator('#live-result').isVisible(),true)}
    cases.push({status:state,save_state:saveState,...visible});
    if(state==='completed'&&saved==='clean'){
      assert.equal(visible.amount,'5');assert.equal(visible.sides,'左 2 次 · 右 3 次');await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(root,'data/verification/step-19/curl-target-completed.png'),fullPage:true});
    }
  }
  assert.equal(frames,0);report.terminal_ui_check={status:'passed',source_sha256:codeHash,scope:'Saved real event replay into isolated store; terminal/save-failure response variants only test rendering, no model or camera',pose_requests:0,cases,...(failureOnly?{reuse:{previous_source_sha256:previousCheck.source_sha256,scope:'Three clean terminal rendering cases unchanged; only terminal save-failure placeholder changed and rerun'}}:{})};
}finally{await fixture.close();fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n')}
console.log(JSON.stringify(report.terminal_ui_check));
