"""Regression tests for geometry/color/NMS that affect the robot's grasp pose."""
import unittest

import numpy as np

from yolo_obb import letterbox_rgb, decode_heads, fast_nms, to_detections


class PostprocessTests(unittest.TestCase):
    def test_rgb_and_odd_padding(self):
        frame = np.full((3, 5, 3), [10, 20, 30], np.uint8)
        rgb, gain, pad = letterbox_rgb(frame, (8, 8))
        self.assertEqual(rgb.shape, (1, 8, 8, 3))
        self.assertEqual(gain, 1.6)
        self.assertEqual(pad, (0, 1))
        np.testing.assert_array_equal(rgb[0, 1, 0], [30, 20, 10])
        np.testing.assert_array_equal(rgb[0, 0, 0], [114, 114, 114])

    def test_dfl_rotation_and_image_coordinates(self):
        # One anchor at (4,4), distances l=1,t=2,r=3,b=4 and angle pi/2.
        meta = {'names': {'0': 'carrot'}, 'reg_max': 16, 'strides': [8], 'input_shape': [8, 8]}
        box = np.full((1, 64, 1, 1), -80, np.float32)
        for side, distance in enumerate([1, 2, 3, 4]):
            box[0, side*16+distance] = 80
        outputs = [box, np.full((1, 1, 1, 1), 8, np.float32), np.full((1, 1, 1, 1), np.log(3), np.float32)]
        predictions = decode_heads(outputs, meta, .8)
        np.testing.assert_allclose(predictions[0, [0, 1, 2, 3, 6]], [-4, 12, 32, 48, np.pi/2], atol=1e-5)
        d = to_detections(predictions, {0: 'carrot'}, 2, (2, 4), .45)[0]
        np.testing.assert_allclose(d['obb'], [-3, 4, 16, 24, np.pi/2], atol=1e-5)
        np.testing.assert_allclose(d['bbox_2d'], [-11, -8, 5, 16, 90], atol=1e-5)

    def test_fast_nms_suppressed_boxes_still_suppress(self):
        # A overlaps B, B overlaps C, A does not overlap C at .45.
        p = np.array([[0, 0, 100, 100, .9, 0, 0],
                      [40, 0, 100, 100, .8, 0, 0],
                      [80, 0, 100, 100, .7, 0, 0]], np.float32)
        np.testing.assert_array_equal(fast_nms(p, .45, block_size=1), [0])
        p[1, 5] = 1
        np.testing.assert_array_equal(fast_nms(p, .45, block_size=1), [0, 1, 2])

    def test_empty_and_zero_iou(self):
        self.assertEqual(len(fast_nms(np.empty((0, 7), np.float32), .45)), 0)
        p = np.array([[0, 0, 100, 100, .9, 0, 0]], np.float32)
        self.assertEqual(len(fast_nms(p, 0)), 0)


if __name__ == '__main__':
    unittest.main()
