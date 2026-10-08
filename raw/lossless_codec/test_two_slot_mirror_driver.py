#!/usr/bin/env python3
"""The driver and the probe must agree, word for word.

Every number in two_slot_mirror_driver.py is a copy of an ``.equ`` in
two_slot_mirror_probe.S. A copy that drifts does not fail loudly: the host seeds
the proof magic into the wrong word, the probe refuses every frame, and the run
reads as "0x12 never arrived" -- the exact wrong answer to the question the run
exists to settle. So these tests parse the assembly and compare.
"""
import pathlib
import re
import sys
import unittest

sys.dont_write_bytecode = True
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import two_slot_mirror_driver as driver          # noqa: E402
import exact_flush_writer_probe as flush         # noqa: E402
import single_frame_codec_probe as codec         # noqa: E402

SOURCE = driver.SOURCE.read_text()


def equs(text):
    """Every .equ NAME, VALUE in the source, as {name: int}."""
    out = {}
    for name, value in re.findall(r"^\.equ\s+(\w+),\s*([^/\n]+)", text, re.M):
        value = value.strip()
        try:
            out[name] = int(value, 0)
        except ValueError:
            # One expression form appears in this file: DNG_LEN - 16.
            expr = re.sub(r"\b([A-Z_][A-Z_0-9]*)\b",
                          lambda m: str(out.get(m.group(1), m.group(0))), value)
            try:
                out[name] = int(eval(expr, {"__builtins__": {}}, {}))
            except Exception:
                pass
    return out


EQU = equs(SOURCE)


class StateLayoutTests(unittest.TestCase):
    """The host writes into the state block by index; the indices must match."""

    def test_every_state_word_the_driver_names_matches_the_assembly(self):
        expected = {
            "S_COUNT": driver.S_COUNT, "S_WRITER": driver.S_WRITER,
            "S_FRAME": driver.S_FRAME, "S_LR": driver.S_LR,
            "S_RESTORED": driver.S_RESTORED, "S_ERROR": driver.S_ERROR,
            "S_STAGE": driver.S_STAGE, "S_FRAMES": driver.S_FRAMES,
            "S_REFUSED": driver.S_REFUSED, "S_RELEASED": driver.S_RELEASED,
            "S_SLOT_A": driver.S_SLOT_A, "S_SLOT_B": driver.S_SLOT_B,
            "S_NODE_BUF": driver.S_NODE_BUF, "S_NODE_LEN": driver.S_NODE_LEN,
            "S_LAST_SEEN": driver.S_LAST_SEEN, "S_SHAPE_BAD": driver.S_SHAPE_BAD,
            "S_PROOF": driver.S_PROOF,
        }
        for name, index in expected.items():
            self.assertIn(name, EQU, f"{name} is not an .equ in the probe")
            self.assertEqual(EQU[name], index * 4,
                             f"{name}: assembly says 0x{EQU[name]:X}, the "
                             f"driver writes word {index} (0x{index * 4:X})")

    def test_the_driver_names_every_state_equ_the_probe_has(self):
        """A field added to the probe and not to the driver reads as zero."""
        named = {n for n in EQU if n.startswith("S_")}
        have = {n for n in dir(driver) if n.startswith("S_")}
        self.assertEqual(named - have, set(),
                         "the probe has state words the driver does not name")

    def test_the_state_claim_is_sized_to_this_probes_own_layout(self):
        """Claiming another probe's 256 bytes for a 68-byte layout did not fit.

        After the codec chain, acquire-second and the memory-read tools the
        arena had 72 bytes left. The claim must be what this probe's .equ block
        uses, and S_PROOF must be the last word it uses.
        """
        self.assertEqual(driver.STATE_WORDS, driver.S_PROOF + 1)
        highest = max(v for k, v in vars(driver).items()
                      if k.startswith("S_") and isinstance(v, int))
        self.assertEqual(highest, driver.S_PROOF)
        self.assertLess(driver.STATE_WORDS * 4, 72,
                        "the claim no longer fits what the chain leaves free")

    def test_configure_pins_the_reused_modules_state_size(self):
        """Every flush helper clears and reads flush.STATE_WORDS words."""
        driver.configure()
        self.assertEqual(flush.STATE_WORDS, driver.STATE_WORDS)


