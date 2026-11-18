// 账号、管理和操作弹窗的实际渲染与状态检查；仅使用项目内隔离数据。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fx=new BrowserFixture('remaining-ui-polish'),out=path.join(root,'第五轮优化/实现核对');
const errors=[],evidence=[],metrics=[];let page;
try{
 const ids=JSON.parse(fx.seed(`import os,json
from sqlalchemy import select
from fitness_app import create_app,db_schema as s
from fitness_app.auth import initialize_admin,password_hash
from scripts.seed_review_account import seed,stable_id
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']});db=app.extensions['database']
initialize_admin(db,'Polish_Admin','polish-admin-password')
seed(os.environ['FITNESS_DATABASE_PATH'],True,'polish-review-password')
with db.transaction() as c:
 uid=s.new_id();c.execute(s.users.insert().values(id=uid,username_display='Dialog_Target',username_key='dialog_target',password_hash=password_hash('dialog-target-password')))
 c.execute(s.user_profiles.insert().values(user_id=uid,nickname='操作核对',avatar_id='avatar-02'))
 sid=c.scalar(select(s.analysis_sessions.c.id).where(s.analysis_sessions.c.user_id==stable_id('user')).order_by(s.analysis_sessions.c.started_at_ms.desc()))
 pid=c.scalar(select(s.training_plans.c.id).where(s.training_plans.c.user_id==stable_id('user'),s.training_plans.c.status=='active'))
print(json.dumps({'record':sid,'plan':pid,'target':uid}))
for hook in app.extensions.get('stop_hooks',[]):hook()
db.dispose()`));
 fx.start();const context=await fx.browser('Edge',{width:1440,height:1000});
 await fx.apiLogin(context,'review_judge','polish-review-password');
 const other=await fx.browser('Chrome',{width:390,height:844});
 await fx.apiLogin(other,'review_judge','polish-review-password');
 page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const providerCalls=[];page.on('request',r=>{if(r.method()==='POST'&&/\/(ai\/conversations|coach\/tasks|ai-settings\/test)$/.test(new URL(r.url()).pathname))providerCalls.push(r.url());});
 async function capture(label,dialog=null){
  const row=await page.evaluate(selector=>{
   const host=selector?document.querySelector(selector):document.querySelector('main'),rect=host.getBoundingClientRect();
   const bad=[...host.querySelectorAll('input:not([type=hidden]),select,textarea,button,table,a.security-row')].filter(el=>{
    const r=el.getBoundingClientRect();if(!r.width||!r.height||r.width<=1)return false;
    if(el.matches('a.security-row')){const group=el.closest('.settings-group').getBoundingClientRect();if(r.left<group.left-1||r.right>group.right+1)return true;}
    for(let p=el.parentElement;p&&p!==host;p=p.parentElement)if(/auto|scroll/.test(getComputedStyle(p).overflowX)&&p.scrollWidth>p.clientWidth+1)return false;
    return r.left<-1||r.right>innerWidth+1;
   }).map(el=>el.id||el.tagName);
   return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,host:{x:rect.x,y:rect.y,width:rect.width,height:rect.height,bottom:rect.bottom},bad};
  },dialog);
  assert(row.scrollWidth<=row.width,label+'页面横向溢出');assert.deepEqual(row.bad,[],label+'控件越出画面');
  if(dialog){assert(row.host.width<=row.width-20&&row.host.y>=0&&row.host.bottom<=row.height+1,label+'弹窗超出视口');assert(await page.locator(dialog).isVisible());}
  const file=path.join(fx.temporary,evidence.length+'.png');await page.screenshot({path:file,animations:'disabled'});
  evidence.push({label:label+' · '+row.width,path:file});metrics.push({label,...row});
  if(['AI设置','设置','登录管理','账号管理','通用确认','登录撤销错误','报告选择','报告结果','头像预览','AI复盘选择','AI复盘历史','AI复盘失败'].includes(label)&&[1440,390].includes(row.width))
   await page.screenshot({path:path.join(out,'账号弹窗精修-'+label+'-'+row.width+'.png'),animations:'disabled'});
 }
 const routes=[['个人中心','/account'],['健身档案','/account/training-profile'],['账号安全','/account/password'],['登录管理','/account/sessions'],['AI设置','/account/ai'],['设置','/settings']];
 for(const width of [1440,1024,768,390,320]){
  await page.setViewportSize({width,height:width<768?844:1000});
  for(const [label,url]of routes){await page.goto(fx.origin+url,{waitUntil:'networkidle'});await capture(label);}
 }
 // 头像预览、通用确认和日期输入均从真实入口打开，取消不写业务数据。
 for(const width of [1440,390,320]){
  await page.setViewportSize({width,height:width<768?844:1000});
  await page.goto(fx.origin+'/account',{waitUntil:'networkidle'});
  await page.locator('#avatar-select').focus();
  await page.locator('#avatar-file').setInputFiles(path.join(root,'fitness_app/static/avatars/avatar-06.png'));
  await page.locator('#avatar-dialog[open]').waitFor();await capture('头像预览','#avatar-dialog');
  await page.locator('#avatar-cancel').click();assert.equal(await page.locator('#avatar-dialog[open]').count(),0);
  await page.goto(fx.origin+'/account/training-profile',{waitUntil:'networkidle'});
  await page.locator('#training-profile-clear').click();await page.locator('#confirm-dialog[open]').waitFor();await capture('通用确认','#confirm-dialog');
  await page.keyboard.press('Escape');assert.equal(await page.locator('#confirm-dialog[open]').count(),0);
  assert.equal(await page.locator('#training-profile-clear').evaluate(el=>el===document.activeElement),true);
  await page.goto(fx.origin+'/training/plans/'+ids.plan+'/edit',{waitUntil:'networkidle'});
  await page.getByRole('button',{name:'复制到新日期',exact:true}).first().click();await page.locator('.input-dialog[open]').waitFor();await capture('日期输入弹窗','.input-dialog');
  await page.keyboard.press('Escape');assert.equal(await page.locator('.input-dialog').count(),0);
  await page.goto(fx.origin+'/account/sessions',{waitUntil:'networkidle'});
  await page.locator('#revoke-others:not([disabled])').waitFor();await page.locator('#revoke-others').click();
  await page.locator('#revoke-password').fill('incorrect-test-password');await page.locator('#revoke-submit').click();await page.locator('#revoke-error').filter({hasText:/密码/}).waitFor();
  await capture('登录撤销错误','#revoke-dialog');await page.locator('#revoke-cancel').click();
  assert.equal(await page.locator('#revoke-password').inputValue(),'');
 }
 // 加载、失败与重试必须保留真实状态，不能停留在“正在读取”。
 await page.setViewportSize({width:390,height:844});
 let release;const gate=new Promise(resolve=>release=resolve);
 await page.route('**/api/v1/account/sessions',async route=>{await gate;await route.fulfill({status:503,json:{error:{code:'TEMP_UNAVAILABLE',message:'登录记录暂时不可用，请重试。'}}});});
 await page.goto(fx.origin+'/account/sessions',{waitUntil:'domcontentloaded'});
 await page.locator('.session-list-panel[aria-busy=true]').waitFor();await capture('登录记录加载');
 release();await page.locator('#sessions-error:not([hidden])').waitFor();await capture('登录记录失败');
 assert((await page.locator('#sessions-list').textContent()).includes('暂时无法读取'));
 await page.unroute('**/api/v1/account/sessions');await page.locator('#sessions-retry').click();await page.locator('#sessions-list tr[data-current]').first().waitFor();assert(await page.locator('#sessions-error').isHidden());
 await page.route('**/api/v1/me/ai-settings',route=>route.fulfill({status:503,json:{error:{code:'TEMP_UNAVAILABLE',message:'AI设置暂时不可用，请重试。'}}}));
 await page.goto(fx.origin+'/account/ai',{waitUntil:'networkidle'});await page.locator('#ai-settings-error:not([hidden])').waitFor();await capture('AI设置失败');
 await page.unroute('**/api/v1/me/ai-settings');await page.locator('#ai-settings-retry').click();await page.locator('#ai-settings-fields:not([disabled])').waitFor();
 await page.locator('#ai-source').selectOption('personal');await capture('个人密钥表单');await page.locator('#ai-source').selectOption('system');
 // 实际训练报告窗口；PDF由本地服务生成，不使用云端AI。
 for(const width of [1440,390,320]){
  await page.setViewportSize({width,height:width<768?844:1000});
  await page.goto(fx.origin+'/records/'+ids.record,{waitUntil:'networkidle'});
  await page.locator('#record-export:not([disabled])').click();await page.locator('#report-export-options:not([hidden])').waitFor();await capture('报告选择','#report-export-dialog');
  if(width===1440){await page.locator('#report-export-generate').click();await page.locator('#report-export-download:not([hidden])').waitFor({timeout:30000});await capture('报告结果','#report-export-dialog');}
  await page.locator('#report-export-back').click();assert.equal(await page.locator('#report-export-dialog[open]').count(),0);
  if(width===1440){
   let releaseOptions;const optionsGate=new Promise(resolve=>releaseOptions=resolve);
   await page.route('**/api/v1/ai/report-options',async route=>{await optionsGate;await route.fulfill({status:503,json:{error:{code:'TEMP_UNAVAILABLE',message:'训练范围暂时无法读取，请稍后重试。'}}});});
   await page.locator('#record-ai-review:not([disabled])').click();await page.locator('#ai-report-state').filter({hasText:'正在读取训练范围'}).waitFor();await capture('AI复盘加载','#ai-report-dialog');
   releaseOptions();await page.locator('#ai-report-error:not([hidden])').waitFor();await capture('AI复盘失败','#ai-report-dialog');
   await page.locator('#ai-report-back').click();await page.unroute('**/api/v1/ai/report-options');
  }
  await page.locator('#record-ai-review:not([disabled])').click();await page.locator('#ai-report-dialog[open]').waitFor();await page.locator('#ai-report-options:not([hidden])').waitFor();assert(await page.locator('#ai-report-error').isHidden());await capture('AI复盘选择','#ai-report-dialog');
  await page.locator('#ai-report-dialog .report-export-history>summary').click();await page.locator('#ai-report-history .ai-report-history-empty').waitFor();await capture('AI复盘历史','#ai-report-dialog');
  await page.locator('#ai-report-back').click();
 }
 // 管理员的表格、额度展开、搜索空状态和密码结果只操作隔离目标账号。
 await fx.apiLogin(context,'Polish_Admin','polish-admin-password');
 for(const width of [1440,768,390,320]){
  await page.setViewportSize({width,height:width<768?844:1000});
  await page.goto(fx.origin+'/admin/users',{waitUntil:'networkidle'});await capture('账号管理');
  await page.locator('#admin-search').fill('never-existing-account');await page.locator('.table-empty').filter({hasText:'没有匹配账号'}).waitFor();await capture('账号搜索空状态');
  await page.goto(fx.origin+'/account/ai',{waitUntil:'networkidle'});await page.locator('.admin-ai-policy>summary').click();await capture('系统额度设置');
 }
 await page.setViewportSize({width:390,height:844});await page.goto(fx.origin+'/admin/users',{waitUntil:'networkidle'});
 const target=page.locator('tr[data-user-id="'+ids.target+'"]');
 await target.getByRole('button',{name:'重置密码',exact:true}).click();await page.locator('#confirm-dialog[open]').waitFor();await capture('重置确认','#confirm-dialog');
 await page.locator('[data-dialog-confirm]').click();await page.locator('#reset-result[open]').waitFor();await capture('密码重置结果','#reset-result');
 await page.locator('[data-action="admin.close-reset-result"]').first().click();
 for(const [label,url]of [['权限与404页面','/missing-page'],['账号密码页','/account/password']]){
  await page.goto(fx.origin+url,{waitUntil:'networkidle'});await capture(label);
 }
 assert.deepEqual(providerCalls,[]);assert.deepEqual(errors,[]);
 fs.writeFileSync(path.join(out,'剩余页面精修核对.json'),JSON.stringify({checked_at:new Date().toISOString(),states:metrics,errors,provider_calls:0,isolated_database:true},null,2)+'\n');
 fx.python(['-c',`import sys,json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
d=json.load(sys.stdin);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',17)
for batch in range((len(d['evidence'])+11)//12):
 canvas=Image.new('RGB',(1600,1000),'#eee8df');draw=ImageDraw.Draw(canvas)
 for i,item in enumerate(d['evidence'][batch*12:(batch+1)*12]):
  x=i%4*400;y=i//4*333;draw.text((x+10,y+4),item['label'],font=font,fill='#3e2d24')
  im=Image.open(item['path']).convert('RGB');im.thumbnail((380,292));canvas.paste(im,(x+10,y+32))
 canvas.save(Path(d['out'])/('剩余页面精修-联系表-'+str(batch+1)+'.jpg'),quality=77)
`],JSON.stringify({out,evidence}));
 console.log(JSON.stringify({polished_states:metrics.length,errors:errors.length,provider_calls:0}));
} finally{await fx.close();}
