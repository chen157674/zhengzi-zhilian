"""缓存已核验的完整融合向量，避免多次头部实验重复世界编码；不按标签改变特征。"""
import hashlib,json
import numpy as np
from fitness_app.paths import ROOT,project_path
from fitness_app.world_features import WorldFeatures
from scripts.train_partial_action_fusion import load_features


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_matrix(matrix,expected):
    if matrix.dtype!=np.float32 or matrix.shape!=(len(expected),2736) or not np.isfinite(matrix).all():raise ValueError('冻结特征矩阵类型/维度/数值无效')
    for row,identity in zip(matrix,expected):
        if hashlib.sha256(row.tobytes()).hexdigest()!=identity['feature_sha256']:raise ValueError('冻结向量与消费摘要不符')
    return matrix


def load_matrix(rows,expected,observed):
    out=ROOT/'data/verification/round3';old=ROOT/'data/verification/step-22';sources=[];identities=[]
    for row in rows:
        key=row['id'];previous=expected[key]
        if row['source_sha256']!=previous['source_sha256']:raise ValueError('向量对应的源视频不同')
        if key in observed:
            semantic=project_path(observed[key]['semantic_cache']);pose=project_path(observed[key]['pose_cache']);new=True
        else:semantic=project_path(row['cache']);pose=old/(key+'-full-world-features.npz');new=False
        if sha(semantic)!=previous['semantic_cache_sha256'] or sha(pose)!=previous['pose_cache_sha256']:raise ValueError('已冻结语义/姿态缓存改变')
        identities.append({k:previous[k] for k in ('id','source_sha256','semantic_cache_sha256','pose_cache_sha256','feature_sha256')});sources.append((semantic,pose,new))
    signature=hashlib.sha256(json.dumps(identities,sort_keys=True).encode()).hexdigest();path=out/'fusion-development-matrix.npz'
    if path.exists():
        with np.load(path,allow_pickle=False) as a:
            if str(a['identity_signature'])!=signature or a['ids'].tolist()!=[r['id'] for r in rows]:raise ValueError('矩阵缓存属于不同冻结输入，不覆盖')
            matrix=checked_matrix(a['matrix'].copy(),identities)
        return matrix,{'cache_hit':True,'world_reencodings':0,'inputs':len(rows),'identity_signature':signature,'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path)}
    encoder=WorldFeatures();values=[]
    for i,(row,source,identity) in enumerate(zip(rows,sources,identities)):
        semantic,pose,new=source;value,_=load_features(row,semantic,pose,encoder,expected=identity,new=new);values.append(value)
        if (i+1)%200==0:print(json.dumps({'stage':'freeze_full_vectors','inputs':i+1}),flush=True)
    matrix=checked_matrix(np.asarray(values,np.float32),identities);temporary=path.with_suffix('.npz.part');owned=False
    try:
        with temporary.open('xb') as f:owned=True;np.savez_compressed(f,identity_signature=signature,ids=np.asarray([r['id'] for r in rows]),matrix=matrix)
        temporary.replace(path)
    finally:
        if owned and temporary.exists():temporary.unlink()
    return matrix,{'cache_hit':False,'world_reencodings':len(rows),'inputs':len(rows),'identity_signature':signature,'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path)}