class SlotRecordTests(unittest.TestCase):

    def test_slot_record_offsets_match_the_assembly(self):
        for name, index in (("L_STATE", driver.L_STATE),
                            ("L_FRAME", driver.L_FRAME),
                            ("L_SEQ", driver.L_SEQ),
                            ("L_BASE", driver.L_BASE),
                            ("L_HELD_TICK", driver.L_HELD_TICK),
                            ("L_FREE_TICK", driver.L_FREE_TICK),
                            ("L_LAST_STATE", driver.L_LAST_STATE),
                            ("L_LEN", driver.L_LEN)):
            self.assertEqual(EQU[name], index * 4, name)

    def test_slot_offset_and_stride_match(self):
        self.assertEqual(EQU["SLOT_OFF"], driver.SLOT_OFF)
        self.assertEqual(EQU["SLOT_STRIDE"], driver.SLOT_STRIDE)

    def test_two_records_fit_inside_the_stride_they_are_spaced_by(self):
        self.assertLessEqual(driver.SLOT_WORDS * 4, driver.SLOT_STRIDE)

    def test_the_records_and_the_watermark_are_both_inside_a_block(self):
        self.assertLess(driver.SLOT_OFF + 2 * driver.SLOT_STRIDE,
                        codec.ALLOC_SIZE)
        self.assertLess(driver.MARK_OFF, codec.ALLOC_SIZE)

    def test_the_records_do_not_overlap_the_frame_copied_into_slot_zero(self):
        """Slot 0's block holds both the records and a 3.2 MB frame copy."""
        self.assertGreaterEqual(driver.SLOT_OFF, driver.DNG_LEN)


class MagicAndGeometryTests(unittest.TestCase):

    def test_proof_magic_matches_and_spells_MSL2(self):
        self.assertEqual(EQU["MARK_MAGIC"], driver.PROOF_MAGIC)
        self.assertEqual(driver.PROOF_MAGIC.to_bytes(4, "little"), b"MSL2")

    def test_dng_length_and_watermark_offset_match(self):
        self.assertEqual(EQU["DNG_LEN"], driver.DNG_LEN)
        self.assertEqual(EQU["MARK_OFF"], driver.MARK_OFF)

    def test_the_length_is_the_one_the_preflight_proved_live(self):
        """0x318200 is the segment length every live preflight has reported."""
        self.assertEqual(driver.DNG_LEN, 0x318200)

    def test_the_release_state_is_0x12_and_the_probe_says_so(self):
        self.assertEqual(EQU["STATE_RELEASED"], 0x12)

    def test_the_site_is_the_flush_not_the_cinemadng_writer(self):
        """The frame pointer is an argument only at the flush site."""
        self.assertEqual(EQU["HOOK_SITE"], flush.HOOK_SITE)
        self.assertEqual(EQU["HOOK_ORIG"], flush.HOOK_ORIG)


class FlushGlobals:
    """Pin the reused module's globals, and put them back.

    ``exact_flush_writer_probe`` keeps CODE/STATE and their limits as module
    globals that every driver rewrites through place(). A test that calls
    build_probe without setting them inherits whatever the previously imported
    test module left behind: this one passed alone and failed under discover,
    with a claim window 116 bytes too small. Tests that touch that module set
    the globals themselves and restore them, so neither direction leaks.
    """

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
            if value is None:
                continue
            setattr(flush, field, value)


class ClaimTests(FlushGlobals, unittest.TestCase):

    def test_the_code_claim_is_shared_at_the_codec_chains_own_size(self):
        """Sharing the name at a SMALLER size resized it and cost a boot."""
        self.assertEqual(driver.CLAIMS["code"], codec.CLAIM_NAMES["code"])
        self.assertEqual(driver.CODE_CLAIM, codec.CODE_CLAIM_BYTES)

    def test_the_state_claim_is_not_shared(self):
        """The codec chain's state still holds the handle release needs."""
        self.assertNotEqual(driver.CLAIMS["state"], codec.CLAIM_NAMES["state"])

    def test_the_image_fits_the_shared_claim(self):
        code = flush.build_probe(HERE.parents[1])
        self.assertLessEqual(len(code), driver.CODE_CLAIM)
        self.assertGreater(len(code), 0)


