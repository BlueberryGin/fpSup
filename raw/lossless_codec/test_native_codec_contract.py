#!/usr/bin/env python3
"""Strict original-instruction codec ABI tests; offline, never a DMA proof.

Runs F_INIT/F_ENC/F_SIZE from seg0. OS flags, open/submit/start and power/clock
boundaries are explicit substitutes. A submit substitute writes synthetic JPEG
marker fixtures, not an encoded image. Real postprocessing and close execute.
Unknown execution fails. The child canary is this workload, not a JIT toy;
unavailable emulation is an error (zero tests skipped), never a passing result.

Entry/exit SP is eight-byte aligned; the pinned legacy firmware itself uses
four-byte alignment at some nested calls. That observed map is tested, not
silently normalized into a claim that every native boundary is AAPCS-aligned.
Production adapter code must still supply its own eight-byte-aligned entry.
"""
import pathlib
import hashlib
import struct
import subprocess
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
IMAGE = HERE.parents[2] / 'out' / 'seg0_c0000000.bin'
IMAGE_SHA256 = 'aaa5208a028d9c4aebb9cc8614add723d456e96b2a95914f433079954320e622'
ROM = 0xC0000000
ARG = 0x45000000
SOURCE = 0x45100000
DEST = 0x45800000
TABLE = 0x45C00000
STACK = 0x46010000
STOP = 0x47000000
ENGINE = 0xC302CDD4
FLAG = 0xC2F2E60C
OWNER = 0xC37CF87C
INIT, ENCODE, SIZES = 0xC05A6890, 0xC05A6920, 0xC05A6990


