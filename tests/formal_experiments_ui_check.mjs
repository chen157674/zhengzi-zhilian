// 13 页面与缓存任务链；不训练、不重新预测原视频。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const scenario=process.argv.find(value=>value.startsWith('--case='))?.slice(7)||'complete';
const fixture=new BrowserFixture('ui-step13'),evidence=path.join(root,'data/verification/step-13');
let context,page;const errors=[];let starts=0;
async function mode(value){
  const target=path.join(fixture.temporary,'mode.json');fs.writeFileSync(target+'.new',JSON.stringify(value));
  for(let attempt=0;;attempt++){try{fs.renameSync(target+'.new',target);break}catch(error){if(!['EPERM','EACCES','EBUSY'].includes(error.code)||attempt>=20)throw error;await new Promise(resolve=>setTimeout(resolve,25))}}
}
async function screenshot(name){fs.mkdirSync(evidence,{recursive:true});await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(evidence,name),fullPage:true})}
try{
  await mode(scenario==='cancel'||scenario==='reentry'?{block:'rf-depth4'}:scenario==='partial'?{fail:'rf-depth4'}:{});
  await fixture.startTestService('tests/formal_experiments_service.py');context=await fixture.browser(scenario==='compat'?'Chrome':'Edge',scenario==='compat'?{width:1280,height:900}:{width:1440,height:1000});await fixture.apiLogin(context,'Experiment_Admin','experiment-fixture-password-123');
  context.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/v1/admin/experiments')starts++});
  page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));await page.goto(fixture.origin+'/admin/experiments');
  await page.waitForFunction(()=>!document.querySelector('#experiment-start').disabled);assert.equal(await page.locator('.experiment-method input:checked').count(),2);
  assert.equal(await page.locator('input[value="geometry-rule"]').isDisabled(),true);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  if(scenario==='complete'||scenario==='compat'){
    await screenshot('configuration.png');await page.locator('input[value="rf-depth4"]').uncheck();assert.equal(await page.locator('#experiment-start').isDisabled(),true);await page.locator('input[value="majority"]').check();
  }
  if(scenario==='unknown-create'){
    let dropped=false;await page.route('**/api/v1/admin/experiments',async route=>{if(route.request().method()==='POST'&&!dropped){dropped=true;await route.fetch();await route.abort('failed')}else await route.continue()});
    await page.locator('#experiment-start').click();await page.locator('#experiment-start').filter({hasText:'核对原对比'}).waitFor();
    await page.reload();await page.locator('#experiment-start').filter({hasText:'核对原对比'}).waitFor();assert.equal(starts,1);
  }
  await page.locator('#experiment-start').click();await page.waitForURL('**/admin/experiments/*');const url=page.url(),eid=new URL(url).pathname.split('/').at(-1);
  if(scenario==='cancel'||scenario==='reentry'){
    await page.waitForFunction(()=>document.querySelector('#experiment-progress-text').textContent.startsWith('1 / 2'));assert.equal(await page.locator('#experiment-task').isDisabled(),true);
    if(scenario==='cancel'){
      await page.locator('.management-tabs a[href="/admin/users"]').click();await page.locator('#confirm-dialog').waitFor();await page.locator('[data-dialog-cancel]').click();assert.equal(page.url(),url);
      await page.locator('#experiment-cancel').click();await page.locator('[data-dialog-confirm]').click();await page.locator('#experiment-badge').filter({hasText:'已取消'}).waitFor();
      const data=(await(await context.request.get(fixture.origin+'/api/v1/admin/experiments/'+eid)).json()).data;assert.equal(data.result_completeness,'partial');assert.equal(data.results.processed_methods,1);assert(data.stopped);
      await screenshot('cancelled.png');
    }else{
      await page.close();page=await context.newPage();await page.goto(url);await page.waitForFunction(()=>document.querySelector('#experiment-progress-text').textContent.startsWith('1 / 2'));assert.equal(starts,1);
      await mode({});await page.locator('#experiment-badge').filter({hasText:'已完成对比'}).waitFor();assert.equal(starts,1);
    }
  }else if(scenario==='partial'){
    await page.locator('#experiment-badge').filter({hasText:'对比失败'}).waitFor();const rows=page.locator('#experiment-result-rows tr');assert.equal(await rows.count(),2);
    await rows.nth(0).getByText('已完成',{exact:true}).waitFor();await rows.nth(1).getByText('汇总失败',{exact:true}).waitFor();assert.equal((await rows.nth(1).locator('td').nth(2).innerText()).trim(),'—');await screenshot('partial-failure.png');
  }else if(scenario==='unknown-create'){
    await page.locator('#experiment-badge').filter({hasText:'已完成对比'}).waitFor();assert.equal(starts,2);
    const list=(await(await context.request.get(fixture.origin+'/api/v1/admin/experiments')).json()).data;assert.equal(list.total,1);assert.equal(list.experiments[0].experiment_id,eid);
  }else if(scenario==='compat'){
    await page.locator('#experiment-badge').filter({hasText:'已完成对比'}).waitFor();assert.equal(starts,1);
    const timingCells=await page.locator('#experiment-result-rows tr td:nth-child(7)').allTextContents();assert.equal(timingCells.length,2);assert(timingCells.every(text=>text.startsWith('计时精度不足')));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.locator('#experiment-detail summary').focus();await page.keyboard.press('Enter');await page.getByText('S04',{exact:true}).first().waitFor();
    await page.keyboard.press('Enter');await screenshot('results.png');
  }else{
    await page.locator('#experiment-badge').filter({hasText:'已完成对比'}).waitFor();assert.equal(starts,1);
    const majority=page.locator('#experiment-result-rows tr').filter({hasText:'多数类基线'});assert.match(await majority.locator('td').nth(2).innerText(),/不可计算/);assert.equal((await majority.locator('td').nth(3).innerText()).trim(),'0.0%');
    await page.locator('#experiment-detail summary').click();await page.getByText('S04',{exact:true}).first().waitFor();await page.locator('#experiment-detail summary').click();
    await screenshot('results.png');await page.reload();await page.locator('#experiment-badge').filter({hasText:'已完成对比'}).waitFor();assert.equal(starts,1);
    await page.locator('#experiment-run-again').click();await page.waitForURL(value=>value.pathname.startsWith('/admin/experiments/')&&value.toString()!==url);await page.locator('#experiment-badge').filter({hasText:'已完成对比'}).waitFor();assert.equal(starts,2);
    const list=(await(await context.request.get(fixture.origin+'/api/v1/admin/experiments')).json()).data;assert.equal(list.total,2);assert(list.experiments.some(item=>item.experiment_id===eid));
  }
  assert.deepEqual(errors,[]);console.log(JSON.stringify({browser:await context.browser().version(),scenario,checks:'passed',experimentCreates:starts,mode:'verified frozen prediction cache'}));
}catch(error){console.log(JSON.stringify({errors,url:page?.url(),diagnostic:page?await page.locator('#experiment-error').textContent({timeout:1000}).catch(()=>null):null}));throw error}
finally{await fixture.close()}
