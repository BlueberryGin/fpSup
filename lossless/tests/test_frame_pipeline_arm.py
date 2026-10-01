"""Execute the actual frame_pipeline.c as ARM, with a synthetic handoff only.

The checked research ELF extractor rejects relocations: this is not a linker,
BIN builder, firmware adapter or camera test. Code is RX, other mappings are
emulator-owned RAM. Every external PC except the handoff/return is rejected.
All JIT execution lives in a child, so a host SIGILL becomes a clear failure,
not a crashed discovery process or a misleading skipped/passing ARM result.
"""
import hashlib
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
HELPER = HERE.parents[1] / 'research/ui/tools/arm_text/elf_text.py'
OK, INVALID, BUSY, UNSUPPORTED, NOT_READY, FAULT = range(6)
FREE, HELD, ENCODING, READY, COMMITTING, RETAINED = range(6)
READY_RAW = 6
RAW, SELECTED = 1, 2
CODE, RAM, STACK, STOP = 0x30000000, 0x45000000, 0x46010000, 0x47000000
HANDOFF, FOREIGN = STOP + 0x100, STOP + 0x200
P, CONTROL, FRAME, TOKEN, OUTPUT, ACTION = (
    RAM, RAM + 0x100, RAM + 0x200, RAM + 0x300, RAM + 0x340, RAM + 0x380)
SOURCE, LATER, META, WRITER, ENCODED = (
    RAM + 0x1000, RAM + 0x2000, RAM + 0x3000, RAM + 0x3400, RAM + 0x4000)
LEASE_OFFSET, OUTPUT_OFFSET = 52, 84


def compile_text(temp, *, mutation=False):
    clang = shutil.which('clang')
    if not clang:
        raise RuntimeError('clang required; ARM compilation was not performed')
    spec = importlib.util.spec_from_file_location('pipeline_checked_arm_text', HELPER)
    elf = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = elf
    spec.loader.exec_module(elf)
    source = (HERE / 'frame_pipeline.c').read_text()
    if mutation:
        before = 'proofs != FPL_HANDOFF_ALL'
        if source.count(before) != 1:
            raise AssertionError('output-proof mutation requires one exact guard')
        source = source.replace(before, 'proofs != FPL_HANDOFF_SOURCE')
    # Independent ARM32 layout assumptions used by this runner must fail at
    # compile time if the actual public C ABI changes.
    source += '''
#include <stddef.h>
_Static_assert(sizeof(uintptr_t) == 4, "ARM32 pointers");
_Static_assert(sizeof(struct fpl_pipeline) == 100, "pipeline ABI");
_Static_assert(offsetof(struct fpl_pipeline, lease) == 52, "lease ABI");
_Static_assert(offsetof(struct fpl_pipeline, output) == 84, "output ABI");
_Static_assert(sizeof(struct fpl_frame_lease) == 32, "lease size");
_Static_assert(offsetof(struct fpl_frame_lease, stock_file_bytes) == 28, "file bytes");
'''
    obj = Path(temp) / ('missing-output-proof.o' if mutation else 'pipeline.o')
    subprocess.run([clang, '--target=armv7-none-eabi', '-mcpu=cortex-a9',
                    '-marm', '-mfloat-abi=soft', '-mfpu=none', '-std=c11', '-O2',
                    '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                    '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra', '-Werror',
                    '-I', str(HERE), '-x', 'c', '-c', '-', '-o', str(obj)],
                   input=source, text=True, check=True, capture_output=True, timeout=30)
    return elf.load_text(obj)  # Do not remove relocations to make this succeed.


