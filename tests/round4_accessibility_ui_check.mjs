// 键盘/弹层与视口模拟；200%以640 CSS像素、2倍像素密度渲染，不冒充手机硬件。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fx=new BrowserFixture('round4-accessibility'),errors=[];
const output=path.join(root,'第四轮优化','实现核对');fs.mkdirSync(output,{recursive:true});
try {
  fx.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Access_Admin','accessibility-fixture-password')
app.extensions['database'].dispose()`);
  fx.start();
  let context=await fx.browser('Edge',{width:390,height:844});
  await fx.apiLogin(context,'Access_Admin','accessibility-fixture-password');
  let page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  let modelPosts=0;
  page.on('request',request=>{if(request.method()==='POST'&&/\/(messages|coach\/tasks)$/.test(request.url()))modelPosts++;});
  await context.addInitScript(()=>{if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{throw new Error('本检查不打开物理相机');};});
  await page.goto(fx.origin+'/realtime');
  await page.locator('[data-ai-open]').click();
  await page.locator('#ai-chat-welcome').waitFor();
  assert.equal(await page.locator('#ai-send-frame').isDisabled(),true,'没有可用画面时不能勾选附图');
  await page.locator('#ai-send-consent').focus();await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'ai-chat-input','焦点循环不能跳过文本域');
  await page.locator('#ai-chat-input').fill('这组先关注什么？');await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'ai-chat-send');
  await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.id),'ai-chat-new');
  await page.keyboard.press('Shift+Tab');assert.equal(await page.evaluate(()=>document.activeElement.id),'ai-chat-send');
  await page.locator('#ai-assistant-close').click();
  assert.equal(await page.locator('main').evaluate(node=>node.inert),false);
  assert.equal(await page.evaluate(()=>document.documentElement.classList.contains('ai-modal-open')),false);
  await page.setViewportSize({width:1440,height:800});
  await page.goto(fx.origin+'/realtime');
  await page.locator('#live-settings>summary').click();
  await page.locator('[data-ai-open]').click();
  const scrollArea=page.locator('.ai-assistant-content');
  assert(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight+100),'使用实际展开的设置制造可滚动外层');
  assert(await scrollArea.evaluate(node=>node.scrollHeight<=node.clientHeight+1),'短内容无需内层滚动');
  const wheelOverContent=async delta=>{
    const box=await scrollArea.boundingBox();
    await page.mouse.move(box.x+box.width/2,box.y+Math.min(box.height/2,100));
    await page.mouse.wheel(0,delta);
    await page.evaluate(()=>new Promise(resolve=>{
      let previous='',stable=0;
      const check=()=>{const next=scrollY+':'+document.querySelector('.ai-assistant-content').scrollTop;stable=next===previous?stable+1:0;previous=next;if(stable>=12)resolve();else requestAnimationFrame(check);};
      requestAnimationFrame(check);
    }));
  };
  await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));
  await wheelOverContent(360);
  await page.waitForFunction(()=>scrollY>30);
  // 受控长回复只验证真实浏览器滚动，不连接供应商。
  await page.locator('#ai-chat-messages').evaluate(node=>{
    const message=document.createElement('p');message.textContent='受控长回复，用于验证滚轮边界与反向滚动。\n'.repeat(150);
    message.style.whiteSpace='pre-wrap';node.append(message);
  });
  await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));
  await scrollArea.evaluate(node=>node.scrollTop=0);
  await wheelOverContent(360);
  await page.waitForFunction(()=>document.querySelector('.ai-assistant-content').scrollTop>30);
  assert(await page.evaluate(()=>scrollY<2),'长内容先滚动侧栏');
  await scrollArea.evaluate(node=>node.scrollTop=node.scrollHeight);
  await wheelOverContent(360);
  await page.waitForFunction(()=>scrollY>30);
  await scrollArea.evaluate(node=>node.scrollTop=0);
  await wheelOverContent(-720);
  await page.waitForFunction(()=>scrollY<2);
  await wheelOverContent(360);
  await page.waitForFunction(()=>document.querySelector('.ai-assistant-content').scrollTop>30);
  await page.locator('#ai-assistant-close').click();
  await page.setViewportSize({width:390,height:844});
  await page.goto(fx.origin+'/coach');
  await page.locator('#ai-chat-input').fill('键盘视口工程检查');
  const normalHeight=await page.evaluate(()=>visualViewport.height);
  await page.evaluate(()=>{
    Object.defineProperty(visualViewport,'height',{configurable:true,get:()=>500});
    visualViewport.dispatchEvent(new Event('resize'));
  });
  await page.waitForFunction(()=>parseFloat(getComputedStyle(document.querySelector('#ai-assistant')).getPropertyValue('--ai-keyboard-inset'))>250);
  await page.waitForFunction(()=>{
    const box=document.querySelector('#ai-chat-input').getBoundingClientRect(),send=document.querySelector('#ai-chat-send').getBoundingClientRect();
    const visibleBottom=visualViewport.offsetTop+visualViewport.height;
    return box.top>=document.querySelector('.topbar').getBoundingClientRect().bottom-1&&box.bottom<=visibleBottom&&send.bottom<=visibleBottom;
  });
  await page.screenshot({path:path.join(output,'AI教练-键盘视口模拟.png'),animations:'disabled'});
  await page.evaluate(height=>{
    Object.defineProperty(visualViewport,'height',{configurable:true,get:()=>height});
    visualViewport.dispatchEvent(new Event('resize'));
  },normalHeight);
  await page.locator('#ai-chat-input').fill('');
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.goto(fx.origin+'/');
  await page.locator('#showcase-toggle:not([disabled])').waitFor();
  assert.equal(await page.locator('#showcase-video').evaluate(video=>video.paused),true,'减少动态时首页不自动播放');
  assert.equal(modelPosts,0);
  await context.close();fx.contexts=[];
  context=await fx.browser('Edge',{width:640,height:450},{deviceScaleFactor:2});
  await fx.apiLogin(context,'Access_Admin','accessibility-fixture-password');
  page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto(fx.origin+'/account/training-profile');
  await page.locator('#training-profile-fields:not([disabled])').waitFor();
  assert.equal(await page.evaluate(()=>devicePixelRatio),2);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('#training-more summary').click();
  await page.locator('#training-minutes').focus();
  await page.waitForFunction(()=>{
    const box=document.querySelector('#training-minutes').getBoundingClientRect();
    return box.top>=document.querySelector('.topbar').getBoundingClientRect().bottom&&box.bottom<=document.querySelector('.topbar nav').getBoundingClientRect().top;
  });
  await page.screenshot({path:path.join(output,'训练档案-200百分比等效缩放.png'),animations:'disabled'});
  assert.deepEqual(errors,[]);
  console.log('Tab/Shift+Tab文本域焦点、弹层解除、桌面侧栏短/长内容原生滚轮及顶底反向接续、不可用画面禁选、键盘视口模拟、减少动态及200%等效渲染通过；未测试真实手机硬件。');
} finally {
  await fx.close();
}