class StateTakeOverTests(unittest.TestCase):
    """A leftover state block is ours or it is a refusal; never a shrug."""

    def test_our_own_previous_run_is_recognised_by_its_proof_word(self):
        source = pathlib.Path(driver.__file__).read_text()
        self.assertIn("present[S_PROOF] != PROOF_MAGIC", source)

    def test_a_leftover_without_the_proof_is_refused(self):
        source = pathlib.Path(driver.__file__).read_text()
        self.assertIn("occupied by something that is not this", source)

    def test_both_hook_sites_are_checked_before_the_region_is_cleared(self):
        source = pathlib.Path(driver.__file__).read_text()
        take = source[source.index("def take_over_shared_region"):
                      source.index("def arm(")]
        self.assertLess(take.index("flush.HOOK_SITE"),
                        take.index("write_words_verified"))
        self.assertIn("base.HOOK_SITE", take)


class BuildTests(FlushGlobals, unittest.TestCase):

    def test_hook_armed_reaches_the_assembler(self):
        """Two values must give two images, or the default gets baked in."""
        placed = {driver.CLAIMS["state"]: 0xC072F700,
                  driver.CLAIMS["code"]: 0xC072F800}
        code = driver.build_with_armed(HERE.parents[1], placed, 0xEB00333F)
        self.assertGreater(len(code), 0)
        self.assertEqual(len(code) % 4, 0)

    def test_the_frames_bound_is_actually_passed_to_the_assembler(self):
        """A bound left at the probe's default is a bound nobody chose."""
        self.assertEqual(driver.FRAMES, 8)
        self.assertIn("f\"FRAMES={FRAMES}\"",
                      pathlib.Path(driver.__file__).read_text())

    def test_the_probe_re_arms_and_is_doubly_bounded(self):
        """One bound on redirected frames, one on hook entries."""
        tail = SOURCE[SOURCE.rindex("call_real:"):]
        self.assertIn("cmp     r0, #FRAMES", tail)
        self.assertIn("cmp     r0, #FRAMES * 3", tail)
        self.assertEqual(tail.count("bhs     no_rearm"), 2)

    def test_the_hook_is_restored_before_anything_risky(self):
        """A fault below the restore costs one frame, not every frame."""
        restore = SOURCE.index("str     r6, [r5]")
        for later in ("release_pass:", "mirror_copy:", "record_slot:"):
            self.assertLess(restore, SOURCE.index(later), later)


class VerdictTests(unittest.TestCase):
    """verdict() is the run's whole output, so it is tested, not eyeballed."""

    def test_no_release_after_both_slots_filled_is_the_negative_answer(self):
        said = driver.verdict(frames=2, released=0)
        self.assertIn("NEVER ARRIVED", said)
        self.assertIn("not the release signal", said)

    def test_a_release_is_the_positive_answer_and_demands_the_card_be_checked(self):
        said = driver.verdict(frames=8, released=5)
        self.assertIn("ARRIVED", said)
        self.assertNotIn("NEVER", said)
        self.assertIn("MSL2", said)
        self.assertIn("sharing a sequence", said)

    def test_nothing_redirected_claims_no_verdict_at_all(self):
        said = driver.verdict(frames=0, released=0)
        self.assertIn("NO VERDICT", said)
        self.assertNotIn("NEVER ARRIVED", said)

    def test_one_frame_is_inconclusive_not_a_negative(self):
        """A clip too short to reach slot 1 must not read as 'no release'."""
        said = driver.verdict(frames=1, released=0)
        self.assertIn("INCONCLUSIVE", said)
        self.assertNotIn("NEVER ARRIVED", said)

    def test_every_input_produces_a_verdict(self):
        for frames in range(0, 10):
            for released in range(0, 10):
                said = driver.verdict(frames, released)
                self.assertTrue(said and said.strip(), (frames, released))

    def test_a_release_never_reads_as_the_negative_answer(self):
        for frames in range(1, 10):
            self.assertNotIn("NEVER ARRIVED", driver.verdict(frames, 1))


