// 19：5分钟720p回放载荷。完全替换getUserMedia，不接触真实摄像头；不用于准确率。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import os from 'node:os';
import {execFileSync} from 'node:child_process';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const plank=process.argv.includes('--plank'),endOnly=process.argv.includes('--end-only'),controlOnly=process.argv.includes('--control-only')||endOnly;
const jack=process.argv.includes('--jack'),deadlift=process.argv.includes('--deadlift'),lunge=process.argv.includes('--lunge'),squat=process.argv.includes('--squat');assert([plank,jack,deadlift,lunge,squat].filter(Boolean).length<=1,'一次只测一种动作');
const pushup=process.argv.includes('--push-up'),curl=process.argv.includes('--curl'),basic=pushup||curl;
assert([plank,jack,deadlift,lunge,squat,pushup,curl].filter(Boolean).length<=1,'一次只测一种动作');
const currentRuntime=process.argv.includes('--current-runtime');assert(!currentRuntime||plank||![jack,deadlift,lunge,squat].some(Boolean),'当前版本补验目录只用于平板／划船');
const postureBackend=process.argv.includes('--posture-backend');assert(!postureBackend||((plank&&currentRuntime)||basic),'新增姿态后端负载用于当前平板或基础动作窗口');
assert(!basic||postureBackend,'基础动作补验必须包含当前姿态保存及指导状态');
const captureAmbiguity=process.argv.includes('--capture-jack-ambiguity');
const roi=process.argv.includes('--jack-content-roi');
const stateless=process.argv.includes('--jack-stateless');assert(!(roi&&(stateless||captureAmbiguity)),'不要混用候选模式');assert(!(stateless&&captureAmbiguity),'不要混用两个诊断模式');
const portrait=process.argv.includes('--deadlift-portrait');
const deadliftDiagnostic=process.argv.includes('--deadlift-diagnostic')||portrait;assert(!deadliftDiagnostic||deadlift,'硬拉诊断需要 --deadlift');
const jackDiagnostic=process.argv.includes('--jack-diagnostic')||captureAmbiguity||stateless||roi;assert(!jackDiagnostic||jack,'开合跳诊断需要 --jack');
const basicDiagnostic=process.argv.includes('--basic-diagnostic');assert(!basicDiagnostic||basic,'基础动作诊断需要--push-up或--curl');
const captureBasicAmbiguity=process.argv.includes('--capture-basic-ambiguity');assert(!captureBasicAmbiguity||(pushup&&basicDiagnostic),'取证只用于俯卧撑短诊断');
const loseResponseBody=process.argv.includes('--response-body-unavailable');assert(!loseResponseBody||basicDiagnostic,'响应体故障注入只用于基础动作短诊断');
const diagnose=jackDiagnostic||deadliftDiagnostic||basicDiagnostic;
const shortRun=controlOnly||diagnose;
const endLost=process.argv.includes('--end-response-lost');
assert(!endLost||endOnly,'丢响应场景需要 --end-only');
assert(!controlOnly||plank,'短控制诊断仅适用于平板支撑');
const action=pushup?'standard_push_up':curl?'standing_dumbbell_curl':plank?'forearm_plank':jack?'jumping_jack':deadlift?'conventional_deadlift':lunge?'reverse_lunge':squat?'bodyweight_squat':'barbell_bent_over_row',prefix=pushup?'push-up':curl?'dumbbell-curl':plank?'plank':jack?'jumping-jack':deadlift?'deadlift':lunge?'reverse-lunge':squat?'bodyweight-squat':'barbell-row',durationMs=diagnose?10000:controlOnly?5000:300000;
const captureWidth=portrait?464:1280,captureHeight=portrait?832:720,submitWidth=portrait?401:1280,submitHeight=720;
const output=path.join(root,'data/verification/step-19/'+(portrait?'diagnostic-portrait-':roi?'content-roi-':stateless?'stateless-':diagnose?'diagnostic-':endOnly?'end-control-':controlOnly?'control-':'performance-')+prefix+'.json');
const relevant=['static/camera.js','static/realtime.js','static/voice_feedback.js','static/voice_player.js','sessions.py','preview.py','pose.py','frame_pipeline.py','content_bounds.py','control.py','persistence.py','records.py','database.py','runtime.py','barbell_row.py','additional_counting_resources.py','counting_resources.py','cycles.py','upstream.py','catalogue.py','templates/prepare.html','static/app.css'];
if(plank)relevant.push('plank_hold.py');if(jack)relevant.push('jumping_jack.py');if(deadlift||squat||basic)relevant.push('action_counting.py');if(lunge)relevant.push('reverse_lunge.py');relevant.push('pose_input.py','preview_routes.py','record_routes.py','video_jobs.py','video_routes.py');
if(postureBackend)relevant.push('posture_engine.py','posture_rules.py','movement_quality.py','coaching.py','auth.py','db_schema.py','profile_assets.py');
const hashes=Object.fromEntries(relevant.map(name=>[name,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'fitness_app',name))).digest('hex')]));
const registered=JSON.parse(fs.readFileSync(path.join(root,'data/datasets/development-movements/sources.json'),'utf8')).sources;
const deadliftEntry=deadlift?JSON.parse(fs.readFileSync(path.join(root,'data/datasets/manifest.json'),'utf8')).files.find(f=>f.path==='mydeadlift/Gerakan Benar/GB_S02_R29.mp4'):null;
const basicEntry=basic?JSON.parse(fs.readFileSync(path.join(root,'data/verification/step-15/basic-range-development.json'),'utf8')).dependencies.inputs.find(row=>row.action===action):null;
const source=basic?{path:basicEntry.source,sha256:basicEntry.source_sha256,bytes:fs.statSync(path.join(root,basicEntry.source)).size}:deadlift?{path:'data/datasets/'+deadliftEntry.path,sha256:deadliftEntry.sha256,bytes:deadliftEntry.bytes}:(plank||jack||lunge||squat)?registered.find(s=>s.id===(plank?'dvids-640237-side':lunge?'dvids-551833':squat?'dvids-548443':'commons-jumping-jacks-segment')):null;
const asset=source?{video:{path:source.path,sha256:source.sha256,bytes:source.bytes}}:JSON.parse(fs.readFileSync(path.join(root,'data/presentation-assets.json'),'utf8')).demos['barbell-row'];
const configDocument=JSON.parse(fs.readFileSync(path.join(root,plank?'data/models/plank-hold.json':jack?'data/models/jumping-jack.json':(deadlift||squat||basic)?'data/models/cycle-counting.json':lunge?'data/models/reverse-lunge.json':'data/models/barbell-row.json'),'utf8'));
const config=(deadlift||squat||basic)?{...configDocument,config:configDocument.actions[action]}:configDocument;
const environment=JSON.parse(execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-B','-c',"import json,platform;from importlib.metadata import version;print(json.dumps({'python':platform.python_version(),'packages':{name:version(name) for name in ('mediapipe','numpy','av','Flask','waitress','SQLAlchemy','Pillow','Flask-Login','Flask-WTF')}}))"],{cwd:root,env:{...process.env,TEMP:path.join(root,'.tmp/runtime'),TMP:path.join(root,'.tmp/runtime'),PYTHONDONTWRITEBYTECODE:'1',PYTHONUTF8:'1'},windowsHide:true,encoding:'utf8'}));
environment.cpuModels=[...new Set(os.cpus().map(c=>c.model))];environment.logicalCpus=os.cpus().length;environment.totalMemoryBytes=os.totalmem();environment.osRelease=os.release();environment.node=process.version;
environment.threadEnvironment=Object.fromEntries(['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','TFLITE_NUM_THREADS'].map(name=>[name,process.env[name]??null]));
environment.browser=execFileSync('powershell.exe',['-NoProfile','-Command',"(Get-Item -LiteralPath 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe').VersionInfo.ProductVersion"],{cwd:root,windowsHide:true,encoding:'utf8'}).trim();
const sourceActual=crypto.createHash('sha256').update(fs.readFileSync(path.join(root,asset.video.path))).digest('hex');assert.equal(sourceActual,asset.video.sha256,'性能原片身份改变，先核查');
const model=JSON.parse(fs.readFileSync(path.join(root,'data/models/manifest.json'),'utf8')).sha256;
assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'data/models/pose_landmarker_full.task'))).digest('hex'),model,'模型身份改变，先核查');
const dependencies={hashes,source:asset.video,model,countingParameters:config.parameters_sha256,countingConfiguration:config.config,countingImplementation:config.implementation,durationMs,width:submitWidth,height:submitHeight,nominalFps:30,environment,...(lunge?{source_segment_seconds:[10,24.4]}:{}),...(squat?{source_segment_seconds:[19,31.7],source_view:'diagonal front; not sagittal quality acceptance'}:{}),...(basic?{replay_service_sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'tests/formal_performance_service.py'))).digest('hex')}:{}),...(captureBasicAmbiguity?{capture_basic_ambiguity:true}:{}),...(loseResponseBody?{response_body_unavailable:true}:{}),measurementProtocol:basic?(basicDiagnostic?19:20):postureBackend?18:squat?16:lunge?15:portrait?14:deadliftDiagnostic?13:deadlift?12:roi?11:stateless?9:captureAmbiguity?8:diagnose?7:jack?6:endLost?5:endOnly?4:controlOnly?3:plank?2:1};
const identity=crypto.createHash('sha256').update(JSON.stringify(dependencies)).digest('hex');
const previous=fs.existsSync(output)?JSON.parse(fs.readFileSync(output,'utf8')):{schema_version:1,action_id:action,runs:[]};
if(process.argv.includes('--describe')){
  console.log(JSON.stringify({identity,dependencies,measurement_source_sha256:crypto.createHash('sha256').update(fs.readFileSync(import.meta.filename)).digest('hex')}));
}else if(previous.runs.some(r=>r.identity===identity||r.performance_reuse?.some(a=>a.identity===identity))){
  const cached=previous.runs.findLast(r=>r.identity===identity||r.performance_reuse?.some(a=>a.identity===identity));
  console.log(JSON.stringify({cache_hit:true,scope:'recorded performance window or explicitly reviewed equivalent window; no repeat inference',result:cached.summary,original_control_check:cached.control_check,reuse:cached.performance_reuse?.find(a=>a.identity===identity)??null}));
}else{
  assert(!process.argv.includes('--reuse-only'),'没有对应的有效复用身份；未启动新推理');
  const fixture=new BrowserFixture(basic?(pushup?'ui-step19-push-up':'ui-step19-curl'):postureBackend?'ui-step19-posture-plank':currentRuntime?(plank?'ui-step19-current-plank':'ui-step19-current-row'):deadlift?'ui-step19-deadlift':lunge?'ui-step19-lunge':squat?'ui-step19-squat':'ui-step19-performance');const errors=[],commands=[],backendSamples=[];let report,bodyFaultInjected=false;const endTransport={requests:0,dropped:false};
  if(postureBackend)fixture.env.FITNESS_PERFORMANCE_POSTURE='1';
  if(captureBasicAmbiguity)fixture.env.FITNESS_CAPTURE_BASIC_AMBIGUITY='1';
  const save=()=>fs.writeFileSync(output,JSON.stringify(previous,null,2)+'\n');
  try{
    fixture.seed(`
import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Performance_Admin','performance-fixture-password-123');db.dispose()
`);fixture.env.FITNESS_PERFORMANCE_ACTION=action;fixture.env.FITNESS_PERFORMANCE_CURRENT=currentRuntime?'1':'0';if(roi)fixture.env.FITNESS_JACK_CONTENT_ROI='1';if(stateless)fixture.env.FITNESS_JACK_STATELESS='1';if(captureAmbiguity)fixture.env.FITNESS_CAPTURE_JACK_AMBIGUITY='1';if(plank||jack||deadlift||lunge||squat||basic)await fixture.startTestService('tests/formal_performance_service.py');else fixture.start();const context=await fixture.browser('Edge',{width:1280,height:900});await fixture.apiLogin(context,'Performance_Admin','performance-fixture-password-123');
    await context.addInitScript(options=>{
      const perf=window.perfReplay={video:null,measuring:false,finished:false,begin:0,end:0,submitted:0,frames:0,framingUsable:0,effective:0,unmatched:0,wrongShape:0,
        sourceLoops:0,sourceSamples:[],diagnosticFrames:[],diagnosedSeq:-1,inputTags:new WeakMap(),submittedTags:new Map(),stateHistory:[],lastMatchedStamp:null,maximumMatchingGapMs:0,matchingGapsAbove200:0,latencies:[],windows:Array.from({length:10},()=>({frames:0,usable:0,effective:0})),states:[],blobs:new WeakMap(),times:new WeakMap(),bitmaps:new WeakMap(),fakeCameraCalls:0};
      const OriginalFile=window.File,toBlob=HTMLCanvasElement.prototype.toBlob,bitmap=window.createImageBitmap.bind(window),draw=CanvasRenderingContext2D.prototype.drawImage,fetcher=window.fetch.bind(window);
      HTMLCanvasElement.prototype.toBlob=function(callback,...args){
        const at=performance.now();let tag=null;
        if(options.deadliftDiagnostic&&perf.measuring){
          const small=document.createElement('canvas');small.width=32;small.height=32;const ctx=small.getContext('2d',{willReadFrequently:true});
          draw.call(ctx,this,0,0,32,32);let hash=2166136261;for(const byte of ctx.getImageData(0,0,32,32).data)hash=Math.imul(hash^byte,16777619);
          const video=perf.video,q=video.getVideoPlaybackQuality();tag={source_time:video.currentTime,source_paused:video.paused,source_decoded_frames:q.totalVideoFrames,source_dropped_frames:q.droppedVideoFrames,capture_pixel_hash:hash};
        }
        return toBlob.call(this,blob=>{if(blob){perf.times.set(blob,at);if(tag)perf.inputTags.set(blob,tag)}callback(blob)},...args)
      };
      window.File=class extends OriginalFile{constructor(parts,...args){super(parts,...args);for(const part of parts){const at=perf.times.get(part);if(at!==undefined){perf.times.set(this,at);const tag=perf.inputTags.get(part);if(tag)perf.inputTags.set(this,tag);break}}}};
      window.fetch=(input,settings)=>{
        if(typeof input==='string'&&input.endsWith('/frames')&&settings?.body instanceof FormData){
          const meta=JSON.parse(settings.body.get('metadata')),file=settings.body.get('frame'),at=perf.times.get(file);perf.blobs.set(file,{...meta,at});
          if(options.deadliftDiagnostic&&perf.measuring)perf.submittedTags.set(meta.frame_seq,perf.inputTags.get(file));
          if(perf.measuring&&at>=perf.begin){perf.submitted++;if(meta.width!==options.submitWidth||meta.height!==options.submitHeight)perf.wrongShape++}
        }
        const pending=fetcher(input,settings);
        if(options.diagnose&&typeof input==='string'&&input.includes('/sessions/')&&input.includes('/updates'))return pending.then(async response=>{
          if(response.ok&&perf.measuring){const data=(await response.clone().json()).data,frame=data?.latest_frame;
            if(frame&&frame.frame_seq>perf.diagnosedSeq){perf.diagnosedSeq=frame.frame_seq;const f=frame.framing;
              perf.diagnosticFrames.push({seq:frame.frame_seq,captured_ms:frame.captured_ms,code:f.code,usable:f.usable,
                invalid_required:(options.deadliftDiagnostic?[11,12,13,14,15,16,23,24,25,26,27,28]:[11,12,13,14,15,16,23,24,27,28]).filter(i=>!f.landmarks[i]?.valid),outside_content:f.outside_content_indices,
                phase:Object.values(frame.analysis?.sides??{}).map(s=>s.phase).filter(Boolean).join('|')||null,reason:Object.values(frame.analysis?.sides??{}).map(s=>s.reason).filter(Boolean).join('|')||frame.analysis?.reason||null,total_count:data.total_count,
                ...(options.deadliftDiagnostic?{width:frame.width,height:frame.height,landmarks:f.landmarks,person_count:f.person_count,measurement_side:frame.analysis?.sides?.none?.measurement_side??null,input:perf.submittedTags.get(frame.frame_seq)??null}:{} )});
            }
          }return response;
        });
        return pending;
      };
      window.createImageBitmap=(input,...args)=>bitmap(input,...args).then(value=>{const tag=perf.blobs.get(input);if(tag)perf.bitmaps.set(value,tag);return value});
      CanvasRenderingContext2D.prototype.drawImage=function(source,...args){
        const result=draw.call(this,source,...args);
        if(this.canvas.id==='camera-canvas'&&perf.measuring){
          const tag=perf.bitmaps.get(source),now=performance.now();if(tag?.at<perf.begin)return result;
          const matched=Boolean(tag&&Number.isFinite(tag.at)&&tag.frame_seq===Number(this.canvas.dataset.frameSeq)&&tag.captured_ms===Number(this.canvas.dataset.capturedMs));
          if(!matched)perf.unmatched++;else{
            perf.latencies.push(now-tag.at);
            if(perf.lastMatchedStamp!==null){const gap=tag.captured_ms-perf.lastMatchedStamp;perf.maximumMatchingGapMs=Math.max(perf.maximumMatchingGapMs,gap);if(gap>200)perf.matchingGapsAbove200++}
            perf.lastMatchedStamp=tag.captured_ms;
          }
          const usable=this.canvas.dataset.framingUsable==='1',state=document.querySelector('#live-state').textContent,effective=usable&&state==='分析中';
          perf.frames++;perf.framingUsable+=usable;perf.effective+=effective;
          const bucket=perf.windows[Math.min(9,Math.floor((now-perf.begin)/30000))];bucket.frames++;bucket.usable+=usable;bucket.effective+=effective;
          if(perf.states.at(-1)?.state!==state)perf.states.push({at_ms:Math.round(now-perf.begin),state});
        }
        return result;
      };
      Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async constraints=>{
        if(constraints.audio!==false)throw Error('性能回放禁止麦克风');perf.fakeCameraCalls++;
        const source=document.createElement('video');source.muted=true;source.playsInline=true;source.preload='auto';source.loop=true;source.src=options.url;
        await new Promise((resolve,reject)=>{source.onloadeddata=resolve;source.onerror=()=>reject(Error('公开原片不可播放'))});perf.video=source;
        if(options.segment){source.loop=false;await new Promise(resolve=>{source.addEventListener('seeked',resolve,{once:true});source.currentTime=options.segment[0]})}
        const canvas=document.createElement('canvas');canvas.width=options.captureWidth;canvas.height=options.captureHeight;
        const paint=()=>{if(options.segment&&source.currentTime>=options.segment[1]&&!source.seeking){perf.sourceLoops++;source.currentTime=options.segment[0]}if(source.seeking)return;const ctx=canvas.getContext('2d');
          if(options.preserveAspect){const scale=Math.min(canvas.width/source.videoWidth,canvas.height/source.videoHeight),w=source.videoWidth*scale,h=source.videoHeight*scale;ctx.fillStyle='#000';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(source,(canvas.width-w)/2,(canvas.height-h)/2,w,h)}
          else ctx.drawImage(source,0,0,canvas.width,canvas.height);
        };paint();
        const stream=canvas.captureStream(30),timer=setInterval(paint,33),track=stream.getVideoTracks()[0],stop=track.stop.bind(track);
        const actual=track.getSettings();perf.captureSettings={width:actual.width,height:actual.height,frameRate:actual.frameRate};
        Object.defineProperty(track,'stop',{value:()=>{clearInterval(timer);source.pause();stop()}});return stream;
      }});
      Object.defineProperty(navigator.mediaDevices,'enumerateDevices',{value:async()=>[{kind:'videoinput',deviceId:'public-performance-replay',label:'公开视频性能回放 · 非真实摄像头'}]});
    },{url:(plank||jack||deadlift||lunge||squat||basic)?'/__fixture/performance-source':'/static/demo/barbell-row/video',preserveAspect:plank||jack||deadlift||lunge||squat||basic,segment:lunge?[10,24.4]:squat?[19,31.7]:null,diagnose,deadliftDiagnostic,captureWidth,captureHeight,submitWidth,submitHeight});
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    page.on('response',async response=>{if(response.url().endsWith('/commands')){const request=response.request();if(request.method()==='POST'){
      const command=request.postDataJSON().command;
      try{
        if(loseResponseBody&&command==='end'&&response.status()===200&&!bodyFaultInjected){bodyFaultInjected=true;throw Error('D-FAULT response body unavailable after navigation')}
        const result=await response.json();commands.push({command,status:response.status(),code:result.error?.code??null});
      }catch(error){commands.push({command,status:response.status(),code:null,response_body_error:error.message})}
    }}});
    if(controlOnly){let delayed=false;await page.route('**/api/v1/sessions/*/commands',async route=>{
      const command=route.request().postDataJSON()?.command;if(command==='end')endTransport.requests++;
      if(!delayed&&command===(endOnly?'end':'pause')){delayed=true;await new Promise(resolve=>setTimeout(resolve,150));const body=route.request().postDataJSON();body.expected_version=Math.max(0,body.expected_version-1);return route.continue({postData:JSON.stringify(body)})}
      if(endLost&&command==='end'&&!endTransport.dropped){const response=await route.fetch();assert.equal(response.status(),200);endTransport.dropped=true;return route.abort('failed')}
      return route.continue();
    })}
    await page.goto(fixture.origin+'/prepare?action='+action);
    await page.locator('#camera-toggle').click();await page.waitForFunction(()=>!document.querySelector('#realtime-start').disabled,{},{timeout:30000});
    await page.locator('#realtime-start').click();await page.waitForURL('**/realtime/*');await page.locator('#live-state').filter({hasText:'分析中'}).waitFor();
    const sid=new URL(page.url()).pathname.split('/').at(-1);
    await page.evaluate(async duration=>{
      await perfReplay.video.play();perfReplay.begin=performance.now();perfReplay.measuring=true;
      const state=document.querySelector('#live-state'),sample=()=>{if(!perfReplay.measuring)return;const value=state.textContent;if(perfReplay.stateHistory.at(-1)?.state!==value)perfReplay.stateHistory.push({at_ms:performance.now()-perfReplay.begin,state:value})};
      const observer=new MutationObserver(sample);observer.observe(state,{childList:true,characterData:true,subtree:true});sample();
      setTimeout(()=>{perfReplay.end=performance.now();perfReplay.measuring=false;perfReplay.finished=true;observer.disconnect()},duration);
    },durationMs);
    console.log(JSON.stringify({phase:'measurement_started',action,width:submitWidth,height:submitHeight,duration_ms:durationMs,physicalCamera:false}));
    for(let i=0;i<(shortRun?1:10);i++){
      await page.waitForTimeout(shortRun?durationMs:30000);
      const state=await page.evaluate(()=>({elapsed_ms:Math.round(performance.now()-perfReplay.begin),frames:perfReplay.frames,effective:perfReplay.effective,unmatched:perfReplay.unmatched,finished:perfReplay.finished}));
      if(lunge||squat||currentRuntime||basic){state.source_playback=await page.evaluate(()=>{const video=perfReplay.video,q=video.getVideoPlaybackQuality(),sample={elapsed_ms:Math.round(performance.now()-perfReplay.begin),source_time:video.currentTime,paused:video.paused,seeking:video.seeking,loop_seek_requests:perfReplay.sourceLoops,decoded_frames:q.totalVideoFrames,dropped_frames:q.droppedVideoFrames};perfReplay.sourceSamples.push(sample);return sample})}
      if(postureBackend){
        const snapshot=(await(await context.request.get(fixture.origin+'/api/v1/sessions/'+sid)).json()).data;
        const sample={elapsed_ms:state.elapsed_ms,save_state:snapshot.save_state,business_status:snapshot.status,coaching_state:snapshot.coaching?.state,
          coaching_frame:snapshot.coaching?.frame_seq,hold_ms:snapshot.hold_ms,rule_version:snapshot.posture_policy_version};
        backendSamples.push(sample);state.backend=sample;
        assert.equal(snapshot.save_state,'clean','新增姿态保存路径出现冻结');assert.equal(snapshot.coaching.overall_correctness,null);
      }
      console.log(JSON.stringify({phase:'measurement_progress',...state}));
      if(errors.length)throw Error(errors[0]);
    }
    await page.waitForFunction(()=>perfReplay.finished,{},{timeout:5000});
    const metrics=await page.evaluate(()=>({duration_ms:perfReplay.end-perfReplay.begin,submitted:perfReplay.submitted,frames:perfReplay.frames,framingUsable:perfReplay.framingUsable,effective:perfReplay.effective,
      unmatched:perfReplay.unmatched,wrongShape:perfReplay.wrongShape,maximumMatchingGapMs:perfReplay.maximumMatchingGapMs,matchingGapsAbove200:perfReplay.matchingGapsAbove200,latencies:perfReplay.latencies,windows:perfReplay.windows,states:perfReplay.states,stateHistory:perfReplay.stateHistory,sourceSamples:perfReplay.sourceSamples,diagnosticFrames:perfReplay.diagnosticFrames,captureSettings:perfReplay.captureSettings,fakeCameraCalls:perfReplay.fakeCameraCalls}));
    const measuredState=(await(await context.request.get(fixture.origin+'/api/v1/sessions/'+sid)).json()).data;
    const sorted=[...metrics.latencies].sort((a,b)=>a-b),percentile=q=>sorted.length?sorted[Math.ceil(sorted.length*q)-1]:null,seconds=metrics.duration_ms/1000;
    const summary={duration_seconds:seconds,processing_fps:metrics.frames/seconds,framing_usable_fps:metrics.framingUsable/seconds,effective_running_fps:metrics.effective/seconds,
      frame_encode_to_draw_p50_ms:percentile(.5),frame_encode_to_draw_p95_ms:percentile(.95),frame_alignment:metrics.unmatched===0,shape_matches_720p:portrait?null:metrics.wrongShape===0,shape_matches_expected:metrics.wrongShape===0,
      all_displayed_frames_running:metrics.states.every(s=>s.state==='分析中'),throughput_at_least_15:metrics.effective/seconds>=15,reference_count_not_accuracy:measuredState.total_count,reference_hold_ms_not_accuracy:measuredState.hold_ms};
    report={identity,dependencies,measurement_source_sha256:crypto.createHash('sha256').update(fs.readFileSync(import.meta.filename)).digest('hex'),summary,metrics:{...metrics,latencies:metrics.latencies.map(x=>Math.round(x*100)/100)},browser:await context.browser().version(),
      scope:(diagnose?'10-second reason diagnostic (extra response observation overhead); not sustained-performance or accuracy acceptance. ':controlOnly?'5-second '+(endOnly?'end':'pause')+'-control race diagnostic, 150ms first-command network delay plus a forced stale version; not sustained-performance acceptance. ':'5-minute headless 720p/30fps public ')+prefix+' video loop through '+(roi?'test-only Full VIDEO-mode with detected padding removed then landmarks mapped back':stateless?'test-only Full IMAGE-mode candidate (production uses VIDEO)':'real Full realtime pipeline')+'; not physical camera or independent accuracy'+(endLost?'; accepted end response deliberately dropped, then original result queried':''),
      source_transform:basic?'original registered train source decoded by browser, proportionally fitted into 1280x720 with black padding; full clip looped, no crop or pose cache':squat?'1024x576 source proportionally upscaled to 1280x720 without added padding; source seconds 19.0-31.7 looped; diagonal front view, not sagittal quality acceptance':lunge?'1024x576 source proportionally upscaled to 1280x720 without added padding; source seconds 10.0-24.4 looped to exclude intro and ending fade; source decode telemetry sampled every 30 seconds':portrait?'464x832 natural portrait capture, production camera.js proportionally downscales to 401x720 without artificial padding':deadlift?'464x832 portrait source downscaled proportionally into 1280x720, approximately 401.54x720 visible source with side padding':plank?'524x288 source aspect-preserved within 1280x720, black letterbox':jack?'640x480 source aspect-preserved as 960x720 within 1280x720, 160px black pillarbox on each side':'516x290 source drawn across 1280x720',
      running_state_scope:'Per-draw states plus separate DOM MutationObserver throughout measurement; UI state only, not proof of continuous inference',
      frame_latency_scope:'JPEG encoding start after capture-canvas draw to result-image draw; excludes sensor and is not movement-completion-to-feedback latency',
      movement_to_text_latency:null,voice_latency:null,full_final_runtime_passed:false,independent_ground_truth:false,physical_camera:false};
    if(diagnose){
      const tally=items=>items.reduce((out,key)=>(out[key]=(out[key]||0)+1,out),{}),frames=metrics.diagnosticFrames;
      report.diagnostic={observed_frames:frames.length,framing:tally(frames.map(f=>f.code)),counter_reasons:tally(frames.map(f=>f.reason??(f.phase?'observable':'no_counter_observation'))),
        invalid_required_indices:tally(frames.flatMap(f=>f.invalid_required)),outside_content_frames:frames.filter(f=>f.outside_content?.length).length};
      if(basic)report.diagnostic.visibility_statistic_scope='Per-index union, not overall rejection; a visible working side may suffice. Phases/reasons are combined across sides.';
      if(deadliftDiagnostic){
        const groups=[[11,13,15,23,25,27],[12,14,16,24,26,28]],missing={left:0,right:0,neither_chain_complete:0};
        for(const frame of frames){const failed=groups.map(group=>!group.every(i=>frame.landmarks[i]?.valid));missing.left+=failed[0];missing.right+=failed[1];missing.neither_chain_complete+=failed.every(Boolean)}
        report.diagnostic.required_chain_failures=missing;
        report.diagnostic.visibility_statistic_scope='Either complete side chain suffices. Individual invalid indices describe the candidate-chain union, not overall frame rejection.';
      }
      console.log(JSON.stringify({phase:'diagnostic_observed',...report.diagnostic}));
    }
    if(postureBackend){
      summary.minimum_30s_effective_fps=shortRun?null:Math.min(...metrics.windows.map(window=>window.effective/30));
      summary.each_30s_window_at_least_15=shortRun?null:summary.minimum_30s_effective_fps>=15;
      report.posture_backend={samples:backendSamples,scope:'default production posture engine, synchronous observation persistence and account loader; no physical camera or independent correctness',source_inference:'actual Full, not a pose cache'};
    }
    report.control_check={status:'pending',scope:'outside the measured window'};
    previous.runs.push(report);save(); // 已完成窗口先保存，后续控制失败不能丢弃或强迫重复推理。
    try{
      if(plank&&!endOnly){
        await page.locator('#live-pause').click();await page.locator('#live-state').filter({hasText:'已暂停'}).waitFor({timeout:8000});
        const paused=(await(await context.request.get(fixture.origin+'/api/v1/sessions/'+sid)).json()).data;
        await page.waitForTimeout(1100);
        const settled=(await(await context.request.get(fixture.origin+'/api/v1/sessions/'+sid)).json()).data;
        assert.equal(paused.status,'paused');assert.equal(settled.hold_ms,paused.hold_ms);assert.equal(settled.total_count,0);
        report.control_check.pause={hold_ms:paused.hold_ms,stable_ms:1100,state_version:paused.state_version};save();
      }
      await page.locator('#live-end').click();
      if(endOnly){
        const opened=(await(await context.request.get(fixture.origin+'/api/v1/sessions/'+sid)).json()).data;
        await page.waitForTimeout(750);
        const waiting=(await(await context.request.get(fixture.origin+'/api/v1/sessions/'+sid)).json()).data;
        assert.equal(opened.status,'running');assert.equal(waiting.status,'running');assert(waiting.hold_ms>opened.hold_ms,'仅打开弹窗不得暂停保持计时');
        report.control_check.modal={opened_hold_ms:opened.hold_ms,after_wait_hold_ms:waiting.hold_ms,still_running:true};save();
      }
      await page.locator('[data-dialog-confirm]').click();await page.waitForURL('**/records/*',{timeout:10000});
      const record=(await(await context.request.get(fixture.origin+'/api/v1/records/'+sid)).json()).data;
      if(postureBackend){
        const observations=basic?record.events.flatMap(e=>e.assessments):record.posture_observations;
        if(!diagnose)assert(observations.length>0,'必须实际保存姿态观察，不能只跑计数／保持');
        assert(observations.every(item=>item.evidence_level==='reference'&&item.rule_version===record.posture_policy_version));
        const storage=JSON.parse(fixture.seed("import os,json,sqlite3;from pathlib import Path;from contextlib import closing;d=json.load(__import__('sys').stdin);p=Path(os.environ['FITNESS_DATABASE_PATH']);\nwith closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as c:\n rows=c.execute('SELECT resource_type,count(*) FROM command_receipts WHERE resource_id=? GROUP BY resource_type',(d['sid'],)).fetchall()\n uid=c.execute('SELECT user_id FROM analysis_sessions WHERE id=?',(d['sid'],)).fetchone()[0]\n plan=[r[3] for r in c.execute(\"EXPLAIN QUERY PLAN SELECT max(ended_at_ms) FROM command_receipts WHERE actor_user_id=? AND resource_id=? AND resource_type LIKE 'session.%'\",(uid,d['sid']))]\n version=c.execute('SELECT schema_version FROM app_meta').fetchone()[0]\nprint(json.dumps({'database_bytes':p.stat().st_size,'receipt_counts':dict(rows),'last_saved_lookup_plan':plan,'schema_version':version}))",{sid}));
        report.posture_backend.saved={observations:observations.length,status_counts:observations.reduce((out,item)=>(out[item.status]=(out[item.status]||0)+1,out),{}),assessment_state:record.posture.assessment_state,rule_version:record.posture_policy_version,...storage};save();
      }
      report.control_check.commands=commands;report.control_check.saved={status:record.status,count:record.total_count,left_count:record.left_count,right_count:record.right_count,hold_ms:record.hold_ms,active_ms:record.active_ms,input_policy:record.input_policy};save();
      assert(record.total_count>=measuredState.total_count,'窗口结束后至确认结束仍可生成完整事件，不得丢失已确认次数');
      if(!plank){if(!diagnose){assert(measuredState.total_count>0,'持续窗口内没有形成可保存周期');assert(record.total_count>0,'原有可确认周期没有在本负载下产生事件')}assert(record.events.every(e=>e.side==='none'||e.side==='left'||e.side==='right'));assert.equal(record.events.length,record.total_count);assert(record.events.every((e,i)=>e.event_seq===i+1&&e.algorithm_version===config.config.algorithm_version));const again=(await(await context.request.get(fixture.origin+'/api/v1/records/'+sid)).json()).data;assert.equal(again.total_count,record.total_count)}
      if(lunge||curl){assert.equal(record.left_count+record.right_count,record.total_count);assert.equal(record.events.filter(e=>e.side==='left').length,record.left_count);assert.equal(record.events.filter(e=>e.side==='right').length,record.right_count)}
      if(plank){if(endOnly)assert(record.hold_ms>=measuredState.hold_ms);else assert.equal(record.hold_ms,report.control_check.pause.hold_ms);assert(record.hold_ms>0);assert(record.active_ms>=record.hold_ms);assert(record.intervals.every(i=>i.kind==='hold'))}
      if(endLost){assert(endTransport.dropped);assert.equal(endTransport.requests,2,'一次明确409、一次已接受但丢响应；此后只允许查询');report.control_check.transport=endTransport}
      if(loseResponseBody){assert(bodyFaultInjected);assert.equal(commands.filter(c=>c.response_body_error).length,1);report.control_check.response_body_recovered_by_record_read=true}
      report.control_check.status='passed';report.control_check.commands=commands;report.control_check.saved={status:record.status,count:record.total_count,left_count:record.left_count,right_count:record.right_count,hold_ms:record.hold_ms,active_ms:record.active_ms,input_policy:record.input_policy};save();
    }catch(caught){report.control_check.status='failed';report.control_check.error=caught.message;report.control_check.commands=commands;save();report.control_check.ui_error=await page.evaluate(selector=>document.querySelector(selector)?.textContent??null,endOnly?'#confirm-message':'#live-error').catch(()=>null);save();throw caught}
    if(postureBackend&&!shortRun)assert(summary.each_30s_window_at_least_15,'后段窗口低于15fps，不能用总平均掩盖持续吞吐下降');
    assert.equal(metrics.captureSettings.width,captureWidth);assert.equal(metrics.captureSettings.height,captureHeight);assert(summary.duration_seconds>=durationMs/1000);assert(summary.frame_alignment);assert(summary.shape_matches_expected);assert.deepEqual(errors,[]);
    console.log(JSON.stringify({phase:'measurement_completed',summary}));
  }finally{await fixture.close()}
}
