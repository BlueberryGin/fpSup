#!/usr/bin/env python3
"""The three modes must be three different experiments, and say which they are.

A probe that reports mode 3 while running mode 2 produces a result that would
be believed and is wrong. The MODE define reaching the assembler is therefore
checked in the driver at build time and held up here, along with the state
layout, the bounds and the ordering that keeps a fault from costing every frame.
"""
import pathlib
import re
import sys
import unittest

sys.dont_write_bytecode = True
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import writeback_length_driver as driver      # noqa: E402
import exact_dng_writer_probe as base         # noqa: E402
import exact_flush_writer_probe as flush      # noqa: E402
import single_frame_codec_probe as codec      # noqa: E402

SOURCE = driver.SOURCE.read_text()


def equs(text):
    out = {}
    for name, value in re.findall(r"^\.equ\s+(\w+),\s*([^/\n]+)", text, re.M):
        value = value.strip()
        try:
            out[name] = int(value, 0)
        except ValueError:
            expr = re.sub(r"\b([A-Z_][A-Z_0-9]*)\b",
                          lambda m: str(out.get(m.group(1), m.group(0))), value)
            try:
                out[name] = int(eval(expr, {"__builtins__": {}}, {}))
            except Exception:
                pass
    return out


EQU = equs(SOURCE)


class FlushGlobals:
    """Pin the reused module's globals and put them back; see the mirror driver."""

    FIELDS = ("CODE", "CODE_LIMIT", "STATE", "STATE_LIMIT", "SOURCE",
              "CLAIM_NAMES", "HOOK_ARMED", "STATE_WORDS")

    def setUp(self):
        self._saved = {f: getattr(flush, f, None) for f in self.FIELDS}
        driver.configure()
        flush.CODE = 0xC072F800
        flush.CODE_LIMIT = flush.CODE + driver.CODE_CLAIM
        flush.STATE = 0xC072F700
        flush.STATE_LIMIT = flush.STATE + driver.STATE_WORDS * 4
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for field, value in self._saved.items():
            if value is not None:
                setattr(flush, field, value)


class LayoutTests(unittest.TestCase):

    def test_every_state_word_matches_the_assembly(self):
        for name in [n for n in EQU if n.startswith("S_")]:
            index = getattr(driver, name, None)
            self.assertIsNotNone(index, f"the driver does not name {name}")
            self.assertEqual(EQU[name], index * 4, name)

    def test_the_driver_names_every_state_word_the_probe_has(self):
        named = {n for n in EQU if n.startswith("S_")}
        have = {n for n in dir(driver) if n.startswith("S_")}
        self.assertEqual(named - have, set())

    def test_the_lengths_match(self):
        self.assertEqual(EQU["DNG_LEN"], driver.DNG_LEN)
        self.assertEqual(EQU["SHORT_LEN"], driver.SHORT_LEN)

    def test_the_short_length_is_a_real_compressed_size(self):
        """G4b wrote exactly this many bytes on 2026-09-23."""
        self.assertEqual(driver.SHORT_LEN, 1396736)
        self.assertLess(driver.SHORT_LEN, driver.DNG_LEN)
        self.assertEqual(driver.SHORT_LEN % 1024, 0)

    def test_the_proof_magic_matches_and_spells_MSL3(self):
        self.assertEqual(EQU["MARK_MAGIC"], driver.PROOF_MAGIC)
        self.assertEqual(driver.PROOF_MAGIC.to_bytes(4, "little"), b"MSL3")

    def test_the_site_is_the_flush(self):
        self.assertEqual(EQU["HOOK_SITE"], flush.HOOK_SITE)
        self.assertEqual(EQU["HOOK_ORIG"], flush.HOOK_ORIG)


