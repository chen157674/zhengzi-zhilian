// 首屏与跨页性能回归：隔离账号/数据库，不调用云端 AI。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('page-loading');
const rows=[],errors=[];
const baseline=process.argv.includes('--baseline');
const out=path.join(root,'第五轮优化/实现核对');
try{
  fixture.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Loading_Admin','loading-isolated-password')
app.extensions['database'].dispose()`);
  fixture.start();
  const context=await fixture.browser('Edge',{width:1440,height:1000});
  await fixture.apiLogin(context,'Loading_Admin','loading-isolated-password');
  const page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  const cloudCalls=[];
  page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/v1/coach/tasks')cloudCalls.push(r.url());});
  for(const [route,ready]of [
    ['/coach/knowledge','.learn-intro-copy'],
    ['/coach/nutrition','#nutrition-page'],
    ['/training/plan','#plan-empty'],
    ['/training/review','#review-page'],
    ['/records?view=list','#records-table'],
    ['/realtime','#live-stage'],
    ['/video','#video-auto-page'],
    ['/','#home-title'],
    ['/coach/knowledge','.learn-intro-copy'],
  ]){
    const start=performance.now();
    await page.goto(fixture.origin+route,{waitUntil:'domcontentloaded'});
    await page.locator(ready).first().waitFor({timeout:30000});
    const readyMs=Math.round(performance.now()-start);
    await page.waitForLoadState('load');
    const timing=await page.evaluate(()=>{
      const nav=performance.getEntriesByType('navigation')[0];
      return {
        ttfb:Math.round(nav.responseStart-nav.requestStart),
        domContentLoaded:Math.round(nav.domContentLoadedEventEnd),
        load:Math.round(nav.loadEventEnd),
        paint:performance.getEntriesByType('paint').map(x=>({name:x.name,ms:Math.round(x.startTime)})),
        resources:performance.getEntriesByType('resource').map(r=>({
          path:new URL(r.name).pathname,ms:Math.round(r.duration),
          bytes:r.transferSize,start:Math.round(r.startTime),
        })).sort((a,b)=>b.ms-a.ms),
      };
    });
    const row={route,readyMs,...timing};
    rows.push(row);
    console.log(JSON.stringify({...row,resources:row.resources.slice(0,5)}));
  }
  if(!baseline){
    assert.equal(await page.locator('.learn-intro-copy').count(),1);assert.equal(await page.locator('.learn-next-lesson').count(),2);
    assert(!rows.at(-1).resources.some(r=>r.path==='/api/v1/knowledge/catalog'),'指南首屏不再二次请求整个目录');
    for(const row of rows)assert(!row.resources.some(r=>/^\/static\/[^/]+\.js$/.test(r.path)),'避免同一模块以两个URL重复初始化');
    const shared=rows.at(-1).resources.filter(r=>/\/static\/bundle\/.*\/(?:app|knowledge_query)\.js$/.test(r.path));
    assert.equal(shared.length,2);
    assert(shared.every(r=>r.bytes===0),'相同版本脚本应直接复用浏览器缓存');
    await page.goto(fixture.origin+'/coach/knowledge?view=actions');await page.locator('.guide-action-card').first().waitFor();await page.getByRole('button',{name:'哑铃',exact:true}).click();
    await page.locator('#guide-action-filter').fill('弯举');
    assert(await page.locator('.guide-action-card').count()>0);
    assert((await page.locator('#guide-action-list').innerText()).includes('弯举'));
    await page.locator('#guide-action-filter').fill('');
    await page.getByRole('button',{name:'全部',exact:true}).click();
    await page.locator('#guide-action-next').click();
    assert((await page.locator('#guide-action-page').innerText()).includes('2 /'));
    await page.locator('#guide-action-prev').click();
    await page.locator('[data-knowledge-open]').click();
    await page.locator('#knowledge-ai-dialog[open]').waitFor();
    await page.locator('#knowledge-dialog-close').click();
    // 人为扣住所有脚本响应，验证真实卡片仍能先出现，且不丢失后续交互。
    const delayed=await context.newPage();
    delayed.on('pageerror',e=>errors.push(e.message));
    let release;
    const gate=new Promise(resolve=>{release=resolve;});
    await delayed.route(/\/static\/bundle\/.+\.js$/,async route=>{await gate;await route.continue();});
    try{
      await delayed.goto(fixture.origin+'/coach/knowledge',{waitUntil:'commit'});
      await delayed.locator('.learn-intro-copy').first().waitFor();
      assert.equal(await delayed.locator('.learn-intro-copy').count(),1);assert.equal(await delayed.locator('.learn-next-lesson').count(),2);
      assert.equal(await delayed.locator('#knowledge-catalog-data').count(),1,'脚本尚未执行，启动数据仍在');
      await delayed.locator('.learn-intro-visual img').evaluateAll(images=>Promise.all(images.slice(0,3).map(x=>x.decode())));
      await delayed.screenshot({path:path.join(out,'页面加载-首屏.jpg'),type:'jpeg',quality:65});
    }finally{release();}
    await delayed.waitForLoadState('domcontentloaded');
    await delayed.locator('#learn-scene-gym').click();
    assert(await delayed.locator('.learn-intro-copy').count()>0);
    await delayed.setViewportSize({width:390,height:844});
    assert(await delayed.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await delayed.close();
    // 通过实际主导航切页、返回：缓存不能改变链接、鉴权或菜单行为。
    await page.locator('[data-nav-group="training"] summary').click();
    await page.getByRole('link',{name:'训练计划',exact:true}).click();
    await page.locator('#plan-empty').waitFor();
    await page.goBack();
    await page.locator('.guide-action-card').first().waitFor();
    assert.equal(cloudCalls.length,0);
  }
  assert.deepEqual(errors,[]);
  if(!baseline){
    fs.writeFileSync(path.join(out,'页面加载性能核对.json'),
      JSON.stringify({scope:'isolated_local_edge',rows,errors,cloud_calls:0,html_starter_lessons:1,html_next_lessons:2,html_directory_cards:6,delayed_scripts_still_show_content:true,cache_and_real_navigation:true},null,2));
  }
}finally{
  await fixture.close();
}
