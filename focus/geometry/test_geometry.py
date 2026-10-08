"""Offline behavioral tests for the shadow-only geometry core.

Build geometry.c directly into a temporary native library; no Python binding,
camera connection, firmware execution, or installed package is required.
Synthetic thresholds below are development fixtures, not camera calibration.
Run: python3 -B fpSup/focus/geometry/test_geometry.py
"""

import ctypes as C
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


Q16 = 65536
U32 = 0xFFFFFFFF
FACE, AAT = 1, 2
UNKNOWN, APPROACH, RECEDE, STATIONARY = 0, 1, 2, 3
NO_ANCHOR, WARMUP, TRACKING = 0, 1, 2
SCALE_VALID, IDENTITY_VALID, TIME_VALID = 1, 2, 4
PROJECTION_STABLE, LENS_STATIONARY, AAT_SCALE_VALIDATED = 8, 16, 32
REQUIRED = 31
OK, NEEDS_FOCUS, BAD_CONFIG, BAD_SAMPLE, STALE, OUT_OF_ORDER = range(6)
CONTEXT_CHANGED, UNTRUSTED_SCALE, LENS_MOVING, ANCHOR_EXPIRED = range(6, 10)
GAP, SCALE_JUMP, TOO_FEW, TOO_SHORT, NOISY = range(10, 15)
CONFIRMATION_REJECTED, EXPLICIT_RESET = 15, 16


def q16(value):
    return int(round(value * Q16))


class Config(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in (
        "window_ms", "min_span_ms", "min_samples", "max_gap_ms",
        "max_age_ms", "max_anchor_age_ms", "min_rate_q16",
        "max_residual_q16", "max_scale_step_q16",
    )]


class Sample(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in (
        "now_ms", "sample_ms", "source_seq", "target_generation",
        "context_generation", "scale_generation", "source_kind", "flags",
        "scale_q16",
    )] + [("lens_position", C.c_int32)]


class Result(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in (
        "phase", "direction", "reason", "anchor_valid", "sample_count",
    )] + [("relative_rate_q16", C.c_int32)] + [
        (name, C.c_uint32) for name in (
            "residual_q16", "confidence_permille", "anchor_age_ms",
        )
    ] + [("anchor_position", C.c_int32)] + [
        (name, C.c_uint32) for name in ("anchor_scale_q16", "motion_authorized")
    ]


