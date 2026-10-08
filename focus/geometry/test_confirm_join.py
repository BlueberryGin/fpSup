"""Offline behavioral tests for the native-confirmation shadow event join.

Compile the C implementation directly; no Python adapter, camera, native hook,
or transport is used. All validity assertions are synthetic test inputs and do
not establish a verified camera policy or authorize focus/lens operations.
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


U32 = 0xFFFFFFFF
BEGIN, DRIVE_BEGIN, AF_RESULT, DRIVE_END, SUBJECT, CANCEL, FAULT, TICK = range(1, 9)
SUPPORTED, CONNECTED, FINAL_END, AF_ACCEPTED = 1, 2, 4, 8
POSITION_VALID, SETTLE_VALIDATED, CANCELLED, FAULTED, NOOP = 16, 32, 64, 128, 256
IDLE, WAIT_DRIVE, WAIT_PAIR, WAIT_SUBJECT, CONSUMED, BLOCKED = range(6)
OK, NEEDS_BEGIN, BAD_CONFIG, BAD_EVENT, OLD_EVENT, LOST_EVENT = range(6)
STALE_EVENT, WRONG_EPISODE, CONTEXT_CHANGED, WRONG_DRIVE = range(6, 10)
WAITING, INTERMEDIATE_END, RESULT_FAILED, CANCEL_REASON, FAULT_REASON = range(10, 15)
TIMEOUT, NOOP_UNSUPPORTED, UNTRUSTED_POSITION, UNTRUSTED_SUBJECT = range(15, 19)
ALREADY_CONSUMED, RESET_REASON = 19, 20
FACE, AAT = 1, 2
FG_REQUIRED, FG_AAT_VALIDATED = 31, 32


class Config(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in (
        "max_episode_ms", "max_event_age_ms", "max_pair_gap_ms",
        "max_sample_age_ms", "max_position_age_ms",
    )]


class Sample(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in (
        "now_ms", "sample_ms", "source_seq", "target_generation",
        "context_generation", "scale_generation", "source_kind", "flags", "scale_q16",
    )] + [("lens_position", C.c_int32)]


class Event(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in (
        "kind", "stream_seq", "event_ms", "now_ms", "episode_generation",
        "drive_generation", "target_generation", "context_generation",
        "scale_generation", "source_kind", "flags", "error_code", "position_ms",
    )] + [("end_position", C.c_int32), ("sample", Sample)]


class Result(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in (
        "candidate_ready", "phase", "reason", "episode_generation", "drive_generation",
        "focus_event_seq", "confirmation_authorized", "motion_authorized",
    )] + [("candidate", Sample)]


class ConfirmationJoinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parent
        compiler = shlex.split(os.environ.get("CC", "cc"))
        if not compiler or shutil.which(compiler[0]) is None:
            raise RuntimeError("A native C compiler is required for these offline tests")
        cls.temporary = tempfile.TemporaryDirectory(prefix="fp-confirm-join-tests-")
        cls.addClassCleanup(cls.temporary.cleanup)
        temporary = Path(cls.temporary.name)
        helper = temporary / "layout.c"
        helper.write_text(
            '#include <stddef.h>\n#include "confirm_join.h"\n'
            'size_t fj_test_state_size(void) { return sizeof(FjState); }\n'
            'size_t fj_test_state_align(void) { return _Alignof(FjState); }\n'
            'size_t fj_test_config_size(void) { return sizeof(FjConfig); }\n'
            'size_t fj_test_event_size(void) { return sizeof(FjEvent); }\n'
            'size_t fj_test_result_size(void) { return sizeof(FjResult); }\n'
            'size_t fj_test_sample_offset(void) { return offsetof(FjEvent, sample); }\n'
            'size_t fj_test_candidate_offset(void) { return offsetof(FjResult, candidate); }\n',
            encoding="utf-8",
        )
        library = temporary / ("join.dylib" if sys.platform == "darwin" else "join.so")
        flags = ["-dynamiclib"] if sys.platform == "darwin" else ["-shared", "-fPIC"]
        command = compiler + ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror"] + flags + [
            "-I", str(source), str(source / "confirm_join.c"), str(helper), "-o", str(library),
        ]
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError("Native confirmation-join build failed:\n" + result.stdout + result.stderr)
        cls.lib = C.CDLL(str(library))
        for name in ("state_size", "state_align", "config_size", "event_size", "result_size",
                     "sample_offset", "candidate_offset"):
            function = getattr(cls.lib, "fj_test_" + name)
            function.argtypes, function.restype = [], C.c_size_t
        cls.state_size = cls.lib.fj_test_state_size()
        if cls.lib.fj_test_state_align() > C.alignment(C.c_uint64):
            raise RuntimeError("State alignment exceeds uint64 storage")
        for name, structure in (("config", Config), ("event", Event), ("result", Result)):
            if getattr(cls.lib, "fj_test_" + name + "_size")() != C.sizeof(structure):
                raise RuntimeError("ctypes layout disagrees with confirm_join.h: " + name)
        if (cls.lib.fj_test_sample_offset() != Event.sample.offset or
                cls.lib.fj_test_candidate_offset() != Result.candidate.offset):
            raise RuntimeError("ctypes embedded sample layout disagrees with confirm_join.h")
        cls.lib.fj_default_config.argtypes, cls.lib.fj_default_config.restype = [C.POINTER(Config)], None
        cls.lib.fj_init.argtypes = [C.c_void_p, C.POINTER(Config)]
        cls.lib.fj_init.restype = C.c_int
        cls.lib.fj_reset.argtypes = [C.c_void_p, C.POINTER(Result)]
        cls.lib.fj_reset.restype = None
        cls.lib.fj_step.argtypes = [C.c_void_p, C.POINTER(Event), C.POINTER(Result)]
        cls.lib.fj_step.restype = None

    def setUp(self):
        self.restart()

    def restart(self, **config):
        values = dict(max_episode_ms=5000, max_event_age_ms=100, max_pair_gap_ms=500,
                      max_sample_age_ms=100, max_position_age_ms=500)
        values.update(config)
        self.config = Config(**values)
        self.state = (C.c_uint64 * ((self.state_size + 7) // 8))()
        self.assertEqual(self.lib.fj_init(self.state, C.byref(self.config)), 1)
        self.seq, self.clock, self.episode, self.drive = 100, 10000, 10, 20

    def event(self, kind, **overrides):
        self.seq = (self.seq + 1) & U32
        self.clock = (self.clock + 10) & U32
        flags = SUPPORTED | CONNECTED
        if kind == AF_RESULT:
            flags |= AF_ACCEPTED
        if kind == DRIVE_END:
            flags |= FINAL_END | POSITION_VALID | SETTLE_VALIDATED
        values = dict(kind=kind, stream_seq=self.seq, event_ms=self.clock, now_ms=self.clock,
                      episode_generation=self.episode, drive_generation=self.drive,
                      target_generation=1, context_generation=2, scale_generation=3,
                      source_kind=FACE, flags=flags, error_code=0,
                      position_ms=self.clock, end_position=12000)
        sample_overrides = overrides.pop("sample_overrides", {})
        values.update(overrides)
        self.seq, self.clock = values["stream_seq"], values["event_ms"]
        sample = dict(now_ms=values["now_ms"], sample_ms=values["event_ms"], source_seq=self.seq,
                      target_generation=values["target_generation"],
                      context_generation=values["context_generation"],
                      scale_generation=values["scale_generation"], source_kind=values["source_kind"],
                      flags=FG_REQUIRED, scale_q16=10000, lens_position=values["end_position"])
        sample.update(sample_overrides)
        values["sample"] = Sample(**sample)
        return Event(**values)

    def step(self, event):
        result = Result()
        C.memset(C.byref(result), 0xA5, C.sizeof(result))
        self.lib.fj_step(self.state, C.byref(event), C.byref(result))
        self.assertEqual(result.confirmation_authorized, 0)
        self.assertEqual(result.motion_authorized, 0)
        self.assertIn(result.phase, range(6))
        self.assertIn(result.candidate_ready, (0, 1))
        if not result.candidate_ready:
            self.assertEqual(bytes(result.candidate), bytes(C.sizeof(Sample)))
        return result

    def send(self, kind, **overrides):
        return self.step(self.event(kind, **overrides))

    def begin_drive(self):
        self.assertEqual(self.send(BEGIN).phase, WAIT_DRIVE)
        self.assertEqual(self.send(DRIVE_BEGIN).phase, WAIT_PAIR)

    def pair(self, end_first=False):
        self.begin_drive()
        first, second = (DRIVE_END, AF_RESULT) if end_first else (AF_RESULT, DRIVE_END)
        self.assertEqual(self.send(first).candidate_ready, 0)
        self.assertEqual(self.send(second).phase, WAIT_SUBJECT)

    def assert_blocked(self, result, reason=None):
        self.assertEqual(result.candidate_ready, 0)
        self.assertEqual(result.phase, BLOCKED)
        if reason is not None:
            self.assertEqual(result.reason, reason)

    def test_default_config_and_invalid_configuration(self):
        defaults = Config()
        self.lib.fj_default_config(C.byref(defaults))
        self.assertEqual([getattr(defaults, f) for f, _ in Config._fields_], [5000, 100, 500, 100, 500])
        for override in ({"max_episode_ms": 19}, {"max_episode_ms": 600001},
                         {"max_event_age_ms": 1001}, {"max_pair_gap_ms": 0},
                         {"max_pair_gap_ms": 5001}, {"max_sample_age_ms": 1001},
                         {"max_position_age_ms": 0}, {"max_position_age_ms": 5001}):
            with self.subTest(override=override):
                config = Config.from_buffer_copy(self.config)
                for field, value in override.items():
                    setattr(config, field, value)
                self.assertEqual(self.lib.fj_init(self.state, C.byref(config)), 0)
        self.assertEqual(self.lib.fj_init(self.state, None), 0)

    def test_both_native_event_orders_need_a_new_subject_and_emit_only_shadow_candidate(self):
        for end_first in (False, True):
            with self.subTest(end_first=end_first):
                self.restart()
                self.pair(end_first=end_first)
                subject = self.event(SUBJECT)
                result = self.step(subject)
                self.assertEqual((result.candidate_ready, result.phase, result.reason), (1, CONSUMED, OK))
                self.assertEqual(bytes(result.candidate), bytes(subject.sample))
                self.assertEqual(result.focus_event_seq, 1)
                repeated = self.send(SUBJECT)
                self.assertEqual((repeated.candidate_ready, repeated.phase, repeated.focus_event_seq),
                                 (0, CONSUMED, 1))

    def test_subject_before_result_or_end_is_not_buffered(self):
        self.begin_drive()
        self.assertEqual(self.send(SUBJECT).reason, WAITING)
        self.send(AF_RESULT)
        self.assertEqual(self.send(SUBJECT).reason, WAITING)
        self.assertEqual(self.send(DRIVE_END).candidate_ready, 0)
        self.assertEqual(self.send(TICK).candidate_ready, 0)
        self.assertEqual(self.send(SUBJECT).candidate_ready, 1)

    def test_result_end_or_subject_cannot_replace_episode_begin(self):
        for kind in (DRIVE_BEGIN, AF_RESULT, DRIVE_END, SUBJECT):
            with self.subTest(kind=kind):
                self.restart()
                result = self.send(kind)
                self.assertEqual((result.candidate_ready, result.phase, result.reason),
                                 (0, IDLE, NEEDS_BEGIN))

    def test_retry_requires_its_own_result_and_final_end(self):
        self.pair()
        self.drive += 1
        self.assertEqual(self.send(DRIVE_BEGIN).phase, WAIT_PAIR)
        self.assertEqual(self.send(SUBJECT).candidate_ready, 0)
        self.send(DRIVE_END)
        self.assertEqual(self.send(SUBJECT).candidate_ready, 0)
        self.send(AF_RESULT)
        self.assertEqual(self.send(SUBJECT).candidate_ready, 1)

    def test_retry_cannot_borrow_previous_drive_completion(self):
        self.pair()
        old_drive = self.drive
        self.drive += 1
        self.send(DRIVE_BEGIN)
        self.assert_blocked(self.send(DRIVE_END, drive_generation=old_drive), WRONG_DRIVE)
        self.assertEqual(self.send(AF_RESULT).candidate_ready, 0)

    def test_backlash_intermediate_end_is_not_final_completion(self):
        self.begin_drive()
        self.send(AF_RESULT)
        intermediate = self.send(DRIVE_END, flags=SUPPORTED | CONNECTED | POSITION_VALID | SETTLE_VALIDATED)
        self.assertEqual(intermediate.reason, INTERMEDIATE_END)
        self.assertEqual(self.send(SUBJECT).candidate_ready, 0)
        self.send(DRIVE_END)
        self.assertEqual(self.send(SUBJECT).candidate_ready, 1)

    def test_cancel_fault_and_failed_result_require_new_episode(self):
        failures = ((CANCEL, {}, CANCEL_REASON), (FAULT, {}, FAULT_REASON),
                    (AF_RESULT, {"error_code": 1}, RESULT_FAILED),
                    (AF_RESULT, {"flags": SUPPORTED | CONNECTED}, RESULT_FAILED),
                    (AF_RESULT, {"flags": SUPPORTED | CONNECTED | AF_ACCEPTED | CANCELLED}, None),
                    (AF_RESULT, {"flags": SUPPORTED | CONNECTED | AF_ACCEPTED | FAULTED}, None))
        for kind, overrides, reason in failures:
            with self.subTest(kind=kind, overrides=overrides):
                self.restart()
                self.begin_drive()
                self.assert_blocked(self.send(kind, **overrides), reason)
                self.assertEqual(self.send(DRIVE_END).candidate_ready, 0)
                self.assertEqual(self.send(AF_RESULT).candidate_ready, 0)
                self.assertEqual(self.send(SUBJECT).candidate_ready, 0)
                self.assert_blocked(self.send(BEGIN))
                self.episode += 1
                self.drive += 1
                self.pair()
                self.assertEqual(self.send(SUBJECT).candidate_ready, 1)

    def test_noop_does_not_borrow_an_earlier_end(self):
        self.begin_drive()
        self.send(DRIVE_END)
        self.assert_blocked(self.send(AF_RESULT, flags=SUPPORTED | CONNECTED | AF_ACCEPTED | NOOP),
                            NOOP_UNSUPPORTED)
        self.assertEqual(self.send(SUBJECT).candidate_ready, 0)

    def test_episode_and_context_changes_block_the_pair(self):
        for field, value, reason in (("episode_generation", 99, WRONG_EPISODE),
                                     ("target_generation", 2, CONTEXT_CHANGED),
                                     ("context_generation", 4, CONTEXT_CHANGED),
                                     ("scale_generation", 4, CONTEXT_CHANGED),
                                     ("source_kind", AAT, CONTEXT_CHANGED)):
            with self.subTest(field=field):
                self.restart()
                self.begin_drive()
                self.assert_blocked(self.send(AF_RESULT, **{field: value}), reason)
                self.assertEqual(self.send(DRIVE_END).candidate_ready, 0)

    def test_new_episode_cannot_reuse_previous_drive_generation(self):
        self.pair()
        self.assertEqual(self.send(SUBJECT).candidate_ready, 1)
        self.episode += 1
        self.send(BEGIN)
        self.assert_blocked(self.send(DRIVE_BEGIN), WRONG_DRIVE)

    def test_consumed_episode_cannot_rearm_with_another_drive(self):
        self.pair()
        self.assertEqual(self.send(SUBJECT).candidate_ready, 1)
        self.drive += 1
        self.assert_blocked(self.send(DRIVE_BEGIN), ALREADY_CONSUMED)
        self.episode += 1
        self.pair()
        self.assertEqual(self.send(SUBJECT).focus_event_seq, 2)

    def test_consumed_tracking_outlives_pending_deadlines_without_reconfirming(self):
        self.pair()
        confirmed = self.send(SUBJECT)
        self.assertEqual(confirmed.candidate_ready, 1)
        for timestamp in (10600, 16000, 30000):
            result = self.send(SUBJECT, event_ms=timestamp, now_ms=timestamp)
            self.assertEqual((result.phase, result.reason, result.candidate_ready, result.focus_event_seq),
                             (CONSUMED, ALREADY_CONSUMED, 0, 1))
        tick = self.send(TICK, episode_generation=0, drive_generation=0, target_generation=0,
                         context_generation=0, scale_generation=0, source_kind=0, flags=0)
        self.assertEqual((tick.phase, tick.reason, tick.candidate_ready), (CONSUMED, ALREADY_CONSUMED, 0))

    def test_consumed_long_tail_still_blocks_context_changes_cancel_fault_and_new_drive(self):
        for kind, overrides, reason in ((SUBJECT, {"target_generation": 99}, CONTEXT_CHANGED),
                                       (SUBJECT, {"context_generation": 99}, CONTEXT_CHANGED),
                                       (SUBJECT, {"scale_generation": 99}, CONTEXT_CHANGED),
                                       (CANCEL, {}, CANCEL_REASON), (FAULT, {}, FAULT_REASON),
                                       (DRIVE_BEGIN, {"drive_generation": 21}, ALREADY_CONSUMED)):
            with self.subTest(kind=kind, overrides=overrides):
                self.restart()
                self.pair()
                self.assertEqual(self.send(SUBJECT).candidate_ready, 1)
                self.assertEqual(self.send(SUBJECT, event_ms=16000, now_ms=16000).phase, CONSUMED)
                self.assert_blocked(self.send(kind, **overrides), reason)

    def test_forward_sequence_gap_requires_new_contiguous_begin(self):
        self.begin_drive()
        self.assert_blocked(self.send(AF_RESULT, stream_seq=self.seq + 2), LOST_EVENT)
        self.assertEqual(self.send(DRIVE_END).candidate_ready, 0)
        self.episode += 1
        self.drive += 1
        self.pair()
        self.assertEqual(self.send(SUBJECT).candidate_ready, 1)

    def test_duplicate_and_older_sequence_cannot_mutate_a_valid_pair(self):
        self.pair()
        duplicate = self.event(SUBJECT)
        duplicate.stream_seq -= 1
        duplicate.kind = CANCEL
        # The generator advanced for the constructed duplicate; restore it.
        self.seq -= 1
        for seq in (duplicate.stream_seq, duplicate.stream_seq - 1):
            duplicate.stream_seq = seq
            before = bytes(self.state)
            self.assertEqual(self.step(duplicate).reason, OLD_EVENT)
            self.assertEqual(bytes(self.state), before)
        self.assertEqual(self.send(SUBJECT).candidate_ready, 1)

    def test_repeated_source_result_and_end_cannot_refresh_the_pair_or_position(self):
        self.pair()
        self.assertEqual(self.send(AF_RESULT).reason, WAITING)
        self.assertEqual(self.send(DRIVE_END, end_position=9999).reason, WAITING)
        # Repeated end did not replace the original end position.
        self.assertEqual(self.send(SUBJECT).candidate_ready, 1)
        self.restart()
        self.pair()
        first_result_ms = self.clock - 10
        self.send(AF_RESULT, event_ms=first_result_ms + 499, now_ms=first_result_ms + 499)
        self.assert_blocked(self.send(TICK, event_ms=first_result_ms + 501, now_ms=first_result_ms + 501),
                            TIMEOUT)

    def test_missing_pair_and_missing_subject_time_out_without_events(self):
        for first in (None, AF_RESULT, DRIVE_END, "both"):
            with self.subTest(first=first):
                self.restart()
                self.begin_drive()
                if first == "both":
                    self.send(AF_RESULT)
                    first_ms = self.clock
                    self.send(DRIVE_END)
                elif first is not None:
                    self.send(first)
                    first_ms = self.clock
                else:
                    first_ms = 10010
                elapsed = self.config.max_pair_gap_ms + 1 if first is not None else self.config.max_episode_ms + 1
                self.assert_blocked(self.send(TICK, event_ms=first_ms + elapsed, now_ms=first_ms + elapsed,
                                              episode_generation=0, drive_generation=0, target_generation=0,
                                              context_generation=0, scale_generation=0, source_kind=0, flags=0),
                                    TIMEOUT)

    def test_late_second_event_blocks_even_without_a_tick(self):
        for first, second in ((AF_RESULT, DRIVE_END), (DRIVE_END, AF_RESULT)):
            with self.subTest(first=first):
                self.restart()
                self.begin_drive()
                self.send(first)
                late = self.clock + self.config.max_pair_gap_ms + 1
                self.assert_blocked(self.send(second, event_ms=late, now_ms=late), TIMEOUT)

    def test_age_and_pair_deadlines_accept_the_boundary_then_expire(self):
        self.restart(max_pair_gap_ms=50, max_position_age_ms=50,
                     max_event_age_ms=20, max_sample_age_ms=20)
        self.pair()
        # First result at 10030; end/position at 10040. All limits are inclusive.
        result = self.send(SUBJECT, event_ms=10060, now_ms=10080)
        self.assertEqual(result.candidate_ready, 1)
        self.restart(max_pair_gap_ms=50, max_position_age_ms=50,
                     max_event_age_ms=20, max_sample_age_ms=20)
        self.pair()
        self.assert_blocked(self.send(SUBJECT, event_ms=10061, now_ms=10081), TIMEOUT)

    def test_backward_or_stale_clocks_cannot_complete_a_pair(self):
        for override in ({"event_ms": 10019, "now_ms": 10030},
                         {"event_ms": 10030, "now_ms": 10019},
                         {"event_ms": 10030, "now_ms": 10131},
                         {"event_ms": 10031, "now_ms": 10030}):
            with self.subTest(override=override):
                self.restart()
                self.begin_drive()
                self.assert_blocked(self.send(AF_RESULT, **override))

    def test_position_requires_source_time_in_drive_interval_and_bounded_age(self):
        for position_ms in (10019, 10101, 10020):
            with self.subTest(position_ms=position_ms):
                self.restart(max_position_age_ms=50)
                self.begin_drive()
                self.assert_blocked(self.send(DRIVE_END, event_ms=10100, now_ms=10100,
                                              position_ms=position_ms), UNTRUSTED_POSITION)

    def test_unverified_settle_or_position_and_disconnected_end_are_rejected(self):
        for missing in (SETTLE_VALIDATED, POSITION_VALID, CONNECTED, SUPPORTED):
            with self.subTest(missing=missing):
                self.restart()
                self.begin_drive()
                flags = (SUPPORTED | CONNECTED | FINAL_END | POSITION_VALID | SETTLE_VALIDATED) & ~missing
                self.assert_blocked(self.send(DRIVE_END, flags=flags))

    def test_position_must_still_be_fresh_at_subject_time(self):
        self.restart(max_position_age_ms=30)
        self.pair()
        self.assert_blocked(self.send(SUBJECT, event_ms=10071, now_ms=10071), UNTRUSTED_POSITION)

    def test_subject_must_be_fresh_and_match_event_clock_position_and_identity(self):
        cases = ({"sample_ms": 10039}, {"sample_ms": 10049}, {"now_ms": 10051},
                 {"lens_position": 12001}, {"target_generation": 99}, {"context_generation": 99},
                 {"scale_generation": 99}, {"source_kind": AAT}, {"flags": FG_REQUIRED & ~16},
                 {"flags": FG_REQUIRED & ~1}, {"scale_q16": 0})
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.restart()
                self.pair()
                self.assert_blocked(self.send(SUBJECT, sample_overrides=overrides))
        self.restart(max_event_age_ms=100, max_sample_age_ms=20)
        self.pair()
        self.assert_blocked(self.send(SUBJECT, event_ms=10050, now_ms=10071))

    def test_aat_subject_requires_independently_asserted_scale_validation(self):
        for validated in (False, True):
            with self.subTest(validated=validated):
                self.restart()
                for kind in (BEGIN, DRIVE_BEGIN, AF_RESULT, DRIVE_END):
                    self.send(kind, source_kind=AAT)
                flags = FG_REQUIRED | (FG_AAT_VALIDATED if validated else 0)
                result = self.send(SUBJECT, source_kind=AAT, sample_overrides={"flags": flags})
                if validated:
                    self.assertEqual(result.candidate_ready, 1)
                else:
                    self.assert_blocked(result, UNTRUSTED_SUBJECT)

    def test_equal_ms_events_are_valid_but_not_repeated_confirmation(self):
        for kind in (BEGIN, DRIVE_BEGIN, AF_RESULT, DRIVE_END):
            self.send(kind, event_ms=10000, now_ms=10000, position_ms=10000)
        result = self.send(SUBJECT, event_ms=10000, now_ms=10000)
        self.assertEqual(result.candidate_ready, 1)
        self.assertEqual(self.send(SUBJECT, event_ms=10000, now_ms=10000).candidate_ready, 0)

    def test_stream_clock_episode_and_drive_wrap_are_forward(self):
        self.seq, self.clock, self.episode, self.drive = U32 - 2, U32 - 25, U32, U32
        self.pair()
        self.assertEqual(self.send(SUBJECT).candidate_ready, 1)
        self.episode, self.drive = 1, 1
        self.pair()
        self.assertEqual(self.send(SUBJECT).focus_event_seq, 2)

    def test_half_epoch_deltas_are_ambiguous_and_never_accepted_as_forward(self):
        self.pair()
        event = self.event(SUBJECT)
        event.stream_seq = ((self.seq - 1) + 0x80000000) & U32
        before = bytes(self.state)
        self.assertEqual(self.step(event).reason, OLD_EVENT)
        self.assertEqual(bytes(self.state), before)
        for field in ("episode_generation", "drive_generation", "event_ms", "now_ms"):
            with self.subTest(field=field):
                self.restart()
                self.begin_drive()
                if field == "episode_generation":
                    event = self.event(BEGIN, episode_generation=(self.episode + 0x80000000) & U32)
                elif field == "drive_generation":
                    event = self.event(DRIVE_BEGIN, drive_generation=(self.drive + 0x80000000) & U32)
                else:
                    last_ms = self.clock
                    event = self.event(AF_RESULT)
                    setattr(event, field, (last_ms + 0x80000000) & U32)
                self.assert_blocked(self.step(event))

    def test_reset_retains_stream_episode_and_drive_replay_protection(self):
        self.pair()
        old_subject = self.event(SUBJECT)
        self.assertEqual(self.step(old_subject).candidate_ready, 1)
        result = Result()
        C.memset(C.byref(result), 0xA5, C.sizeof(result))
        self.lib.fj_reset(self.state, C.byref(result))
        self.assertEqual((result.candidate_ready, result.confirmation_authorized, result.motion_authorized), (0, 0, 0))
        self.assertEqual(result.reason, RESET_REASON)
        self.assertEqual(self.step(old_subject).reason, OLD_EVENT)
        self.assert_blocked(self.send(BEGIN))
        self.episode += 1
        self.send(BEGIN)
        self.assert_blocked(self.send(DRIVE_BEGIN), WRONG_DRIVE)

    def test_unknown_kind_and_zero_generations_do_not_start_confirmation(self):
        for overrides in ({"kind": 999}, {"episode_generation": 0},
                          {"target_generation": 0}, {"context_generation": 0},
                          {"scale_generation": 0}, {"source_kind": 99}, {"flags": 0}):
            with self.subTest(overrides=overrides):
                self.restart()
                event = self.event(BEGIN)
                for field, value in overrides.items():
                    setattr(event, field, value)
                self.assert_blocked(self.step(event))


if __name__ == "__main__":
    unittest.main()
