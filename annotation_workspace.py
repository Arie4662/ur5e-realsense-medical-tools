"""Local, human-reviewed object annotations; never imports robot/camera code."""
import copy
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import threading
import time
import uuid

CLASSES = [
    {'id': 0, 'name': '圓頭手術剪', 'slug': 'round_tip_scissors'},
    {'id': 1, 'name': '彎止血鉗', 'slug': 'curved_hemostat'},
    {'id': 2, 'name': '直止血鉗', 'slug': 'straight_hemostat'},
    {'id': 3, 'name': '短鑷子', 'slug': 'short_forceps'},
    {'id': 4, 'name': '長鑷子', 'slug': 'long_forceps'},
]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
    finally:
        if temp.exists(): temp.unlink()


def jpeg_size(raw):
    """Read JPEG SOF dimensions without loading a platform image library."""
    if raw[:2] != b'\xff\xd8': raise ValueError('不是 JPEG 圖片')
    at = 2
    while at < len(raw):
        if raw[at] != 255: raise ValueError('無效 JPEG 標記')
        while at < len(raw) and raw[at] == 255: at += 1
        marker = raw[at]; at += 1
        if marker in (0xD9, 0xDA): break
        if marker in (0x01, *range(0xD0, 0xD9)): continue
        size = struct.unpack('>H', raw[at:at+2])[0]
        if size < 2 or at + size > len(raw): break
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack('>HH', raw[at+3:at+7])
            if width and height: return width, height
        at += size
    raise ValueError('無法讀取 JPEG 尺寸')


