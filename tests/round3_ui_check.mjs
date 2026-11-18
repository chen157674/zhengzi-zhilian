// 专属E盘测试库/浏览器配置；不请求真实相机，不配置/调用付费API。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fixture=new BrowserFixture('round3-mobile-ai');
const out=path.join(root,'第四轮优化/实现核对');
const password='Round3-ui-fixture-2026';
const errors=[];
async function settled(page){await page.evaluate(async()=>{await Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})));});}
try{
  fixture.seed(`from fitness_app.paths import configure_environment
configure_environment()
import os
from fitness_app.database import Database
from fitness_app.auth import initialize_admin
db=Database(os.environ['FITNESS_DATABASE_PATH']);db.initialize();initialize_admin(db,'round3_admin','${password}');db.dispose()`);
  fixture.start();const browser=await fixture.browser('Edge',{width:1440,height:980});
  await fixture.apiLogin(browser,'round3_admin',password);
  await browser.addInitScript(()=>{window.cameraRequests=0;if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{window.cameraRequests++;throw new DOMException('fixture denied','NotAllowedError');};});
  const page=await browser.newPage();page.on('pageerror',e=>errors.push(String(e)));let messagePosts=0;page.on('request',request=>{if(request.method()==='POST'&&/\/ai\/conversations\/[^/]+\/messages$/.test(request.url()))messagePosts++;});
  if(process.env.FITNESS_UI_SCOPE!=='training'){
  await page.goto(fixture.origin+'/realtime');await page.locator('[data-ai-open]').waitFor();
  assert.equal(await page.evaluate(()=>window.cameraRequests),0);
  await page.locator('[data-ai-open]').click();await page.locator('#ai-chat-welcome').waitFor();assert.equal(await page.locator('#ai-assistant-scope').count(),0);
  await page.locator('#ai-chat-input').fill('肩推怎么更稳定？');
  await settled(page);await page.screenshot({path:path.join(out,'AI教练-侧栏桌面.png'),fullPage:true});
  await page.locator('#ai-assistant-close').click();assert.equal(await page.locator('#ai-assistant').isVisible(),false);
  await page.locator('[data-ai-open]').click();assert.equal(await page.locator('#ai-chat-input').inputValue(),'肩推怎么更稳定？');
  await page.locator('#ai-assistant-close').click();
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>scrollTo(0,0));
  assert.equal(await page.locator('.topbar nav > a, .topbar nav > details').count(),5);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  const nav=await page.locator('.topbar nav').boundingBox();assert(nav.y>700,'手机五项导航应在底部');
  await page.locator('#live-camera-flip').click();assert.equal(await page.evaluate(()=>window.cameraRequests),0);
  await page.evaluate(()=>scrollTo(0,0));
  await settled(page);await page.screenshot({path:path.join(out,'mobile-realtime.png')});
  await page.locator('[data-ai-open]').click();
  assert.equal(await page.locator('#main').evaluate(node=>node.inert),true);
  assert.equal(await page.locator('#ai-assistant').getAttribute('aria-modal'),'true');
  await settled(page);assert.equal(await page.locator('#ai-assistant').evaluate(node=>getComputedStyle(node).opacity),'1');
  const panelBox=await page.locator('#ai-assistant').boundingBox();assert.equal(panelBox.x,0);assert.equal(panelBox.width,390);
  await page.screenshot({path:path.join(out,'mobile-ai.png')});
  assert.equal(await page.locator('#ai-send-consent').isChecked(),true);await page.locator('#ai-send-consent').uncheck();
  await page.locator('#ai-chat-send').click();await page.locator('#ai-chat-error').filter({hasText:'请勾选训练摘要'}).waitFor();assert.equal(messagePosts,0);
  await page.locator('#ai-send-consent').check();await page.locator('#ai-chat-send').click();
  await page.locator('#ai-chat-error').filter({hasText:'系统AI服务尚未配置'}).waitFor();
  assert.equal(await page.locator('#ai-chat-input').inputValue(),'肩推怎么更稳定？');
  await page.locator('#ai-assistant-close').click();assert.equal(await page.locator('#main').evaluate(node=>node.inert),false);
  await page.locator('#live-toggle').click();await page.locator('#live-error').filter({hasText:'权限未允许'}).waitFor();
  assert.equal(await page.evaluate(()=>window.cameraRequests),1);
  await page.goto(fixture.origin+'/account/ai');await page.locator('#ai-settings-fields:not([disabled])').waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await settled(page);await page.screenshot({path:path.join(out,'AI服务设置-操作窄屏.png'),fullPage:true});
  assert.equal(await page.locator('#ai-policy-fields').isEnabled(),true);
  await page.locator('#ai-history').uncheck();await page.locator('#ai-settings-save').click();
  await page.locator('#global-message').filter({hasText:'AI设置已保存'}).waitFor();await page.reload();
  await page.locator('#ai-settings-fields:not([disabled])').waitFor();assert.equal(await page.locator('#ai-history').isChecked(),false);
  await page.setViewportSize({width:320,height:740});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.setViewportSize({width:844,height:390});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  }
  await page.setViewportSize({width:390,height:844});
  await page.goto(fixture.origin+'/account/training-profile');await page.locator('#training-profile-fields:not([disabled])').waitFor();
  await page.locator('#training-more summary').click();
  await page.locator('#training-height').fill('178.5');await page.locator('#training-weight').fill('72.4');await page.locator('#training-age').fill('25');
  await page.locator('[name=experience-choice][value=beginner]').check();await page.locator('#training-minutes').fill('45');
  await page.locator('[name=equipment][value=dumbbell]').check();await page.locator('[name=goals][value=movement_quality]').check();
  await page.locator('#training-profile-form button:not([type=button])').click();await page.locator('#global-message').filter({hasText:'训练档案已保存'}).waitFor();
  await page.reload();await page.locator('#training-profile-fields:not([disabled])').waitFor();assert.equal(await page.locator('#training-minutes').inputValue(),'45');
  assert.equal(await page.locator('#training-height').inputValue(),'178.5');assert.equal(await page.locator('#training-weight').inputValue(),'72.4');
  await page.locator('#training-more summary').click();
  assert.equal(await page.locator('[name=equipment][value=dumbbell]').isChecked(),true);await settled(page);
  await page.screenshot({path:path.join(out,'训练档案-操作窄屏.png'),fullPage:true});
  await page.locator('[name=experience-choice][value=experienced]').check();await page.locator('#training-minutes').fill('20');
  await page.locator('#training-profile-cancel').click();assert.equal(await page.locator('#training-minutes').inputValue(),'45');assert.equal(await page.locator('[name=experience-choice][value=beginner]').isChecked(),true);
  await page.locator('#training-experience-clear').click();assert.equal(await page.locator('[name=experience-choice]:checked').count(),0);await page.locator('#training-profile-cancel').click();
  await page.locator('#training-profile-clear').click();await page.locator('[data-dialog-cancel]').click();assert.equal(await page.locator('#training-minutes').inputValue(),'45');
  await page.locator('#training-profile-clear').click();await page.locator('[data-dialog-confirm]').click();await page.locator('#global-message').filter({hasText:'训练档案已清除'}).waitFor();
  assert.equal(await page.locator('#training-minutes').inputValue(),'');assert.equal(await page.locator('[name=experience-choice]:checked').count(),0);assert.equal(await page.locator('[name=equipment]:checked').count(),0);
  const record=JSON.parse(fixture.seed(`from fitness_app.paths import configure_environment
configure_environment()
import os,json
from sqlalchemy import select
from fitness_app.database import Database
from fitness_app import db_schema as s
from fitness_app.records import SessionStore
from fitness_app.catalogue import get_action
db=Database(os.environ['FITNESS_DATABASE_PATH']);store=SessionStore(db)
with db.transaction(False) as c:
 uid=c.scalar(select(s.users.c.id).where(s.users.c.username_key=='round3_admin'))
 auth=c.scalar(select(s.auth_sessions.c.id).where(s.auth_sessions.c.user_id==uid,s.auth_sessions.c.revoked_at_ms.is_(None)))
data=store.create(uid,auth,{'action_id':'bodyweight_squat','capability_version':get_action('bodyweight_squat')['capability_version'],'source':'camera','mode':'free','target':None},s.new_id());sid=data['session_id']
store.set_state(uid,sid,'running',s.new_id());store.set_state(uid,sid,'ended_early',s.new_id());print(json.dumps({'id':sid}));db.dispose()`));
  await page.goto(fixture.origin+'/records/'+record.id);await page.locator('#record-tab-note').click();
  await page.locator('#training-note-fields:not([disabled])').waitFor();await page.locator('#note-unit').selectOption('plate_index');await page.locator('#note-load').fill('5');
  assert.equal(await page.locator('#note-basis').inputValue(),'device_setting');await page.locator('#note-difficulty').selectOption('hard');
  await page.locator('#note-text').fill('隔离UI夹具：配重档位不是公斤');await page.locator('#training-note-form button:not([type=button])').click();
  await page.locator('#global-message').filter({hasText:'训练备注已保存'}).waitFor();await page.reload();
  await page.locator('#record-tab-note').click();await page.locator('#training-note-fields:not([disabled])').waitFor();
  assert.equal(await page.locator('#note-load').inputValue(),'5');assert.equal(await page.locator('#note-unit').inputValue(),'plate_index');
  assert.equal(await page.locator('#note-difficulty').inputValue(),'hard');
  assert.deepEqual(errors,[]);console.log(process.env.FITNESS_UI_SCOPE==='training'?'Training profile and user-entered load notes: save/reload passed.':'Desktop/mobile AI, camera permission, settings and training-information flows: passed.');
}finally{await fixture.close();}
