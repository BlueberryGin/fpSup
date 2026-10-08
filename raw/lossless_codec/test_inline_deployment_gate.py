#!/usr/bin/env python3
"""Offline refusal and evidence interpretation for the historical inline probe.

These tests deliberately do not install the probe or emulate a safe buffer
lease. They verify that its known unsafe live path cannot be enabled through
the public API or CLI while preserving offline build and captured-state use.
"""
import builtins
import contextlib
import io
import pathlib
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import inline_compress_driver as driver


class FakeShell:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        self.calls.append(name)
        raise AssertionError(f"unexpected camera access: {name}")


class ArmGateTests(unittest.TestCase):
    def no_live_entrypoints(self):
        """Fail if the refusal path even imports a live helper or claims RAM."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in {"callfn", "cave", "putfile", "getfile"}:
                self.fail(f"live helper imported before refusing arm: {name}")
            return real_import(name, *args, **kwargs)

        stack.enter_context(mock.patch("builtins.__import__", guarded_import))
        for owner, name in ((driver, "place"), (driver, "both_handles"),
                            (driver, "close_codec"),
                            (driver.base, "CameraShell"),
                            (driver.flush, "arm_site")):
            stack.enter_context(mock.patch.object(
                owner, name, side_effect=AssertionError(f"unexpected {name}")))

    def test_direct_arm_refuses_before_any_camera_or_import_effect(self):
        self.no_live_entrypoints()
        shell = FakeShell()
        with self.assertRaisesRegex(driver.flush.ProbeError, "arming is disabled"):
            driver.arm(shell, pathlib.Path("/missing/fpsup"),
                       pathlib.Path("/missing/reference.DNG"))
        self.assertEqual(shell.calls, [])

    def test_direct_arm_has_no_frame_bound_escape(self):
        self.no_live_entrypoints()
        for bound in (0, 1, 40, 2**31):
            with self.subTest(bound=bound), mock.patch.object(driver, "FRAMES", bound):
                with self.assertRaises(driver.flush.ProbeError):
                    driver.arm(FakeShell(), None, None)

    def test_cli_refuses_before_constructing_shell_or_calling_arm(self):
        self.no_live_entrypoints()
        argv = [str(driver.__file__), "arm", "--bound", "40", "--reference",
                "/missing/reference.DNG", "--fpsup", "/missing/fpsup",
                "--fpsh", "/missing/fpsh"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
                driver, "arm", side_effect=AssertionError("CLI called arm")):
            with self.assertRaisesRegex(driver.flush.ProbeError, "arming is disabled"):
                driver.main()

    def test_cli_arm_without_reference_still_reports_safety_gate(self):
        self.no_live_entrypoints()
        with mock.patch.object(sys, "argv", [str(driver.__file__), "arm"]):
            with self.assertRaisesRegex(driver.flush.ProbeError, "native source lease"):
                driver.main()

    def test_no_force_or_unsafe_command_line_bypass(self):
        self.no_live_entrypoints()
        for flag in ("--force", "--unsafe", "--allow-unsafe"):
            with self.subTest(flag=flag), mock.patch.object(
                    sys, "argv", [str(driver.__file__), "arm", flag]), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    driver.main()
                self.assertEqual(raised.exception.code, 2)

    def test_gate_explains_all_three_unresolved_requirements(self):
        self.assertIn("earlier frame into a later frame", driver.ARM_DISABLED)
        self.assertIn("native source lease", driver.ARM_DISABLED)
        self.assertIn("async drain/release", driver.ARM_DISABLED)
        self.assertIn("same-frame product pipeline", driver.ARM_DISABLED)

    def test_build_still_uses_offline_assembler_without_camera(self):
        self.no_live_entrypoints()
        assemble = mock.Mock(return_value=b"\0" * 16)
        with mock.patch.object(sys, "argv", [str(driver.__file__), "build"]), \
                mock.patch.object(driver.base, "load_assembler",
                                  return_value=(assemble, mock.Mock())), \
                mock.patch.object(driver, "configure"), \
                mock.patch.object(driver, "FRAMES", 8), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(driver.main(), 0)
        assemble.assert_called_once()
        self.assertIn("no camera touched", out.getvalue())


class CapturedStateTests(unittest.TestCase):
    @staticmethod
    def state():
        v = [0] * driver.STATE_WORDS
        v[driver.S_PROOF] = driver.PROOF_MAGIC
        v[driver.S_DONE] = 9
        v[driver.S_LATE] = 4
        v[driver.S_RAW_BYTES] = 1024
        v[driver.S_SUM] = 512
        return v

    def test_processed_steps_are_not_reported_as_compressed_frames(self):
        report = driver.status_report(self.state(), driver.flush.HOOK_ORIG)
        self.assertEqual(report["processed_steps"], 9)
        self.assertEqual(report["frames_compressed"], 4)
        self.assertEqual(report["frames_written_from_an_earlier_frame"], 4)

    def test_ratio_uses_captured_geometry_not_fixed_fhd_raster(self):
        v = self.state()
        self.assertEqual(driver.status_report(v, 0)["ratio_percent"], 50.0)
        v[driver.S_RAW_BYTES] = 0
        self.assertIsNone(driver.status_report(v, 0)["ratio_percent"])

    def test_no_seed_does_not_claim_every_frame_was_refused(self):
        v = self.state()
        v[driver.S_PROOF] = 0
        report = driver.status_report(v, 0)
        self.assertFalse(report["seeded"])
        self.assertNotIn("refused every", report["note"])

    def test_submissions_alone_are_not_successful_compression(self):
        v = self.state()
        v[driver.S_LATE] = 0
        said = driver.state_verdict(v)
        self.assertIn("NOTHING COMPRESSED", said)
        self.assertIn("9 processed steps", said)
        self.assertIn("include submissions", said)

    def test_writeback_verdict_keeps_frame_identity_warning(self):
        said = driver.state_verdict(self.state())
        self.assertIn("4 compressed write-back(s)", said)
        self.assertIn("EARLIER frame", said)
        self.assertIn("not same-frame lossless validation", said)
        self.assertIn("50.0 percent", said)

    def test_missing_callback_is_absent_evidence_not_absent_completion(self):
        said = driver.refusal_reading(self.state())
        self.assertIn("does not establish", said)
        self.assertNotIn("no job ever completed", said)

    def test_failure_end_positions_do_not_infer_production_or_capacity(self):
        for endpos in (0, 4096):
            v = self.state()
            v[driver.S_CB_HITS] = 1
            v[driver.S_ENG_ERRS] = 1
            v[driver.S_ENG_STATUS] = 5
            v[driver.S_ENG_ENDPOS] = endpos
            with self.subTest(endpos=endpos):
                said = driver.refusal_reading(v)
                self.assertIn("failure-path register semantics are unverified", said)
                self.assertIn("does not prove", said)
                self.assertNotIn("produced nothing at all", said)
                self.assertNotIn("HAD produced output", said)

    def test_unmapped_status_bits_are_not_assigned_a_cause(self):
        v = self.state()
        v[driver.S_CB_HITS] = v[driver.S_ENG_ERRS] = 1
        v[driver.S_ENG_STATUS] = 0x25
        said = driver.refusal_reading(v)
        self.assertIn("meaning is not established", said)
        self.assertNotIn("which is a cause", said)

    def test_soi_is_neither_decode_success_nor_decode_failure(self):
        said = driver.card_verdict([{
            "bytes": 2048, "shortened": True,
            "payload_starts_lossless_jpeg": True, "sha256": "example",
        }])
        self.assertIn("SHORTENED WITH EXPECTED SOI", said)
        self.assertIn("does not prove", said)
        self.assertIn("decode and same-frame comparison are required", said)
        self.assertNotIn("files do not decode yet", said)
        self.assertNotIn("only thing left", said)


if __name__ == "__main__":
    unittest.main()
