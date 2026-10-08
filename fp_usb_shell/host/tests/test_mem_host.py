"""Compile and exercise the actual host MEM1 code with a fake USB worker."""
import pathlib
import subprocess
import tempfile
import unittest


HERE = pathlib.Path(__file__).resolve().parent


class MemHostTests(unittest.TestCase):
    def test_mem1_round_trips_and_refusals(self):
        with tempfile.TemporaryDirectory(prefix='fpshd-mem-test-') as tmp:
            binary = pathlib.Path(tmp) / 'mem-harness'
            subprocess.run([
                'cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-D_DARWIN_C_SOURCE',
                '-I', str(HERE / 'ep83_fake_libusb'),
                str(HERE / 'mem_harness.c'), '-lz', '-o', str(binary),
            ], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True,
                                    timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PASS mem host', result.stdout)


if __name__ == '__main__':
    unittest.main()
