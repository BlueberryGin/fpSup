#!/usr/bin/env python3
"""Offline tests for probe placement: computed arming and cache publication."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import probe_placement as placement



def arena_bounds():
    """The arena, from the file that declares it -- never named here."""
    import probe_placement
    cave = probe_placement.load_cave()
    return cave.CAVE_ARENA, cave.CAVE_ARENA_END

class ArmedWordTests(unittest.TestCase):
    def test_reproduces_the_constant_the_probes_carried(self):
        """0xEB00333F encoded a fixed site AND a fixed code address."""
        self.assertEqual(placement.armed_word(0xC0722AFC, 0xC072F800), 0xEB00333F)

    def test_round_trips_for_both_probe_sites(self):
        for site, target in ((0xC0722AFC, 0xC072F800),   # codec probe
                             (0xC03A5490, 0xC0730000),   # final-flush probe
                             (0xC0722AFC, arena_bounds()[0] + 0x9C),   # a cave claim, backwards
                             (0xC0722AFC, arena_bounds()[1] - 4)):
            word = placement.armed_word(site, target)
            self.assertEqual(placement.decode_bl(site, word), target,
                             f"0x{site:08X} -> 0x{target:08X}")

    def test_cave_arena_is_reachable_from_both_hook_sites(self):
        """Both probes must be able to branch into a claimed cave block."""
        for site in (0xC0722AFC, 0xC03A5490):
            for target in (arena_bounds()[0], arena_bounds()[1] - 8):   # arena ends
                word = placement.armed_word(site, target)
                self.assertEqual(placement.decode_bl(site, word), target)

    def test_backward_branch_encodes_negative(self):
        """The codec hook is above some firmware, so negatives must work."""
        word = placement.armed_word(0xC0722AFC, 0xC0700000)
        self.assertEqual(placement.decode_bl(0xC0722AFC, word), 0xC0700000)
        self.assertNotEqual(word & 0x800000, 0)

    def test_misalignment_and_range_are_refused(self):
        with self.assertRaises(placement.PlacementError):
            placement.armed_word(0xC0722AFE, 0xC072F800)
        with self.assertRaises(placement.PlacementError):
            placement.armed_word(0xC0722AFC, 0xC072F802)
        with self.assertRaises(placement.PlacementError):
            placement.armed_word(0xC0722AFC, 0x40000000)

    def test_decode_rejects_non_bl(self):
        with self.assertRaises(placement.PlacementError):
            placement.decode_bl(0xC0722AFC, 0xEBFDE061 ^ 0xFF000000)


class EncodingAgreementTests(unittest.TestCase):
    def test_one_encoder_not_two(self):
        """placement must not grow a second copy of the BL encoding."""
        import exact_dng_writer_probe as base
        for site in (0xC0722AFC, 0xC03A5490, 0xC0700000):
            targets = (arena_bounds()[0], 0xC072F800, 0xC0730000, 0xC0710000)
            for target in targets:
                self.assertEqual(placement.armed_word(site, target),
                                 base.arm_bl(site, target))

    def test_base_default_still_matches_the_historical_constant(self):
        import exact_dng_writer_probe as base
        self.assertEqual(base.HOOK_ARMED, 0xEB00333F)


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.handed = {}
        self.bump = arena_bounds()[0]

    def fake_claim(self, name, size):
        if name in self.handed:
            return self.handed[name]
        size = (size + 7) & ~7
        at = self.bump
        self.bump += size
        self.handed[name] = at
        return at

    def test_regions_are_distinct_and_stable(self):
        spec = [("lossless.codec.code", 1640), ("lossless.codec.state", 256),
                ("lossless.codec.log", 256)]
        first = placement.claim_regions(spec, claim=self.fake_claim)
        second = placement.claim_regions(spec, claim=self.fake_claim)
        self.assertEqual(first, second, "a claim must be stable within a boot")
        self.assertEqual(len(set(first.values())), 3)

    def test_overlap_between_register_and_camera_is_caught(self):
        """If the register hands back addresses that overlap, refuse."""
        spec = [("a", 1024), ("b", 1024)]
        overlapping = {"a": arena_bounds()[0], "b": arena_bounds()[0] + 512}
        with self.assertRaises(placement.PlacementError):
            placement.claim_regions(spec, claim=lambda n, s: overlapping[n])

    def test_bad_sizes_refused(self):
        for size in (0, -8, 13):
            with self.assertRaises(placement.PlacementError):
                placement.claim_regions([("x", size)], claim=self.fake_claim)


class PublicationTests(unittest.TestCase):
    def test_dcache_is_published_before_icache_is_invalidated(self):
        """Data must reach memory before the fetch path is told to forget."""
        seen = []
        placement.publish(lambda fn: seen.append(fn) or 0)
        self.assertEqual(seen, [placement.DCACHE_MAINTENANCE,
                                placement.ICACHE_INVALIDATE])

    def test_dsb_is_not_a_substitute(self):
        """The pair must be real ROM calls, not barriers."""
        self.assertEqual(len(placement.CACHE_PUBLICATION_FUNCTIONS), 2)
        self.assertEqual(placement.DCACHE_MAINTENANCE, 0xC000E91C)
        self.assertEqual(placement.ICACHE_INVALIDATE, 0xC000EABC)
        self.assertNotEqual(placement.DCACHE_MAINTENANCE,
                            placement.ICACHE_INVALIDATE)

    def test_results_are_reported_for_the_evidence_record(self):
        results = placement.publish(lambda fn: 0)
        self.assertEqual([r["function"] for r in results],
                         ["0xC000E91C", "0xC000EABC"])
        self.assertTrue(all(r["result"] == 0 for r in results))

    def test_a_failing_call_propagates(self):
        def boom(fn):
            raise RuntimeError("callfn did not return")
        with self.assertRaises(RuntimeError):
            placement.publish(boom)


class CaveRuleTests(unittest.TestCase):
    """The lossless probes held to fp_usb_shell/cave.py's rule.

    The rule: no source names a cave address at build time; it asks the
    allocator at boot and keeps only what the firmware has to branch to. A
    #ifndef-guarded default in a .S is allowed -- it is what the file
    assembles to on its own -- but every assemble() of it must pass the real
    address. getfile.S was the one template not converted with the rest: the
    host wrote parameters into the arena, the template read 0xC072F700, and
    the injected code hung with the shell behind it.
    """

    HOST_NAMED = ("STATE", "LOG", "HOOK_ARMED")
    # Every source the host assembles, not a chosen few: listing only two is
    # how exact_writer_power_preflight.S kept `.equ STATE` and died on the
    # first claimed build with "expected identifier".
    SOURCES = ("single_frame_encode_discard_probe.S", "exact_flush_writer_probe.S",
               "exact_writer_power_preflight.S", "exact_writer_scratch_preflight.S")

    def test_the_source_list_is_every_assembled_source(self):
        import single_frame_codec_probe as codec
        import exact_flush_writer_probe as flush
        assembled = {codec.PREFLIGHT_SOURCE.name, codec.SCRATCH_SOURCE.name,
                     codec.ENCODE_SOURCE.name, flush.SOURCE.name}
        self.assertEqual(assembled - set(self.SOURCES), set(),
                         "a source the host assembles is not held to the rule")
    HERE = pathlib.Path(__file__).resolve().parent

    def test_host_named_addresses_are_guarded_not_equated(self):
        for name in self.SOURCES:
            text = (self.HERE / name).read_text()
            for symbol in self.HOST_NAMED:
                if f"#define {symbol} " not in text:
                    continue
                self.assertIn(f"#ifndef {symbol}", text,
                              f"{name}: {symbol} must be overridable")
                self.assertNotRegex(
                    text, rf"^\.equ {symbol},", f"{name}: {symbol} is equated, "
                    "so a caller cannot pass the claimed address")

    def test_every_caller_passes_them(self):
        """No tool may assemble these sources without supplying the address."""
        import single_frame_codec_probe as codec
        import exact_flush_writer_probe as flush
        codec_src = pathlib.Path(codec.__file__).read_text()
        flush_src = pathlib.Path(flush.__file__).read_text()
        for src, who in ((codec_src, "codec"), (flush_src, "flush")):
            self.assertIn("STATE=0x", src, f"{who} never passes STATE")
            self.assertNotIn("--placed", src,
                             f"{who} still offers an unplaced path")
        self.assertIn("HOOK_ARMED=0x", codec_src)
        self.assertIn("LOG=0x", codec_src)

    def test_no_source_names_an_arena_address(self):
        lo, hi = arena_bounds()
        import re
        for path in sorted(self.HERE.glob("*.S")) + sorted(self.HERE.glob("*.py")):
            for line in path.read_text().splitlines():
                for hit in re.findall(r"0x[0-9A-Fa-f]{8}", line):
                    value = int(hit, 16)
                    if lo <= value < hi and "arena_bounds" not in line:
                        self.fail(f"{path.name}: {hit} is inside the arena")


if __name__ == "__main__":
    unittest.main(verbosity=2)
