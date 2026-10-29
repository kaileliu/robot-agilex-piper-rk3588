"""Counter semantics and missing telemetry; no dependencies or hardware writes."""
import unittest
from unittest.mock import patch

import resource_monitor as monitor


class ResourceCounterTest(unittest.TestCase):
    def test_guest_time_is_not_counted_twice(self):
        before = [100, 10, 50, 800, 40, 0, 0, 0, 20, 0]
        after = [160, 20, 70, 910, 40, 0, 0, 0, 50, 0]
        self.assertEqual(monitor.cpu_usage(after, before), 45.0)

    def test_initial_and_reset_counters_are_unknown(self):
        self.assertIsNone(monitor.cpu_usage([0] * 10, None))
        self.assertIsNone(monitor.rate(10, 20, 1))
        self.assertIsNone(monitor.rate(20, 10, 0))

    def test_idle_npu_is_distinct_from_unavailable(self):
        self.assertEqual(monitor.parse_npu_load(''), [])
        cores = monitor.parse_npu_load('NPU load: Core0: 0%, Core1: 18%, Core2: 3%,')
        self.assertEqual(cores, [{'id': 0, 'percent': 0.0}, {'id': 1, 'percent': 18.0},
                                 {'id': 2, 'percent': 3.0}])

    def test_disk_sectors_and_parent_device_only(self):
        stats = ('179 0 mmcblk0 1 0 8 20 3 0 16 30 0 500 50\n'
                 '179 6 mmcblk0p6 1 0 8 20 3 0 16 30 0 500 50\n')
        with patch.object(monitor, 'read', return_value=stats):
            self.assertEqual(monitor.io_counters(), {'mmcblk0': (8, 16, 500)})
        self.assertEqual(monitor.rate(28, 8, 2, 512), 5120.0)
        self.assertEqual(monitor.rate(600, 500, 2, .1), 5.0)

    def test_virtual_interfaces_do_not_double_count_network(self):
        fields = '10 0 0 0 0 0 0 0 20 0 0 0 0 0 0 0'
        text = '\n'.join(f'{name}: {fields}' for name in ('eth0', 'lo', 'docker0', 'veth5', 'can0', 'dummy0'))
        with patch.object(monitor, 'read', return_value=text):
            self.assertEqual(monitor.network_counters(), {'eth0': (10, 20)})


if __name__ == '__main__':
    unittest.main()
