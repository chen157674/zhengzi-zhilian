// 首页一块一屏：视口、原生滚动、锚点和减少动态；不发起分析或AI请求。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fx=new BrowserFixture('home-screen-ui'),errors=[],layouts=[];
const output=path.join(root,'第五轮优化/实现核对');
const ids=['home-start','home-movement','home-training','home-guide','home-ready'];
try {
  fx.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Home_Screen','home-screen-fixture-password')
app.extensions['database'].dispose()`);
  fx.start();
  const context=await fx.browser('Edge',{width:1440,height:1000});
  await fx.apiLogin(context,'Home_Screen','home-screen-fixture-password');
  const page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  const writes=[];
  page.on('request',request=>{if(request.method()==='POST')writes.push(new URL(request.url()).pathname);});
  await page.goto(fx.origin+'/');
  await page.waitForFunction(()=>document.querySelector('#showcase-video')?.readyState>=2);
  await page.locator('#home-ready').evaluate(el=>el.scrollIntoView({block:'start',behavior:'instant'}));
  await page.waitForFunction(()=>Array.from(document.querySelectorAll('.home-ready-visual img')).every(img=>img.complete&&img.naturalWidth>0));
  assert.equal(await page.locator('.home-ready-visual a').count(),3);
  for(const id of ['bodyweight_squat','standing_dumbbell_curl','forearm_plank']){
    assert.equal((await context.request.get(fx.origin+'/actions/'+id)).status(),200);
  }
  for(const [width,height] of [[1920,1080],[1440,1000],[1440,900],[1366,768],[1024,768],[768,1000],[430,932],[390,844],[320,568],[844,390]]){
    await page.setViewportSize({width,height});
    const row=await page.evaluate(()=>{
      const header=document.querySelector('.topbar').getBoundingClientRect();
      return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,headerHeight:header.height,
        snap:getComputedStyle(document.documentElement).scrollSnapType,
        sections:Array.from(document.querySelectorAll('.home-screen')).map(el=>{
          const box=el.getBoundingClientRect();
          const content=Array.from(el.children).filter(n=>!n.hidden&&getComputedStyle(n).position!=='absolute').map(n=>n.getBoundingClientRect());
          return {id:el.id,height:box.height,minHeight:parseFloat(getComputedStyle(el).minHeight),
            firstTop:Math.min(...content.map(r=>r.top))-box.top,lastBottom:Math.max(...content.map(r=>r.bottom))-box.bottom,
            overflow:getComputedStyle(el).overflow};
        })};
    });
    assert(row.scrollWidth<=width,`${width}横向溢出`);
    assert.equal(row.sections.length,5);
    for(const section of row.sections){
      assert(section.height>=section.minHeight-.5,JSON.stringify(section));
      assert(section.firstTop>=-1&&section.lastBottom<=1,'板块内容越界 '+JSON.stringify({width,height,...section}));
      if(width>=1024&&height>=768){
        assert(Math.abs(section.height-(height-row.headerHeight))<=1,'桌面每块应完整占一屏 '+JSON.stringify({width,height,...section}));
      }
    }
    if(width<768)assert.equal(row.snap,'none','手机保留自由滚动');
    const photo=await page.locator('.home-ready-main img').boundingBox();
    assert(photo.height>100&&photo.width>220,'收尾训练图片应可见且有足够面积');
    layouts.push(row);
  }
  await page.setViewportSize({width:1440,height:1000});
  for(const id of ids){
    await page.locator('#'+id).evaluate(el=>el.scrollIntoView({block:'start',behavior:'instant'}));
    await page.waitForFunction(id=>Math.abs(document.getElementById(id).getBoundingClientRect().top-80)<2,id);
    await page.screenshot({path:path.join(output,'首页分屏-'+id+'.png'),animations:'disabled'});
  }
  // 原生滚轮须能从一屏前进、反向返回；没有wheel劫持或嵌套滚动陷阱。
  await page.locator('#home-start').evaluate(el=>el.scrollIntoView({block:'start',behavior:'instant'}));
  await page.mouse.move(1100,700);
  await page.mouse.wheel(0,920);
  await page.waitForFunction(()=>Math.abs(document.querySelector('#home-movement').getBoundingClientRect().top-80)<3);
  await page.mouse.wheel(0,-920);
  await page.waitForFunction(()=>scrollY<3);
  await page.getByRole('link',{name:'跳到主要内容'}).focus();
  await page.keyboard.press('PageDown');
  await page.waitForFunction(()=>scrollY>300);
  await page.goto(fx.origin+'/#home-training');
  await page.waitForFunction(()=>Math.abs(document.querySelector('#home-training').getBoundingClientRect().top-80)<3);
  await page.setViewportSize({width:390,height:844});
  for(const id of ['home-movement','home-training','home-ready']){
    await page.locator('#'+id).evaluate(el=>el.scrollIntoView({block:'start',behavior:'instant'}));
    await page.screenshot({path:path.join(output,'首页分屏-'+id+'-手机.png'),animations:'disabled'});
  }
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).scrollSnapType),'none');
  await page.goto(fx.origin+'/coach/knowledge');
  assert.notEqual(await page.evaluate(()=>getComputedStyle(document.documentElement).scrollSnapType),'y proximity','分屏样式不能影响其他页面');
  assert.deepEqual(writes,[]);
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'首页分屏核对.json'),JSON.stringify({checked_at:new Date().toISOString(),layouts,
    checks:['桌面一块一屏','十档宽高无横向溢出','矮屏内容自然展开','原生滚轮正反向','PageDown','锚点避开顶栏','减少动态','其他页面不受影响','收尾三张训练图片可见','收尾动作入口可达'],
    writes,errors},null,2)+'\n');
  console.log('首页五块分屏、十档尺寸、原生滚轮/键盘/锚点和减少动态检查通过。');
} finally {
  await fx.close();
}
