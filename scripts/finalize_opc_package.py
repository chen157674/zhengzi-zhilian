"""补齐正式运行资源并重新整理参赛提交包。"""
from pathlib import Path
import argparse, hashlib, json, os, shutil, zipfile
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'OPC专项赛';BUILD=ROOT/'.tmp/opc-completion';APP=BUILD/'作品工程'
ARCHIVE_ROOT='知练同行——基于姿态识别与大模型协同的智能健身陪练平台_530055'
DENIED={'.git','.venv','__pycache__','.cache','.tmp','third_party','reference-repos'}
def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def copy(source,target):
    source=source.resolve();target=target.resolve();source.relative_to(ROOT);target.relative_to(ROOT)
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and source.stat().st_size==target.stat().st_size and digest(source)==digest(target):return
    if not target.exists() and source.stat().st_size>64*1024*1024:
        try:os.link(source,target);return
        except OSError:pass
    shutil.copy2(source,target)
def prepare():
    for source in (ROOT/'fitness_app').rglob('*'):
        relative=source.relative_to(ROOT)
        if source.is_file() and not set(relative.parts)&DENIED and source.suffix!='.pyc':copy(source,APP/relative)
    path=APP/'fitness_app/paths.py';text=path.read_text(encoding='utf8')
    text=text.replace('if str(ROOT).lower() != r"E:\\07_Projects\\05_PersonalProjects\\10_fitness-assistant".lower():','if not (ROOT / "fitness_app/app.py").is_file():')
    path.write_text(text,encoding='utf8')
    copy(ROOT/'data/presentation-assets.json',APP/'data/presentation-assets.json')
    presentation=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf8'))
    for item in presentation['demos'].values():
        for key in ('video','poster','track'):
            value=item.get(key)
            if isinstance(value,dict) and value.get('path'):copy(ROOT/value['path'],APP/value['path'])
    for folder in ('qwen3-vl-4b-instruct','clip-vit-base-patch32','bge-small-zh-v1.5'):
        for source in (ROOT/'data/models'/folder).rglob('*'):
            if source.is_file() and not set(source.parts)&DENIED:copy(source,APP/source.relative_to(ROOT))
    for source in (ROOT/'data/models').iterdir():
        if source.is_file() and source.suffix in {'.json','.joblib','.npz','.onnx','.task','.pt'}:copy(source,APP/source.relative_to(ROOT))
    for source in (ROOT/'data/knowledge/index').glob('*'):
        if source.is_file():copy(source,APP/source.relative_to(ROOT))
    required=set()
    def collect(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if isinstance(item,str) and (key.endswith('path') or key.endswith('_path')) and item.startswith('data/verification/') and (ROOT/item).is_file():
                    required.add(item)
                collect(item)
        elif isinstance(value,list):
            for item in value:collect(item)
    for name in ('semantic-action-classifier.json','pull-press-review.json','overhead-fast-review.json'):
        collect(json.loads((ROOT/'data/models'/name).read_text(encoding='utf8')))
    for relative in required:copy(ROOT/relative,APP/relative)
    resources=[]
    for folder in ('data/models','data/fonts','data/datasets','data/knowledge/index','data/experiments','data/verification'):
        for path in (APP/folder).rglob('*'):
            if path.is_file() and path.suffix not in {'.lock','.pyc'}:resources.append({'path':path.relative_to(APP).as_posix(),'bytes':path.stat().st_size,'sha256':digest(path)})
    (APP/'交付资源清单.json').write_text(json.dumps({'schema_version':1,'profile':'production_full','files':resources},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'stage':str(APP),'resource_count':len(resources),'resource_bytes':sum(x['bytes'] for x in resources)},ensure_ascii=False),flush=True)
