// 10 新视频页面、任务背景存活和原片回看。业务输入不用于准确率评估。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step10'),evidence=path.join(root,'data/verification/step-10');
const cancelOnly=process.argv.includes('--cancel-only');
const compatOnly=process.argv.includes('--compat-only');
let context,page;const errors=[],writes=[];
try{
  await fixture.seed("import sys\nsys.path.insert(0,'tests')\nfrom test_formal_media import sample_mp4\nfrom pathlib import Path\nPath('"+fixture.temporary.replaceAll('\\','/')+"/clip.mp4').write_bytes(sample_mp4(frames="+(cancelOnly?120:35)+"))");
  await fixture.startTestService('tests/formal_video_service.py');context=await fixture.browser(compatOnly?'Chrome':'Edge',compatOnly?{width:1280,height:900}:{width:1440,height:1000});await fixture.apiLogin(context,'Video_User','video-fixture-password-123');
  page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));context.on('request',request=>{if(request.method()==='POST')writes.push(new URL(request.url()).pathname)});
  await page.goto(fixture.origin+'/video');await page.locator('#video-action').selectOption('bodyweight_squat');
  await page.locator('#video-file').setInputFiles(path.join(fixture.temporary,'clip.mp4'));await page.locator('#video-input-state').filter({hasText:'校验通过'}).waitFor();
  await page.waitForFunction(()=>!document.querySelector('#video-start').disabled);assert.equal(await page.locator('#video-framing').textContent(),'侧面，肩、髋、膝、踝连续可见');
  if(compatOnly){
    await page.locator('#video-player').evaluate(async video=>{video.muted=true;await video.play();video.pause()});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.equal(writes.filter(url=>url==='/api/v1/jobs/video').length,0);
    console.log(JSON.stringify({browser:await context.browser().version(),checks:'passed',scope:'Chrome native MP4 replay and 1280px layout only'}));
  }else{
  fs.mkdirSync(evidence,{recursive:true});await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(evidence,'preparation.png'),fullPage:true});
  await page.locator('#video-start').click();await page.waitForURL('**/video/jobs/*');const jobUrl=page.url();const jid=new URL(jobUrl).pathname.split('/').at(-1);
  if(cancelOnly){
    await page.locator('.topbar nav a[href="/records"]').click();await page.locator('#confirm-dialog').waitFor();await page.locator('[data-dialog-cancel]').click();assert.equal(page.url(),jobUrl);assert.equal(writes.filter(url=>url.endsWith('/cancel')).length,0);
    await page.locator('#video-cancel').click();await page.locator('[data-dialog-confirm]').click();await page.locator('#video-state').filter({hasText:'已取消'}).waitFor();
    const partial=(await(await context.request.get(fixture.origin+'/api/v1/jobs/'+jid)).json()).data;assert.equal(partial.status,'cancelled');assert(partial.processed_frames<120);assert(partial.stopped);assert.equal(partial.save_state,'clean');
    assert((await page.locator('#video-skeleton').boundingBox()).width<=20);
    await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(evidence,'results.png'),fullPage:true});
    await page.locator('.topbar nav a[href="/records"]').click();await page.waitForURL('**/records');
    console.log(JSON.stringify({checks:'passed',scope:'cancel navigation stays, confirmed cancel partial, checkbox layout',processedFrames:partial.processed_frames,sourceFrames:120}));
  }else{
  await page.close();page=await context.newPage();await page.goto(jobUrl);await page.locator('#video-state').filter({hasText:'分析完成'}).waitFor();
  const job=(await(await context.request.get(fixture.origin+'/api/v1/jobs/'+jid)).json()).data;assert.equal(job.status,'completed');assert.equal(job.processed_frames,35);assert.equal(job.session.total_count,34);
  await page.locator('#video-file').setInputFiles(path.join(fixture.temporary,'clip.mp4'));await page.waitForFunction(()=>document.querySelector('#video-player').readyState>=1);
  const before=writes.filter(url=>url==='/api/v1/jobs/video').length;await page.locator('#video-player').evaluate(video=>{video.currentTime=1;video.pause()});assert.equal(await page.locator('#video-skeleton').isDisabled(),true);
  await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(evidence,'results.png'),fullPage:true});
  await page.locator('#video-detail').click();await page.waitForURL('**/records/*');await page.locator('#record-source-file').setInputFiles(path.join(fixture.temporary,'clip.mp4'));
  await page.getByText('原文件匹配成功，可按已保存问题时间回看。',{exact:true}).waitFor();await page.locator('.record-video').evaluate(video=>{video.currentTime=.5;video.pause()});
  await page.locator('#record-source-file').setInputFiles({name:'clip.mp4',mimeType:'video/mp4',buffer:Buffer.from('same name different content')});await page.getByText('文件内容与本条记录不匹配，请重新选择原视频',{exact:true}).waitFor();
  assert.equal(writes.filter(url=>url==='/api/v1/jobs/video').length,before);assert.equal((await(await context.request.get(fixture.origin+'/api/v1/records/'+job.session_id)).json()).data.total_count,34);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({browser:await context.browser().version(),checks:'passed',scope:'upload, background/reentry, source-match, playback without inference',input:'D-POSE/D-EVENT, encoded MP4',frames:35}));
  }
  }
}catch(error){console.log(JSON.stringify({errors,diagnostic:page?await page.locator('#video-error').textContent({timeout:1000}).catch(()=>null):null}));throw error}
finally{await fixture.close()}
