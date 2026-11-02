"""HTTP integration checks against a temporary server and a fake robot only."""
import io
import json
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from PIL import Image
import realsense_live as app


class FakeRobot:
    def __init__(self,*args): pass
    def start(self): pass
    def close(self): pass
    def revoke(self,*args): pass
    def release(self,*args): pass
    def snapshot(self): return {'armed':False, 'moving_requested':False}
    def arm(self,*args): raise AssertionError('Tests must never arm hardware')
    def hold(self,*args): raise AssertionError('Tests must never request movement')


class ConsoleAPI(unittest.TestCase):
    def test_camera_commands_frozen_frame_and_rejected_grasp(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = root/'runtime'
            with patch.object(app,'ROOT',root), patch.object(app,'DATA',data), patch('robot_bridge.RobotBridge',FakeRobot):
                app.HALT.clear()
                thread = threading.Thread(target=app.serve,args=(0,),daemon=True)
                thread.start()
                try:
                    for _ in range(100):
                        if (data/'runtime.json').exists(): break
                        time.sleep(.01)
                    runtime = json.loads((data/'runtime.json').read_text())
                    url = runtime['url']; origin = url.rstrip('/')
                    html = urlopen(url).read().decode()
                    token = re.search("TOKEN='([^']+)'",html).group(1)
                    def post(path, payload=None, bad_origin=False):
                        body = {'client':'test-client', **(payload or {})}
                        req = Request(origin+path, data=json.dumps(body).encode(),
                                      headers={'Origin':'http://foreign.invalid' if bad_origin else origin,
                                               'X-Control-Token':token, 'Content-Type':'application/json'})
                        try:
                            with urlopen(req) as response: return response.status,json.loads(response.read())
                        except HTTPError as exc:
                            raw=exc.read(); exc.close()
                            return exc.code, json.loads(raw) if raw.startswith(b'{') else {}
                    self.assertEqual(post('/camera/start',bad_origin=True)[0],403)
                    self.assertEqual(post('/camera/start')[0],409)
                    (data/'worker.json').write_text(json.dumps({'version':app.WORKER_VERSION,'updated':time.time()}))
                    self.assertEqual(post('/camera/start')[0],200)
                    self.assertEqual(app.camera_command(runtime['session'])['action'],'start')
                    self.assertEqual(post('/camera/stop')[0],200)
                    self.assertEqual(app.camera_command(runtime['session'])['action'],'stop')
                    meta={'published':77,'session':runtime['session'],'updated':time.time(),
                          'scale':.001,'width':640,'height':480,'fx':600,'fy':600,
                          'ppx':320,'ppy':240,'model':0,'coeffs':[0]*5}
                    (data/'meta-77.json').write_text(json.dumps(meta))
                    (data/'depth-77.bin').write_bytes(b'\xe8\x03'*(640*480))
                    out=io.BytesIO();Image.new('RGB',(1280,480)).save(out,format='JPEG')
                    (data/'frame-77.jpg').write_bytes(out.getvalue())
                    app.report({'session':runtime['session'],'phase':'live','updated':time.time(),'published':77})
                    code, frozen = post('/vision/freeze',{'frame':77})
                    self.assertEqual(code,200)
                    for filename in ('meta-77.json','depth-77.bin','frame-77.jpg'): (data/filename).unlink()
                    # The exact frozen depth survives normal preview ring eviction.
                    payload={'frame':77,'freeze':frozen['freeze'],'x':320,'y':240,'box':[10,10,40,40]}
                    code,point=post('/vision/point',payload)
                    self.assertEqual(code,200)
                    self.assertEqual(point['camera_xyz_m'],[0.0,0.0,1.0])
                    self.assertIsNone(point['robot_target'])
                    self.assertEqual(post('/vision/sample',payload)[0],200)
                    self.assertEqual(post('/vision/detect',payload)[0],409)
                    code,plan=post('/grasp/preview',payload)
                    self.assertEqual(code,200)
                    self.assertFalse(plan['ready']);self.assertFalse(plan['executed'])
                    self.assertEqual(post('/vision/point',{**payload,'freeze':'wrong'})[0],409)
                    self.assertEqual(post('/robot/arm',{'displayed_at':0})[0],409)
                    self.assertEqual(post('/vision/resume')[0],200)
                    self.assertEqual(post('/vision/point',payload)[0],409)
                finally:
                    app.HALT.set();thread.join(timeout=3);app.HALT.clear()
                self.assertFalse(thread.is_alive())


if __name__ == '__main__': unittest.main()
