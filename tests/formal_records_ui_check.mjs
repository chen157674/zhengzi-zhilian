// 06 新页面与故障分支；不重跑 03/05 的密码、管理员和权限全矩阵。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step06-check');
const password='record-fixture-password-123';
let context,seed;
const compatibility=process.argv.includes('--compat-only');
const capture=process.argv.includes('--capture-only');
const evidence=path.resolve(root,process.env.FITNESS_UI_EVIDENCE_DIR||'第四轮优化/实现核对');
assert(!path.relative(root,evidence).startsWith('..'),'证据目录须在项目内');fs.mkdirSync(evidence,{recursive:true});
async function post(url,body,key=crypto.randomUUID()){
  const token=(await(await context.request.get(fixture.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;
  return context.request.post(fixture.origin+url,{data:body,headers:{'X-CSRFToken':token,'Idempotency-Key':key}});
}
async function snapshot(id){return (await(await context.request.get(fixture.origin+'/api/v1/sessions/'+id)).json()).data}
function fault(blocked){fs.writeFileSync(path.join(fixture.temporary,'fault.json'),JSON.stringify({blocked}))}
try{
  await fixture.startTestService('tests/formal_records_service.py');
  seed=JSON.parse(fs.readFileSync(path.join(fixture.temporary,'fixture.json'),'utf8'));
  context=await fixture.browser(compatibility?'Chrome':'Edge',compatibility?{width:1280,height:900}:{width:1440,height:1000});
  await fixture.apiLogin(context,'Record_User',password);
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  if(capture){
    await page.goto(fixture.origin+'/records/'+seed.failed[0]);await page.locator('#save-banner').waitFor();
    await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(220);await page.screenshot({path:path.join(evidence,'记录保存失败.png'),fullPage:true});
  }else{
  await page.goto(fixture.origin+'/records?view=list');await page.locator('#record-total').filter({hasText:'共 26 条'}).waitFor();
  assert.equal(await page.locator('#record-rows tr').count(),10);
  await page.setViewportSize({width:390,height:844});
  const mobileRows=await page.locator('#record-rows tr[data-session-id]').evaluateAll(nodes=>nodes.map(node=>{const row=node.getBoundingClientRect(),actions=node.querySelector('.row-actions').getBoundingClientRect(),state=node.children[4].getBoundingClientRect();return {left:row.left,right:row.right,actionsLeft:actions.left,actionsRight:actions.right,stateRight:state.right};}));
  assert(mobileRows.length===10&&mobileRows.every(row=>row.left>=0&&row.right<=390&&row.actionsLeft>=0&&row.actionsRight<=390&&row.stateRight<=390),'手机每条记录的状态与操作均在屏内');
  const firstChoice=page.locator('#record-rows tr[data-session-id] input:not([disabled])').first();await firstChoice.check();assert.equal(await page.locator('#record-selection').innerText(),'已选 1 条');await firstChoice.uncheck();assert.equal(await page.locator('#record-selection').textContent(),'');
  await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));
  await page.screenshot({path:path.join(evidence,'训练记录-有内容窄屏.png'),animations:'disabled'});
  await page.setViewportSize({width:1440,height:1000});

  assert.match(await page.locator('#record-page-label').innerText(),/1 \/ 3/);
  await page.locator('#record-select-page').check();assert.equal(await page.locator('#record-select-page').isChecked(),true);
  await page.locator('#record-next').click();await page.locator('#record-page-label').filter({hasText:'第 2 / 3 页'}).waitFor();
  assert.equal(await page.locator('#record-selection').textContent(),'');
  const remove=page.locator('#record-rows tr').first().getByRole('button',{name:'删除',exact:true});
  let deletes=0;page.on('request',request=>{if(request.url().endsWith('/api/v1/records/delete')&&request.method()==='POST')deletes++});
  await remove.click();await page.getByRole('button',{name:'取消',exact:true}).click();assert.equal(deletes,0);
  if(!compatibility){
    await page.locator('#record-select-page').check();assert.equal(await page.locator('#record-selection').innerText(),'已选 10 条');
    await page.locator('#record-delete-selected').click();await page.getByRole('button',{name:'删除记录',exact:true}).click();
    await page.locator('#record-total').filter({hasText:'共 16 条'}).waitFor();assert.equal(deletes,1);
    await page.locator('#record-search').fill('肩推');await page.locator('#record-total').filter({hasText:'共 1 条'}).waitFor();
    await page.locator('#record-rows [data-action="history.open"]').click();await page.locator('#detail-title').filter({hasText:'站姿杠铃过顶推举'}).waitFor();
    await page.locator('#record-tab-info').click();
    assert.match(await page.locator('#detail-coverage').innerText(),/0 \/ 1/);await page.locator('#record-tab-events').click();
    assert.match(await page.locator('#detail-event-list').innerText(),/质量无法评估/);
    await page.locator('#page-back').click();await page.locator('#record-total').filter({hasText:'共 1 条'}).waitFor();assert.equal(await page.locator('#record-search').inputValue(),'肩推');
    await page.locator('#record-clear').click();await page.locator('#record-total').filter({hasText:'共 16 条'}).waitFor();
    // API 的错误批次检查只补本页确认集合与版本联动，事务约束沿用先前证据。
    const record=(await(await context.request.get(fixture.origin+'/api/v1/records?page=2&page_size=10')).json()).data.records[0];
    const invalid=await post('/api/v1/records/delete',{ids:[record.session_id,seed.active],expected_versions:{[record.session_id]:record.state_version,[seed.active]:(await snapshot(seed.active)).state_version}});
    assert.equal(invalid.status(),409);assert.equal((await context.request.get(fixture.origin+'/api/v1/records/'+record.session_id)).status(),200);
    await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(220);await page.screenshot({path:path.join(evidence,'训练记录.png'),fullPage:true});
    // 等待自动重试次数耗尽，只在这一分支检验页面手动重交。
    await page.goto(fixture.origin+'/records/'+seed.failed[0]);await page.locator('#save-banner').waitFor();
    await page.locator('#record-tab-info').click();
    assert.match(await page.locator('#detail-count').innerText(),/1 次/);assert.match(await page.locator('#save-description').innerText(),/第 2 次/);
    await page.locator('#save-discard').click();await page.getByRole('button',{name:'取消',exact:true}).click();assert.equal((await snapshot(seed.failed[0])).pending.event_seq,2);
    await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(220);await page.screenshot({path:path.join(evidence,'记录保存失败.png'),fullPage:true});
    fault(false);await page.locator('#save-retry').click();await page.locator('#detail-count').filter({hasText:'2 次'}).waitFor();
    assert.match(await page.locator('#detail-save-status').innerText(),/分析仍暂停/);
    // 第二个故障保持原已保存 1 次，只放弃未提交第 2 次。
    fault(true);await page.goto(fixture.origin+'/records/'+seed.failed[1]);await page.locator('#save-banner').waitFor();
    await page.locator('#save-discard').click();await page.getByRole('button',{name:'确认放弃未保存部分',exact:true}).click();
    await page.locator('#records-return-message').waitFor();assert.match(await page.locator('#records-return-message').innerText(),/终态尚未写入/);
    let result=await snapshot(seed.failed[1]);assert.equal(result.total_count,1);assert.equal(result.unsaved_discarded,true);assert.equal(result.terminal_persisted,false);
    fault(false);const reconcile=await post('/api/v1/sessions/'+seed.failed[1]+'/commands',{command:'retry_save',expected_version:result.state_version});assert.equal(reconcile.status(),200);
    result=await snapshot(seed.failed[1]);assert.equal(result.status,'interrupted');assert.equal(result.total_count,1);
    await page.goto(fixture.origin+'/records/'+seed.ids[0]);await page.locator('#detail-title').filter({hasText:'站姿杠铃过顶推举'}).waitFor();
    assert.equal(await page.locator('.record-resources [role=tab]').count(),3);
    assert.equal(await page.locator('#detail-summary').getAttribute('role'),'tabpanel');
    assert.equal(await page.locator('#detail-summary>summary,#training-note-panel>summary,#record-events>summary').count(),0,'不再保留三个折叠卡片头');
    await page.locator('#record-tab-info').focus();await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#record-tab-note').getAttribute('aria-selected'),'true');
    assert.equal(await page.locator('#training-note-panel').isVisible(),true);
    await page.locator('#training-note-fields:not([disabled])').waitFor();
    await page.locator('#note-text').fill('标签切换保留的未保存输入');
    await page.locator('#record-tab-events').click();assert.equal(await page.locator('#record-events').isVisible(),true);
    await page.locator('#record-tab-note').click();assert.equal(await page.locator('#note-text').inputValue(),'标签切换保留的未保存输入');
    await page.locator('#training-note-cancel').click();
    await page.locator('#record-tab-note').focus();await page.keyboard.press('Home');
    assert.equal(await page.locator('#record-tab-info').getAttribute('aria-selected'),'true');
    for(const width of [1440,1060,768,390,320]){
      await page.setViewportSize({width,height:1000});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'资料区不能横向溢出');
      assert.equal(await page.locator('.record-resources [role=tabpanel]:visible').count(),1);
      assert(await page.locator('.record-resource-tabs').evaluate(node=>node.getBoundingClientRect().height<65),'标签区不能撑成大卡片');
      if(width===390)await page.screenshot({path:path.join(evidence,'训练记录详情-窄屏.png'),fullPage:true});
    }
    await page.setViewportSize({width:1440,height:1000});

    await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(220);await page.screenshot({path:path.join(evidence,'训练记录详情.png'),fullPage:true});
    // 旧响应不得覆盖新查询；只在新实现的历史筛选做一次。
    await page.goto(fixture.origin+'/records?view=list');await page.locator('#record-total').filter({hasText:'共 16 条'}).waitFor();
    let delayed=false;
    await page.route('**/api/v1/records?**',async route=>{const url=new URL(route.request().url());if(url.searchParams.get('search')==='深蹲'){delayed=true;await new Promise(resolve=>setTimeout(resolve,650))}await route.continue()});
    await page.locator('#record-search').fill('深蹲');await page.waitForFunction(()=>document.querySelector('#records-table').getAttribute('aria-busy')==='true');
    await page.locator('#record-search').fill('肩推');await page.locator('#record-total').filter({hasText:'共 1 条'}).waitFor();await page.waitForTimeout(750);assert(delayed);assert.equal(await page.locator('#record-total').innerText(),'共 1 条');
  }
  }
  const geometry=await page.evaluate(()=>({width:innerWidth,document:document.documentElement.scrollWidth}));assert(geometry.document<=geometry.width);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({browser:await context.browser().version(),compatibility,captureOnly:capture,checks:capture?'captured':'passed'}));
}finally{
  // 即使用例中途失败，也仅清理本隔离实例，先解除其故障再保存原批。
  if(seed&&context){fault(false);for(const id of seed.failed){const result=await snapshot(id);if(result.save_state==='save_failed')await post('/api/v1/sessions/'+id+'/commands',{command:'retry_save',expected_version:result.state_version})}}
  await fixture.close();
}
