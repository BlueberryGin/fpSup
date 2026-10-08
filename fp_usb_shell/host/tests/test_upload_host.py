"""Compile and exercise the actual host upload code with a fake USB worker."""
import pathlib
import subprocess
import tempfile
import unittest


HERE = pathlib.Path(__file__).resolve().parent


class UploadHostTests(unittest.TestCase):
    def test_binary_upload_and_uncertain_ack(self):
        with tempfile.TemporaryDirectory(prefix='fpshd-upload-test-') as tmp:
            binary = pathlib.Path(tmp) / 'upload-harness'
            subprocess.run([
                'cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                '-I', str(HERE / 'ep83_fake_libusb'),
                str(HERE / 'upload_harness.c'), '-lz', '-o', str(binary),
            ], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True,
                                    capture_output=True, text=True, timeout=5)
            self.assertIn('PASS upload host', result.stdout)


if __name__ == '__main__':
    unittest.main()