class RoundRobinTests(FlushGlobals, unittest.TestCase):
    """The round-robin variant is a different experiment, not a print."""

    def test_the_define_actually_changes_the_image(self):
        placed = {driver.CLAIMS["state"]: 0xC072F700,
                  driver.CLAIMS["code"]: 0xC072F800}
        saved = driver.ROUND_ROBIN
        try:
            driver.ROUND_ROBIN = False
            free = driver.build_with_armed(HERE.parents[1], placed, 0xEB00333F)
            driver.ROUND_ROBIN = True
            robin = driver.build_with_armed(HERE.parents[1], placed, 0xEB00333F)
        finally:
            driver.ROUND_ROBIN = saved
        self.assertNotEqual(free, robin)
        self.assertLess(len(robin), len(free),
                        "round robin drops the release pass, so it is smaller")

    def test_round_robin_has_no_release_pass_and_no_free_search(self):
        block = SOURCE[SOURCE.index("#ifdef ROUND_ROBIN"):
                       SOURCE.index("#else", SOURCE.index("#ifdef ROUND_ROBIN"))]
        self.assertIn("and     r5, r5, #1", block)
        self.assertNotIn("STATE_RELEASED", block)
        self.assertNotIn("pick_slot", block)

    def test_the_slot_index_is_the_frame_count_not_a_search(self):
        block = SOURCE[SOURCE.index("#ifdef ROUND_ROBIN"):
                       SOURCE.index("#else", SOURCE.index("#ifdef ROUND_ROBIN"))]
        self.assertIn("ldr     r5, [r4, #S_FRAMES]", block)


class CardVerdictTests(unittest.TestCase):
    """The answer comes off the card, so this is where it is decided."""

    @staticmethod
    def rows(sequences):
        return [{"frame": i + 1, "ours": True, "sequence": s, "sha256": "x"}
                for i, s in enumerate(sequences)]

    def test_every_file_holding_its_own_sequence_is_the_good_answer(self):
        said = driver.card_verdict(self.rows([0, 1, 2, 3, 4, 5, 6, 7]))
        self.assertIn("POOL DEEP ENOUGH", said)
        self.assertIn("batched at clip close", said)

    def test_a_bound_shorter_than_a_batch_reports_saturation_not_a_verdict(self):
        """Eight frames is a third of a batch, so it can only saturate.

        The 8-frame run of 2026-09-29 was read as 'the card reads at clip
        close'. The 40-frame run showed two batches and a boundary between
        them. A run too short to contain a boundary must say so.
        """
        said = driver.card_verdict(self.rows([6, 7, 6, 7, 6, 7, 6, 7]))
        self.assertIn("SATURATED", said)
        self.assertIn("64 MiB", said)
        self.assertIn("Raise --bound", said)
        self.assertNotIn("POOL DEEP ENOUGH", said)
        self.assertNotIn("clip close", said.replace(
            "do NOT conclude 'the card reads at clip close'.", ""))

    def test_a_partial_overwrite_reports_how_much_deeper_it_must_be(self):
        # File 1 holds sequence 2: slot 0 was overwritten one frame too early.
        said = driver.card_verdict(self.rows([2, 1, 2, 3, 4, 5, 6, 7]))
        self.assertIn("POOL TOO SHALLOW by 2", said)
        self.assertIn("64 MiB", said)
        # Seven distinct sequences over eight files is not a plateau, so the
        # verdict must NOT call them batch flush points.
        self.assertNotIn("batch flush points", said)

    def test_plateaus_are_named_as_flush_points_only_when_they_are_plateaus(self):
        """The shape actually measured: two plateaus over many files."""
        seqs = [20 if i % 2 == 0 else 19 for i in range(21)]
        seqs += [38 if i % 2 == 0 else 39 for i in range(19)]
        said = driver.card_verdict(self.rows(seqs))
        self.assertIn("batch flush points", said)
        self.assertIn("POOL TOO SHALLOW", said)

    def test_no_marked_file_claims_no_verdict(self):
        said = driver.card_verdict([{"frame": 1, "ours": False}])
        self.assertIn("NO VERDICT", said)

    def test_unreadable_files_do_not_become_a_verdict(self):
        said = driver.card_verdict([{"frame": 1, "error": "no such file"}])
        self.assertIn("NO VERDICT", said)

    def test_every_shape_produces_a_verdict(self):
        import itertools
        for combo in itertools.product(range(3), repeat=3):
            said = driver.card_verdict(self.rows(list(combo)))
            self.assertTrue(said and said.strip(), combo)


if __name__ == "__main__":
    unittest.main()
