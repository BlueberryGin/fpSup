"""Execute assembled observer instructions in Unicorn, never on a camera.

Native callees are isolated BX-LR stubs with controlled register/flag/payload
mutations. This checks the observer ABI and bounded writes, not firmware
behavior, live memory ownership, concurrency, stack headroom or timing.

Verified with /usr/bin/python3 and Unicorn 2.1.4 on macOS, using:
  /usr/bin/python3 -B fpSup/focus/geometry/test_native_observer.py -v
The execution environment must permit Unicorn's JIT memory operations. This
host's restricted sandbox terminates Unicorn at its first mem_map (SIGILL),
before any observer instruction runs. The command passed with the normal
tool-approved sandbox escalation; no codesign/security settings were changed.
A subprocess probe converts that restriction into a unittest ERROR instead of
crashing the whole suite. Missing Unicorn is an import failure; neither case
is skipped or reported as successful ARM validation.
"""

import struct
import subprocess
import sys
import unittest

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                               UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
                               UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8,
                               UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
                               UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR,
                               UC_ARM_REG_PC, UC_ARM_REG_CPSR)

import build_observer as builder


REGISTERS = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
             UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
             UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_R12]
APSR_MASK = 0xF80F0000
STACK_BASE, STACK_SIZE, STACK_SP = 0x43000000, 0x10000, 0x43008000
CALLEES = {"af": 0xC022B720, "lens": 0xC036D758, "face": 0xC05A9900, "aat": 0xC05B6CF8}
CONTINUATIONS = {"af": 0xC028E314, "lens": 0xC034045C, "face": 0xC05AB34C, "aat": 0xC05AB8CC}
SOURCES = {"lens": (0xC3476414, 92), "face": (0xC37CCAAC, 156), "aat": (0xC37CDFA8, 44)}
EXTRAS = {"af": ((0xC329160B, 1), (0xC32914E0, 4), (0xC33D51FC, 4)),
          "lens": (), "face": ((0xC37CCAA0, 1), (0xC37CCAA8, 1), (0xC37CCAA9, 1)),
          "aat": ((0xC37CDFD4, 1), (0xC37CDFD5, 1), (0xC2F2D860, 1))}


