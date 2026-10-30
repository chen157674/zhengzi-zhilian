# 观动知姿

AI健身动作分析与运动指导Web平台。第九届传智杯AI WEB网页开发挑战赛参赛项目。

交付版本：`v1.0.0-preguidance`。固定版本与离线Git包使用方式见[版本说明](docs/版本说明.md)。


系统将训练画面、姿态观察、问题时间段、训练记录和AI解读组织在同一个Web工作流中，帮助使用者查看动作、定位问题、理解建议并回看训练过程。

## 技术文档

- [作品技术文档 PDF](docs/观动知姿作品技术文档.pdf)
- [可编辑源稿](docs/作品技术文档.md)
- 在依赖准备完成后运行 `python -B scripts/build_document.py` 可重新生成PDF。

## 核心功能

- 摄像头实时分析与可选动作设置，呈现姿态骨架和当前指导。
- 上传训练视频，显示处理进度；完成后列出具体问题、时间段、调整建议，并定位回看。
- 按动作和适用机位组织姿态参考规则，采用连续时间窗、动作阶段与可见性门控。
- 提供身体侧倾、双侧阶段同步、上臂与肘部稳定、回程节奏和可见手腕方向等参考规则。
- 保存个人训练记录、组次、备注、训练档案及可选骨架，支持历史回看和PDF报告。
- 提供基于当前训练证据的AI对话、复盘和前后对比；密钥保存在服务端，费用来源和调用额度由使用者控制。
- 桌面与移动Web共用界面，包含观察、处理、保存、完成等明确状态。

## 技术架构

前端采用HTML、CSS、原生JavaScript ES模块、Canvas及浏览器媒体接口；Flask/Jinja负责页面和API，Waitress负责本机服务；SQLAlchemy/SQLite负责数据；MediaPipe与OpenMMLab ONNX提供视觉关键点；连续规则引擎生成时间段与指导；服务端AI接入层提供流式解读。

浏览器画面 → 输入与主体校验 → 姿态/动作观察 → 连续规则与任务状态 → 数据持久化 → 问题回看与AI解读。

## Windows快速启动

当前锁定环境为Python 3.12.14。所有依赖、缓存、运行数据均保留在本仓库目录内。

1. 将仓库放在本机可写目录，在该目录打开PowerShell。
2. 创建环境、安装依赖和恢复固定模型：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
   ```

3. 首次运行，创建本人管理员。用户名和密码由操作者在终端输入，不使用内置共享密码：

   ```powershell
   .\start.cmd --init-admin
   ```

4. 启动Web应用：

   ```powershell
   .\start.cmd --port 8765
   ```

5. 打开 `http://127.0.0.1:8765`，使用刚创建的账号登录。

准备脚本优先查找已安装的Python 3.12；也可以使用`-Python`参数指定该版本解释器的完整路径。

端口已占用时可改为8766等空闲端口。管理员账号建立后，可在页面管理测试账号。服务默认只监听本机回环地址。

## 评审体验顺序

1. 首页查看公开来源的内置训练视频和同步骨架。
2. 打开“视频分析”，选择内置示例，点击“开始分析”。处理过程中显示“正在观察”；完成后查看保存的问题和时间段，使用“定位片段”回看。
3. 打开“实时分析”，在设置中选择要观察的动作，按取景提示开启摄像头。
4. 查看个人训练记录、训练档案和PDF报告。
5. 需要AI解读时，进入“设置 → AI密钥”，配置评审者自己的AI服务密钥；设置与费用控制均在服务端处理。

## 环境配置

`.env.example`列出可选配置，复制为`.env`后由`start.cmd`读取。默认评审配置启用身体观察与运动指导规则。个人AI密钥推荐在设置页面保存，应用自动加密存储。

不要将`.env`、数据库、认证主密钥、个人上传视频或运行日志提交到Git。评审者启动后生成自己的数据库、账号和认证文件。

```powershell
# 核验CPU姿态评审资源，完全离线
python -B .\scripts\setup_resources.py --check

# 如需使用完整的本地视频语义模型环境，另行安装视频依赖并恢复公开模型
python -m pip install -r .\requirements.video.txt
python -B .\scripts\setup_resources.py --profile full
```

完整视频语义配置会恢复CLIP与Qwen3-VL固定版本，需按`资源清单.json`所列文件准备磁盘和GPU环境。CPU姿态、人工选择动作的视频分析和连续规则工作流使用默认core配置。

## 仓库结构

```text
fitness_app/          页面、API、姿态与动作观察、任务、认证、AI和持久化
scripts/              环境、资源恢复与启动工具
tests/                规则、状态、认证、保存与界面提示的测试
data/models/          运行参数、小型分类头与模型身份清单
data/verification/    运行参数所依赖的公开校准摘要
data/fonts/           中文报告字体与授权
data/datasets/        有明确公开来源的演示视频
third_party/          固定来源的开源参考与许可
docs/                 技术文档源稿、运行说明和源码来源记录
资源清单.json          固定下载地址、文件大小与SHA256
```

## 核心验证

安装依赖后，在仓库根目录运行：

```powershell
python -B -m unittest discover -s tests -p test_movement_guidance.py
python -B -m unittest discover -s tests -p test_body_lean.py
python -B -m unittest discover -s tests -p test_coaching_sides.py
node --test tests/correction_cue.test.mjs
```

测试代码区分工程输入、模型输出和页面状态。目录`docs/源码来源.json`记录本次从开发工程提取的文件摘要；版本历史从本次比赛材料整理时按实际修改建立。

## 第三方资源

详见`THIRD_PARTY_NOTICES.md`、`third_party/sources.json`和各来源LICENSE。模型下载脚本核验固定版本、文件大小及SHA256，运行服务不会隐式下载模型。首页演示使用公开DVIDS来源，原项目中的私人训练视频和账号数据不在本仓库中。

本仓库为比赛评审准备，原创代码权利归团队作者；第三方部分遵循其各自许可证。本仓库的私有可见性不改变第三方许可要求。
