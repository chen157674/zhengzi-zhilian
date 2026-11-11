// 18-C新来源实际Full执行一次；回看／刷新／个人统计隔离不重跑模型。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step18-demo');const errors=[],writes=[];
try{
  await fixture.startTestService('tests/formal_demo_service.py');const context=await fixture.browser('Edge',{width:1440,height:1000});
  await fixture.apiLogin(context,'Demo_Admin','demo-fixture-password-123');
  await context.addInitScript(()=>{window.demoCameraCalls=0;Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{window.demoCameraCalls++;throw new Error('演示分析不得开启摄像头')}})});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')writes.push(new URL(r.url()).pathname)});
  await page.goto(fixture.origin+'/video?demo=1');await page.waitForFunction(()=>!document.querySelector('#video-start').disabled);
  assert.equal(await page.locator('#video-action').inputValue(),'barbell_bent_over_row');assert(await page.locator('#video-action').isDisabled());assert(await page.locator('#video-demo-note').isVisible());
  assert.equal(await page.locator('#video-start').textContent(),'开始实际分析');const out=path.join(root,'data/verification/step-18');
  const shot=async name=>{await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo({top:0,behavior:'instant'})});await page.screenshot({path:path.join(out,name),fullPage:true})};
  await page.waitForFunction(()=>document.querySelector('#video-player').readyState>=3&&!document.querySelector('#video-start').disabled);
  if(process.argv.includes('--presentation-only')){
    await page.locator('#video-demo-credit summary').click();
    assert((await page.locator('#video-demo-credit').textContent()).includes('does not imply or constitute DoW endorsement'));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await shot('demo-preparation.png');
    const metrics=(await(await context.request.get(fixture.origin+'/__fixture/demo-metrics')).json()).data;assert.deepEqual(metrics,{model_instances:0,pose_calls:0});
    const evidence=path.join(out,'demo-analysis-ui.json'),prior=JSON.parse(fs.readFileSync(evidence,'utf8'));
    fs.writeFileSync(evidence,JSON.stringify({...prior,presentationRecheck:'Attribution and ready-state screenshot checked with zero model calls; actual analysis evidence reused'},null,2)+'\n');
    console.log('Demo attribution presentation checked; zero model calls.');
  }else{
  await shot('demo-preparation.png');await page.locator('#video-start').click();await page.waitForURL('**/video/jobs/*');const jid=new URL(page.url()).pathname.split('/').at(-1);
  console.log(JSON.stringify({stage:'actual_demo_started',jobId:jid}));
  await page.locator('#video-state').filter({hasText:'分析完成'}).waitFor({timeout:90000});
  const data=(await(await context.request.get(fixture.origin+'/api/v1/jobs/'+jid)).json()).data;
  assert.equal(data.status,'completed');assert.equal(data.session.source,'demo');assert.equal(data.session.total_count,3);assert.equal(data.processed_frames,855);assert.equal(data.save_state,'clean');assert(data.terminal_persisted);
  const metrics=async()=> (await(await context.request.get(fixture.origin+'/__fixture/demo-metrics')).json()).data;
  assert.deepEqual(await metrics(),{model_instances:1,pose_calls:855});assert.equal(await page.evaluate(()=>demoCameraCalls),0);
  assert((await page.locator('#video-result-note').textContent()).includes('不计入个人训练统计'));await shot('demo-results.png');
  await page.reload();await page.locator('#video-amount').filter({hasText:'3 次'}).waitFor();await page.waitForFunction(()=>document.querySelector('#video-player').readyState>=2);
  assert.deepEqual(await metrics(),{model_instances:1,pose_calls:855});
  const list=(await(await context.request.get(fixture.origin+'/api/v1/records')).json()).data;assert.equal(list.total,0);
  await page.locator('#video-detail').click();await page.waitForURL('**/records/*');await page.locator('#detail-count').filter({hasText:'3 次'}).waitFor();
  assert((await page.locator('#detail-meta').textContent()).includes('内置演示 · 不计入个人训练统计'));await page.waitForFunction(()=>document.querySelector('.record-video')?.readyState>=2);
  await shot('demo-record.png');
  await page.route('**/static/demo/barbell-row/video*',r=>r.abort());await page.reload();await page.getByText('演示原片暂时无法播放，请重新加载。',{exact:true}).waitFor();
  await page.unroute('**/static/demo/barbell-row/video*');await page.locator('#record-source-select').click();await page.waitForFunction(()=>document.querySelector('.record-video')?.readyState>=2);
  assert.deepEqual(await metrics(),{model_instances:1,pose_calls:855});
  await page.goto(fixture.origin+'/video');await page.locator('#video-file').setInputFiles(path.join(root,'data/datasets/development-movements/barbell-row-dvids-639914-side.mp4'));
  await page.locator('#video-input-state').filter({hasText:'校验通过'}).waitFor();await page.locator('#video-action').selectOption('barbell_bent_over_row');
  await page.waitForFunction(()=>!document.querySelector('#video-start').disabled);assert(await page.locator('#video-demo-note').isHidden());assert(await page.locator('#video-action').isEnabled());
  assert.equal(await page.locator('#video-start').textContent(),'开始分析');await page.locator('#video-remove').click();await page.locator('#video-file-panel').waitFor({state:'hidden'});
  assert.deepEqual(await metrics(),{model_instances:1,pose_calls:855});assert.equal(writes.filter(p=>p==='/api/v1/jobs/video').length,1);assert.equal(writes.filter(p=>p==='/api/v1/sessions').length,1);
  assert.deepEqual(errors,[]);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  const result={checks:'passed',scope:'registered demo actual Full video analysis and saved demo replay',freshPoseInference:true,poseCalls:855,modelInstances:1,count:3,processedFrames:data.processed_frames,processingElapsedMs:data.processing_elapsed_ms,
    personalHistoryTotal:list.total,cameraCalls:0,source:'demo',refreshAndReplayDidNotReanalyze:true,replayFailureRecovered:true,localFileSelectionStillAvailable:true,independentAccuracyTruth:false};
  fs.writeFileSync(path.join(out,'demo-analysis-ui.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
  }
}finally{await fixture.close()}
