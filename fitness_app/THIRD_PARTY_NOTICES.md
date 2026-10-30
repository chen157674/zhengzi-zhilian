# 第三方纠错适配说明

- `sitrex_features.py`中的数学前处理来自Abdellah SELLAM的SiTrEx，固定提交`1eb3d7d431a6b52ba07afca0cde55d9f33822d43`，保留MIT许可于`licenses/sitrex.txt`。`reference_similarity.py`根据其模型定义读取已发布权重进行冻结数值推理，没有重新训练或宣称复现论文独立准确率。论文：SiTrEx: Siamese transformer for feedback and posture correction on workout exercises，Neurocomputing 658 (2025), 131703，DOI 10.1016/j.neucom.2025.131703。
- MMPose/RTMPose适配的坐标变换与SimCC解码依据OpenMMLab官方ONNX示例和已发布SDK。固定提交`759b39c13fea6ba094afc1fa932f51dc1b11cbf9`，Apache-2.0许可见`licenses/mmpose.txt`；预训练权重和训练数据条款需分别沿资源清单查阅。
- PSLeon24/AI_Exercise_Pose_Feedback固定提交`041ceeb6cf6263311cabb1b76344c3695b434bd7`没有声明代码/数据/权重再分发许可。该仓库及其CSV/模型仅按用户授权保留在本机研究目录；项目实现通用的受限数值树读取器，未直接复制该仓库应用代码。不能据此认定可随网站或安装包再分发其材料。

源码、原始许可、数据与模型SHA及具体运行结果见项目`开发步骤/64_现成纠错项目与预训练模型复用.md`及`data/experiments/open-source-correction/manifest.json`。私人视频不向这些项目作者或第三方服务上传。


- 运动指导源码研究与复现：Sports2D固定提交`e3b15be162182abbfe5d2084ac6b0d3f3d6099ed`及其固定Pose2Sim滤波依赖`5bce3d82096200ce248f17211b209b3723f9091a`（BSD-3-Clause），许可见`licenses/sports2d.txt`、`licenses/pose2sim.txt`；本项目新增规则参考角度/异常点/时序思路，使用本项目因果短窗实现，未直接导入上游GUI/OpenSim或双向插值来制造实时证据。原Hampel数值函数仅在复现脚本中从已校验源码提取运行。
- RTMLib固定提交`03a1693e59e4f7cd84582c0fb30459b3bf18ad42`，Apache-2.0见`licenses/rtmlib.txt`。直接使用其原RTMPose实现与本项目已有本地身体/手部ONNX做数值对照；未静默替换本项目原最小轴响应分数，新增接口不自动下载模型。
- LearnOpenCV的`AI-Fitness-Trainer-Using-MediaPipe-Analyzing-Squats`固定于`ed110e23a176c353df797d5f08977cf9d4007675`，所选子目录未发现明确再分发许可，仅本机研究其原阶段状态机；未复制到应用发布代码，不采用“膝过脚尖/深蹲太深均错误”的泛化标签。
- MotionBERT、OpenCap、SAM2及Qwen2.5-VL本轮保留固定源码/配置/许可供研究，未声称已运行其大型权重或取得私人素材上传权限。具体取用范围、提交和SHA见原资源清单与步骤66，不能把源码可读当作已具备相应准确性。