class NativeCodec:
    def __init__(self, image):
        from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE,
                             UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC)
        from unicorn import arm_const
        self.reg = arm_const
        self.mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.mu.mem_map(ROM, 0x3000000)
        self.mu.mem_write(ROM, image)
        self.mu.mem_protect(ROM, 0x3000000, UC_PROT_READ | UC_PROT_EXEC)
        rw = UC_PROT_READ | UC_PROT_WRITE
        # The ROM dump contains the lazy event-flag global. Only its page is
        # writable/NX; all runtime BSS and synthetic RAM are also NX.
        self.mu.mem_protect(FLAG & ~4095, 4096, rw)
        self.mu.mem_map(0xC3000000, 0x800000, rw)
        self.mu.mem_map(ARG, 0x1000, rw)
        self.mu.mem_map(SOURCE, 0x400000, rw)
        self.mu.mem_map(DEST, 0x400000, rw)
        self.mu.mem_map(TABLE, 0x2000, rw)
        self.mu.mem_map(STACK - 0x10000, 0x10000, rw)
        self.mu.mem_map(STOP, 0x1000, UC_PROT_READ | UC_PROT_EXEC)
        self.mu.mem_map(0x300D0000, 0x1000, rw)
        self.mu.hook_add(UC_HOOK_CODE, self.step)
        self.ranges = [(INIT, 0xC05A69DC), (0xC03D9668, 0xC03D97C4),
                       (0xC062F478, 0xC062F50A),
                       (0xC062F5A8, 0xC062FAE0),
                       (0xC062FCF8, 0xC062FEE8),
                       (0xC062FF68, 0xC062FFF8)]
        self.calls, self.trace, self.requests = [], [], []
        self.stub_sp = []
        self.wait_results = [(0, 1)]
        self.waits = 0
        self.returned = False
        self.boundary_stops = set()
        self.boundary_reached = None
        self.close_result = 0
        self.submit_result = 0
        self.start_result = 0
        self.open_result = 0
        self.flag_result = 150
        self.stubs = {
            0xC0015058: self.zero,
            0xC001510C: self.copy,
            0xC062F440: self.flag,
            0xC00169C8: lambda: self.ret(0),
            0xC062FEE8: lambda: self.event_return('open', self.open_result),
            0xC062FFF8: self.submit,
            0xC0630208: lambda: self.event_return('start', self.start_result),
            0xC0016C08: self.wait,
            0xC00158A4: self.divide,
            0xC06304F0: lambda: self.event_return('reset-close', 0),
            0xC000FCA0: self.irq_disable,
            0xC0630480: self.device_disable,
            0xC07095A8: self.clock_disable,
            0xC0708C60: self.power_disable,
        }

    def word(self, address):
        return struct.unpack('<I', self.mu.mem_read(address, 4))[0]

    def put(self, address, value):
        self.mu.mem_write(address, struct.pack('<I', value & 0xFFFFFFFF))

    def r(self, name):
        return self.mu.reg_read(getattr(self.reg, 'UC_ARM_REG_' + name.upper()))

    def setr(self, name, value):
        self.mu.reg_write(getattr(self.reg, 'UC_ARM_REG_' + name.upper()), value)

    def ret(self, value=0):
        self.setr('r0', value & 0xFFFFFFFF)
        self.setr('pc', self.r('lr'))

    def event_return(self, name, value):
        self.calls.append(name)
        self.ret(value)

    def owned(self, address, length):
        if length > 4096 or not any(a <= address <= z - length for a, z in (
                (ARG, ARG + 4096), (DEST, DEST + 0x400000),
                (TABLE, TABLE + 8192), (STACK - 0x10000, STACK),
                (0xC37CF884, 0xC37CF8B4))):
            raise AssertionError('stub memory access outside bounded fixture')

    def zero(self):
        address, fill, length = (self.r('r' + str(i)) for i in range(3))
        self.owned(address, length)
        if fill != 0:
            raise AssertionError('nonzero memset not authorized')
        self.mu.mem_write(address, bytes(length))
        self.ret(address)

    def copy(self):
        destination, source, length = (self.r('r' + str(i)) for i in range(3))
        self.owned(destination, length)
        self.owned(source, length)
        self.mu.mem_write(destination, bytes(self.mu.mem_read(source, length)))
        self.ret(destination)

    def flag(self):
        self.put(FLAG, self.flag_result)
        self.ret(self.flag_result)

    def divide(self):
        numerator = self.r('r0') | self.r('r1') << 32
        denominator = self.r('r2') | self.r('r3') << 32
        if denominator != 32000:
            raise AssertionError('unexpected timeout divisor')
        quotient = numerator // denominator
        self.setr('r1', quotient >> 32)
        self.ret(quotient)

    def submit(self):
        address = self.r('r0')
        self.owned(address, 48)
        request = struct.unpack('<12I', self.mu.mem_read(address, 48))
        self.requests.append(request)
        self.calls.append('submit')
        if self.submit_result:
            self.ret(self.submit_result)
            return
        if request[7] != DEST or request[9] != TABLE + 1024:
            raise AssertionError('unexpected encoder output fixture')
        tiles = ((request[0] + request[3] - 1) // request[3] *
                 ((request[1] + request[4] - 1) // request[4]))
        if not 1 <= tiles <= 160:
            raise AssertionError('refusing empty/excess synthetic tile set')
        # Underreport one tile and overreport the next by two bytes, and
        # underreport the final tile by four. Native EOI discovery must fix
        # each boundary; final KiB rounding alone would hide the last defect.
        sizes = [64] * tiles
        if tiles > 1:
            sizes[0], sizes[1] = 62, 66
        sizes[-1] = 60
        self.mu.mem_write(TABLE + 1024, struct.pack('>' + 'I' * tiles, *sizes))
        self.mu.mem_write(DEST, (b'\xff\xd8' + bytes(60) + b'\xff\xd9') * tiles)
        self.put(OWNER, 1)
        self.ret(0)

    def wait(self):
        self.calls.append('wait')
        if (self.r('r0'), self.r('r1'), self.r('r2')) != (150, 5, 1):
            raise AssertionError('unexpected event wait ABI')
        if self.word(self.r('sp')) < 100:
            raise AssertionError('native timeout floor was lost')
        result, pattern = self.wait_results[min(self.waits, len(self.wait_results) - 1)]
        self.waits += 1
        if result == 0:
            self.put(self.r('r3'), pattern)
        self.ret(result)

    def irq_disable(self):
        if (self.r('r0'), self.r('r1')) != (0x29, 0):
            raise AssertionError('unexpected IRQ call')
        self.event_return('irq-disable', 0)

    def device_disable(self):
        if self.r('r0') != 0:
            raise AssertionError('unexpected device-disable argument')
        self.event_return('device-disable', 0)

    def clock_disable(self):
        if (self.r('r0'), self.r('r1')) != (5, 0):
            raise AssertionError('unexpected clock-disable argument')
        self.event_return('clock-disable', self.close_result)

    def power_disable(self):
        if self.r('r0') != 0:
            raise AssertionError('unexpected power-disable argument')
        self.event_return('power-disable', 0)

    def step(self, _mu, address, _size, _user):
        self.trace.append(address)
        if address == STOP:
            self.returned = True
            self.mu.emu_stop()
        elif address in self.boundary_stops:
            self.boundary_reached = address
            self.mu.emu_stop()
        elif address in self.stubs:
            self.stub_sp.append((address, self.r('sp') & 7))
            if self.r('sp') & 3:
                raise AssertionError('native stub boundary SP lost word alignment')
            self.stubs[address]()
        elif not any(a <= address < z for a, z in self.ranges):
            raise AssertionError('unapproved firmware execution: %#x' % address)

    def run(self, address, *args, budget=20000, permit_budget_stop=False):
        self.returned = False
        self.setr('sp', STACK - 0x100)
        self.setr('lr', STOP)
        saved = {}
        for i in range(4, 12):
            saved['r' + str(i)] = 0x11110000 + i
            self.setr('r' + str(i), saved['r' + str(i)])
        for i, value in enumerate(args):
            self.setr('r' + str(i), value)
        self.mu.emu_start(address, 0, count=budget)
        if not self.returned:
            if permit_budget_stop:
                return None
            raise AssertionError('instruction budget exhausted')
        if self.r('sp') != STACK - 0x100:
            raise AssertionError('stack not restored')
        for name, value in saved.items():
            if self.r(name) != value:
                raise AssertionError('callee-saved register not restored: ' + name)
        return self.r('r0')

    def initialize(self, depth=0, source=SOURCE, destination=DEST, width=1936, height=1090):
        words = (width, height, depth, 512, 368, source, destination, TABLE, 0)
        self.mu.mem_write(ARG, struct.pack('<9I', *words))
        return self.run(INIT, ARG)

    def encode(self, **kwargs):
        return self.run(ENCODE, ARG, **kwargs)

    def validate_request(self, request):
        # Stop before the first MMIO read/write, after the actual validator.
        self.mu.mem_write(ARG + 0x200, struct.pack('<12I', *request))
        self.stubs.pop(0xC062FFF8)
        self.ranges.append((0xC062FFF8, 0xC0630208))
        self.boundary_stops.add(0xC06300C8)
        return self.run(0xC062FFF9, ARG + 0x200, permit_budget_stop=True)


def workload(image):
    runner = NativeCodec(image)
    assert runner.initialize() == 1
    assert runner.encode() == 1
    assert runner.run(SIZES, ARG + 0x100, 1) == 1
    return runner


def pinned_image():
    image = IMAGE.read_bytes()
    if hashlib.sha256(image).hexdigest() != IMAGE_SHA256:
        raise RuntimeError('unexpected firmware image SHA-256; unverified')
    return image


class NativeCodecContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not IMAGE.is_file():
            raise RuntimeError('firmware image unavailable: unverified')
        child = subprocess.run([sys.executable, '-B', __file__, '--selfcheck'],
                               capture_output=True, text=True, timeout=60)
        if child.returncode or 'SELFCHECK OK' not in child.stdout:
            raise RuntimeError('actual codec workload unavailable; exit %s: %s' %
                               (child.returncode, child.stderr[-2000:]))
        cls.image = pinned_image()

    def test_init_preserves_supplied_pointer_aliases(self):
        for source, dest in ((SOURCE, DEST), (SOURCE + 0x40000000, DEST + 0x40000000)):
            runner = NativeCodec(self.image)
            self.assertEqual(runner.initialize(source=source, destination=dest), 1)
            self.assertEqual(runner.word(ENGINE), source)
            self.assertEqual(runner.word(ENGINE + 8), dest)
            self.assertEqual(runner.word(ENGINE + 16), TABLE)

    def test_all_four_depths_round_source_and_destination_lengths_up(self):
        for depth, bits in ((0, 12), (1, 14), (2, 16), (3, 10)):
            runner = NativeCodec(self.image)
            self.assertEqual(runner.initialize(depth), 1)
            expected = ((1936 * 1090 * bits // 8) + 1023) & ~1023
            self.assertEqual(runner.word(ENGINE + 4), expected)
            self.assertEqual(runner.word(ENGINE + 12), expected)
            self.assertEqual(runner.word(ENGINE + 20), 12 * 4)

    def test_source_tail_is_not_proven_by_stock_file_length(self):
        runner = NativeCodec(self.image)
        runner.initialize()
        raw = 1936 * 1090 * 12 // 8
        declared = runner.word(ENGINE + 4)
        self.assertGreaterEqual(declared, raw)
        self.assertEqual(0x13400 + declared - 0x318200, 512)
        self.assertEqual(raw - (raw & ~1023), 176)

    def test_request_validator_requires_all_five_kib_aligned_fields(self):
        request = [1936, 1090, 0, 512, 368, SOURCE, 3166208,
                   DEST, 0x400000, TABLE, 48, 0xC062FCB1]
        runner = NativeCodec(self.image)
        self.assertIsNone(runner.validate_request(request))
        self.assertEqual(runner.boundary_reached, 0xC06300C8)
        for index in (5, 6, 7, 8, 9):
            changed = request[:]
            changed[index] += 4
            runner = NativeCodec(self.image)
            self.assertEqual(runner.validate_request(changed), 1)
            self.assertIsNone(runner.boundary_reached)

    def test_original_driver_zero_height_at_exact_tile_row_multiple(self):
        # Native arithmetic uses remainder without the host plan()'s `or full`.
        # This is an additional all-format gate, not a codec-content diagnosis.
        runner = NativeCodec(self.image)
        runner.initialize(width=1536, height=736)
        runner.submit_result = 1  # request captured; no synthetic DMA at h=0
        self.assertEqual(runner.encode(), 0)
        self.assertEqual(runner.requests[0][1], 0)
        self.assertEqual(runner.requests[0][6], 0)
        validator = NativeCodec(self.image)
        self.assertEqual(validator.validate_request(runner.requests[0]), 1)
        self.assertIsNone(validator.boundary_reached)

    def test_mutated_source_alignment_gate_is_detected(self):
        request = [1936, 1090, 0, 512, 368, SOURCE + 4, 3166208,
                   DEST, 0x400000, TABLE, 48, 0xC062FCB1]
        runner = NativeCodec(self.image)
        runner.mu.mem_write(0xC06300AA, b'\x00\xbf')
        with self.assertRaises(AssertionError):
            self.assertEqual(runner.validate_request(request), 1)

    def test_native_success_corrects_tile_end_closes_and_returns_one(self):
        runner = workload(self.image)
        self.assertEqual(runner.requests[0][6], 3166208)
        self.assertEqual(runner.requests[0][11], 0xC062FCB1)
        self.assertIn(0xC062F6C0, runner.trace)
        self.assertIn(0xC062FCF8, runner.trace)
        self.assertIn(0xC062FF68, runner.trace)
        self.assertEqual(runner.word(OWNER), 0)
        self.assertEqual(runner.calls[-5:], ['reset-close', 'irq-disable',
                                           'device-disable', 'clock-disable', 'power-disable'])
        sizes = struct.unpack('<12I', runner.mu.mem_read(ARG + 0x100, 48))
        self.assertEqual(sizes, (64,) * 11 + (320,))
        self.assertEqual(sum(sizes), 1024)

    def test_legacy_native_stub_stack_alignment_map_is_explicit(self):
        runner = workload(self.image)
        self.assertEqual(set(runner.stub_sp), {
            (0xC0015058, 0), (0xC0015058, 4), (0xC001510C, 0),
            (0xC00158A4, 0), (0xC00169C8, 0), (0xC0016C08, 0),
            (0xC062F440, 0), (0xC062FEE8, 0), (0xC062FFF8, 4),
            (0xC0630208, 0), (0xC06304F0, 4), (0xC000FCA0, 4),
            (0xC0630480, 4), (0xC07095A8, 4), (0xC0708C60, 4),
        })

    def test_timeout_retries_start_without_resubmitting(self):
        runner = NativeCodec(self.image)
        runner.initialize()
        runner.wait_results = [(-50, 0), (-50, 0), (0, 1)]
        self.assertEqual(runner.encode(), 1)
        self.assertEqual(runner.calls.count('submit'), 1)
        self.assertEqual(runner.calls.count('start'), 3)
        self.assertEqual(runner.waits, 3)

    def test_repeated_timeout_has_no_native_bounded_exit(self):
        runner = NativeCodec(self.image)
        runner.initialize()
        runner.wait_results = [(-50, 0)]
        self.assertIsNone(runner.encode(budget=6000, permit_budget_stop=True))
        self.assertGreater(runner.waits, 10)
        self.assertNotIn('reset-close', runner.calls)
        self.assertEqual(runner.word(OWNER), 1)

    def test_error_pattern_closes_without_postprocessing(self):
        runner = NativeCodec(self.image)
        runner.initialize()
        runner.wait_results = [(0, 4)]
        self.assertEqual(runner.encode(), 0)
        self.assertIn('power-disable', runner.calls)
        self.assertNotIn(0xC062FCF8, runner.trace)

    def test_non_timeout_wait_failure_does_not_close(self):
        runner = NativeCodec(self.image)
        runner.initialize()
        runner.wait_results = [(-42, 0)]
        self.assertEqual(runner.encode(), 0)
        self.assertNotIn('reset-close', runner.calls)
        self.assertEqual(runner.word(OWNER), 1)

    def test_partial_close_failure_is_not_success_even_with_owner_zero(self):
        runner = NativeCodec(self.image)
        runner.initialize()
        runner.close_result = 1
        self.assertEqual(runner.encode(), 0)
        self.assertEqual(runner.word(OWNER), 0)
        self.assertNotIn('power-disable', runner.calls)

    def test_failed_submit_and_start_do_not_invent_cleanup(self):
        for field in ('submit_result', 'start_result'):
            runner = NativeCodec(self.image)
            runner.initialize()
            setattr(runner, field, 1)
            self.assertEqual(runner.encode(), 0)
            self.assertNotIn('reset-close', runner.calls)

    def test_mutating_rounding_postprocess_and_close_is_detected(self):
        # Fresh private emulated bytes for every mutation; firmware untouched.
        runner = NativeCodec(self.image)
        runner.mu.mem_write(0xC062F5E6, b'\x00\xbf\x00\xbf')
        with self.assertRaises(AssertionError):
            self.assertEqual(runner.initialize(), 1)
            self.assertEqual(runner.word(ENGINE + 4), 3166208)
        runner = NativeCodec(self.image)
        runner.initialize()
        runner.mu.mem_write(0xC062F92A, b'\x00\xbf\x00\xbf')
        runner.encode()
        runner.run(SIZES, ARG + 0x100, 1)
        with self.assertRaises(AssertionError):
            self.assertEqual(runner.word(ARG + 0x100), 64)
        runner = NativeCodec(self.image)
        runner.initialize()
        runner.mu.mem_write(0xC062FA1A, b'\x00\xbf\x00\xbf')
        runner.encode()
        with self.assertRaises(AssertionError):
            self.assertEqual(runner.word(OWNER), 0)

    def test_unknown_call_fails_loudly(self):
        runner = NativeCodec(self.image)
        with self.assertRaisesRegex(AssertionError, 'unapproved firmware'):
            runner.run(0xC0011100)


if __name__ == '__main__':
    if '--selfcheck' in sys.argv:
        workload(pinned_image())
        print('SELFCHECK OK')
    else:
        unittest.main(verbosity=2)
