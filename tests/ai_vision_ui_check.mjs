// 用合成视频验证画面选择与单次发送；不打开相机、不访问真实供应商。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fx=new BrowserFixture('ai-vision-ui'),errors=[],posts=[];
const cid='11111111-1111-4111-8111-111111111111',rid='22222222-2222-4222-8222-222222222222';
try{
 const seed=JSON.parse(fx.seed(`import os,json
from fitness_app import create_app
from fitness_app.auth import initialize_admin
from fitness_app.catalogue import get_action
from fitness_app import db_schema as s
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Vision_UI','vision-ui-fixture-2026')
client=app.test_client()
def post(url,data):
 token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
 result=client.post(url,json=data,headers={'X-CSRFToken':token,'Idempotency-Key':s.new_id()})
 assert result.status_code in (200,201,202),result.json
 return result.json['data']
post('/api/v1/auth/login',{'username':'Vision_UI','password':'vision-ui-fixture-2026'})
iid=post('/api/v1/demo-inputs',{'demo_id':'user-1688'})['input_id']
app.extensions['media_inputs'].threads[iid].join(15)
sid=post('/api/v1/sessions',{'input_id':iid,'demo_id':'user-1688','action_id':'lat_pulldown','capability_version':get_action('lat_pulldown')['capability_version'],'source':'demo','mode':'free','target':None})['session_id']
with client.session_transaction() as state:auth=state['_user_id']
with db.transaction(False) as c:uid=c.scalar(s.users.select().with_only_columns(s.users.c.id))
store=app.extensions['session_store'];store.set_state(uid,sid,'running',s.new_id());store.set_state(uid,sid,'ended_early',s.new_id(),reason='fixture')
for hook in app.extensions.get('stop_hooks',[]):hook()
db.dispose();print(json.dumps({'record_id':sid}))
`));
 fx.start();const browser=await fx.browser('Edge',{width:1440,height:1000});await fx.apiLogin(browser,'Vision_UI','vision-ui-fixture-2026');
 const page=await browser.newPage();page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(()=>{window.cameraRequests=0;if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{window.cameraRequests++;throw Error('No camera in this test');};});
 await page.route('**/api/v1/ai/conversations',route=>route.fulfill({status:201,json:{data:{conversation_id:cid,context_revision:'fixture',context_current:true}}}));
 await page.route('**/api/v1/ai/conversations/*/messages',route=>{posts.push(route.request().postDataJSON());return route.fulfill({status:202,json:{data:{request_id:rid,status:'queued'}}});});
 await page.route('**/api/v1/ai/requests/*',route=>{
  const currentPost=posts.at(-1);
  if(currentPost?.text==='测试失败状态')return route.fulfill({json:{data:{request_id:rid,status:'failed',context_current:true,message:'本次未能完成回复，请重试。'}}});
  const post=posts.at(-1),review=post?.text==='这个视频人物有问题嘛'?{visual_review:{state:'observations_returned',findings:[{
   code:'side_lean',status:'observed',title:'身体向一侧偏斜',observation:'肩髋方向需要核对。',suggestion:'先稳定坐姿。',
   samples:[{source_ms:post.frames[0].source_ms}]}]}}:{};
  return route.fulfill({json:{data:{request_id:rid,status:'succeeded',context_current:true,result:{summary:'这是隔离测试回复。',explanations:[],next_focus:[],...review}}}});
 });
 await page.addInitScript(()=>{
  window.loaderFlashes=[];
  const observe=()=>{const n=document.querySelector('#overview-loading');if(n&&!n.hidden&&n.textContent.trim())window.loaderFlashes.push(n.textContent);};
  new MutationObserver(observe).observe(document,{subtree:true,attributes:true,childList:true});
 });
 await page.goto(fx.origin+'/records');
 await page.locator('#overview-empty:not([hidden]),#overview-content:not([hidden])').waitFor();
 assert.deepEqual(await page.evaluate(()=>window.loaderFlashes),[],'快速读取不能闪现小字');
 await page.screenshot({path:path.join(root,'data/verification/round3/overview-without-loading-flash.png'),animations:'disabled'});
 await page.goto(fx.origin+'/realtime');await page.locator('[data-ai-open]').click();
 assert.equal(await page.locator('#ai-send-frame').isChecked(),false);
 await page.locator('#ai-chat-input').fill('你有什么用？');await page.locator('#ai-chat-send').click();
 await page.locator('.ai-chat-message.assistant').waitFor();assert.equal(posts.length,1);assert(!('frames' in posts[0]));
 await page.evaluate(async()=>{
  const canvas=document.createElement('canvas');canvas.width=960;canvas.height=540;const ctx=canvas.getContext('2d');ctx.fillStyle='#647489';ctx.fillRect(0,0,960,540);
  window.visionTestStream=canvas.captureStream(5);document.querySelector('#live-source').srcObject=window.visionTestStream;await document.querySelector('#live-source').play();
 });
 await page.locator('#ai-send-frame').check();assert.equal(await page.locator('#ai-frame-hint').isVisible(),true);
 await page.locator('#ai-chat-input').fill('视频里面的人正在做什么？');await page.locator('#ai-chat-send').click();
 await page.waitForFunction(()=>document.querySelectorAll('.ai-chat-message.assistant').length===2);
 assert.equal(posts.length,2);assert.equal(posts[1].include_current_frame,true);assert.equal(posts[1].frames.length,3);
 for(const frame of posts[1].frames){assert(frame.data_url.startsWith('data:image/jpeg;base64,'));assert(Number.isInteger(frame.source_ms));}
 await page.locator('#ai-send-frame').uncheck();await page.locator('#ai-chat-input').fill('肩推怎么练？');await page.locator('#ai-chat-send').click();
 await page.waitForFunction(()=>document.querySelectorAll('.ai-chat-message.assistant').length===3);assert(!('frames' in posts[2]));
 await page.locator('#ai-send-frame').check();
 await page.evaluate(()=>{window.visionTestStream.getTracks().forEach(track=>track.stop());document.querySelector('#live-source').srcObject=null;});
 await page.locator('#ai-send-frame:disabled').waitFor();assert.equal(await page.locator('#ai-send-frame').isChecked(),false);
 assert.match(await page.locator('#ai-send-frame-label').getAttribute('title'),/开启摄像头或加载视频/);assert.equal(posts.length,3);
 await page.goto(fx.origin+'/records/'+seed.record_id);
 await page.waitForFunction(()=>document.querySelector('#record-media video')?.readyState>=2);
 for(const stamp of [3,7,10,14]){
  await page.locator('#record-media video').evaluate((v,t)=>{v.pause();v.currentTime=t;},stamp);
  await page.waitForFunction(()=>document.querySelector('#record-media .video-correction [data-cue-title]')?.textContent==='身体持续向一侧倾斜');
 }
 assert.match(await page.locator('#detail-quality').innerText(),/0:02.*0:15/s);
 await page.screenshot({path:path.join(root,'data/verification/round3/lat-confirmed-continuous-feedback.png'),animations:'disabled'});

 await page.locator('[data-ai-open]').click();
 await page.waitForFunction(()=>document.querySelector('#ai-send-frame').checked);
 assert.equal(await page.locator('#ai-send-frame').isChecked(),true);
 assert.equal(await page.locator('#ai-frame-label').innerText(),'视频画面');
 const before=await page.locator('#record-media video').evaluate(v=>{v.pause();v.dataset.aiRangeStartMs='2000';v.dataset.aiRangeEndMs='6000';return v.currentTime;});
 await page.locator('#ai-chat-input').fill('这个视频人物有问题嘛');await page.locator('#ai-chat-send').click();
 await page.locator('.ai-chat-message.assistant').waitFor();
 assert.equal(posts.length,4);assert.equal(posts[3].include_current_frame,true);assert.equal(posts[3].frames.length,3);
 assert(posts[3].frames.every(f=>f.source_ms>=2000&&f.source_ms<6000));
 assert(posts[3].frames.every((f,i)=>!i||f.source_ms>posts[3].frames[i-1].source_ms));
 assert.equal(await page.locator('#record-media video').evaluate(v=>v.currentTime),before);
 assert.equal(await page.locator('[data-visual-finding="side_lean"]').count(),1);
 await page.screenshot({path:root+'/data/verification/round3/ai-visual-findings-desktop.png',animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:root+'/data/verification/round3/ai-visual-findings-mobile.png',animations:'disabled'});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.setViewportSize({width:1440,height:1000});
 await page.locator('[data-visual-finding="side_lean"] button').click();
 await page.waitForFunction(t=>Math.abs(document.querySelector('#record-media video').currentTime-t/1000)<.03,posts[3].frames[0].source_ms);
 const located=await page.locator('#record-media video').evaluate(v=>v.currentTime);
 await page.locator('#record-media video').evaluate(v=>{v.dataset.aiRangeStartMs='7000';v.dataset.aiRangeEndMs='9000';});
 await page.locator('[data-visual-finding="side_lean"] button').click();
 assert.equal(await page.locator('#record-media video').evaluate(v=>v.currentTime),located);
 assert.match(await page.locator('#ai-request-state').innerText(),/离开所选片段/);

 await page.locator('#ai-send-frame').uncheck();await page.locator('#ai-chat-input').fill('下拉怎么练？');await page.locator('#ai-chat-send').click();
 await page.waitForFunction(()=>document.querySelectorAll('.ai-chat-message.assistant').length===2);
 assert.equal(posts.length,5);assert(!('frames' in posts[4]));
 await page.locator('#ai-chat-input').fill('请帮我计算2+3，并解释一下');await page.locator('#ai-chat-send').click();
 await page.waitForFunction(()=>document.querySelectorAll('.ai-chat-message.assistant').length===3);
 assert.equal(posts.at(-1).text,'请帮我计算2+3，并解释一下');
 await page.locator('#ai-chat-input').fill('测试失败状态');await page.locator('#ai-chat-send').click();
 await page.locator('#ai-chat-error:not([hidden])').waitFor();
 await page.waitForFunction(()=>document.querySelector('#ai-request-state').textContent==='');
 assert.equal(await page.locator('#ai-assistant').getAttribute('data-busy'),'false');
 assert.equal(await page.locator('.ai-typing').count(),0);

 await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert.equal(await page.evaluate(()=>window.cameraRequests),0);assert.deepEqual(errors,[]);
 console.log('PASS: live capture, selected record defaults to images, selected-range samples without seeking the player, text-only opt-out, missing-frame reset, mobile layout, no camera or provider calls.');
}finally{await fx.close();}
