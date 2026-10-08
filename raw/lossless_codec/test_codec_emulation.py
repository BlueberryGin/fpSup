#!/usr/bin/env python3
"""PHASE=7 copy-back, executed under unicorn against the real firmware image.

Every other test of this code reads the SOURCE TEXT. These run it, and then
parse the buffer the camera would have written with the same checker that will
be pointed at the card. Two defects turned up here that no source-text
assertion could have found:

  * every successful copy-back reported ERR_SOURCE, because finish_source
    compared the first pixel word against its BEFORE value and copy-back
    deliberately replaces it with the new IFD.
  * S_STAGE could not tell "copied" from "skipped", because `complete`
    overwrites it with 8 on the way out.

What these tests cannot say: anything about the real codec, about timing, or
about whether the writer's buffer really is aliased at +0x40000000 on the
camera. Emulated is not on the camera.
"""
import pathlib
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
TOOLS = (pathlib.Path(__file__).resolve().parents[3]
         / "projects" / "lossless-sup" / "tools")
sys.path.insert(0, str(TOOLS))

import emulation_capability as capability
import exact_dng_writer_probe as base
import single_frame_codec_probe as probe

# The import is guarded TWICE, for two different failures. ImportError is the
# easy one (no unicorn). The hard one is a fault inside unicorn's JIT, which is
# a signal: it kills this process with no traceback and unittest never prints a
# thing - a bare exit 132 was exactly that. emulation_capability asks the
# question in a child process first, so that machine reports a SKIP with the
# signal named instead of a status nobody can interpret.
CAPABILITY = capability.capability()
emulation = None
if CAPABILITY.ok:
    try:
        import emulate_codec_probe as emulation
    except ImportError as exc:                          # unicorn absent
        CAPABILITY = capability.Capability(False, f"import failed: {exc}")
SKIP_REASON = CAPABILITY.reason

import verify_compressed_dng as verify

BUFFER = 0x53B00000
ARENA = 0x45000000
S_STAGE, S_ERROR, S_LEN_AFTER = 0x28, 0x2C, 0x90
S_COMP_SIZE, S_ADOPTED = 0x64, 0xF8