class Emulator:
    def __init__(self, code, symbols, name, capacity=2, sp_mod8=0, state_overrides=None,
                 invalid_af_pointer=False, mutate_return_register=False):
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.code, self.symbols, self.name, self.capacity = code, symbols, name, capacity
        self.sp = STACK_SP + sp_mod8
        self.state_base, self.record_base = builder.STATE_BASES[name], builder.RECORD_BASES[name]
        self.source, self.payload_length = ((self.sp + 0x6F4, 172) if name == "af" else SOURCES[name])
        self.invalid_af_pointer = invalid_af_pointer
        self.mutate_return_register = mutate_return_register
        self.pages = set()
        self.map(builder.CODE_BASE, len(code))
        self.uc.mem_write(builder.CODE_BASE, code)
        self.map(STACK_BASE, STACK_SIZE)
        self.uc.mem_write(STACK_BASE, bytes([0xD6]) * STACK_SIZE)
        self.map(0x42000000, 0x1000)
        self.uc.mem_write(0x42000000, bytes([0xA5]) * 0x1000)
        for source_name in builder.SOURCE_NAMES:
            self.uc.mem_write(builder.STATE_BASES[source_name], builder.initial_state(capacity, enabled=True))
            self.map(builder.RECORD_BASES[source_name], capacity * 256 + 0x100)
            self.uc.mem_write(builder.RECORD_BASES[source_name], bytes([0xA5]) * (capacity * 256 + 0x100))
        for address in (*CALLEES.values(), *CONTINUATIONS.values()):
            self.map(address, 4)
            self.uc.mem_write(address, struct.pack("<I", 0xE12FFF1E))  # BX LR, not firmware.
        self.map(self.source, self.payload_length)
        for address, width in EXTRAS[name]:
            self.map(address, width)
        self.uc.mem_write(self.source, self.pattern(1))
        self.set_extras(1)
        for index, value in (state_overrides or {}).items():
            self.uc.mem_write(self.state_base + index * 4, struct.pack("<I", value))
        self.invocations = 0
        self.writes = []
        self.uc.hook_add(UC_HOOK_CODE, self.on_code)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.on_write)

    def map(self, address, length):
        for page in range(address & ~0xFFF, (address + length + 0xFFF) & ~0xFFF, 0x1000):
            if page not in self.pages:
                self.uc.mem_map(page, 0x1000)
                self.pages.add(page)

    def pattern(self, generation):
        return bytes((i * 7 + generation * 29) & 255 for i in range(self.payload_length))

    def set_extras(self, generation):
        for index, (address, width) in enumerate(EXTRAS[self.name]):
            value = ((0x81234560 + generation * 0x11 + index) if width == 4
                     else (0x80 + generation * 7 + index))
            self.uc.mem_write(address, int(value).to_bytes(width, "little"))

    def extras(self):
        return tuple(int.from_bytes(self.uc.mem_read(address, width), "little")
                     for address, width in EXTRAS[self.name]) or (0, 0, 0)

    def state(self):
        return struct.unpack("<10I", self.uc.mem_read(self.state_base, 40))

    def record(self, index):
        return bytes(self.uc.mem_read(self.record_base + index * 256, 256))

    def on_write(self, uc, access, address, size, value, _):
        allowed = ((self.sp - 64, self.sp), (self.state_base, self.state_base + 40),
                   (self.record_base, self.record_base + self.capacity * 256))
        if not any(start <= address and address + size <= end for start, end in allowed):
            raise AssertionError(f"write outside observer frame/owned state/records: {address:#x}+{size}")
        self.writes.append((address, size, value))

    def on_code(self, uc, address, size, _):
        self.steps += 1
        if builder.CODE_BASE <= address < builder.CODE_BASE + len(self.code):
            return
        if address == CALLEES[self.name]:
            self.native_calls += 1
            assert self.native_calls == 1, "original native callee ran more than once"
            for index, reg in enumerate(REGISTERS):
                assert uc.reg_read(reg) == self.initial[index], f"native input r{index} mismatch"
            assert uc.reg_read(UC_ARM_REG_SP) == self.sp, "native input SP mismatch"
            assert uc.reg_read(UC_ARM_REG_CPSR) & APSR_MASK == self.input_flags, "native input APSR mismatch"
            if self.name == "af":
                assert uc.reg_read(UC_ARM_REG_LR) == CONTINUATIONS[self.name], "AF native LR mismatch"
            else:
                # The first ADR LR at entry targets entry+8 in these assembled stubs.
                expected_after = builder.CODE_BASE + self.symbols["native_observer_" + self.name] + 8
                assert uc.reg_read(UC_ARM_REG_LR) == expected_after, "post native LR mismatch"
            self.payload_before_native = bytes(uc.mem_read(self.source, self.payload_length))
            self.extras_before_native = self.extras()
            uc.mem_write(self.source, self.pattern(self.invocations + 2))
            self.set_extras(self.invocations + 2)
            self.payload_after_native = bytes(uc.mem_read(self.source, self.payload_length))
            self.extras_after_native = self.extras()
            self.returned = list(self.initial)
            for index in (0, 1, 2, 3, 12):
                self.returned[index] = 0xF1234000 + index * 0x111
                uc.reg_write(REGISTERS[index], self.returned[index])
            # Preserve architecture mode while changing all tested APSR groups.
            self.output_flags = (self.input_flags ^ APSR_MASK) & APSR_MASK
            uc.reg_write(UC_ARM_REG_CPSR, 0x13 | self.output_flags)
            return  # Execute only the synthetic BX LR instruction.
        if address == CONTINUATIONS[self.name]:
            self.reached_continuation = True
            if self.mutate_return_register:
                uc.reg_write(UC_ARM_REG_R4, uc.reg_read(UC_ARM_REG_R4) ^ 1)
            uc.emu_stop()
            return
        raise AssertionError(f"unexpected execution address: {address:#x}")

    def run(self, flags=0xA80A0000):
        self.input_flags = flags & APSR_MASK
        self.initial = [0x11220000 + i * 0x1001 for i in range(13)]
        if self.name == "af":
            self.initial[:3] = [0xC32044D8, self.source + (4 if self.invalid_af_pointer else 0), 0]
        elif self.name == "lens":
            self.initial[0], self.initial[1], self.initial[4] = 0xC3476458, 1, 0xC3476414
        elif self.name == "face":
            self.initial[0] = self.initial[4] = 0xC37CCA9C
        self.uc.reg_write(UC_ARM_REG_CPSR, 0x13 | self.input_flags)
        for reg, value in zip(REGISTERS, self.initial):
            self.uc.reg_write(reg, value)
        self.uc.reg_write(UC_ARM_REG_SP, self.sp)
        self.uc.reg_write(UC_ARM_REG_LR, CONTINUATIONS[self.name])
        self.native_calls = self.steps = 0
        self.reached_continuation = False
        self.writes = []
        self.uc.emu_start(builder.CODE_BASE + self.symbols["native_observer_" + self.name],
                          0xFFFFFFFF, count=20000)
        assert self.reached_continuation, "observer did not reach continuation within instruction bound"
        assert self.native_calls == 1, "original native callee did not run exactly once"
        for index, reg in enumerate(REGISTERS):
            assert self.uc.reg_read(reg) == self.returned[index], f"returned preserved r{index} mismatch"
        assert self.uc.reg_read(UC_ARM_REG_SP) == self.sp, "returned SP mismatch"
        assert self.uc.reg_read(UC_ARM_REG_LR) == CONTINUATIONS[self.name], "returned LR mismatch"
        assert self.uc.reg_read(UC_ARM_REG_CPSR) & APSR_MASK == self.output_flags, "returned APSR mismatch"
        self.invocations += 1
        return self


class NativeObserverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # macOS sandboxing may allow import but terminate the first JIT setup.
        # Probe in a child and report an ERROR, never a skipped/pass result.
        probe = subprocess.run([sys.executable, "-B", "-c",
            "import unicorn; u=unicorn.Uc(unicorn.UC_ARCH_ARM,unicorn.UC_MODE_ARM); "
            "u.mem_map(4096,4096); u.mem_write(4096,bytes.fromhex('0000a0e1')); "
            "u.emu_start(4096,4100,count=1)"], capture_output=True, timeout=10)
        if probe.returncode:
            raise RuntimeError(f"Unicorn ARM/JIT probe failed (exit {probe.returncode}); "
                               "use an execution environment that permits JIT. ARM emulation is unverified.")
        cls.code, cls.symbols, cls.manifest = builder.assemble_observer(capacity=2)

    def emulator(self, name="af", **options):
        return Emulator(self.code, self.symbols, name, **options)

    def assert_record(self, emulator, index, nonce=builder.NONCE):
        record = emulator.record(index)
        words = struct.unpack("<64I", record)
        source_id = builder.SOURCE_NAMES.index(emulator.name) + 1
        self.assertEqual(words[:6], (0x31524E46, 1, source_id, index + 1, nonce, emulator.sp))
        observed_r0 = emulator.initial[0] if emulator.name == "af" else emulator.returned[0]
        observed_flags = emulator.input_flags if emulator.name == "af" else emulator.output_flags
        self.assertEqual(words[6], observed_r0)
        self.assertEqual(words[7] & APSR_MASK, observed_flags)
        self.assertEqual(words[8], emulator.payload_length)
        expected_extras = emulator.extras_before_native if emulator.name == "af" else emulator.extras_after_native
        self.assertEqual(words[9:12], expected_extras)
        self.assertEqual(record[48:64], bytes(16))
        payload = emulator.payload_before_native if emulator.name == "af" else emulator.payload_after_native
        self.assertEqual(record[64:64 + len(payload)], payload)
        self.assertEqual(record[64 + len(payload):252], bytes(188 - len(payload)))
        self.assertEqual(words[63], index + 1)

    def test_all_actual_stubs_preserve_abi_phase_and_payload_for_both_sp_alignments(self):
        for name in builder.SOURCE_NAMES:
            for sp_mod8 in (0, 4):
                for flags in (0xA80A0000, 0x50050000):
                    with self.subTest(name=name, sp_mod8=sp_mod8, flags=hex(flags)):
                        emulator = self.emulator(name, sp_mod8=sp_mod8).run(flags)
                        self.assert_record(emulator, 0)
                        self.assertEqual(emulator.state()[6:], (1, 0, 0, 0))
                        self.assertLess(emulator.steps, 2000)

    def test_body_trailer_and_commit_precede_count_publication(self):
        for name in builder.SOURCE_NAMES:
            with self.subTest(name=name):
                emulator = self.emulator(name).run()
                writes = emulator.writes
                trailer = next(i for i, (a, _, v) in enumerate(writes) if a == emulator.record_base + 252 and v == 1)
                commit = next(i for i, (a, _, v) in enumerate(writes) if a == emulator.record_base + 12 and v == 1)
                count = next(i for i, (a, _, v) in enumerate(writes) if a == emulator.state_base + 24 and v == 1)
                self.assertLess(trailer, commit)
                self.assertLess(commit, count)
                self.assertFalse(any(emulator.record_base <= a < emulator.record_base + 256
                                     for a, _, _ in writes[commit + 1:]))

    def test_finite_capacity_preserves_first_records_and_never_overwrites(self):
        for name in builder.SOURCE_NAMES:
            with self.subTest(name=name):
                emulator = self.emulator(name).run()
                first = emulator.record(0)
                emulator.run()
                self.assert_record(emulator, 1)
                self.assertEqual(emulator.record(0), first)
                self.assertEqual(emulator.state()[6:], (2, 1, 0, 0))
                records = emulator.record(0) + emulator.record(1)
                emulator.run()
                self.assertEqual(emulator.record(0) + emulator.record(1), records)
                self.assertEqual(emulator.state()[6:], (2, 1, 0, 0))
                self.assertFalse(any(emulator.record_base <= address < emulator.record_base + 512
                                     for address, _, _ in emulator.writes))

    def test_other_sources_and_canary_bytes_are_never_written(self):
        for name in builder.SOURCE_NAMES:
            with self.subTest(name=name):
                emulator = self.emulator(name)
                before = {other: (bytes(emulator.uc.mem_read(builder.STATE_BASES[other], 40)),
                                  bytes(emulator.uc.mem_read(builder.RECORD_BASES[other], 512)))
                          for other in builder.SOURCE_NAMES if other != name}
                emulator.run()
                for other, values in before.items():
                    self.assertEqual(bytes(emulator.uc.mem_read(builder.STATE_BASES[other], 40)), values[0])
                    self.assertEqual(bytes(emulator.uc.mem_read(builder.RECORD_BASES[other], 512)), values[1])
                self.assertEqual(bytes(emulator.uc.mem_read(emulator.record_base + 512, 256)), b"\xA5" * 256)
                self.assertEqual(bytes(emulator.uc.mem_read(emulator.sp - 68, 4)), b"\xD6" * 4)
                self.assertEqual(bytes(emulator.uc.mem_read(emulator.sp, 4)), b"\xD6" * 4)

    def test_invalid_header_disabled_nonce_zero_and_sticky_full_skip_recording(self):
        invalid_fields = ((0, 0), (1, 2), (2, 128), (3, 0), (3, 3), (4, 0),
                          (5, 0), (5, 2), (7, 1))
        for name in builder.SOURCE_NAMES:
            for field, value in invalid_fields:
                with self.subTest(name=name, field=field, value=value):
                    emulator = self.emulator(name, state_overrides={field: value})
                    state = emulator.state()
                    records = emulator.record(0) + emulator.record(1)
                    emulator.run()
                    self.assertEqual(emulator.state(), state)
                    self.assertEqual(emulator.record(0) + emulator.record(1), records)
                    self.assertTrue(all(emulator.sp - 64 <= address < emulator.sp
                                        for address, _, _ in emulator.writes))

    def test_busy_conflict_sets_loss_without_clearing_foreign_busy_or_touching_records(self):
        for name in builder.SOURCE_NAMES:
            with self.subTest(name=name):
                emulator = self.emulator(name, state_overrides={8: 1}).run()
                self.assertEqual(emulator.state()[6:], (0, 0, 1, 1))
                self.assertEqual(emulator.record(0), b"\xA5" * 256)
                self.assertFalse(any(address == emulator.state_base + 32 for address, _, _ in emulator.writes))

    def test_bad_count_and_count_at_capacity_fail_closed_after_lock(self):
        for count, tail in ((2, (2, 1, 0, 0)), (3, (3, 0, 0, 1)), (0xFFFFFFFF, (0xFFFFFFFF, 0, 0, 1))):
            for name in builder.SOURCE_NAMES:
                with self.subTest(name=name, count=count):
                    emulator = self.emulator(name, state_overrides={6: count}).run()
                    self.assertEqual(emulator.state()[6:], tail)
                    self.assertEqual(emulator.record(0) + emulator.record(1), b"\xA5" * 512)

    def test_invalid_af_stack_payload_pointer_records_loss_but_still_forwards_once(self):
        for sp_mod8 in (0, 4):
            with self.subTest(sp_mod8=sp_mod8):
                emulator = self.emulator("af", sp_mod8=sp_mod8, invalid_af_pointer=True).run()
                self.assertEqual(emulator.state()[6:], (0, 0, 0, 1))
                self.assertEqual(emulator.record(0), b"\xA5" * 256)

    def test_nonzero_nonce_is_copied_without_truncation_and_loss_remains_sticky(self):
        for nonce in (1, 0xFFFFFFFF):
            with self.subTest(nonce=nonce):
                emulator = self.emulator("aat", state_overrides={4: nonce, 9: 1}).run()
                self.assert_record(emulator, 0, nonce=nonce)
                self.assertEqual(emulator.state()[9], 1)

    def test_actual_emitted_buffers_decode_as_raw_evidence_without_geometry_authority(self):
        from observer_decode import decode_snapshot
        for source_id, name in enumerate(builder.SOURCE_NAMES, 1):
            with self.subTest(name=name):
                emulator = self.emulator(name).run()
                emulator.run()
                state = bytearray(emulator.uc.mem_read(emulator.state_base, 40))
                # This is an offline copy, not a claim that any camera producer quiesced.
                struct.pack_into("<I", state, 20, 0)
                records = emulator.record(0) + emulator.record(1)
                decoded = decode_snapshot(bytes(state), records, source_id=source_id)
                self.assertEqual((decoded["count"], decoded["full"], decoded["loss_observed"]), (2, True, False))
                self.assertIs(decoded["snapshot_quiescence_independently_verified"], False)
                self.assertIs(decoded["source_ordering_verified"], False)
                for index, row in enumerate(decoded["records"]):
                    self.assertEqual(bytes.fromhex(row["bytes_hex"]), emulator.record(index))
                    for gate in ("geometry_eligible", "confirmation_authorized", "motion_authorized"):
                        self.assertIs(row[gate], False)
                    self.assertIsNone(row["source_time_ms"])
                    self.assertIsNone(row["episode_generation"])

    def test_negative_control_corrupting_actual_restore_instruction_is_detected(self):
        code = bytearray(self.code)
        # MSR APSR_nzcvqg,r12 in the AF wrapper: replacing it leaves recorder flags live.
        instruction = struct.pack("<I", 0xE12CF00C)
        offset = code.index(instruction, self.symbols["native_observer_af"], self.symbols["native_observer_lens"])
        code[offset:offset + 4] = struct.pack("<I", 0xE1A00000)
        with self.assertRaisesRegex(AssertionError, "native input APSR mismatch"):
            Emulator(bytes(code), self.symbols, "af").run()

    def test_negative_control_corrupting_a_returned_preserved_register_is_detected(self):
        with self.assertRaisesRegex(AssertionError, "returned preserved r4 mismatch"):
            self.emulator("lens", mutate_return_register=True).run()

    def test_builder_artifact_remains_disabled_and_not_installable(self):
        state = struct.unpack("<10I", builder.initial_state())
        self.assertEqual(state[5:], (0, 0, 0, 0, 0))
        for key in ("installable", "deployment_ready", "hardware_io_performed", "ram_ownership_verified",
                    "stack_headroom_verified", "timing_budget_verified", "source_coherence_verified",
                    "optical_validity_verified", "arm_emulation_verified", "confirmation_authorized", "motion_authorized"):
            self.assertIs(self.manifest[key], False, key)
        self.assertTrue(self.manifest["emulation_addresses_only"])


if __name__ == "__main__":
    unittest.main()
