#!/usr/bin/env python3
"""The two-block code, EXECUTED under unicorn rather than read.

The five PHASE 9 tests that existed before this file all matched source text.
They would pass on a record written to the wrong offset, a check that compares
the wrong register, a refusal that leaves a block allocated, or a size call
whose failure is ignored. Each of those is a test here instead.

The cases that matter are the refusals. A routine that allocates 4 MiB and then
decides it cannot use it must hand it back before returning, and the only way
to see that is to watch F_FREE.
"""
import pathlib
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import emulation_capability as capability
import single_frame_codec_probe as probe

HARNESS = pathlib.Path(__file__).resolve().parent / "emulate_two_block.py"
CAPABILITY = capability.capability(emulator=HARNESS)
emulate = None
if CAPABILITY.ok:
    try:
        import emulate_two_block as emulate
    except ImportError as exc:
        CAPABILITY = capability.Capability(False, f"import failed: {exc}")

ERR_INIT, ERR_ENCODE, ERR_SIZE_CALL = 17, 18, 19
ERR_ALIAS, ERR_LIFE, ERR_OWN = 9, 26, 27


@unittest.skipUnless(CAPABILITY.ok, CAPABILITY.reason)
class AcquireTests(unittest.TestCase):
    """acquire_second_block.S, executed."""

    def run_it(self, **kwargs):
        return emulate.AcquireEmulator(**kwargs).run()

    # ---- the clean run -------------------------------------------------

    def test_it_acquires_a_block_and_records_it_completely(self):
        emu = self.run_it()
        self.assertEqual(emu.status, 0)
        self.assertEqual(emu.frees, [], "it freed a block it was keeping")
        record = emu.life_record(emulate.BLOCK_A, "B")
        self.assertEqual(record[probe.L_MAGIC], emulate.LIFE_MAGIC)
        self.assertEqual(record[probe.L_BOOT], emulate.BOOT_VALUE,
                         'the record must carry the boot that wrote it, or a record left in DRAM by the PREVIOUS boot reads as live')
        self.assertEqual(record[probe.L_ROLE], 2)
        self.assertEqual(record[probe.L_ALLOC], emulate.ALLOCATOR_OBJECT)
        self.assertEqual(record[probe.L_HANDLE], emulate.BLOCK_B)
        self.assertEqual(record[probe.L_BYTES], probe.ALLOC_SIZE)
        self.assertEqual(record[probe.L_END], emulate.BLOCK_B + probe.ALLOC_SIZE)
        self.assertEqual(record[probe.L_PURPOSE], emulate.PURPOSE_SRC)
        self.assertEqual(record[probe.L_OWNMARK], emulate.OWN_MAGIC)
        self.assertEqual(record[probe.L_STATE], emulate.LIFE_HELD)
        self.assertEqual(record[probe.L_FREERET], 0)
        self.assertEqual(record[probe.L_FREETICK], 0)
        self.assertGreater(record[probe.L_TICK], 0, "no acquisition tick")
        self.assertEqual(record[probe.L_SEQ], 3, "the invocation count")

    def test_the_block_itself_carries_the_mark(self):
        """Not the record's copy of the magic: the two words inside block B."""
        emu = self.run_it()
        self.assertEqual(emu.mark(emulate.BLOCK_B),
                         [emulate.OWN_MAGIC, emulate.BLOCK_B])

    def test_it_records_that_block_a_was_still_held(self):
        emu = self.run_it(record_a_tick=0x900, clock_start=0x5000)
        self.assertGreaterEqual(emu.life(emulate.BLOCK_A, "A", probe.L_HELD),
                                0x5000)

    def test_it_leaves_the_stack_where_it_found_it(self):
        self.assertTrue(self.run_it().sp_returned)

    def test_it_does_not_touch_block_as_record_fields(self):
        emu = self.run_it()
        record = emu.life_record(emulate.BLOCK_A, "A")
        self.assertEqual(record[probe.L_HANDLE], emulate.BLOCK_A)
        self.assertEqual(record[probe.L_ROLE], 1)
        self.assertEqual(record[probe.L_STATE], emulate.LIFE_HELD)
        self.assertEqual(record[probe.L_PURPOSE], emulate.PURPOSE_WORK)

    # ---- refusals BEFORE anything is allocated -------------------------

    def assert_refused_without_allocating(self, status, **kwargs):
        emu = self.run_it(**kwargs)
        self.assertEqual(emu.status, status)
        self.assertNotIn(emulate.F_ALLOC, emu.calls,
                         "it called the allocator anyway")
        self.assertEqual(emu.life(emulate.BLOCK_A, "B", probe.L_MAGIC), 0,
                         "it wrote a record for a block it never got")
        return emu

    def test_no_scratch_proof_is_refused(self):
        self.assert_refused_without_allocating(1, scratch_proof=False)

    def test_a_missing_record_for_block_a_is_refused(self):
        self.assert_refused_without_allocating(2, record_a=False)

    def test_a_record_whose_magic_is_missing_is_refused(self):
        """Partial initialisation. The magic is written last precisely so that
        an interrupted write leaves a record that fails."""
        self.assert_refused_without_allocating(
            2, record_a_kwargs={"magic": 0})

    def test_a_record_naming_another_handle_is_refused(self):
        self.assert_refused_without_allocating(
            2, record_a_kwargs={"handle": 0x46000000})

    def test_a_record_that_is_not_held_is_refused(self):
        for state in (emulate.LIFE_UNCLEAR, emulate.LIFE_FREED, 0):
            self.assert_refused_without_allocating(
                2, record_a_kwargs={"state": state})

    def test_a_record_with_the_wrong_role_is_refused(self):
        self.assert_refused_without_allocating(2, record_a_kwargs={"role": 2})

    def test_a_block_without_the_mark_is_refused(self):
        self.assert_refused_without_allocating(3, mark_a=False)

    def test_a_mark_naming_another_block_is_refused(self):
        self.assert_refused_without_allocating(3, mark_a_names=0x46000000)

    def test_a_clock_that_went_backwards_is_refused(self):
        """The same-boot witness: a reading below the one recorded at
        acquisition means the counter restarted."""
        self.assert_refused_without_allocating(
            4, record_a_tick=0x900000, clock_start=0x1000)

    def test_a_second_block_already_held_is_refused(self):
        """Re-entry. Overwriting the record would lose the only handle by which
        the existing block could be freed - so the old record must survive."""
        emu = self.run_it(block_b_record={"handle": 0x57000000, "role": 2})
        self.assertEqual(emu.status, 5)
        self.assertNotIn(emulate.F_ALLOC, emu.calls)
        self.assertEqual(emu.frees, [])
        self.assertEqual(emu.life(emulate.BLOCK_A, "B", probe.L_HANDLE),
                         0x57000000, "it overwrote the existing record")

    def test_a_second_block_already_freed_is_replaced(self):
        emu = self.run_it(block_b_record={"handle": 0x57000000, "role": 2,
                                          "state": emulate.LIFE_FREED})
        self.assertEqual(emu.status, 0)
        self.assertEqual(emu.life(emulate.BLOCK_A, "B", probe.L_HANDLE),
                         emulate.BLOCK_B)

    def test_an_allocator_that_gives_nothing_is_reported(self):
        emu = self.run_it(allocator=0)
        self.assertEqual(emu.status, 6)
        self.assertEqual(emu.frees, [])

    def test_a_block_that_is_not_returned_is_reported(self):
        emu = self.run_it(handle=0)
        self.assertEqual(emu.status, 7)
        self.assertEqual(emu.frees, [], "there was nothing to free")
        self.assertEqual(emu.life(emulate.BLOCK_A, "B", probe.L_MAGIC), 0)

    # ---- refusals AFTER it has the block: it must hand it back ----------

    def assert_refused_and_freed(self, status, **kwargs):
        emu = self.run_it(**kwargs)
        self.assertEqual(emu.status, status)
        self.assertEqual(emu.frees,
                         [(emulate.ALLOCATOR_OBJECT, kwargs.get("handle"))],
                         "a block it refused was left allocated")
        self.assertEqual(emu.life(emulate.BLOCK_A, "B", probe.L_MAGIC), 0,
                         "it recorded a block it had just given back")
        return emu

    def test_a_handle_in_the_wrong_quarter_is_freed_again(self):
        self.assert_refused_and_freed(8, handle=0x30000000)

    def test_a_misaligned_handle_is_freed_again(self):
        self.assert_refused_and_freed(8, handle=emulate.BLOCK_B + 0x200)

    def test_a_handle_overlapping_block_a_is_freed_again(self):
        """The check the host used to make alone, now made on the camera."""
        self.assert_refused_and_freed(
            9, handle=emulate.BLOCK_A + probe.ALLOC_SIZE // 2)

    def test_memory_that_will_not_hold_the_mark_is_freed_again(self):
        """The store lands and the readback comes back zero. A block we cannot
        really write would otherwise turn up later as a bad encode."""
        self.assert_refused_and_freed(10, handle=emulate.BLOCK_B,
                                      mark_sticks=False)


@unittest.skipUnless(CAPABILITY.ok, CAPABILITY.reason)
class BoundProbeTests(unittest.TestCase):
    """The PHASE=9 two-block image, executed at the writer hook."""

    def run_it(self, **kwargs):
        return emulate.BoundProbeEmulator(**kwargs).run()

    # ---- the clean run -------------------------------------------------

    def test_a_clean_run_reports_every_return_and_intact_guards(self):
        emu = self.run_it()
        self.assertEqual(emu.state(emulate.S_ERROR), 0)
        self.assertEqual(emu.state(emulate.S_INIT_RET), 1)
        self.assertEqual(emu.state(emulate.S_ENC_RET), 1)
        self.assertEqual(emu.state(emulate.S_SIZE_RET), 1)
        self.assertEqual(emu.state(emulate.S_COMP_SIZE), sum(emu.tile_sizes))
        self.assertEqual(emu.guards(), [emulate.GUARD_MAGIC] * 5)

    def test_it_encodes_from_block_b_into_block_a(self):
        emu = self.run_it()
        self.assertEqual(emu.init_struct[5], emulate.BLOCK_B + emulate.UNCACHED,
                         "source is not block B through the uncached alias")
        self.assertEqual(emu.init_struct[6], emulate.BLOCK_A + emulate.UNCACHED,
                         "destination is not block A through the uncached alias")

    def test_the_source_really_is_generated_in_the_second_block(self):
        emu = self.run_it()
        self.assertNotEqual(emu.word(emulate.BLOCK_B), 0,
                            "nothing was written into block B")
        self.assertNotEqual(emu.word(emulate.BLOCK_B + 0x300000), 0,
                            "the fill stopped short")

    def test_it_records_the_declared_length_and_where_it_read_it(self):
        """The constant we pass and an actual reading are different things, and
        have been conflated in a result field before."""
        emu = self.run_it(declared=0x123000)
        declared = emu.declared()
        self.assertEqual(declared["constant"], probe.PROBE_DECLARED)
        self.assertEqual(declared["readback_address"], emulate.DECL_VAR)
        self.assertEqual(declared["readback_value"], 0x123000,
                         "the readback field is not a read")

    def test_the_frame_is_never_written_and_seg_is_handed_on_unchanged(self):
        emu = self.run_it()
        self.assertEqual(emu.frame_before, emu.frame_after)
        self.assertEqual(emu.seg_after, [emulate.FRAME, emulate.DNG_LEN])
        self.assertEqual(emu.handed_on[1], emu.seg,
                         "the writer was handed a different segment")

    def test_an_engine_that_overruns_is_visible_in_the_guards(self):
        """The question the guards exist to answer. If this cannot fail, the
        probe cannot report a bound either way."""
        emu = self.run_it(overwrite=16)
        self.assertNotEqual(emu.guards()[0], emulate.GUARD_MAGIC)
        far = self.run_it(overwrite=0x410)
        self.assertNotEqual(far.guards()[4], emulate.GUARD_MAGIC,
                            "a large overshoot must look different")

    # ---- the record checks, executed -----------------------------------

    def test_a_missing_record_for_block_b_stops_before_the_codec(self):
        emu = self.run_it(record_b=False)
        self.assertEqual(emu.state(emulate.S_ERROR), ERR_LIFE)
        self.assertNotIn(emulate.F_INIT, emu.calls,
                         "it initialised the codec anyway")

    def test_a_record_for_block_b_that_is_not_held_fails_as_a_record(self):
        emu = self.run_it(record_b_kwargs={"state": emulate.LIFE_UNCLEAR})
        self.assertEqual(emu.state(emulate.S_ERROR), ERR_LIFE)
        self.assertNotIn(emulate.F_INIT, emu.calls)

    def test_a_block_b_without_the_mark_fails_as_a_mark(self):
        emu = self.run_it(mark_b=False)
        self.assertEqual(emu.state(emulate.S_ERROR), ERR_OWN)
        self.assertNotIn(emulate.F_INIT, emu.calls)

    def test_a_mark_naming_another_block_fails(self):
        emu = self.run_it(mark_b_names=emulate.BLOCK_A)
        self.assertEqual(emu.state(emulate.S_ERROR), ERR_OWN)

    def test_block_a_is_checked_before_it_is_adopted(self):
        """S_ADOPTED is what the release path reads."""
        for kwargs, error in ((dict(record_a_kwargs={"magic": 0}), ERR_LIFE),
                              (dict(record_a_kwargs={"state": 0}), ERR_LIFE),
                              (dict(mark_a=False), ERR_OWN)):
            emu = self.run_it(**kwargs)
            self.assertEqual(emu.state(emulate.S_ERROR), error)
            self.assertEqual(emu.state(emulate.S_ADOPTED), 0,
                             "it adopted a block whose record is wrong")
            self.assertNotIn(emulate.F_INIT, emu.calls)

    # ---- the failure classification ------------------------------------

    def test_a_refused_init_reports_init_and_reads_no_guards(self):
        emu = self.run_it(init_ret=0)
        self.assertEqual(emu.state(emulate.S_ERROR), ERR_INIT)
        self.assertNotIn(emulate.F_ENC, emu.calls)
        self.assertEqual(emu.guards(), [0] * 5,
                         "it filled the result words with readings it did not "
                         "take")

    def test_a_refused_encode_reports_encode_and_reads_all_five_guards(self):
        """Two of the five used to be read, leaving the other three holding
        whatever the last run put there - indistinguishable from survival."""
        emu = self.run_it(enc_ret=0)
        self.assertEqual(emu.state(emulate.S_ERROR), ERR_ENCODE)
        self.assertEqual(emu.guards(), [emulate.GUARD_MAGIC] * 5)
        self.assertNotIn(emulate.F_SIZE, emu.calls,
                         "it asked for sizes after a failed encode")

    def test_a_refused_size_call_is_a_failure_not_a_sum(self):
        """The return was stored and never tested, so an untouched size table
        was summed and reported as a compressed size."""
        emu = self.run_it(size_ret=0)
        self.assertEqual(emu.state(emulate.S_ERROR), ERR_SIZE_CALL)
        self.assertEqual(emu.state(emulate.S_COMP_SIZE), 0,
                         "it summed a table the call never filled")
        self.assertEqual(emu.guards(), [emulate.GUARD_MAGIC] * 5,
                         "the guards are still worth reading")

    def test_a_failed_run_still_hands_the_frame_on(self):
        for kwargs in (dict(init_ret=0), dict(enc_ret=0), dict(size_ret=0),
                       dict(record_b=False)):
            emu = self.run_it(**kwargs)
            self.assertIsNotNone(emu.handed_on,
                                 "the writer's own routine was never reached")
            self.assertEqual(emu.frame_before, emu.frame_after)

    # ---- the host and the camera must generate the SAME bytes ----------

    def assert_matches_host(self, variant, generator_name, length=0x3000):
        """The criterion hashes a pattern the HOST generates and compares it to
        the run. If the two generators disagree by a byte, every demand figure
        and every hash in the record describes data the camera never saw. So the
        bytes are compared directly, not the constants that produce them."""
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]
                               / "projects" / "lossless-sup" / "tools"))
        import bound_criterion
        emu = self.run_it(variant=variant)
        self.assertEqual(emu.state(emulate.S_ERROR), 0)
        on_camera = bytes(emu.uc.mem_read(emulate.BLOCK_B, length))
        on_host = getattr(bound_criterion, generator_name)()[:length]
        self.assertEqual(on_camera, on_host,
                         f"{variant}: the camera and the host generate "
                         "different bytes")

    def test_the_incompressible_source_matches_the_hosts_generator(self):
        self.assert_matches_host("bound-probe-two", "generate_source")

    def test_the_compressible_source_matches_the_hosts_generator(self):
        self.assert_matches_host("bound-probe-two-ramp", "generate_flat")

    def test_the_compressible_source_is_what_it_claims_to_be(self):
        """Alternating 1024 and 3072 in sample space, which is the whole point:
        a first version was a ramp in BYTE space and measured 12.15 bits a
        sample, over the declared length, separating nothing."""
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]
                               / "projects" / "lossless-sup" / "tools"))
        import bound_criterion
        emu = self.run_it(variant="bound-probe-two-ramp")
        data = bytes(emu.uc.mem_read(emulate.BLOCK_B, 0x1200))
        image = bound_criterion.unpack12(data, width=16, height=16)
        self.assertEqual(sorted(set(image.ravel().tolist())), [1024, 3072])
        self.assertTrue((image[:, 0::2] == 1024).all())
        self.assertTrue((image[:, 1::2] == 3072).all())

    # ---- the source-location variant ------------------------------------

    def test_the_copy_variant_takes_the_frames_own_pixels_into_block_b(self):
        """Content held constant, location changed. That is the whole design of
        this variant, so what lands in block B has to BE the frame's pixels."""
        emu = self.run_it(variant="bound-probe-two-copy")
        self.assertEqual(emu.state(emulate.S_ERROR), 0)
        copied = bytes(emu.uc.mem_read(emulate.BLOCK_B, 0x1000))
        self.assertEqual(copied, emu.frame_pixels,
                         "block B does not hold the frame's pixels")
        # and it is still block B that the engine is pointed at
        self.assertEqual(emu.init_struct[5],
                         emulate.BLOCK_B + emulate.UNCACHED)

    def test_the_copy_variant_only_reads_the_frame(self):
        emu = self.run_it(variant="bound-probe-two-copy")
        self.assertEqual(emu.frame_before, emu.frame_after)
        self.assertEqual(emu.seg_after, [emulate.FRAME, emulate.DNG_LEN])

    def test_the_copy_variant_generates_nothing(self):
        """If the LCG were still running it would overwrite the copy, and the
        test above would pass anyway for the first 4 KiB only if the fill ran
        first. Check a word the ramp and the LCG cannot agree on."""
        plain = self.run_it()
        copy = self.run_it(variant="bound-probe-two-copy")
        at = emulate.BLOCK_B + 0x200000
        self.assertNotEqual(plain.word(at), copy.word(at),
                            "the two variants produced the same source")

    def test_nothing_is_allocated_or_freed_by_this_phase(self):
        emu = self.run_it()
        for call in (emulate.F_ALLOC, emulate.F_GET, emulate.F_FREE):
            self.assertNotIn(call, emu.calls)


if __name__ == "__main__":
    unittest.main()
