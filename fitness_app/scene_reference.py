"""以人体区域外的长直线共识估计画面竖轴；不足时拒判，不假定画面边缘为重力。"""
import math

VERSION = 'background-vertical-consensus-1'


def estimate_vertical(rgb, landmarks):
    import numpy as np
    missing = {'status': 'unavailable', 'basis': VERSION}
    try:
        import cv2
    except ImportError:
        return {**missing, 'reason': 'scene_reference_dependency_missing'}
    height, width = rgb.shape[:2]
    if min(height, width) < 120:
        return {**missing, 'reason': 'scene_reference_unavailable'}
    scale = min(1., 640/width)
    image = cv2.resize(rgb, (round(width*scale), round(height*scale))) if scale < 1 else rgb
    h, w = image.shape[:2]
    edges = cv2.Canny(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), 70, 160)
    visible = [p for p in landmarks if p.get('visibility', 0) >= .5 and p.get('presence', 0) >= .5
               and 0 <= p.get('x', -1) <= 1 and 0 <= p.get('y', -1) <= 1]
    if len(visible) < 8:
        return {**missing, 'reason': 'scene_reference_unavailable'}
    left = max(0, int((min(p['x'] for p in visible)-.04)*w))
    right = min(w, int((max(p['x'] for p in visible)+.04)*w))
    top = max(0, int((min(p['y'] for p in visible)-.04)*h))
    bottom = min(h, int((max(p['y'] for p in visible)+.04)*h))
    edges[top:bottom, left:right] = 0
    lines = cv2.HoughLinesP(edges, 1, math.pi/720, threshold=40,
                            minLineLength=max(35, int(.18*h)), maxLineGap=8)
    candidates = []
    for x1,y1,x2,y2 in np.asarray(lines).reshape(-1,4) if lines is not None else []:
        if y2 > y1:
            x1,y1,x2,y2 = x2,y2,x1,y1
        angle = math.degrees(math.atan2(x2-x1, y1-y2))
        length = math.hypot(x2-x1,y2-y1)
        if abs(angle) <= 25:
            candidates.append((angle,length,(x1+x2)/2))
    if len(candidates) < 3:
        return {**missing, 'reason': 'scene_reference_unavailable'}
    center = max(candidates, key=lambda row: sum(other[1] for other in candidates if abs(other[0]-row[0]) <= 2.5))[0]
    support = [row for row in candidates if abs(row[0]-center) <= 2.5]
    total = sum(row[1] for row in candidates)
    weight = sum(row[1] for row in support)
    if len(support) < 3 or weight/total < .6 or weight < .65*h or max(r[2] for r in support)-min(r[2] for r in support) < .08*w:
        return {**missing, 'reason': 'scene_reference_unavailable'}
    angle = float(np.median([row[0] for row in support]))
    return {'status': 'observed', 'basis': VERSION, 'image_up_degrees': angle,
            'support_lines': len(support), 'support_fraction': round(weight/total, 4),
            'width': width, 'height': height,
            'limitation': '背景近竖直长线的图像参考，不是相机IMU或实测重力'}


def lateral_measurement(frame):
    from .posture_engine import points, view_ratio
    reference = frame.get('framing', {}).get('scene_reference', {})
    if reference.get('status') != 'observed' or reference.get('basis') != VERSION:
        return None, 'scene_reference_unavailable'
    if (reference.get('width'), reference.get('height')) != (frame.get('width'), frame.get('height')):
        return None, 'scene_reference_unavailable'
    p = points(frame, [11,12,23,24])
    if p is None:
        return None, 'required_parts_or_identity_missing'
    ratio = view_ratio(frame)
    if ratio is None or ratio < .55:
        return None, 'unsupported_view'
    degrees = reference.get('image_up_degrees')
    if type(degrees) not in (float,int) or not math.isfinite(degrees) or abs(degrees)>25:
        return None, 'scene_reference_unavailable'
    angle = math.radians(degrees)
    up = (math.sin(angle), -math.cos(angle))
    right = (-up[1], up[0])
    trunk = tuple((p[11][i]+p[12][i]-p[23][i]-p[24][i])/2 for i in (0,1))
    vertical = sum(trunk[i]*up[i] for i in (0,1))
    horizontal = sum(trunk[i]*right[i] for i in (0,1))
    if vertical < .08*frame['height']:
        return None, 'required_parts_or_identity_missing'
    signed = math.degrees(math.atan2(horizontal,vertical))
    anatomical_left = sum((p[11][i]-p[12][i])*right[i] for i in (0,1))
    side = 'left' if horizontal*anatomical_left > 0 else 'right'
    return {'stamp': frame['captured_ms'], 'angle': abs(signed), 'signed_angle_degrees': signed,
            'side': side, 'reference_degrees': degrees}, None


def evaluate_lateral(frames, threshold, allowed_views, previous=None):
    rows = []
    for frame in reversed(frames):
        if frame.get('posture_view') not in allowed_views:
            reason = 'unsupported_view'
            row = None
        else:
            row, reason = lateral_measurement(frame)
        if row is None:
            if not rows:
                return {'status': 'unassessable', 'reason': reason}
            break
        if rows and not 0 < rows[-1]['stamp']-row['stamp'] <= 250:
            break
        rows.append(row)
    rows.reverse()
    if len(rows)<8 or rows[-1]['stamp']-rows[0]['stamp']<900:
        return {'status':'unassessable','reason':'posture_duration_unconfirmed'}
    current = rows[-1]
    held = bool(previous and previous['identity'][0]=='issue' and previous.get('side')==current['side'])
    boundary = threshold-3 if held else threshold
    start = current['stamp']
    for row in reversed(rows):
        if row['angle'] <= boundary or row['side'] != current['side']:
            break
        start = row['stamp']
    issue = current['angle'] > boundary and (held or current['stamp']-start >= 900)
    if current['angle'] > boundary and not issue:
        return {'status':'unassessable','reason':'posture_duration_unconfirmed'}
    return {'status':'issue' if issue else 'not_observed','reason':None,'value':current['angle'],
            'threshold':threshold,'side':current['side'] if issue else 'none',
            'start_ms':start if issue else current['stamp'], 'end_ms':current['stamp'],
            'feature_name':'trunk_angle_from_background_vertical', 'reference_degrees':current['reference_degrees'],
            'signed_angle_degrees':current['signed_angle_degrees'],
            'measurement_basis':'双肩髋中线相对背景长线共识方向；连续900ms，恢复带低3度；工程参考未独立校准'}
