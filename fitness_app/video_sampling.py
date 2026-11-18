"""研究与正式视频分类共用的转正取帧和完整画面缩放；不保存RGB中间图。"""
import numpy as np
from PIL import Image,ImageEnhance
from .paths import project_path
from .media import display_rgb
from .content_bounds import detect_content_bounds


def sample_frames(entry,count=8,center=None):
    import av
    start,end=entry['start_ms'],entry['end_ms'];span=min(3200,end-start)
    center=(start+end)/2 if center is None else center;start=center-span/2;end=center+span/2
    desired=np.linspace(start,end-100,count);images=[];times=[];index=0
    with av.open(str(project_path(entry['path']))) as video:
        video.seek(round(start)*1000)
        for frame in video.decode(video=0):
            if frame.time is None:continue
            stamp=round(float(frame.time)*1000)
            if stamp<desired[index]:continue
            rgb=display_rgb(frame)
            if entry['crop']:
                h,w=rgb.shape[:2];left,top,right,bottom=entry['crop'];rgb=np.ascontiguousarray(rgb[round(top*h):round(bottom*h),round(left*w):round(right*w)])
            images.append(rgb);times.append(stamp);index+=1
            if index==count:return images,times
    raise ValueError('请提供包含完整动作过程的视频')


def fit_pixels(frames,config,augmentation=None):
    """完整人物/器械等比放入224，不从竖屏视频中间切掉手脚。"""
    edge=224;mean=np.asarray(config['image_mean'],np.float32);std=np.asarray(config['image_std'],np.float32);result=[]
    for rgb in frames:
        h,w=rgb.shape[:2];left,top,right,bottom=detect_content_bounds(rgb)
        image=Image.fromarray(rgb[round(top*h):round(bottom*h),round(left*w):round(right*w)])
        if augmentation:
            if augmentation['flip']:image=image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            image=ImageEnhance.Brightness(image).enhance(augmentation['brightness'])
            image=ImageEnhance.Contrast(image).enhance(augmentation['contrast'])
        size=round(edge*(augmentation['scale'] if augmentation else 1.));image.thumbnail((size,size),Image.Resampling.BILINEAR)
        canvas=Image.new('RGB',(edge,edge),tuple((mean*255).round().astype(int)))
        canvas.paste(image,((edge-image.width)//2,(edge-image.height)//2))
        values=(np.asarray(canvas,np.float32)/255-mean)/std;result.append(values.transpose(2,0,1))
    return np.ascontiguousarray(np.asarray(result,np.float32))
