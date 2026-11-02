import io
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from vision_workspace import VisionWorkspace, validate_box


class VisionTests(unittest.TestCase):
    def test_box_validation(self):
        for box in (None, [0,0,640,481], [-1,0,40,50], [0,0,4,6], [True,0,20,20], [3,4,2,1]):
            with self.assertRaises(ValueError): validate_box(box)
        self.assertEqual(validate_box([0,0,640,480]), [0,0,640,480])

    def test_human_label_saves_exact_frame_and_color_only(self):
        with tempfile.TemporaryDirectory() as folder:
            vision = VisionWorkspace(folder)
            image = Image.new('RGB', (1280,480), 'red')
            image.paste('blue',(640,0,1280,480))
            out = io.BytesIO(); image.save(out,format='JPEG')
            raw = b'\x01\x00'*(640*480)
            result = vision.save_sample({'published':42,'session':'test'}, out.getvalue(), raw,
                                        [100,100,200,300], {'armed':False})
            path = Path(result['path'])
            with Image.open(path/'color.jpg') as saved:
                self.assertEqual(saved.size,(640,480))
            self.assertEqual((path/'depth_z16.bin').read_bytes(),raw)
            label = json.loads((path/'annotation.json').read_text())
            self.assertEqual(label['frame'],42)
            self.assertEqual(label['source'],'human_box')
            self.assertFalse(label['robot_pose_synchronized'])
            self.assertEqual(vision.status()['sample_count'],1)
            self.assertTrue((path/'labels.txt').read_text().startswith('0 '))

    def test_absent_model_is_not_claimed_as_detection_or_executable_grasp(self):
        with tempfile.TemporaryDirectory() as folder:
            vision = VisionWorkspace(folder)
            self.assertFalse(vision.status()['model_ready'])
            with self.assertRaises(ValueError): vision.detect({}, b'', b'')
            plan = vision.grasp_preview({'published':1},None)
            self.assertFalse(plan['ready'])
            self.assertFalse(plan['executed'])
            self.assertIsNone(plan['robot_target'])
            self.assertGreater(len(plan['blockers']),3)

    def test_model_path_is_restricted(self):
        with tempfile.TemporaryDirectory() as folder:
            vision = VisionWorkspace(folder)
            vision.manifest.parent.mkdir()
            for name in ('../robot_bridge.py', 'unknown.pt', '/tmp/model.onnx'):
                vision.manifest.write_text(json.dumps({'file':name}))
                with self.assertRaises(ValueError): vision.config()


if __name__ == '__main__': unittest.main()
