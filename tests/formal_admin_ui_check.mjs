import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step05');
const evidence=path.resolve(root,process.env.FITNESS_UI_EVIDENCE_DIR||'第四轮优化/实现核对');assert(!path.relative(root,evidence).startsWith('..'),'证据目录须在项目内');fs.mkdirSync(evidence,{recursive:true});
const password='admin-ui-fixture-123';
const seed=`import sys,json,os\nfrom fitness_app.database import Database\nfrom fitness_app.auth import initialize_admin,password_hash\nfrom fitness_app import db_schema as s\nd=json.load(sys.stdin);db=Database(os.environ['FITNESS_DATABASE_PATH']);initialize_admin(db,'UI_Admin',d['password']);hashed=password_hash(d['password'])\nwith db.transaction() as c:\n c.execute(s.users.insert(),[dict(username_display=name,username_key=name.casefold(),password_hash=hashed,enabled=0 if name=='Disabled_Test' else 1) for name in ['Target_One','Target_Two','Disabled_Test']+[f'Member_{i:02}' for i in range(22)]])\ndb.dispose()\nprint('Admin UI fixtures ready')`;
try {
  fixture.seed(seed,{password});const origin=fixture.start();
  if(process.argv.includes('--credential-only')) {
    const context=await fixture.browser();await fixture.apiLogin(context,'UI_Admin',password);const page=context.pages()[0];const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin+'/admin/users');await page.locator('#account-rows tr[data-user-id]').first().waitFor();await page.locator('#admin-search').fill('Target_Two');await page.waitForFunction(()=>document.querySelectorAll('#account-rows tr[data-user-id]').length===1&&document.querySelector('.username-cell').textContent==='Target_Two');
    await page.locator('[data-action="admin.reset-password"]').click();await page.locator('[data-dialog-confirm]').click();await page.locator('#reset-result[open]').waitFor();
    assert((await page.locator('#temporary-expiry').innerText()).includes('30 分钟'));
    await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{window.fixtureCopyLength=value.length}}}));
    await page.locator('[data-action="admin.copy-temporary"]').click();await page.waitForFunction(()=>document.querySelector('[data-action="admin.copy-temporary"]').textContent==='已复制');assert(await page.evaluate(()=>window.fixtureCopyLength>=12));
    await page.evaluate(()=>{window.fixtureRealNow=Date.now;Date.now=()=>window.fixtureRealNow()+31*60*1000});
    try{await page.waitForFunction(()=>document.querySelector('#temporary-password').value===''&&document.querySelector('#reset-result-message').textContent.includes('已过期'),{},{timeout:5000})}finally{await page.evaluate(()=>{Date.now=window.fixtureRealNow;delete window.fixtureRealNow})}
    assert(await page.locator('[data-action="admin.reveal-temporary"]').isDisabled());assert(await page.locator('[data-action="admin.copy-temporary"]').isDisabled());assert(await page.locator('#reset-again').isVisible());await page.keyboard.press('Escape');assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,browser:'Edge',version:context.browser().version(),scope:'复制成功/30分钟提示/页面到期清除增量',system_clipboard_accessed:false,system_clock_changed:false}));
  } else if(process.argv.includes('--compat-only')) {
    const context=await fixture.browser('Chrome',{width:1280,height:720});await fixture.apiLogin(context,'UI_Admin',password);
    const page=context.pages()[0];const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin+'/admin/users');await page.locator('#account-rows tr[data-user-id]').first().waitFor();
    await page.locator('#admin-search').fill('Target_Two');await page.waitForFunction(()=>document.querySelectorAll('#account-rows tr[data-user-id]').length===1&&document.querySelector('.username-cell').textContent==='Target_Two');
    await page.locator('[data-action="admin.reset-password"]').click();await page.locator('#confirm-dialog[open]').waitFor();await page.keyboard.press('Escape');
    assert(!(await page.locator('#confirm-dialog').isVisible()));assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
    const count=fixture.seed("import os,sqlite3\nfrom contextlib import closing\nwith closing(sqlite3.connect(os.environ['FITNESS_DATABASE_PATH'])) as c: print(c.execute(\"SELECT count(*) FROM admin_operations WHERE operation='reset'\").fetchone()[0])");assert.equal(count.trim(),'0');
    console.log(JSON.stringify({passed:true,browser:'Chrome',version:context.browser().version(),scope:'管理页/搜索/确认取消/小窗口兼容增量',reset_operations:0}));
  } else {
  const context=await fixture.browser();await fixture.apiLogin(context,'UI_Admin',password);
  const versions={Edge:context.browser().version()};
  const page=context.pages()[0];const errors=[];page.on('pageerror',error=>errors.push(error.message));
  let disables=0,resets=0;const resetKeys=[];
  page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/disable'))disables++;if(request.method()==='POST'&&request.url().endsWith('/password-reset')){resets++;resetKeys.push(request.headers()['idempotency-key'])}});
  await page.goto(origin+'/admin/users');await page.waitForFunction(()=>document.querySelector('#account-total').textContent.includes('26'));
  assert.equal(await page.locator('#account-rows tr[data-user-id]').count(),20);
  await page.locator('#admin-next').click();await page.waitForFunction(()=>document.querySelector('#admin-page-label').textContent.includes('2 / 2'));
  assert.equal(await page.locator('#account-rows tr[data-user-id]').count(),6);
  const search=page.locator('#admin-search');const status=page.locator('#admin-status');
  async function find(name){await search.fill(name);await page.waitForFunction(value=>{const rows=[...document.querySelectorAll('#account-rows tr[data-user-id]')];return rows.length===1&&rows[0].querySelector('.username-cell').textContent===value},name);return page.locator('#account-rows tr[data-user-id]').first()}
  let releaseFirst;const barrier=new Promise(resolve=>releaseFirst=resolve);
  await page.route('**/api/v1/admin/users?**',async route=>{if(new URL(route.request().url()).searchParams.get('search')==='Target_One')await barrier;await route.continue()});
  try {
    const firstRequest=page.waitForRequest(request=>new URL(request.url()).pathname==='/api/v1/admin/users'&&new URL(request.url()).searchParams.get('search')==='Target_One');
    await search.fill('Target_One');await firstRequest;await find('Target_Two');
    const lateResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/admin/users'&&new URL(response.url()).searchParams.get('search')==='Target_One');releaseFirst();await lateResponse;await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await page.locator('.username-cell').innerText(),'Target_Two');
  }finally{releaseFirst();await page.unroute('**/api/v1/admin/users?**')}
  let row=await find('UI_Admin');assert(await row.locator('[data-action="admin.reset-password"]').isDisabled());assert(await row.locator('[data-action="admin.disable"]').isDisabled());
  row=await find('Disabled_Test');assert(await row.locator('[data-action="admin.reset-password"]').isDisabled());assert(await row.locator('[data-action="admin.disable"]').isDisabled());
  await status.selectOption('1');row=await find('Target_One');await row.locator('[data-action="admin.disable"]').click();await page.locator('#confirm-dialog[open]').waitFor();assert((await page.locator('#confirm-message').innerText()).includes('Target_One'));await page.keyboard.press('Escape');assert.equal(disables,0);
  await row.locator('[data-action="admin.disable"]').click();await page.evaluate(()=>{const button=document.querySelector('[data-dialog-confirm]');button.click();button.click()});
  await page.waitForFunction(()=>!document.querySelector('#confirm-dialog').open&&document.querySelector('#account-rows').textContent.includes('没有匹配账号'));
  assert.equal(disables,1);assert.equal(await page.evaluate(()=>document.activeElement.id),'account-table');
  await status.selectOption('');row=await find('Target_Two');await row.locator('[data-action="admin.reset-password"]').click();await page.locator('[data-dialog-confirm]').click();await page.locator('#reset-result[open]').waitFor();
  const temporary=page.locator('#temporary-password');assert.equal(await temporary.getAttribute('type'),'password');const secret=await temporary.inputValue();assert(secret.length>=12,'必须取得实际临时凭据');assert.equal(resets,1);
  await page.locator('[data-action="admin.reveal-temporary"]').click();assert.equal(await temporary.getAttribute('type'),'text');await page.locator('[data-action="admin.reveal-temporary"]').click();
  await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('test clipboard denied')}}}));
  await page.locator('[data-action="admin.copy-temporary"]').click();await page.waitForFunction(()=>document.querySelector('#reset-result-message').textContent.includes('复制失败'));assert.notEqual(await page.locator('[data-action="admin.copy-temporary"]').innerText(),'已复制');
  assert(!(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}))).includes(secret),'临时密码不得进入持久缓存');
  await page.mouse.click(5,5);assert(await page.locator('#reset-result').isVisible());await page.screenshot({path:path.join(evidence,'密码重置结果.png'),fullPage:true});await page.keyboard.press('Escape');
  assert((await temporary.inputValue())==='','关闭结果必须清空凭据');assert(!(await page.locator('#reset-result').isVisible()));
  row=await find('Target_Two');await row.locator('[data-action="admin.reset-password"]').click();await page.keyboard.press('Escape');assert.equal(resets,1);
  let dropNext=true;await page.route('**/password-reset',async route=>{if(dropNext){dropNext=false;await route.fetch();await route.abort()}else await route.continue()});
  await row.locator('[data-action="admin.reset-password"]').click();await page.locator('[data-dialog-confirm]').click();await page.locator('#reset-result[open]').waitFor();
  assert((await page.locator('#reset-title').innerText()).includes('未取得临时密码'));assert(await page.locator('#temporary-section').isHidden());assert((await temporary.inputValue())==='');assert.equal(resets,2);
  await page.locator('#reset-again').click();await page.locator('#confirm-dialog[open]').waitFor();assert.equal(resets,2,'重新取得凭据前必须再确认');
  await page.locator('[data-dialog-confirm]').click();await page.locator('#reset-result[open]').waitFor();assert(await page.locator('#temporary-section').isVisible());assert.equal(resets,3);assert.equal(new Set(resetKeys).size,3);
  await page.locator('#reset-result [data-action="admin.close-reset-result"]').last().click();assert((await temporary.inputValue())==='');await page.unroute('**/password-reset');
  await search.fill('Target');await page.waitForFunction(()=>document.querySelectorAll('#account-rows tr[data-user-id]').length===2);await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(evidence,'账号管理-操作.png'),fullPage:true});
  await page.setViewportSize({width:1280,height:720});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  const chrome=await fixture.browser('Chrome',{width:1280,height:720});versions.Chrome=chrome.browser().version();
  await fixture.apiLogin(chrome,'UI_Admin',password);const compatibility=chrome.pages()[0];const chromeErrors=[];compatibility.on('pageerror',error=>chromeErrors.push(error.message));
  await compatibility.goto(origin+'/admin/users');await compatibility.locator('#account-rows tr[data-user-id]').first().waitFor();
  await compatibility.locator('#admin-search').fill('Target_Two');await compatibility.waitForFunction(()=>document.querySelectorAll('#account-rows tr[data-user-id]').length===1&&document.querySelector('.username-cell').textContent==='Target_Two');
  await compatibility.locator('[data-action="admin.reset-password"]').click();await compatibility.locator('#confirm-dialog[open]').waitFor();await compatibility.keyboard.press('Escape');
  assert(!(await compatibility.locator('#confirm-dialog').isVisible()));assert(await compatibility.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(chromeErrors,[]);
  const stats=JSON.parse(fixture.seed("import os,sqlite3,json\nfrom contextlib import closing\nwith closing(sqlite3.connect(os.environ['FITNESS_DATABASE_PATH'])) as c: print(json.dumps(dict(resets=c.execute(\"SELECT count(*) FROM admin_operations WHERE operation='reset'\").fetchone()[0],disables=c.execute(\"SELECT count(*) FROM admin_operations WHERE operation='disable'\").fetchone()[0],records=c.execute('SELECT count(*) FROM analysis_sessions').fetchone()[0])))"));
  assert.equal(stats.resets,3);assert.equal(stats.disables,1);assert.equal(stats.records,0);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,scope:'T05-01..04',browsers:versions,reset_requests:resets,disable_requests:disables,stats,evidence:path.relative(root,evidence)}));
  }
}finally{await fixture.close()}
