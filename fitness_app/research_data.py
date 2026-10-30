"""既有四动作研究索引与固定人物划分；不下载或重写源数据。"""
from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .database import json_text
from .paths import ROOT,project_path

DATA_ROOT=ROOT/'data/datasets'
INDEX_SHA256='95608ab5db3048ba87fbb86868799431d6b0a5ee1cb24d9d72ab68d779b87baf'
MANIFEST_SHA256='84b149f93a33533087ad6cc6707bdaf053f0ab540f43154d86d09d450b27a817'
SEED=20261018
COMMON_GROUPS={
    'train':('001','002','005+006','007','009','010+013','011','014+020','015+026','021','022','023','024'),
    'validation':('004','016','018','019'),
    'test':('003','008','012','017'),
}
DL_FOLDS=(('S01',),('S02','S03'),('S05',),('S06',))
SUPPORTED={'conventional_deadlift','bodyweight_squat','standard_push_up','standing_dumbbell_curl'}


@dataclass(frozen=True)
class Sample:
    dataset_id:str
    sha256:str
    path:str
    bytes:int
    action_id:str
    label:str|None
    subject_group:str
    subjects:tuple
    views:tuple
    partition:str
    validation_fold:int|None
    annotation_status:str
    quality_use:str

    @property
    def key(self):return self.dataset_id+':'+self.sha256

    @property
    def absolute_path(self):
        relative=Path(self.path)
        if relative.is_absolute() or relative.drive or '..' in relative.parts:raise ValueError('样本路径不是安全的相对路径')
        path=project_path(DATA_ROOT/relative)
        path.relative_to(DATA_ROOT)
        return path

    def assignment(self):
        return {'sample_key':self.key,'action_id':self.action_id,'label':self.label,'subject_group':self.subject_group,
            'partition':self.partition,'validation_fold':self.validation_fold,'annotation_status':self.annotation_status,'quality_use':self.quality_use}


def common_partitions():
    return {'+'.join('subject_'+part for part in group.split('+')):partition for partition,groups in COMMON_GROUPS.items() for group in groups}


def plan_rows(rows,check_files=False):
    """输入副本可注入冲突验证；不从路径名称推断标签或信任来源 split。"""
    groups=common_partitions();seen_keys=set();seen_hashes={};assignments={};samples=[]
    for row in rows:
        action=row['action_id'];dataset=row['dataset_id'];sha=row['sha256'];group=row['subject_group']
        if action not in SUPPORTED:raise ValueError('索引含未确认研究动作')
        if dataset!=('w5prmmxyt9' if action=='conventional_deadlift' else 'kgbb3yn47p'):raise ValueError('研究任务与来源不匹配')
        if len(sha)!=64 or any(char not in '0123456789abcdef' for char in sha):raise ValueError('样本摘要不合法')
        key=dataset+':'+sha
        if key in seen_keys:raise ValueError('索引含重复 sample key')
        seen_keys.add(key)
        labels=set(row['source_labels'].split('|'));excluded=row['annotation_status']=='excluded_conflicting_source_labels'
        expected={'GB','PB'} if action=='conventional_deadlift' else {'good','bad'}
        if not labels<=expected or not labels:raise ValueError('来源标签不属于当前任务')
        if len(labels)>1 and not excluded:raise ValueError('冲突标签没有被隔离')
        if excluded:
            if len(labels)<2 or row['quality_use']!='excluded':raise ValueError('隔离原因与原标签不一致')
            partition='excluded';fold=None;label=None
        else:
            if row['annotation_status']!='consistent_source_label':raise ValueError('未经确认的标注状态')
            use='source_GB_PB_only' if action=='conventional_deadlift' else 'coarse_good_bad_only'
            if row['quality_use']!=use:raise ValueError('标签使用粒度不一致')
            label=next(iter(labels));fold=None
            if action=='conventional_deadlift':
                if group in {'S04','S07'}:partition='test'
                else:
                    partition='development';fold=next((index for index,fold_groups in enumerate(DL_FOLDS) if group in fold_groups),None)
                    if fold is None:raise ValueError('硬拉人物没有固定划分')
            else:
                if group not in groups:raise ValueError('人物关联组没有固定划分')
                partition=groups[group]
        subjects=tuple(row['source_subjects'].split('|'))
        if not set(subjects)<=set(group.split('+')):raise ValueError('原始人物未全部纳入关联组')
        # 同一摘要与任意关联人物都不能跨集合；合并人物组不拆回单个编号。
        signature=(dataset,group,partition)
        if sha in seen_hashes and seen_hashes[sha]!=signature:raise ValueError('同源摘要跨组或跨集合')
        seen_hashes[sha]=signature
        if partition!='excluded':
            for subject in subjects:
                owner=(dataset,subject)
                if owner in assignments and assignments[owner]!=(group,partition):raise ValueError('人物跨关联组或集合泄漏')
                assignments[owner]=(group,partition)
        sample=Sample(dataset,sha,row['path'],int(row['bytes']),action,label,group,subjects,tuple(row['views'].split('|')),partition,fold,row['annotation_status'],row['quality_use'])
        path=sample.absolute_path
        if sample.bytes<=0:raise ValueError('源文件字节数无效')
        if check_files and (not path.is_file() or path.stat().st_size!=sample.bytes):raise ValueError('已准备文件缺失或大小变化：'+sample.key)
        samples.append(sample)
    return sorted(samples,key=lambda sample:sample.key)


def load_dataset():
    raw=(DATA_ROOT/'index.csv').read_bytes();manifest=(DATA_ROOT/'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=INDEX_SHA256 or hashlib.sha256(manifest).hexdigest()!=MANIFEST_SHA256:
        raise ValueError('原索引／manifest 与已确认版本不同，停止实验')
    rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    return plan_rows(rows,check_files=True)


def split_snapshot(samples):
    rows=[sample.assignment() for sample in sorted(samples,key=lambda sample:sample.key)]
    digest=hashlib.sha256(json_text({'assignments':rows}).encode()).hexdigest()
    counts=Counter((sample.action_id,sample.partition) for sample in samples)
    return {'schema_version':1,'index_sha256':INDEX_SHA256,'manifest_sha256':MANIFEST_SHA256,'split_sha256':digest,'seed':SEED,
        'truth_scope':'source_clip_labels_only','assignments':rows,
        'counts':[{'action_id':action,'partition':partition,'samples':count} for (action,partition),count in sorted(counts.items())]}
