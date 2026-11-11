// 只验证步骤 04 的新增页面／连接；复用现有浏览器，不运行旧原型测试。
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const root=path.resolve(import.meta.dirname,'..');
assert.equal(root,'E:\\07_Projects\\05_PersonalProjects\\10_fitness-assistant');
const temporary=path.join(root,'.tmp/tests/ui-step04');
assert(!fs.existsSync(temporary),'测试目录已存在，先核实归属，不覆盖其他会话');
fs.mkdirSync(temporary,{recursive:true});
const evidence=path.join(root,'data/verification/step-04');fs.mkdirSync(evidence,{recursive:true});
const python=path.join(root,'.venv/Scripts/python.exe');
const env={...process.env,TEMP:temporary,TMP:temporary,PYTHONDONTWRITEBYTECODE:'1',PYTHONUTF8:'1',
  FITNESS_RUNTIME_DIR:path.join(temporary,'runtime'),FITNESS_DATABASE_PATH:path.join(temporary,'app.sqlite3')};
const require=createRequire('C:/Users/chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/package.json');
const playwright=await import(pathToFileURL(require.resolve('playwright')).href);
const {chromium}=playwright.default||playwright;
const password='ui-fixture-password-123',changed='ui-fixture-changed-456';
const seed=`import json,sys,os\nfrom sqlalchemy import update\nfrom fitness_app.database import Database\nfrom fitness_app.auth import initialize_admin,password_hash\nfrom fitness_app import db_schema as s\nd=json.load(sys.stdin);db=Database(os.environ['FITNESS_DATABASE_PATH']);initialize_admin(db,'UI_Admin',d['password'])\nwith db.transaction() as c:c.execute(s.users.insert().values(username_display='UI_Temporary',username_key='ui_temporary',password_hash=password_hash(d['password']),must_change_password=1,temporary_password_hash=password_hash(d['password']),temporary_expires_at_ms=s.now_ms()+1800000))\ndb.dispose()\nprint('UI fixtures ready')`;
const call=(args,options={})=>execFileSync(python,['-B',...args],{cwd:root,env,windowsHide:true,encoding:'utf8',...options});
let context=null,started=false;const results=[];const layoutOnly=process.argv.includes('--layout-only');const captureOnly=process.argv.includes('--capture-only');const keyboardOnly=process.argv.includes('--keyboard-only');const dialogOnly=process.argv.includes('--dialog-only');
try{
  call(['-c',seed],{input:JSON.stringify({password})});
  const startedInfo=JSON.parse(call(['-m','fitness_app','--no-browser','--port','0']).trim());started=true;
  const origin=`http://127.0.0.1:${startedInfo.port}`;
  for(const [name,executable,wantsFull] of [['Edge','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',true],['Chrome','C:/Program Files/Google/Chrome/Application/chrome.exe',false]]){
    if((captureOnly||keyboardOnly||dialogOnly)&&name!=='Edge')continue;
    const full=wantsFull&&!layoutOnly&&!captureOnly&&!keyboardOnly;
    assert(fs.existsSync(executable),name+' 浏览器缺失');
    context=await chromium.launchPersistentContext(path.join(temporary,name),{headless:true,executablePath:executable,
      viewport:{width:1440,height:900},env,args:['--no-first-run','--no-default-browser-check','--disable-component-update','--disable-background-networking',`--disk-cache-dir=${path.join(temporary,name+'-cache')}`,`--crash-dumps-dir=${path.join(temporary,name+'-crashes')}`]});
    await context.addInitScript(()=>{window.cameraCalls=0;if(navigator.mediaDevices){const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=(...args)=>{window.cameraCalls++;return original(...args)}}});
    const page=context.pages()[0];const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const login=async(user,pass)=>{await page.goto(origin+'/login');await page.locator('#username').fill(user);await page.locator('#password').fill(pass);await page.locator('[data-action="auth.login"]').click();await page.waitForURL(url=>!url.pathname.endsWith('/login'))};
    const logout=async()=>{await page.locator('#account-menu summary').click();await page.locator('[data-action="auth.logout"]').click();await page.waitForURL('**/login')};
    if(dialogOnly){
      const token=(await(await context.request.get(origin+'/api/v1/auth/csrf')).json()).data.csrf_token;
      assert.equal((await context.request.post(origin+'/api/v1/auth/login',{data:{username:'UI_Admin',password},headers:{'X-CSRFToken':token}})).status(),200);
      await page.goto(origin+'/actions'); const first=page.locator('.action-card:visible').first();await first.focus();await page.keyboard.press('Space');
      await page.evaluate(()=>{window.dialogResult=null;import('/static/app.js').then(m=>m.confirmDialog({title:'键盘确认测试',message:'不执行业务变更',confirmLabel:'继续检查'}).then(v=>window.dialogResult=v))});
      await page.locator('#confirm-dialog[open]').waitFor();
      for(const key of ['Tab','Tab','Shift+Tab','Tab','Tab']){await page.keyboard.press(key);assert(await page.evaluate(()=>document.querySelector('#confirm-dialog').contains(document.activeElement)))}
      await page.keyboard.press('Escape');await page.waitForFunction(()=>window.dialogResult===false);assert(await first.evaluate(e=>e===document.activeElement));
      await page.waitForTimeout(220);await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(evidence,'actions.png'),fullPage:true});
      assert.deepEqual(errors,[]);results.push({browser:name,passed:true,scope:'弹框焦点修复增量与归位截图'});await context.close();context=null;continue;
    }
    if(keyboardOnly){
      await page.goto(origin+'/register');await page.locator('#username').fill('UI_Admin');await page.locator('#password').fill(password);await page.locator('#password_confirmation').fill(password);await page.locator('#password_confirmation').press('Enter');await page.locator('#email-error').waitFor({state:'visible'});assert.equal(await page.evaluate(()=>document.activeElement.id),'username');
      await page.goto(origin+'/login');await page.locator('#username').fill('UI_Admin');await page.locator('#password').fill(password);await page.keyboard.press('Tab');assert.equal(await page.locator('[data-toggle-password="password"]').evaluate(e=>e===document.activeElement),true);await page.keyboard.press('Tab');assert.equal(await page.locator('[data-action="auth.login"]').evaluate(e=>e===document.activeElement),true);await page.keyboard.press('Space');await page.waitForURL(origin+'/');
      await page.goto(origin+'/account/password');await page.locator('#current_password').fill('incorrect-password-123');await page.locator('#new_password').fill(changed);await page.locator('#password_confirmation').fill(changed);await page.locator('#password_confirmation').press('Enter');await page.locator('#current_password-error').waitFor({state:'visible'});assert.equal(await page.evaluate(()=>document.activeElement.id),'current_password');await page.locator('#current_password').fill(password);await page.locator('#password_confirmation').focus();await page.keyboard.press('Tab');await page.keyboard.press('Tab');assert.equal(await page.locator('[data-action="password.cancel"]').evaluate(e=>e===document.activeElement),true);await page.keyboard.press('Tab');assert.equal(await page.locator('[data-action="password.save"]').evaluate(e=>e===document.activeElement),true);await page.keyboard.press('Enter');await page.waitForURL('**/login?notice=changed');await login('UI_Admin',changed);
      await page.goto(origin+'/actions');const first=page.locator('.action-card:visible').first();await first.focus();await page.keyboard.press('Space');assert.equal(await first.getAttribute('aria-pressed'),'true');await page.evaluate(()=>{window.dialogResult=null;import('/static/app.js').then(m=>m.confirmDialog({title:'键盘确认测试',message:'不执行业务变更',confirmLabel:'继续检查'}).then(v=>window.dialogResult=v))});await page.locator('#confirm-dialog[open]').waitFor();for(let i=0;i<4;i++){await page.keyboard.press('Tab');assert(await page.evaluate(()=>document.querySelector('#confirm-dialog').contains(document.activeElement)))}await page.keyboard.press('Escape');await page.waitForFunction(()=>window.dialogResult===false);assert(await first.evaluate(e=>e===document.activeElement));await page.waitForTimeout(220);await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(evidence,'actions.png'),fullPage:true});assert.deepEqual(errors,[]);results.push({browser:name,passed:true,scope:'T04 键盘、字段定位与弹框焦点增量'});await context.close();context=null;continue;
    }
    if(!layoutOnly&&!captureOnly){await page.goto(origin+'/');assert.equal(await page.locator('.home-section').count(),3);assert.equal(await page.evaluate(()=>window.cameraCalls),0);
    await page.locator('a.scroll-link[href="#realtime-entry"]').click();await page.waitForTimeout(400);assert((await page.locator('#realtime-entry h2').boundingBox()).y>=75);
    await page.goto(origin+'/login');assert(await page.locator('[data-action="auth.login"]').isDisabled());
    }
    if(full){await page.screenshot({path:path.join(evidence,'login.png'),fullPage:true});await page.setViewportSize({width:1280,height:720});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(evidence,'login-1280.png'),fullPage:true});await page.setViewportSize({width:1440,height:900});
      await page.goto(origin+'/register');const username='ui_user_edge@qq.com';await page.locator('#username').fill(username);await page.locator('#password').fill(password);await page.locator('#password_confirmation').fill('different');assert(await page.locator('[data-action="auth.register"]').isDisabled());
      await page.locator('[data-toggle-password="password"]').click();assert.equal(await page.locator('#password').getAttribute('type'),'text');assert.equal(await page.locator('#password_confirmation').getAttribute('type'),'password');await page.locator('[data-toggle-password="password"]').click();
      await page.locator('#password_confirmation').fill(password);let submits=0;page.on('request',r=>{if(r.url().endsWith('/api/v1/auth/register')&&r.method()==='POST')submits++});
      await page.evaluate(()=>{document.querySelector('#auth-form').requestSubmit();document.querySelector('#auth-form').requestSubmit()});await page.waitForURL('**/login?notice=registered*');assert.equal(submits,1);assert.equal(await page.locator('#username').inputValue(),username);
      await page.locator('#password').fill('incorrect-password-123');await page.locator('[data-action="auth.login"]').click();await page.locator('#form-message:not([hidden])').waitFor();assert.equal(await page.locator('#password').inputValue(),'');await login(username,password);
    }else if(layoutOnly){const token=(await(await context.request.get(origin+'/api/v1/auth/csrf')).json()).data.csrf_token;const response=await context.request.post(origin+'/api/v1/auth/login',{data:{username:'UI_Admin',password},headers:{'X-CSRFToken':token}});assert.equal(response.status(),200)}else if(!captureOnly)await login('UI_Admin',password);
    if(captureOnly){const token=(await(await context.request.get(origin+'/api/v1/auth/csrf')).json()).data.csrf_token;assert.equal((await context.request.post(origin+'/api/v1/auth/login',{data:{username:'UI_Admin',password},headers:{'X-CSRFToken':token}})).status(),200);await page.goto(origin+'/actions');await page.waitForFunction(()=>document.querySelector('#page-label').textContent.length>0);await page.locator('.action-card:visible').first().click();await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.waitForFunction(()=>scrollY===0);await page.screenshot({path:path.join(evidence,'actions.png'),fullPage:true});results.push({browser:name,passed:true,scope:'截图归位复核'});await context.close();context=null;continue}
    await page.goto(origin+'/actions');await page.waitForFunction(()=>document.querySelector('#page-label').textContent.length>0);assert.equal(await page.locator('.action-card').count(),12);assert.equal(await page.locator('.action-card:visible').count(),6);
    await page.locator('.action-card:visible').first().click();const selected=await page.locator('#selected-action').innerText();await page.locator('[data-filter="杠铃"]').click();assert.equal(await page.locator('.action-card:visible').count(),3);assert.equal(await page.locator('#selected-action').innerText(),selected);assert(await page.locator('#action-next').isEnabled());
    await page.locator('#action-search').fill('肩推');assert.equal(await page.locator('.action-card:visible').count(),1);await page.locator('#action-search').fill('没有这个动作');assert(await page.locator('#no-actions').isVisible());await page.locator('#reset-filters').click();assert.equal(await page.locator('.action-card:visible').count(),6);
    await page.locator('#next-page').click();assert((await page.locator('#page-label').innerText()).includes('2 / 2'));assert.equal(await page.locator('#selected-action').innerText(),selected);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.equal(await page.evaluate(()=>window.cameraCalls),0);
    const footer=await page.locator('.action-footer').boundingBox();const lastCard=await page.locator('.action-card:visible').last().boundingBox();assert(footer.y>=lastCard.y+lastCard.height,'操作栏不得遮住动作卡片');
    if(full||(layoutOnly&&name==='Edge')){await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.waitForFunction(()=>scrollY===0);await page.screenshot({path:path.join(evidence,'actions.png'),fullPage:true});}
    if(full){await page.locator('#action-next').click();await page.waitForURL('**/prepare?action=*');assert(await page.getByRole('button',{name:'开始分析',exact:true}).isDisabled());
      await page.goto(origin+'/account/password');assert(await page.locator('[data-action="password.save"]').isDisabled());await page.locator('[data-action="password.cancel"]').click();await page.waitForURL(origin+'/');
      await page.goto(origin+'/account/password');await page.locator('#current_password').fill(password);await page.locator('#new_password').fill(changed);await page.locator('#password_confirmation').fill(changed);await page.locator('[data-action="password.save"]').click();await page.waitForURL('**/login?notice=changed');await login('UI_User_Edge',changed);
      await page.goto(origin+'/actions');await page.evaluate(()=>{window.dialogResult=null;import('/static/app.js').then(m=>m.confirmDialog({title:'测试确认',message:'仅验证关闭和焦点，不执行业务变更',confirmLabel:'继续检查'}).then(value=>window.dialogResult=value))});await page.locator('#confirm-dialog[open]').waitFor();await page.keyboard.press('Escape');await page.waitForFunction(()=>window.dialogResult===false);assert(!(await page.locator('#confirm-dialog').isVisible()));
      const other=await context.newPage();await other.goto(origin+'/actions');await logout();await login('UI_Admin',password);await other.waitForFunction(()=>document.body.dataset.userId.length>0);await page.waitForTimeout(300);assert.equal(await other.locator('body').getAttribute('data-user-id'),await page.locator('body').getAttribute('data-user-id'));await other.close();
      await logout();await login('UI_Temporary',password);assert(page.url().includes('/account/password'));assert.equal(await page.locator('[data-action="password.cancel"]').count(),0);await page.goto(origin+'/records');assert(page.url().includes('/account/password'));await page.screenshot({path:path.join(evidence,'forced-password.png'),fullPage:true});
      await page.locator('#current_password').fill(password);await page.locator('#new_password').fill(changed);await page.locator('#password_confirmation').fill(changed);await page.locator('[data-action="password.save"]').click();await page.waitForURL('**/login?notice=changed');
      let loginRequests=0;await page.route('**/api/v1/auth/login',async route=>{loginRequests++;await route.fetch();await route.abort()});await login('UI_Admin',password);assert.equal(loginRequests,1);await page.unroute('**/api/v1/auth/login');
    }
    assert.deepEqual(errors,[]);results.push({browser:name,passed:true,scope:layoutOnly?'动作目录布局增量':full?'T04-01..04':'认证/目录/布局兼容连接'});await context.close();context=null;
  }
  const counts=call(['-c',"import os,sqlite3;from contextlib import closing\nwith closing(sqlite3.connect(os.environ['FITNESS_DATABASE_PATH'])) as c: print(c.execute('SELECT count(*) FROM analysis_sessions').fetchone()[0])"]);assert.equal(counts.trim(),'0');
  console.log(JSON.stringify({results,analysis_records:0,evidence:path.relative(root,evidence)}));
}finally{
  if(context)await context.close();if(started)call(['-m','fitness_app','--stop']);
  const resolved=fs.realpathSync(temporary);assert.equal(resolved,temporary);assert(!path.relative(root,resolved).startsWith('..'));
  fs.rmSync(temporary,{recursive:true,force:true,maxRetries:10,retryDelay:200});console.log('UI test profiles and temporary database removed.');
}
