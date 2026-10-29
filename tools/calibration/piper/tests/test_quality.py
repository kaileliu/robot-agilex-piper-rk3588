"""Synthetic geometry checks only; these tests never open hardware or save samples."""
import sys
from pathlib import Path
import unittest

import numpy as np

PIPER = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PIPER), str(PIPER.parent)]
from quality import fit_checked


class CalibrationQualityTest(unittest.TestCase):
    def setUp(self):
        self.image = np.array([(x, y) for y in (100, 300, 500) for x in (200, 500, 800)] +
                              [(350, 200), (650, 400), (700, 200)], dtype=float)
        self.robot = self.image * 0.0005 + [0.05, -0.15]
        self.expected = np.array([[0.0005, 0, 0.05], [0, 0.0005, -0.15], [0, 0, 1]])

    def samples(self):
        return [{"xyz_m": [*p, 0.135], "tilt_from_down_deg": 0.1,
                 "sdk_fk_difference_mm": 0.2} for p in self.robot]

    def test_holdout_is_not_fitted(self):
        self.robot[-3:, 0] += 0.003
        H, _, _, quality = fit_checked(self.image, self.robot, self.samples())
        np.testing.assert_allclose(H, self.expected, atol=1e-6)
        np.testing.assert_allclose(quality["held_out_errors_mm"], [3, 3, 3], atol=0.01)
        self.assertFalse(quality["deployment_updated"])

    def test_wrong_training_correspondence_rejected(self):
        self.robot[4] += [0.08, -0.04]
        with self.assertRaisesRegex(ValueError, "拟合误差"):
            fit_checked(self.image, self.robot, self.samples())

    def test_wrong_independent_point_rejected(self):
        self.robot[-1] += [0.02, 0]
        with self.assertRaisesRegex(ValueError, "独立验证未通过"):
            fit_checked(self.image, self.robot, self.samples())

    def test_height_mismatch_rejected(self):
        samples = self.samples()
        samples[2]["xyz_m"][2] += 0.01
        with self.assertRaisesRegex(ValueError, "高度"):
            fit_checked(self.image, self.robot, samples)

    def test_repeated_point_rejected(self):
        self.image[-1] = self.image[0]
        with self.assertRaisesRegex(ValueError, "重复"):
            fit_checked(self.image, self.robot, self.samples())


if __name__ == "__main__":
    unittest.main()
