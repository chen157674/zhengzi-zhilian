"""本人头像的受限解码与标准化，不使用上传文件名或保留EXIF。"""
from io import BytesIO
import re
import warnings
from PIL import Image,ImageOps,UnidentifiedImageError
from .auth import APIError
from .paths import project_path
from .database import StorageError

MAX_AVATAR_BYTES=4*1024*1024


def decode_avatar(stream):
    raw=stream.read(MAX_AVATAR_BYTES+1)
    if len(raw)>MAX_AVATAR_BYTES:raise APIError('AVATAR_TOO_LARGE','请选择4 MiB以内的头像',413)
    if not raw:raise APIError('INVALID_AVATAR','请选择一张图片',422)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                if image.format not in {'JPEG','PNG','WEBP'}:raise APIError('INVALID_AVATAR','头像仅支持JPEG、PNG或WebP图片',422)
                if not all(64<=n<=4096 for n in image.size):raise APIError('INVALID_AVATAR','图片边长需在64～4096像素之间',422)
                if getattr(image,'is_animated',False):raise APIError('INVALID_AVATAR','请使用静态头像图片',422)
                image.verify()
            with Image.open(BytesIO(raw)) as source:
                image=ImageOps.fit(ImageOps.exif_transpose(source),(512,512),method=Image.Resampling.LANCZOS).convert('RGBA')
                background=Image.new('RGBA',image.size,(246,247,244,255));background.alpha_composite(image)
                output=BytesIO();background.convert('RGB').save(output,format='JPEG',quality=88,optimize=True)
                return output.getvalue()
    except (UnidentifiedImageError,OSError,ValueError,SyntaxError,Image.DecompressionBombWarning,Image.DecompressionBombError) as error:
        raise APIError('INVALID_AVATAR','请重新选择图片',422) from error


def stored_avatar(root,key):
    if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{32}\.jpg',key):raise StorageError('已存头像标识不合法')
    root=project_path(root);path=project_path(root/key)
    try:path.relative_to(root)
    except ValueError as error:raise StorageError('头像存储边界不合法') from error
    return path
