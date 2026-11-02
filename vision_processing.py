"""Keep native camera/vision libraries outside the robot HTTP process."""
import base64
import json
from pathlib import Path
import subprocess
import sys

VISION_PYTHON = '/usr/local/bin/python3'


def request(operation, meta, raw, jpeg=None, **extra):
    payload = {'operation':operation, 'meta':meta,
               'depth':base64.b64encode(raw).decode(), **extra}
    if jpeg is not None: payload['jpeg'] = base64.b64encode(jpeg).decode()
    try:
        result = subprocess.run([VISION_PYTHON, str(Path(__file__).resolve())],
                                input=json.dumps(payload), text=True, capture_output=True,
                                timeout=18, check=False)
        if result.returncode:
            raise ValueError('視覺處理程序失敗：'+result.stderr[-300:])
        response = json.loads(result.stdout)
        if 'error' in response: raise ValueError(response['error'])
        return response
    except subprocess.TimeoutExpired:
        raise ValueError('視覺處理逾時，請重新選取影格')


def process(data):
    operation = data['operation']
    meta = data['meta']
    raw = base64.b64decode(data['depth'], validate=True)
    if len(raw) != 640*480*2: raise ValueError('深度資料尺寸不正確')
    if operation == 'point':
        import numpy as np
        import pyrealsense2 as rs
        x,y = data['x'], data['y']
        if type(x) is not int or type(y) is not int or not 0 <= x < 640 or not 0 <= y < 480:
            raise ValueError('選點超出影像範圍')
        depth = np.frombuffer(raw,dtype='<u2').reshape(480,640)
        patch = depth[max(0,y-1):y+2,max(0,x-1):x+2]
        good = patch[patch>0]
        if len(good)<5: raise ValueError('該點附近缺少足夠有效深度，請另選位置')
        distance = float(np.median(good))*meta['scale']
        intr = rs.intrinsics()
        for key in ('width','height','fx','fy','ppx','ppy','coeffs'): setattr(intr,key,meta[key])
        intr.model = rs.distortion(meta['model'])
        xyz = rs.rs2_deproject_pixel_to_point(intr,[x,y],distance)
        return {'pixel':[x,y], 'camera_xyz_m':xyz, 'depth_m':distance,
                'frame':meta['published'], 'updated':meta['updated'],
                'coordinate_system':'camera optical (x right, y down, z forward)', 'robot_target':None}
    from vision_workspace import VisionWorkspace
    # root is supplied only by the local HTTP process, not accepted from an API field.
    vision = VisionWorkspace(data['root'])
    jpeg = base64.b64decode(data['jpeg'], validate=True)
    if operation == 'sample':
        return vision.save_sample(meta,jpeg,raw,data['box'],data['robot'])
    if operation == 'detect':
        return vision.detect(meta,jpeg,raw)
    raise ValueError('未知的視覺操作')


if __name__ == '__main__':
    try:
        text = sys.stdin.read(4_000_001)
        if len(text)>4_000_000: raise ValueError('視覺請求過大')
        print(json.dumps(process(json.loads(text)),ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False))