class AnnotationWorkspace:
    def __init__(self, source, root):
        self.source = Path(source).resolve(strict=True)
        self.root = Path(root).resolve()
        if self.source == self.root or self.source in self.root.parents:
            raise ValueError('標註輸出不可放進原始資料集')
        self.lock = threading.RLock()
        self.entries = {}
        for category in CLASSES:
            folder = self.source / category['name']
            if not folder.is_dir(): continue
            for path in sorted(folder.glob('*.jpg')):
                if not path.is_file() or self.source not in path.resolve().parents:
                    raise ValueError('圖片路徑離開資料集')
                relative = path.relative_to(self.source).as_posix()
                image_id = hashlib.sha256(relative.encode()).hexdigest()[:24]
                raw = path.read_bytes()
                width, height = jpeg_size(raw)
                self.entries[image_id] = dict(id=image_id, file=relative, source_class=category['id'],
                    width=width, height=height, sha256=hashlib.sha256(raw).hexdigest())
        if not self.entries: raise ValueError('資料集內沒有支援的 JPG 圖片')
        self.root.mkdir(parents=True, exist_ok=True)
        project = self.root/'project.json'
        identity = {'source': str(self.source), 'classes': CLASSES, 'schema': 1}
        if project.exists() and json.loads(project.read_text()) != identity:
            raise ValueError('此標註資料夾屬於另一個資料集或類別版本')
        if not project.exists(): write_json(project, identity)
        self.annotations = {}
        self.proposal_cache = {}
        for image_id in self.entries:
            path = self.root/'annotations'/(image_id+'.json')
            if path.exists():
                value = json.loads(path.read_text())
                if value['sha256'] != self.entries[image_id]['sha256']:
                    raise ValueError('已標註圖片已被更換：'+self.entries[image_id]['file'])
                self.annotations[image_id] = value

    def entry(self, image_id):
        if not isinstance(image_id, str) or image_id not in self.entries:
            raise ValueError('未知圖片')
        return self.entries[image_id]

    def image(self, image_id):
        entry = self.entry(image_id)
        path = (self.source/entry['file']).resolve(strict=True)
        if self.source not in path.parents: raise ValueError('圖片路徑離開資料集')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError('原始圖片已變更，請重新啟動標註服務並檢查標註')
        return raw

    def annotation(self, image_id):
        entry = self.entry(image_id)
        with self.lock:
            # Read the saved revision from disk so restarted servers/other tabs
            # cannot silently overwrite a newer human annotation.
            saved_path = self.root/'annotations'/(image_id+'.json')
            if saved_path.exists():
                saved = json.loads(saved_path.read_text())
                if saved['sha256'] != entry['sha256']: raise ValueError('原圖與標註版本不符')
                self.annotations[image_id] = saved
                return copy.deepcopy(saved)
            value = dict(id=image_id,
                sha256=entry['sha256'], revision=0, status='unreviewed', split='train',
                boxes=[], negative=False, note='', updated=None)
            proposal = self.proposal(image_id)
            if proposal:
                value.update(boxes=proposal['boxes'], status='auto_pending' if proposal['boxes'] else 'needs_manual',
                             provenance='automatic', suggestion={k:proposal[k] for k in
                                ('method','run_id','priority','flags','class_basis')})
            return copy.deepcopy(value)

    def proposal(self, image_id):
        entry = self.entry(image_id)
        path = self.root/'proposals'/(image_id+'.json')
        if not path.exists(): return None
        stamp = path.stat().st_mtime_ns
        cached = self.proposal_cache.get(image_id)
        if cached and cached[0] == stamp: return cached[1]
        value = json.loads(path.read_text())
        if value.get('schema') != 1 or value.get('id') != image_id or value.get('sha256') != entry['sha256']:
            return None
        boxes = value.get('boxes')
        if not isinstance(boxes,list) or len(boxes)>100: return None
        for box in boxes:
            xy = box.get('xyxy',[])
            if (type(box.get('class_id')) is not int or not 0 <= box['class_id'] < len(CLASSES)
                    or len(xy)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in xy)):
                return None
            if not (0 <= xy[0] < xy[2] <= entry['width'] and 0 <= xy[1] < xy[3] <= entry['height']):
                return None
        if any(k not in value for k in ('method','run_id','priority','flags','class_basis')): return None
        self.proposal_cache[image_id] = (stamp,value)
        return value

    def catalog(self):
        with self.lock:
            items = []
            for entry in self.entries.values():
                a = self.annotation(entry['id'])
                items.append({**entry, 'status': a['status'], 'split': a['split'],
                              'box_count': len(a['boxes']), 'revision': a['revision'],
                              'priority': a.get('suggestion',{}).get('priority','normal')})
            counts = {state: sum(x['status'] == state for x in items)
                      for state in ('unreviewed', 'draft', 'reviewed', 'skipped', 'auto_pending', 'needs_manual')}
            return {'source': str(self.source), 'output': str(self.root), 'classes': CLASSES,
                    'items': items, 'counts': counts}

    def save(self, data):
        entry = self.entry(data.get('id'))
        self.image(entry['id'])  # Refuse to attach old coordinates to a changed image.
        boxes = data.get('boxes')
        if not isinstance(boxes, list) or len(boxes) > 100: raise ValueError('框選數量無效')
        checked = []
        for box in boxes:
            if not isinstance(box, dict): raise ValueError('框選格式無效')
            cls = box.get('class_id')
            if type(cls) is not int or not 0 <= cls < len(CLASSES): raise ValueError('類別無效')
            coords = box.get('xyxy')
            if (not isinstance(coords, list) or len(coords) != 4 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in coords)):
                raise ValueError('座標必須是四個有限數值')
            x1, y1, x2, y2 = coords
            if not (0 <= x1 < x2 <= entry['width'] and 0 <= y1 < y2 <= entry['height']
                    and x2-x1 >= 2 and y2-y1 >= 2):
                raise ValueError('框選超出圖片或小於 2 像素')
            checked.append({'class_id': cls, 'xyxy': [round(v, 3) for v in coords]})
        status = data.get('status')
        if status not in ('draft', 'reviewed', 'skipped'): raise ValueError('標註狀態無效')
        split = data.get('split')
        if split not in ('train', 'val', 'test'): raise ValueError('用途無效')
        negative = data.get('negative') is True
        if negative and checked: raise ValueError('負樣本不能含有器械框')
        if status == 'reviewed':
            if data.get('confirmed') is not True: raise ValueError('請先核對全部器械後再確認完成')
            if not checked and not negative: raise ValueError('沒有框選，請明確確認是否為負樣本')
        note = data.get('note', '')
        if not isinstance(note, str) or len(note) > 1000: raise ValueError('備註最多 1000 字')
        with self.lock, (self.root/'.write.lock').open('a') as file_lock:
            fcntl.flock(file_lock,fcntl.LOCK_EX)
            old = self.annotation(entry['id'])
            if type(data.get('revision')) is not int or data['revision'] != old['revision']:
                raise ValueError('另一個頁面已更新此圖片。請重新載入，避免覆蓋新標註。')
            value = dict(id=entry['id'], sha256=entry['sha256'], revision=old['revision']+1,
                         status=status, split=split, boxes=checked, negative=negative,
                         note=note, updated=time.time(), provenance='human', schema=1)
            if old.get('suggestion'): value['suggestion'] = old['suggestion']
            # Keep a recoverable history before replacing the current annotation.
            history = self.root/'history'/entry['id']/('%06d-%s.json' % (old['revision'], uuid.uuid4().hex))
            write_json(history, old)
            write_json(self.root/'annotations'/(entry['id']+'.json'), value)
            self.annotations[entry['id']] = value
            return copy.deepcopy(value)

    def export(self, mode):
        if mode not in ('all', 'hemostat'): raise ValueError('匯出類型無效')
        with self.lock:
            current = [self.annotation(image_id) for image_id in self.entries]
            selected = [a for a in current if a['status'] == 'reviewed']
        if not selected: raise ValueError('尚無已確認完成的標註可匯出')
        export_id = time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        final = self.root/'exports'/export_id
        staging = final.with_name('.'+export_id)
        names = [c['slug'] for c in CLASSES] if mode == 'all' else ['hemostat']
        counts = dict(train=0, val=0, test=0)
        box_count = 0
        manifest = []
        try:
            for split in counts:
                (staging/'images'/split).mkdir(parents=True)
                (staging/'labels'/split).mkdir(parents=True)
            for a in sorted(selected, key=lambda x: x['id']):
                e = self.entry(a['id'])
                raw = self.image(a['id'])
                stem = CLASSES[e['source_class']]['slug']+'_'+Path(e['file']).stem+'_'+a['id'][:8]
                lines = []
                for box in a['boxes']:
                    cls = box['class_id']
                    if mode == 'hemostat' and cls not in (1, 2): continue
                    x1, y1, x2, y2 = box['xyxy']; w, h = e['width'], e['height']
                    lines.append('%d %.8f %.8f %.8f %.8f' % (0 if mode == 'hemostat' else cls,
                                 (x1+x2)/2/w, (y1+y2)/2/h, (x2-x1)/w, (y2-y1)/h))
                (staging/'images'/a['split']/(stem+'.jpg')).write_bytes(raw)
                (staging/'labels'/a['split']/(stem+'.txt')).write_text('\n'.join(lines)+('\n' if lines else ''))
                counts[a['split']] += 1; box_count += len(lines)
                manifest.append({'image': stem+'.jpg', 'source': e['file'], 'sha256': e['sha256'],
                                 'annotation': a, 'export_box_count': len(lines)})
            yaml = '# Local YOLO detection export; only human-reviewed images.\n'
            yaml += 'path: '+json.dumps(str(final), ensure_ascii=False)+'\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n'
            yaml += ''.join('  %d: %s\n' % (i, name) for i, name in enumerate(names))
            (staging/'data.yaml').write_text(yaml, encoding='utf-8')
            write_json(staging/'manifest.json', {'mode':mode, 'classes':names, 'source':str(self.source),
                       'counts':counts, 'images':manifest, 'created':time.time()})
            (staging/'README.txt').write_text(
                'Only manually reviewed annotations are included. Draft/skipped images are excluded.\n'
                'Empty label files are reviewed negatives (or no hemostat in hemostat mode).\n'
                'Train/val/test use your explicit per-image choices; no random split was applied.\n'
                'Keep images from the same recording/session together to avoid evaluation leakage.\n'
                'Collect independent validation scenes before judging real-world performance.\n'
                'Fill train and val before training. Moving this export requires updating data.yaml path.\n'
                'These boxes are for object detection, not grasp poses or robot calibration.\n', encoding='utf-8')
            staging.rename(final)
        except Exception:
            if staging.exists(): shutil.rmtree(staging)
            raise
        return {'path': str(final), 'images': len(selected), 'boxes': box_count, 'counts': counts,
                'training_ready': counts['train'] > 0 and counts['val'] > 0 and box_count > 0,
                'mode': mode}