class ModeTests(FlushGlobals, unittest.TestCase):

    def images(self):
        placed = {driver.CLAIMS["state"]: 0xC072F700,
                  driver.CLAIMS["code"]: 0xC072F800}
        return {m: driver.build_with_armed(HERE.parents[1], placed,
                                           0xEB00333F, m)
                for m in (1, 2, 3)}

    def test_the_three_modes_are_three_different_images(self):
        built = self.images()
        self.assertEqual(len({bytes(v) for v in built.values()}), 3)

    def test_mode_3_is_the_largest_because_it_does_both(self):
        built = self.images()
        self.assertGreater(len(built[3]), len(built[1]))
        self.assertGreater(len(built[3]), len(built[2]))

    def test_a_mode_that_does_not_reach_the_assembler_is_refused(self):
        """The check is in build_with_armed, not a comment."""
        source = pathlib.Path(driver.__file__).read_text()
        self.assertIn("MODE is not reaching the assembler", source)

    def test_only_the_shortening_modes_touch_the_length(self):
        body = SOURCE[SOURCE.index("#if MODE != 2"):]
        self.assertIn("str     r1, [r2, #0x08]", body)
        before = SOURCE[:SOURCE.index("#if MODE != 2")]
        self.assertNotIn("str     r1, [r2, #0x08]", before)

    def test_only_the_writing_modes_touch_the_camera_buffer(self):
        block = SOURCE[SOURCE.index("#if MODE != 1"):
                       SOURCE.index("#if MODE != 2")]
        self.assertIn("add     r6, r5, #UNCACHED", block)
        self.assertIn("MARK_MAGIC", block)


class MarkOffsetTests(unittest.TestCase):

    def test_mode_2_marks_the_trailing_padding_so_the_clip_stays_valid(self):
        self.assertEqual(driver.mark_offset(2), driver.DNG_LEN - 16)
        self.assertEqual(driver.expected_length(2), driver.DNG_LEN)

    def test_the_cutting_modes_mark_inside_the_shortened_file(self):
        for mode in (1, 3):
            self.assertEqual(driver.mark_offset(mode), driver.SHORT_LEN - 16)
            self.assertEqual(driver.expected_length(mode), driver.SHORT_LEN)

    def test_every_mark_lands_inside_the_length_the_node_declares(self):
        """A mark past the node's length would write outside the buffer."""
        for mode in (1, 2, 3):
            self.assertLessEqual(driver.mark_offset(mode) + 8, driver.DNG_LEN)


class SafetyTests(unittest.TestCase):

    def test_the_hook_is_restored_before_anything_risky(self):
        restore = SOURCE.index("str     r6, [r5]")
        for later in ("#if MODE != 1", "#if MODE != 2", "shape_bad:"):
            self.assertLess(restore, SOURCE.index(later), later)

    def test_the_stock_length_is_the_only_shape_accepted(self):
        """A frame that is not the exact FHD length is left completely alone.

        Scoped to the comparison itself: "bne shape_bad" appears several times
        in this file, so asserting it merely EXISTS passes even when the
        length branch has been replaced by a nop -- which a mutation proved.
        The mark offset is computed from DNG_LEN, so a frame of another length
        would be marked outside the buffer the camera gave us.
        """
        at = SOURCE.index("LDA     r0, DNG_LEN")
        window = SOURCE[at:at + 120]
        self.assertIn("cmp     r3, r0", window)
        branch = window[window.index("cmp     r3, r0"):]
        self.assertTrue(branch.split("\n")[1].strip().startswith("bne"),
                        "the length comparison must be followed by its branch, "
                        "not by a nop: " + branch.split("\n")[1])
        self.assertIn("shape_bad", branch.split("\n")[1])

    def test_the_run_is_doubly_bounded_and_re_arms_last(self):
        tail = SOURCE[SOURCE.rindex("call_real:"):]
        self.assertIn("cmp     r0, #FRAMES", tail)
        self.assertIn("cmp     r0, #FRAMES * 3", tail)
        self.assertEqual(tail.count("bhs     no_rearm"), 2)

    def test_nothing_is_allocated_and_no_codec_runs(self):
        for forbidden in ("F_ALLOC", "F_GET", "F_FREE", "F_INIT", "F_ENC",
                          "F_SIZE", "F_PWR", "F_CLK"):
            self.assertNotIn(forbidden, SOURCE, forbidden)

    def test_the_claims_are_borrowed_at_the_codec_chains_own_sizes(self):
        self.assertEqual(driver.CLAIMS["code"], codec.CLAIM_NAMES["code"])
        self.assertEqual(driver.CLAIMS["state"], codec.CLAIM_NAMES["state"])
        self.assertEqual(driver.CODE_CLAIM, codec.CODE_CLAIM_BYTES)

    def test_it_refuses_to_borrow_while_anything_is_armed(self):
        source = pathlib.Path(driver.__file__).read_text()
        take = source[source.index("def take_over_shared_region"):
                      source.index("def arm(")]
        self.assertIn("flush.HOOK_SITE", take)
        self.assertIn("base.HOOK_SITE", take)
        self.assertLess(take.index("HOOK_SITE"),
                        take.index("write_words_verified"))