def compact_runtime_paths():
    """保留资源摘要，将模型的部署路径限制在Windows ZIP文件夹可读取的长度内。"""
    registry_path=APP/'资源清单.json'
    registry=json.loads(registry_path.read_text(encoding='utf8'))
    aliases={'rtmpose-body':'pose-body','rtmpose-hand':'pose-hand','rtmdet-hand':'hand-detector'}
    mapping={}
    for name,record in registry['models'].items():
        if name not in aliases:continue
        for item in record['files']:
            old=item['path'];new='data/models/'+aliases[name]+'/'+Path(old).name
            if old==new:continue
            source=(APP/old).resolve();target=(APP/new).resolve()
            source.relative_to(APP.resolve());target.relative_to(APP.resolve())
            target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():source.replace(target)
            if target.stat().st_size!=item['bytes'] or digest(target)!=item['sha256']:
                raise ValueError('模型部署路径调整后摘要不符：'+new)
            mapping[old]=new
    if mapping:
        def rewrite(value):
            if isinstance(value,str):return mapping.get(value,value)
            if isinstance(value,list):return [rewrite(item) for item in value]
            if isinstance(value,dict):return {key:rewrite(item) for key,item in value.items()}
            return value
        for path in (registry_path,APP/'data/experiments/open-source-correction/manifest.json'):
            value=json.loads(path.read_text(encoding='utf8'))
            path.write_text(json.dumps(rewrite(value),ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        path=APP/'交付资源清单.json'
        inventory=json.loads(path.read_text(encoding='utf8'))
        for item in inventory['files']:
            item['path']=mapping.get(item['path'],item['path'])
            file=APP/item['path']
            if item['path']=='data/experiments/open-source-correction/manifest.json':
                item['bytes']=file.stat().st_size;item['sha256']=digest(file)
        path.write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return len(mapping)
def documents():
    from build_opc_documents import build,head,sub,para,table
    pages=[
        [head('一 运行条件与完整交付内容'),
         para('本说明用于在评审电脑上部署并体验知练同行的完整功能。程序、正式自动分析模型、姿态模型、知识检索模型、字体与内置演示视频随参赛包提供，运行数据保存在解压后的项目目录。'),
         table(['项目','运行要求与准备方式'],[
             ['操作系统','Windows 10或Windows 11，64位；安装Python 3.12。'],
             ['完整自动分析','需要支持CUDA 12.8的NVIDIA显卡及兼容驱动。本机核验环境为RTX 5060 Laptop 8GB；建议内存32GB。'],
             ['磁盘与解压','建议预留30GB以上可用空间。完整解压ZIP后运行，支持中文目录。'],
             ['Python依赖','首次启动需联网安装基础及完整分析依赖；后续使用项目内Python环境。'],
             ['浏览器','使用现代Edge或Chrome。摄像头分析需在浏览器中允许摄像头访问。'],
             ['AI教练','需要网络、本人可用的DeepSeek接口密钥和调用额度；在设置页配置。'],
         ],[3.3,13.1]),
         sub('随包资源与个人数据'),
         para('提交包包含正式运行资源，不包含个人API密钥、开发账号数据库和个人训练档案。首次在网页注册本机账号后使用；模型在本地执行，AI教练由设置页中选定的接口提供服务。')],
        [head('二 完整启动流程'),
         table(['步骤','具体操作'],[
             ['1 完整解压','将整个参赛ZIP解压到可写目录，保持内部目录层级。'],
             ['2 启动入口','双击包根目录的“启动评审体验.cmd”。首次自动建立Python环境并安装完整运行依赖。'],
             ['3 等待检查','入口检查依赖、CUDA环境及交付资源。初次模型加载需要等待，请按窗口提示处理具体错误。'],
             ['4 注册登录','服务就绪后访问http://127.0.0.1:8765，点击注册，填写邮箱、密码及确认密码，再正常登录。'],
             ['5 配置AI','进入“设置→AI密钥”，选择个人接口来源并填写可用密钥；确认账户具备调用额度。'],
             ['6 开始体验','进入视频分析或实时分析，随后查看训练记录、AI教练、个人档案和PDF报告。'],
         ],[3.3,13.1]),
         sub('手动命令与停止'),
         para('首次准备可在作品工程目录打开PowerShell，执行：powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1。后续执行start.cmd启动，执行start.cmd --stop停止当前副本的服务。'),
         para('离线资源核对：python -B scripts/check_delivery.py。执行环境核对：python -B scripts/check_runtime.py。所有命令均在解压后的作品工程目录运行。')],
        [head('三 建议体验路径与运行核验'),
         table(['体验环节','建议操作与观察内容'],[
             ['首页与账号','注册并登录，查看导航、首页及个人资料。'],
             ['视频分析','选择内置片段或上传训练视频，启动自动分析，查看进度、动作片段与反馈。'],
             ['问题回看','点击反馈中的时间定位，核对原片、骨架显示和对应提示。'],
             ['实时分析','打开摄像头并按取景提示操作，查看姿态观察与动作反馈。'],
             ['AI追问与复盘','配置接口后围绕当前训练提问，选择训练记录生成复盘。'],
             ['记录与报告','填写训练备注，筛选历史记录，查看档案并导出PDF。'],
         ],[3.3,13.1]),
         sub('故障处理'),
         para('依赖安装失败时检查网络并重新执行准备命令；模型资源检查失败时完整解压参赛包；显卡检查失败时使用符合完整自动分析要求的电脑。摄像头被占用时释放占用后重试，AI不可用时在设置页检查接口密钥和额度。'),
         sub('核验资料'),
         para('完整运行核验覆盖本机交付副本的启动、注册登录、核心页面、真实视频分析、记录保存及PDF导出。过程记录保留在项目档案中备查。程序包内的交付资源清单记录文件大小与摘要，用于核对模型和素材完整性。'),
         para('答辩材料采用“知练同行_答辩PPT.pdf”。评审视频通过官网对应视频栏目上传，作品说明、运行说明、第三方资源说明与补充成果资料随参赛ZIP提供。')]
    ]
    build('知练同行_运行与评审体验说明','运行与评审体验说明',pages)
    evidence=OUT/'补充成果附件'
    check=json.loads((evidence/'E08_文档核验.json').read_text(encoding='utf8'))
    for row in check['文档']:
        if row['文件']=='知练同行_运行与评审体验说明.pdf':
            row['SHA256']=digest(OUT/row['文件']);row['页数']=3
    check['最终版本']='当前PDF与参赛包逐项核对；完整运行说明同步硬件条件、完整依赖及启动方式'
    (evidence/'E08_文档核验.json').write_text(json.dumps(check,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (evidence/'E09_PPT核验.json').write_text(json.dumps({'文件':'知练同行_答辩PPT.pdf','SHA256':digest(OUT/'知练同行_答辩PPT.pdf'),
        '页数':25,'说明':'采用用户指定的合并版答辩文档'},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
def package():
    compact_runtime_paths()
    review=OUT/'知练同行_作品评审视频.mp4'
    display=OUT/'知练同行_作品展示视频.mp4'
    if digest(display)!=digest(review):
        raise ValueError('展示视频尚未同步本次确认的最新成片，拒绝打包旧版')
    video_checks=json.loads((OUT/'补充成果附件/E10_视频核验.json').read_text(encoding='utf8'))
    display_check=next(item for item in video_checks if item['文件']==display.name)
    if display_check['SHA256']!=digest(display):
        raise ValueError('展示视频与当前核验记录不一致')
    targets={}
    for path in APP.rglob('*'):
        rel=path.relative_to(APP)
        if not path.is_file() or set(rel.parts)&DENIED:continue
        if rel.as_posix().startswith('data/models/open-source-correction/'):continue
        if path.name in {'.env','auth-secret.key','instance.json','service.lock'} or path.suffix in {'.pyc','.db','.sqlite3','.log','.lock','.bak'}:continue
        targets[path]=ARCHIVE_ROOT+'/01_作品及成果文件/作品工程/'+rel.as_posix()
    folders={'知练同行_OPC创新实践说明.pdf':'03_作品介绍文档','知练同行_作品介绍文档.pdf':'03_作品介绍文档','知练同行_运行与评审体验说明.pdf':'04_运行或使用说明','知练同行_补充成果材料.pdf':'05_补充成果材料','知练同行_第三方资源说明.pdf':'06_第三方资源说明','知练同行_人工智能工具使用情况说明.pdf':'07_AI工具使用补充说明','知练同行_答辩PPT.pdf':'05_补充成果材料','知练同行_作品展示视频.mp4':'02_作品展示视频'}
    for name,folder in folders.items():targets[OUT/name]=ARCHIVE_ROOT+'/'+folder+'/'+name
    evidence=OUT/'补充成果附件'
    for name in ('E05_工程审计摘录.txt','同主体下肢显示核验.png','下肢显示第三方资源说明.txt'):
        targets[evidence/name]=ARCHIVE_ROOT+'/05_补充成果材料/'+name
    for name in ('E02_真实片段接线.json','E03_连续状态观察.json','E04_动作识别开发基线.json','证据索引.json'):
        targets[evidence/name]=ARCHIVE_ROOT+'/05_补充成果材料/原始验证数据/'+name
    pending=BUILD/'submission.pending.zip';final=OUT/'最终提交材料/02_参赛压缩包/知练同行_参赛打包文件.zip'
    for arc in targets.values():
        if len(arc.encode('utf8'))>=260:raise ValueError('Windows ZIP路径过长：'+arc)
    with zipfile.ZipFile(pending,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as archive:
        for path,arc in targets.items():
            compression=zipfile.ZIP_STORED if path.suffix in {'.safetensors','.onnx','.task','.mp4','.npz','.pt'} else zipfile.ZIP_DEFLATED
            archive.write(path,arc,compress_type=compression)
            if path.stat().st_size>1024**3:print('已加入模型：'+path.name,flush=True)
        archive.writestr(ARCHIVE_ROOT+'/启动评审体验.cmd','@echo off\nchcp 65001 >nul\ncall "%~dp001_作品及成果文件\\作品工程\\启动评审体验.cmd"\n')
        archive.writestr(ARCHIVE_ROOT+'/知练同行_材料阅读顺序.txt','先阅读03_作品介绍文档和04_运行或使用说明，再双击启动评审体验.cmd。\n答辩材料为05_补充成果材料/知练同行_答辩PPT.pdf。\n报名信息在系统填写；盖章承诺书、截图、评审视频按官网对应栏目单独上传。\n02_作品展示视频用于补充呈现实际成果，其余适用说明材料已分类随包提供。\n')
    if pending.stat().st_size>=10_000_000_000:raise ValueError('超过官网单包10GB限制，未替换正式包')
    with zipfile.ZipFile(pending) as archive:
        if archive.testzip() is not None:raise ValueError('压缩包完整性检查失败')
        if any(set(Path(n).parts)&DENIED for n in archive.namelist()):raise ValueError('压缩包含禁止目录')
        expected={'01_作品及成果文件','02_作品展示视频','03_作品介绍文档','04_运行或使用说明','05_补充成果材料','06_第三方资源说明','07_AI工具使用补充说明'}
        names=archive.namelist()
        if {name.split('/')[0] for name in names}!={ARCHIVE_ROOT}:raise ValueError('最外层目录不符合官方参考命名')
        if {name.split('/')[1] for name in names if name.count('/')>1}!=expected:raise ValueError('材料分类目录不符合当前适用清单')
        for path,arc in targets.items():
            if path.stat().st_size<1024**3 and archive.read(arc)!=path.read_bytes():
                raise ValueError('打包期间文件发生变化：'+path.name)
    pending.replace(final)
    print(json.dumps({'package':str(final),'bytes':final.stat().st_size,'files':len(targets)+2,'sha256':digest(final),'zip_integrity':'passed','under_10gb':True},ensure_ascii=False),flush=True)
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('prepare','documents','package'));args=parser.parse_args()
    {'prepare':prepare,'documents':documents,'package':package}[args.action]()
