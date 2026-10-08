"""Offline tests compile actual fpshd.c against fake USB; no socket/device I/O.

Run: python3 -B fpSup/fp_usb_shell/host/tests/test_ep83_host.py
The fake harness is intentionally not a usable daemon binary.
"""
import pathlib
import subprocess
import tempfile
import unittest


HERE = pathlib.Path(__file__).resolve().parent


class EP83HostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fpshd-ep83-test-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.binary = pathlib.Path(cls.tmp.name) / "ep83-harness"
        subprocess.run([
            "cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
            "-I", str(HERE / "ep83_fake_libusb"),
            str(HERE / "ep83_harness.c"), "-lz", "-o", str(cls.binary),
        ], check=True, capture_output=True, text=True)

    def run_case(self, case):
        result = subprocess.run([str(self.binary), case], check=True,
                                capture_output=True, text=True, timeout=5)
        self.assertIn(f"PASS {case}", result.stdout)


for _case in ("success", "owner_reuse", "arguments", "descriptor", "out_faults", "data_faults",
              "terminal_faults", "deadline", "sequence_exhausted", "preflight_deadline", "bounded_output"):
    setattr(EP83HostTests, f"test_{_case}",
            lambda self, case=_case: self.run_case(case))


if __name__ == "__main__":
    unittest.main(verbosity=2)
