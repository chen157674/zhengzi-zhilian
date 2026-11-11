// 18-A：只查新封面、完整显示与缺图降级，不重复04筛选／账号矩阵。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step18-covers');const errors=[];
try{
  fixture.seed(`
import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Asset_Admin','asset-fixture-password-123');db.dispose()
`);
  fixture.start();const context=await fixture.browser('Edge',{width:1440,height:900});await fixture.apiLogin(context,'Asset_Admin','asset-fixture-password-123');
  await context.addInitScript(()=>{window.assetCameraCalls=0;Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{window.assetCameraCalls++;throw new Error('封面页面不应请求摄像头')}})});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(fixture.origin+'/actions');
  const out=path.join(root,'data/verification/step-18');fs.mkdirSync(out,{recursive:true});const seen=[];
  const capture=async name=>{await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo({top:0,behavior:'instant'})});await page.screenshot({path:path.join(out,name),fullPage:true})};
  if(process.argv.includes('--capture-only')){
    await page.locator('#next-page').click();
    await page.waitForFunction(()=>[...document.querySelectorAll('.action-card:not([hidden]) img')].every(i=>i.complete&&i.naturalWidth>0));
    await capture('covers-page-2.png');
    await page.route('**/static/covers/standard_push_up.png*',route=>route.abort());await page.reload();
    await page.locator('[data-action-id="standard_push_up"] .cover-placeholder').waitFor({state:'visible'});await capture('covers-missing.png');
    console.log('Recaptured two screenshots from scroll top; passed behavior checks reused.');
  }else{
  for(let number=1;number<=2;number++){
    await page.waitForFunction(()=>[...document.querySelectorAll('.action-card:not([hidden]) .cover-media img')].length===6&&[...document.querySelectorAll('.action-card:not([hidden]) .cover-media img')].every(i=>i.complete&&i.naturalWidth>0));
    const cards=await page.locator('.action-card:visible').evaluateAll(items=>items.map(card=>{const img=card.querySelector('img'),box=img.getBoundingClientRect();return {id:card.dataset.actionId,alt:img.alt,width:img.naturalWidth,height:img.naturalHeight,fit:getComputedStyle(img).objectFit,displayWidth:box.width,displayHeight:box.height}}));
    assert.equal(cards.length,6);assert(cards.every(c=>c.fit==='contain'&&c.width===1536&&c.height===1024&&c.alt.includes('AI生成')));seen.push(...cards);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.equal(await page.evaluate(()=>assetCameraCalls),0);
    await capture(`covers-page-${number}.png`);
    if(number===1)await page.locator('#next-page').click();
  }
  assert.equal(new Set(seen.map(c=>c.id)).size,12);
  await page.route('**/static/covers/standard_push_up.png*',route=>route.abort());await page.reload();
  const missing=page.locator('[data-action-id="standard_push_up"]');await missing.locator('.cover-placeholder').waitFor({state:'visible'});
  assert(await missing.locator('img').isHidden());assert((await missing.innerText()).includes('封面暂缺'));
  await capture('covers-missing.png');
  const count=Number(fixture.seed(`
import os
from sqlalchemy import select,func
from fitness_app.database import Database
from fitness_app import db_schema as s
db=Database(os.environ['FITNESS_DATABASE_PATH'])
with db.transaction(False) as c:print(c.scalar(select(func.count()).select_from(s.analysis_sessions)))
db.dispose()
`).trim());
  assert.equal(count,0);assert.deepEqual(errors,[]);
  const result={checks:'passed',scope:'12 navigation covers and missing-image fallback',cards:seen,cameraCalls:0,analysisSessionsCreated:count};
  fs.writeFileSync(path.join(out,'covers-ui.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({checks:'passed',covers:seen.length,cameraCalls:0,analysisSessionsCreated:count}));
  }
}finally{await fixture.close()}
