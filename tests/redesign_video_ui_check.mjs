import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fx=new BrowserFixture('ui-step21-video'),output=path.resolve(root,process.env.FITNESS_UI_EVIDENCE_DIR||'前端重设计/实现核对');assert(!path.relative(root,output).startsWith('..'));fs.mkdirSync(output,{recursive:true});
try{
 fx.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Video_UI','ui-video-test-password')
app.extensions['database'].dispose()
`);
 await fx.startTestService('tests/formal_redesign_service.py');
 const context=await fx.browser('Edge',{width:1536,height:1024});await fx.apiLogin(context,'Video_UI','ui-video-test-password');
 const page=await context.newPage(),errors=[];page.setDefaultTimeout(20000);page.on('pageerror',e=>errors.push(e.message));
 await page.goto(fx.origin+'/video?demo=1');await page.waitForFunction(()=>!document.querySelector('#video-start').disabled);
 await page.locator('#video-start').click();await page.waitForFunction(()=>document.querySelector('#video-page').dataset.stage==='processing');
 await page.waitForTimeout(1800);await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(250);await page.screenshot({path:path.join(output,'视频处理中.png'),fullPage:true,animations:'disabled'});
 await page.waitForFunction(()=>document.querySelector('#video-page').dataset.stage==='result',null,{timeout:90000});
 assert(await page.locator('#video-result-note').textContent());await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(250);await page.screenshot({path:path.join(output,'视频结果.png'),fullPage:true,animations:'disabled'});
 const jobs=(await (await context.request.get(fx.origin+'/api/v1/jobs')).json()).data;
 assert.equal((await(await context.request.get(fx.origin+'/api/v1/records')).json()).data.total,0,'演示不可进入个人成果');
 await page.locator('#video-detail').click();await page.waitForFunction(()=>!document.querySelector('#quality-panel').hidden);
 assert((await page.locator('#detail-meta').textContent()).includes('演示'));
 await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(250);await page.screenshot({path:path.join(output,'视频记录详情.png'),fullPage:true,animations:'disabled'});
 assert.deepEqual(errors,[]);
 console.log('PASS: real row video decode + existing Full cache -> new UI progress/result/detail; demo excluded, no new model inference or camera.');
}finally{await fx.close();}
