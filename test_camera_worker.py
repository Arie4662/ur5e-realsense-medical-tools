import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import realsense_live as app


class HungCapture:
    def __init__(self):
        self.returncode = None
        self.signals = []
        self.killed = False
    def poll(self): return self.returncode
    def send_signal(self, sig): self.signals.append(sig)
    def wait(self, timeout=None):
        if not self.killed: raise subprocess.TimeoutExpired('fake-camera',timeout)
        return self.returncode
    def kill(self): self.killed = True; self.returncode = -9


class CameraWorkerTests(unittest.TestCase):
    def run_supervisor(self, stop_at=None):
        child = HungCapture()
        now = [100.0]
        heartbeats = []
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data/'runtime.json').write_text(json.dumps({'session':'test'}))
            (data/'camera-command.json').write_text(json.dumps({'session':'test','action':'start','id':'one'}))
            class Event:
                step = 0
                def is_set(self): return self.step >= 8
                def wait(self, delay):
                    self.step += 1
                    now[0] += 2
                    heartbeats.append(json.loads((data/'worker.json').read_text())['updated'])
                    if self.step == stop_at:
                        (data/'camera-command.json').write_text(json.dumps({'session':'test','action':'stop','id':'two'}))
            with patch.object(app,'DATA',data), patch.object(app,'HALT',Event()), \
                    patch.object(app.time,'time',lambda:now[0]), \
                    patch.object(app.subprocess,'Popen',return_value=child) as start:
                app.camera_worker()
            result = json.loads((data/'status.json').read_text())
            self.assertEqual(start.call_count,1)  # No automatic retry of a failed command.
            self.assertIn('--capture-managed',start.call_args[0][0])
            self.assertTrue(child.killed)
            self.assertTrue(child.signals)
            self.assertEqual(len(set(heartbeats)),8)
            self.assertEqual(json.loads((data/'worker.json').read_text())['updated'],0)
            return result

    def test_hung_sdk_cannot_block_worker_heartbeat_and_is_terminated(self):
        state = self.run_supervisor()
        self.assertEqual(state['phase'],'error')
        self.assertIn('12 秒',state['message'])

    def test_web_stop_terminates_even_a_hung_sdk_child(self):
        state = self.run_supervisor(stop_at=1)
        self.assertEqual(state['phase'],'stopped')


if __name__ == '__main__': unittest.main()
