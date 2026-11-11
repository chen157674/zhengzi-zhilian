// 使用 Trellis 官方任务 SDK；原 PRD／步骤只引用，不生成副本或 .codex 配置。
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const root = path.resolve(import.meta.dirname, '..');
if (root !== 'E:\\07_Projects\\05_PersonalProjects\\10_fitness-assistant') throw Error('项目路径越界');
const sdk = await import(pathToFileURL(path.join(root, '.tools/node_modules/@mindfoldhq/trellis-core/dist/task/index.js')).href);
const [number = '01', status = 'in_progress'] = process.argv.slice(2);
const files = fs.readdirSync(path.join(root, '开发步骤')).filter(n => /^\d\d_.*\.md$/.test(n)).sort();
if (!files.some(n => n.startsWith(number + '_'))) throw Error('步骤编号不存在');
for (const filename of files) {
  const id = filename.slice(0,2);
  const taskDir = path.join(root, '.trellis/tasks', 'step-' + id);
  let record = fs.existsSync(path.join(taskDir, 'task.json'))
    ? sdk.loadTaskRecord({taskDir})
    : sdk.emptyTaskRecord({id:'step-'+id,name:'step-'+id,title:filename.slice(3,-3),status:'planning',
        creator:'codex',assignee:'codex',createdAt:'2026-10-19',relatedFiles:['PRD.md','开发文档.md','开发步骤/'+filename],
        notes:'以原步骤文档为执行与验收依据，不复制 PRD；不自动提交或启动子代理。'});
  if (id === number) {
    record.status=status; record.completedAt=status==='completed'?'2026-10-19':null;
    if(status==='completed'){
      const sourceFiles=fs.readdirSync(path.join(root,'fitness_app'),{recursive:true}).filter(name=>/\.(py|js|css|html)$/.test(name)).map(name=>path.join('fitness_app',name));
      sourceFiles.push('requirements.lock.txt','third_party/sources.json','data/models/manifest.json');
      record.meta={...record.meta,source_sha256:Object.fromEntries(sourceFiles.map(name=>[name.replaceAll('\\','/'),createHash('sha256').update(fs.readFileSync(path.join(root,name))).digest('hex')])),
        evidence_note:'实际命令、用例范围与限制见原步骤执行记录；文件哈希用于后续影响核对，不代表自动运行或全量通过。'};
    }
  }
  sdk.writeTaskRecord({taskDir,record});
}
fs.writeFileSync(path.join(root,'.trellis/.current-task'),'.trellis/tasks/step-'+number+'\n');
console.log('Trellis task: step-'+number+' '+status);
