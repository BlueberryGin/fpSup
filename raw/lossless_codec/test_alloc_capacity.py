#!/usr/bin/env python3
"""The capability probe must never keep what it takes.

A probe that asks for 64 MiB and keeps it has leaked 64 MiB for the rest of the
boot, and nothing else in this project can then allocate. So the free is not a
courtesy, it is the property these tests are here to hold up.
"""
import pathlib
import re
import sys
import unittest

sys.dont_write_bytecode = True
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import alloc_capacity_driver as driver        # noqa: E402
import exact_dng_writer_probe as base         # noqa: E402
import exact_flush_writer_probe as flush      # noqa: E402
import single_frame_codec_probe as codec      # noqa: E402

SOURCE = driver.SOURCE.read_text()


class ProbeSourceTests(unittest.TestCase):

    def test_it_frees_what_it_gets(self):
        """F_GET and F_FREE both appear, and the free is after the get."""
        self.assertIn("F_GET", SOURCE)
        self.assertIn("F_FREE", SOURCE)
        self.assertLess(SOURCE.index("LDA     r12, F_GET"),
                        SOURCE.index("LDA     r12, F_FREE"))

    def test_a_refusal_stops_the_asking_and_frees_only_what_was_granted(self):
        """Freeing a handle that was never obtained is worse than leaking."""
        after_get = SOURCE[SOURCE.index("LDA     r12, F_GET"):]
        before_free = after_get[:after_get.index("LDA     r12, F_FREE")]
        self.assertIn("cmp     r0, #0", before_free)
        self.assertIn("beq     give_back", before_free)
        # The free loop walks the count, not the request, so a partial grant
        # frees exactly the blocks that exist.
        loop = SOURCE[SOURCE.index("free_one:"):SOURCE.index("done:")]
        self.assertIn("cmp     r8, #0", loop)
        self.assertIn("beq     done", loop)
        self.assertIn("cmp     r1, #0", loop)

    def test_the_block_count_is_bounded_by_the_table_not_the_caller(self):
        """A count past the table's end would write over whatever follows."""
        self.assertIn("cmp     r9, #MAX_BLOCKS", SOURCE)
        self.assertIn("movhi   r9, #MAX_BLOCKS", SOURCE)

    def test_the_handle_table_is_cleared_before_use(self):
        self.assertIn("clear_table:", SOURCE)

    def test_the_worst_free_return_is_initialised_on_every_path(self):
        """done reads r10 even when the allocator itself failed."""
        prologue = SOURCE[SOURCE.index("probe_entry:"):SOURCE.index("LDA     r7, SCRATCH")]
        self.assertIn("mov     r10, #0", prologue)

    def test_it_retains_nothing_and_writes_no_lifecycle_record(self):
        for forbidden in ("LIFE", "OWN_OFF", "MARK", "HOOK_SITE", "HOOK_ARMED"):
            self.assertNotIn(forbidden, SOURCE, forbidden)

    def test_the_result_area_is_guarded_by_a_name_the_checks_know(self):
        """P, SCRATCH and SLOT are the three; anything else is unguarded."""
        self.assertIn("#ifndef SCRATCH", SOURCE)
        guard = re.search(r"#ifndef SCRATCH\s*\n\.equ SCRATCH, (0x[0-9A-Fa-f]+)",
                          SOURCE)
        self.assertIsNotNone(guard, "the default must be a plain .equ")
        default = int(guard.group(1), 16)
        self.assertGreater(default, 0xC072EFB4,
                           "the standalone default must sit ABOVE the arena")

    def test_the_fifth_argument_is_stacked_and_the_stack_is_put_back(self):
        """F_GET takes five arguments; the fifth does not fit in a register."""
        body = SOURCE[SOURCE.index("sub     sp, sp, #8"):]
        self.assertIn("str     r12, [sp]", body)
        self.assertLess(body.index("str     r12, [sp]"),
                        body.index("add     sp, sp, #8"))

    def test_it_records_both_inputs_it_was_asked_for(self):
        """A caller reading the wrong slot must be able to tell."""
        self.assertIn("str     r4, [r7, #H_SIZE]", SOURCE)
        self.assertIn("str     r9, [r7, #H_WANT]", SOURCE)

    def test_the_driver_and_the_probe_agree_on_the_result_layout(self):
        """Every H_ offset in the assembly, against the driver's indices."""
        equ = dict(re.findall(r"^\.equ (H_\w+),\s*(0x[0-9A-Fa-f]+)",
                              SOURCE, re.M))
        for name, index in (("H_COUNT", driver.H_COUNT),
                            ("H_ALLOC", driver.H_ALLOC),
                            ("H_FREE_RET", driver.H_FREE_RET),
                            ("H_SIZE", driver.H_SIZE),
                            ("H_WANT", driver.H_WANT),
                            ("H_TABLE", driver.H_TABLE)):
            self.assertIn(name, equ, name)
            self.assertEqual(int(equ[name], 16), index * 4, name)
        self.assertIn(".equ MAX_BLOCKS,      %d" % driver.MAX_BLOCKS, SOURCE)

    def test_the_image_fits_the_borrowed_claim(self):
        assemble, _ = base.load_assembler(HERE.parents[1])
        image = assemble(driver.SOURCE, defines=("SCRATCH=0xC072F700",))
        self.assertLessEqual(len(image), driver.CODE_CLAIM)
        self.assertGreater(len(image), 0)