class ArmPipeline:
    def __init__(self, text, enabled=True):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
        from unicorn import UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC, arm_const
        self.text, self.reg = text, arm_const
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        extent = (len(text.code) + 4095) & ~4095
        self.uc.mem_map(CODE, extent, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_write(CODE, text.code)
        self.uc.mem_protect(CODE, extent, UC_PROT_READ | UC_PROT_EXEC)
        self.uc.mem_map(RAM, 0x10000, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_map(STACK - 0x10000, 0x10000, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_map(STOP, 0x1000, UC_PROT_READ | UC_PROT_EXEC)
        self.uc.hook_add(UC_HOOK_CODE, self.instruction)
        self.proofs, self.callback_result = 3, OK
        self.callback_seen, self.expected = [], None
        self.raw_handoff = False
        self.write(CONTROL, 0x46504C53, 1, int(enabled), 2 if enabled else 1, 0, 0, 0, 0)
        if self.call('init', P, 71) != OK:
            raise AssertionError('ARM pipeline initialization failed')
        if self.call('begin', P, CONTROL) != OK:
            raise AssertionError('ARM pipeline begin failed')

    def r(self, name):
        return self.uc.reg_read(getattr(self.reg, 'UC_ARM_REG_' + name.upper()))

    def setr(self, name, value):
        self.uc.reg_write(getattr(self.reg, 'UC_ARM_REG_' + name.upper()), value)

    def write(self, address, *values):
        self.uc.mem_write(address, struct.pack('<' + 'I' * len(values), *values))

    def words(self, address, count=1):
        return struct.unpack('<' + 'I' * count, self.uc.mem_read(address, 4 * count))

    def field(self, index):
        return self.words(P + index * 4)[0]

    def instruction(self, _uc, pc, size, _data):
        if CODE <= pc and pc + size <= CODE + len(self.text.code):
            return
        if pc == STOP:
            self.stopped = True
            self.uc.emu_stop()
            return
        if pc != HANDOFF:
            raise AssertionError(f'unapproved external PC: {pc:#010x}')
        if self.r('sp') % 8:
            raise AssertionError('handoff entered with unaligned SP')
        args = tuple(self.r(f'r{i}') for i in range(4))
        expected_output = 0 if self.raw_handoff else P + OUTPUT_OFFSET
        if args[:3] != (RAM + 0xF000, P + LEASE_OFFSET, expected_output):
            raise AssertionError('callback did not receive saved source/output')
        if not STACK - 0x10000 <= args[3] <= STACK - 4:
            raise AssertionError('proof output is not owned caller stack')
        if self.field(7) != COMMITTING:
            raise AssertionError('handoff can be reentered before ownership transfer')
        lease = self.words(args[1], 8)
        output = self.words(args[2], 4) if args[2] else None
        if lease != self.expected:
            raise AssertionError('handoff substituted a later frame or file identity')
        # Synthetic copy-back, deliberately not a DNG/native writer emulator.
        # The test checks that only the selected native buffer is changed.
        if output is not None:
            self.uc.mem_write(lease[3], bytes(self.uc.mem_read(output[0], output[1])))
        self.callback_seen.append((lease, output))
        self.write(args[3], self.proofs)
        self.setr('r0', self.callback_result)
        self.setr('pc', self.r('lr'))

    def call(self, name, *args):
        saved = {f'r{i}': 0xAABB0000 + i for i in range(4, 12)}
        for register, value in saved.items():
            self.setr(register, value)
        self.stopped = False
        sp = STACK - 0x100
        self.setr('sp', sp)
        self.setr('lr', STOP)
        for index, value in enumerate(args):
            if index < 4:
                self.setr(f'r{index}', value)
            else:
                self.write(sp + 4 * (index - 4), value)
        entry = CODE + self.text.functions['fpl_pipeline_' + name]
        self.uc.emu_start(entry, 0, timeout=250000, count=20000)
        if not self.stopped:
            raise AssertionError('bounded ARM call did not return')
        if self.r('sp') != sp or any(self.r(k) != v for k, v in saved.items()):
            raise AssertionError('ARM pipeline clobbered SP or r4-r11')
        return self.r('r0')

    def frame(self, number, source=SOURCE, valid=True):
        self.write(FRAME, 71, self.field(2), number,
                   source if valid else 0, META if valid else 0,
                   WRITER if valid else 0, 4096 if valid else 0, 2048 if valid else 0)
        if valid:
            self.uc.mem_write(source, bytes([number % 251]) * 2048)
            self.uc.mem_write(META, ('source-%d\0' % number).encode())
            self.write(WRITER, number)

    def arrive(self, held=1, deferred=1):
        result = self.call('arrive', P, FRAME, held, deferred, ACTION)
        return result, self.words(ACTION)[0]

    def select(self, number):
        self.frame(number)
        self.expected = self.words(FRAME, 8)
        if self.arrive() != (OK, SELECTED):
            raise AssertionError('ARM pipeline failed to select the source')
        self.write(TOKEN, *self.expected[:3])
        if self.call('submitted', P, TOKEN, OK) != OK:
            raise AssertionError('ARM pipeline failed to submit the source')

    def complete(self):
        self.uc.mem_write(ENCODED, b'encoded-original-source-' * 16)
        self.write(OUTPUT, ENCODED, 256, 512, 1)
        return self.call('complete', P, TOKEN, OK, 1, OUTPUT)

    def handoff(self, callback=HANDOFF):
        return self.call('handoff', P, TOKEN, callback, RAM + 0xF000)


def worker_suite(text, mutant):
    class ExecutedArmTests(unittest.TestCase):
        def test_continuous_busy_pass_same_source_handoff_next_arrival(self):
            h = ArmPipeline(text)
            for frame in range(1, 193, 3):  # 64 selected frames, past the old 40 cap.
                h.select(frame)
                metadata = bytes(h.uc.mem_read(META, 32))
                for later in (frame + 1, frame + 2):
                    h.frame(later, LATER, valid=False)
                    h.uc.mem_write(LATER, b'later-raw' * 256)
                    self.assertEqual(h.arrive(0, 0), (OK, RAW))
                later_bytes = bytes(h.uc.mem_read(LATER, 2048))
                h.uc.mem_write(FRAME, bytes(32))  # caller descriptor is disposable.
                self.assertEqual(h.complete(), OK)
                self.assertEqual(h.handoff(), OK)
                self.assertEqual(bytes(h.uc.mem_read(SOURCE, 256)),
                                 bytes(h.uc.mem_read(ENCODED, 256)))
                self.assertEqual(bytes(h.uc.mem_read(LATER, 2048)), later_bytes)
                self.assertEqual(bytes(h.uc.mem_read(META, 32)), metadata)
                self.assertEqual(h.words(WRITER)[0], frame)
                self.assertEqual(h.field(7), FREE)
            self.assertEqual(h.words(P + 36, 4), (192, 128, 64, 64))

        def proof_scenario(self, compiled):
            h = ArmPipeline(compiled)
            h.select(1)
            self.assertEqual(h.complete(), OK)
            h.proofs = 1  # native source transferred, codec output still borrowed.
            self.assertEqual(h.handoff(), FAULT)
            self.assertEqual(h.field(7), RETAINED)
            self.assertEqual(h.words(P + LEASE_OFFSET, 8), h.expected)
            self.assertEqual(h.words(P + OUTPUT_OFFSET)[0], ENCODED)
            self.assertEqual(h.call('reap', P, TOKEN, 0, 1), BUSY)
            self.assertEqual(h.call('reap', P, TOKEN, 1, 1), OK)

        def test_output_release_proof_is_required(self):
            self.proof_scenario(text)

        def test_isolated_source_mutation_losing_output_proof_is_caught(self):
            h = ArmPipeline(mutant)
            h.select(1)
            self.assertEqual(h.complete(), OK)
            h.proofs = 1
            returned = h.handoff()
            # Establish the mutation's precise unsafe effect, not just some
            # unrelated assertion or emulator error inside a broad try block.
            self.assertEqual((returned, h.field(7)), (OK, FREE))
            with self.assertRaises(AssertionError):
                self.assertEqual(returned, FAULT, 'missing output proof must retain')

        def test_stop_drains_selected_frame_then_allows_new_take(self):
            h = ArmPipeline(text)
            h.select(1)
            self.assertEqual(h.call('stop', P), OK)
            self.assertEqual(h.call('finish', P, 1), BUSY)
            h.frame(2, valid=False)
            self.assertEqual(h.arrive(0, 0), (OK, RAW))
            self.assertEqual(h.complete(), OK)
            self.assertEqual(h.handoff(), OK)
            self.assertEqual(h.call('finish', P, 0), BUSY)
            self.assertEqual(h.call('finish', P, 1), OK)
            self.assertEqual(h.call('begin', P, CONTROL), OK)
            self.assertEqual(h.field(2), 2)

        def test_equal_or_larger_file_hands_off_unchanged_raw_then_selects_next(self):
            for file_bytes in (2048, 2049, 5000):
                with self.subTest(file_bytes=file_bytes):
                    h = ArmPipeline(text)
                    h.select(1)
                    source = bytes(h.uc.mem_read(SOURCE, 2048))
                    metadata = bytes(h.uc.mem_read(META, 32))
                    h.write(OUTPUT, ENCODED, 256, file_bytes, 1)
                    self.assertEqual(h.call('complete', P, TOKEN, OK, 1, OUTPUT), OK)
                    self.assertEqual(h.field(7), READY_RAW)
                    h.raw_handoff = True
                    self.assertEqual(h.handoff(), OK)
                    self.assertEqual(h.callback_seen[-1], (h.expected, None))
                    self.assertEqual(bytes(h.uc.mem_read(SOURCE, 2048)), source)
                    self.assertEqual(bytes(h.uc.mem_read(META, 32)), metadata)
                    self.assertEqual(h.words(WRITER)[0], 1)
                    self.assertEqual(h.field(7), FREE)
                    h.frame(2)
                    self.assertEqual(h.arrive(), (OK, SELECTED))

        def test_raw_fallback_still_requires_both_ownership_proofs(self):
            for proofs in (0, 1, 2):
                with self.subTest(proofs=proofs):
                    h = ArmPipeline(text)
                    h.select(1)
                    source = bytes(h.uc.mem_read(SOURCE, 2048))
                    h.write(OUTPUT, ENCODED, 256, 2048, 1)
                    self.assertEqual(h.call('complete', P, TOKEN, OK, 1, OUTPUT), OK)
                    h.raw_handoff, h.proofs = True, proofs
                    self.assertEqual(h.handoff(), FAULT)
                    self.assertEqual(h.field(7), RETAINED)
                    self.assertEqual(bytes(h.uc.mem_read(SOURCE, 2048)), source)

        def test_raw_take_never_acquires_a_source(self):
            h = ArmPipeline(text, enabled=False)
            h.frame(1, valid=False)
            self.assertEqual(h.arrive(0, 0), (OK, RAW))
            self.assertEqual(h.field(4), 0)
            self.assertEqual(h.words(P + LEASE_OFFSET, 8), (0,) * 8)

        def test_unapproved_external_callback_is_rejected(self):
            h = ArmPipeline(text)
            h.select(1)
            self.assertEqual(h.complete(), OK)
            with self.assertRaisesRegex(AssertionError, 'unapproved external PC'):
                h.handoff(FOREIGN)

    return unittest.defaultTestLoader.loadTestsFromTestCase(ExecutedArmTests)


class CompiledArmPipelineTests(unittest.TestCase):
    def test_actual_arm_pipeline_in_isolated_worker(self):
        optimized = ['-' + 'O' * sys.flags.optimize] if sys.flags.optimize else []
        child = subprocess.run([sys.executable, '-B', *optimized,
                                str(Path(__file__).resolve()), '--worker'],
                               capture_output=True, text=True, timeout=90)
        detail = child.stdout + child.stderr
        self.assertEqual(child.returncode, 0,
                         'ARM worker failed (negative exit means host signal/JIT restriction); '
                         'ARM execution is not verified.\n' + detail)
        self.assertIn('ARM PIPELINE VERIFIED: 8 tests, 0 skipped', child.stdout)
        print(child.stdout.strip())


if __name__ == '__main__':
    if sys.argv[1:] == ['--worker']:
        with tempfile.TemporaryDirectory(prefix='fpl-pipeline-arm-') as temp:
            text = compile_text(temp)
            mutant = compile_text(temp, mutation=True)
            result = unittest.TextTestRunner(verbosity=2).run(worker_suite(text, mutant))
        if result.wasSuccessful() and not result.skipped:
            print(f'ARM PIPELINE VERIFIED: {result.testsRun} tests, 0 skipped; '
                  f'text={len(text.code)} bytes; r4-r11/SP checked; handoff SP8 checked; '
                  f'text_sha256={hashlib.sha256(text.code).hexdigest()}')
            raise SystemExit(0)
        raise SystemExit(1)
    unittest.main()
