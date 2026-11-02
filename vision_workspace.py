"""Local hemostat dataset and optional ONNX detection. Never sends robot commands."""
import io
import json
import math
import threading
import time
import uuid
from pathlib import Path


def validate_box(box):
    if (not isinstance(box, list) or len(box) != 4
            or any(type(v) is not int for v in box)):
        raise ValueError('請先拖曳框選止血鉗')
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= 640 and 0 <= y1 < y2 <= 480
            and x2-x1 >= 5 and y2-y1 >= 5):
        raise ValueError('框選範圍無效或太小')
    return box


class VisionWorkspace:
    def __init__(self, root):
        self.root = Path(root)
        self.dataset = self.root/'vision_data'/'hemostat'
        self.manifest = self.root/'models'/'hemostat.json'
        self.lock = threading.Lock()
        self.net = None
        self.model_stamp = None

    def config(self):
        if not self.manifest.exists():
            return None
        config = json.loads(self.manifest.read_text())
        # Restrict the loader to a local, declarative ONNX model, never .pt pickle.
        name = config.get('file', '')
        if not isinstance(name, str) or Path(name).name != name or not name.endswith('.onnx'):
            raise ValueError('模型必須是 models 目錄內的 ONNX 檔案')
        if config.get('format') != 'yolov8-detect' or config.get('class_names') != ['hemostat']:
            raise ValueError('目前支援單類別 hemostat 的 YOLOv8 detection ONNX')
        if config.get('input_size') != 640:
            raise ValueError('模型輸入尺寸須為 640')
        if not (self.manifest.parent/name).is_file():
            raise ValueError('找不到設定中的 ONNX 模型')
        return config

    def status(self):
        try:
            config = self.config()
            model_message = '模型已載入設定，辨識結果仍需實物驗證' if config else '尚未提供止血鉗辨識模型'
        except (OSError, ValueError) as exc:
            config, model_message = None, str(exc)
        return {'target': '止血鉗', 'model_ready': config is not None,
                'model_message': model_message,
                'sample_count': len(list(self.dataset.glob('*/annotation.json'))),
                'calibrated': False, 'auto_grasp_ready': False,
                'blockers': ['手眼校正（相機 → TCP）', 'TCP／夾爪幾何與夾持參數',
                             '止血鉗抓取姿態與碰撞路徑驗證']}

    def save_sample(self, meta, jpeg, raw_depth, box, robot):
        from PIL import Image
        box = validate_box(box)
        sample = time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        folder = self.dataset/sample
        folder.mkdir(parents=True)
        # The displayed JPEG has color on its left and aligned depth on its right.
        Image.open(io.BytesIO(jpeg)).crop((0, 0, 640, 480)).save(folder/'color.jpg', quality=95)
        (folder/'depth_z16.bin').write_bytes(raw_depth)
        (folder/'camera.json').write_text(json.dumps(meta, indent=2))
        x1, y1, x2, y2 = box
        (folder/'labels.txt').write_text('0 %.8f %.8f %.8f %.8f\n' %
                                      ((x1+x2)/1280, (y1+y2)/960, (x2-x1)/640, (y2-y1)/480))
        annotation = {'label': 'hemostat', 'label_zh': '止血鉗', 'source': 'human_box',
                      'box_xyxy': box, 'frame': meta['published'], 'saved_at': time.time(),
                      'robot_snapshot': robot, 'robot_pose_synchronized': False,
                      'note': '人工標註樣本；不是辨識結果或手眼校正樣本'}
        (folder/'annotation.json').write_text(json.dumps(annotation, ensure_ascii=False, indent=2))
        return {'ok': True, 'sample': sample, 'path': str(folder), 'message': '已儲存止血鉗人工框選、彩色與對齊深度'}

    def detect(self, meta, jpeg, raw_depth):
        import cv2
        import numpy as np
        config = self.config()
        if config is None:
            raise ValueError('尚未提供止血鉗模型；可先拖曳框選並儲存訓練樣本')
        if not self.lock.acquire(blocking=False):
            raise ValueError('辨識處理中，請稍候')
        try:
            path = self.manifest.parent/config['file']
            stamp = (str(path), path.stat().st_mtime_ns)
            if self.model_stamp != stamp:
                self.net = cv2.dnn.readNetFromONNX(str(path))
                self.model_stamp = stamp
            rgb = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)[:, :640]
            # Letterbox 640x480 to 640x640 without changing pixel scale.
            padded = cv2.copyMakeBorder(rgb, 80, 80, 0, 0, cv2.BORDER_CONSTANT, value=(114,114,114))
            self.net.setInput(cv2.dnn.blobFromImage(padded, 1/255, (640,640), swapRB=True))
            output = np.asarray(self.net.forward())
            if output.ndim != 3 or output.shape[0] != 1:
                raise ValueError('不支援此模型輸出；需要未內建 NMS 的 YOLOv8 detection')
            rows = output[0]
            if rows.shape[0] == 5: rows = rows.T
            if rows.shape[1] != 5: raise ValueError('模型不是單類別止血鉗 detection')
            boxes, scores = [], []
            for cx, cy, width, height, score in rows:
                if not np.isfinite([cx,cy,width,height,score]).all() or score < .5:
                    continue
                x1, y1 = max(0, int(cx-width/2)), max(0, int(cy-height/2-80))
                x2, y2 = min(640, int(cx+width/2)), min(480, int(cy+height/2-80))
                if x2-x1 < 5 or y2-y1 < 5: continue
                boxes.append([x1,y1,x2-x1,y2-y1]); scores.append(float(score))
            keep = cv2.dnn.NMSBoxes(boxes, scores, .5, .45)
            detections = []
            for idx in np.asarray(keep).reshape(-1):
                x,y,w,h = boxes[int(idx)]
                detections.append({'label':'止血鉗', 'confidence':scores[int(idx)],
                                   'box':[x,y,x+w,y+h], 'validated':False})
            return {'frame':meta['published'], 'detections':detections,
                    'message':'模型候選結果，請核對實物；不會觸發移動', 'robot_target':None}
        finally:
            self.lock.release()

    def grasp_preview(self, meta, box):
        if box is not None: validate_box(box)
        status = self.status()
        blockers = list(status['blockers'])
        if not status['model_ready']: blockers.insert(0, '止血鉗辨識模型與實物驗證')
        if box is None: blockers.insert(0, '尚未選取目標')
        return {'ready':False, 'executed':False, 'target':'止血鉗',
                'frame':meta['published'], 'box':box, 'robot_target':None,
                'blockers':blockers, 'message':'尚不能產生可執行的抓取路徑；沒有發送機器人命令'}