class ClaimTests(unittest.TestCase):

    def test_both_claims_are_borrowed_at_the_codec_chains_own_size(self):
        """A smaller claim under the same name resizes it and costs a boot."""
        self.assertEqual(driver.CLAIMS["code"], codec.CLAIM_NAMES["code"])
        self.assertEqual(driver.CLAIMS["state"], codec.CLAIM_NAMES["state"])
        self.assertEqual(driver.CODE_CLAIM, codec.CODE_CLAIM_BYTES)

    def test_it_refuses_to_borrow_while_anything_is_armed(self):
        source = pathlib.Path(driver.__file__).read_text()
        self.assertIn("def require_nothing_armed", source)
        self.assertIn("flush.HOOK_SITE", source)
        self.assertIn("base.HOOK_SITE", source)
        # Scoped to main(): comparing a CALL against a DEFINITION found
        # elsewhere in the file proves nothing, and a mutation that swapped
        # the two lines slipped through exactly that way.
        body = source[source.index("def main():"):]
        self.assertLess(body.index("require_nothing_armed(shell)"),
                        body.index("install(shell, fpsup"),
                        "the sites must be checked BEFORE the claim is "
                        "overwritten")


class SizeTests(unittest.TestCase):

    def test_the_largest_size_asked_for_is_the_measured_batch(self):
        """64 MiB is vol[0x4828] = 0x04000000, measured live 2026-09-29."""
        self.assertEqual(max(driver.SIZES), 0x04000000)

    def test_a_known_good_size_anchors_the_run(self):
        """Without 4 MiB in the list a total refusal cannot be diagnosed."""
        self.assertIn(codec.ALLOC_SIZE, driver.SIZES)
        self.assertEqual(min(driver.SIZES), codec.ALLOC_SIZE)

    def test_the_sizes_ascend(self):
        self.assertEqual(list(driver.SIZES), sorted(driver.SIZES))


class VerdictTests(unittest.TestCase):

    @staticmethod
    def rows(granted_mib, freed=True, count=1):
        return [{"MiB": m, "granted": m in granted_mib,
                 "granted_count": count if m in granted_mib else 0,
                 "total_MiB": m * count if m in granted_mib else 0,
                 "freed": freed}
                for m in (4, 8, 16, 32, 64)]

    def test_a_partial_grant_is_not_read_as_nothing_granted(self):
        """One block of eight is still 16 MiB, and the first version of this
        verdict called that "NOTHING GRANTED"."""
        rows = [{"MiB": 16, "granted": False, "granted_count": 1,
                 "total_MiB": 16, "freed": True}]
        said = driver.verdict(rows)
        self.assertNotIn("NOTHING GRANTED", said)
        self.assertIn("16 MiB", said)

    def test_several_blocks_are_counted_as_their_total(self):
        rows = [{"MiB": 16, "granted": True, "granted_count": 4,
                 "total_MiB": 64, "freed": True}]
        self.assertIn("64 MiB IS AVAILABLE", driver.verdict(rows))

    def test_sixty_four_available_says_the_pool_fits_in_one_allocation(self):
        said = driver.verdict(self.rows({4, 8, 16, 32, 64}))
        self.assertIn("64 MiB IS AVAILABLE", said)
        self.assertIn("no copy-back and no synchronous encode", said)
        self.assertNotIn("short of", said)

    def test_short_of_sixty_four_names_both_remaining_routes(self):
        said = driver.verdict(self.rows({4, 8}))
        self.assertIn("8 MiB", said)
        self.assertIn("synchronous encode", said)
        self.assertIn("camera's own buffer", said)

    def test_nothing_granted_blames_the_probe_not_the_allocator(self):
        said = driver.verdict(self.rows(set()))
        self.assertIn("NOTHING GRANTED", said)
        self.assertIn("not the allocator", said)

    def test_an_unfreed_block_is_shouted_about_in_every_verdict(self):
        for granted in ({4}, {4, 8, 16, 32, 64}):
            said = driver.verdict(self.rows(granted, freed=False))
            self.assertIn("WARNING", said)
            self.assertIn("not freed", said)

    def test_every_shape_produces_a_verdict(self):
        import itertools
        for n in range(6):
            for combo in itertools.combinations((4, 8, 16, 32, 64), n):
                said = driver.verdict(self.rows(set(combo)))
                self.assertTrue(said and said.strip(), combo)


if __name__ == "__main__":
    unittest.main()