@unittest.skipUnless(CAPABILITY.ok, SKIP_REASON)
class CopyBackEmulationTests(unittest.TestCase):

    def run_it(self, tile_sizes=None):
        return emulation.CodecEmulator("copy-back", tile_sizes=tile_sizes).run()

    # ---- the frame that fits ------------------------------------------

    def test_a_fitting_frame_is_copied_and_only_the_length_changes(self):
        emu = self.run_it()
        self.assertEqual(emu.state(S_ERROR), 0, "no error expected")
        self.assertEqual(emu.result()['fit'], 1, "should have copied")
        self.assertEqual(emu.state(S_ADOPTED), 1, "must adopt the retained block")
        payload = sum(emu.tile_sizes)
        self.assertEqual(emu.state(S_COMP_SIZE), payload)
        self.assertEqual(emu.result()['file_bytes'], probe.OUT_OFF + payload)
        self.assertEqual(emu.result()['copy_bytes'], probe.OUT_OFF + payload)
        self.assertEqual(emu.state(S_LEN_AFTER), probe.OUT_OFF + payload)
        # seg[0] untouched, seg[1] set: nothing of ours is handed to the writer.
        self.assertEqual(emu.registered["buffer"], BUFFER,
                         "the writer must still be pointed at its OWN buffer")
        self.assertNotEqual(emu.registered["buffer"], ARENA)
        self.assertEqual(emu.registered["length"], probe.OUT_OFF + payload)
        self.assertEqual(emu.registered["kind"], 2)

    def test_what_lands_in_the_buffer_is_a_valid_compressed_dng(self):
        """The strongest statement available offline: the bytes the camera
        would write, parsed by the same checker the card readback uses."""
        emu = self.run_it()
        written = emu.buffer_bytes(0, emu.result()['file_bytes'])
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            frame = pathlib.Path(tmp) / "emulated.DNG"
            frame.write_bytes(written)
            stock = pathlib.Path(tmp) / "stock.DNG"
            stock.write_bytes(emu.stock_source)
            result = verify.check(frame, stock)
        self.assertTrue(result["ok"])
        self.assertEqual(result["payload_bytes"], sum(emu.tile_sizes))
        self.assertEqual(result["file_bytes"], emu.result()['file_bytes'])
        self.assertEqual(result["root_ifd_at"], probe.IFD_AT)
        self.assertTrue(result["original_header_preserved"])
        self.assertEqual(len(result["tile_offsets"]), 12)

    def test_the_post_encode_cost_is_timed(self):
        """C2's first run measured only the encode, so the copy cost - one of
        its two objectives - was missed entirely. One timestamp after the copy
        against S_T1, the end of the encode, covers the header copy, the
        template patch, the payload copy and the stores."""
        emu = self.run_it()
        t_after = emu.result()["t_after"]
        self.assertGreater(t_after, 0, "t_after must be recorded")
        # the stubbed clock advances a fixed amount per call, so what is
        # assertable here is the ORDER: t_after is taken after S_T1.
        self.assertGreater(t_after, emu.state(0x54), "later than S_T1")
        self.assertGreater(emu.state(0x54), emu.state(0x50), "S_T1 after S_T0")
        self.assertEqual(emu.result()["fit"], 1)

    def test_the_source_beyond_the_file_is_left_alone(self):
        """The copy writes file_bytes, not the whole buffer."""
        emu = self.run_it()
        end = emu.result()['copy_bytes']
        self.assertLess(end, emulation.DNG_LEN)
        self.assertEqual(emu.buffer_bytes(end, emulation.DNG_LEN - end),
                         emu.stock_source[end:],
                         "bytes past the copy must be the original frame")

    # ---- the frame that does not fit ----------------------------------

    def test_a_frame_that_does_not_fit_leaves_everything_alone(self):
        """The RAW fallback. Worst-case lossless output exceeds the buffer, so
        this path is required, and it must touch NOTHING."""
        too_big = [300000] * 12          # 3,600,000 + 79,872 > 3,244,544
        emu = self.run_it(tile_sizes=too_big)
        self.assertGreater(probe.OUT_OFF + sum(too_big), emulation.DNG_LEN)
        self.assertEqual(emu.result()['fit'], 0, "must refuse to copy")
        self.assertEqual(emu.state(S_ERROR), 0, "refusing is not an error")
        self.assertEqual(emu.result()['copy_bytes'], 0)
        self.assertEqual(emu.state(S_LEN_AFTER), emulation.DNG_LEN,
                         "the length must be recorded as unchanged")
        self.assertEqual(emu.registered["length"], emulation.DNG_LEN,
                         "the writer must still be given the STOCK length")
        self.assertEqual(emu.registered["buffer"], BUFFER)
        self.assertEqual(emu.buffer_bytes(), emu.stock_source,
                         "the stock frame must be byte-identical afterwards")

    def test_the_boundary_is_the_handed_length(self):
        """One tile bigger than fits is refused; one that exactly fits is not."""
        room = emulation.DNG_LEN - probe.OUT_OFF
        exact = [room // 12] * 12
        exact[-1] += room - sum(exact)
        self.assertEqual(probe.OUT_OFF + sum(exact), emulation.DNG_LEN)
        self.assertEqual(self.run_it(tile_sizes=exact).result()['fit'], 1,
                         "a file exactly seg[1] long must be accepted")
        over = list(exact)
        over[-1] += 16
        self.assertEqual(self.run_it(tile_sizes=over).result()['fit'], 0,
                         "one copy unit over must be refused")

    # ---- invariants that hold either way ------------------------------

    def test_it_allocates_nothing_and_frees_nothing(self):
        for sizes in (None, [300000] * 12):
            emu = self.run_it(tile_sizes=sizes)
            for name, address in (("F_ALLOC", emulation.F_ALLOC),
                                  ("F_GET", emulation.F_GET),
                                  ("F_FREE", emulation.F_FREE)):
                self.assertNotIn(address, emu.calls, f"{name} was called")
            self.assertIn(emulation.F_ENC, emu.calls)
            self.assertIn(emulation.REAL_REGISTER, emu.calls,
                          "the original routine must still be reached")

    def test_the_hook_word_is_restored_and_the_stack_comes_back(self):
        emu = self.run_it()
        self.assertEqual(emu.word(base.HOOK_SITE), base.HOOK_ORIG,
                         "the probe must self-restore its hook")
        self.assertEqual(emu.state(0x20), base.HOOK_ORIG)        # S_RESTORED
        self.assertEqual(emu.sp_after,
                         emulation.STACK + emulation.STACK_SPAN - 0x100,
                         "the stack must come back where it started")

    def test_a_wrong_kind_is_refused_without_touching_the_frame(self):
        emu = emulation.CodecEmulator("copy-back")
        emu.run(kind=3)
        self.assertEqual(emu.state(S_ERROR), 1, "ERR_KIND")
        self.assertEqual(emu.result()['fit'], 0)
        self.assertEqual(emu.buffer_bytes(), emu.stock_source)

    # ---- the compressed kind still redirects, for contrast ------------

    def test_compress_redirects_where_copy_back_does_not(self):
        """Same encode, opposite handling of seg[0]. If this ever matched
        copy-back, the distinction the whole design rests on would be gone."""
        redirect = emulation.CodecEmulator("compress").run()
        self.assertEqual(redirect.registered["buffer"], ARENA,
                         "compress hands the writer OUR arena")
        copyback = self.run_it()
        self.assertEqual(copyback.registered["buffer"], BUFFER,
                         "copy-back leaves the camera's buffer in place")



@unittest.skipUnless(CAPABILITY.ok, SKIP_REASON)
class FreezeDiagnosticTests(unittest.TestCase):
    """The pair that isolates what copy-back does that mirror and compress do not.

    copy-back froze the camera on 2026-09-28 with no host USB traffic during the
    clip - three failures against one success. It does two things the working
    paths do not: it copies about 1.5 MB into the CAMERA'S OWN frame buffer from
    inside the hook, and it shortens seg[1] on a buffer that is still the
    camera's. Each variant drops exactly one of them, so a freeze names the
    cause. These tests hold each variant to dropping only its own half.
    """

    def run_it(self, kind):
        return emulation.CodecEmulator(kind).run()

    def test_the_length_only_variant_changes_the_length_and_copies_nothing(self):
        emu = self.run_it("copy-back-length-only")
        self.assertEqual(emu.state(S_ERROR), 0)
        self.assertEqual(emu.result()["fit"], 1)
        self.assertEqual(emu.buffer_bytes(), emu.stock_source,
                         "it wrote into the camera's frame buffer")
        self.assertEqual(emu.word(emu.seg + 4), emu.result()["file_bytes"],
                         "it did not change the length")

    def test_the_copy_only_variant_copies_and_leaves_the_length_alone(self):
        emu = self.run_it("copy-back-copy-only")
        self.assertEqual(emu.state(S_ERROR), 0)
        self.assertEqual(emu.result()["fit"], 1)
        self.assertNotEqual(emu.buffer_bytes(), emu.stock_source,
                            "it did not copy")
        self.assertEqual(emu.word(emu.seg + 4), emulation.DNG_LEN,
                         "it changed the length")
        # and it records the length it did NOT change, so the record cannot be
        # read as if it had
        self.assertEqual(emu.state(S_LEN_AFTER), emulation.DNG_LEN)

    def test_between_them_they_cover_exactly_what_copy_back_does(self):
        full = self.run_it("copy-back")
        self.assertNotEqual(full.buffer_bytes(), full.stock_source)
        self.assertEqual(full.word(full.seg + 4), full.result()["file_bytes"])
        # the same file, built the same way, in all three
        sizes = {kind: self.run_it(kind).result()["file_bytes"]
                 for kind in ("copy-back", "copy-back-length-only",
                              "copy-back-copy-only")}
        self.assertEqual(len(set(sizes.values())), 1,
                         f"the variants built different files: {sizes}")


if __name__ == "__main__":
    unittest.main()
