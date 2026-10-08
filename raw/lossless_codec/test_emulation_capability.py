#!/usr/bin/env python3
"""The gate is only worth having if it really survives the child dying.

Each test replaces the self-check child with a script that fails a specific
way, so the gate is exercised by an actual process death rather than by a
mock returning a number. The SIGILL case is the one that matters: it is the
failure that produced a bare exit 132 with no traceback, and a gate that
merely LOOKS like it would catch a signal is worth nothing.
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import emulation_capability as capability


class FakeChildTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.original = capability.EMULATOR
        self.addCleanup(setattr, capability, "EMULATOR", self.original)
        capability._CACHED = {}
        self.addCleanup(setattr, capability, "_CACHED", {})

    def child(self, body):
        path = pathlib.Path(self.dir.name) / "fake_emulator.py"
        path.write_text(body)
        capability.EMULATOR = path
        return capability.capability(force=True)

    # ---- the failure that started this ------------------------------------

    def test_a_child_killed_by_sigill_is_a_skip_naming_the_signal(self):
        cap = self.child("import os, signal\n"
                         "os.kill(os.getpid(), signal.SIGILL)\n")
        self.assertFalse(cap.ok)
        self.assertIn("SIGILL", cap.reason)
        self.assertIn("cannot execute the probe here", cap.reason)

    def test_a_child_exiting_132_is_read_as_128_plus_sigill(self):
        # A shell or wrapper between us and the child reports a signal as
        # 128+n instead of a negative returncode; both must decode.
        cap = self.child("raise SystemExit(132)\n")
        self.assertFalse(cap.ok)
        self.assertIn("128+4", cap.reason)
        self.assertIn("SIGILL", cap.reason)

    def test_other_fatal_signals_are_named_too(self):
        cap = self.child("import os, signal\n"
                         "os.kill(os.getpid(), signal.SIGSEGV)\n")
        self.assertFalse(cap.ok)
        self.assertIn("SIGSEGV", cap.reason)

    # ---- the other ways the child can fail --------------------------------

    def test_a_traceback_in_the_child_is_a_skip_carrying_the_message(self):
        cap = self.child("raise RuntimeError('unstubbed firmware call')\n")
        self.assertFalse(cap.ok)
        self.assertIn("exit 1", cap.reason)
        self.assertIn("unstubbed firmware call", cap.detail)

    def test_a_child_that_ran_but_got_the_wrong_answer_is_not_usable(self):
        cap = self.child("print('SELFCHECK RAN BUT WRONG error=5 fit=0')\n"
                         "raise SystemExit(3)\n")
        self.assertFalse(cap.ok)
        self.assertIn("SELFCHECK RAN BUT WRONG", cap.detail)

    def test_a_hang_is_a_skip_not_a_hang_here(self):
        self.addCleanup(setattr, capability, "TIMEOUT", capability.TIMEOUT)
        capability.TIMEOUT = 1
        cap = self.child("import time\ntime.sleep(30)\n")
        self.assertFalse(cap.ok)
        self.assertIn("timed out", cap.reason)

    def test_exit_zero_without_the_token_is_not_a_pass(self):
        # Silence must never read as success: the token is the only pass.
        cap = self.child("print('nothing happened')\n")
        self.assertFalse(cap.ok)

    def test_the_token_and_exit_zero_together_are_a_pass(self):
        cap = self.child("print('SELFCHECK OK')\n")
        self.assertTrue(cap.ok)

    def test_a_missing_emulator_is_a_skip(self):
        capability.EMULATOR = pathlib.Path(self.dir.name) / "absent.py"
        cap = capability.capability(force=True)
        self.assertFalse(cap.ok)
        self.assertIn("missing", cap.reason)

    def test_the_environment_switch_wins_over_everything(self):
        os.environ["FP_SKIP_EMULATION"] = "1"
        self.addCleanup(os.environ.pop, "FP_SKIP_EMULATION", None)
        cap = self.child("print('SELFCHECK OK')\n")
        self.assertFalse(cap.ok)
        self.assertIn("FP_SKIP_EMULATION", cap.reason)


class ReportingTests(unittest.TestCase):

    def test_the_environment_report_does_not_import_unicorn_in_process(self):
        # If loading the library is itself fatal on a machine, collecting the
        # report must not be what kills it. Asked in a clean interpreter: under
        # `unittest discover` the emulation tests have already imported unicorn
        # into THIS process, so `"unicorn" in sys.modules` here says nothing
        # about what environment() did.
        out = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys; sys.dont_write_bytecode = True;"
             f"sys.path.insert(0, {str(capability.HERE)!r});"
             "import emulation_capability as c; c.environment();"
             "print('unicorn' in sys.modules)"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-400:])
        self.assertEqual(out.stdout.strip(), "False",
                         "environment() pulled unicorn into its own process")

    def test_the_environment_report_names_the_interpreter_and_the_arch(self):
        env = capability.environment()
        self.assertEqual(env["executable"], sys.executable)
        for key in ("python", "machine", "arch_of_interpreter", "unicorn"):
            self.assertIn(key, env)

    def test_the_real_selfcheck_is_what_the_gate_runs(self):
        # Not a fake: the shipped emulator, with the argument the gate passes.
        self.assertTrue(capability.EMULATOR.is_file())
        self.assertIn("--selfcheck", capability.EMULATOR.read_text())


if __name__ == "__main__":
    unittest.main()
