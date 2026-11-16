"""Host resource evidence contains numbers only, not raw procfs content."""
import json
import tempfile
import unittest
from pathlib import Path

import host_resources as host


class HostResourcesTests(unittest.TestCase):
    def test_counters_exclude_guest_double_counting_and_never_export_raw_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'pressure').mkdir()
            (root / 'stat').write_text('cpu  10 2 3 40 5 6 7 8 100 200\ncpu0 SECRET\n')
            (root / 'meminfo').write_text('MemAvailable: 123456 kB\nSECRET: sensitive\n')
            (root / 'pressure' / 'cpu').write_text('some avg10=12.34 avg60=5.00 avg300=1.00 total=999\n')
            sample = host.snapshot(root)
        self.assertEqual(sample['cpu_total_ticks'], 81)
        self.assertEqual(sample['cpu_idle_ticks'], 45)
        self.assertEqual(sample['memory_available_kb'], 123456)
        self.assertEqual(sample['cpu_pressure_some_avg10'], 12.34)
        self.assertNotIn('SECRET', json.dumps(sample))
        self.assertTrue(all(type(value) in (int, float) for value in sample.values()))

    def test_invalid_or_nonfinite_counters_fail_without_echoing_procfs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'pressure').mkdir()
            (root / 'stat').write_text('cpu 1 2 3 4 5 6 7 8\n')
            (root / 'meminfo').write_text('MemAvailable: 42 kB\n')
            for pressure in ('some avg10=nan SECRET', 'some avg10=inf SECRET', 'SECRET'):
                (root / 'pressure' / 'cpu').write_text(pressure)
                with self.subTest(pressure=pressure), self.assertRaises(ValueError) as caught:
                    host.snapshot(root)
                self.assertNotIn('SECRET', str(caught.exception))
