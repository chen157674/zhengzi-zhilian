// 合成媒体与供应商替身，验证分段AI保留当前原片、只读所选区间及切换上下文。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('round3-video-ui'),contexts=[],posts=[],errors=[];
const unknown=process.argv.includes('--unknown');
try{
 const input=path.join(fixture.temporary,'source.mp4');
 fixture.seed("import sys;from pathlib import Path;sys.path.insert(0,'tests');from test_formal_media import sample_mp4;Path(__import__('os').environ['FITNESS_DATABASE_PATH']).with_name('source.mp4').write_bytes(sample_mp4(frames=100))");
 await fixture.startTestService('tests/formal_roundthree_video_service.py');
 const browser=await fixture.browser('Edge',{width:1440,height:980});
 await fixture.apiLogin(browser,'Upload_Admin','Round3-upload-fixture-2026');
 const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));
 if(unknown){
  const token=(await(await browser.request.get(fixture.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;
  assert.equal((await browser.request.post(fixture.origin+'/api/v1/_fixture/video-control',{data:{terminal_fault:false,unknown:true},headers:{'X-CSRFToken':token}})).status(),200);
 }
 await page.route('**/api/v1/ai/conversations',route=>{
  contexts.push(route.request().postDataJSON());
  return route.fulfill({status:201,json:{data:{conversation_id:crypto.randomUUID(),context_revision:'fixture',context_current:true,image_input_supported:true,image_max_frames:6}}});
 });
 await page.route('**/api/v1/ai/conversations/*',route=>route.request().method()==='DELETE'?route.fulfill({json:{data:{closed:true}}}):route.continue());
 await page.route('**/api/v1/ai/conversations/*/messages',route=>{
  posts.push(route.request().postDataJSON());
  return route.fulfill({status:202,json:{data:{request_id:crypto.randomUUID(),status:'queued'}}});
 });
 await page.route('**/api/v1/ai/requests/*',route=>route.fulfill({json:{data:{
  status:'succeeded',context_current:true,result:{summary:'工程替身已读取本次所选片段画面。',explanations:[],next_focus:[]}}}}));
 await page.goto(fixture.origin+'/video');
 await page.locator('#video-file').setInputFiles(input);await page.locator('#video-start:not([disabled])').waitFor();
 await page.locator('#video-start').click();await page.waitForURL('**/video/analyses/*');
 await page.locator('#video-auto-page[data-stage=result]').waitFor({timeout:20000});
 await page.locator('.video-segment-card').nth(unknown?0:1).waitFor();
 await page.waitForFunction(()=>document.querySelector('#video-player').readyState>=2);
 const url=page.url(),pid=url.split('/').at(-1);
 const parent=(await(await browser.request.get(fixture.origin+'/api/v1/video-analyses/'+pid)).json()).data;
 for(const [index,segment] of parent.segments.entries()){
  if(index){await page.locator('#ai-chat-input').fill('这个视频人物有问题嘛');await page.locator('#ai-assistant-close').click();}
  const player=page.locator('#video-player');await player.evaluate(v=>v.pause());
  await page.locator('.video-segment-card').nth(index).getByRole('link',{name:'AI解读',exact:true}).click();
  await page.locator('#ai-assistant:not([hidden])').waitFor();
  await page.waitForFunction(id=>document.querySelector('#video-auto-page').dataset.aiSegmentId===id,segment.id);
  await page.waitForFunction(()=>document.querySelector('#ai-chat-messages').childElementCount===0);
  await page.waitForFunction(()=>!document.querySelector('#video-player').seeking&&document.querySelector('#video-player').readyState>=2);
  const before=await player.evaluate(v=>v.currentTime);
  assert(before*1000>=segment.start_ms&&before*1000<segment.end_ms);
  assert.equal(page.url(),url);
  assert.equal(await page.locator('#ai-send-frame').isChecked(),true);
  if(index)assert.equal(await page.locator('#ai-chat-input').inputValue(),'这个视频人物有问题嘛');
  await page.locator('#ai-chat-input').fill('这个视频人物有问题嘛');
  await page.locator('#ai-chat-send').click();await page.locator('.ai-chat-message.assistant').waitFor();
  await page.waitForFunction(()=>document.querySelector('#ai-assistant').dataset.busy==='false');
  assert.equal(contexts.at(-1).context_kind,segment.current_revision?'record':'general');
  assert.equal(contexts.at(-1).context_id,segment.current_revision?.session_id);
  if(!segment.current_revision)assert.equal(contexts.at(-1).history_scope,'none');
  assert.equal(posts.at(-1).include_current_frame,true);
  assert(posts.at(-1).frames.length>=1&&posts.at(-1).frames.length<=6);
  assert(posts.at(-1).frames.every(f=>f.source_ms>=segment.start_ms&&f.source_ms<segment.end_ms));
  assert.equal(await player.evaluate(v=>v.currentTime),before);
 }
 if(!unknown){
  await page.locator('#ai-assistant-close').click();
  const last=parent.segments.at(-1),oldId=last.current_revision.session_id,card=page.locator('.video-segment-card').last();
  await card.getByRole('button',{name:'修改本段动作',exact:true}).click();
  await card.getByRole('combobox',{name:'选择本段实际动作',exact:true}).selectOption('dumbbell_shoulder_press');
  page.once('filechooser',chooser=>chooser.setFiles(input));
  await card.getByRole('button',{name:'仅重新分析本段',exact:true}).click();
  await card.locator('h3').filter({hasText:'哑铃推肩'}).waitFor({timeout:20000});
  await page.waitForFunction(old=>document.querySelector('#video-auto-page').dataset.aiRecordId!==old,oldId);
  const newId=await page.locator('#video-auto-page').getAttribute('data-ai-record-id');
  assert(newId&&newId!==oldId);
  await card.getByRole('link',{name:'AI解读',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#ai-assistant').dataset.busy==='false');
  assert.equal(contexts.at(-1).context_id,newId);
 }
 await page.setViewportSize({width:390,height:844});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 if(!unknown)await page.screenshot({path:path.join(root,'第四轮优化/实现核对/视频片段-AI读取.png'),animations:'disabled'});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({selectedContexts:contexts.map(v=>v.context_id),posts:posts.length,retainedOriginalPage:true,samplesBoundToSelectedRanges:true,pageErrors:errors}));
}finally{await fixture.close();}