class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_dir = Path(__file__).resolve().parent
        compiler = shlex.split(os.environ.get("CC", "cc"))
        if not compiler or shutil.which(compiler[0]) is None:
            raise RuntimeError("A native C compiler is required for these offline tests")
        cls.build_dir = tempfile.TemporaryDirectory(prefix="fp-geometry-tests-")
        cls.addClassCleanup(cls.build_dir.cleanup)
        temporary = Path(cls.build_dir.name)
        helper = temporary / "state_size.c"
        helper.write_text(
            '#include <stddef.h>\n#include "geometry.h"\n'
            'size_t fg_test_state_size(void) { return sizeof(FgState); }\n'
            'size_t fg_test_state_align(void) { return _Alignof(FgState); }\n'
            'size_t fg_test_config_size(void) { return sizeof(FgConfig); }\n'
            'size_t fg_test_sample_size(void) { return sizeof(FgSample); }\n'
            'size_t fg_test_result_size(void) { return sizeof(FgResult); }\n',
            encoding="utf-8",
        )
        library = temporary / ("geometry.dylib" if sys.platform == "darwin" else "geometry.so")
        link_flags = ["-dynamiclib"] if sys.platform == "darwin" else ["-shared", "-fPIC"]
        command = compiler + ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror"] + link_flags + [
            "-I", str(source_dir), str(source_dir / "geometry.c"), str(helper),
            "-o", str(library),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode:
            raise RuntimeError("Native geometry build failed:\n" + completed.stdout + completed.stderr)
        cls.lib = C.CDLL(str(library))
        for name in ("state", "config", "sample", "result"):
            function = getattr(cls.lib, "fg_test_" + name + "_size")
            function.argtypes, function.restype = [], C.c_size_t
        cls.lib.fg_test_state_align.argtypes = []
        cls.lib.fg_test_state_align.restype = C.c_size_t
        cls.state_size = cls.lib.fg_test_state_size()
        if cls.lib.fg_test_state_align() > C.alignment(C.c_uint64):
            raise RuntimeError("State requires alignment stronger than the uint64 storage")
        for name, structure in (("config", Config), ("sample", Sample), ("result", Result)):
            if getattr(cls.lib, "fg_test_" + name + "_size")() != C.sizeof(structure):
                raise RuntimeError("ctypes layout disagrees with geometry.h: " + name)
        cls.lib.fg_default_config.argtypes = [C.POINTER(Config)]
        cls.lib.fg_default_config.restype = None
        cls.lib.fg_init.argtypes = [C.c_void_p, C.POINTER(Config)]
        cls.lib.fg_init.restype = C.c_int
        cls.lib.fg_invalidate.argtypes = [C.c_void_p, C.c_uint32, C.POINTER(Result)]
        cls.lib.fg_invalidate.restype = None
        cls.lib.fg_confirm.argtypes = [C.c_void_p, C.POINTER(Sample), C.c_uint32,
                                      C.c_uint32, C.POINTER(Result)]
        cls.lib.fg_confirm.restype = None
        cls.lib.fg_update.argtypes = [C.c_void_p, C.POINTER(Sample), C.POINTER(Result)]
        cls.lib.fg_update.restype = None

    def setUp(self):
        self.config = self.make_config()
        self.state = self.new_state()
        self.assertEqual(self.lib.fg_init(self.state, C.byref(self.config)), 1)
        self.base_ms = 10000
        self.base_seq = 100
        self.base_scale = q16(0.15)
        self.focus_event = 77

    @staticmethod
    def make_config(**overrides):
        values = dict(window_ms=600, min_span_ms=200, min_samples=5,
                      max_gap_ms=100, max_age_ms=80, max_anchor_age_ms=4000,
                      min_rate_q16=q16(0.04), max_residual_q16=q16(0.008),
                      max_scale_step_q16=q16(0.12))
        values.update(overrides)
        return Config(**values)

    def new_state(self):
        return (C.c_uint64 * ((self.state_size + 7) // 8))()

    def sample(self, elapsed=0, scale=None, **overrides):
        stamp = (self.base_ms + elapsed) & U32
        values = dict(now_ms=stamp, sample_ms=stamp,
                      source_seq=(self.base_seq + elapsed // 50) & U32,
                      target_generation=1, context_generation=1, scale_generation=1,
                      source_kind=FACE, flags=REQUIRED,
                      scale_q16=self.base_scale if scale is None else int(scale),
                      lens_position=22000)
        values.update(overrides)
        return Sample(**values)

    @staticmethod
    def fresh_result():
        result = Result()
        # Check that every result path explicitly revokes any previous authority.
        C.memset(C.byref(result), 0xA5, C.sizeof(result))
        return result

    def check_result_contract(self, result):
        self.assertEqual(result.motion_authorized, 0)
        self.assertIn(result.direction, (UNKNOWN, APPROACH, RECEDE, STATIONARY))
        self.assertIn(result.phase, (NO_ANCHOR, WARMUP, TRACKING))
        self.assertLessEqual(result.sample_count, 32)
        self.assertLessEqual(result.confidence_permille, 1000)
        return result

    def confirm(self, sample=None, event=None, flags=3):
        result = self.fresh_result()
        self.lib.fg_confirm(self.state, C.byref(sample or self.sample()),
                            self.focus_event if event is None else event,
                            flags, C.byref(result))
        return self.check_result_contract(result)

    def update(self, sample):
        result = self.fresh_result()
        self.lib.fg_update(self.state, C.byref(sample), C.byref(result))
        return self.check_result_contract(result)

    def invalidate(self):
        result = self.fresh_result()
        self.lib.fg_invalidate(self.state, EXPLICIT_RESET, C.byref(result))
        return self.check_result_contract(result)

    def seed(self, **sample_fields):
        result = self.confirm(self.sample(**sample_fields))
        self.assertEqual(result.anchor_valid, 1)
        self.assertEqual(result.direction, UNKNOWN)
        self.assertEqual(result.sample_count, 1)
        self.assertEqual(result.anchor_position, 22000)
        return result

    def trajectory(self, rate, end=400, start=50, **fields):
        """Constant relative radial speed, not constant image-size speed.

        z(t)/z(0)=1+rate*t implies scale(t)=scale(0)/(1+rate*t).
        The API normalizes fitted reciprocal slope by window-mean reciprocal.
        """
        result = None
        for elapsed in range(start, end + 1, 50):
            scale = round(self.base_scale / (1.0 + rate * elapsed / 1000.0))
            result = self.update(self.sample(elapsed, scale, **fields))
        return result

    def test_defaults_are_accepted(self):
        config = Config()
        self.lib.fg_default_config(C.byref(config))
        self.assertEqual(self.lib.fg_init(self.new_state(), C.byref(config)), 1)

    def test_without_optical_confirmation_motion_remains_unknown(self):
        for elapsed in range(0, 450, 50):
            # Reported mechanical positions/settled flags cannot make an anchor.
            result = self.update(self.sample(elapsed, self.base_scale + elapsed,
                                             lens_position=21000 + elapsed))
            self.assertEqual(result.anchor_valid, 0)
            self.assertEqual(result.direction, UNKNOWN)
            self.assertEqual(result.reason, NEEDS_FOCUS)

    def test_confirmation_needs_optical_and_position_bits(self):
        for flags in (0, 1, 2):
            with self.subTest(flags=flags):
                result = self.confirm(flags=flags)
                self.assertEqual(result.reason, CONFIRMATION_REJECTED)
                self.assertEqual(result.anchor_valid, 0)
                self.assertEqual(result.direction, UNKNOWN)
        self.seed()

    def test_confirmation_rejects_invalid_measurement_and_untrusted_aat(self):
        cases = [dict(scale_q16=0), dict(flags=REQUIRED & ~IDENTITY_VALID),
                 dict(flags=REQUIRED & ~LENS_STATIONARY), dict(source_kind=AAT),
                 dict(target_generation=0), dict(now_ms=self.base_ms + 81)]
        for fields in cases:
            with self.subTest(fields=fields):
                result = self.confirm(self.sample(**fields))
                self.assertEqual(result.anchor_valid, 0)
                self.assertEqual(result.direction, UNKNOWN)
        self.seed()

    def test_first_confirmation_seeds_only_not_direction(self):
        result = self.seed()
        self.assertEqual(result.phase, WARMUP)
        self.assertEqual(result.anchor_scale_q16, self.base_scale)
        result = self.update(self.sample(50, self.base_scale + 40))
        self.assertEqual(result.direction, UNKNOWN)

    def test_reciprocal_physics_approach_recede_and_stationary(self):
        for rate, direction in ((-0.2, APPROACH), (0.2, RECEDE), (0.0, STATIONARY)):
            with self.subTest(rate=rate):
                self.setUp()
                self.seed()
                result = self.trajectory(rate)
                self.assertEqual(result.direction, direction)
                self.assertEqual(result.phase, TRACKING)
                self.assertEqual(result.anchor_valid, 1)
                # Samples span 0..400 ms, so mean normalized depth is 1+r*0.2.
                expected = rate / (1.0 + rate * 0.2)
                self.assertAlmostEqual(result.relative_rate_q16 / Q16, expected, delta=0.003)
                self.assertLess(result.residual_q16, q16(0.002))

    def test_noisy_scale_sequence_is_not_a_trusted_direction(self):
        self.seed()
        for index, elapsed in enumerate(range(50, 450, 50)):
            reciprocal = 1.0 + (0.035 if index % 2 else -0.035)
            result = self.update(self.sample(elapsed, round(self.base_scale / reciprocal)))
        self.assertEqual(result.anchor_valid, 1)
        self.assertEqual(result.direction, UNKNOWN)
        self.assertEqual(result.reason, NOISY)

    def test_small_stationary_jitter_does_not_create_radial_motion(self):
        self.seed()
        for index, elapsed in enumerate(range(50, 450, 50)):
            result = self.update(self.sample(elapsed, self.base_scale + (-2 if index % 2 else 2)))
        self.assertEqual(result.direction, STATIONARY)

    def test_target_context_scale_or_source_change_drops_anchor(self):
        cases = [dict(target_generation=2), dict(context_generation=2),
                 dict(scale_generation=2),
                 dict(source_kind=AAT, flags=REQUIRED | AAT_SCALE_VALIDATED)]
        for fields in cases:
            with self.subTest(fields=fields):
                self.setUp()
                self.seed()
                self.trajectory(0.2)
                result = self.update(self.sample(450, **fields))
                self.assertEqual(result.anchor_valid, 0)
                self.assertEqual(result.direction, UNKNOWN)
                self.assertEqual(result.reason, CONTEXT_CHANGED)
                result = self.update(self.sample(500, **fields))
                self.assertEqual(result.anchor_valid, 0)
                self.assertEqual(result.direction, UNKNOWN)

    def test_new_context_can_restart_source_sequence_after_new_confirmation(self):
        cases = [dict(target_generation=2), dict(context_generation=2),
                 dict(scale_generation=2),
                 dict(source_kind=AAT, flags=REQUIRED | AAT_SCALE_VALIDATED)]
        for fields in cases:
            with self.subTest(fields=fields):
                self.setUp()
                self.seed()
                self.trajectory(0.2)
                result = self.update(self.sample(450, source_seq=0, **fields))
                self.assertEqual(result.reason, CONTEXT_CHANGED)
                self.assertEqual(result.anchor_valid, 0)
                self.assertEqual(result.direction, UNKNOWN)
                result = self.confirm(self.sample(500, source_seq=1, **fields), event=78)
                self.assertEqual(result.anchor_valid, 1)
                self.assertEqual(result.direction, UNKNOWN)
                self.assertEqual(result.sample_count, 1)

    def test_unvalidated_aat_cannot_seed_or_extend_geometry(self):
        result = self.confirm(self.sample(source_kind=AAT))
        self.assertEqual(result.anchor_valid, 0)
        self.seed(source_kind=AAT, flags=REQUIRED | AAT_SCALE_VALIDATED)
        result = self.update(self.sample(50, source_kind=AAT))
        self.assertEqual(result.direction, UNKNOWN)
        self.assertEqual(result.anchor_valid, 0)
        self.assertEqual(result.reason, UNTRUSTED_SCALE)

    def test_explicitly_validated_aat_has_same_physics_contract(self):
        fields = dict(source_kind=AAT, flags=REQUIRED | AAT_SCALE_VALIDATED)
        self.seed(**fields)
        self.assertEqual(self.trajectory(-0.2, **fields).direction, APPROACH)

    def test_lens_motion_clears_trend_but_keeps_optical_anchor(self):
        self.seed()
        self.assertEqual(self.trajectory(-0.2).direction, APPROACH)
        result = self.update(self.sample(450, flags=REQUIRED & ~LENS_STATIONARY))
        self.assertEqual((result.anchor_valid, result.direction, result.reason),
                         (1, UNKNOWN, LENS_MOVING))
        self.assertEqual(result.sample_count, 0)
        for elapsed in range(500, 750, 50):
            result = self.update(self.sample(elapsed))
        self.assertEqual(result.direction, STATIONARY)
        self.assertEqual(result.anchor_position, 22000)

    def test_short_gap_rebuilds_window_without_new_confirmation(self):
        self.seed()
        self.trajectory(0.2)
        result = self.update(self.sample(550))
        self.assertEqual((result.anchor_valid, result.direction, result.reason),
                         (1, UNKNOWN, GAP))
        self.assertEqual(result.sample_count, 1)
        for elapsed in range(600, 800, 50):
            result = self.update(self.sample(elapsed))
        self.assertEqual(result.direction, STATIONARY)

    def test_stale_observation_drops_anchor_and_cannot_restart_it(self):
        self.seed()
        result = self.update(self.sample(50, now_ms=self.base_ms + 131))
        self.assertEqual((result.anchor_valid, result.direction, result.reason),
                         (0, UNKNOWN, STALE))
        result = self.update(self.sample(150))
        self.assertEqual(result.anchor_valid, 0)

    def test_future_timestamp_cannot_form_a_trend(self):
        self.seed()
        result = self.update(self.sample(50, now_ms=self.base_ms + 40))
        self.assertEqual(result.anchor_valid, 0)
        self.assertEqual(result.direction, UNKNOWN)

    def test_duplicate_or_old_time_or_sequence_does_not_pollute_history(self):
        self.seed()
        baseline = self.trajectory(0.2)
        samples = [self.sample(400, source_seq=109), self.sample(450, source_seq=108),
                   self.sample(350, source_seq=109), self.sample(450, source_seq=107),
                   # An obsolete observation must not drop a valid new context.
                   self.sample(350, source_seq=107, target_generation=99, scale_q16=65536)]
        for sample in samples:
            with self.subTest(time=sample.sample_ms, seq=sample.source_seq):
                result = self.update(sample)
                self.assertEqual(result.reason, OUT_OF_ORDER)
                self.assertEqual(result.anchor_valid, 1)
                self.assertEqual(result.direction, UNKNOWN)
                self.assertEqual(result.sample_count, baseline.sample_count)
        result = self.trajectory(0.2, start=450, end=450)
        self.assertEqual(result.direction, RECEDE)
        expected = 0.2 / (1.0 + 0.2 * 0.225)
        self.assertAlmostEqual(result.relative_rate_q16 / Q16, expected, delta=0.003)

    def test_anchor_expires_even_when_measurement_is_fresh(self):
        self.seed()
        result = self.update(self.sample(4050))
        self.assertEqual((result.anchor_valid, result.direction, result.reason),
                         (0, UNKNOWN, ANCHOR_EXPIRED))

    def test_scale_discontinuity_requires_new_optical_anchor(self):
        self.seed()
        result = self.update(self.sample(50, round(self.base_scale * 1.3)))
        self.assertEqual((result.anchor_valid, result.direction, result.reason),
                         (0, UNKNOWN, SCALE_JUMP))
        result = self.update(self.sample(100, round(self.base_scale * 1.3)))
        self.assertEqual(result.anchor_valid, 0)

    def test_uint32_clock_and_observation_sequence_wrap(self):
        self.base_ms = 0xFFFFFF80
        self.base_seq = 0xFFFFFFFC
        self.seed()
        result = self.trajectory(0.2)
        self.assertEqual(result.direction, RECEDE)
        self.assertEqual(result.anchor_valid, 1)
        self.assertEqual(result.anchor_age_ms, 400)

    def test_focus_event_sequence_wrap_is_accepted_only_forward(self):
        self.focus_event = 0xFFFFFFFE
        self.seed()
        result = self.confirm(self.sample(50), event=1)
        self.assertEqual(result.anchor_valid, 1)
        self.assertEqual(result.anchor_age_ms, 0)
        result = self.confirm(self.sample(100), event=0xFFFFFFFD)
        self.assertEqual(result.direction, UNKNOWN)
        self.assertEqual(result.reason, CONFIRMATION_REJECTED)
        self.assertEqual(result.anchor_scale_q16, self.base_scale)

    def test_rejected_confirmation_does_not_refresh_existing_anchor(self):
        self.seed()
        self.trajectory(0.2)
        for event, flags in ((77, 3), (76, 3), (78, 2)):
            result = self.confirm(self.sample(450), event=event, flags=flags)
            self.assertEqual(result.direction, UNKNOWN)
            self.assertEqual(result.reason, CONFIRMATION_REJECTED)
            self.assertEqual(result.anchor_valid, 1)
        result = self.update(self.sample(4050))
        self.assertEqual(result.reason, ANCHOR_EXPIRED)
        self.assertEqual(result.anchor_valid, 0)

    def test_old_confirmation_cannot_overwrite_newer_observation(self):
        self.seed()
        self.trajectory(-0.2)
        result = self.confirm(self.sample(350), event=78)
        self.assertEqual(result.reason, CONFIRMATION_REJECTED)
        self.assertEqual(result.direction, UNKNOWN)
        result = self.trajectory(-0.2, start=450, end=450)
        self.assertEqual(result.direction, APPROACH)

    def test_reset_retains_confirmation_and_observation_replay_protection(self):
        self.seed()
        self.trajectory(0.2)
        result = self.invalidate()
        self.assertEqual(result.anchor_valid, 0)
        self.assertEqual(result.direction, UNKNOWN)
        result = self.confirm(self.sample(450), event=77)
        self.assertEqual(result.reason, CONFIRMATION_REJECTED)
        self.assertEqual(result.anchor_valid, 0)
        result = self.confirm(self.sample(350), event=78)
        self.assertEqual(result.reason, CONFIRMATION_REJECTED)
        self.assertEqual(result.anchor_valid, 0)
        result = self.confirm(self.sample(450), event=79)
        self.assertEqual(result.anchor_valid, 1)
        self.assertEqual(result.direction, UNKNOWN)

    def test_zero_generation_invalid_source_and_invalid_scale_are_rejected(self):
        cases = [dict(target_generation=0), dict(context_generation=0),
                 dict(scale_generation=0), dict(source_kind=0), dict(source_kind=3),
                 dict(scale_q16=63), dict(scale_q16=131073),
                 dict(flags=REQUIRED & ~SCALE_VALID),
                 dict(flags=REQUIRED & ~PROJECTION_STABLE)]
        for fields in cases:
            with self.subTest(fields=fields):
                self.setUp()
                self.seed()
                result = self.update(self.sample(50, **fields))
                self.assertEqual(result.anchor_valid, 0)
                self.assertEqual(result.direction, UNKNOWN)

    def test_invalid_configurations_are_fail_closed(self):
        cases = {"window_ms": (19, 1001), "min_span_ms": (0, 601),
                 "min_samples": (2, 33), "max_gap_ms": (0, 601),
                 "max_age_ms": (1001,), "max_anchor_age_ms": (600, 600001),
                 "min_rate_q16": (0, 65537), "max_residual_q16": (0, 16385),
                 "max_scale_step_q16": (0, 32769)}
        for name, values in cases.items():
            for value in values:
                with self.subTest(field=name, value=value):
                    config = self.make_config(**{name: value})
                    self.state = self.new_state()
                    self.assertEqual(self.lib.fg_init(self.state, C.byref(config)), 0)
                    result = self.confirm()
                    self.assertEqual(result.reason, BAD_CONFIG)
                    self.assertEqual(result.anchor_valid, 0)
                    result = self.update(self.sample(50))
                    self.assertEqual(result.reason, BAD_CONFIG)
                    self.assertEqual(result.direction, UNKNOWN)
        self.state = self.new_state()
        self.assertEqual(self.lib.fg_init(self.state, None), 0)
        self.assertEqual(self.update(self.sample()).reason, BAD_CONFIG)


if __name__ == "__main__":
    unittest.main(verbosity=2)
