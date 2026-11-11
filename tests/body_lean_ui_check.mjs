// 用户确认的演示标注持续显示，原模型反馈保持可核查；不替换视频。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const guidance=process.argv.includes('--guidance');
const fixture=new BrowserFixture(guidance?'movement-guidance-ui':'body-lean-ui');
fixture.env.FITNESS_BODY_LEAN='1';fixture.env.FITNESS_PRETRAINED_CORRECTION='0';
fixture.env.FITNESS_MOVEMENT_GUIDANCE=guidance?'1':'0';
const errors=[];
try{
  fixture.seed(`from fitness_app.paths import configure_environment
configure_environment()
import os
from fitness_app.database import Database
from fitness_app.auth import initialize_admin
db=Database(os.environ['FITNESS_DATABASE_PATH']);db.initialize()
initialize_admin(db,'lean_validator','Local-lean-check-2026')
db.dispose()`);
  fixture.start();
  const browser=await fixture.browser('Edge',{width:1440,height:1040});
  const page=await browser.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto(fixture.origin+'/');
  await page.waitForFunction(()=>document.querySelector('#showcase-video')?.currentTime>.1);
  for(const expected of ['user-1694']){
    await page.evaluate(async()=>{
      const video=document.querySelector('#showcase-video');
      video.currentTime=video.duration-.04;await video.play();
    });
    await page.waitForFunction(id=>document.querySelector('#showcase-video')?.src.includes(id),expected);
    await page.waitForFunction(()=>document.querySelector('#showcase-video')?.readyState>=2);
  }
  const track=JSON.parse(fs.readFileSync(path.join(root,'fitness_app/static/demo/user-1694-track.json'),'utf8'));
  const issue=track.frames.find(frame=>frame.feedback.state==='issue'&&frame.feedback.highlight);
  assert(issue,'原视频应产生实际可见侧倾提示');
  await page.evaluate(async stamp=>{
    const video=document.querySelector('#showcase-video');video.pause();
    await new Promise(resolve=>{video.addEventListener('seeked',resolve,{once:true});video.currentTime=stamp/1000+.015;});
  },issue.t);
  await page.waitForFunction(()=>document.querySelector('#showcase-feedback')?.dataset.state==='issue');
  const title=await page.locator('#showcase-feedback [data-feedback-title]').innerText();
  assert.equal(title,'身体侧倾、握持不当');
  assert(await page.locator('#showcase-feedback [data-feedback-instruction]').isVisible());
  await page.locator('#showcase-player').scrollIntoViewIfNeeded();
  await page.locator('#showcase-player').screenshot({path:path.join(root,guidance?'data/verification/round3/movement-guidance-home.jpg':'data/verification/round3/body-lean-home.jpg')});
  assert.equal(await page.locator('#showcase-error').isVisible(),false);
  const served=await (await browser.request.get(fixture.origin+await page.locator('#home-demo').getAttribute('data-track-url'))).json();
  assert(served.model_results_unchanged);
  assert.deepEqual(served.frames.map(f=>f.model_feedback),track.frames.map(f=>f.feedback));
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:1040});
    for(const stamp of [0.015,3.015,10.015,20.015,26.015]){
      await page.evaluate(async stamp=>{
        const v=document.querySelector('#showcase-video');v.pause();
        await new Promise(resolve=>{v.addEventListener('seeked',resolve,{once:true});v.currentTime=stamp;});
      },stamp);
      await page.waitForFunction(()=>document.querySelector('[data-feedback-title]').textContent==='身体侧倾、握持不当');
      assert.equal(await page.locator('#showcase-feedback').getAttribute('data-state'),'issue');
      assert(await page.locator('[data-feedback-instruction]').isVisible());
      assert(!(await page.locator('#showcase-feedback').innerText()).includes('尚未检出'));
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    }
  }
  await page.locator('#showcase-player').screenshot({path:path.join(root,'data/verification/round3/demo-annotation-mobile.png')});
  assert(await page.locator('.showcase-badges strong').evaluate(el=>getComputedStyle(el).whiteSpace==='nowrap'));
  // 播放完错误演示返回正常演示，下一轮再次进入时标注仍生效。
  await page.evaluate(async()=>{const v=document.querySelector('#showcase-video');v.currentTime=v.duration-.04;await v.play();});
  await page.waitForFunction(()=>document.querySelector('#home-demo').dataset.demoId==='user-1575');
  await page.waitForFunction(()=>document.querySelector('#showcase-video').readyState>=2);
  await page.evaluate(async()=>{const v=document.querySelector('#showcase-video');v.currentTime=v.duration-.04;await v.play();});
  await page.waitForFunction(()=>document.querySelector('#home-demo').dataset.demoId==='user-1694'&&document.querySelector('[data-feedback-title]').textContent==='身体侧倾、握持不当');

  if(guidance){
    await fixture.apiLogin(browser,'lean_validator','Local-lean-check-2026');
    const response=await browser.request.get(fixture.origin+'/api/v1/actions');
    const data=await response.json();const entries=data.data.actions||data.data;
    const action=entries.find(item=>item.action_id==='machine_chest_press');
    assert(action,'动作接口必须包含器械推胸');
    for(const suffix of ['phase_lag','fast_return','wrist_bend'])assert(action.issues.some(item=>item.code.endsWith(suffix)),suffix);
    assert(track.movement_guidance&&track.frames.some(f=>f.feedback.checks.some(c=>c.code.endsWith('wrist_bend'))));
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  }
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({source:'user-1694',source_ms:issue.t,title,persistent_annotation:true,model_feedback_preserved:true,multiple_timestamps_and_loop:true,script_errors:errors.length}));
}finally{await fixture.close();}
