"""把指定开发片段与已有角度序列排成核查图，不生成或修改人体画面。"""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import av
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from fitness_app.paths import ROOT
from fitness_app.action_counting import ActionCountConfig,side_observation
from fitness_app.cycles import CycleConfig
from fitness_app.media import display_rgb
from fitness_app.research_data import load_dataset
from diagnose_cycles import cached_frames

font=FontProperties(fname=r'C:\Windows\Fonts\msyh.ttc')
folder=ROOT/'data/verification/step-12';report=json.loads((folder/'development-cycles.json').read_text(encoding='utf-8'))
baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'));samples={sample.key:sample for sample in load_dataset()}
choices={'bodyweight_squat':(0,3),'standard_push_up':(0,1),'standing_dumbbell_curl':(0,9),'conventional_deadlift':(0,9)}
for action,indices in choices.items():
    item=report['actions'][action];raw=item['config'];config=ActionCountConfig(cycle=CycleConfig(**raw['cycle']),**{key:value for key,value in raw.items() if key!='cycle'})
    fig=plt.figure(figsize=(18,10),layout='constrained');grid=fig.add_gridspec(4,6,height_ratios=[1.8,1.2,1.8,1.2])
    for row,case_index in enumerate(indices):
        case=item['cases'][case_index];sample=samples[case['sample_key']];frames=cached_frames(sample,baseline);duration=frames[-1]['captured_ms']/1000
        with av.open(str(sample.absolute_path)) as container:
            stream=container.streams.video[0];origin=float(stream.start_time*stream.time_base) if stream.start_time is not None else 0
            for column,target in enumerate(np.linspace(0,duration,6)):
                container.seek(max(0,int((target+origin)*av.time_base)),backward=True);chosen=None
                for frame in container.decode(video=0):
                    chosen=frame
                    if float(frame.time)-origin>=target:break
                axis=fig.add_subplot(grid[row*2,column]);axis.axis('off')
                if chosen is not None:axis.imshow(display_rgb(chosen));axis.set_title(f'{float(chosen.time)-origin:.2f}s',fontsize=10)
        axis=fig.add_subplot(grid[row*2+1,:]);times=[frame['captured_ms']/1000 for frame in frames]
        for side,color in (('left','#287859'),('right','#c47738')):
            values=[]
            for frame in frames:
                observation=side_observation(action,frame['framing']['landmarks'],frame['width'],frame['height'],side,config) if frame['identity_accepted'] else None
                values.append(observation['values']['primary'] if observation else np.nan)
            axis.plot(times,values,color=color,label=side,linewidth=1)
        axis.axhline(config.cycle.start_threshold,color='#555555',linestyle='--',linewidth=1,label='start band')
        for event in case['events']:axis.axvline(event['end_ms']/1000,color='#547fc0',alpha=.5,linewidth=.7)
        axis.set_ylim(0,185);axis.set_xlim(0,duration);axis.legend(loc='lower right',fontsize=8);axis.set_ylabel('angle (degree)');axis.set_xlabel('source seconds')
        axis.set_title(sample.path+' | '+str(case['observed_counts']),fontproperties=font,fontsize=10)
    fig.suptitle(action+' · 开发诊断，竖线为算法事件，非计数真值',fontproperties=font,fontsize=15)
    output=folder/f'diagnostic-{action}.png';fig.savefig(output,dpi=110);plt.close(fig);print(output)
