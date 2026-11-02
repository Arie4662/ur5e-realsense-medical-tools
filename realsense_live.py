#!/usr/local/bin/python3
"""Local-only RealSense viewer. HTTP runs unprivileged; USB capture runs separately."""
import argparse
import fcntl
import gc
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import secrets
import hmac
import uuid

ROOT = Path(__file__).resolve().parent
DATA = ROOT / '.realsense_live'
PAGE = ROOT / 'realsense_live.html'
HALT = threading.Event()
WORKER_VERSION = 2
CAPTURE_STALL_SECONDS = 12


def atomic(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(data)
    os.chmod(tmp, 0o644)
    tmp.replace(path)


def report(state):
    atomic(DATA / 'status.json', json.dumps(state, ensure_ascii=False).encode())


def camera_status(session):
    status = {'phase': 'waiting', 'message': '等待相機讀取程序', 'fresh': False}
    try:
        candidate = json.loads((DATA / 'status.json').read_text())
        if candidate.get('session') == session:
            status = candidate
            status['age_seconds'] = max(0, time.time()-status.get('updated', 0))
            status['fresh'] = status.get('phase') == 'live' and status['age_seconds'] < 1.0
            if status.get('phase') == 'live' and not status['fresh']:
                status.update(phase='stale', message='未收到新影像，已停用移動')
    except (OSError, ValueError):
        pass
    try:
        worker = json.loads((DATA/'worker.json').read_text())
        status['worker_ready'] = (worker.get('version') == WORKER_VERSION
                                  and 0 <= time.time()-worker['updated'] < 3)
    except (OSError, ValueError, KeyError):
        status['worker_ready'] = False
    return status


def camera_command(session):
    try:
        command = json.loads((DATA/'camera-command.json').read_text())
        if command.get('session') == session and command.get('action') in ('start', 'stop'):
            return command
    except (OSError, ValueError):
        pass
    return {'action': 'stop', 'id': None}


def worker_report(session):
    atomic(DATA/'worker.json', json.dumps({'version': WORKER_VERSION, 'session': session,
                                         'updated': time.time(), 'pid': os.getpid()}).encode())


def capture(managed=False):
    import numpy as np
    import pyrealsense2 as rs
    from PIL import Image
    runtime = json.loads((DATA / 'runtime.json').read_text())
    session = runtime['session']
    lock = (DATA / 'capture.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('已有相機讀取程序，請先在該終端機按 Control+C。')
    for pattern in ('frame-*.jpg', 'depth-*.bin', 'meta-*.json'):
        for cached in DATA.glob(pattern):
            cached.unlink()
    # Persist a bounded ring of aligned frames, allowing a clicked frozen frame to
    # be matched with its exact depth map, not a later frame.
    sdk_log = DATA / ('sdk-%s.log' % time.strftime('%Y%m%d-%H%M%S'))
    rs.log_to_file(rs.log_severity.warn, str(sdk_log))
    print('SDK 診斷日誌：%s；執行 UID：%d' % (sdk_log, os.geteuid()), flush=True)
    state = {'session': session, 'phase': 'starting', 'message': '正在開啟相機',
             'updated': time.time(), 'sdk_log': str(sdk_log), 'run_uid': os.geteuid()}
    published = time.time_ns() // 1000000
    failures = 0
    try:
        while not HALT.is_set():
            if json.loads((DATA/'runtime.json').read_text())['session'] != session:
                break
            if managed:
                if camera_command(session)['action'] != 'start':
                    break
            pipe = profile = conf = device = devices = ctx = None
            align = colorizer = frames = depth = color = rgb = None
            started = False
            stage = 'enumerate'
            try:
                state.update(phase='starting', message='正在開啟相機', updated=time.time())
                report(state)
                ctx = rs.context()
                devices = ctx.query_devices()
                if len(devices) != 1:
                    raise RuntimeError('SDK 偵測到 %d 台相機，請檢查 USB 連線' % len(devices))
                stage = 'open_device'
                device = devices[0]
                serial = device.get_info(rs.camera_info.serial_number)
                stage = 'configure'
                conf = rs.config()
                conf.enable_device(serial)
                # Lower frame rate after a failure to reduce USB bandwidth.
                fps = 30 if failures == 0 else 15
                conf.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, fps)
                conf.enable_stream(rs.stream.color, 640, 480, rs.format.rgb8, fps)
                pipe = rs.pipeline(ctx)
                stage = 'start_stream'
                profile = pipe.start(conf)
                started = True
                align = rs.align(rs.stream.color)
                colorizer = rs.colorizer()
                for key, value in ((rs.option.histogram_equalization_enabled, 0),
                                   (rs.option.min_distance, .2), (rs.option.max_distance, 8),
                                   (rs.option.color_scheme, 0)):
                    colorizer.set_option(key, value)
                scale = profile.get_device().first_depth_sensor().get_depth_scale()
                state.update(name=device.get_info(rs.camera_info.name), serial=serial,
                             resolution=[640, 480], depth_scale_m=scale, requested_fps=fps)
                print('相機已開啟：%s，%d fps；等待影像。Control+C 停止。' % (serial, fps), flush=True)
                stage = 'receive_frames'
                last_publish = 0
                last_color = last_depth = None
                nc = nd = 0
                rate_start = time.monotonic()
                cfps = dfps = 0
                no_frame_since = time.monotonic()
                while not HALT.is_set():
                    if managed:
                        if camera_command(session)['action'] != 'start':
                            break
                    if json.loads((DATA/'runtime.json').read_text())['session'] != session:
                        break
                    ok, frames = pipe.try_wait_for_frames(1000)
                    if not ok:
                        if time.monotonic()-no_frame_since > 8:
                            raise RuntimeError('8 秒內沒有新影像，將重新開啟串流')
                        continue
                    frames = align.process(frames)
                    depth, color = frames.get_depth_frame(), frames.get_color_frame()
                    if not depth or not color:
                        if time.monotonic()-no_frame_since > 8:
                            raise RuntimeError('沒有完整的彩色與深度影像')
                        continue
                    cn, dn = color.get_frame_number(), depth.get_frame_number()
                    # Both channels must advance: repeated depth cannot keep jogging enabled.
                    new_color, new_depth = cn != last_color, dn != last_depth
                    nc += int(new_color); nd += int(new_depth)
                    last_color, last_depth = cn, dn
                    if not (new_color and new_depth):
                        if time.monotonic()-no_frame_since > 8:
                            raise RuntimeError('彩色或深度影像停止更新')
                        continue
                    no_frame_since = time.monotonic()
                    now = time.monotonic()
                    if now-rate_start >= 1:
                        cfps, dfps = nc/(now-rate_start), nd/(now-rate_start)
                        nc = nd = 0; rate_start = now
                    if now-last_publish < .1:
                        continue
                    rgb = np.asanyarray(color.get_data())
                    z = np.asanyarray(depth.get_data()).copy()
                    colored = np.asanyarray(colorizer.colorize(depth).get_data()).copy()
                    colored[z == 0] = 0
                    buf = io.BytesIO()
                    Image.fromarray(np.concatenate((rgb, colored), axis=1), 'RGB').save(buf, format='JPEG', quality=85)
                    published += 1
                    slot = published
                    intr = depth.profile.as_video_stream_profile().intrinsics
                    meta = {'published': published, 'session': session, 'updated': time.time(),
                            'scale': scale, 'fx': intr.fx, 'fy': intr.fy, 'ppx': intr.ppx,
                            'ppy': intr.ppy, 'model': int(intr.model), 'coeffs': intr.coeffs,
                            'width': 640, 'height': 480}
                    atomic(DATA/('depth-%d.bin' % slot), z.astype('<u2').tobytes())
                    atomic(DATA/('frame-%d.jpg' % slot), buf.getvalue())
                    atomic(DATA/('meta-%d.json' % slot), json.dumps(meta).encode())
                    state.update(phase='live', message='即時擷取', updated=meta['updated'],
                                 color_frame=cn, depth_frame=dn, published=published,
                                 color_fps=round(cfps, 1), depth_fps=round(dfps, 1),
                                 valid_fraction=float(np.count_nonzero(z)/z.size),
                                 center_depth_m=depth.get_distance(320,240))
                    report(state)
                    for prefix, suffix in (('depth', 'bin'), ('frame', 'jpg'), ('meta', 'json')):
                        old = DATA/('%s-%d.%s' % (prefix, published-32, suffix))
                        if old.exists(): old.unlink()
                    last_publish = now
            except Exception as exc:
                failures += 1
                state.update(phase='error', message=str(exc), stage=stage, updated=time.time())
                report(state)
                print('串流失敗（%d/3，%s）：%s' % (failures, stage, exc), flush=True)
            finally:
                if started:
                    try: pipe.stop()
                    except Exception: pass
                # Drop all SDK owners and frame-backed NumPy views before opening
                # another context; stopping a pipeline alone doesn't release them.
                rgb = frames = depth = color = align = colorizer = None
                profile = pipe = conf = device = devices = ctx = None
                gc.collect()
            if failures >= 3 or HALT.is_set():
                break
            HALT.wait(2)
    finally:
        if state['phase'] != 'error':
            state.update(phase='stopped', message='相機已停止', updated=time.time())
            report(state)
        lock.close()


def camera_worker():
    """One locally authorized helper; the page sends start/stop requests only.

    No shell commands, arbitrary paths, privilege changes or robot actions are
    accepted from the page. A new server session always requires a new start.
    """
    DATA.mkdir(exist_ok=True, mode=0o700)
    worker_lock = (DATA/'worker.lock').open('a')
    try:
        fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('新版背景服務已在執行，請回到網頁操作。')
    last_command = None
    child = None
    child_session = None
    launched_at = 0
    print('RealSense 背景服務就緒。請保留此終端機，在網頁啟動／停止相機。', flush=True)
    try:
        while not HALT.is_set():
            runtime = json.loads((DATA/'runtime.json').read_text())
            session = runtime['session']
            worker_report(session)
            command = camera_command(session)
            key = (session, command.get('id'))
            if child is not None:
                if child.poll() is not None:
                    state = camera_status(child_session)
                    if state.get('phase') != 'error':
                        report({'session':child_session,'phase':'stopped','message':'相機擷取程序已結束',
                                'updated':time.time()})
                    child = None
                elif session != child_session or command['action'] == 'stop':
                    stop_capture_child(child)
                    child = None
                    report({'session':child_session,'phase':'stopped','message':'相機已停止；背景服務仍就緒',
                            'updated':time.time()})
                else:
                    state = camera_status(child_session)
                    # A native SDK call can hold Python's GIL indefinitely. A
                    # separate supervisor process keeps web stop/restart alive.
                    last_progress = max(launched_at, state.get('updated', 0))
                    if time.time()-last_progress > CAPTURE_STALL_SECONDS:
                        stop_capture_child(child)
                        child = None
                        report({'session':child_session,'phase':'error',
                                'message':'相機 SDK 超過 12 秒沒有進度，已關閉擷取程序；可按啟動相機重試',
                                'updated':time.time()})
            if session != 'stopped' and command['action'] == 'start' and key != last_command:
                last_command = key
                if child is None:
                    child_session = session
                    launched_at = time.time()
                    report({'session':session,'phase':'starting','message':'正在啟動相機擷取程序',
                            'updated':launched_at})
                    child = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()),
                                              '--capture-managed'], stdin=subprocess.DEVNULL,
                                             start_new_session=True)
            HALT.wait(.2)
    finally:
        if child is not None:
            stop_capture_child(child)
        atomic(DATA/'worker.json', json.dumps({'version': WORKER_VERSION, 'updated': 0}).encode())
        worker_lock.close()


