import fs from "node:fs/promises";
import path from "node:path";
import {pathToFileURL} from "node:url";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")), "..");
const out = path.join(root, "OPC专项赛");
const build = path.join(root, ".tmp", "opc-completion");
await fs.mkdir(build, {recursive:true});
const runtime = "C:/Users/chen/.cache/codex-runtimes/codex-primary-runtime/dependencies";
process.env.RUNTIME_NODE_MODULES=path.join(runtime,"node/node_modules");
const skill = "C:/Users/chen/.codex/plugins/cache/openai-primary-runtime/presentations/26.1030.10813/skills/presentations";
const {Presentation, PresentationFile} = await import(pathToFileURL(
  path.join(runtime, "node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs")
).href);
const {finalizePresentation} = await import(pathToFileURL(
  path.join(skill, "container_tools/artifact_tool_utils.mjs")
).href);
const pres = Presentation.create({slideSize: {width: 1280, height: 720}});
const font = "Microsoft YaHei";
const specs = [
  ["知练同行", "让训练反馈回到具体片段", "面向自主健身的\nWeb应用原型\n\n姿态观察与视频回看\nAI解释与训练记录", "01_首页.png",
   "作品定位及界面来源：OPC专项赛/知练同行_作品介绍文档.pdf；作品截图/01_首页.png。"],
  ["训练者需要看懂自己的动作", "从观看教程到复核本人训练", "用户上传训练视频\n\n查看带时间范围的问题\n\n对照画面理解调整方向", "02_视频分析首页.png",
   "定位与场景来源：作品介绍文档第一部分。没有大规模用户调研或商业验证数据。"],
  ["一条反馈关联同一段画面", "问题定位与交互回看", "问题包含部位和时间段\n\n点击后回到对应片段\n\n结合骨架与原画面复核", "07_动作反馈.png",
   "交互证据：作品截图/07_动作反馈.png；视频任务与训练结果实现。"],
  ["连续观察与可见性约束", "证据不足时保留不可判断", "人体关键点与连续窗口\n\n动作阶段和机位约束\n\n区分未观察到与不可判断", "03_实时分析.png",
   "规则依据：fitness_app、补充成果附件/E02_真实片段接线.json。图像规则不是医学诊断。"],
  ["AI解释使用当前训练依据", "让追问围绕本次训练", "组织有效训练摘要\n\n通过服务端调用已配置模型\n\n区分检测结果与AI解释", "04_AI教练.png",
   "AI能力取决于实际供应商及配置；本页不宣称全部供应商支持视觉输入。截图来源：04_AI教练.png。"],
  ["训练记录支持持续复盘", "将一次反馈留在训练历史中", "记录、问题与备注关联\n\n有效修订与历史可追踪\n\n数据按账号范围隔离", "06_训练记录.png",
   "数据一致性依据：开发步骤57_第三轮跨模块业务审计.md。截图只证明界面，不证明使用规模。"],
  ["档案连接反馈与复练", "保留下一次训练的线索", "个人训练记录汇集\n\n复盘与报告入口\n\n无充分数据时保留暂无结论", "09_健身档案.png",
   "截图来源：09_健身档案.png。训练趋势不是实际改善效果的对照实验。"],
  ["已有成果可以按证据复核", "开发结果与工程结果\n分别报告", "193个开发正例窗口\n含拒判正确率约88.60%\n\n88项历史工程审计通过\n不替代独立纠错效果评价", "05_训练概览.png",
   "来源：补充成果附件/E04_动作识别开发基线.json；E05_工程审计摘录.txt。宏召回83.90%，已输出精确率90.96%。基线不是完整第三轮验收。"],
  ["协作贡献覆盖完整工作流", "实际贡献以成员核对为准", "需求与方案组织\n视觉适配与应用集成\n前端、后端、测试与交付\n\nAI辅助范围单独披露", "08_单项复测.png",
   "成员使用匿名代号，真实成员与分工由报名系统和团队确认。第三方基础模型不作为从零原创模型。"],
  ["以可运行原型继续验证", "当前成果与后续计划分开", "当前展示分析与复盘流程\n\n继续完善弱类与遮挡\n验证手机使用体验\n\n保留源代码与配置\n保存验证记录", "10_登录页面.png",
   "交付通过运行说明与可运行工程复核。独立纠错真值、真实AI和手机测试仍按各自范围开展。"],
];

function text(slide, value, x, y, w, h, size, color="#14251F", bold=false) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position: {left:x, top:y, width:w, height:h},
    fill:"none", line:{fill:"none", width:0},
  });
  shape.text = value;
  shape.text.style = {typeface:font, fontSize:size, color, bold};
  return shape;
}
for (let i=0; i<specs.length; i++) {
  const [title, sub, body, img, notes] = specs[i];
  const slide = pres.slides.add();
  slide.background.fill="#F7F9F6";
  text(slide, title, 52, 40, 1160, 74, 45, "#14251F", true);
  text(slide, sub, 54, 137, 340, 84, 25, "#247148", true);
  text(slide, body, 54, 250, 338, 345, 25);
  slide.images.add({
    blob:new Uint8Array(await fs.readFile(path.join(out,"作品截图",img))),
    contentType:"image/png", alt:title+" 实际系统界面",
    fit:"contain", position:{left:426,top:135,width:802,height:492},
  });
  text(slide, String(i+1).padStart(2,"0"), 1176, 663, 50, 28, 18, "#66786E");
  slide.speakerNotes.text=notes;
  const preview = await pres.export({slide,format:"png",scale:1});
  await fs.writeFile(path.join(build,`slide-${i+1}.png`),new Uint8Array(await preview.arrayBuffer()));
  console.log(`slide ${i+1} rendered`);
}
const draft=path.join(build,"presentation-candidate.pptx");
await (await PresentationFile.exportPptx(pres)).save(draft);
const finalPath=path.join(out,"知练同行_作品汇报.pptx");
if (await fs.stat(finalPath).catch(()=>false)) throw new Error("目标已存在，请在原件上实施修订");
const result=await finalizePresentation({
  workspaceDir:root, candidatePath:draft, finalPath,
  pythonExecutable:path.join(runtime,"python/python.exe"),
  integrityValidatorPath:path.join(skill,"container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath:path.join(skill,"container_tools/inspect_presentation_layout_geometry.py"),
  layoutArgs:["--expected-slide-size-emu","12192000,6858000","--validate-heading-fit"],
  explicitTotalSlideCount:10,
  requiredNativeTableOwnerSlides:[],
  requiredNativeChartOwnerSlides:[],
  fontPolicy:{basis:"design",families:[font]},
  verifyArtifactToolImport:true,
  receiptPath:path.join(build,"presentation-validation.json"),
});
console.log(JSON.stringify(result));
