"""从已有性能数字绘图，不启动应用、解码视频或推理模型。"""
import json
import math
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path,configure_environment
configure_environment()
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties,fontManager


def main():
    source=project_path(sys.argv[1] if len(sys.argv)>1 else 'data/verification/step-19/performance-barbell-row.json')
    source.relative_to(ROOT/'data/verification/step-19');report=json.loads(source.read_text(encoding='utf-8'));run=report['runs'][-1];metrics=run['metrics'];summary=run['summary']
    font=Path(r'C:\Windows\Fonts\msyh.ttc')
    if not font.is_file():raise RuntimeError('现有中文字体不可用，先核实，不安装额外字体')
    fontManager.addfont(str(font));plt.rcParams.update({'font.family':FontProperties(fname=str(font)).get_name(),'axes.unicode_minus':False})
    fig,axes=plt.subplots(1,2,figsize=(12,4.8));windows=metrics['windows'];x=[(i+.5)*.5 for i in range(len(windows))]
    seconds=[min(30,max(.001,metrics['duration_ms']/1000-i*30)) for i in range(len(windows))]
    axes[0].plot(x,[w['frames']/t for w,t in zip(windows,seconds)],marker='o',color='#759ba5',label='处理帧率')
    axes[0].plot(x,[w['effective']/t for w,t in zip(windows,seconds)],marker='o',color='#24644d',label='程序判定可用且分析中')
    axes[0].axhline(15,color='#ad6243',linestyle='--',label='15 fps参考门槛');axes[0].set(xlabel='运行时间（分钟；每点为30秒窗口）',ylabel='fps',ylim=(0,max(21,math.ceil(max(w['frames']/t for w,t in zip(windows,seconds))/5)*5+1)),xlim=(0,5));axes[0].legend(fontsize=9,loc='lower right');axes[0].grid(alpha=.2)
    axes[1].hist(metrics['latencies'],bins=40,color='#a8c7b5',edgecolor='white')
    for key,label,color in [('frame_encode_to_draw_p50_ms','P50','#24644d'),('frame_encode_to_draw_p95_ms','P95','#ad6243')]:
        value=summary[key];axes[1].axvline(value,color=color,linestyle='--',label=f'{label} {value:.1f} ms')
    axes[1].set(xlabel='JPEG编码开始至结果画面绘制（毫秒）',ylabel='帧数');axes[1].legend(fontsize=9)
    names={'barbell_bent_over_row':'杠铃划船','forearm_plank':'前臂平板支撑','jumping_jack':'标准开合跳','conventional_deadlift':'传统杠铃硬拉','reverse_lunge':'后撤弓步','bodyweight_squat':'徒手深蹲'}
    fig.suptitle(names[report['action_id']]+' · 5分钟公开素材回放负载',fontsize=16)
    fig.text(.5,.07,f"有效分析 {summary['effective_running_fps']:.2f} fps · {metrics['frames']}个显示帧 · 同帧对齐：{'通过' if summary['frame_alignment'] else '未通过'}",ha='center',fontsize=11)
    source_note={'bodyweight_squat':'原片1024×576等比放大至1280×720，循环19.0～31.7秒；斜前方机位','reverse_lunge':'原片1024×576等比放大至1280×720，循环10.0～24.4秒动作段','forearm_plank':'原片524×288等比放入1280×720','jumping_jack':'原片640×480等比放入1280×720，两侧留边','conventional_deadlift':'原片464×832等比放入1280×720，竖幅源片两侧留边'}.get(report['action_id'],'原片516×290上采样至1280×720')
    fig.text(.5,.025,source_note+'；非现场摄像头。帧显示时延不等于动作完成→文字／语音时延。',ha='center',fontsize=9,color='#555555')
    fig.tight_layout(rect=(0,.13,1,.93));target=source.with_suffix('.png');fig.savefig(target,dpi=150);plt.close(fig);print(target)


if __name__=='__main__':main()