def stop_capture_child(child):
    """Only stop the worker's own child; never search for or kill other apps."""
    if child.poll() is not None:
        return
    child.send_signal(signal.SIGINT)
    try:
        child.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=2)


def serve(port):
    from robot_bridge import RobotBridge
    from vision_workspace import VisionWorkspace
    from vision_processing import request as process_vision
    DATA.mkdir(exist_ok=True, mode=0o700)
    session = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    robot = RobotBridge(lambda: camera_status(session).get('fresh', False))
    vision = VisionWorkspace(ROOT)
    frozen = {}
    frozen_lock = threading.Lock()

    def frame_data(seq, suffix):
        seq = int(seq)
        state = camera_status(session)
        if not state.get('published') or not state['published']-31 <= seq <= state['published']:
            raise ValueError('畫面已過期，請繼續預覽後重新選點')
        meta = json.loads((DATA/('meta-%d.json' % seq)).read_text())
        if meta['session'] != session or meta['published'] != seq:
            raise ValueError('影像不屬於目前連線')
        return meta, (DATA/('%s-%d.%s' % ('depth' if suffix == 'bin' else 'frame', seq, suffix))).read_bytes()

    def selected_frame(data, suffix):
        if data.get('freeze'):
            with frozen_lock:
                entry = frozen.get(data['client'])
                if (not entry or entry['id'] != data['freeze'] or time.monotonic()-entry['created'] > 300
                        or entry['meta']['published'] != data['frame']):
                    raise ValueError('凍結影格已過期，請繼續預覽後重新選取')
                return entry['meta'], entry['depth' if suffix == 'bin' else 'jpeg']
        return frame_data(data['frame'], suffix)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def allowed_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def respond(self, payload, content_type='application/json; charset=utf-8', code=200):
            if isinstance(payload, dict): payload = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            try: self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError): pass

        def do_GET(self):
            if not self.allowed_host():
                self.send_error(403); return
            parsed = urlparse(self.path)
            route = parsed.path
            try:
                if route == '/':
                    self.respond(PAGE.read_bytes().replace(b'__CONTROL_TOKEN__', token.encode()), 'text/html; charset=utf-8')
                elif route == '/status':
                    status = camera_status(session)
                    status['robot'] = robot.snapshot()
                    status['vision'] = vision.status()
                    self.respond(status)
                elif route == '/frame.jpg':
                    if not camera_status(session).get('fresh'): raise ValueError('沒有新影像')
                    seq = parse_qs(parsed.query)['seq'][0]
                    _, payload = frame_data(seq, 'jpg')
                    self.respond(payload, 'image/jpeg')
                else: self.send_error(404)
            except (ValueError, KeyError, OSError) as exc:
                self.respond({'error': str(exc)}, code=409)

        def do_POST(self):
            origin = self.headers.get('Origin')
            expected = 'http://' + self.headers.get('Host', '')
            if (not self.allowed_host() or origin != expected or
                not hmac.compare_digest(self.headers.get('X-Control-Token', ''), token)):
                self.send_error(403); return
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 2048: raise ValueError('無效請求大小')
                data = json.loads(self.rfile.read(size))
                client = data.get('client')
                if not isinstance(client, str) or not 1 <= len(client) <= 80: raise ValueError('無效控制者')
                if self.path == '/robot/stop':
                    # Stop is accepted from any same-origin authenticated tab.
                    reason=data.get('reason','已停止，請重新啟用')
                    robot.revoke(reason if isinstance(reason,str) and len(reason)<=120 else '已停止')
                elif self.path in ('/camera/start', '/camera/stop'):
                    robot.revoke('相機狀態變更，已解除移動')
                    if not camera_status(session)['worker_ready']:
                        raise ValueError('請先啟動新版 RealSense 背景服務；網頁不會收取管理員密碼')
                    action = self.path.rsplit('/', 1)[1]
                    atomic(DATA/'camera-command.json', json.dumps({'session': session,
                           'action': action, 'id': uuid.uuid4().hex}).encode())
                    self.respond({'ok': True, 'message': '啟動要求已送出' if action == 'start' else '停止要求已送出'})
                    return
                elif self.path in ('/vision/sample', '/vision/detect', '/grasp/preview'):
                    robot.revoke('視覺分析中，已解除移動')
                    meta, jpeg = selected_frame(data, 'jpg')
                    _, depth = selected_frame(data, 'bin')
                    if self.path == '/vision/sample':
                        result = process_vision('sample',meta,depth,jpeg,box=data['box'],
                                                robot=robot.snapshot(),root=str(ROOT))
                    elif self.path == '/vision/detect':
                        result = process_vision('detect',meta,depth,jpeg,root=str(ROOT))
                    else:
                        result = vision.grasp_preview(meta, data.get('box'))
                    self.respond(result)
                    return
                elif self.path == '/vision/freeze':
                    robot.revoke('畫面已凍結，已解除移動')
                    meta, jpeg = frame_data(data['frame'], 'jpg')
                    _, depth = frame_data(data['frame'], 'bin')
                    freeze = uuid.uuid4().hex
                    with frozen_lock:
                        if len(frozen) >= 4 and client not in frozen:
                            oldest = min(frozen, key=lambda key: frozen[key]['created'])
                            del frozen[oldest]
                        frozen[client] = {'id':freeze, 'created':time.monotonic(),
                                          'meta':meta, 'jpeg':jpeg, 'depth':depth}
                    self.respond({'freeze':freeze, 'frame':meta['published']})
                    return
                elif self.path == '/vision/resume':
                    with frozen_lock: frozen.pop(client, None)
                    self.respond({'ok': True})
                    return
                elif self.path in ('/robot/arm', '/robot/start', '/robot/heartbeat'):
                    displayed = float(data.get('displayed_at', 0))
                    if not 0 <= time.time()-displayed < 1:
                        robot.release(client)
                        raise ValueError('畫面未更新，請繼續預覽後再啟用')
                    if self.path == '/robot/arm':
                        robot.arm(client,data.get('mode','joint'),data.get('tcp_confirmed',False),
                                  data.get('tcp_offset'))
                    else:
                        robot.hold(client, data['seq'], data['hold_id'], data['axis'], data['direction'],
                                   self.path == '/robot/start',data.get('mode','joint'))
                elif self.path == '/vision/point':
                    x, y = data['x'], data['y']
                    if type(x) is not int or type(y) is not int or not 0 <= x < 640 or not 0 <= y < 480:
                        raise ValueError('選點超出影像範圍')
                    meta, raw = selected_frame(data, 'bin')
                    self.respond(process_vision('point',meta,raw,x=x,y=y))
                    return
                else:
                    self.send_error(404); return
                self.respond({'ok':True, 'robot':robot.snapshot()})
            except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
                self.respond({'error':str(exc)}, code=409)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.timeout = .2
    url = f'http://127.0.0.1:{server.server_port}/'
    atomic(DATA/'runtime.json', json.dumps({'session':session, 'url':url}).encode())
    atomic(DATA/'camera-command.json', json.dumps({'session':session, 'action':'stop', 'id':uuid.uuid4().hex}).encode())
    robot.start()
    print('CONSOLE_URL='+url, flush=True)
    try:
        while not HALT.is_set(): server.handle_request()
    finally:
        robot.close()
        server.server_close()
        atomic(DATA/'runtime.json', json.dumps({'session':'stopped','url':url}).encode())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--camera-worker', action='store_true')
    parser.add_argument('--capture-managed', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: HALT.set())
    if args.camera_worker:
        camera_worker()
    elif args.capture or args.capture_managed:
        capture(managed=args.capture_managed)
    else:
        serve(args.port)
