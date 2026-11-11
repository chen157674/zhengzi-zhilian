"""复用原姿态为确切视频帧确定上肢窗口；只为新训练原片补姿态，不读取保留源。"""
import numpy as np
from fitness_app.paths import project_path
from fitness_app.upper_body_roi import upper_body_roi
from fitness_app.source_partitions import enforce_source_policy
from scripts.train_chest_expert import sha,OLD
from scripts.probe_new_actions import arrays_cache


def prepare(entries,expected,observed):
    enforce_source_policy(entries);enforce_source_policy(entries,operation='inference');calls=0
    for row in entries:
        key=row['id']
        if key in expected:
            pose=project_path(observed[key]['pose_cache']) if key in observed else OLD/(key+'-full-world-features.npz')
            if sha(pose)!=expected[key]['pose_cache_sha256']:raise ValueError('原姿态摘要变化，不能用于新裁剪')
            with np.load(pose,allow_pickle=False) as a:arrays={k:a[k].copy() for k in ('timestamps','points','people')}
        else:
            if row['partition']!='train':raise ValueError('只给已核查新训练源补姿态')
            arrays,pose,count=arrays_cache(row);calls+=count
        row['roi_info']=upper_body_roi(arrays,row['actual_ms']);row['roi_pose_sha256']=sha(pose)
    return calls
