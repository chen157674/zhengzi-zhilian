// 只验证显式指定的评审账号和现有本机预览；不生成AI回复或改动训练/餐食。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const password=process.env.FITNESS_REVIEW_PASSWORD;
assert(password,'需要通过进程环境提供评审账号密码');
const fx=new BrowserFixture('review-account-ui'),errors=[];
const output=path.join(root,'第五轮优化/实现核对');
const checks=[];
try {
  fx.origin='http://127.0.0.1:8766';
  const context=await fx.browser('Edge',{width:1440,height:1000});
  await fx.apiLogin(context,'review_judge',password);
  const page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/v1/ai/**',route=>{
    assert.equal(route.request().method(),'GET','核对过程中不得生成AI请求');
    return route.continue();
  });
  for(const [url,label,ready] of [
    ['/training/plan','训练计划','#plan-overview:not([hidden])'],
    ['/records?view=list','训练记录','table tbody tr'],
    ['/training/review','成长看板','#review-page[data-growth-state=ready]'],
    ['/coach/meals','餐食与记忆','#journal-records[aria-busy=false]'],
    ['/coach','AI教练','#ai-chat-input'],
  ]){
    await page.goto(fx.origin+url);
    await page.locator(ready).first().waitFor();
    await page.waitForFunction(()=>!document.querySelector('#main [aria-busy=true]'));
    assert(!page.url().includes('/login'),'评审账号应能访问'+label);
    assert.match(await page.locator('.account-name').textContent(),/评审体验/);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    if(label==='训练记录')assert.equal(await page.locator('table tbody tr').count(),10);
    if(label==='训练计划'){
      await page.getByRole('heading',{name:/全身基础训练/}).first().waitFor();
      await page.getByRole('button',{name:/开始.*训练|开始今日训练/}).first().waitFor();
    }
    await page.screenshot({path:path.join(output,'评审账号-'+label+'.png'),fullPage:true,animations:'disabled'});
    checks.push({page:url,label,loaded:true});
  }
  await page.setViewportSize({width:390,height:844});
  await page.goto(fx.origin+'/training/plan');
  await page.getByRole('heading',{name:/全身基础训练/}).first().waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(output,'评审账号-手机.png'),fullPage:true,animations:'disabled'});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'评审账号核对.json'),JSON.stringify({checked_at:new Date().toISOString(),username:'review_judge',checks,errors,provider_requests:0},null,2)+'\n');
  console.log('评审账号登录、计划/记录/成长/餐食/AI页及手机渲染通过；未发起AI或分析。');
} finally {
  for(const context of fx.contexts)await context.request.post(fx.origin+'/api/v1/auth/logout',{
    data:{},headers:{'X-CSRFToken':(await(await context.request.get(fx.origin+'/api/v1/auth/csrf')).json()).data.csrf_token}});
  await fx.close();
}
