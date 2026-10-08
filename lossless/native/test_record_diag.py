"""Bounded recording diagnostics, compiled from production C with read shims.

This tests copied observations only, not camera scheduling/media durability.
Mutations are isolated temporary source copies; shared source is never changed.
Missing compiler/build errors fail the suite rather than silently skipping it.
"""
import ctypes as ct
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
MAX = 0xFFFFFFFF
RAW, COMPLETION, TEARDOWN = 0xC038BF0C, 0xC038C0A8, 0xC0398D48


class Drop(ct.Structure):
    _fields_ = [(n, ct.c_uint32) for n in ('slot', 'generation', 'caller')] + [
        ('name', ct.c_ubyte * 48)]


class Diag(ct.Structure):
    _fields_ = [(n, ct.c_uint32) for n in (
        'event_calls', 'raw_errors', 'capture_limits', 'completion_errors',
        'lookup_missing', 'last_event', 'last_result', 'last_slot',
        'last_generation', 'last_state', 'last_handle', 'last_missing',
        'discard_calls', 'discard_count', 'discard_missing', 'discard_raw',
        'discard_completion', 'discard_teardown', 'discard_other',
        'drop_next', 'drop_used', 'drop_overwritten')] + [
        ('drops', Drop * 16)] + [(n, ct.c_uint32) for n in (
        'writer_calls', 'writer_zero', 'writer_nonzero', 'writer_low', 'writer_high')]


def build(directory, mutation=None, name='diag'):
    source = HERE / 'record_diag.c'
    if mutation:
        old, new = mutation
        text = source.read_text()
        if text.count(old) != 1:
            raise AssertionError('mutation must replace exactly one seam')
        source = Path(directory) / (name + '.c')
        source.write_text(text.replace(old, new))
    output = Path(directory) / (name + '.dylib')
    subprocess.run([shutil.which('clang') or 'clang', '-shared', '-fPIC', '-O2',
                    '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-DFPL_RECORD_DIAG_HOST_TEST', '-I', str(HERE), str(source),
                    str(HERE / 'record_diag_fixture.c'), '-o', str(output)],
                   check=True, capture_output=True, text=True, timeout=60)
    lib = ct.CDLL(str(output))
    for fn, args in (('reset', 0), ('message', 3), ('frame', 4), ('name', 2),
                     ('event', 1), ('discard', 2), ('writer', 3), ('null_reset', 0)):
        f = getattr(lib, 'fpl_diag_fixture_' + fn)
        f.argtypes = [ct.c_uint32] * args
        f.restype = None
    lib.fpl_diag_fixture_state.argtypes = []
    lib.fpl_diag_fixture_state.restype = ct.POINTER(Diag)
    lib.fpl_diag_fixture_size.argtypes = []
    lib.fpl_diag_fixture_size.restype = ct.c_uint32
    lib.fpl_diag_fixture_reads.argtypes = [ct.c_uint32]
    lib.fpl_diag_fixture_reads.restype = ct.c_uint32
    return lib


class RecordDiagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='fpl-record-diag-')
        cls.lib = build(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.lib.fpl_diag_fixture_reset()
        self.d = self.lib.fpl_diag_fixture_state().contents
        self.lib.fpl_diag_fixture_frame(7, 23, 11, 0x55000000)

    def tearDown(self):
        self.assertEqual(self.lib.fpl_diag_fixture_reads(2), 0, 'invalid native read')

    def event(self, event, result=0, slot=7, mask=0):
        self.lib.fpl_diag_fixture_message(event, result, slot)
        self.lib.fpl_diag_fixture_event(mask)

    def snapshot(self):
        return ct.string_at(ct.addressof(self.d), ct.sizeof(self.d))

    def test_01_size_and_reset_cover_whole_state(self):
        self.assertEqual(self.lib.fpl_diag_fixture_size(), ct.sizeof(Diag))
        self.assertEqual(ct.sizeof(Diag), 1068)
        ct.memset(ct.addressof(self.d), 0xA5, ct.sizeof(self.d))
        self.lib.fpl_diag_fixture_reset()
        self.assertEqual(self.snapshot(), bytes(ct.sizeof(Diag)))

    def test_02_null_diag_and_message_do_not_read_memory(self):
        before = self.snapshot()
        self.lib.fpl_diag_fixture_event(1)
        self.lib.fpl_diag_fixture_event(2)
        self.lib.fpl_diag_fixture_discard(RAW, 1)
        self.lib.fpl_diag_fixture_writer(8, 9, 1)
        self.lib.fpl_diag_fixture_null_reset()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.lib.fpl_diag_fixture_reads(0), 0)
        self.assertEqual(self.lib.fpl_diag_fixture_reads(1), 0)

    def test_03_unrelated_events_do_not_change_diagnostics(self):
        before = self.snapshot()
        for code in (0, 1, 6, 7, 10, MAX):
            self.event(code, 1)
        self.assertEqual(self.snapshot(), before)

    def test_04_success_events_are_not_writer_success(self):
        for code in (5, 8, 9):
            self.event(code)
        self.assertEqual(self.d.event_calls, 3)
        for n in ('raw_errors', 'capture_limits', 'completion_errors',
                  'lookup_missing', 'writer_calls', 'writer_nonzero'):
            self.assertEqual(getattr(self.d, n), 0, n)

    def test_05_raw_error_keeps_exact_last_fields(self):
        self.event(5, 0xFFFFFFFF)
        self.assertEqual(self.d.raw_errors, 1)
        self.assertEqual((self.d.last_event, self.d.last_result, self.d.last_slot,
                          self.d.last_generation, self.d.last_state,
                          self.d.last_handle, self.d.last_missing),
                         (5, MAX, 7, 23, 11, 0x55000000, 0))

    def test_06_capture_limit_is_separate_from_raw_error(self):
        self.event(5, 4)
        self.assertEqual(self.d.capture_limits, 1)
        self.assertEqual(self.d.raw_errors, 0)
        self.assertEqual((self.d.last_event, self.d.last_result), (5, 4))

    def test_07_completion_error_includes_both_stages(self):
        self.event(8, 1)
        self.event(9, 4)
        self.assertEqual(self.d.completion_errors, 2)
        self.assertEqual(self.d.capture_limits, 0)
        self.assertEqual((self.d.last_event, self.d.last_result), (9, 4))

    def test_08_missing_lookup_is_recorded_even_without_error_result(self):
        self.event(9, 0, MAX, 4)
        self.assertEqual(self.d.lookup_missing, 1)
        self.assertEqual(self.d.completion_errors, 0)
        self.assertEqual((self.d.last_slot, self.d.last_generation, self.d.last_state,
                          self.d.last_handle, self.d.last_missing), (MAX, MAX, MAX, 0, 1))
        self.assertEqual(self.lib.fpl_diag_fixture_reads(0), 3)

    def test_09_missing_capture_limit_has_both_observations(self):
        self.event(5, 4, MAX, 4)
        self.assertEqual((self.d.capture_limits, self.d.lookup_missing,
                          self.d.raw_errors), (1, 1, 0))

    def test_10_success_does_not_overwrite_last_error(self):
        self.event(5, 1, 7)
        last = (self.d.last_event, self.d.last_result, self.d.last_slot)
        self.event(9, 0, 91)
        self.assertEqual((self.d.last_event, self.d.last_result, self.d.last_slot), last)

    def test_11_discard_copies_fixed_name_bytes_without_requiring_nul(self):
        name = bytes(range(48))
        for n, b in enumerate(name):
            self.lib.fpl_diag_fixture_name(n, b)
        self.lib.fpl_diag_fixture_discard(RAW, 0)
        drop = self.d.drops[0]
        self.assertEqual((drop.slot, drop.generation, drop.caller), (7, 23, RAW))
        self.assertEqual(bytes(drop.name), name)
        self.assertEqual(self.lib.fpl_diag_fixture_reads(1), 48)
        self.assertEqual((self.d.discard_calls, self.d.discard_count,
                          self.d.discard_raw, self.d.drop_used), (1, 1, 1, 1))

    def test_12_discard_groups_only_exact_native_callers(self):
        for caller in (RAW, COMPLETION, TEARDOWN, 0xC037DEFC, 0):
            self.lib.fpl_diag_fixture_discard(caller, 0)
        self.assertEqual((self.d.discard_raw, self.d.discard_completion,
                          self.d.discard_teardown, self.d.discard_other), (1, 1, 1, 2))
        self.assertEqual(self.d.discard_count, 5)

    def test_13_null_discard_is_not_a_dropped_frame(self):
        self.lib.fpl_diag_fixture_discard(RAW, 4)
        self.assertEqual((self.d.discard_calls, self.d.discard_missing,
                          self.d.discard_count, self.d.drop_used), (1, 1, 0, 0))
        self.assertEqual(self.lib.fpl_diag_fixture_reads(0), 0)
        self.assertEqual(self.lib.fpl_diag_fixture_reads(1), 0)

    def test_14_recent_drop_window_wraps_after_sixteen(self):
        for n in range(19):
            self.lib.fpl_diag_fixture_frame(n, 23, 11, 0x55000000)
            self.lib.fpl_diag_fixture_discard(RAW, 0)
        self.assertEqual((self.d.discard_count, self.d.drop_used,
                          self.d.drop_next, self.d.drop_overwritten), (19, 16, 3, 3))
        chronological = [self.d.drops[(self.d.drop_next + n) % 16].slot for n in range(16)]
        self.assertEqual(chronological, list(range(3, 19)))

    def test_15_writer_uses_both_halves_and_keeps_exact_return(self):
        for low, high in ((0, 0), (8, 0), (0, 1), (MAX, MAX)):
            self.lib.fpl_diag_fixture_writer(low, high, 0)
            self.assertEqual((self.d.writer_low, self.d.writer_high), (low, high))
        self.assertEqual((self.d.writer_calls, self.d.writer_zero,
                          self.d.writer_nonzero), (4, 1, 3))

    def test_16_counters_saturate_without_stopping_new_observations(self):
        names = ('event_calls', 'raw_errors', 'capture_limits', 'completion_errors',
                 'lookup_missing', 'discard_calls', 'discard_count', 'discard_missing',
                 'discard_raw', 'discard_completion', 'discard_teardown', 'discard_other',
                 'drop_overwritten', 'writer_calls', 'writer_zero', 'writer_nonzero')
        for name in names:
            setattr(self.d, name, MAX)
        self.d.drop_used = 16
        self.event(5, 1, 3, 4)
        self.event(5, 4)
        self.event(9, 1)
        for caller in (RAW, COMPLETION, TEARDOWN, 0):
            self.lib.fpl_diag_fixture_discard(caller, 0)
        self.lib.fpl_diag_fixture_discard(RAW, 4)
        self.lib.fpl_diag_fixture_writer(0, 0, 0)
        self.lib.fpl_diag_fixture_writer(0, 1, 0)
        for name in names:
            self.assertEqual(getattr(self.d, name), MAX, name)
        self.assertEqual((self.d.writer_low, self.d.writer_high), (0, 1))
        self.assertEqual(self.d.drop_next, 4)

    def test_17_mutant_ignoring_writer_high_half_is_detected(self):
        lib = build(self.tmp.name, ('if (low || high)', 'if (low)'), 'writer-mutant')
        lib.fpl_diag_fixture_reset()
        lib.fpl_diag_fixture_writer(0, 1, 0)
        with self.assertRaises(AssertionError):
            self.assertEqual(lib.fpl_diag_fixture_state().contents.writer_nonzero, 1)

    def test_18_mutant_merging_capture_limit_into_raw_error_is_detected(self):
        lib = build(self.tmp.name, ('if (result == 4)', 'if (result == 3)'), 'limit-mutant')
        lib.fpl_diag_fixture_reset()
        lib.fpl_diag_fixture_message(5, 4, 7)
        lib.fpl_diag_fixture_event(0)
        with self.assertRaises(AssertionError):
            self.assertEqual(lib.fpl_diag_fixture_state().contents.raw_errors, 0)


if __name__ == '__main__':
    unittest.main()
