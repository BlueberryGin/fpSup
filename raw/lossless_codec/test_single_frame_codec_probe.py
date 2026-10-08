#!/usr/bin/env python3

import contextlib
import hashlib
import io
import pathlib
import re
import sys
import unittest
from unittest import mock

import exact_dng_writer_probe as base
import single_frame_codec_probe as probe



def arena_bounds():
    """The arena, from the file that declares it -- never named here."""
    import probe_placement
    cave = probe_placement.load_cave()
    return cave.CAVE_ARENA, cave.CAVE_ARENA_END

class FakeShell:
    def __init__(self, *, context=None, fire_immediately=False, site=None):
        self.site = base.HOOK_ORIG if site is None else site
        self.context = list(context or base.EXPECTED_CONTEXT)
        self.state = [0] * probe.STATE_WORDS
        self.log = [0] * (probe.AGG_OFF // 4 + probe.AGG_WORDS)
        self.fire_immediately = fire_immediately
        self.writes = []
        # Sparse word memory for the allocated blocks, so the lifecycle records
        # and the marks inside the blocks can be read and written the way the
        # camera would. Reads outside a registered block still raise: a test
        # that silently reads zeroes from an address nothing allocated proves
        # nothing, and that is how a 4 MiB block with no record would look.
        self.memory = {}
        self.blocks = []

    def add_block(self, handle, span=None):
        self.blocks.append((handle, handle + (span or probe.ALLOC_SIZE)))
        return handle

    def in_block(self, address, count):
        return any(start <= address and address + count * 4 <= end
                   for start, end in self.blocks)

    def install_life_record(self, handle_a, slot, *, handle, role,
                            allocator=0x45001000, state=None, tick=1000,
                            seq=1, magic=None, own_mark=True,
                            size=None, end=None, held=2000):
        """Write a record the way the camera writes it, field by field.

        Every field is a parameter because the tests that matter are the ones
        where exactly one of them is wrong.
        """
        base_at = handle_a + probe.LIFE_OFF + probe.LIFE_SLOT[slot]
        size = probe.ALLOC_SIZE if size is None else size
        words = [0] * probe.LIFE_WORDS
        words[probe.L_MAGIC] = probe.LIFE_MAGIC if magic is None else magic
        words[probe.L_ROLE] = role
        words[probe.L_ALLOC] = allocator
        words[probe.L_HANDLE] = handle
        words[probe.L_BYTES] = size
        words[probe.L_END] = handle + size if end is None else end
        words[probe.L_TICK] = tick
        words[probe.L_SEQ] = seq
        words[probe.L_PURPOSE] = 0x4B524F57 if role == 1 else 0x45435253
        words[probe.L_OWNMARK] = probe.OWN_MAGIC if own_mark else 0
        words[probe.L_HELD] = held
        words[probe.L_STATE] = probe.LIFE_HELD if state is None else state
        for i, word in enumerate(words):
            self.memory[base_at + i * 4] = word

    def install_mark(self, handle, *, magic=None, names=None):
        self.add_block(handle)
        self.memory[handle + probe.OWN_OFF] = (
            probe.OWN_MAGIC if magic is None else magic)
        self.memory[handle + probe.OWN_OFF + 4] = (
            handle if names is None else names)

    def read_word(self, address):
        if address != base.HOOK_SITE:
            raise AssertionError(f"unexpected read 0x{address:08X}")
        return self.site

    def read_words(self, address, count):
        if address == base.CONTEXT_ADDRESS and count == len(base.EXPECTED_CONTEXT):
            words = list(self.context)
            words[base.HOOK_CONTEXT_INDEX] = self.site
            return words
        if address == probe.STATE and count in (base.STATE_WORDS, probe.STATE_WORDS):
            return list(self.state[:count])
        if address == probe.LOG and count <= len(self.log):
            return list(self.log[:count])
        if address == probe.LOG + probe.AGG_OFF:
            start = probe.AGG_OFF // 4
            return list(self.log[start:start + count])
        if address == base.HOOK_SITE and count == 1:
            return [self.site]
        if address == probe.LOAD_DONE_US and count == 1:
            # The boot identity. A record carries the boot that wrote it, and
            # a short power cycle leaves DRAM intact, so without this a record
            # from the PREVIOUS boot reads as valid in every other field. The
            # fake records here carry 0, so the fake stamp is 0 too.
            return [getattr(self, "boot", 0)]
        if self.in_block(address, count):
            return [self.memory.get(address + i * 4, 0) for i in range(count)]
        raise AssertionError(f"unexpected read 0x{address:08X}, {count}")

    def set_word(self, address, value):
        if address != base.HOOK_SITE:
            raise AssertionError(f"unexpected write 0x{address:08X}")
        self.writes.append(("set", address, (value,)))
        if value == base.HOOK_ARMED and self.fire_immediately:
            self.state[probe.S_COUNT] = 1
            self.state[probe.S_DONE] = base.DONE_MAGIC
            self.state[probe.S_RESTORED] = base.HOOK_ORIG
            self.site = base.HOOK_ORIG
        else:
            self.site = value

    def write_words_verified(self, address, values, attempts=8):
        values = tuple(values)
        self.writes.append(("verified", address, values))
        if address == probe.CODE:
            return
        if address == probe.STATE and len(values) == probe.STATE_WORDS:
            self.state = list(values)
            return
        if address == probe.LOG and len(values) <= len(self.log):
            self.log[:len(values)] = list(values)
            return
        if (probe.STATE <= address
                and address + len(values) * 4 <= probe.STATE + probe.STATE_WORDS * 4):
            start = (address - probe.STATE) // 4
            self.state[start:start + len(values)] = list(values)
            return
        if address == base.HOOK_SITE and values == (base.HOOK_ORIG,):
            self.site = base.HOOK_ORIG
            return
        if self.in_block(address, len(values)):
            for i, value in enumerate(values):
                self.memory[address + i * 4] = value
            return
        raise AssertionError(f"unexpected verified write 0x{address:08X}")

    def write_word_verified(self, address, value, attempts=8):
        self.write_words_verified(address, [value], attempts)


class CodecProbeTests(unittest.TestCase):
    def setUp(self):
        """No test reaches the camera. Arming now always publishes caches,
        which would otherwise run callfn against a live shell."""
        patcher = mock.patch.object(
            probe, "native_caller", lambda *a, **k: (lambda fn, **kw: (True, 0)))
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def successful_preflight_state():
        values = [0] * probe.STATE_WORDS
        values[probe.S_COUNT] = 1
        values[probe.S_DONE] = base.DONE_MAGIC
        values[probe.S_RESTORED] = base.HOOK_ORIG
        values[probe.S_PHASE] = probe.PHASE_PREFLIGHT
        values[probe.S_STAGE] = 7
        values[probe.S_R2] = 2
        values[probe.S_LR] = base.EXPECTED_LR
        values[probe.S_BUFFER] = 0x53B02C00
        values[probe.S_LENGTH] = 0x00318200
        values[probe.S_SOURCE] = 0x53B16000
        values[probe.S_PREFLIGHT] = probe.PREFLIGHT_MAGIC
        return values

    @staticmethod
    def successful_scratch_state(handle=0x45166C00):
        values = [0] * probe.STATE_WORDS
        values[probe.S_COUNT] = 1
        values[probe.S_R2] = 2
        values[probe.S_LR] = base.EXPECTED_LR
        values[probe.S_BUFFER] = 0x53B02C00
        values[probe.S_LENGTH] = 0x00318200
        values[probe.S_DONE] = base.DONE_MAGIC
        values[probe.S_RESTORED] = base.HOOK_ORIG
        values[probe.S_PHASE] = probe.PHASE_SCRATCH
        values[probe.S_STAGE] = 7
        values[probe.S_ALLOCATOR] = 0xC3073A5C
        values[probe.S_HANDLE] = handle
        values[probe.S_ALLOC_END] = handle + probe.ALLOC_SIZE
        values[probe.S_WORK_BASE] = handle
        values[probe.S_WORK_END] = handle + probe.WORK_CAP
        values[probe.S_WORK_GUARD] = handle + probe.WORK_CAP
        values[probe.S_TABLE_BASE] = handle + probe.TABLE_OFFSET
        values[probe.S_TABLE_TEMP] = handle + probe.TABLE_TEMP_OFFSET
        values[probe.S_SCRATCH_SOURCE] = 0x53B16000
        values[probe.S_GUARD_WORK] = probe.GUARD_MAGIC
        values[probe.S_GUARD_TABLE] = probe.GUARD_MAGIC
        values[probe.S_GUARD_TEMP] = probe.GUARD_MAGIC
        values[probe.S_PREFLIGHT] = probe.PREFLIGHT_MAGIC
        values[probe.S_SCRATCH_MAGIC] = probe.SCRATCH_MAGIC
        values[probe.S_END_GUARD] = handle + probe.ALLOC_SIZE - 4
        values[probe.S_GUARD_END] = probe.GUARD_MAGIC
        values[probe.S_TABLE_GUARD] = (
            handle + probe.TABLE_OFFSET + probe.TABLE_BYTES
        )
        values[probe.S_TEMP_GUARD] = (
            handle + probe.TABLE_TEMP_OFFSET + probe.TABLE_BYTES
        )
        return values

    def test_power_preflight_build_is_small_and_transparent(self):
        code = probe.build_image("preflight", base.DEFAULT_FPSUP)
        words = base.words_from(code)
        self.assertGreater(len(code), 0)
        self.assertLessEqual(probe.CODE + len(code), probe.PREFLIGHT_CODE_LIMIT)
        self.assertEqual(words[0], 0xE92D503F)  # push r0-r5,r12,lr
        self.assertEqual(words[-2], 0xE51FF004)
        self.assertEqual(words[-1], base.REAL_WRITE)

    def test_preflight_source_cannot_touch_allocator_codec_or_dng(self):
        source = probe.PREFLIGHT_SOURCE.read_text()
        for forbidden in (
            "F_ALLOC",
            "F_GET",
            "F_INIT",
            "F_ENC",
            "F_SIZE",
            "0xC001CF78",
            "0xC001D038",
            "0xC05A6890",
            "0xC05A6920",
            "0xC05A6990",
        ):
            self.assertNotIn(forbidden, source)
        # The preflight may read the segment/DNG for its exact FHD shape guard,
        # but its only stores are to STATE through r4 and the hook through r1.
        self.assertEqual(source.count("str     r2, [r1]"), 1)
        store_lines = [line.strip() for line in source.splitlines() if line.strip().startswith("str")]
        self.assertTrue(store_lines)
        for line in store_lines:
            self.assertTrue("[r4," in line or line == "str     r2, [r1]", line)

    def test_the_codec_mode_gate_accepts_every_mode_a_setup_path_can_latch(self):
        """0xC37CF87C selects the engine's owner; it is not a busy flag.

        C06301F8 latches 1 and C06303E4 latches 2; 0 means closed and is
        written only by the close routine at C062FF7A, so a non-zero read is
        the normal state whenever the engine is open.  A ``cmp #0 / bne`` gate
        refused three live runs in a row with ERR_MODE_BUSY and cost a wrong
        diagnosis ("the camera is stuck").  Both preflights must refuse only
        a value no setup path can produce.
        """
        for source in (probe.PREFLIGHT_SOURCE, probe.SCRATCH_SOURCE):
            text = source.read_text()
            self.assertIn("CODEC_MODE", text)
            # The gate is whatever sits between the read and the refusal; a
            # source that refuses on any non-zero value has no "bhi" branch
            # to codec_busy at all, which is exactly what must not pass.
            self.assertIn("codec_busy", text, source.name)
            branch = text.index("codec_busy", text.index("S_CODEC_MODE]"))
            end = text.index("\n", branch)
            gate = text[text.rindex("CODEC_MODE", 0, branch):end]
            self.assertIn("cmp", gate, gate)
            self.assertIn("#2", gate, gate)
            self.assertIn("bhi", gate, gate)
            self.assertNotIn("bne", gate, gate)

    def test_the_async_phase_starts_the_engine_and_not_only_programs_it(self):
        """Programming the engine is not running it.

        C062FFF8 writes the geometry and the five pointers into 0x300D0040 and
        memcpys the request to 0xC37CF884 -- and stops.  The GO bit is
        ``[0x300D0000] |= 2``, set only by the mode-1 starter C0630208 (wrapper
        C062FEB1), which first requires the owner word 0xC37CF87C to read 1.
        The 2026-09-29 run submitted a perfectly valid request, polled seven
        times and timed out on every one, because nothing had ever started it.
        """
        source = probe.ENCODE_SOURCE.read_text()
        self.assertIn("0xC062FEB1", source)
        submit = source.index("ENG_SUBMIT\n    blx")
        start = source.index("ENG_START\n    blx")
        self.assertLess(submit, start, "the engine is started AFTER the submit")
        between = source[submit:start]
        self.assertNotIn("blx", between.replace("ENG_SUBMIT\n    blx", "", 1),
                         "nothing may run between programming and starting")
        self.assertIn("str     r0, [r5, #A_START_RET]", source)

    def test_the_async_phase_points_the_hardware_where_F_SIZE_reads(self):
        """F_SIZE does not read wherever the hardware wrote.

        F_SIZE delegates to C062FA48, which takes the table pointer from
        ``[0xC302CDD4+0x10]`` and the loop bound from ``[0xC302CDEC]``.  A
        request whose size-table pointer is anything else leaves the sizes
        somewhere F_SIZE will never look, and a tile count left at the driver's
        value makes the loop run zero times -- which is exactly how the
        2026-09-29 run completed a real encode and still reported sum 0.
        """
        source = probe.ENCODE_SOURCE.read_text()
        self.assertIn("0xC302CDEC", source)
        block = source[source.index("#elif PHASE == 11"):]
        table = block[block.index("str     r0, [r7, #0x24]") - 200:
                      block.index("str     r0, [r7, #0x24]")]
        self.assertIn("ldr     r0, [r5, #A_ENG10]", table)
        # round_up arithmetic between the read and the store is the bug.
        self.assertNotIn("bic", table)
        self.assertIn("LDA     r1, TILE_COUNT_VAR", block)
        self.assertIn("mov     r0, #PROBE_TILES", block)

    def test_a_record_from_another_boot_is_refused(self):
        """DRAM survives a short power cycle, so a record from the PREVIOUS
        boot names itself, carries the mark, has the right role and says held.
        On 2026-09-30 one of those let an arm through on a block this boot
        never owned; only reading the ticks by hand caught it.
        """
        record = {"present": True, "role": 1, "state_word": probe.LIFE_HELD,
                  "state": "held", "handle": 0x45177000, "allocator": 0xC3073A5C,
                  "bytes": probe.ALLOC_SIZE, "end": 0x45177000 + probe.ALLOC_SIZE,
                  "own_mark": True, "boot": 0x11111111}
        mark = {"magic_ok": True, "names_itself": True, "raw": [0, 0]}
        probe.check_life_record(record, mark, 0x45177000, 1, boot=0x11111111)
        with self.assertRaises(probe.ProbeError) as caught:
            probe.check_life_record(record, mark, 0x45177000, 1,
                                    boot=0x22222222)
        self.assertIn("boot", str(caught.exception))

    def test_the_boot_check_is_skipped_only_when_none_is_offered(self):
        """A caller with no identity to offer gets the old behaviour, not a
        silent pass on a mismatched one."""
        record = {"present": True, "role": 1, "state_word": probe.LIFE_HELD,
                  "state": "held", "handle": 0x45177000, "allocator": 0xC3073A5C,
                  "bytes": probe.ALLOC_SIZE, "end": 0x45177000 + probe.ALLOC_SIZE,
                  "own_mark": True, "boot": 0x11111111}
        mark = {"magic_ok": True, "names_itself": True, "raw": [0, 0]}
        probe.check_life_record(record, mark, 0x45177000, 1)

    def test_both_record_writers_stamp_the_boot(self):
        """The STORE, not just the constant. Naming L_BOOT in an .equ and
        never writing it leaves the field zero, which matches a zeroed record
        and defeats the whole check."""
        for path, reg in ((probe.SCRATCH_SOURCE, "r10"),
                          (probe.SCRATCH_SOURCE.with_name(
                              "acquire_second_block.S"), "r11")):
            text = path.read_text()
            self.assertIn(f"str     r12, [{reg}, #L_BOOT]", text, path.name)
            # and it must not clobber the magic the next store writes
            at = text.index("LDA     r12, LOAD_DONE_US")
            self.assertIn("ldr     r12, [r12]", text[at:at + 200], path.name)
            self.assertNotIn("LDA     r0, LOAD_DONE_US", text, path.name)

    def test_the_acquire_path_passes_the_boot_it_read(self):
        """A validator that accepts None must be given a value by every caller
        that has one, or the check is present and never exercised."""
        source = pathlib.Path(probe.__file__).read_text()
        body = source[source.index("def acquire_second_block("):]
        body = body[:body.index("\ndef ")]
        self.assertIn("shell.read_words(LOAD_DONE_US, 1)[0]", body)
        self.assertIn("boot=boot", body)

    def test_scratch_build_is_transparent_and_bounded(self):
        code = probe.build_image("scratch", base.DEFAULT_FPSUP)
        words = base.words_from(code)
        self.assertGreater(len(code), 0)
        self.assertLessEqual(probe.CODE + len(code), probe.SCRATCH_CODE_LIMIT)
        self.assertGreater(probe.CODE + len(code), 0xC072FC00)
        self.assertEqual(words[0], 0xE92D55FF)
        self.assertEqual(words[-2:], [0xE51FF004, base.REAL_WRITE])

    def test_scratch_source_only_allocates_and_never_calls_codec_or_power(self):
        source = probe.SCRATCH_SOURCE.read_text()
        self.assertIn("F_ALLOC", source)
        self.assertIn("F_GET", source)
        for forbidden in (
            "F_INIT",
            "F_ENC",
            "F_SIZE",
            "F_PWR",
            "F_CLK",
            "0xC05A6890",
            "0xC05A6920",
            "0xC05A6990",
            "0xC0708C61",
            "0xC07095A9",
            "F_ADDR",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("LDA     r2, 0x400", source)
        self.assertIn("str     r3, [sp]", source)

    def test_scratch_success_requires_exact_retained_layout(self):
        values = self.successful_scratch_state()
        self.assertTrue(probe.scratch_preflight_success(values))
        for index in (
            probe.S_DONE,
            probe.S_RESTORED,
            probe.S_PHASE,
            probe.S_STAGE,
            probe.S_ERROR,
            probe.S_ALLOCATOR,
            probe.S_HANDLE,
            probe.S_ALLOC_END,
            probe.S_WORK_BASE,
            probe.S_WORK_END,
            probe.S_WORK_GUARD,
            probe.S_TABLE_BASE,
            probe.S_TABLE_TEMP,
            probe.S_SCRATCH_SOURCE,
            probe.S_SCRATCH_CODEC_MODE,
            probe.S_GUARD_WORK,
            probe.S_GUARD_TABLE,
            probe.S_GUARD_TEMP,
            probe.S_PREFLIGHT,
            probe.S_SCRATCH_MAGIC,
            probe.S_END_GUARD,
            probe.S_GUARD_END,
            probe.S_TABLE_GUARD,
            probe.S_TEMP_GUARD,
        ):
            broken = list(values)
            if index == probe.S_ALLOCATOR:
                broken[index] = 0
            else:
                broken[index] ^= 1
            if index == probe.S_SCRATCH_CODEC_MODE:
                broken[index] = 3   # see the power test: 1 is legal
            self.assertFalse(probe.scratch_preflight_success(broken), index)

    def test_scratch_arm_requires_power_proof_and_preserves_magic_seed(self):
        code = probe.build_image("scratch", base.DEFAULT_FPSUP)
        shell = FakeShell()
        with self.assertRaises(probe.ProbeError):
            probe.arm_scratch_preflight(shell, code)
        self.assertEqual(shell.writes, [])

        shell.state = self.successful_preflight_state()
        with contextlib.redirect_stdout(io.StringIO()):
            probe.arm_scratch_preflight(shell, code)
        self.assertEqual(shell.site, base.HOOK_ARMED)
        self.assertEqual(shell.writes[0][0:2], ("verified", probe.CODE))
        seeded = shell.writes[1]
        self.assertEqual(seeded[0:2], ("verified", probe.STATE))
        self.assertEqual(seeded[2][probe.S_PREFLIGHT], probe.PREFLIGHT_MAGIC)
        self.assertEqual(sum(value != 0 for value in seeded[2]), 1)
        self.assertEqual(shell.writes[2], ("set", base.HOOK_SITE, (base.HOOK_ARMED,)))

    def test_successful_scratch_state_refuses_duplicate_arm(self):
        code = probe.build_image("scratch", base.DEFAULT_FPSUP)
        shell = FakeShell()
        shell.state = self.successful_scratch_state()
        with self.assertRaises(probe.ProbeError):
            probe.arm_scratch_preflight(shell, code)
        self.assertEqual(shell.writes, [])

    def test_retained_scratch_cannot_be_orphaned_by_power_rearm(self):
        code = probe.build_image("preflight", base.DEFAULT_FPSUP)
        shell = FakeShell()
        shell.state = self.successful_scratch_state()
        with self.assertRaises(probe.ProbeError):
            probe.arm_power_preflight(shell, code)
        self.assertEqual(shell.writes, [])

    def test_preflight_has_balanced_order_and_failure_cleanup(self):
        source = probe.PREFLIGHT_SOURCE.read_text()
        power_on = source.index("mov     r0, #1\n    LDA     r12, F_PWR")
        clock_on = source.index("mov     r1, #1\n    LDA     r12, F_CLK")
        clock_off = source.index("mov     r1, #0\n    LDA     r12, F_CLK")
        power_off = source.index("mov     r0, #0\n    LDA     r12, F_PWR", clock_off)
        self.assertLess(power_on, clock_on)
        self.assertLess(clock_on, clock_off)
        self.assertLess(clock_off, power_off)
        cleanup = source[source.index("cleanup_power_after_clock_failure:") :]
        self.assertIn("LDA     r12, F_PWR", cleanup)
        self.assertIn("mov     r0, #ERR_CLK_ON", cleanup)
        # The reviewed firmware-matching policy does not cut power after a
        # failed clock-off; that path goes straight to finish.
        clock_off_failure = source[
            source.index("clock_off_failed:") : source.index("power_off_failed:")
        ]
        self.assertNotIn("F_PWR", clock_off_failure)

    def test_preflight_success_requires_every_proof_field(self):
        values = self.successful_preflight_state()
        self.assertTrue(probe.power_preflight_success(values))

        for index in (
            probe.S_COUNT,
            probe.S_DONE,
            probe.S_RESTORED,
            probe.S_STAGE,
            probe.S_ERROR,
            probe.S_CLEANUP_ERROR,
            probe.S_CODEC_MODE,
            probe.S_R2,
            probe.S_LR,
            probe.S_BUFFER,
            probe.S_LENGTH,
            probe.S_SOURCE,
            probe.S_PWR_ON,
            probe.S_CLK_ON,
            probe.S_CLK_OFF,
            probe.S_PWR_OFF,
            probe.S_PREFLIGHT,
        ):
            broken = list(values)
            broken[index] = 1
            if index == probe.S_COUNT:
                broken[index] = 0
            if index in (
                probe.S_DONE,
                probe.S_RESTORED,
                probe.S_LR,
                probe.S_BUFFER,
                probe.S_LENGTH,
                probe.S_SOURCE,
                probe.S_PREFLIGHT,
            ):
                broken[index] ^= 1
            if index == probe.S_CODEC_MODE:
                # 1 is a legal owner value now, so it proves nothing here; 3 is
                # the smallest value no setup path can latch.  [C 2026-09-29]
                broken[index] = 3
            self.assertFalse(probe.power_preflight_success(broken), index)

    def test_status_reports_proven_preflight(self):
        shell = FakeShell()
        shell.state = self.successful_preflight_state()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            probe.show_status(shell)
        self.assertIn("power_preflight           PASS", output.getvalue())

    def test_preflight_gate_is_read_only_and_rechecks_context(self):
        shell = FakeShell()
        shell.state = self.successful_preflight_state()
        self.assertEqual(probe.require_power_preflight(shell), shell.state)
        self.assertEqual(shell.writes, [])

        shell.context[0] ^= 1
        with self.assertRaises(base.ProbeError):
            probe.require_power_preflight(shell)
        self.assertEqual(shell.writes, [])

    def test_arm_uses_context_guard_and_hook_is_final_write(self):
        code = probe.build_image("preflight", base.DEFAULT_FPSUP)
        shell = FakeShell()
        with contextlib.redirect_stdout(io.StringIO()):
            probe.arm_power_preflight(shell, code)
        self.assertEqual(shell.site, base.HOOK_ARMED)
        self.assertEqual(shell.writes[0][0:2], ("verified", probe.CODE))
        self.assertEqual(
            shell.writes[1],
            ("verified", probe.STATE, (0,) * probe.STATE_WORDS),
        )
        self.assertEqual(shell.writes[2], ("set", base.HOOK_SITE, (base.HOOK_ARMED,)))

    def test_context_mismatch_refuses_before_any_write(self):
        code = probe.build_image("preflight", base.DEFAULT_FPSUP)
        context_words = list(base.EXPECTED_CONTEXT)
        context_words[0] ^= 1
        shell = FakeShell(context=context_words)
        with self.assertRaises(base.ProbeError):
            probe.arm_power_preflight(shell, code)
        self.assertEqual(shell.writes, [])

    def test_immediate_fire_is_not_rearmed(self):
        code = probe.build_image("preflight", base.DEFAULT_FPSUP)
        shell = FakeShell(fire_immediately=True)
        # Model the in-probe completion fields required by base.arm_site.
        original_set = shell.set_word

        def set_and_complete(address, value):
            original_set(address, value)
            if value == base.HOOK_ARMED:
                shell.state[base.S_DONE] = base.DONE_MAGIC
                shell.state[base.S_RESTORED] = base.HOOK_ORIG

        shell.set_word = set_and_complete
        with contextlib.redirect_stdout(io.StringIO()):
            probe.arm_power_preflight(shell, code)
        arm_writes = [entry for entry in shell.writes if entry[0] == "set"]
        self.assertEqual(len(arm_writes), 1)
        self.assertEqual(shell.site, base.HOOK_ORIG)

    def test_guarded_restore_rejects_bad_context(self):
        bad = list(base.EXPECTED_CONTEXT)
        bad[-1] ^= 1
        shell = FakeShell(context=bad, site=base.HOOK_ARMED)
        with self.assertRaises(base.ProbeError):
            base.restore_probe(shell)
        self.assertEqual(shell.site, base.HOOK_ARMED)

    def test_encode_layout_is_aligned_in_bounds_and_disjoint(self):
        # Derived from the layout constants, not pinned to one arena size:
        # the arena moved on 2026-09-23 when it was sized from the worst-case
        # output bound instead of the packed raster.
        handle = 0x45166C00
        layout = probe.encode_layout(handle)
        self.assertTrue(probe.encode_layout_is_safe(layout))
        self.assertEqual(layout["work"], (handle, handle + probe.WORK_CAP))
        self.assertEqual(layout["size_table"][0], handle + probe.TABLE_OFFSET)
        self.assertEqual(layout["temporary_table"][0],
                         handle + probe.TABLE_TEMP_OFFSET)
        self.assertLessEqual(
            layout["temporary_table_guard"][1], layout["allocation"][1]
        )
        self.assertFalse(probe.encode_layout_is_safe(probe.encode_layout(1)))

    def test_encode_design_builds_but_live_action_is_refused_before_shell(self):
        code = probe.build_image("encode", base.DEFAULT_FPSUP)
        self.assertLessEqual(probe.CODE + len(code), probe.ENCODE_DESIGN_LIMIT)
        self.assertEqual(base.words_from(code)[-2:], [0xE51FF004, base.REAL_WRITE])

        called = False

        def forbidden_shell(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("CameraShell must not be constructed")

        argv = ["single_frame_codec_probe.py", "encode"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            base, "CameraShell", forbidden_shell
        ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            self.assertEqual(probe.main(), 1)
        self.assertFalse(called)

    def test_encode_struct_uses_init6_for_output_and_init7_for_table(self):
        source = probe.ENCODE_SOURCE.read_text()
        self.assertIn("str     r8, [sp, #0x18]", source)
        self.assertIn("compressed output/work", source)
        self.assertIn("str     r0, [sp, #0x1C]", source)
        self.assertIn("size-table base", source)
        self.assertIn("mov     r2, #TILE_COUNT", source)
        self.assertIn("str     r1, [r4, #S_COMP_SIZE]", source)

    # ---- PHASE=2: consume the retained block, then release it --------------

    @staticmethod
    def lda_pair(register, value):
        """The movw/movt pair the LDA macro emits for an absolute address."""
        low, high = value & 0xFFFF, value >> 16
        return [
            0xE3000000 | ((low >> 12) << 16) | (register << 12) | (low & 0xFFF),
            0xE3400000 | ((high >> 12) << 16) | (register << 12) | (high & 0xFFF),
        ]

    @classmethod
    def calls(cls, words, address):
        pair = cls.lda_pair(12, address)
        return any(words[index:index + 2] == pair for index in range(len(words) - 1))

    def successful_encode_state(self, handle=0x45166C00, *, encode_return=1,
                                entered=1, adopted=1, error=0):
        values = [0] * probe.STATE_WORDS
        values[probe.S_COUNT] = 1
        values[probe.S_DONE] = base.DONE_MAGIC
        values[probe.S_RESTORED] = base.HOOK_ORIG
        values[probe.S_PHASE] = probe.PHASE_ENCODE
        values[probe.S_STAGE] = 8
        values[probe.S_ERROR] = error
        values[probe.S_ALLOCATOR] = 0xC3A00000
        values[probe.S_HANDLE] = handle
        values[probe.S_ADOPTED] = adopted
        values[probe.S_ENCODE_STARTED] = entered
        values[probe.S_INIT_RET] = 1
        values[probe.S_ENC_RET] = encode_return
        values[probe.S_SIZE_RET] = 1
        values[probe.S_T0] = 0xFFFFFF00
        values[probe.S_T1] = (0xFFFFFF00 + 21000) & 0xFFFFFFFF
        values[probe.S_COMPRESSED] = 1_300_000
        for index in range(probe.TILE_COUNT):
            values[probe.S_TILE0 + index] = 1_300_000 // probe.TILE_COUNT
        if encode_return == 1 and not error:
            values[probe.S_FREE_DONE] = probe.FREE_MAGIC
        else:
            values[probe.S_FREE_ERROR] = probe.ERR_RETAINED
        return values

    def test_live_encode_build_is_bounded_and_transparent(self):
        code = probe.build_image("encode-live", base.DEFAULT_FPSUP)
        words = base.words_from(code)
        self.assertLessEqual(probe.CODE + len(code), probe.ENCODE_LIVE_LIMIT)
        self.assertLess(probe.ENCODE_LIVE_LIMIT, 0xC0730000)
        self.assertEqual(words[0], 0xE92D55FF)
        self.assertEqual(words[-2:], [0xE51FF004, base.REAL_WRITE])

    def test_live_encode_allocates_nothing_and_frees(self):
        live = base.words_from(probe.build_image("encode-live", base.DEFAULT_FPSUP))
        design = base.words_from(probe.build_image("encode", base.DEFAULT_FPSUP))
        for allocator_call in (0xC001CF78, 0xC001D038):
            self.assertTrue(self.calls(design, allocator_call))
            self.assertFalse(self.calls(live, allocator_call))
        self.assertTrue(self.calls(live, 0xC001D2B8))
        self.assertFalse(self.calls(design, 0xC001D2B8))
        for shared in (0xC05A6890, 0xC05A6920, 0xC05A6990, 0xC002B6E0):
            self.assertTrue(self.calls(live, shared))

    def test_reviewed_design_image_is_pinned(self):
        """Adding a phase must not shift PHASE=0/1.

        The pin moved once, on 2026-09-23: fixing the tile-height wiring adds
        one LDA (8 bytes) to every image that builds the encoder structs.
        """
        code = probe.build_image("encode", base.DEFAULT_FPSUP)
        self.assertEqual(len(code), 1276)
        self.assertEqual(probe.CODE + len(code), 0xC072FCFC)

    def test_live_encode_arm_requires_a_complete_scratch_result(self):
        code = probe.build_image("encode-live", base.DEFAULT_FPSUP)
        shell = FakeShell()
        with self.assertRaises(probe.ProbeError):
            probe.arm_live_encode(shell, code)
        self.assertEqual(shell.writes, [])

        shell.state = self.successful_preflight_state()
        with self.assertRaises(probe.ProbeError):
            probe.arm_live_encode(shell, code)
        self.assertEqual(shell.writes, [])

        broken = self.successful_scratch_state()
        broken[probe.S_GUARD_WORK] ^= 1
        shell.state = broken
        with self.assertRaises(probe.ProbeError):
            probe.arm_live_encode(shell, code)
        self.assertEqual(shell.writes, [])

    def test_live_encode_seeds_only_the_handle_and_both_proofs(self):
        code = probe.build_image("encode-live", base.DEFAULT_FPSUP)
        shell = FakeShell()
        scratch = self.successful_scratch_state()
        shell.state = scratch
        with contextlib.redirect_stdout(io.StringIO()):
            probe.arm_live_encode(shell, code)
        self.assertEqual(shell.writes[0][0:2], ("verified", probe.CODE))
        seeded = shell.writes[1]
        self.assertEqual(seeded[0:2], ("verified", probe.STATE))
        values = seeded[2]
        self.assertEqual(values[probe.S_PREFLIGHT], probe.PREFLIGHT_MAGIC)
        self.assertEqual(values[probe.S_SCRATCH_MAGIC], probe.SCRATCH_MAGIC)
        self.assertEqual(values[probe.S_ALLOCATOR], scratch[probe.S_ALLOCATOR])
        self.assertEqual(values[probe.S_HANDLE], scratch[probe.S_HANDLE])
        self.assertEqual(sum(value != 0 for value in values), 4)
        self.assertEqual(shell.writes[2], ("set", base.HOOK_SITE, (base.HOOK_ARMED,)))

    def test_encode_report_measures_across_a_counter_wrap(self):
        report = probe.encode_report(self.successful_encode_state())
        self.assertEqual(report["elapsed_us"], 21000)
        self.assertEqual(report["mpix_per_second"], round(1936 * 1090 / 21000, 1))
        self.assertLess(report["elapsed_us"], report["frame_budget_24p_us"])
        self.assertEqual(report["ratio"], round(probe.RAW_BYTES / 1_300_000, 3))
        self.assertTrue(report["block_released"])
        self.assertFalse(report["release_refused"])
        self.assertTrue(report["sampled_source_markers_unchanged"])
        # the claim must stay scoped to what was actually compared
        self.assertNotIn("source_dng_byte_identical", report)
        self.assertNotIn("source_unmodified", report)
        self.assertEqual(report["source_bytes_compared"], 16)
        self.assertEqual(report["source_bytes_total"], 0x318200)
        self.assertFalse(report["full_source_hash_available"])

    def test_failed_encode_reports_no_rate_and_keeps_the_block(self):
        values = self.successful_encode_state(encode_return=0, error=18)
        report = probe.encode_report(values)
        self.assertNotIn("elapsed_us", report)
        self.assertNotIn("mpix_per_second", report)
        self.assertTrue(report["release_refused"])
        self.assertFalse(report["block_released"])

    def test_release_policy_is_asymmetric_in_the_source(self):
        source = probe.ENCODE_SOURCE.read_text()
        self.assertIn("ldr     r0, [r4, #S_ADOPTED]", source)
        self.assertIn("ldr     r0, [r4, #S_ENC_STARTED]", source)
        self.assertIn("ERR_RETAINED", source)
        self.assertIn("str     r0, [r4, #S_ENC_STARTED]   /* set BEFORE", source)
        # the marker must precede the timed call, never follow it
        self.assertLess(source.index("S_ENC_STARTED]   /* set BEFORE"),
                        source.index("LDA     r12, F_ENC"))

    # ---- PHASE=3: sustained run ------------------------------------------

    def test_sustained_build_is_bounded_below_its_log(self):
        code = probe.build_image("encode-sustained", base.DEFAULT_FPSUP)
        words = base.words_from(code)
        end = probe.CODE + len(code)
        self.assertLessEqual(end, probe.SUSTAINED_CODE_LIMIT)
        self.assertLessEqual(end, probe.LOG, "code must not reach into the log")
        log_end = probe.LOG + probe.AGG_OFF + probe.AGG_WORDS * 4
        self.assertLessEqual(log_end, 0xC0730000)
        self.assertEqual(words[0], 0xE92D55FF)
        self.assertEqual(words[-2:], [0xE51FF004, base.REAL_WRITE])

    def test_sustained_still_allocates_nothing_and_frees(self):
        live = base.words_from(probe.build_image("encode-sustained", base.DEFAULT_FPSUP))
        for allocator_call in (0xC001CF78, 0xC001D038):
            self.assertFalse(self.calls(live, allocator_call))
        self.assertTrue(self.calls(live, 0xC001D2B8))

    def test_one_shot_images_are_unchanged_by_phase_three(self):
        for kind, size, end in (("encode", 1276, 0xC072FCFC),
                                ("encode-live", 1428, 0xC072FD94)):
            code = probe.build_image(kind, base.DEFAULT_FPSUP)
            self.assertEqual(len(code), size, kind)
            self.assertEqual(probe.CODE + len(code), end, kind)

    def test_rearm_is_the_last_action_and_is_doubly_bounded(self):
        """Scoped to the sustained loop it is about.

        It used to scan the whole file and rely on the first `LDA r1,
        HOOK_ARMED` in it being the sustained phases'. A later phase with its
        own re-arm broke that - `loop = ... if False else source` was a
        half-finished attempt to scope it already.
        """
        source = probe.ENCODE_SOURCE.read_text()
        # rindex: the guard appears more than once and the sustained tail is
        # the last of them.
        start = source.rindex("#if PHASE == 3 || PHASE == 6")
        loop = source[start:source.index("final_frame:", start)]
        rearm = loop.index("LDA     r1, HOOK_ARMED")
        # both ceilings are tested before the re-arm store
        self.assertLess(loop.index("cmp     r5, #FRAMES"), rearm)
        self.assertLess(loop.index("cmp     r0, #FRAMES * 2"), rearm)
        # and an error stops the loop before either
        self.assertLess(loop.index("bne     final_frame"), rearm)
        self.assertIn("dsb     sy", loop[rearm:rearm + 400])
        self.assertIn("isb", loop[rearm:rearm + 400])

    def test_every_rearm_in_the_file_comes_after_the_entry_restore(self):
        """Whatever phase it belongs to. The site is put back at entry so that a
        fault below costs one frame rather than every frame after it."""
        source = probe.ENCODE_SOURCE.read_text()
        restore = source.index("str     r8, [r7]")
        first_rearm = source.index("LDA     r1, HOOK_ARMED")
        self.assertLess(restore, first_rearm)
        # and every re-arm is bounded by a ceiling tested just above it
        at = 0
        while True:
            at = source.find("LDA     r1, HOOK_ARMED", at)
            if at < 0:
                break
            window = source[max(0, at - 400):at]
            self.assertIn("#FRAMES", window,
                          f"a re-arm at {at} with no ceiling above it")
            at += 1

    def test_sustained_arm_requires_scratch_and_clears_the_log(self):
        code = probe.build_image("encode-sustained", base.DEFAULT_FPSUP)
        shell = FakeShell()
        with self.assertRaises(probe.ProbeError):
            probe.arm_sustained_encode(shell, code)
        self.assertEqual(shell.writes, [])

        shell.state = self.successful_scratch_state()
        with contextlib.redirect_stdout(io.StringIO()):
            probe.arm_sustained_encode(shell, code)
        targets = [(kind, address) for kind, address, _ in shell.writes]
        self.assertEqual(targets[0], ("verified", probe.CODE))
        self.assertEqual(targets[1], ("verified", probe.LOG))
        self.assertEqual(targets[2], ("verified", probe.STATE))
        self.assertEqual(targets[3], ("set", base.HOOK_SITE))
        cleared = shell.writes[1][2]
        self.assertEqual(len(cleared), probe.AGG_OFF // 4 + probe.AGG_WORDS)
        self.assertTrue(all(word == 0 for word in cleared))
        # the rows and every aggregate slot are covered by that clear
        self.assertGreaterEqual(len(cleared) * 4,
                                probe.RING_FRAMES * probe.LOG_WORDS_PER_FRAME * 4)

    def test_log_reader_skips_unwritten_records(self):
        frames = probe.RING_FRAMES
        words = [0] * (frames * probe.LOG_WORDS_PER_FRAME)
        for index, (elapsed, size) in enumerate(((18000, 1200000), (19500, 1310000))):
            words[index * 4:index * 4 + 4] = [elapsed, size, 0x53B02C00 + index * 0x1000,
                                              0x124D3112 + index]

        class LogShell(FakeShell):
            def read_words(self, address, count):
                if address == probe.LOG:
                    return words[:count]
                return super().read_words(address, count)

        records = probe.read_log(LogShell(), frames)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["elapsed_us"], 18000)
        self.assertEqual(records[1]["compressed_bytes"], 1310000)
        self.assertNotEqual(records[0]["buffer"], records[1]["buffer"])
        self.assertIn("first_pixel", records[0])

    def test_rows_ring_and_worst_case_covers_every_frame(self):
        source = probe.ENCODE_SOURCE.read_text()
        # rows are masked into a ring, so a long run cannot walk off the log
        self.assertIn("and     r7, r5, #RING_MASK", source)
        self.assertIn(".equ RING_MASK,       7", source)
        # the worst-case trackers are updated unconditionally, not inside the
        # ring-row path, so they see frames whose rows were overwritten
        agg = source.index("LDA     r6, LOG + AGG_OFF")
        ring = source.index("and     r7, r5, #RING_MASK")
        self.assertLess(ring, agg)
        self.assertIn("strhi   r2, [r6, #AGG_MAX_US]", source)
        self.assertIn("strhi   r3, [r6, #AGG_MAX_BYTES]", source)
        self.assertIn("addhs   r0, r0, #1", source)
        # the ring must fit below the aggregates
        self.assertLessEqual(probe.RING_FRAMES * probe.LOG_WORDS_PER_FRAME * 4,
                             probe.AGG_OFF)

    def test_aggregate_reader(self):
        class AggShell(FakeShell):
            def read_words(self, address, count):
                if address == probe.LOG + probe.AGG_OFF:
                    return [41999, 1400000, 2][:count]
                return super().read_words(address, count)

        aggregates = probe.read_aggregates(AggShell())
        self.assertEqual(aggregates["max_elapsed_us"], 41999)
        self.assertEqual(aggregates["max_compressed_bytes"], 1400000)
        self.assertEqual(aggregates["frames_over_budget"], 2)
        self.assertGreater(aggregates["max_elapsed_us"], probe.BUDGET_US)

    # ---- P0-1: tile height must actually reach the codec -------------------

    @staticmethod
    def build_with(defines, fpsup=None):
        import exact_dng_writer_probe as base_mod
        assemble, _ = base_mod.load_assembler(fpsup or base_mod.DEFAULT_FPSUP)
        return base_mod.words_from(assemble(probe.ENCODE_SOURCE, defines=defines))

    def test_tile_height_is_wired_not_aliased_to_width(self):
        """A 512x368 build must differ from 512x512, and carry 368.

        Before 2026-09-23 all four tile fields were written from TILE_W, so a
        368 build silently emitted 512x512 and this test would fail.
        """
        square = self.build_with(("PHASE=2", "TILE_W=512", "TILE_H=512"))
        product = self.build_with(("PHASE=2", "TILE_W=512", "TILE_H=368"))
        self.assertNotEqual(square, product,
                            "tile height does not reach the encoder structs")
        self.assertTrue(self.calls_imm(product, 368),
                        "the 368 immediate is absent from the 512x368 build")
        self.assertFalse(self.calls_imm(square, 368))

    @classmethod
    def calls_imm(cls, words, value):
        """True if any register is loaded with `value` via the LDA macro."""
        return any(cls.lda_pair(reg, value)[0] in words for reg in range(13))

    def test_tile_fields_are_distinct_stores(self):
        source = probe.ENCODE_SOURCE.read_text()
        for offset, note in (("0x0C", "init tile width"), ("0x34", "enc  tile width"),
                             ("0x10", "init tile height"), ("0x38", "enc  tile height")):
            self.assertIn(f"str     r0, [sp, #{offset}]            /* {note} */", source)
        width_load = source.index("LDA     r0, TILE_W")
        height_load = source.index("LDA     r0, TILE_H")
        self.assertLess(width_load, height_load)
        # the height stores must follow the height load, not the width load
        self.assertGreater(source.index("str     r0, [sp, #0x10]"), height_load)
        self.assertGreater(source.index("str     r0, [sp, #0x38]"), height_load)

    # ---- P0-3: the arena must cover the worst case, before the fact --------

    def test_output_bound_exceeds_the_raster(self):
        """Lossless JPEG expands on noise; the raster size is not a capacity."""
        bound = probe.worst_case_output_bytes(512, 368)
        raster = 0x304CB0
        self.assertGreater(bound, raster,
                           "a bound at or below the raster assumes compression")
        self.assertGreater(bound / raster, 1.2)

    def test_arena_covers_the_bound_with_margin(self):
        bound = probe.worst_case_output_bytes()
        self.assertGreaterEqual(probe.WORK_CAP, bound)
        margin = (probe.WORK_CAP - bound) / bound
        self.assertGreater(margin, 0.02, "arena margin is too thin to absorb "
                                         "a Huffman table unlike the model")

    def test_tables_sit_above_the_arena_not_inside_it(self):
        self.assertGreaterEqual(probe.TABLE_OFFSET, probe.WORK_CAP)
        self.assertGreaterEqual(probe.TABLE_TEMP_OFFSET,
                                probe.TABLE_OFFSET + probe.TABLE_BYTES)
        self.assertLessEqual(probe.TABLE_TEMP_OFFSET + probe.TABLE_BYTES + 4,
                             probe.ALLOC_SIZE)
        # the old layout put the final table 4 KiB above the cap, inside reach
        self.assertGreater(probe.TABLE_OFFSET - probe.WORK_CAP, 0)

    def test_unsafe_geometry_is_refused_at_build_time(self):
        """512x512 has no safe layout in 4 MiB and must never assemble."""
        with self.assertRaises(probe.ProbeError):
            probe.require_safe_arena(512, 512)
        bound = probe.worst_case_output_bytes(512, 512)
        self.assertGreater(bound, probe.ALLOC_SIZE)

    def test_encode_builds_enforce_the_arena(self):
        original = probe.WORK_CAP
        try:
            probe.WORK_CAP = 0x305000          # the pre-2026-09-23 arena
            for kind in ("encode", "encode-live", "encode-sustained"):
                with self.assertRaises(probe.ProbeError, msg=kind):
                    probe.build_image(kind, base.DEFAULT_FPSUP)
        finally:
            probe.WORK_CAP = original
        probe.build_image("encode", base.DEFAULT_FPSUP)

    # ---- work package 4: claimed placement and cache publication ----------

    def fake_claim_factory(self):
        state = {"bump": arena_bounds()[0], "handed": {}}
        def claim(name, size):
            if name in state["handed"]:
                return state["handed"][name]
            at = state["bump"]
            state["bump"] += (size + 7) & ~7
            state["handed"][name] = at
            return at
        return claim, state

    def test_placed_build_bakes_in_the_claimed_addresses(self):
        claim, _ = self.fake_claim_factory()
        code, placed = probe.place_and_build("encode-sustained",
                                             base.DEFAULT_FPSUP, claim=claim)
        default = probe.build_image("encode-sustained", base.DEFAULT_FPSUP)
        self.assertEqual(len(code), len(default), "claim length would be wrong")
        self.assertNotEqual(code, default)
        words = base.words_from(code)
        # the claimed state address is present, the historical one is gone
        self.assertTrue(self.calls_imm(words, placed["lossless.codec.state"]))
        self.assertFalse(self.calls_imm(words, 0xC072F700))
        self.assertFalse(self.calls_imm(words, 0xC072FF00))

    def test_claims_do_not_land_in_the_shell_template_block(self):
        claim, _ = self.fake_claim_factory()
        _, placed = probe.place_and_build("encode-sustained",
                                          base.DEFAULT_FPSUP, claim=claim)
        for name, at in placed.items():
            self.assertLess(at, 0xC072F050,
                            f"{name} at 0x{at:08X} is in the worker/template area")
            self.assertGreaterEqual(at, arena_bounds()[0], name)

    def test_armed_word_follows_the_claimed_code_address(self):
        claim, _ = self.fake_claim_factory()
        _, placed = probe.place_and_build("encode-live",
                                          base.DEFAULT_FPSUP, claim=claim)
        import probe_placement as placement
        expected = placement.armed_word(base.HOOK_SITE,
                                        placed["lossless.codec.code"])
        self.assertNotEqual(expected, 0xEB00333F,
                            "a claimed address must not reproduce the old constant")
        self.assertEqual(placement.decode_bl(base.HOOK_SITE, expected),
                         placed["lossless.codec.code"])

    def test_arming_always_publishes_now_that_placement_is_the_only_path(self):
        """There is no unpublished arm to fall back to."""
        self.assertFalse(hasattr(probe, "PLACED_SESSION"))
        source = pathlib.Path(probe.__file__).read_text()
        self.assertNotIn("--placed", source,
                         "an opt-in leaves a half-converted world, which is "
                         "the failure cave.py's rule exists to stop")

    def test_publication_precedes_arming_and_aborts_on_failure(self):
        order = []

        class RecordingShell(FakeShell):
            def set_word(self, address, value):
                order.append(("arm", address))
                super().set_word(address, value)

        shell = RecordingShell()
        result = probe.publish_then_arm(shell, call=lambda fn: order.append(("publish", fn)) or 0)
        self.assertEqual([step for step, _ in order][:2], ["publish", "publish"])
        self.assertIn("arm", [step for step, _ in order])
        self.assertEqual(result["method"], "rom-dcache-icache/callfn/v1")

        order.clear()
        failing = RecordingShell()
        def boom(fn):
            order.append(("publish", fn))
            raise RuntimeError("callfn did not return")
        with self.assertRaises(RuntimeError):
            probe.publish_then_arm(failing, call=boom)
        self.assertNotIn("arm", [step for step, _ in order])
        self.assertEqual(failing.site, base.HOOK_ORIG, "armed despite no publication")

    def test_reported_layout_matches_the_constants(self):
        """A stale layout line is what a live entry condition gets read from."""
        code = probe.build_image("encode-live", base.DEFAULT_FPSUP)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            probe.describe_image("encode-live", code)
        printed = buffer.getvalue()
        self.assertIn(f"+0x{probe.WORK_CAP:X}", printed)
        self.assertIn(f"+0x{probe.TABLE_OFFSET:X}", printed)
        self.assertIn(f"+0x{probe.TABLE_TEMP_OFFSET:X}", printed)
        self.assertIn(f"{probe.TILE_W}x{probe.TILE_H}", printed)
        # Superseded values must not appear anywhere in the report. This is
        # the line a live entry condition is read from, so a stale number here
        # is worse than a wrong one somewhere quiet.
        for stale in ("0x305000", "0x306000",   # pre-2026-09-23 arena
                      "0x3E0000", "0x3E1000",   # pre-2026-09-23 arena, moved
                      "3,957,832",              # pre-2026-09-24 float bound
                      "3,957,839",              # the tool's pre-integer bound
                      "4,063,232", "2.66%"):    # arena and margin from both
            self.assertNotIn(stale, printed, f"stale value {stale} in the report")

        # The compressed report must state the SMALLER capacity and say why.
        compressed = io.StringIO()
        with contextlib.redirect_stdout(compressed):
            probe.describe_image("compress", probe.build_image("compress",
                                                               base.DEFAULT_FPSUP))
        text = compressed.getvalue()
        self.assertIn(f"{probe.output_capacity('compress'):,} B", text)
        self.assertIn(f"+0x{probe.OUT_OFF:X}", text)
        self.assertNotIn(f"capacity {probe.WORK_CAP:,} B", text,
                         "the compressed report must not show the whole arena")

    def test_scratch_and_encode_share_one_layout(self):
        """The scratch phase writes the guards the encode phase checks.

        These constants live in two .S files. When only one moved, scratch
        wrote its guards at +0x305000 while encode looked at +0x3E0000, and
        the chain failed at the host gate with error 0 - a split that reads
        like a pass everywhere except the one check that compares them.
        """
        import re
        def constants(path):
            text = path.read_text()
            out = {}
            for name in ("WORK_CAP", "TABLE_OFF", "TABLE_OFFSET",
                         "TABLE_TMP_OFF", "ALLOC_SIZE"):
                m = re.search(rf"^\.equ {name},\s*(0x[0-9A-Fa-f]+)", text, re.M)
                if m:
                    out[name.replace("TABLE_OFFSET", "TABLE_OFF")] = int(m.group(1), 16)
            return out

        encode = constants(probe.ENCODE_SOURCE)
        scratch = constants(probe.SCRATCH_SOURCE)
        for key in ("WORK_CAP", "TABLE_OFF", "TABLE_TMP_OFF", "ALLOC_SIZE"):
            self.assertIn(key, encode)
            self.assertIn(key, scratch)
            self.assertEqual(encode[key], scratch[key],
                             f"{key} differs between the two probe sources")
        self.assertEqual(encode["WORK_CAP"], probe.WORK_CAP)
        self.assertEqual(encode["TABLE_OFF"], probe.TABLE_OFFSET)
        self.assertEqual(encode["TABLE_TMP_OFF"], probe.TABLE_TEMP_OFFSET)

        # OUT_OFF and the tile geometry are what the shared precheck divides
        # and multiplies by, so a drift between the two files would make the
        # host prove a capacity the camera does not use. OUT_OFF is
        # preprocessor-conditional and TILE_* are #defines, so neither shows
        # up in the .equ sweep above; check them where they are written.
        text = probe.ENCODE_SOURCE.read_text()
        out_offs = [int(v, 0) for v in
                    re.findall(r"^\.equ OUT_OFF,\s*(\S+?),?\s*(?:/\*|$)", text, re.M)]
        self.assertEqual(sorted(out_offs), [0, probe.OUT_OFF],
                         "the two OUT_OFF branches must be 0 and the host's")
        self.assertIn("#if PHASE == 5 || PHASE == 6", text)
        for name, value in (("TILE_W", probe.TILE_W), ("TILE_H", probe.TILE_H)):
            self.assertIn(f"#define {name} {value}\n", text)

    # ---- retention and release -------------------------------------------

    def test_a_multi_entry_phase_is_not_behind_the_one_shot_guard(self):
        """The guard sits ABOVE the hook restore, so an entry it turns away never
        puts the site back. A phase that needs several entries and is guarded as
        one-shot therefore leaves the hook armed for the whole clip - which is
        what happened on 2026-09-29: 103 entries, no polls, hook still armed."""
        source = probe.ENCODE_SOURCE.read_text()
        guard = source.index("ldr     r3, [r4, #S_COUNT]")
        restore = source.index("LDA     r7, HOOK_SITE")
        self.assertLess(guard, restore, "the restore moved above the guard")
        block = source[guard:restore]
        self.assertIn("#elif PHASE == 11", block,
                      "PHASE 11 needs several entries and must not use the "
                      "one-shot guard")
        one_shot = block.index("cmp     r3, #1")
        eleven = block.index("#elif PHASE == 11")
        self.assertLess(eleven, one_shot, "PHASE 11 still falls through to it")

    def test_the_async_submit_creates_the_flag_before_it_submits(self):
        """Stage one measured [0xC2F2E60C] as -42 = E_NOEXS after F_INIT alone, so
        a submit that trusts that word would hand the engine work whose
        completion nobody can wait for."""
        source = probe.ENCODE_SOURCE.read_text()
        block = source[source.index("#ifdef ASYNC_SUBMIT\n    /* ---- build the request"):]
        block = block[:block.index("#endif")]
        create = block.index("LDA     r12, FLAG_CREATE")
        submit = block.index("LDA     r12, ENG_SUBMIT")
        self.assertLess(create, submit, "the flag is created after the submit")
        self.assertIn("blt     async_no_flag", block,
                      "a negative id must stop it submitting")
        self.assertLess(block.index("blt     async_no_flag"), submit)

    def test_the_async_request_is_built_from_the_record_not_from_registers(self):
        """Keeping the engine fields in registers across F_INIT and the flag call
        is how the flag-id read came to clobber the size-table length."""
        source = probe.ENCODE_SOURCE.read_text()
        block = source[source.index("#ifdef ASYNC_SUBMIT\n    /* ---- build the request"):]
        block = block[:block.index("#endif")]
        for field in ("A_ENG0", "A_ENG8", "A_ENGC", "A_ENG10", "A_ENG14"):
            self.assertRegex(block, rf"ldr\s+r\d+, \[r5, #{field}\]",
                             f"{field} is not read back from the record")

    def test_the_async_submit_never_waits(self):
        """The phase's whole claim. A wait anywhere in the submit path would make
        it the synchronous encode it is meant to replace."""
        source = probe.ENCODE_SOURCE.read_text()
        block = source[source.index("#ifdef ASYNC_SUBMIT\n    /* ---- build the request"):]
        block = block[:block.index("#endif")]
        self.assertNotIn("TWAI_FLG", block)
        self.assertNotIn("F_ENC", block)

    def test_every_block_retaining_phase_cannot_reach_F_FREE_at_all(self):
        """Not skipped over: not assembled.

        PHASE 11 was NOT on this list on 2026-09-29. Its first frame fell into
        the release path, F_FREE handed the 4 MiB block back, and every later
        frame read its own record area out of memory the allocator had given to
        someone else - the recorded 'engine fields' were another owner's pixels.
        A phase that keeps a block across frames belongs here by construction,
        not by remembering to check.
        """
        retaining = ("mirror", "compress", "compress-sustained", "copy-back",
                     "bound-probe", "bound-probe-frame", "bound-probe-two",
                     "bound-probe-two-copy", "bound-probe-two-ramp",
                     "async-inspect")
        for kind in retaining:
            words = base.words_from(probe.build_image(kind, base.DEFAULT_FPSUP))
            self.assertFalse(self.calls(words, probe.F_FREE),
                             f"{kind} can reach F_FREE")
            self.assertNotIn(probe.F_FREE, words,
                             f"{kind} carries F_FREE as a constant")

    def test_keep_images_cannot_reach_F_FREE_at_all(self):
        """Not skipped over: not assembled."""
        probe.KEEP_BLOCK["active"] = True
        try:
            for kind in probe.KEEP_KINDS:
                kept = base.words_from(probe.build_image(kind, base.DEFAULT_FPSUP))
                self.assertFalse(self.calls(kept, probe.F_FREE),
                                 f"{kind} --keep still carries F_FREE")
        finally:
            probe.KEEP_BLOCK["active"] = False
        for kind in probe.KEEP_KINDS:
            normal = base.words_from(probe.build_image(kind, base.DEFAULT_FPSUP))
            self.assertTrue(self.calls(normal, probe.F_FREE),
                            f"{kind} lost its release path")

    def test_keep_is_smaller_because_the_release_code_is_gone(self):
        normal = probe.build_image("encode-live", base.DEFAULT_FPSUP)
        probe.KEEP_BLOCK["active"] = True
        try:
            kept = probe.build_image("encode-live", base.DEFAULT_FPSUP)
        finally:
            probe.KEEP_BLOCK["active"] = False
        self.assertLess(len(kept), len(normal))

    def retained_shell(self, **kwargs):
        """A shell whose block A exists and carries its record and mark, and
        whose block B slot is empty - the state after scratch alone."""
        shell = FakeShell()
        shell.state = self.retained_state(**kwargs)
        handle = shell.state[probe.S_HANDLE]
        shell.install_mark(handle)
        shell.install_life_record(handle, "A", handle=handle, role=1)
        return shell

    def retained_state(self, entered=0, enc_ret=0, error=0, freed=0):
        values = self.successful_scratch_state()
        values[probe.S_ENCODE_STARTED] = entered
        values[probe.S_ENC_RET] = enc_ret
        values[probe.S_ERROR] = error
        values[probe.S_FREE_DONE] = freed
        return values

    def test_release_frees_a_block_the_codec_never_touched(self):
        shell = self.retained_shell()
        calls = []
        result = probe.release_retained_scratch(
            shell, call=lambda fn, **kw: calls.append((fn, kw)) or (True, 0))
        self.assertEqual(calls[0][0], probe.F_FREE)
        self.assertEqual(calls[0][1]["r1"], shell.state[probe.S_HANDLE])
        self.assertTrue(result["recorded"])
        self.assertEqual(shell.state[probe.S_FREE_DONE], probe.FREE_MAGIC)

    def test_release_frees_after_a_successful_kept_encode(self):
        shell = self.retained_shell(entered=1, enc_ret=1)
        result = probe.release_retained_scratch(shell, call=lambda fn, **kw: (True, 0))
        self.assertTrue(result["entered"])
        self.assertEqual(shell.state[probe.S_FREE_DONE], probe.FREE_MAGIC)

    def test_release_refuses_when_the_codec_result_is_unknown(self):
        for entered, enc_ret, error in ((1, 0, 0), (1, 1, 18), (1, 2, 0)):
            shell = self.retained_shell(entered=entered, enc_ret=enc_ret,
                                        error=error)
            called = []
            with self.assertRaises(probe.ProbeError):
                probe.release_retained_scratch(
                    shell, call=lambda fn, **kw: called.append(fn) or (True, 0))
            self.assertEqual(called, [], "freed a block the codec may still own")

    def test_release_is_idempotent_and_refuses_a_bad_handle(self):
        shell = self.retained_shell(freed=probe.FREE_MAGIC)
        self.assertTrue(probe.release_retained_scratch(
            shell, call=lambda fn, **kw: (True, 0))["already_released"])

        shell = FakeShell()
        bad = self.retained_state()
        bad[probe.S_HANDLE] = 0xC3000000          # not an allocator handle
        shell.state = bad
        with self.assertRaises(probe.ProbeError):
            probe.release_retained_scratch(shell, call=lambda fn, **kw: (True, 0))

    def test_release_does_not_record_if_the_free_never_returned(self):
        shell = self.retained_shell()
        with self.assertRaises(probe.ProbeError):
            probe.release_retained_scratch(shell, call=lambda fn, **kw: (False, 0))
        self.assertNotEqual(shell.state[probe.S_FREE_DONE], probe.FREE_MAGIC)

    # ---- G4a mirror: the first destructive phase ---------------------------

    @staticmethod
    def mirror_block(source):
        """The PHASE=4 body only -- not the SOURCE_EXIT define of the same name."""
        start = source.index("/* MIRROR.")
        return source[start:source.index("#elif PHASE == 0", start)]

    def test_mirror_touches_no_codec_and_never_frees(self):
        """Its whole point is that only the buffer changes, not the bytes."""
        mirror = base.words_from(probe.build_image("mirror", base.DEFAULT_FPSUP))
        for name, address in (("F_INIT", 0xC05A6890), ("F_ENC", 0xC05A6920),
                              ("F_SIZE", 0xC05A6990), ("F_ALLOC", 0xC001CF78),
                              ("F_GET", 0xC001D038), ("F_FREE", 0xC001D2B8)):
            self.assertFalse(self.calls(mirror, address),
                             f"mirror image carries {name}")
        self.assertTrue(self.calls(mirror, 0xC002B6E0), "mirror should time itself")

    def test_mirror_keeps_the_length_unchanged(self):
        source = probe.ENCODE_SOURCE.read_text()
        block = self.mirror_block(source)
        self.assertIn("str     r8, [r0]", block, "node buffer is not redirected")
        self.assertIn("LDA     r1, DNG_LEN", block)
        self.assertIn("str     r1, [r0, #4]", block)
        self.assertNotIn("S_COMP_SIZE", block[:4000],
                         "mirror must not use a compressed length")

    def test_mirror_writes_through_the_uncached_view(self):
        source = probe.ENCODE_SOURCE.read_text()
        block = self.mirror_block(source)
        self.assertIn("LDA     r0, UNCACHED", block)
        self.assertIn("add     r6, r8, r0", block)
        self.assertIn("dsb     sy", block)
        self.assertIn(".equ UNCACHED,        0x40000000", source)

    def test_mirror_preserves_the_pointers_finish_source_needs(self):
        """The first live run clobbered r5/r6/r7 and raised a false ERR_SOURCE."""
        block = self.mirror_block(probe.ENCODE_SOURCE.read_text())
        self.assertIn("push    {r5, r6, r7, r10}", block)
        self.assertIn("pop     {r5, r6, r7, r10}", block)
        self.assertLess(block.index("push    {r5, r6, r7, r10}"),
                        block.index("mirror_copy:"))
        self.assertLess(block.index("pop     {r5, r6, r7, r10}"),
                        block.index("b       finish_source"))

    def test_mirror_watermarks_the_copy_in_tail_padding(self):
        """An identical copy cannot prove the redirect happened."""
        block = self.mirror_block(probe.ENCODE_SOURCE.read_text())
        self.assertIn("LDA     r0, MIRROR_MARK", block)
        self.assertIn("LDA     r1, DNG_LEN - 16", block)
        # the mark must land after the raster, in padding no decoder reads
        raster_end = 0x13400 + 0x304CB0
        self.assertGreater(0x318200 - 16, raster_end)

    def test_mirror_copies_the_whole_dng(self):
        source = probe.ENCODE_SOURCE.read_text()
        block = self.mirror_block(source)
        self.assertIn("LDA     r7, DNG_LEN", block)
        self.assertIn("ldm     r5!,", block)
        self.assertIn("stm     r6!,", block)
        self.assertIn("subs    r7, r7, #16", block)
        # 0x318200 is a multiple of 16, so the four-word loop terminates exactly
        self.assertEqual(0x318200 % 16, 0)

    # ---- G4b compressed write --------------------------------------------

    def test_compress_capacity_accounts_for_the_header(self):
        """The arena is shared, so the reported margin must not be the
        discard one. A stale report line is what a live entry condition reads."""
        self.assertEqual(probe.output_capacity("compress"),
                         probe.WORK_CAP - probe.OUT_OFF)
        self.assertEqual(probe.output_capacity("encode-live"), probe.WORK_CAP)
        bound = probe.worst_case_output_bytes()
        self.assertGreater(probe.output_capacity("compress"), bound)
        margin = (probe.output_capacity("compress") - bound) / bound
        self.assertGreater(margin, 0.02)

    def test_bound_matches_the_tool_exactly_at_every_geometry(self):
        """BUFFER_RESEARCH 4.3 refuses "within 64 bytes": the two estimates
        must be one formula. They were 7 bytes apart, and in the direction
        that matters - the probe's was the LOWER one, so the build gate that
        is supposed to refuse an unsafe geometry was the permissive copy."""
        import importlib.util
        tools = (pathlib.Path(probe.__file__).resolve().parents[3]
                 / "projects" / "lossless-sup" / "tools" / "output_bound.py")
        spec = importlib.util.spec_from_file_location("output_bound", tools)
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)

        self.assertEqual(probe.BOUND_BITS, tool.BOUND_BITS)
        self.assertEqual(probe.TILE_MARKER_BYTES, tool.TILE_MARKER_BYTES)
        # 14 must stay ABOVE the numerical supremum it stands in for, or the
        # integer that removed the rounding would have introduced a worse bug.
        self.assertLess(tool.max_bits_per_sample(), tool.BOUND_BITS)
        self.assertGreater(tool.max_bits_per_sample(), tool.BOUND_BITS - 1,
                           "14 must be the CEILING, not a loose round number")
        # The searched supremum itself, pinned: this superseded a test that
        # allowed the two bounds to differ by up to 64 bytes, which is the
        # tolerance BUFFER_RESEARCH 4.3 names and refuses, and is exactly what
        # let the 7-byte drift sit there looking checked.
        self.assertAlmostEqual(tool.max_bits_per_sample(), 13.9998, places=3)

        for tile_w, tile_h in ((512, 368), (512, 512), (256, 256),
                               (1024, 1090), (368, 512), (640, 480)):
            self.assertEqual(
                probe.worst_case_output_bytes(tile_w, tile_h),
                tool.bound(probe.FRAME_WIDTH, probe.FRAME_HEIGHT,
                           tile_w, tile_h)["bound_bytes"],
                f"tile {tile_w}x{tile_h}: the two bounds must be one number")

    def test_the_bound_is_above_every_size_the_camera_has_produced(self):
        """A bound the hardware has already exceeded is refuted, not tight."""
        import json, re
        ledgers = sorted((pathlib.Path(probe.__file__).resolve().parents[3]
                          / "projects" / "lossless-sup" / "build").glob("ledger-*.json"))
        self.assertTrue(ledgers, "no ledger entries to check the bound against")
        # Every key under which a measured codec output was recorded. Keeping
        # this list explicit is the point: a new ledger key that nothing here
        # matches would make the check pass by finding nothing.
        keys = ("compressed_bytes", "compressed_bytes_burst",
                "compressed_bytes_single", "payload_bytes", "payload",
                "tile_count_sum")
        bound, seen, largest = probe.worst_case_output_bytes(), 0, 0
        for path in ledgers:
            blob = json.dumps(json.loads(path.read_text()))
            for key in keys:
                for value in re.findall(rf'"{key}":\s*(\d+)', blob):
                    seen, largest = seen + 1, max(largest, int(value))
                    self.assertLessEqual(int(value), bound,
                                         f"{path.name}: {key}={value} exceeds the bound")
        # 11 recorded outputs as of 2026-09-24. This only ever rises as
        # ledgers are added; a fall means the sweep stopped matching and the
        # check started passing by finding nothing.
        self.assertGreaterEqual(seen, 11, "the ledger sweep matched almost nothing")
        # File lengths carry the 79,872-byte header on top of the payload, so
        # they are checked against the capacity the header leaves, not the arena.
        for key in ("file_bytes", "file_bytes_each",
                    "frame_1_bytes", "frame_2_bytes", "frame_4_bytes", "frame_8_bytes"):
            for path in ledgers:
                for value in re.findall(rf'"{key}":\s*(\d+)',
                                        json.dumps(json.loads(path.read_text()))):
                    self.assertLessEqual(int(value) - probe.OUT_OFF,
                                         probe.output_capacity("compress"),
                                         f"{path.name}: {key}={value} does not fit")
        self.assertLess(largest, bound,
                        "no measured output has yet come close to the bound; "
                        "it is a model, not a measurement")

    def test_device_checks_the_payload_against_the_shared_capacity(self):
        """The host precheck is advisory; the camera does its own sum. Before
        2026-09-24 the device compared that sum against the whole WORK_CAP,
        which is 0x13800 more room than the payload actually has when the
        header shares the arena, so an output in that band passed the check
        having already overwritten the guard and the size tables."""
        shared = probe.WORK_CAP - probe.OUT_OFF
        self.assertEqual(shared, 4048896)
        for kind in ("compress", "compress-sustained"):
            words = base.words_from(probe.build_image(kind, base.DEFAULT_FPSUP))
            self.assertTrue(self.calls_imm(words, shared),
                            f"{kind} must bound the payload by {shared}")
        # The phases that write at the arena base keep the full capacity,
        # and OUT_OFF is 0 there, so one expression serves both.
        for kind in ("encode", "encode-live", "encode-sustained"):
            words = base.words_from(probe.build_image(kind, base.DEFAULT_FPSUP))
            self.assertTrue(self.calls_imm(words, probe.WORK_CAP),
                            f"{kind} bounds the payload by the whole arena")

    def test_device_and_host_agree_on_every_kind_capacity(self):
        """One number, two implementations: a host precheck that passes an
        image the camera then rejects is the failure this guards."""
        source = probe.ENCODE_SOURCE.read_text()
        self.assertIn("LDA     r0, WORK_CAP - OUT_OFF", source)
        self.assertNotIn("""    LDA     r0, WORK_CAP
    cmp     r1, r0""", source)
        for kind in ("encode-live", "encode-sustained",
                     "compress", "compress-sustained"):
            words = base.words_from(probe.build_image(kind, base.DEFAULT_FPSUP))
            self.assertTrue(self.calls_imm(words, probe.output_capacity(kind)),
                            f"{kind}: device must carry output_capacity()")

    def test_every_codec_kind_goes_through_the_one_precheck(self):
        """The compressed kinds used to skip the precheck entirely: the
        geometry that build_image refuses for `encode` was assembled without
        complaint for the phase that has 79,872 FEWER bytes to write into."""
        original = (probe.TILE_W, probe.TILE_H)
        probe.TILE_W, probe.TILE_H = 512, 512      # bounds above the arena
        try:
            for kind in probe.CODEC_KINDS:
                with self.assertRaises(probe.ProbeError, msg=f"{kind} skipped it"):
                    probe.build_image(kind, base.DEFAULT_FPSUP)
            # "mirror" is not a codec kind and must stay assemblable: it
            # copies the stock DNG at its own fixed length.
            probe.build_image("mirror", base.DEFAULT_FPSUP)
        finally:
            probe.TILE_W, probe.TILE_H = original
        for kind in probe.CODEC_KINDS:
            probe.build_image(kind, base.DEFAULT_FPSUP)

    def test_the_kind_dispatch_has_no_unreachable_duplicate(self):
        """Both compress branches appeared twice after `mirror` was inserted.
        Identical today, so nothing broke - but an edit to the first copy
        would have been silently outranked by nothing at all, and an edit to
        the second would never have run."""
        text = pathlib.Path(probe.__file__).read_text()
        body = text[text.index("def build_image("):]
        body = body[:body.index("\n\ndef ")]
        for kind in probe.COMPRESSED_KINDS:
            self.assertEqual(body.count(f'elif kind == "{kind}":'), 1,
                             f"{kind} is dispatched more than once")

    # ---- PHASE=7 copy-back --------------------------------------------

    def test_copyback_never_hands_the_writer_our_buffer(self):
        """The whole point of copy-back: seg[0] is not touched.

        The compressed kinds write our arena handle into seg[0]; copy-back
        must not, because holding a buffer the card reads later is exactly
        what broke G5. `str r8, [r0]` is 0xE5808000."""
        REDIRECT = 0xE5808000
        compress = base.words_from(probe.build_image("compress", base.DEFAULT_FPSUP))
        copyback = base.words_from(probe.build_image("copy-back", base.DEFAULT_FPSUP))
        self.assertIn(REDIRECT, compress, "compress must redirect seg[0]")
        self.assertNotIn(REDIRECT, copyback, "copy-back must NOT touch seg[0]")

    def test_copyback_bounds_the_copy_by_the_length_it_was_handed(self):
        """S_LEN is seg[1] as captured on entry. The admission rule reads it,
        not a constant and not anything about the allocator."""
        source = probe.ENCODE_SOURCE.read_text()
        block = source[source.index("#if PHASE == 7"):source.index("copyback_done:")]
        self.assertIn("ldr     r3, [r4, #S_LEN]", block,
                      "the bound must be the handed length")
        self.assertIn("cmp     r2, r3", block)
        self.assertIn("bhi     copyback_skip", block)
        # rounded up to the copy unit, and the ROUNDED value is what is checked
        self.assertLess(block.index("adds    r2, r1, #15"), block.index("cmp     r2, r3"))
        self.assertIn("bic     r2, r2, #15", block)
        # no constant length anywhere in the admission test
        for constant in ("DNG_LEN", "0x318200", "3244544"):
            self.assertNotIn(constant, block,
                             f"{constant} must not appear: the bound is seg[1]")

    def test_copyback_uses_the_uncached_alias_on_both_sides(self):
        """EMULATION CANNOT CHECK THIS. In the emulator both aliases are mapped
        onto one host buffer, so writing the cached one lands in the same place
        and the mutation passes. On the camera it would not: G4a established
        that a CPU write is visible to the card DMA through the +0x40000000
        view with only a barrier. So the alias has to be pinned here."""
        source = probe.ENCODE_SOURCE.read_text()
        block = source[source.index("#if PHASE == 7"):source.index("copyback_done:")]
        self.assertIn("LDA     r0, UNCACHED", block)
        self.assertIn("add     r5, r8, r0", block, "source through the alias")
        self.assertIn("add     r6, r6, r0", block, "destination through the alias")
        # and a barrier after the copy, before the length is published
        copy = block.index("copyback_copy:")
        self.assertIn("dsb     sy", block[copy:])
        self.assertLess(block[copy:].index("dsb     sy"),
                        block[copy:].index("[r0, #4]"),
                        "the barrier must precede publishing the new length")

    def test_copyback_skip_path_cannot_reach_the_writer(self):
        """A frame that does not fit must leave the stock frame untouched, so it
        writes as RAW. The invariant that guarantees it: the skip path never
        loads S_R1, so it does not have the seg pointer and CANNOT change
        seg[1]; and it contains no block move, so it cannot touch the buffer.
        Everything it does write is our own state or our own result words."""
        source = probe.ENCODE_SOURCE.read_text()
        skip = source[source.index("copyback_skip:"):source.index("copyback_done:")]
        self.assertNotIn("S_R1", skip,
                         "the skip path must not be able to reach seg at all")
        self.assertNotIn("S_BUF", skip,
                         "nor the buffer")
        for forbidden in ("stm", "ldm"):
            self.assertNotIn(forbidden, skip, f"no {forbidden} on the skip path")
        self.assertIn("str     r0, [r4, #S_LEN_AFTER]", skip,
                      "the skip path must record that the length is unchanged")
        self.assertIn("ldr     r0, [r4, #S_LEN]", skip,
                      "and that value must be the STOCK length")

    def test_copyback_reports_which_way_it_went(self):
        """A silent failure is what G5 looked like. The state has to say."""
        source = probe.ENCODE_SOURCE.read_text()
        # The results live in the RETAINED BLOCK, not the state block. The state
        # block is full - 64 of 64 words - and the three that looked free
        # (0xDC, 0xE0, 0xE4) are the scratch phase's guard records, which
        # scratch_preflight_passed reads as a gate. Overlaying them would have
        # made every copy-back invalidate the gate admitting the next one.
        self.assertIn(f".equ RESULT_OFF,      0x{probe.RESULT_OFF:X}", source)
        for name in ("S_FILE_BYTES", "S_COPY_BYTES", "S_FIT"):
            self.assertNotIn(f".equ {name},", source,
                             f"{name} must not take a state word")
        block = source[source.index("#if PHASE == 7"):source.index("copyback_done:")]
        # The pointer is computed a few times rather than held in a register:
        # r0-r3 are the copy loop's and r5-r8/r10 are all live, and a spare
        # would have to be r9 or r11, which probe_entry does not preserve.
        loads = re.findall(r"LDA     r\d+, RESULT_OFF", block)
        self.assertGreaterEqual(len(loads), 3, "results, verdict and skip path")
        self.assertIn("str     r0, [r2, #0xC]", block,
                      "the post-encode timestamp must be recorded")
        # fit is the verdict, because `complete` overwrites S_STAGE with 8 on
        # the way out; stages 11 and 12 only survive a fault before that.
        self.assertIn("str     r1, [r0, #8]", block)
        self.assertIn("mov     r0, #11", block)     # progress: copied
        self.assertIn("mov     r0, #12", block)     # progress: skipped
        self.assertIn("Stage 11/12 are PROGRESS markers only", block)

    def test_the_result_words_are_clear_of_the_state_block(self):
        """RESULT_OFF must be inside the allocation and outside everything else
        the layout already uses, or the results would land on a table."""
        self.assertGreaterEqual(probe.RESULT_OFF, probe.TEMPLATE_META + 8)
        self.assertLess(probe.RESULT_OFF + 12, probe.ALLOC_SIZE - 4)
        self.assertGreater(probe.RESULT_OFF, probe.WORK_CAP,
                           "results must be above the output arena")

    def test_copyback_shares_the_compressed_layout_and_precheck(self):
        """It builds the same file, so it needs the same capacity proof."""
        self.assertIn("copy-back", probe.COMPRESSED_KINDS)
        self.assertIn("copy-back", probe.CODEC_KINDS)
        self.assertEqual(probe.output_capacity("copy-back"),
                         probe.WORK_CAP - probe.OUT_OFF)
        words = base.words_from(probe.build_image("copy-back", base.DEFAULT_FPSUP))
        self.assertTrue(self.calls_imm(words, probe.output_capacity("copy-back")))

    def test_copyback_allocates_nothing_and_frees_nothing(self):
        words = base.words_from(probe.build_image("copy-back", base.DEFAULT_FPSUP))
        for name, address in (("F_ALLOC", 0xC001CF78), ("F_FREE", probe.F_FREE)):
            self.assertFalse(self.calls(words, address),
                             f"copy-back must not call {name}")
        for name, address in (("F_INIT", 0xC05A6890), ("F_ENC", 0xC05A6920),
                              ("F_SIZE", 0xC05A6990)):
            self.assertTrue(self.calls(words, address),
                            f"copy-back must call {name}")

    def test_copyback_is_a_retaining_phase(self):
        """copy-back adopts the block, encodes into it and never frees, so
        release has to recognise it. Leaving 7 out made release refuse a block
        it was allowed to free, with a power cycle as the only recovery."""
        self.assertIn(7, probe.RETAINING_PHASES)
        words = base.words_from(probe.build_image("copy-back", base.DEFAULT_FPSUP))
        self.assertFalse(self.calls(words, probe.F_FREE),
                         "copy-back must not free the block itself")

    # ---- PHASE=9 bound probes ------------------------------------------

    def test_the_bound_probes_never_write_the_frame_or_the_segment(self):
        """All three variants are non-destructive by construction. The one that
        reads the camera's frame must READ it and nothing more: no store may go
        through the buffer pointer, and seg must never be written at all."""
        source = probe.ENCODE_SOURCE.read_text()
        block = source[source.index("#elif PHASE == 9"):source.index("#else\n    /* Build initStruct")]
        self.assertNotIn("S_R1", block, "the bound probes must not reach seg")
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("str", "stm")):
                continue
            # every store goes to our own state or through r5/r6, which are the
            # block pointers the variants set up; none goes through S_BUF
            self.assertNotIn("[r6, r", stripped.replace("[r6, r7]", ""),
                             f"unexpected indexed store: {stripped!r}")
        self.assertIn("READ ONLY", block,
                      "the frame variant must say so where it sets src")

    def test_the_two_block_variant_takes_its_handle_from_the_record(self):
        """The second handle used to arrive as a command-line argument. Now the
        camera acquires the block and the probe reads it from the record, which
        it verifies against the mark inside that block before writing 3.1 MB
        into it."""
        source = probe.ENCODE_SOURCE.read_text()
        # The checks sit BEFORE the push, because verify_life's failure paths
        # end at finish_source, which needs r5/r6/r7 and the entry stack.
        at = source.index("#ifdef PROBE_TWO\n    /* Block B's record and mark")
        block = source[at:source.index("push    {r5, r6, r7, r10}", at)]
        self.assertIn("#LIFE_OFF", block, "the record, not an argument")
        self.assertIn("bl      verify_life", block,
                      "the record and the block's own mark are checked")
        self.assertIn("ldr     r1, [r0, #L_HANDLE]", block,
                      "the handle comes out of the record")
        self.assertIn("lsr #30", block, "the 0x4..0x7 quarter, still")
        self.assertIn("LDA     r0, 0x3FF", block, "0x400 alignment, still")
        # and the failure paths it uses are the pre-push ones
        for label in ("bad_life", "bad_alias", "bad_align", "bad_overlap"):
            self.assertIn(label, block)
        self.assertIn("beq     bad_life", block, "and not null")
        # The frame check can only be made here: the acquire routine runs with
        # no frame in flight, and the fill loop below writes 3.1 MB.
        self.assertIn("ldr     r0, [r4, #S_BUF]", block,
                      "B must be disjoint from the frame the camera handed us")
        self.assertIn("probe_second_disjoint", block)
        # The word that must never be used as scratch, and no longer is.
        self.assertNotIn("S_SCRATCH_PROOF", block,
                         "that word is release's gate input")

    def test_verify_life_checks_the_record_and_the_block_it_describes(self):
        """Two different questions. The record says a block was acquired and is
        held; the mark, inside that block's memory and holding that block's own
        address, says the memory is the memory the record describes."""
        source = probe.ENCODE_SOURCE.read_text()
        at = source.index("verify_life:")
        block = source[at:source.index("bx      lr", at)]
        for needed, why in (
                ("LDA     r12, LIFE_MAGIC", "the record is a record"),
                ("cmpeq   r2, #LIFE_HELD", "and says held"),
                ("cmpeq   r2, r1", "and names the handle we were given"),
                ("#OWN_OFF", "the mark inside the block"),
                ("LDA     r12, OWN_MAGIC", "with the right magic"),
                ("cmpeq   r3, r1", "and the block's own address"),
                ("bne     bad_life", "a bad record fails"),
                ("bne     bad_own", "a bad mark fails separately")):
            self.assertIn(needed, block, why)
        # It must not return on failure: a return code a caller might forget to
        # test is worse than no return at all.
        self.assertNotIn("moveq   r0, #0", block)

    def test_the_bound_probe_verifies_block_a_before_adopting_it(self):
        """S_ADOPTED is what the release path reads. Verifying the record after
        setting it would let a block with a bad record be freed."""
        source = probe.ENCODE_SOURCE.read_text()
        at = source.index("#if PHASE == 9\n    /* The lifecycle record")
        adopted = source.index("str     r0, [r4, #S_ADOPTED]", at)
        self.assertLess(source.index("bl      verify_life", at), adopted)
        # and it is assembled for PHASE 9 only, so copy-back's reviewed image
        # is unchanged
        self.assertEqual(len(probe.build_image("copy-back", base.DEFAULT_FPSUP)),
                         1764, "copy-back's image changed size")

    def test_a_failed_encode_fails_instead_of_reporting_garbage(self):
        """The first run summed an untouched size table, reported 0xBCFFEFB0 as
        a compressed size, left error at 0 and reached stage 8. It looked
        clean."""
        source = probe.ENCODE_SOURCE.read_text()
        block = source[source.index("#elif PHASE == 9"):source.index("#else\n    /* Build initStruct")]
        self.assertIn("bne     probe_encode_failed", block)
        self.assertLess(block.index("bne     probe_encode_failed"),
                        block.index("LDA     r12, F_SIZE"),
                        "the check must precede reading the size table")

    def test_the_bound_probes_allocate_and_free_nothing(self):
        for kind in ("bound-probe", "bound-probe-frame", "bound-probe-two"):
            words = base.words_from(probe.build_image(kind, base.DEFAULT_FPSUP))
            for name, address in (("F_ALLOC", 0xC001CF78),
                                  ("F_GET", 0xC001D038),
                                  ("F_FREE", probe.F_FREE)):
                self.assertFalse(self.calls(words, address),
                                 f"{kind} must not call {name}")

    def test_compress_refuses_a_geometry_that_no_longer_fits(self):
        with self.assertRaises(probe.ProbeError):
            probe.require_safe_arena(512, 512, kind="compress")

    def test_compress_image_encodes_and_never_frees(self):
        image = base.words_from(probe.build_image("compress", base.DEFAULT_FPSUP))
        self.assertTrue(self.calls(image, 0xC05A6920), "compress must encode")
        self.assertTrue(self.calls(image, 0xC05A6990), "compress must read sizes")
        self.assertFalse(self.calls(image, 0xC001D2B8),
                         "the flush reads this block after we return")
        self.assertFalse(self.calls(image, 0xC001CF78))

    def test_compress_places_the_payload_past_the_header(self):
        source = probe.ENCODE_SOURCE.read_text()
        self.assertIn(".equ OUT_OFF,         0x13800", source)
        self.assertIn(".equ IFD_AT,          0x13400", source)
        self.assertIn(".equ HDR_BYTES,       78848", source)
        self.assertEqual(probe.OUT_OFF % 1024, 0, "codec needs 1 KiB alignment")
        self.assertEqual(probe.HDR_BYTES % 16, 0, "the copy loop moves 16 at a time")
        self.assertEqual(probe.IFD_AT, probe.HDR_BYTES)

    def test_compress_actually_moves_the_codec_output(self):
        """A .equ is invisible to #if, and that cost a live frame.

        The first compressed write kept `#if OUT_OFF`, which the preprocessor
        evaluated as 0, so the codec wrote at the arena base and the header
        copy destroyed two tiles. The check is on the built image: only the
        compressed phase may carry the offset as an immediate.
        """
        compressed = base.words_from(probe.build_image("compress", base.DEFAULT_FPSUP))
        discard = base.words_from(probe.build_image("encode-live", base.DEFAULT_FPSUP))
        self.assertTrue(self.calls_imm(compressed, probe.OUT_OFF),
                        "the compressed image never materialises OUT_OFF")
        self.assertFalse(self.calls_imm(discard, probe.OUT_OFF),
                         "a discard image should write at the arena base")

    def test_compress_commits_the_root_pointer_last(self):
        source = probe.ENCODE_SOURCE.read_text()
        start = source.index("hdr_copy:")
        body = source[start:source.index("b       finish_checked", start)]
        root = body.index("str     r0, [r10, #4]")
        self.assertLess(body.index("patch_tiles:"), root,
                        "tables must be written before the pointer that names them")
        self.assertLess(root, body.index("ldr     r0, [r4, #S_R1]"),
                        "the node must not be redirected before the header is valid")
        self.assertGreaterEqual(body.count("dsb     sy"), 3)

    def test_ifd_template_is_frame_invariant_and_fits(self):
        """The fixture is a frame committed in the repo, not one in /tmp.

        It used to be /tmp/wm002.DNG, which existed for as long as whoever put
        it there kept it. When the system cleaned /tmp the test silently became
        a skip - still printing OK - and stopped guarding the IFD template that
        copy-back builds every frame from. A fixture that can evaporate is a
        test that can evaporate.
        """
        import pathlib as pl
        frame = (pl.Path(__file__).resolve().parents[3] / "projects"
                 / "open-gate" / "captures" / "opengate_ev_bug_20260914"
                 / "dng" / "A001_083_FHD_f07.DNG")
        if not frame.is_file():
            self.skipTest(f"stock frame missing from the repo: {frame}")
        block, offset_table, count_table = probe.build_ifd_template(frame)
        self.assertEqual(len(block), probe.TEMPLATE_BYTES)
        self.assertGreaterEqual(offset_table, probe.IFD_AT)
        self.assertLess(count_table + 48, probe.OUT_OFF,
                        "tile tables must fit below the payload")

    # ---- G5 sustained compressed write ------------------------------------

    def test_sustained_compress_encodes_loops_and_never_frees(self):
        image = base.words_from(probe.build_image("compress-sustained",
                                                  base.DEFAULT_FPSUP))
        self.assertTrue(self.calls(image, 0xC05A6920), "must encode each frame")
        self.assertTrue(self.calls_imm(image, probe.OUT_OFF),
                        "must place the payload past the header")
        self.assertFalse(self.calls(image, 0xC001D2B8),
                         "the flush is still reading when we return")
        self.assertFalse(self.calls(image, 0xC001CF78))

    def test_sustained_compress_reuses_the_loop_that_was_proven(self):
        source = probe.ENCODE_SOURCE.read_text()
        self.assertIn("#if PHASE == 3 || PHASE == 6", source)
        # the re-arm and the ceilings are the ones PHASE=3 ran live
        self.assertIn("LDA     r1, HOOK_ARMED", source)
        self.assertIn("cmp     r5, #FRAMES", source)
        self.assertIn("cmp     r0, #FRAMES * 2", source)

    def test_frame_limit_is_bounded_and_shrinks_the_run(self):
        original = probe.FRAME_LIMIT["frames"]
        try:
            probe.FRAME_LIMIT["frames"] = 8
            self.assertEqual(probe.sustained_frames(), 8)
            small = probe.build_image("compress-sustained", base.DEFAULT_FPSUP)
            probe.FRAME_LIMIT["frames"] = 64
            big = probe.build_image("compress-sustained", base.DEFAULT_FPSUP)
            self.assertNotEqual(small, big, "the frame count is not in the image")
        finally:
            probe.FRAME_LIMIT["frames"] = original
        self.assertEqual(probe.sustained_frames(), probe.SUSTAINED_FRAMES)

    def test_sustained_compress_claims_a_log(self):
        claim, _ = self.fake_claim_factory()
        _, placed = probe.place_and_build("compress-sustained",
                                          base.DEFAULT_FPSUP, claim=claim)
        self.assertIn(probe.CLAIM_NAMES["log"], placed)

    def test_one_buffer_cannot_hold_two_worst_case_frames(self):
        """Double buffering does not fit, which is why a single buffer is
        tried first and the flush timing is what the run measures."""
        need = 2 * (probe.HDR_BYTES + probe.worst_case_output_bytes())
        self.assertGreater(need, probe.ALLOC_SIZE)



class TwoBlockLifecycleTests(unittest.TestCase):
    """Both allocations, from acquisition to reclaim, exercised as behaviour.

    Every test here runs the host code against a FakeShell whose blocks are
    real sparse memory, so a record that is not written, or written to the
    wrong place, fails instead of passing on a source-text match. The ones
    that matter are the failures: a second allocation that does not happen, a
    handle that overlaps, a record that is half written, a reclaim that frees
    the wrong block first.
    """

    HANDLE_A = 0x45000000
    HANDLE_B = 0x53B00000

    def setUp(self):
        patcher = mock.patch.object(
            probe, "native_caller", lambda *a, **k: (lambda fn, **kw: (True, 0)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.code = b"\x1e\xff\x2f\xe1" * 4
        placed = {probe.CLAIM_NAMES["code"]: probe.CODE,
                  probe.CLAIM_NAMES["state"]: probe.STATE}
        builder = mock.patch.object(probe, "place_and_build_acquire",
                                   lambda *a, **k: (self.code, placed))
        builder.start()
        self.addCleanup(builder.stop)
        publisher = mock.patch.object(probe.placement, "publish",
                                      lambda call, **k: [])
        publisher.start()
        self.addCleanup(publisher.stop)

    # ---- fixtures ------------------------------------------------------

    def shell_after_scratch(self, **record):
        shell = FakeShell()
        shell.state = CodecProbeTests.successful_scratch_state(
            handle=self.HANDLE_A)
        shell.install_mark(self.HANDLE_A)
        shell.install_life_record(self.HANDLE_A, "A", handle=self.HANDLE_A,
                                  role=1, **record)
        return shell

    def acquiring_call(self, shell, status=0, handle_b=None, **record):
        """A call that behaves like the camera's routine: on success it leaves a
        record and a mark behind, because that is what the host then checks."""
        handle_b = self.HANDLE_B if handle_b is None else handle_b

        def call(function, **registers):
            if function != probe.F_FREE and status == 0:
                shell.install_mark(handle_b)
                shell.install_life_record(self.HANDLE_A, "B", handle=handle_b,
                                          role=2, **record)
            return (True, status)
        return call

    # ---- acquisition ---------------------------------------------------

    def test_it_refuses_without_a_scratch_preflight(self):
        shell = FakeShell()
        with self.assertRaises(probe.ProbeError) as caught:
            probe.acquire_second_block(shell, base.DEFAULT_FPSUP,
                                       call=lambda fn, **kw: (True, 0))
        self.assertIn("scratch preflight", str(caught.exception))

    def test_it_refuses_when_block_a_has_no_record(self):
        shell = FakeShell()
        shell.state = CodecProbeTests.successful_scratch_state(
            handle=self.HANDLE_A)
        shell.add_block(self.HANDLE_A)           # memory, but nothing in it
        with self.assertRaises(probe.ProbeError) as caught:
            probe.acquire_second_block(shell, base.DEFAULT_FPSUP,
                                       call=lambda fn, **kw: (True, 0))
        self.assertIn("no lifecycle record", str(caught.exception))

    def test_it_refuses_when_block_a_does_not_carry_the_mark(self):
        shell = self.shell_after_scratch()
        shell.memory[self.HANDLE_A + probe.OWN_OFF] = 0        # mark wiped
        with self.assertRaises(probe.ProbeError) as caught:
            probe.acquire_second_block(shell, base.DEFAULT_FPSUP,
                                       call=lambda fn, **kw: (True, 0))
        self.assertIn("does not hold our mark", str(caught.exception))

    def test_it_refuses_a_record_that_names_a_different_handle(self):
        shell = self.shell_after_scratch()
        shell.install_life_record(self.HANDLE_A, "A", handle=0x46000000, role=1)
        with self.assertRaises(probe.ProbeError) as caught:
            probe.acquire_second_block(shell, base.DEFAULT_FPSUP,
                                       call=lambda fn, **kw: (True, 0))
        self.assertIn("record names", str(caught.exception))

    def test_it_refuses_a_record_whose_magic_is_missing(self):
        """A record written field by field can be interrupted. The magic goes in
        last for exactly this reason, so a partial record must fail."""
        shell = self.shell_after_scratch(magic=0)
        with self.assertRaises(probe.ProbeError):
            probe.acquire_second_block(shell, base.DEFAULT_FPSUP,
                                       call=lambda fn, **kw: (True, 0))

    def test_it_refuses_to_acquire_a_second_one_over_a_held_record(self):
        """Re-entry. Overwriting the record would lose the only handle by which
        the existing block could ever be freed."""
        shell = self.shell_after_scratch()
        shell.install_mark(self.HANDLE_B)
        shell.install_life_record(self.HANDLE_A, "B", handle=self.HANDLE_B,
                                  role=2)
        called = []
        with self.assertRaises(probe.ProbeError) as caught:
            probe.acquire_second_block(
                shell, base.DEFAULT_FPSUP,
                call=lambda fn, **kw: called.append(fn) or (True, 0))
        self.assertIn("already recorded as held", str(caught.exception))
        self.assertEqual(called, [], "it ran the routine anyway")

    def test_a_refused_acquisition_is_reported_not_raised(self):
        """The camera's own refusals are results: it freed anything it took."""
        for status, fragment in ((6, "allocator object"), (7, "returned no block"),
                                 (8, "wrong shape"), (9, "overlaps"),
                                 (10, "would not hold our mark")):
            shell = self.shell_after_scratch()
            result = probe.acquire_second_block(
                shell, base.DEFAULT_FPSUP,
                call=self.acquiring_call(shell, status=status))
            self.assertFalse(result["acquired"])
            self.assertEqual(result["status"], status)
            self.assertIn(fragment, result["meaning"])

    def test_a_successful_acquisition_is_checked_against_the_block(self):
        shell = self.shell_after_scratch()
        result = probe.acquire_second_block(shell, base.DEFAULT_FPSUP,
                                            call=self.acquiring_call(shell))
        self.assertTrue(result["acquired"])
        self.assertEqual(result["handle_a"], self.HANDLE_A)
        self.assertEqual(result["handle_b"], self.HANDLE_B)
        record = result["lifecycle"]["block_b"]
        self.assertEqual(record["role"], 2)
        self.assertEqual(record["state_word"], probe.LIFE_HELD)
        self.assertEqual(record["purpose"], "generated source")
        self.assertTrue(result["lifecycle"]["mark_b"]["names_itself"])

    def test_a_success_whose_block_lacks_the_mark_is_still_refused(self):
        """The routine says ok; the block does not carry the mark. The record
        describes a block, the mark IS the block, and they disagree."""
        shell = self.shell_after_scratch()
        call = self.acquiring_call(shell)

        def sabotaged(function, **registers):
            out = call(function, **registers)
            shell.memory[self.HANDLE_B + probe.OWN_OFF] = 0
            return out
        with self.assertRaises(probe.ProbeError) as caught:
            probe.acquire_second_block(shell, base.DEFAULT_FPSUP, call=sabotaged)
        self.assertIn("does not hold our mark", str(caught.exception))

    def test_a_success_reporting_an_overlapping_block_is_refused(self):
        shell = self.shell_after_scratch()
        inside = self.HANDLE_A + probe.ALLOC_SIZE // 2
        with self.assertRaises(probe.ProbeError) as caught:
            probe.acquire_second_block(
                shell, base.DEFAULT_FPSUP,
                call=self.acquiring_call(shell, handle_b=inside))
        self.assertIn("overlap", str(caught.exception))

    def test_a_success_whose_record_disagrees_about_the_size_is_refused(self):
        shell = self.shell_after_scratch()
        with self.assertRaises(probe.ProbeError) as caught:
            probe.acquire_second_block(
                shell, base.DEFAULT_FPSUP,
                call=self.acquiring_call(shell, size=probe.ALLOC_SIZE // 2))
        self.assertIn("which is not the", str(caught.exception))

    def test_a_call_that_never_returns_says_power_cycle(self):
        shell = self.shell_after_scratch()
        with self.assertRaises(probe.ProbeError) as caught:
            probe.acquire_second_block(shell, base.DEFAULT_FPSUP,
                                       call=lambda fn, **kw: (False, 0))
        message = str(caught.exception)
        self.assertIn("did not return", message)
        self.assertIn("Power-cycle", message)
        self.assertIn("do not release", message)

    # ---- arming --------------------------------------------------------

    def test_arming_refuses_without_a_recorded_second_block(self):
        """And says what to do instead. A missing record and a malformed one
        both fail, but they need different answers: the first means the acquire
        step has not been run, and the message has to say so rather than
        describing a record nobody wrote."""
        shell = self.shell_after_scratch()
        with self.assertRaises(probe.ProbeError) as caught:
            probe.arm_bound_probe_two(shell, self.code)
        message = " ".join(str(caught.exception).split())
        self.assertIn("Run `acquire-second` first", message)
        self.assertIn("Passing a handle by hand is no longer possible", message)

    def test_arming_refuses_a_second_block_that_is_not_held(self):
        for state in (probe.LIFE_UNCLEAR, probe.LIFE_FREED):
            shell = self.shell_after_scratch()
            shell.install_mark(self.HANDLE_B)
            shell.install_life_record(self.HANDLE_A, "B", handle=self.HANDLE_B,
                                      role=2, state=state)
            with self.assertRaises(probe.ProbeError) as caught:
                probe.arm_bound_probe_two(shell, self.code)
            self.assertIn("not held", " ".join(str(caught.exception).split()))

    def test_arming_parks_no_handle_and_clears_the_result_words(self):
        """The old design wrote the second handle into the block. The only write
        into a block at arming time now is clearing the eight result words - and
        that one is necessary: they are not part of the seeded state block, so a
        run that fails before reading the guards would otherwise report the
        PREVIOUS run's five words, which is what a pass looks like."""
        shell = self.shell_after_scratch()
        shell.install_mark(self.HANDLE_B)
        shell.install_life_record(self.HANDLE_A, "B", handle=self.HANDLE_B,
                                  role=2)
        stale = self.HANDLE_A + probe.RESULT_OFF + 0x14
        for i in range(8):
            shell.memory[stale + i * 4] = 0xA55AA55A
        probe.arm_bound_probe_two(shell, self.code)
        allowed = range(stale, stale + 32)
        for kind, address, values in shell.writes:
            if address in (probe.CODE, probe.STATE, base.HOOK_SITE):
                continue
            if shell.in_block(address, len(values)):
                self.assertIn(address, allowed,
                              f"arming wrote into a block at 0x{address:08X}")
        self.assertEqual([shell.memory[stale + i * 4] for i in range(8)],
                         [0] * 8, "the previous run's words survived")
        # and no record field was touched
        record = probe.read_life_record(shell, self.HANDLE_A, "B")
        self.assertEqual(record["handle"], self.HANDLE_B)
        self.assertEqual(record["state"], "held")

    # ---- reclaim -------------------------------------------------------

    def held_both(self, **record):
        shell = self.shell_after_scratch()
        shell.state[probe.S_ENCODE_STARTED] = 1
        shell.state[probe.S_ENC_RET] = 1
        shell.install_mark(self.HANDLE_B)
        shell.install_life_record(self.HANDLE_A, "B", handle=self.HANDLE_B,
                                  role=2, **record)
        return shell

    def test_release_frees_the_second_block_first(self):
        """Mechanical, not stylistic: block B's record lives inside block A."""
        shell = self.held_both()
        freed = []
        result = probe.release_retained_scratch(
            shell, call=lambda fn, **kw: freed.append(kw["r1"]) or (True, 0))
        self.assertEqual(freed, [self.HANDLE_B, self.HANDLE_A])
        self.assertEqual(result["second_block"]["handle"], self.HANDLE_B)
        self.assertTrue(result["second_block"]["recorded"])

    def test_release_records_the_second_free_in_its_record(self):
        shell = self.held_both()
        probe.release_retained_scratch(shell, call=lambda fn, **kw: (True, 0x55))
        record = probe.read_life_record(shell, self.HANDLE_A, "B")
        self.assertEqual(record["state"], "freed")
        self.assertEqual(record["free_result"], 0x55)
        # and the acquisition fields are untouched: they are the camera's
        self.assertEqual(record["handle"], self.HANDLE_B)
        self.assertEqual(record["role"], 2)
        self.assertTrue(record["own_mark"])

    def test_release_leaves_block_a_alone_if_the_second_free_hangs(self):
        shell = self.held_both()
        with self.assertRaises(probe.ProbeError) as caught:
            probe.release_retained_scratch(shell, call=lambda fn, **kw: (False, 0))
        self.assertIn("Block A has NOT been freed", str(caught.exception))
        self.assertNotEqual(shell.state[probe.S_FREE_DONE], probe.FREE_MAGIC)
        self.assertEqual(probe.read_life_record(shell, self.HANDLE_A,
                                                "B")["state"], "held")

    def test_release_frees_neither_when_the_codec_result_is_unknown(self):
        shell = self.held_both()
        shell.state[probe.S_ENC_RET] = 0          # entered, did not succeed
        freed = []
        with self.assertRaises(probe.ProbeError):
            probe.release_retained_scratch(
                shell, call=lambda fn, **kw: freed.append(kw) or (True, 0))
        self.assertEqual(freed, [], "the engine was handed both blocks")

    def test_release_skips_a_second_block_whose_ownership_is_unclear(self):
        shell = self.held_both(state=probe.LIFE_UNCLEAR)
        freed = []
        result = probe.release_retained_scratch(
            shell, call=lambda fn, **kw: freed.append(kw["r1"]) or (True, 0))
        self.assertEqual(freed, [self.HANDLE_A])
        self.assertEqual(result["second_block"]["skipped"], "ownership unclear")

    def test_release_refuses_a_second_record_that_lost_its_mark(self):
        shell = self.held_both()
        shell.memory[self.HANDLE_B + probe.OWN_OFF + 4] = 0     # names nothing
        with self.assertRaises(probe.ProbeError):
            probe.release_retained_scratch(shell, call=lambda fn, **kw: (True, 0))

    def test_release_marks_block_a_freed_before_it_frees_it(self):
        """Its record lives inside it: after the free those words belong to
        whoever gets the block next."""
        shell = self.held_both()
        states = []

        def call(function, **registers):
            if registers.get("r1") == self.HANDLE_A:
                states.append(probe.read_life_record(shell, self.HANDLE_A,
                                                     "A")["state"])
            return (True, 0)
        probe.release_retained_scratch(shell, call=call)
        self.assertEqual(states, ["freed"],
                         "block A was freed while its record still said held")

    def test_a_block_a_free_that_hangs_leaves_a_record_saying_freed(self):
        """The safe direction: freed means nothing may use it again."""
        shell = self.held_both()

        def call(function, **registers):
            return (registers.get("r1") != self.HANDLE_A, 0)
        with self.assertRaises(probe.ProbeError):
            probe.release_retained_scratch(shell, call=call)
        self.assertEqual(probe.read_life_record(shell, self.HANDLE_A,
                                                "A")["state"], "freed")
        self.assertNotEqual(shell.state[probe.S_FREE_DONE], probe.FREE_MAGIC)

    # ---- the record a bound verdict is judged on -----------------------

    def bound_shell(self, *, guards=None, tiles=None, error=0, rets=(1, 1, 1)):
        shell = self.held_both()
        state = shell.state
        state[probe.S_ERROR] = error
        state[probe.S_INIT_RET], state[probe.S_ENC_RET], state[probe.S_SIZE_RET] = rets
        sizes = tiles if tiles is not None else [260_000] * probe.TILE_COUNT
        state[probe.S_TILE0:probe.S_TILE0 + probe.TILE_COUNT] = sizes
        state[probe.S_TILE_COUNT] = probe.TILE_COUNT
        state[probe.S_COMPRESSED] = sum(sizes)
        at = self.HANDLE_A + probe.RESULT_OFF
        words = guards if guards is not None else [0xA55AA55A] * 5
        for i, word in enumerate(words):
            shell.memory[at + 0x14 + i * 4] = word
        shell.memory[at + 0x28] = probe.PROBE_DECLARED
        shell.memory[at + 0x2C] = 0xC302CDE0
        shell.memory[at + 0x30] = probe.PROBE_DECLARED
        return shell

    def test_the_bound_record_carries_everything_the_criterion_needs(self):
        import bound_criterion
        shell = self.bound_shell()
        record = probe.read_bound_result(shell, base.DEFAULT_FPSUP)
        self.assertEqual(record["variant"], "bound-probe-two")
        self.assertEqual(len(record["state"]), probe.STATE_WORDS)
        self.assertEqual(record["tile_sizes"], [260_000] * probe.TILE_COUNT)
        self.assertEqual(record["compressed_size"], record["tile_sum"])
        self.assertEqual(record["guards"], [0xA55AA55A] * 5)
        self.assertEqual(record["declared_constant"], probe.PROBE_DECLARED)
        self.assertEqual(record["declared_readback_address"], 0xC302CDE0)
        self.assertEqual(record["declared_readback_value"], probe.PROBE_DECLARED)
        self.assertEqual(len(record["image_sha256"]), 64)
        self.assertEqual(record["source_sha256"], hashlib.sha256(
            bound_criterion.generate_source()).hexdigest())
        self.assertTrue(record["lifecycle"]["block_b"]["present"])
        # every field the criterion requires, by name
        for name in ("init_ret", "enc_ret", "size_ret", "error", "tile_sizes",
                     "compressed_size", "guards", "variant", "source_sha256",
                     "image_sha256"):
            self.assertIn(name, record)

    def test_the_record_and_the_criterion_agree_on_a_clean_run(self):
        import bound_criterion
        record = probe.read_bound_result(self.bound_shell(), base.DEFAULT_FPSUP)
        demand = {"demand_exceeds_declared": True, "encoded_bytes": 3_392_834,
                  "source_sha256": record["source_sha256"]}
        self.assertEqual(bound_criterion.verdict(record, demand)["verdict"],
                         "bound_enforced")

    def test_a_record_from_a_failed_run_is_refused_by_the_criterion(self):
        """End to end, the way it would be read after a real run."""
        import bound_criterion
        for kwargs in (dict(error=18), dict(rets=(1, 0, 1)),
                       dict(rets=(1, 1, 0)),
                       dict(tiles=[10_000] * probe.TILE_COUNT)):
            record = probe.read_bound_result(self.bound_shell(**kwargs),
                                             base.DEFAULT_FPSUP)
            demand = {"demand_exceeds_declared": True,
                      "encoded_bytes": 3_392_834,
                      "source_sha256": record["source_sha256"]}
            self.assertEqual(bound_criterion.verdict(record, demand)["verdict"],
                             "refused", f"{kwargs} read as a pass")

    def test_the_host_may_only_write_the_reclaim_fields(self):
        shell = self.held_both()
        for field in (probe.L_MAGIC, probe.L_HANDLE, probe.L_TICK,
                      probe.L_ALLOC, probe.L_OWNMARK):
            with self.assertRaises(probe.ProbeError) as caught:
                probe._write_life_field(shell, self.HANDLE_A, "B", field, 1)
            self.assertIn("camera's to write", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
