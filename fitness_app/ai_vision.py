"""聊天图片只随明确授权的单次提问发送；不落盘，不接受远程图片地址。"""
import base64
import hashlib
from io import BytesIO

from PIL import Image, UnidentifiedImageError

MAX_FRAMES = 6
MAX_FRAME_BYTES = 200 * 1024
JPEG_PREFIX = 'data:image/jpeg;base64,'


def validate_frames(frames):
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError('请附上当前画面后再发送')
    checked = []
    for frame in frames:
        if not isinstance(frame, dict) or set(frame) != {'data_url', 'source_ms'}:
            raise ValueError('画面格式无效，请重新发送')
        url, stamp = frame['data_url'], frame['source_ms']
        if not isinstance(url, str) or not url.startswith(JPEG_PREFIX) or len(url) > MAX_FRAME_BYTES * 4 // 3 + 32:
            raise ValueError('画面过大或格式无效，请重新发送')
        if type(stamp) is not int or not 0 <= stamp <= 2**53 - 1:
            raise ValueError('画面时间无效，请重新发送')
        try:
            data = base64.b64decode(url[len(JPEG_PREFIX):], validate=True)
            if not 0 < len(data) <= MAX_FRAME_BYTES:
                raise ValueError('图片长度无效')
            with Image.open(BytesIO(data)) as image:
                if image.format != 'JPEG' or min(image.size) < 1 or max(image.size) > 640:
                    raise ValueError('图片尺寸无效')
                image.verify()
        except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
            raise ValueError('画面内容无效，请重新发送') from error
        digest = hashlib.sha256(data).hexdigest()
        if any(row['sha256'] == digest for row in checked):
            continue
        checked.append({'data_url': url, 'source_ms': stamp, 'sha256': digest})
    if any(b['source_ms'] < a['source_ms'] for a, b in zip(checked, checked[1:])):
        raise ValueError('画面顺序无效，请重新发送')
    return checked


def input_budget(messages):
    """文字按UTF-8字节预留；官方视觉上限为每张图片1024个输入Token。"""
    if not isinstance(messages, list) or not 1 <= len(messages) <= 30:
        raise ValueError('消息无效')
    text_bytes = images = encoded_bytes = 0
    for row in messages:
        if not isinstance(row, dict) or set(row) != {'role', 'content'} or row['role'] not in {'system', 'user', 'assistant'}:
            raise ValueError('消息结构无效')
        content = row['content']
        if isinstance(content, str):
            text_bytes += len(content.encode('utf-8'))
            continue
        if row['role'] != 'user' or not isinstance(content, list) or not content or len(content) > MAX_FRAMES * 2 + 1:
            raise ValueError('图片只能附在用户提问中')
        for part in content:
            if isinstance(part, dict) and set(part) == {'type', 'text'} and part['type'] == 'text' and isinstance(part['text'], str):
                text_bytes += len(part['text'].encode('utf-8'))
            elif isinstance(part, dict) and set(part) == {'type', 'image_url'} and part['type'] == 'image_url':
                image = part['image_url']
                if not isinstance(image, dict) or set(image) != {'url', 'detail'} or image['detail'] not in {'low','original'} or not isinstance(image['url'], str) or not image['url'].startswith(JPEG_PREFIX):
                    raise ValueError('图片输入无效')
                encoded_bytes += len(image['url'])
                images += 1
            else:
                raise ValueError('消息内容无效')
    if text_bytes > 256 * 1024 or images > MAX_FRAMES or encoded_bytes > MAX_FRAMES * (MAX_FRAME_BYTES * 4 // 3 + 32):
        raise ValueError('上下文超过本应用上限')
    return text_bytes + images * 1024, images


def attach_frames(messages, frames):
    content = [{'type': 'text', 'text': messages[-1]['content']}]
    for index, frame in enumerate(frames):
        content.append({'type': 'text', 'text': '所选媒体抽样画面 ' + str(index + 1) + '，源时间 ' + str(frame['source_ms']) + ' ms，按源时间排列。直接回答动作问题，不复述采样张数或间隔。'})
        content.append({'type': 'image_url', 'image_url': {'url': frame['data_url'], 'detail': 'original'}})
    messages[-1] = {'role': 'user', 'content': content}
    input_budget(messages)
