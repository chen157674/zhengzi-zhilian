// 后续页面测试共用的隔离服务／浏览器启动器；不接日常资料或正式个人库。
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {execFileSync,spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

export const root=path.resolve(import.meta.dirname,'..');
assert(fs.existsSync(path.join(root,'fitness_app/app.py')));
const require=createRequire('C:/Users/chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/package.json');
const module=await import(pathToFileURL(require.resolve('playwright')).href);
const {chromium}=module.default||module;
export class BrowserFixture {
  constructor(name){
    assert(/^[a-z0-9-]+$/.test(name));
    this.temporary=path.join(root,'.tmp/tests',name);
    assert(!fs.existsSync(this.temporary),'测试目录已存在，不覆盖其他会话');
    fs.mkdirSync(this.temporary,{recursive:true});
    this.env={...process.env,TEMP:this.temporary,TMP:this.temporary,PYTHONDONTWRITEBYTECODE:'1',PYTHONUTF8:'1',
      FITNESS_RUNTIME_DIR:path.join(this.temporary,'runtime'),FITNESS_DATABASE_PATH:path.join(this.temporary,'app.sqlite3')};
    this.contexts=[];this.started=false;
  }
  python(args,input){return execFileSync(path.join(root,'.venv/Scripts/python.exe'),['-B',...args],{
    cwd:root,env:this.env,windowsHide:true,encoding:'utf8',input})}
  seed(code,data={}){return this.python(['-c',code],JSON.stringify(data))}
  start(){const info=JSON.parse(this.python(['-m','fitness_app','--no-browser','--port','0']).trim());this.started=true;this.origin=`http://127.0.0.1:${info.port}`;return this.origin}
  async startTestService(script){
    assert(/^tests\/formal_[a-z_]+_service\.py$/.test(script));
    const log=fs.openSync(path.join(this.temporary,'service.log'),'a');
    try{this.child=spawn(path.join(root,'.venv/Scripts/python.exe'),['-B','-c',`import runpy; runpy.run_path('${script}',run_name='__main__')`],{cwd:root,env:this.env,windowsHide:true,stdio:['ignore',log,log]})}finally{fs.closeSync(log)}
    this.exitPromise=new Promise(resolve=>this.child.once('exit',resolve));
    for(let attempt=0;attempt<100;attempt++){
      if(this.child.exitCode!==null)throw new Error('测试服务异常退出：'+fs.readFileSync(path.join(this.temporary,'service.log'),'utf8').slice(-1800));
      const instance=path.join(this.env.FITNESS_RUNTIME_DIR,'instance.json');
      if(fs.existsSync(instance)){const info=JSON.parse(fs.readFileSync(instance,'utf8'));this.origin=`http://127.0.0.1:${info.port}`;try{if((await fetch(this.origin+'/api/v1/health',{signal:AbortSignal.timeout(3000)})).ok){this.started=true;return this.origin}}catch{}}
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    throw new Error('测试服务启动未确认');
  }
  async browser(name='Edge',viewport={width:1440,height:900},{deviceScaleFactor=1,recordVideoDir=null}={}){
    const executable=name==='Chrome'?'C:/Program Files/Google/Chrome/Application/chrome.exe':'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
    assert(fs.existsSync(executable),name+' 不存在');
    if(recordVideoDir){const resolved=path.resolve(recordVideoDir);assert(!path.relative(root,resolved).startsWith('..'));assert(fs.existsSync(resolved));}
    const context=await chromium.launchPersistentContext(path.join(this.temporary,name),{headless:true,executablePath:executable,viewport,deviceScaleFactor,...(recordVideoDir?{recordVideo:{dir:recordVideoDir,size:viewport}}:{}),env:this.env,downloadsPath:path.join(this.temporary,name+'-downloads'),
      args:['--no-first-run','--no-default-browser-check','--disable-component-update','--disable-background-networking',
        `--disk-cache-dir=${path.join(this.temporary,name+'-cache')}`,`--crash-dumps-dir=${path.join(this.temporary,name+'-crashes')}`]});
    this.contexts.push(context);return context;
  }
  async apiLogin(context,username,password){
    const token=(await(await context.request.get(this.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;
    const response=await context.request.post(this.origin+'/api/v1/auth/login',{data:{username,password},headers:{'X-CSRFToken':token}});
    assert.equal(response.status(),200,'测试身份准备失败');
  }
  async close(){
    const wasStarted=this.started;
    for(const context of this.contexts)await context.close();
    if(this.started){const result=JSON.parse(this.python(['-m','fitness_app','--stop']).trim());assert(result.stopped);this.started=false}
    if(this.child)await this.exitPromise;
    const resolved=fs.realpathSync(this.temporary);assert.equal(resolved,this.temporary);assert(!path.relative(root,resolved).startsWith('..'));
    fs.rmSync(resolved,{recursive:true,force:true,maxRetries:10,retryDelay:200});
    console.log('Owned browser profiles, service and test database cleaned.');
    if(this.child&&wasStarted)assert.equal(this.child.exitCode,0);
  }
}
