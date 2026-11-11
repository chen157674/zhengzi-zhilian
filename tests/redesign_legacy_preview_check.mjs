// 固定动作兼容入口只验证新骨架绘制与释放，不开启硬件。
import assert from 'node:assert/strict';import {BrowserFixture} from './formal_browser_support.mjs';
const fx=new BrowserFixture('ui-step21-legacy');
try{fx.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Legacy_UI','ui-legacy-test-password')
app.extensions['database'].dispose()
`);fx.start();const context=await fx.browser();await fx.apiLogin(context,'Legacy_UI','ui-legacy-test-password');const page=await context.newPage();page.setDefaultTimeout(15000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{navigator.mediaDevices.getUserMedia=async()=>{const canvas=document.createElement('canvas');canvas.width=640;canvas.height=360;const ctx=canvas.getContext('2d');ctx.fillStyle='#ddd';ctx.fillRect(0,0,640,360);const stream=canvas.captureStream(30);window.testTracks=stream.getTracks();return stream;};navigator.mediaDevices.enumerateDevices=async()=>[];});
 let frame=null;
 await page.route('**/api/v1/previews',r=>r.fulfill({json:{data:{preview_id:'00000000-0000-4000-8000-000000000001',preview_token:'fixture',stream_epoch:'00000000-0000-4000-8000-000000000002'}}}));
 await page.route('**/api/v1/previews/*/frames',async r=>{const text=r.request().postDataBuffer().toString();frame=JSON.parse(text.match(/\{"stream_epoch"[^\r\n]+\}/)[0]);await r.fulfill({status:202,json:{data:{accepted:true}}});});
 await page.route('**/api/v1/previews/00000000-0000-4000-8000-000000000001',async r=>{if(r.request().method()==='DELETE'){await r.fulfill({json:{data:{released:true}}});return;}await r.fulfill({json:{data:{status:'active',result:frame?{...frame,processing_ms:10,framing:{usable:true,ready_for_tracking:true,landmarks:[],message:'合成输入'},skeleton:{available:true,joints:[{index:11,x:.25,y:.25},{index:13,x:.5,y:.5}],connections:[[11,13]],highlight:{joint_indices:[13]}}}:null}}});});
 await page.goto(fx.origin+'/prepare?action=bodyweight_squat');await page.locator('#camera-toggle').click();await page.waitForFunction(()=>Number(document.querySelector('#camera-canvas').dataset.frameSeq)>0);await page.waitForTimeout(150);
 const colors=()=>{const c=document.querySelector('#camera-canvas'),d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let teal=0;for(let i=0;i<d.length;i+=4)if(d[i+1]>d[i]+40&&d[i+2]>d[i]+25)teal++;return teal;};
 assert(await page.evaluate(colors)>0,'必须绘制服务端骨架');
 await page.locator('#legacy-skeleton').uncheck();await page.waitForTimeout(200);assert.equal(await page.evaluate(colors),0);
 await page.locator('#camera-toggle').click();assert(await page.evaluate(()=>window.testTracks.every(t=>t.readyState==='ended')));assert.deepEqual(errors,[]);
 console.log('PASS: fixed-action preview draws backend skeleton, hides overlay, releases synthetic stream.');
}finally{await fx.close();}
