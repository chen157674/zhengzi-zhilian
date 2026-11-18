// 第五轮真实页面＋协议夹具：只验证展示与交互，不运行模型或供应商。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fx=new BrowserFixture('round5-correction');
const output=path.join(root,'第五轮优化','实现核对');fs.mkdirSync(output,{recursive:true});
const sid='11111111-1111-4111-8111-111111111111',pid='22222222-2222-4222-8222-222222222222';
const issue={code:'trunk.lean',status:'issue',side:'right',body_parts:['躯干'],message:'身体向右侧持续倾斜',suggestion:'保持躯干稳定，减少左右摆动。',start_ms:15000,end_ms:16000,rule_version:'fixture-1'};
const record={session_id:sid,action_id:'machine_chest_press',action_name:'器械推胸',source:'video',status:'completed',save_state:'clean',started_at_ms:1000,state_version:1,config:{},posture:{findings:[issue]},saved_skeleton:{status:'not_saved'},groups:[],events:[],intervals:[],supported_items:[],total_count:3,evaluable_count:3,unassessable_count:0,active_ms:5000,last_saved_at_ms:1000};
const segment={id:'33333333-3333-4333-8333-333333333333',start_ms:0,end_ms:20000,kind:'known',action_name:'器械推胸',effective_action_id:'machine_chest_press',state_version:1,current_revision:{session_id:sid,origin:'auto'},revisions:[]};
const analysis={analysis_id:pid,status:'completed',mode:'auto',state_version:1,segments:[segment],processed_ms:20000,duration_ms:20000,can_reanalyse:true,save_failed_records:[]};
const errors=[];
try{
 const seeded=fx.seed(`from fitness_app import create_app
from fitness_app.auth import initialize_admin
import os
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
owner=initialize_admin(app.extensions['database'],'Round5_Admin','round5-fixture-password')['id']
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
client=app.test_client()
token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
client.post('/api/v1/auth/login',json={'username':'Round5_Admin','password':'round5-fixture-password'},headers={'X-CSRFToken':token})
with client.session_transaction() as state:auth=state['_user_id']
store=app.extensions['session_store']
import sys,io
from pathlib import Path
sys.path.insert(0,'tests')
from test_formal_media import sample_mp4
media=sample_mp4(frames=200)
Path(os.environ['FITNESS_RUNTIME_DIR']).parent.joinpath('replay.mp4').write_bytes(media)
token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
upload=client.post('/api/v1/inputs',data={'file':(io.BytesIO(media),'replay.mp4')},headers={'X-CSRFToken':token,'Idempotency-Key':s.new_id()}).json['data']
manager=app.extensions['media_inputs']
manager.threads[upload['input_id']].join(10)
record=store.create(owner,auth,{'action_id':'machine_chest_press','capability_version':get_action('machine_chest_press')['capability_version'],'source':'video','mode':'free','input_id':upload['input_id']},s.new_id())
manager.stop()
print(record['session_id'])
app.extensions['database'].dispose()`);
 fx.start();const context=await fx.browser('Edge',{width:1440,height:1000});await fx.apiLogin(context,'Round5_Admin','round5-fixture-password');
 const realSid=seeded.trim().split(/\r?\n/).at(-1);
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/v1/video-analyses',r=>r.fulfill({json:{data:{analyses:[]}}}));
 await page.route('**/api/v1/jobs',r=>r.fulfill({json:{data:{jobs:[]}}}));
 await page.route('**/api/v1/records/'+sid,r=>r.fulfill({json:{data:record}}));
 await page.route('**/api/v1/video-analyses/'+pid,r=>r.fulfill({json:{data:analysis}}));
 await page.goto(fx.origin+'/video');await page.locator('#video-select').waitFor();
 // 页路由按真实owner校验；先进入真实选择页，再在其模块装载前注入任务身份。
 await page.addInitScript(id=>{document.addEventListener('DOMContentLoaded',()=>{const root=document.querySelector('#video-auto-page');if(root)root.dataset.analysisId=id;},{once:true});},pid);
 await page.route('**/video?round5-result=1',async r=>{const res=await context.request.get(fx.origin+'/video');const body=(await res.text()).replace('data-analysis-id=""','data-analysis-id="'+pid+'"');await r.fulfill({status:200,contentType:'text/html',body});});
 await page.goto(fx.origin+'/video?round5-result=1');
 await page.locator('#video-correction-problem').filter({hasText:issue.message}).waitFor();
 assert(await page.locator('#video-correction-seek').isDisabled());assert(await page.locator('#video-source-select').isVisible());
 assert((await page.locator('#video-correction-instruction').textContent()).includes('保持躯干稳定'));
 assert.equal(await page.locator('#video-correction-record').getAttribute('href'),'/records/'+sid);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:path.join(output,'纠正提示-桌面缺片.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 const boxes=await page.evaluate(()=>['video-player-wrap','video-correction-summary','video-segments'].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return {id,top:r.top,bottom:r.bottom};}));
 assert(boxes[1].top>=boxes[0].bottom-2);assert(boxes[2].top>=boxes[1].bottom-2);
 await page.screenshot({path:path.join(output,'纠正提示-手机缺片.png'),fullPage:true});
 await page.route('**/api/v1/records/'+realSid,r=>r.fulfill({json:{data:{...record,session_id:realSid}}}));
 await page.route('**/api/v1/records/'+realSid+'/retest-options',r=>r.fulfill({json:{data:{options:[]}}}));
 await page.route('**/api/v1/records/'+realSid+'/training-note',r=>r.fulfill({json:{data:{version:0}}}));
 await page.goto(fx.origin+'/records/'+realSid);
 await page.locator('#record-focus').filter({hasText:'动作纠正提示'}).waitFor();
 assert((await page.locator('#record-focus').textContent()).includes(issue.suggestion));assert.equal(await page.locator('.correction-basis').count(),0);assert.equal(await page.locator('.correction-method').first().getAttribute('href'),'/actions/machine_chest_press');
 assert(await page.locator('#record-source-select').isVisible());
 assert(await page.locator('[data-source-seek]').first().isDisabled());
 await page.screenshot({path:path.join(output,'记录纠正-手机缺片.png'),fullPage:true});
 const matchRequests=[];page.on('request',r=>{if(r.url().endsWith('/source-match'))matchRequests.push(r.method());});
 const mediaFile={name:'wrong.mp4',mimeType:'video/mp4',buffer:Buffer.from('wrong-source')};
 await page.locator('#record-source-file').setInputFiles(mediaFile);
 await page.getByText('文件内容与本条记录不匹配，请重新选择原视频',{exact:true}).waitFor();
 assert(await page.locator('[data-source-seek]').first().isDisabled());
 await page.locator('#record-source-file').setInputFiles(path.join(fx.temporary,'replay.mp4'));
 await page.getByText('原文件匹配成功，可按已保存问题时间回看。',{exact:true}).waitFor();
 await page.waitForFunction(()=>document.querySelector('.record-video').readyState>=2);
 assert(await page.locator('[data-source-seek]').first().isEnabled());assert.deepEqual(matchRequests,['POST','POST']);
 await page.locator('[data-source-seek]').first().click();await page.waitForFunction(()=>Math.abs(document.querySelector('.record-video').currentTime-15)<.2);
 // 实际合成MP4验证解码/内容匹配/源时间定位，不作为真人或模型检出证据。
 await page.goto(fx.origin+'/realtime');await page.locator('#live-toggle').waitFor();
 await page.evaluate(()=>{const cue=document.querySelector('#live-cue');cue.hidden=false;cue.dataset.state='issue';cue.querySelector('#live-cue-title').textContent='身体向右侧持续倾斜';cue.querySelector('#live-cue-text').textContent='保持躯干稳定，减少左右摆动。';cue.querySelector('#live-cue-next').hidden=false;});
 await page.waitForTimeout(100);
 const live=await page.evaluate(()=>{const a=document.querySelector('#live-cue').getBoundingClientRect(),b=document.querySelector('.live-foot').getBoundingClientRect();return {cueBottom:a.bottom,footTop:b.top,width:document.documentElement.scrollWidth};});assert(live.footTop>=live.cueBottom-2);assert(live.width<=390);
 await page.screenshot({path:path.join(output,'实时纠正-手机布局夹具.png'),fullPage:true});
 assert.deepEqual(errors,[]);console.log('PASS:真实页面协议夹具桌面/手机纠正与缺片状态，实时手机提示不遮设置；不是模型或真实相机验收。');
}finally{await fx.close();}
