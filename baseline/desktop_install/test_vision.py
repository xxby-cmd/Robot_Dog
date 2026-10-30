"""Camera stride, resize coordinates and model trust boundary contracts."""
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import numpy as np

SOURCE = Path(__file__).resolve().parents[1]/'go2_jazzy'
sys.path.insert(0, str(SOURCE))
import yolo_runtime
import vision_assets


class VisionContracts(unittest.TestCase):
    def test_camera_stride_and_bgr_are_not_misread_as_rgb(self):
        message = types.SimpleNamespace(width=2, height=1, step=8,
            encoding='bgr8', data=bytes([0, 0, 255, 255, 0, 0, 99, 99]))
        self.assertEqual(yolo_runtime.rgb_array(message).tolist(), [[[255,0,0],[0,0,255]]])
        message.data = message.data[:-1]
        with self.assertRaises(ValueError):
            yolo_runtime.rgb_array(message)

    def test_resize_keeps_aspect_and_bottom_padding_has_no_coordinate_offset(self):
        tensor, scales = yolo_runtime.preprocess(np.full((240,320,3),255,np.uint8))
        self.assertEqual(tensor.shape, (1,3,640,640))
        self.assertEqual(tensor.dtype, np.float32)
        self.assertTrue(np.all(tensor[0,:,:480,:] == 1))
        self.assertTrue(np.all(tensor[0,:,480:,:] == 0))
        self.assertEqual(scales, (.5,.5))
        boxes = yolo_runtime.decode(np.array([[[20,40,100,120,.8,0]]]), (320,240), scales, {'0':'person'})
        self.assertEqual(boxes[0]['xyxy'], [10,20,50,60])

    def test_empty_invalid_and_padding_predictions_do_not_make_false_boxes(self):
        output = np.array([[[10,500,80,600,.99,0], [10,20,10,50,.9,0],
                            [0,0,10,10,.99,80], [0,0,10,10,float('nan'),0],
                            [0,0,10,10,.1,0]]])
        self.assertEqual(yolo_runtime.decode(output, (320,240), (.5,.5), {'0':'person'}), [])
        with self.assertRaises(ValueError):
            yolo_runtime.decode(np.zeros((1,84,8400)), (320,240), (.5,.5), {'0':'person'})

    def test_one_to_one_predictions_are_not_run_through_a_second_nms(self):
        output = np.array([[[0,0,40,40,.8,0], [1,1,41,41,.9,0]]])
        boxes = yolo_runtime.decode(output, (100,100), (1,1), {'0':'person'})
        self.assertEqual(len(boxes), 2)
        for value in (-.1, 1.1, float('nan')):
            with self.assertRaises(ValueError):
                yolo_runtime.decode(output, (100,100), (1,1), {'0':'person'}, value)

    def test_same_geometry_keeps_alternatives_but_shows_highest_score(self):
        output=np.array([[[0,0,40,40,.6,0],[0,0,40,40,.9,1]]])
        boxes=yolo_runtime.decode(output,(100,100),(1,1),{'0':'person','1':'chair'})
        self.assertEqual(len(boxes),1)
        self.assertEqual(boxes[0]['label'],'chair')
        self.assertEqual([h['label'] for h in boxes[0]['hypotheses']],['chair','person'])
        clipped=np.array([[[-5,-5,110,110,.8,0],[-10,-20,100,100,.9,1]]])
        self.assertEqual(len(yolo_runtime.decode(clipped,(100,100),(1,1),{'0':'person','1':'chair'})),2)

    def test_wrong_model_download_cannot_replace_previous_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old, incoming = root/'model.onnx', root/'untrusted.onnx'
            old.write_bytes(b'previous asset'); incoming.write_bytes(b'incorrect download')
            with self.assertRaises(ValueError):
                vision_assets.ensure_model(root, incoming)
            self.assertEqual(old.read_bytes(), b'previous asset')
            self.assertFalse((root/'model.onnx.part').exists())

    def test_both_install_paths_include_all_detection_tools(self):
        from seed import TOOLS
        for name in ('yolo_node.py','vision_panel.py','yolo_runtime.py','vision_assets.py',
                     'vision_model.json','vision-requirements.txt','install-vision.sh','start-vision.sh'):
            self.assertIn(name, TOOLS)
            self.assertIn(name, (SOURCE/'provision.sh').read_text(encoding='utf8'))
        spec = json.loads((SOURCE/'vision_model.json').read_text())
        self.assertEqual(len(spec['id2label']), 80)
        self.assertEqual(len(spec['sha256']), 64)
        self.assertIn(spec['revision'], spec['url'])

    def test_offline_part_file_is_never_removed_as_our_staging_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'model.onnx.part';source.write_bytes(b'verified fixture')
            spec={'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'bytes':source.stat().st_size}
            with patch.object(vision_assets.json,'loads',return_value=spec):
                result=vision_assets.ensure_model(root,source)
            self.assertEqual(result.read_bytes(),source.read_bytes())
            self.assertTrue(source.exists())
            self.assertEqual(list(root.glob('.model-*.part')),[])


if __name__ == '__main__':
    unittest.main()
