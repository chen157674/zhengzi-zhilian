# 第三方来源与许可说明

本项目采用独立的Web、任务与数据持久化实现，并使用开源视觉模型、数值算法和开发框架。保留的第三方代码与模型采用各自许可证，原创系统实现不改变上游权利。

| 来源 | 本项目使用方式 | 许可与记录 |
| --- | --- | --- |
| Apache ECharts 6.1.0 | 成长看板的折线、柱状图与SVG交互；本地固定发行构建 | Apache-2.0，`third_party/echarts/LICENSE`、`NOTICE`及`third_party/sources.json`固定提交/SHA |
| Flask、Jinja、SQLAlchemy、Waitress | Web服务、模板、数据映射与本机HTTP服务 | 按各发行包的许可证分发 |
| MediaPipe | 人体姿态关键点输入 | Apache-2.0，`data/models/manifest.json`与`data/models/LICENSE` |
| OpenMMLab MMPose、RTMPose、RTMDet | ONNX身体/手部关键点与输入适配 | Apache-2.0，固定权重摘要见`资源清单.json` |
| yakupzengin/fitness-trainer-pose-estimation | 动作YAML结构和关节角计算思路；本项目增加边界与数值校验 | MIT，`third_party/fitness-trainer-pose-estimation/LICENSE` |
| OpenCV Zoo | 可选视觉特征骨干的固定来源 | Apache-2.0，`third_party/opencv_zoo` |
| Google Fonts Noto Sans SC | 训练报告与比赛PDF的中文字体 | SIL OFL，`data/fonts/noto-sans-sc/OFL.txt`及`third_party/sources.json` |
| DVIDS | 首页公开杠铃划船示例 | 来源页面与素材说明见`data/presentation-assets.json` |
| CLIP、Qwen3-VL | 扩展视频语义识别环境中的固定版本编码器 | 按对应模型仓库许可，见各模型manifest与LICENSE |

其他保留的源码参考均在`third_party/sources.json`登记来源、固定提交及文件摘要，并在各来源目录保留LICENSE。模型下载器只从登记的官方或固定托管地址获取资源。

评审与运行时请保留本说明、来源清单、上游LICENSE和字体OFL。不要把第三方预训练模型表述为团队从零独立训练。
