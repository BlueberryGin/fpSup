"""Execute the assembled GCSV formatter and its shared numeric helpers.

No camera or firmware image is needed.  Only F_WRITE is stubbed, with hostile
caller-saved registers.  Baseline comparisons/profile are optional in a bare
fpSup checkout; all current-code correctness tests still run without snapshots.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import unittest
from collections import Counter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'fp_usb_shell'))
from armasm import assemble, symbols

SOURCE = HERE / 'gcsv_task.S'
REPORT = (HERE.parent.parent / 'projects/gyro-sup/validation'
          / '20261004-gcsv-direct-output')
BASELINE = REPORT / 'before'
POOL, CODE = 0x45000000, 0x45044000
INPUT, JOB = 0x46000000, 0x46008000
OUTPUT, STACK, STOP = 0x47000003, 0x10000000, 0x10100000
FRAMEBUF, WRITE, TEXT_BYTES = 0xC3757A7C, 0xC03660E8, 0x22000


def unicorn_available():
    if importlib.util.find_spec('unicorn') is None:
        return False, 'Unicorn is missing; ARM execution is unverified'
    # Some macOS sandboxes permit import but prohibit Unicorn's JIT execution.
    probe = subprocess.run([sys.executable, '-B', '-c',
        'import unicorn; u=unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM);'
        'u.mem_map(4096,4096); u.mem_write(4096,b"\\x00\\x00\\xa0\\xe1");'
        'u.emu_start(4096,4100,count=1)'], capture_output=True, timeout=10)
    return (probe.returncode == 0,
            f'Unicorn JIT probe exited {probe.returncode}; ARM execution is unverified')


HAS_UNICORN, UNICORN_REASON = unicorn_available()
if HAS_UNICORN:
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_SP, UC_ARM_REG_LR,
                                   UC_ARM_REG_PC, UC_ARM_REG_CPSR)


def pack_records(records):
    return b''.join(struct.pack('<hhhh', *record) for record in records)


def expected(records, start=0):
    """Independent stream oracle: attach an accel reading to its preceding gyro."""
    result, held, row = [], None, start

    def emit(sample, accel=None):
        nonlocal row
        fields = [row & 0xFFFFFFFF, sample[0], sample[1], sample[3]]
        if accel is not None:
            fields.extend((accel[1], -accel[0], accel[3]))
        result.append((','.join(map(str, fields)) + '\n').encode())
        row += 1

    for record in records:
        if record[2] == 1:
            if held is not None:
                emit(held, record)
                held = None
        else:
            if held is not None:
                emit(held)
            held = record
    if held is not None:
        emit(held)
    return b''.join(result)


def records(count):
    axes = (-32768, 32767, -10000, -1, 0, 1, 999, 10000)
    return [(axes[i % 8], axes[(i + 3) % 8],
             int(i % 53 == 0 or i % 128 == 0), axes[(i + 5) % 8])
            for i in range(count)]


def u32_cases():
    cases = {0, 1, 0xFFFFFFFF}
    for boundary in [10**n for n in range(10)] + [1 << n for n in range(32)]:
        cases.update(n for n in (boundary - 1, boundary, boundary + 1)
                     if 0 <= n <= 0xFFFFFFFF)
    rng = random.Random(0xF09C5)
    cases.update(rng.randrange(1 << 32) for _ in range(512))
    return sorted(cases)


class Formatter:
    """Strict ARM calls with ABI checks; any unmocked native call fails."""

    def __init__(self, code, syms):
        self.code, self.syms = code, syms
        self.u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        for address, size in ((POOL, 0x100000), (INPUT, 0x10000),
                              (OUTPUT & ~0xFFF, 0x24000), (STACK, 0x10000),
                              (STOP, 0x1000), (FRAMEBUF & ~0xFFF, 0x1000),
                              (WRITE & ~0xFFF, 0x1000)):
            self.u.mem_map(address, size)
        self.u.mem_write(CODE, code)
        self.put32(FRAMEBUF, POOL)
        self.writes = []
        self.returned = False
        self.call_sites = {
            CODE + offset for offset in range(0, len(code), 4)
            if ((struct.unpack_from('<I', code, offset)[0] & 0x0F000000) == 0x0B000000
                or (struct.unpack_from('<I', code, offset)[0] & 0x0FFFFFF0) == 0x012FFF30)
        }
        self.u.hook_add(UC_HOOK_CODE, self._code)

    def get(self, number):
        return self.u.reg_read(UC_ARM_REG_R0 + number)

    def put32(self, address, value):
        self.u.mem_write(address, struct.pack('<I', value))

    def state(self, symbol, value):
        self.put32(CODE + self.syms[symbol], value)

    def _code(self, u, address, size, _):
        if address == STOP:
            self.returned = True
            u.emu_stop()
            return
        if address == WRITE:
            assert u.reg_read(UC_ARM_REG_SP) % 8 == 0, 'native call SP alignment'
            self.writes.append(bytes(u.mem_read(self.get(1), self.get(2))))
            lr = u.reg_read(UC_ARM_REG_LR)
            for number in (0, 1, 2, 3, 12):
                u.reg_write(UC_ARM_REG_R0 + number, 0xBAD00000 + number)
            u.reg_write(UC_ARM_REG_R0, 1)
            u.reg_write(UC_ARM_REG_CPSR,
                        (u.reg_read(UC_ARM_REG_CPSR) & 0x0FFFFFFF) | 0xA0000000)
            u.reg_write(UC_ARM_REG_PC, lr)
            return
        assert CODE <= address < CODE + len(self.code), f'unmocked code {address:#x}'
        if address in self.call_sites:
            assert u.reg_read(UC_ARM_REG_SP) % 8 == 0, f'call SP alignment at {address:#x}'

    def call(self, name, args=(), job=None, cursor=None):
        u = self.u
        # Set mode before its banked stack and link registers.
        u.reg_write(UC_ARM_REG_CPSR, 0x13)
        saved = {number: 0x55550000 + number for number in range(4, 12)}
        if job is not None:
            saved[4] = job
        if cursor is not None:
            saved[9] = cursor
        for number, value in saved.items():
            u.reg_write(UC_ARM_REG_R0 + number, value)
        for number, value in enumerate(args):
            u.reg_write(UC_ARM_REG_R0 + number, value & 0xFFFFFFFF)
        initial_sp = STACK + 0xFFF0
        u.reg_write(UC_ARM_REG_SP, initial_sp)
        u.reg_write(UC_ARM_REG_LR, STOP)
        self.returned = False
        u.emu_start(CODE + self.syms[name], STOP + 4, count=3_000_000)
        assert self.returned, f'{name} did not return'
        assert u.reg_read(UC_ARM_REG_SP) == initial_sp, f'{name} changed SP'
        for number, value in saved.items():
            if number != 9 or cursor is None:
                assert self.get(number) == value, f'{name} changed r{number}'
        return self.get(0), self.get(1)

    def guard(self, budget):
        self.u.mem_write(OUTPUT - 3, b'\xA5' * (budget + 8))

    def output(self, end, budget):
        assert OUTPUT <= end <= OUTPUT + budget, 'output outside budget'
        assert bytes(self.u.mem_read(OUTPUT - 3, 3)) == b'\xA5' * 3
        tail = OUTPUT + budget + 5 - end
        assert bytes(self.u.mem_read(end, tail)) == b'\xA5' * tail, 'output guard changed'
        return bytes(self.u.mem_read(OUTPUT, end - OUTPUT))

    def format(self, batch, budget=TEXT_BYTES):
        if batch:
            self.u.mem_write(INPUT, pack_records(batch))
        self.guard(budget)
        end, left = self.call('gcsv_format', (INPUT, len(batch), OUTPUT, OUTPUT + budget))
        assert left <= len(batch), 'invalid remaining record count'
        return self.output(end, budget), left

    def last(self):
        self.guard(64)
        end, _ = self.call('gcsv_last', (OUTPUT,))
        return self.output(end, 64)


def poisoned_stream(machine, samples, chunk=2048, budget=TEXT_BYTES):
    out = []
    for offset in range(0, len(samples), chunk):
        pending = samples[offset:offset + chunk]
        while pending:
            text, left = machine.format(pending, budget)
            out.append(text)
            assert left < len(pending), 'formatter made no progress'
            # The writer may release and reuse this block as soon as the call ends.
            machine.u.mem_write(INPUT, b'\xD1' * (len(pending) * 8))
            pending = pending[len(pending) - left:] if left else []
        assert machine.format([]) == (b'', 0), 'empty call emitted or consumed data'
    out.append(machine.last())
    assert machine.last() == b'', 'last row emitted twice'
    return b''.join(out)


class FastNumbers:
    """No per-instruction Python hook for the 131,073 exhaustive numeric cases."""

    def __init__(self, code, syms):
        self.syms = syms
        self.u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        for address, size in ((CODE, 0x10000), (OUTPUT & ~0xFFF, 0x1000),
                              (STACK, 0x10000), (STOP, 0x1000)):
            self.u.mem_map(address, size)
        self.u.mem_write(CODE, code)
        self.u.reg_write(UC_ARM_REG_CPSR, 0x13)
        for number in range(4, 12):
            self.u.reg_write(UC_ARM_REG_R0 + number, 0x77770000 + number)

    def check(self, name, values):
        u = self.u
        for value in values:
            text = str(value).encode()
            u.mem_write(OUTPUT - 3, b'\xA5' * 32)
            u.reg_write(UC_ARM_REG_R0, value & 0xFFFFFFFF)
            u.reg_write(UC_ARM_REG_R0 + 1, OUTPUT)
            u.reg_write(UC_ARM_REG_SP, STACK + 0xFFF0)
            u.reg_write(UC_ARM_REG_LR, STOP)
            u.emu_start(CODE + self.syms[name], STOP, count=1000)
            assert u.reg_read(UC_ARM_REG_PC) == STOP, f'{name}({value}) did not return'
            assert u.reg_read(UC_ARM_REG_SP) == STACK + 0xFFF0
            assert u.reg_read(UC_ARM_REG_R0) == OUTPUT + len(text), (name, value)
            assert bytes(u.mem_read(OUTPUT - 3, 32)) == (
                b'\xA5' * 3 + text + b'\xA5' * (29 - len(text))), (name, value)
        for number in range(4, 12):
            assert u.reg_read(UC_ARM_REG_R0 + number) == 0x77770000 + number


class Profile(Formatter):
    def __init__(self, code, syms):
        self.counts = Counter()
        super().__init__(code, syms)
        self.u.hook_add(UC_HOOK_MEM_WRITE, self._write)

    def _code(self, u, address, size, data):
        if CODE <= address < CODE + len(self.code):
            self.counts['arm_instructions'] += 1
            if CODE + self.syms['put_u32'] <= address < CODE + self.syms['put_i16']:
                self.counts['put_u32_instructions'] += 1
            if address == CODE + self.syms['put_u32']:
                self.counts['put_u32_calls'] += 1
        super()._code(u, address, size, data)

    def _write(self, u, access, address, size, value, data):
        if CODE + self.syms['g_g_held'] <= address < CODE + self.syms['g_g_held'] + 8:
            self.counts['held_record_write_bytes'] += size


@unittest.skipUnless(HAS_UNICORN, UNICORN_REASON)
class DirectOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = assemble(SOURCE)
        cls.syms = symbols(SOURCE)

    @staticmethod
    def has_baseline(edition='gcsv'):
        return all((BASELINE / name).is_file() for name in
                   (f'{edition}.bin', f'{edition}-symbols.json'))

    @staticmethod
    def baseline(edition='gcsv'):
        return ((BASELINE / f'{edition}.bin').read_bytes(),
                json.loads((BASELINE / f'{edition}-symbols.json').read_text()))

    def variants(self):
        yield 'current', self.code, self.syms
        if self.has_baseline():
            yield 'baseline', *self.baseline()

    def test_all_unsigned_16bit_and_unsigned_32bit_boundaries(self):
        machine = FastNumbers(self.code, self.syms)
        machine.check('put_u32', range(65536))
        machine.check('put_u32', u32_cases())

    def test_all_signed_16bit_and_negated_accel_minimum(self):
        machine = FastNumbers(self.code, self.syms)
        machine.check('put_i16', range(-32768, 32768))
        machine.check('put_i16', [32768])

    def test_number_abi_and_output_guards_at_every_alignment(self):
        machine = Formatter(self.code, self.syms)
        samples = {'put_u32': u32_cases()[:65] + [999999999, 1000000000, 0xFFFFFFFF],
                   'put_i16': [-32768, -10000, -1, 0, 1, 9999, 32767, 32768]}
        for alignment in range(4):
            dest = ((OUTPUT + 16) & ~3) + alignment
            for name, values in samples.items():
                for value in values:
                    machine.u.mem_write(dest - 3, b'\xA5' * 32)
                    end, _ = machine.call(name, (value, dest))
                    text = str(value).encode()
                    self.assertEqual(end, dest + len(text))
                    self.assertEqual(bytes(machine.u.mem_read(dest - 3, 32)),
                                     b'\xA5' * 3 + text + b'\xA5' * (29 - len(text)))

    def test_held_rows_survive_input_reuse_and_unusual_sequences(self):
        gyro, accel = (123, -456, 0, 789), (14, 15, 1, 16)
        sequences = [[], [gyro], [accel], [accel] * 129, [gyro, accel],
                     [gyro, accel, accel, gyro], [accel, gyro, gyro, accel, accel]]
        sequences += [records(n) for n in (0, 1, 127, 128, 129, 2048, 2049)]
        for name, code, syms in self.variants():
            for sequence in sequences:
                for chunk in (1, 127, 2048):
                    with self.subTest(variant=name, count=len(sequence), chunk=chunk):
                        self.assertEqual(poisoned_stream(Formatter(code, syms), sequence, chunk),
                                         expected(sequence))

    def test_tiny_budgets_keep_the_same_bytes(self):
        samples = records(513)
        for name, code, syms in self.variants():
            for budget in (64, 67, 73, 131):
                with self.subTest(variant=name, budget=budget):
                    self.assertEqual(poisoned_stream(Formatter(code, syms), samples, 129, budget),
                                     expected(samples))
            for budget in (0, 1, 31, 63):
                self.assertEqual(Formatter(code, syms).format(samples, budget), (b'', len(samples)))

    def test_sample_counter_wrap_is_uint32(self):
        samples, start = records(129), 0xFFFFFFFC
        for _, code, syms in self.variants():
            machine = Formatter(code, syms)
            machine.state('g_g_row', start)
            self.assertEqual(poisoned_stream(machine, samples, 7), expected(samples, start))

    def test_writer_preserves_one_write_and_baseline_bytes(self):
        samples = records(2048)
        outputs = []
        for _, code, syms in self.variants():
            machine = Formatter(code, syms)
            machine.u.mem_write(INPUT, pack_records(samples))
            machine.u.mem_write(JOB, struct.pack('<6I', 0, 0, INPUT, len(samples) * 8, 0, 0))
            machine.state('g_g_text', OUTPUT)
            machine.state('g_t_fobj', 0x48000000)
            machine.guard(TEXT_BYTES)
            machine.call('writer_put', job=JOB)
            self.assertEqual(len(machine.writes), 1)
            machine.output(OUTPUT + len(machine.writes[0]), TEXT_BYTES)
            machine.u.mem_write(INPUT, b'\xD1' * (len(samples) * 8))
            output = machine.writes[0] + machine.last()
            self.assertEqual(output, expected(samples))
            outputs.append(output)
        self.assertTrue(all(output == outputs[0] for output in outputs))

    def test_missing_cross_call_copy_mutation_is_detected(self):
        mutated = bytearray(self.code)
        offset = self.syms['gcsv_hold_store']
        original = struct.unpack_from('<I', mutated, offset)[0]
        self.assertEqual(original, 0x18880003, 'mutation must target STMIAne r8,{r0,r1}')
        struct.pack_into('<I', mutated, offset, 0xE1A00000)
        samples = [(123, -456, 0, 789), (14, 15, 1, 16)]
        actual = poisoned_stream(Formatter(bytes(mutated), self.syms), samples, 1)
        with self.assertRaises(AssertionError):
            self.assertEqual(actual, expected(samples))

    def test_json_helpers_in_gcsv_and_base(self):
        variants = [('gcsv', self.code, self.syms)]
        defines = ('FPGYRO_EDITION_BASE=1',)
        variants.append(('base', assemble(SOURCE, defines), symbols(SOURCE, defines)))
        values = sorted(set(u32_cases()[::5] + [0, 1, 9, 10, 99, 100, 999, 1000,
                                               32768, 65535, 0xFFFFFFFF]))
        helpers = {'ji': lambda n: str(n),
                   'jm': lambda n: f'{n // 1000}.{n % 1000:03}',
                   'jhalf': lambda n: f'{n // 2}.{5 if n & 1 else 0}'}
        for edition, code, syms in variants:
            machines = [Formatter(code, syms)]
            if self.has_baseline(edition):
                machines.append(Formatter(*self.baseline(edition)))
            for machine in machines:
                for name, oracle in helpers.items():
                    for value in values:
                        machine.guard(32)
                        _, r1 = machine.call(name, (value, 0x12345678), cursor=OUTPUT)
                        self.assertEqual(machine.output(machine.get(9), 32), oracle(value).encode())
                        if name == 'ji':
                            self.assertEqual(r1, 0x12345678)

    def test_before_after_instruction_counts_and_output_identity(self):
        if not self.has_baseline():
            self.skipTest('optional instruction profile needs before/gcsv.bin and symbols')
        old_code, old_syms = self.baseline()
        report = {'scope': 'Actual ARM gcsv_format plus gcsv_last. Synthetic instruction counts '
                  'are not CPU cycles, hardware timing, or evidence of an audio fix.',
                  'before_sha256': hashlib.sha256(old_code).hexdigest(),
                  'after_sha256': hashlib.sha256(self.code).hexdigest(), 'cases': []}
        for name, axes, start in (('small_axes_start', (0, 1, -1), 0),
                                  ('extreme_axes_later', (-32768, 32767, -10000), 1500000)):
            samples = [(axes[0], axes[1], int(i % 51 == 50), axes[2]) for i in range(2048)]
            machines = [Profile(old_code, old_syms), Profile(self.code, self.syms)]
            results = []
            for machine in machines:
                machine.state('g_g_row', start)
                text, left = machine.format(samples)
                self.assertEqual(left, 0)
                text += machine.last()
                self.assertEqual(text, expected(samples, start))
                results.append(text)
            self.assertEqual(*results)
            before, after = (dict(machine.counts) for machine in machines)
            saved = before['arm_instructions'] - after['arm_instructions']
            self.assertGreater(saved, 0)
            report['cases'].append({'name': name, 'records': 2048, 'gyro_records': 2008,
                'accel_records': 40, 'before': before, 'after': after,
                'output_bytes': len(results[0]), 'output_sha256': hashlib.sha256(results[0]).hexdigest(),
                'saved_arm_instructions': saved})
        REPORT.mkdir(parents=True, exist_ok=True)
        (REPORT / 'instruction-counts.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
