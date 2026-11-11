"""生成本轮同版本作品技术文档；只读源码/证据，不改冻结比赛资料。"""
from pathlib import Path
import json,os,sys,hashlib
from html import escape
ROOT=Path(__file__).resolve().parents[1];TASK=Path(__file__).resolve().parent
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'.tmp/runtime');os.environ['PYTHONDONTWRITEBYTECODE']='1'
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak,Table,TableStyle,Image,KeepTogether
from reportlab.graphics.shapes import Drawing,Rect,Line,String
FONT=ROOT/'data/fonts/noto-sans-sc';pdfmetrics.registerFont(TTFont('SC',str(FONT/'GuanDongSansSC-Regular.ttf')));pdfmetrics.registerFont(TTFont('SCB',str(FONT/'GuanDongSansSC-SemiBold.ttf')))
INK=colors.HexColor('#402517');ACCENT=colors.HexColor('#B9431C');MUTED=colors.HexColor('#78675B');SOFT=colors.HexColor('#FFF4E8');LINE=colors.HexColor('#E8D8C8');W,H=A4;CW=W-96
styles={k:ParagraphStyle(k,fontName='SCB' if k in {'title','h1','h2','th'} else 'SC',fontSize=size,leading=leading,textColor=INK if k!='caption' else MUTED,spaceAfter=space,wordWrap='CJK',keepWithNext=k in {'h1','h2'}) for k,size,leading,space in [('title',30,42,18),('h1',21,30,18),('h2',13,21,8),('body',10.1,17.5,10),('small',8.8,14,7),('caption',8,12,10),('cell',8.6,13.5,0),('th',8.6,13.5,0)]}
styles['title'].textColor=ACCENT
story=[]
def text(value,style='body'):return Paragraph(escape(str(value)).replace('\n','<br/>'),styles[style])
def add(value,style='body'):story.append(text(value,style))
def section(title,paragraphs):add(title,'h2');[add(p) for p in paragraphs]
def table(headers,rows,widths=None):
 data=[[text(x,'th') for x in headers]]+[[text(x,'cell') for x in row] for row in rows]
 t=Table(data,colWidths=[CW*x for x in (widths or [1/len(headers)]*len(headers))],repeatRows=1,hAlign='LEFT');t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),SOFT),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.4,LINE),('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]));story.extend([t,Spacer(1,12)])
def image(name,height,caption):
 p=TASK/'实现核对'/name
 from PIL import Image as PIL
 with PIL.open(p) as im:width,pixels=im.size
 scale=min(CW/width,height/pixels);story.append(Image(str(p),width=width*scale,height=pixels*scale,hAlign='CENTER'));add(caption,'caption')
def page(title):
 if story:story.append(PageBreak())
 add(title,'h1')
start=json.loads((TASK/'实现核对/源码交付启动核对.json').read_text(encoding='utf-8'));backend=json.loads((TASK/'实现核对/相关后端回归核对.json').read_text(encoding='utf-8'));real=json.loads((TASK/'实现核对/当前原片自动纠正核对.json').read_text(encoding='utf-8'));retrieval=json.loads((TASK/'实现核对/知识检索对照核对.json').read_text(encoding='utf-8'))
assert backend['result']=='passed' and real['state']=='completed'
for name,digest in start['source_files'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,'源码已改变，先重验启动'
page('知练同行作品技术文档')
add('基于姿态识别与证据约束的智能健身指导平台','title')
add('第九届传智杯 AI WEB网页开发挑战赛','h2')
add('本轮功能与交付说明  2026年11月','caption')
add('作品面向自主训练场景，将画面采集、动作观察、具体纠正、片段定位、针对性练习和训练计划组织为可回看的Web工作流。此次完善重点是让已有纠正提示清楚可见，并把“怎样调整”连接到“如何练习与核对”。')
table(['使用者关心的问题','对应产品行为'],[['这次哪里需要调整','分析结果首层显示具体问题、下一次做法、侧别或部位及发生时段。'],['如何理解依据','可查看保存规则、适用机位和有版本的知识来源。'],['如何记录后续训练','计划逐组保存实际完成量，复测选择新记录并明确可比条件。']], [.32,.68])
add('当前交付为已验证本地源码及既有资源。本说明与第五轮源码对应；旧预指导提交副本保持冻结。文中分别说明工程测试、实际模型回归及尚未验证范围，不以UI示意或协议替身证明算法效果。','small')
page('一 产品任务与信息组织')
section('目标使用者与使用情境',['使用者通过浏览器训练或上传已录制视频，希望快速看懂具体问题并回到对应画面。用户可以不配置云端AI，先使用分析反馈、手动计划和本地饮食估算；需要解释时再明确发送范围。','系统记录的是图像可见观察和用户主动填写的训练事实。计划完成率、参考姿态结论及训练效果具有不同含义，不合并成一个健康分数。'])
table(['一级入口','主要内容'],[['首页','保留介绍和演示，提供实时分析、视频分析及轻量AI咨询入口；下滑按主题连续阅读。'],['实时分析','主动开启摄像头、取景、观察、暂停与结束；当前有效纠正靠近画面。'],['视频分析','选片、真实异步任务、自动分段、局部重分析、结果与原片回看。'],['我的训练','计划、记录、周回顾；纠正任务从具体问题和计划直达。'],['健身指南','动作知识与饮食建议，知识检索与身体资料采用分别授权。']], [.22,.78])
section('交互组织',['主要问题与一句做法默认展示；规则、来源与详细过程按需展开。桌面保留主辅布局，手机按当前任务、结果、来源、次级操作堆叠。固定底栏预留内容高度与安全区，避免遮住表单和反馈。'])
page('二 技术架构与数据流')
add('系统沿用Flask/Jinja、原生JavaScript ES模块与CSS、Canvas、SQLAlchemy和SQLite。浏览器负责媒体与交互，本地服务负责身份、任务、视觉推理、连续规则及保存；云端语言模型经受控任务调用，不直接写训练数据。')
d=Drawing(CW,238)
for i,(head,body) in enumerate([('浏览器','摄像头 / 本地视频 / Canvas / 表单与反馈'),('本地API与任务','认证与CSRF / 输入校验 / 异步分析 / 取消与恢复'),('视觉与证据','姿态与动作模型 / 可见性 / 连续规则 / 源时间'),('存储与知识','SQLite事务 / 版本与回执 / 发布知识 / 费用账本')]):
 y=187-i*56;d.add(Rect(0,y,CW,43,rx=8,fillColor=SOFT,strokeColor=LINE));d.add(String(12,y+24,head,fontName='SCB',fontSize=10,fillColor=INK));d.add(String(12,y+9,body,fontName='SC',fontSize=9,fillColor=MUTED))
 if i<3:d.add(Line(CW/2,y,CW/2,y-13,strokeColor=ACCENT))
story.append(d)
section('接口按资源归属',['视频父任务、分段修订和记录详情保留原协议；新训练计划与执行统一在/api/v1/training。知识目录与条目按发布版本返回，练习信息扩展原复测任务身份。每类资源使用自身版本号，不能以同一个version覆盖所有对象。'])
section('本轮原创工程工作',['本轮实现集中于纠正可发现性、跨页证据身份、原片恢复、计量分配防重、计划状态机、引用失效和任务隐私生命周期。模型权重与阈值沿原能力边界，不把第三方预训练模型描述为团队从零训练的基础模型。'])
page('三 动作纠正与原片回看')
add('视频结果保留原播放器与反馈结构，首层明确显示问题、下一次做法及时间定位。记录详情使用同一保存证据；AI为次级解释，不是获得基本纠正提示的前置步骤。')
image('纠正提示-桌面缺片.png',280,'当前页面工程状态检查：反馈由明确协议夹具提供，示例问题不作为真实模型检出证据。')
section('证据与状态',['准备、观察不足、暂停、遮挡、断网、保存失败和过期状态撤回旧问题，不以“没有结果”显示动作正常。半开时段、侧别、部位、规则版本和来源身份贯穿反馈与定位。','刷新后本地原片可能未加载，保存提示仍可读。用户重新选择原片后，经本地服务流式核验SHA256与字节数，匹配成功才创建本地播放URL并启用定位；错片拒绝，不重跑分析。'])
section('可查看的依据',['反馈行可展开已保存规则、适用机位与工程来源。读取当前规则时若版本不一致，明确标为当前参考；外部资料支持动作原则，工程比例与确认时间不冒称机构阈值。'])
page('四 实时与异步视频分析')
section('实时交互',['保留宽画面与原工具栏。深色纠正提示在画面附近默认展示，手机紧邻画面下方；详细说明展开不隐藏主要做法。镜像只改变显示坐标，不改变真实左右侧语义。','摄像头必须由使用者主动开启。切后台、断网、中断和旧帧撤回反馈；恢复需明确继续。结束与保存失败分开处理，未保存内容保留核对、重试或明确放弃入口。'])
section('上传与修订',['视频任务区分处理中、处理结束、终态保存和临时副本清理。自动分段保留无法确认范围；用户可对单段明确指定动作重新分析，原有效结果保留至新版本正式发布。','取消后不得以前端停止轮询冒充服务端取消完成。任务历史、清理和重试沿原资源身份核对；不同输入或旧修订不能混用同一反馈缓存。'])
table(['场景','用户看见的行为'],[['缺原片','显示占位与重新选择，保存反馈保留，定位禁用并解释原因。'],['保存失败','分析暂停，保留已保存版本和原批次，用户明确重试或放弃未保存部分。'],['切换来源或旧响应迟到','按页面generation、资源ID和版本丢弃旧包，清旧媒体绑定。'],['双侧比较','原片分别匹配、手动锚点对齐；缓冲或结束暂停两侧，取消等待也不能恢复播放。']], [.25,.75])
add('实时生命周期浏览器检查采用合成Canvas流；实际手机摄像头硬件、软键盘和跨设备实时性能仍需独立验收。','small')
page('五 有来源知识与饮食参考')
section('知识检索与RAG',['本机已发布知识条目分别标识外部摘要与工程规则，提供动作范围、版本、摘录及来源。查询先返回本地检索结果，用户同意后才生成回答；公共知识模式禁止读取身体档案和私人训练历史。','关键词与本地语义向量融合采用真实返回模式。冷加载时先返回关键词结果并标语义准备中，模型不可用时明确降级。生成引用必须来自本任务实际检索集合，撤回或旧版本不能默默换成新正文支持旧结论。'])
image('知识检索-桌面.png',230,'知识页面真实检索与引用界面；此截图生成回答使用供应商协议替身，仅证明页面接线和引用身份。')
section('饮食输入与本地方法',['蛋白质与每日维持热量复用确定性方法，输入不足提示具体字段，不猜单位、年龄或整体活动水平。结果显示适用条件与来源；模型不重新计算精确摄入值。','本次输入、保存营养资料、删除已存资料与更新健身档案是不同动作。一般知识可不填体重；只有主动勾选时发送本次表单。改输入撤回旧回答并清同意，离页或换号清临时正文。'])
page('六 纠正练习与同项复测')
add('使用者从真实保存问题选择一项纠正重点，明确保存后形成练习任务。任务保留origin与证据版本，练习步骤可编辑并关联同动作训练执行；知识引用撤回时撤回相应步骤，重新核对后才能继续。')
table(['任务阶段','含义'],[['待练习 / 练习中','记录用户主动保存的重点及当前练习状态，不增加原分析训练量。'],['待复测','用户确认练习完成；仍没有动作改善结论。'],['已有比较','选择新记录、确认同人及可比条件，服务器核对动作、侧别、规则、机位与观察量。'],['来源失效','来源删除、修订或过期后不可继续使用旧比较；仍给出可理解的返回出口。']], [.3,.7])
section('候选与比较',['目标选择提供搜索、日期筛选、分页与记录详情预览。确认时核对预览版本，超时先读取原任务查看是否已绑定；不能换目标消除未知结果。','比较显示issue、evaluated与unknown及真实单位。观察不足、不适用或条件不同明确解释，未知不当正常，不输出整体姿态正确或长期改善率。两侧原片独立匹配，手动相对时间对齐不称自动配准。'])
section('与计划协同',['计划动作可关联同动作纠正任务，执行页直达具体重点；该关联不自动标练习完成。删除计划解除执行关联，保留原分析与纠正来源。'])
page('七 手动计划与逐组进度')
section('保存与启用分开',['手动创建可填写目标、经验、器械、日期和每个动作的组数、次数或保持时间、休息与负重。支持排序、复制与移除确认，先保存草稿，再核对具体目标启用；替换计划同时校验新旧版本。','未来修订仅替换未开始安排，已开始执行保留目标快照。改期、跳过、恢复、暂停与归档记录事件，周进度可展开核对分区、分母和排除原因。'])
image('训练计划-桌面.png',230,'隔离账号的实际手动计划界面；目标与完成量属于工程流程检查，不作为真人训练效果。')
section('权威完成量',['每组保存成功后更新进度。少于目标可记部分完成；跳过为空，不当0次达标。整场必须全部目标达标且明确确认，未达标只能提前结束。暂停或离页不会自动完成。','分析记录只采用用户明确选择的完整周期或有效保持区间。同源SHA、周期与重叠范围防重；每侧动作要求左右完整等量，静态区间可明确切组但不自动平均。删除来源后派生待核对，手动量保留。'])
page('八 AI任务 数据与隐私')
section('有界任务与用户决定',['知识、饮食、计划草稿与周解释复用费用来源和账本，受模型轮次、工具、输出与任务预算约束。工具来自允许集合，模型不能产生任意可执行链接或直接保存/启用计划。','AI草稿可人工编辑，保存与启用分别操作。周解释仅采用指定周结构化事实，不重算数字；事实版本变化时撤回旧解释。达到轮次上限返回明确未完成状态，不发布未验证答案。'])
table(['边界','实现'],[['权限与请求','登录身份、本人资源校验、CSRF、版本冲突；私有响应no-store。'],['正式数据','计划、组、安排事件与用户主动保存资料持久化；命令回执只存最小身份与版本。'],['临时AI内容','问题、答案与未保存生成草稿仅内存，有效期、退出、换号、取消和来源失效清理。'],['未知结果','保留原命令ID和内存body核对；刷新后丢body的未登记命令须明确事务封存，拒绝迟到原写。'],['用量','取消、失败、超时和排队过期均收尾；已发生费用保留，未知费用不显示0元成功。']], [.22,.78])
section('迁移与兼容',['schema13新增计划、修订、日期、执行、组、分配、安排事件与原复测附表。所有人复合约束与唯一活动索引由SQLite执行。原schema12默认拒绝自动升级，需显式允许；隔离迁移验证数据保持、重复迁移、错误身份和失败回滚。'])
page('九 测试与实测结果')
add('测试按证据层级报告。协议替身验证状态和契约，合成媒体验证匹配、解码与定位，真实开发原片验证对应模型执行；这些结果不能互相替代。')
table(['验证范围','当前结果'],[['相关后端工程','39组共'+str(backend['tests'])+'项，0失败、0错误、0跳过；涵盖认证、权限、事务、版本、AI任务、计量、迁移与原业务。'],['浏览器交互','新14入口×7宽度；原15页面、认证、角色、表单、未知结果、迟到包、取消、键盘焦点、报告与媒体专项通过。'],['正式启动','start.ps1正常运行配置、Cookie/CSRF登录、9页与5私有API、计划→执行→完成→回顾通过。'],['知识既有90题','混合动作开发21/21、原留出18/21；关键词19/21、18/21。两种饮食均12/12；题集已使用，不计新增独立留出。']], [.25,.75])
section('当前真实原片回归',['正式默认规则下，两段既有开发原片均识别为高位下拉。1688保存5733至6200毫秒左侧倾，1689无保存issue；新姿态调用1112次，动作窗口25次，分别约155.9秒与174.7秒。输入副本已清理。','前一次未设置参考规则开关的运行也保留，未算通过；模型源码身份异常只恢复到已验证字节，不改权重、阈值或清单门禁。结果为已见开发原片回归，不是实时速度或独立人群精度。'])
add('真实供应商生成质量、有无检索回答质量对照、独立错误真值、真人同条件调整、物理相机与手机硬件均未由本轮工程检查证明。','small')
page('十 运行交付与可复现入口')
section('当前源码交付',['前端、后端、启动脚本与资源清单指纹已固定，完整记录在实现核对中的源码交付启动核对.json。该报告对应实际启动及核心写入链，不以打包成功替代运行验证。','原个人库和预览库仍为schema12，未被隔离检查迁移或重启。使用新功能前须按README在已核对范围内决定升级，不能用空库替换已有账号库。'])
table(['操作','入口'],[['启动','在项目根目录PowerShell运行 scripts/start.ps1 --no-browser --port 0；沿项目已有Python环境与资源。'],['迁移','仅在确认目标库并完成隔离验证后，启动进程显式设置 FITNESS_ALLOW_ROUND5_MIGRATION=1；未知身份仍拒绝。'],['开发与接口','第五轮开发详细文档第4至11、17至19节：字段、状态、按钮与位置；开发步骤记录真实验证。'],['工程复现','tests/round5_delivery_start_check.py 及本轮UI检查；知识对照与原片报告保留实际模式、来源和版本。'],['提交材料','本技术文档及本轮演示材料对应当前源码；旧比赛/预指导副本保持冻结，官网提交另以回执核对。']], [.23,.77])
add('源码指纹','h2');add(start['source_fingerprint'],'small')
section('演示应遵循的事实',['先展示实际纠正提示与定位，再展示依据、练习和计划。若演示为工程预置数据、模型结果回放或供应商协议替身，必须在画面或讲解中明确标识；不得拼接为真实用户改善或实时推理。'])
page('十一 第三方资源与边界')
add('作品复用公开框架、预训练模型与公开方法资料；第三方能力与本轮工程贡献分别说明。正式分发按各资源许可证与清单恢复，不能删除应保留的署名或许可。')
table(['资源类别','用途与来源说明'],[['Web与数据','Flask、Jinja、Waitress、SQLAlchemy与SQLite用于页面、服务和事务；前端为原生模块和Canvas。'],['视觉输入','MediaPipe与OpenMMLab相关姿态资源提供关键点；模型与辅助包依既有固定清单恢复，实际能力有机位与可见性边界。'],['动作语义','已登记Qwen视觉语言基座与项目融合/复核策略；当前源码严格校验执行资源身份，不把33类目录等同全类别独立精度。'],['知识检索','BGE-small-zh-v1.5本地编码，固定revision与推理文件校验；检索条目保存发布状态、定位与外部链接。'],['饮食方法','ISSN蛋白质与运动资料、FAO/WHO/UNU能量方法等；计算来源与适用人群在结果中可查，维持热量不等于个性化处方。'],['动作原则','ACE、NASM等已核对摘要与链接；工程阈值不冒称原机构建议值。'],['字体与素材','中文字体沿项目已登记OFL资源。原演示素材、模型和第三方许可遵循各自清单；私人原片未上传至云端生成服务。']], [.24,.76])
section('后续验证方向',['补充独立新人物与机位的具体错误真值，开展真实同条件复测、物理设备与不同手机浏览器检查，并按明确范围评价真实生成回答质量。任何新结论都应绑定数据、版本、分子分母与未知量。'])
def footer(canvas,doc):
 canvas.saveState();canvas.setStrokeColor(LINE);canvas.line(48,42,W-48,42);canvas.setFont('SC',8);canvas.setFillColor(MUTED);canvas.drawString(48,29,'知练同行  作品技术文档');canvas.drawRightString(W-48,29,str(doc.page));canvas.restoreState()
def main():
 target=TASK/'作品技术文档.pdf';doc=SimpleDocTemplate(str(target),pagesize=A4,leftMargin=48,rightMargin=48,topMargin=42,bottomMargin=58,title='知练同行作品技术文档',author='',subject='本轮功能 技术与验证');doc.build(story,onFirstPage=footer,onLaterPages=footer)
 sys.path.append('C:/Users/chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/Lib/site-packages')
 from pypdf import PdfReader
 reader=PdfReader(target);assert len(reader.pages)<=30;print(json.dumps({'output':str(target),'pages':len(reader.pages),'source_fingerprint':start['source_fingerprint']},ensure_ascii=False))
if __name__=='__main__':main()
