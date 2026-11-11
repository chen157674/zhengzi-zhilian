// 页面协议夹具验证展示，不运行纠错模型；原视频及当前可见坐标来自真实1694。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fixture=new BrowserFixture('video-correction-ui'),errors=[];
try{
  const payload=JSON.parse(fixture.seed(`from fitness_app.paths import configure_environment
configure_environment()
import os,json
from fitness_app.database import Database
from fitness_app.auth import initialize_admin
from fitness_app.presentation_assets import demo_resource
from fitness_app.skeleton import CONNECTIONS
from pathlib import Path
db=Database(os.environ['FITNESS_DATABASE_PATH']);db.initialize();initialize_admin(db,'Video_Cue','Local-video-cue-2026');db.dispose()
item=demo_resource('user-1694');session={'source':'demo','config':{'demo_id':item['id']},'action_id':item['action_id'],'source_sha256':item['video']['sha256'],'source_bytes':item['video']['bytes'],'unit':'repetitions','total_count':0,'hold_ms':0,'state_version':1}
track=json.loads(Path(item['track']['path']).read_text(encoding='utf-8'));frame=next(f for f in track['frames'] if f['t']==15000)
joints=[{'index':i,'x':p[0],'y':p[1]} for i,p in enumerate(frame['p']) if p is not None];visible={j['index'] for j in joints}
skeleton={'available':True,'captured_ms':15000,'joints':joints,'connections':[list(c) for c in CONNECTIONS if set(c)<=visible],'highlight':None}
job={'job_id':'11111111-1111-4111-8111-111111111111','session_id':'22222222-2222-4222-8222-222222222222','session':session,'status':'running','stopped':False,'save_state':'clean','terminal_persisted':False,'state_version':1,'processed_ms':15000,'total_ms':item['duration_ms'],'counting_available':True,'skeleton_reason':'','skeleton':skeleton,'coaching':{'state':'observing'}}
record={'posture':{'findings':[{'code':'fixture.return','status':'issue','start_ms':4800,'end_ms':5133,'message':'左侧回程节奏明显快于用力阶段','suggestion':'平稳控制回程。'},{'code':'fixture.lean','status':'issue','start_ms':15000,'end_ms':16000,'message':'身体向右侧持续倾斜','suggestion':'检查坐姿，保持肩髋对齐。'},{'code':'fixture.partial','status':'not_observed','start_ms':7000,'end_ms':12000}]}}
source={'demo_id':item['id'],'video_url':item['video_url'],'title':item['title'],'action_id':item['action_id'],'source_type':'user_supplied'}
print(json.dumps({'job':job,'record':record,'source':source}))`).trim());
  fixture.start();
  const context=await fixture.browser('Edge',{width:1440,height:1100});
  await fixture.apiLogin(context,'Video_Cue','Local-video-cue-2026');
  const template=(await (await context.request.get(fixture.origin+'/video?demo=1&demo_id=user-1694')).text()).replace('data-job-id=""','data-job-id="'+payload.job.job_id+'"');
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/video/jobs/'+payload.job.job_id,route=>route.fulfill({contentType:'text/html',body:template}));
  await page.route('**/api/v1/jobs/'+payload.job.job_id,route=>route.fulfill({json:{data:payload.job}}));
  await page.route('**/api/v1/records/'+payload.job.session_id+'/demo-source',route=>route.fulfill({json:{data:payload.source}}));
  await page.route('**/api/v1/records/'+payload.job.session_id,route=>route.fulfill({json:{data:payload.record}}));
  await page.goto(fixture.origin+'/video/jobs/'+payload.job.job_id);
  await page.waitForFunction(()=>document.querySelector('#video-correction [data-cue-title]').textContent==='正在观察'&&Math.abs(document.querySelector('#video-player').currentTime-15)<.1);
  await page.locator('#video-live-skeleton').waitFor({state:'visible'});
  assert(await page.locator('#video-player').evaluate(video=>{const c=document.createElement('canvas');c.width=64;c.height=36;const ctx=c.getContext('2d');ctx.drawImage(video,0,0,64,36);const data=ctx.getImageData(0,0,64,36).data;let low=255,high=0;for(let i=0;i<data.length;i+=4){low=Math.min(low,data[i]);high=Math.max(high,data[i]);}return high-low>30;}),'处理中应显示真实原片像素');
  await page.evaluate(()=>{window.fixtureSeeks=0;document.querySelector('#video-player').addEventListener('seeking',()=>window.fixtureSeeks++);});
  const polled=page.waitForResponse(response=>response.url().endsWith('/api/v1/jobs/'+payload.job.job_id));await polled;
  assert.equal(await page.evaluate(()=>window.fixtureSeeks),0,'相同处理时间的轮询不应重新定位造成闪帧');
  await page.locator('#video-page').screenshot({path:path.join(root,'第四轮优化/实现核对/视频处理中-演示.png')});
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:1000});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert.equal(await page.locator('#video-correction [data-cue-text]').isVisible(),false);
    assert(!(await page.locator('#video-correction').innerText()).includes('正常'));
    await page.locator('#video-player-wrap').screenshot({path:path.join(root,'第四轮优化/实现核对/视频原片提示-'+(width===390?'窄屏':'桌面')+'.png')});
  }
  payload.job.stopped=true;payload.job.status='completed';payload.job.terminal_persisted=true;
  payload.job.coaching={state:'ended'};payload.job.skeleton={available:false};
  await page.waitForFunction(()=>document.querySelector('#video-page').dataset.stage==='result');
  assert.equal(await page.locator('#video-events button').count(),2);
  assert.equal(await page.locator('#video-progress-panel').isVisible(),false);
  assert.equal(await page.locator('#video-result-status').innerText(),'分析完成');
  await page.locator('#video-events button').nth(1).click();
  await page.waitForFunction(()=>document.querySelector('#video-correction [data-cue-title]').textContent==='身体向右侧持续倾斜'&&Math.abs(document.querySelector('#video-player').currentTime-15)<.1);
  assert.equal(await page.locator('#video-correction [data-cue-text]').innerText(),'检查坐姿，保持肩髋对齐。');
  await page.locator('#video-events button').nth(0).click();
  await page.waitForFunction(()=>document.querySelector('#video-correction [data-cue-title]').textContent==='左侧回程节奏明显快于用力阶段');
  for(const [stamp,title] of [[10,'此处暂无新的姿态结论'],[20,'此处暂无新的姿态结论'],[15.5,'身体向右侧持续倾斜']]){
    await page.locator('#video-player').evaluate(async(v,stamp)=>{v.pause();await new Promise(resolve=>{v.addEventListener('seeked',resolve,{once:true});v.currentTime=stamp;});},stamp);
    assert.equal(await page.locator('#video-correction [data-cue-title]').innerText(),title);
  }
  await page.setViewportSize({width:1440,height:1100});
  const columns=await page.locator('#video-page').evaluate(node=>({
    player:node.querySelector('#video-player-wrap').getBoundingClientRect().width,
    feedback:node.querySelector('#video-results').getBoundingClientRect().width
  }));
  assert(columns.player>columns.feedback,'桌面结果应以画面为主，不能被旧布局规则挤成窄栏');
  await page.locator('#video-page').screenshot({path:path.join(root,'第四轮优化/实现核对/视频分析结果-演示.png')});
  for(const width of [1440,1060,768,390,320]){
    await page.setViewportSize({width,height:1000});
    const layout=await page.evaluate(()=>{const rect=id=>{const r=document.getElementById(id).getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width};};return {media:rect('video-player-wrap'),result:rect('video-results'),feedback:rect('video-feedback'),events:rect('video-events'),columns:getComputedStyle(document.querySelector('#video-events')).gridTemplateColumns.split(' ').length,overflow:document.documentElement.scrollWidth>innerWidth};});
    assert.equal(layout.overflow,false);
    assert(layout.feedback.top>=Math.max(layout.media.bottom,layout.result.bottom)-1,'问题列表独立于窄侧栏并处于媒体摘要之后');
    if(width<=1100)assert(layout.result.top>=layout.media.bottom-1,'中等窗口下摘要在画面之后');
    if(width<=767)assert.equal(layout.columns,1,'手机反馈单列');
    if(width===1060||width===390)await page.locator('#video-page').screenshot({path:path.join(root,'第四轮优化/实现核对/视频结果-'+(width===1060?'中屏':'窄屏')+'.png')});
  }

  await page.reload();
  await page.waitForFunction(()=>document.querySelector('#video-page').dataset.stage==='result');
  await page.waitForFunction(()=>document.querySelector('#video-player').readyState>=2&&!document.querySelector('#video-player').seeking&&Math.abs(document.querySelector('#video-player').currentTime-4.8)<.1);
  assert.equal(await page.locator('#video-events button').count(),2);
  // 处理中即便协议已有具体问题，也先观察，完成后再统一给出。
  payload.job.stopped=false;payload.job.status='running';payload.job.terminal_persisted=false;
  payload.job.coaching={state:'needs_adjustment',primary_cue:{message:'身体向右侧持续倾斜',suggestion:'保持肩髋对齐。'}};
  await page.reload();await page.locator('#video-correction [data-cue-title]').filter({hasText:'正在观察'}).waitFor();
  assert.equal(await page.locator('#video-correction').getAttribute('data-state'),'observing');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({scope:'controlled UI protocol; not model accuracy',observingDuringProcessing:true,savedFindingCards:true,locateAndReplaySeek:true,refresh:true,partialChecksScoped:true,noFalseNormal:true,mobileOverflow:false,pageErrors:errors}));
}finally{await fixture.close();}