class VerdictTests(unittest.TestCase):

    @staticmethod
    def state(done=8, mode=2, len_after=0, shape_bad=0, error=0):
        v = [0] * driver.STATE_WORDS
        v[driver.S_DONE] = done
        v[driver.S_MODE] = mode
        v[driver.S_LEN_AFTER] = len_after
        v[driver.S_SHAPE_BAD] = shape_bad
        v[driver.S_ERROR] = error
        return v

    def test_nothing_altered_claims_nothing_about_freezing(self):
        said = driver.state_verdict(self.state(done=0))
        self.assertIn("NOTHING ALTERED", said)
        self.assertNotIn("SURVIVED", said)

    def test_surviving_is_stated_when_frames_were_altered(self):
        said = driver.state_verdict(self.state(done=8, mode=2))
        self.assertIn("SURVIVED", said)
        self.assertIn("does not freeze", said)

    def test_a_length_that_did_not_stick_is_not_reported_as_success(self):
        said = driver.state_verdict(
            self.state(done=8, mode=3, len_after=driver.DNG_LEN))
        self.assertIn("did not stick", said)
        self.assertNotIn("reached the structure", said)

    def test_a_length_that_stuck_still_defers_to_the_card(self):
        said = driver.state_verdict(
            self.state(done=8, mode=3, len_after=driver.SHORT_LEN))
        self.assertIn("reached the structure", said)
        self.assertIn("file size on the card", said)

    @staticmethod
    def rows(sizes, ours=True):
        return [{"frame": i + 1, "bytes": b, "ours": ours, "sequence": i}
                for i, b in enumerate(sizes)]

    def test_all_files_at_the_short_length_is_the_good_answer(self):
        said = driver.card_verdict(
            self.rows([driver.SHORT_LEN] * 8), mode=3)
        self.assertIn("HONOURS A SHORTER NODE", said)
        self.assertIn("costs no pool", said)

    def test_files_at_full_length_in_a_cutting_mode_is_a_failure(self):
        said = driver.card_verdict(self.rows([driver.DNG_LEN] * 8), mode=3)
        self.assertIn("LENGTH DID NOT TAKE", said)
        self.assertNotIn("HONOURS", said)

    def test_mode_2_reports_only_what_mode_2_can_show(self):
        said = driver.card_verdict(self.rows([driver.DNG_LEN] * 8), mode=2)
        self.assertIn("reaches the card", said)
        self.assertNotIn("SHORTER NODE", said)

    def test_no_marked_file_claims_no_verdict(self):
        said = driver.card_verdict([{"frame": 1, "error": "not read"}], mode=2)
        self.assertIn("NO VERDICT", said)

    def test_every_mode_and_shape_produces_a_verdict(self):
        for mode in (1, 2, 3):
            for sizes in ([], [driver.DNG_LEN], [driver.SHORT_LEN],
                          [driver.SHORT_LEN, driver.DNG_LEN]):
                said = driver.card_verdict(self.rows(sizes), mode)
                self.assertTrue(said and said.strip(), (mode, sizes))


if __name__ == "__main__":
    unittest.main()
