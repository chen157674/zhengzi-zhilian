"""识别明确的成对深色编码留边；不把暗背景当作人体边界或精确遮挡检测。"""
import hashlib
import math
from pathlib import Path

import numpy as np

BOUNDS_VERSION=1
BOUNDS_CONFIG={'dark_max':32,'uniform_range':16,'minimum_pixels':4,'minimum_fraction':.01,
    'maximum_fraction':.25,'symmetry_fraction':.025,'interior_pixels':16,'interior_contrast':12,'interior_fraction':.55}


def bounds_signature():
    return {'version':BOUNDS_VERSION,'config':BOUNDS_CONFIG.copy(),'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def _edge_run(mask):
    indices=np.flatnonzero(~mask)
    return int(indices[0]) if len(indices) else len(mask)


def _axis_padding(rgb,axis):
    length=rgb.shape[axis];cfg=BOUNDS_CONFIG
    if axis==0:
        maximum=rgb.max(axis=(1,2));minimum=rgb.min(axis=(1,2))
    else:
        maximum=rgb.max(axis=0).max(axis=1);minimum=rgb.min(axis=0).min(axis=1)
    flat=(maximum<=cfg['dark_max']) & ((maximum-minimum)<=cfg['uniform_range'])
    leading=_edge_run(flat);trailing=_edge_run(flat[::-1]);minimum_band=max(cfg['minimum_pixels'],round(length*cfg['minimum_fraction']))
    # 整幅纯色、单边阴影、过厚或明显不对称的暗区都不擅自解释为编码留边。
    if not minimum_band<=min(leading,trailing) or max(leading,trailing)>length*cfg['maximum_fraction'] or abs(leading-trailing)>max(2,length*cfg['symmetry_fraction']):return 0,0
    if leading+trailing>=length:return 0,0
    border=max(int(maximum[:leading].max()),int(maximum[length-trailing:].max()))
    step=min(cfg['interior_pixels'],length-leading-trailing)
    if axis==0:inside=(rgb[leading:leading+step],rgb[length-trailing-step:length-trailing])
    else:inside=(rgb[:,leading:leading+step],rgb[:,length-trailing-step:length-trailing])
    # 活动画面需在边界内侧广泛区别于深色带，避免把黑背景中的小人物当作整幅画面。
    if any(float(np.mean(part.max(axis=2)>border+cfg['interior_contrast']))<cfg['interior_fraction'] for part in inside):return 0,0
    return leading,trailing


def detect_content_bounds(rgb):
    if not isinstance(rgb,np.ndarray) or rgb.dtype!=np.uint8 or rgb.ndim!=3 or rgb.shape[2]!=3 or min(rgb.shape[:2])<1:raise ValueError('有效画面检测需要实际 RGB uint8 图像')
    height,width=rgb.shape[:2];top,bottom=_axis_padding(rgb,0)
    # 水平留边不参与柱状留边的颜色判定，坐标仍保持原图归一空间。
    interior=rgb[top:height-bottom if bottom else height]
    left,right=_axis_padding(interior,1)
    return [left/width,top/height,(width-right)/width,(height-bottom)/height]


def validate_content_bounds(bounds):
    if not isinstance(bounds,(list,tuple)) or len(bounds)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in bounds):raise ValueError('图像有效区域格式无效')
    left,top,right,bottom=bounds
    if not 0<=left<right<=1 or not 0<=top<bottom<=1:raise ValueError('图像有效区域范围无效')
    return bounds


def point_in_content(point,bounds):
    left,top,right,bottom=bounds
    # 原图边缘仍遵循既有0～1含端点规则；明确留边的内侧边界采用半开范围。
    return left<=point['x'] and top<=point['y'] and (point['x']<right if right<1 else point['x']<=1) and (point['y']<bottom if bottom<1 else point['y']<=1)
