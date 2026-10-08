#!/usr/bin/env python3
"""The integrated probe does more dangerous things at once than any before it.

It drives a DMA engine, writes into the camera's own frame buffer and changes
the length the card will write -- in one call, on a live clip. So the checks
here are about the joins between those, and about every path that must leave a
frame completely alone.
"""
import pathlib
import re
import sys
import unittest

sys.dont_write_bytecode = True
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import inline_compress_driver as driver       # noqa: E402
import exact_dng_writer_probe as base          # noqa: E402
import exact_flush_writer_probe as flush       # noqa: E402
import single_frame_codec_probe as codec       # noqa: E402

SOURCE = driver.SOURCE.read_text()


def code_only(text=None):
    """The source with its comments removed.

    Several checks here search a window of the source for an instruction. A
    comment inside that window pushes the instruction out of it, so a test that
    counts characters measures the prose and not the code -- which is how a
    correct refusal path came to fail its own test once its explanation grew.
    """
    text = SOURCE if text is None else text
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    # Removing a trailing comment leaves the whitespace that was in front of
    # it, which breaks an assertion on two consecutive instructions just as
    # surely as the comment did.
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def bodies(only=None):
    """Every label mapped to its body: from the label to the NEXT label.

    Bounded by the code itself rather than by a character count, and read with
    the comments stripped. `only` keeps just the labels with that prefix.
    """
    text = code_only()
    marks = [(m.start(), m.group(1))
             for m in re.finditer(r"^(\w+):", text, re.M)]
    out = {}
    for i, (at, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        if only is None or name.startswith(only):
            out[name] = text[at:end]
    return out


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


class MemorySafetyTests(unittest.TestCase):
    """Every read and every write must be inside a buffer we were given."""

    def test_a_zero_sum_is_refused_before_it_becomes_a_copy_length(self):
        """subs on a zero count would wrap and copy four gigabytes."""
        at = SOURCE.index("str     r1, [r4, #S_SUM]")
        window = SOURCE[at:at + 80]
        self.assertIn("cmp     r1, #0", window)
        self.assertIn("beq     fail_size", window)

    def test_every_pointer_is_null_checked_before_it_is_used(self):
        """Four pointers reach a DMA or a 3 MB copy; each gets its own check.

        Counting branches instead of naming them is how a missing check hides,
        so each register is looked for next to its own comparison.
        """
        head = SOURCE[:SOURCE.index("mov     r0, #2")]
        self.assertIn("ldr     r10, [r4, #S_BLOCK_A]", head)
        self.assertIn("ldr     r8, [r4, #S_BLOCK_B]", head)
        for register in ("r0", "r5", "r10", "r8"):
            pattern = f"cmp     {register}, #0\n    beq     shape_bad"
            self.assertIn(pattern, head,
                          f"{register} reaches a DMA or a copy unchecked")
        self.assertIn("tst     r5, #3", head)      # the buffer must be aligned
        self.assertIn("tst     r0, #3", head)      # and so must the writer


class TrailingIfdTests(unittest.TestCase):
    """The file must end after the second table, not after the payload."""

    def test_the_node_length_is_computed_from_the_file_not_the_payload(self):
        at = SOURCE.index("str     r1, [r4, #S_FILE_BYTES]")
        window = SOURCE[at - 260:at]
        # From count_table, which already carries both alignment round-ups.
        self.assertIn("add     r1, r3, r0, lsl #2", window)
        after = SOURCE[at:at + 260]
        self.assertIn("str     r1, [r9, #0x08]", after)

    def test_the_root_pointer_is_the_only_header_byte_that_moves(self):
        """Everything else in the frame's own header stays as the camera
        wrote it, which is what keeps the per-frame data alive."""
        self.assertEqual(SOURCE.count("str     r1, [r11, #4]"), 1)

    def test_both_value_fields_are_patched_from_seeded_offsets(self):
        at = SOURCE.index("ldr     r0, [r4, #S_PATCH_OFF]")
        window = SOURCE[at:at + 160]
        self.assertIn("str     r2, [r7, r0]", window)
        self.assertIn("ldr     r0, [r4, #S_PATCH_CNT]", window)
        self.assertIn("str     r3, [r7, r0]", window)

    def test_the_offsets_run_from_the_rasters_own_start(self):
        at = SOURCE.index("write_tables:")
        window = SOURCE[at - 120:at + 200]
        self.assertIn("ldr     r0, [r4, #S_PIXEL_OFF]", window)
        self.assertIn("add     r0, r0, r2", window)

    def test_the_driver_seeds_the_size_and_both_patch_offsets(self):
        source = pathlib.Path(driver.__file__).read_text()
        self.assertIn("S_IFD_SIZE * 4", source)
        self.assertIn('template["patch_offset_table_at"]', source)
        self.assertIn('template["patch_count_table_at"]', source)

    def test_the_driver_pads_the_template_to_the_copy_stride(self):
        source = pathlib.Path(driver.__file__).read_text()
        self.assertIn("(-len(blob)) % 16", source)

    def test_the_driver_and_the_probe_agree_on_the_template_offset(self):
        self.assertEqual(EQU["TEMPLATE_OFF"], driver.TEMPLATE_OFF)

    def test_the_tile_geometry_matches_between_driver_and_probe(self):
        self.assertEqual((driver.TILE_W, driver.TILE_H),
                         (EQU["TILE_W"], EQU["TILE_H"]))


class CapacityTests(unittest.TestCase):
    """The engine refuses a frame whose output exceeds the capacity it is
    TOLD, and F_INIT's own figure is 53.6 percent of the raster -- about 6.4
    bits a sample, which lossless compression cannot promise on a detailed
    frame. Block B holds 132 percent of it."""

    def test_the_capacity_passed_is_the_block_we_own(self):
        # the REQUEST's capacity field, not initStruct's ninth word
        req = SOURCE.index("build the 0x30-byte request")
        at = SOURCE.index("str     r0, [sp, #0x20]", req)
        window = SOURCE[at - 200:at]
        self.assertIn("ldr     r0, [r4, #S_DST_CAP]", window)
        self.assertNotIn("ldr     r0, [r6, #0x0C]", window)

    def test_the_capacity_is_never_bigger_than_the_block(self):
        import test_inline_geometry as geo
        for bits in (10, 12, 14):
            g = driver.geometry(geo.written(width=1024, height=576, bits=bits,
                                            raster=1024 * 576 * bits // 8))
            self.assertLessEqual(g["dst_cap"], codec.ALLOC_SIZE, bits)


class ChainOrderTests(unittest.TestCase):
    """The order is the design; a step out of place is a different experiment."""

    # The asynchronous order: a job submitted at the end of one call is
    # collected at the START of the next, so the poll and F_SIZE come first
    # and the submit comes last. The payload a frame carries is an EARLIER
    # frame's picture.
    # No source copy any more: the engine reads the camera's own buffer, so
    # a frame either COLLECTS the previous payload or SUBMITS the next one,
    # and the two alternate.
    STEPS = ("ENG_OPEN", "TWAI_FLG", "F_SIZE", "copy_back:",
             "str     r1, [r9, #0x08]", "do_submit:", "F_INIT",
             "TILE_COUNT_VAR", "ENG_SUBMIT", "ENG_START")

    def test_every_step_appears_exactly_once_and_in_order(self):
        at = -1
        for step in self.STEPS:
            found = SOURCE.find(step, at + 1)
            self.assertGreater(found, at, f"{step} is out of order or missing")
            at = found

    def test_the_engine_is_started_after_the_submit_and_nothing_between(self):
        submit = SOURCE.index("LDA     r12, ENG_SUBMIT")
        start = SOURCE.index("LDA     r12, ENG_START")
        between = SOURCE[submit:start]
        self.assertEqual(between.count("blx"), 1, between)

    def test_the_tile_count_is_published_with_the_submit_it_belongs_to(self):
        """C062FA48 takes its loop bound from 0xC302CDEC. It is published
        alongside the submit; the F_SIZE that reads it runs on the NEXT call."""
        publish = SOURCE.index("LDA     r1, TILE_COUNT_VAR")
        self.assertGreater(publish, SOURCE.index("do_submit:"))
        self.assertLess(publish, SOURCE.index("LDA     r12, ENG_SUBMIT"))

    def test_the_poll_passes_the_frame_on_and_still_checks_the_error_bit(self):
        """A timeout is not a failure any more: the job keeps running and the
        next frame collects it. An ERROR still is."""
        at = SOURCE.index("LDA     r12, TWAI_FLG")
        window = SOURCE[at:at + 700]
        self.assertIn("bne     not_ready", window)
        self.assertNotIn("fail_wait", window)
        self.assertIn("tst     r1, #4", window)
        self.assertIn("bne     fail_engine", window)

    def test_a_not_ready_frame_is_left_completely_alone(self):
        at = SOURCE.index("not_ready:") + len("not_ready:")
        body = [l.strip() for l in SOURCE[at:SOURCE.index("shape_bad:", at)]
                .splitlines()
                if l.strip() and not l.strip().startswith(("/*", "*"))]
        self.assertEqual(body, ["ldr     r0, [r4, #S_NOT_READY]",
                                "add     r0, r0, #1",
                                "str     r0, [r4, #S_NOT_READY]",
                                "b       call_real"])

    def test_the_write_back_only_runs_when_a_payload_exists(self):
        at = SOURCE.index("copy_back:")
        window = SOURCE[:at]
        self.assertIn("ldr     r0, [r4, #S_INFLIGHT]", window)
        self.assertIn("beq     do_submit                  /* nothing to collect",
                      window)

    def test_the_submit_runs_after_the_write_back_not_before(self):
        """Submitting first would set a DMA writing block B while the
        write-back copies out of it."""
        self.assertLess(SOURCE.index("copy_back:"),
                        SOURCE.index("LDA     r12, ENG_SUBMIT"))
        self.assertLess(SOURCE.index("str     r1, [r9, #0x08]"),
                        SOURCE.index("LDA     r12, ENG_SUBMIT"))

    def test_the_in_flight_flag_is_SET_right_after_the_engine_starts(self):
        """Without it no frame ever collects a payload and nothing is ever
        compressed on the card, while the state block still looks healthy."""
        at = SOURCE.index("LDA     r12, ENG_START")
        window = SOURCE[at:at + 400]
        self.assertIn("mov     r0, #1", window)
        self.assertIn("str     r0, [r4, #S_INFLIGHT]", window)

    def test_the_in_flight_flag_is_CLEARED_once_the_payload_is_consumed(self):
        """Without it block B would be written back again on the next frame,
        and every file after the first would carry the same picture."""
        at = SOURCE.index("str     r1, [r9, #0x08]")
        window = SOURCE[at:at + 900]
        self.assertIn("mov     r0, #0", window)
        self.assertIn("str     r0, [r4, #S_INFLIGHT]", window)
        self.assertLess(window.index("str     r0, [r4, #S_INFLIGHT]"),
                        window.index("do_submit:"))

    def test_no_copy_length_survives_a_copy_that_clobbers_it(self):
        """Every ldm in this probe writes r0..r3. A length held in one of them
        across another copy is a random number by the time it is used, and on
        2026-09-30 that copied block B into the camera's buffer with a garbage
        length and took the camera down hard.

        So each copy loop must load its own count from memory, between the
        previous copy and its own.
        """
        for label in ("copy_back:", "copy_ifd:"):
            at = SOURCE.index(label)
            setup = SOURCE[max(0, at - 700):at]
            previous = max(setup.rfind("ldm     r6!"), 0)
            after = setup[previous:]
            self.assertRegex(
                after, r"(ldr|LDA)\s+r\d+, ",
                f"{label} takes its count from a register that an earlier ldm "
                "may have written")

    def test_the_write_back_length_comes_from_the_state(self):
        at = SOURCE.index("copy_back:")
        window = SOURCE[at - 260:at]
        self.assertIn("ldr     r1, [r4, #S_SUM]", window)
        self.assertLess(window.index("ldr     r1, [r4, #S_SUM]"),
                        window.index("add     r12, r1, #15"))

    def test_block_B_is_reloaded_before_the_submit(self):
        """copy_ifd uses r8 as a scratch register and r8 holds block B."""
        at = SOURCE.index("do_submit:")
        self.assertIn("ldr     r8, [r4, #S_BLOCK_B]", SOURCE[at:at + 240])

    def test_the_open_runs_once_and_only_on_the_first_frame(self):
        at = SOURCE.index("ldr     r0, [r4, #S_DONE]")
        window = SOURCE[at:SOURCE.index("have_device:")]
        self.assertIn("cmp     r0, #0", window)
        self.assertIn("bne     have_device", window)
        self.assertIn("ENG_OPEN", window)


class FailurePathTests(unittest.TestCase):
    """A failure must leave the frame exactly as the camera made it."""

    def test_every_failure_label_reaches_skip_frame(self):
        # From each label to the NEXT one, not a fixed number of characters:
        # a window that ends mid-body turns a long COMMENT into a test failure,
        # which is what a 700-character one did once the refusal path grew an
        # explanation. Comments are stripped, so only instructions are read.
        windows = bodies()
        labels = [k for k in windows if k.startswith("fail_")]
        self.assertGreaterEqual(len(labels), 9)
        for label in labels:
            body = windows[label]
            self.assertTrue("skip_frame" in body or "FAIL" in body, label)

    def test_no_failure_path_can_run_after_the_buffer_is_written(self):
        """Once the write-back has happened the frame is ours and must be
        finished; a skip after it would leave a full-length node over a
        compressed payload."""
        after = SOURCE[SOURCE.index("copy_back:"):SOURCE.index("fail_submit_sp:")]
        self.assertNotIn("FAIL", after)
        self.assertNotIn("skip_frame", after)

    def test_the_submit_failure_puts_the_stack_back(self):
        """The request frame is 0x30 bytes and the failure path leaves it."""
        at = SOURCE.index("fail_submit_sp:")
        self.assertIn("add     sp, sp, #0x30", SOURCE[at:at + 80])

    def test_the_start_failure_runs_after_the_stack_is_restored(self):
        start = SOURCE.index("LDA     r12, ENG_START")
        window = SOURCE[start:start + 200]
        self.assertLess(window.index("add     sp, sp, #0x30"),
                        window.index("bne     fail_start"))

    def test_skipped_frames_are_counted_separately_from_shape_refusals(self):
        self.assertIn("S_SKIPPED", SOURCE)
        self.assertIn("S_SHAPE_BAD", SOURCE)
        self.assertNotEqual(EQU["S_SKIPPED"], EQU["S_SHAPE_BAD"])


class AlignmentTests(unittest.TestCase):
    """Every ldm, stm and str the trailer makes must be word aligned.

    ARM's ldm and stm require it whatever SCTLR.A says, and an unaligned one on
    the writer's own thread is a Data Abort. ifd_at is 0x13400 plus whatever the
    codec produced -- 1,343,147 on 2026-09-29, which put ifd_at at ...3 -- and
    that wedged the camera three times. The host tool cannot see this: Python
    has no alignment. So it is checked here, in the assembly.
    """

    def test_ifd_at_is_rounded_up_before_anything_uses_it(self):
        # The LAST one: there are two S_IFD_AT stores, the level-0 skip and
        # the real one.
        at = SOURCE.rindex("str     r1, [r4, #S_IFD_AT]")
        window = SOURCE[at - 200:at]
        self.assertIn("add     r1, r1, #15", window)
        self.assertIn("bic     r1, r1, #15", window)
        self.assertLess(at, SOURCE.index("copy_ifd:"))

    def test_the_table_pointer_is_word_aligned(self):
        """ifd_size is 714 and 714 is not a multiple of four."""
        at = SOURCE.index("/* r2 = offset_table, word aligned:")
        window = SOURCE[at - 160:at]
        self.assertIn("add     r2, r2, #3", window)
        self.assertIn("bic     r2, r2, #3", window)

    def test_every_multi_word_store_has_an_aligned_base(self):
        """ifd_at is rounded to 16, offset_table to 4, and count_table is
        offset_table plus four bytes a tile -- so all three are word aligned
        whatever the tile count turns out to be."""
        self.assertIn("add     r3, r2, r3, lsl #2", SOURCE)

    def test_the_file_length_comes_from_the_table_the_device_wrote(self):
        """Re-deriving it would lose the round-ups and cut the tables off."""
        at = SOURCE.index("/* r1 = file_bytes */")
        window = SOURCE[at - 120:at + 30]
        self.assertIn("add     r1, r3, r0, lsl #2", window)
        self.assertNotIn("ldr     r0, [r4, #S_IFD_SIZE]", window)


class ReportingTests(unittest.TestCase):
    """What the driver prints has to be what the probe does.

    The mode line said SYNCHRONOUS and named a WAIT for two runs after the
    probe stopped waiting. Stale output is worse than none: it is read as
    evidence.
    """

    def test_the_mode_line_describes_the_asynchronous_flow(self):
        source = pathlib.Path(driver.__file__).read_text()
        arm = source[source.index("def arm("):source.index("def status(")]
        self.assertIn("ASYNCHRONOUS", arm)
        self.assertIn("without waiting", arm)
        self.assertIn("EARLIER frame's picture", arm)

    def test_the_probe_really_does_not_wait(self):
        """The line and the assembly must agree: a one-tick poll, not a wait."""
        self.assertEqual(EQU["WAIT_TMOUT"], 1)


class GeometryTests(unittest.TestCase):
    """No shape is compiled in, and a frame of another shape is untouched."""

    def test_no_geometry_constant_survives_in_the_probe(self):
        for gone in ("WIDTH,", "HEIGHT,", "DEPTH,", "DNG_LEN,",
                     "PIXEL_OFFSET,", "RAW_BYTES,", "DECLARED,",
                     "PAYLOAD_CAP,", "TILE_COUNT,"):
            self.assertNotIn(f".equ {gone}", SOURCE, gone)

    def test_the_shape_is_checked_before_anything_is_touched(self):
        check = SOURCE.index("wrong_shape")
        for later in ("LDA     r12, ENG_OPEN", "LDA     r12, F_INIT"):
            self.assertLess(check, SOURCE.index(later), later)

    def test_a_frame_of_another_shape_is_left_completely_alone(self):
        at = SOURCE.index("wrong_shape:") + len("wrong_shape:")
        body = [l.strip() for l in SOURCE[at:SOURCE.index("not_ready:")]
                .splitlines()
                if l.strip() and not l.strip().startswith(("/*", "*"))]
        self.assertEqual(body, ["ldr     r0, [r4, #S_WRONG_SHAPE]",
                                "add     r0, r0, #1",
                                "str     r0, [r4, #S_WRONG_SHAPE]",
                                "b       call_real"])

    def test_the_tag_offsets_are_the_ones_measured_on_both_formats(self):
        for name, at in (("T_WIDTH", 0x16), ("T_HEIGHT", 0x22),
                         ("T_BITS", 0x2E), ("T_STRIP_OFF", 0x76),
                         ("T_STRIP_LEN", 0xA6)):
            self.assertEqual(EQU[name], at, name)

    def test_one_tag_id_is_checked_as_a_canary(self):
        """The rest are covered by their values: a shifted layout cannot also
        hold the seeded width, height, depth and strip offset."""
        # 256 is an 8-bit-rotated immediate, so the tag id is compared
        # directly; what matters is that a tag ID is compared at all, at the
        # offset the width tag is expected at.
        self.assertIn("ldrh    r0, [r11, #T_WIDTH]\n    cmp     r0, #256",
                      SOURCE)
        self.assertEqual(SOURCE.count("bne     wrong_shape"), 5)

    def test_the_host_reads_the_same_offsets_the_probe_does(self):
        source = pathlib.Path(driver.__file__).read_text()
        for at in ("0x16", "0x22", "0x2E", "0x76", "0xA6"):
            self.assertIn(at, source, at)

    def test_the_host_refuses_a_depth_the_engine_cannot_take(self):
        self.assertEqual(sorted(driver.DEPTH_CODE), [10, 12, 14, 16])
        self.assertNotIn(8, driver.DEPTH_CODE)

    def test_the_tile_array_is_bounded_on_both_sides(self):
        self.assertEqual(EQU["TILE_MAX"], driver.TILE_MAX)
        self.assertEqual(driver.S_AFTER_TILES, driver.S_TILE0 + driver.TILE_MAX)
        self.assertEqual(driver.STATE_WORDS, driver.S_GOOD_ENDPOS + 1)


class EndOfRunTests(unittest.TestCase):
    """A run must not end with a job nobody will collect.

    Every asynchronous run before 2026-09-30 did: the flag stayed set, the
    codec stayed open with its power on, block B held a payload no frame would
    claim, and the clip recorded next came out as zero-length files.
    """

    def test_the_last_frame_does_not_start_another_job(self):
        at = SOURCE.index("do_submit:")
        window = SOURCE[at:SOURCE.index("no_more_submits:")]
        self.assertIn("ldr     r0, [r4, #S_DONE]", window)
        self.assertIn("LDA     r1, FRAMES", window)
        self.assertIn("bhs     no_more_submits", window)
        self.assertLess(window.index("bhs     no_more_submits"),
                        window.index("LDA     r12, F_INIT"))

    def test_the_skip_lands_after_the_in_flight_flag_is_set(self):
        """Jumping past the submit must also jump past the flag that says one
        is running, or the next frame would collect a payload that is not
        there."""
        label = SOURCE.index("no_more_submits:")
        flag = SOURCE.index("str     r0, [r4, #S_INFLIGHT]      /* B is the")
        self.assertLess(flag, label)

    def test_restore_closes_the_codec_and_reaches_it(self):
        """Asserting the call EXISTS is not enough: a mutation that returned
        before it walked straight through."""
        source = pathlib.Path(driver.__file__).read_text()
        body = source[source.index("def restore("):source.index("def close_codec(")]
        self.assertIn("close_codec(fpsup)", body)
        before = body[:body.index("close_codec(fpsup)")]
        self.assertNotIn("\n    return", before,
                         "restore returns before it closes the codec")
        self.assertNotIn("\n    raise", before)


class GiveUpTests(unittest.TestCase):
    """One misbehaving frame stops the run; it does not tax every frame.

    On 2026-09-29 a 1000-tick wait on a codec that had been closed between
    clips made every later frame pay a full timeout, and the camera stopped
    answering. The fix is not a shorter timeout alone -- it is that the first
    such failure ends the run.
    """

    @staticmethod
    def body(label):
        """Just this label's own lines.

        A fixed-size window spills into the NEXT failure path, and two
        mutations walked straight through this test by borrowing the
        neighbour's FAIL_STOP.
        """
        at = SOURCE.index(f"{label}:") + len(label) + 2
        rest = SOURCE[at:]
        end = min((rest.index(m) for m in ("\nfail_", "\nshape_bad:",
                                           "\nskip_frame:", "\ncall_real:")
                   if m in rest), default=len(rest))
        return rest[:end]

    def test_every_codec_failure_stops_the_run(self):
        for label in ("fail_open", "fail_flag", "fail_init", "fail_submit_sp",
                      "fail_start", "fail_size"):
            body = self.body(label)
            self.assertIn("FAIL_STOP", body,
                          f"{label} lets the rest of the clip pay the same "
                          f"cost; its body is {body!r}")

    def test_a_frame_the_engine_refuses_does_NOT_stop_the_run(self):
        """Its output would not fit the capacity F_INIT allows -- about 54
        percent of the raster. That is the engine doing its job on a detailed
        frame, and treating it as fatal stopped a whole run on 2026-09-30."""
        body = self.body("fail_engine")
        self.assertIn("FAIL    ERR_ENGINE", body)
        self.assertNotIn("FAIL_STOP", body)
        self.assertIn("str     r0, [r4, #S_INFLIGHT]", body)

    def test_a_frame_that_merely_does_not_fit_does_NOT_stop_the_run(self):
        """A frame too big to compress is a normal outcome, not a fault."""
        body = self.body("fail_too_big")
        self.assertIn("FAIL    ERR_TOO_BIG", body)
        self.assertNotIn("FAIL_STOP", body)

    def test_a_shape_refusal_does_NOT_stop_the_run(self):
        at = SOURCE.index("shape_bad:")
        self.assertNotIn("S_GIVE_UP", SOURCE[at:at + 200])

    def test_the_give_up_flag_is_honoured_before_either_ceiling(self):
        tail = SOURCE[SOURCE.rindex("call_real:"):]
        give = tail.index("ldr     r0, [r4, #S_GIVE_UP]")
        self.assertLess(give, tail.index("LDA     r1, FRAMES"))
        self.assertIn("bne     no_rearm", tail[give:give + 80])

    def test_the_timeout_is_a_poll_and_not_a_wait(self):
        """A wait holds the writer thread. At 100 it aborted the clip on
        2026-09-30; the job is collected by the next frame instead."""
        self.assertEqual(EQU["WAIT_TMOUT"], 1)

    def test_the_driver_reports_whether_the_run_gave_up(self):
        source = pathlib.Path(driver.__file__).read_text()
        self.assertIn('"gave_up": bool(v[S_GIVE_UP])', source)


class BoundsAndRestoreTests(unittest.TestCase):

    def test_the_hook_is_restored_before_anything_risky(self):
        restore = SOURCE.index("str     r6, [r5]")
        for later in ("LDA     r12, ENG_OPEN", "copy_back:", "do_submit:"):
            self.assertLess(restore, SOURCE.index(later), later)

    def test_the_run_is_doubly_bounded_and_re_arms_last(self):
        tail = code_only()[code_only().rindex("call_real:"):]
        self.assertIn("LDA     r1, FRAMES\n    cmp     r0, r1", tail)
        # the hook-entry ceiling is three times the same register, so the
        # second comparison cannot drift away from the first
        self.assertIn("add     r1, r1, r1, lsl #1\n    cmp     r0, r1", tail)
        self.assertIn("ldr     r0, [r4, #S_COUNT]\n    add     r1, r1, r1", tail)
        self.assertEqual(tail.count("bhs     no_rearm"), 2)

    def test_the_bounds_go_through_a_register_not_an_immediate(self):
        """6000 is not an 8-bit-rotated immediate, so a raised bound written
        as one would not assemble -- which is the point: it must fail loudly
        rather than compare against a truncated value. Every ceiling therefore
        compares against a REGISTER, and none of them names FRAMES in a cmp.
        The hook-entry ceiling derives three times the bound from the register
        the frame ceiling just loaded, which is why there are three LDA uses
        and four comparisons."""
        self.assertNotIn("cmp     r0, #FRAMES", SOURCE)
        self.assertNotIn("cmp     r0, #FRAMES * 3", SOURCE)
        self.assertEqual(SOURCE.count("LDA     r1, FRAMES"), 3)
        self.assertIn("add     r1, r1, r1, lsl #1", SOURCE)
        for window in bodies("call_real").values():
            self.assertNotIn("#FRAMES", window)

    def test_nothing_is_allocated(self):
        for forbidden in ("F_ALLOC", "F_GET", "F_FREE"):
            self.assertNotIn(forbidden, SOURCE, forbidden)


class LayoutTests(unittest.TestCase):

    def test_every_state_word_matches_the_assembly(self):
        for name in [n for n in EQU if n.startswith("S_")]:
            index = getattr(driver, name, None)
            self.assertIsNotNone(index, f"the driver does not name {name}")
            self.assertEqual(EQU[name], index * 4, name)

    def test_the_driver_names_every_state_word(self):
        named = {n for n in EQU if n.startswith("S_")}
        have = {n for n in dir(driver) if n.startswith("S_")}
        self.assertEqual(named - have, set())

    def test_the_state_holds_every_tile_size_the_bound_allows(self):
        self.assertEqual(driver.S_AFTER_TILES, driver.S_TILE0 + driver.TILE_MAX)
        self.assertEqual(driver.STATE_WORDS, driver.S_GOOD_ENDPOS + 1)

    def test_the_driver_names_an_error_for_every_code(self):
        for name, value in EQU.items():
            if name.startswith("ERR_"):
                self.assertIn(value, driver.ERRORS, name)

    def test_the_proof_magic_matches_and_spells_MSL4(self):
        self.assertEqual(EQU["MARK_MAGIC"], driver.PROOF_MAGIC)
        self.assertEqual(driver.PROOF_MAGIC.to_bytes(4, "little"), b"MSL4")

    def test_the_call_addresses_match_the_codec_drivers(self):
        source = codec.ENCODE_SOURCE.read_text()
        for name in ("F_INIT", "F_SIZE", "ENG_SUBMIT", "ENG_START",
                     "ENG_CALLBACK", "FLAG_CREATE", "TWAI_FLG", "CLR_FLG",
                     "TILE_COUNT_VAR", "ENGINE_BLOCK"):
            mine = f".equ {name},"
            self.assertIn(mine, SOURCE, name)
            theirs = re.search(rf"^\.equ {name},\s*(0x[0-9A-Fa-f]+)",
                               source, re.M)
            if theirs:
                self.assertEqual(EQU[name], int(theirs.group(1), 16), name)


class ClaimTests(unittest.TestCase):

    def test_the_code_claim_is_borrowed_at_the_codec_chains_own_size(self):
        self.assertEqual(driver.CLAIMS["code"], codec.CLAIM_NAMES["code"])
        self.assertEqual(driver.CODE_CLAIM, codec.CODE_CLAIM_BYTES)

    def test_the_state_address_is_derived_from_block_A(self):
        source = pathlib.Path(driver.__file__).read_text()
        self.assertIn("flush.STATE = handle_a + STATE_OFF", source)

    def test_it_refuses_to_borrow_while_anything_is_armed(self):
        source = pathlib.Path(driver.__file__).read_text()
        take = source[source.index("def take_over_shared_region"):
                      source.index("def arm(")]
        self.assertIn("flush.HOOK_SITE", take)
        self.assertIn("base.HOOK_SITE", take)
        self.assertLess(take.index("HOOK_SITE"),
                        take.index("write_words_verified"))

    def test_arming_closes_the_codec_first(self):
        """C0708C60(1) fails when the power is already on, so a run that left
        the codec open poisoned the next run's first frame with open_return 1
        and gave up before compressing anything."""
        source = pathlib.Path(driver.__file__).read_text()
        arm = source[source.index("def arm("):source.index("def status(")]
        self.assertIn("callfn.call(ENG_CLOSE", arm)
        self.assertLess(arm.index("callfn.call(ENG_CLOSE"),
                        arm.index("flush.arm_site"))

    def test_the_handles_come_from_the_cameras_records(self):
        source = pathlib.Path(driver.__file__).read_text()
        self.assertIn("check_life_record", source)
        self.assertIn("read_lifecycle", source)

    def test_the_image_fits_the_borrowed_claim(self):
        assemble, _ = base.load_assembler(HERE.parents[1])
        code = assemble(driver.SOURCE, defines=("STATE=0xC072F700",
                                                "HOOK_ARMED=0xEB00333F",
                                                "FRAMES=8"))
        self.assertLessEqual(len(code), driver.CODE_CLAIM)


class VerdictTests(unittest.TestCase):

    @staticmethod
    def state(done=8, sum_=141805, skipped=0, error=0, stage=7,
              new_len=0x155000, writebacks=4):
        v = [0] * driver.STATE_WORDS
        v[driver.S_DONE], v[driver.S_SUM] = done, sum_
        v[driver.S_SKIPPED], v[driver.S_ERROR] = skipped, error
        v[driver.S_STAGE], v[driver.S_NEW_LEN] = stage, new_len
        v[driver.S_LATE], v[driver.S_RAW_BYTES] = writebacks, 3165360
        return v

    def test_nothing_compressed_reports_the_stage_it_stopped_at(self):
        said = driver.state_verdict(
            self.state(done=0, sum_=0, skipped=3, error=7, stage=3, writebacks=0))
        self.assertIn("NOTHING COMPRESSED", said)
        self.assertIn("owner word was not 1", said)
        self.assertIn("stage 3", said)

    def test_a_run_distinguishes_steps_from_writebacks_and_requires_validation(self):
        said = driver.state_verdict(self.state())
        self.assertIn("8 processed steps; 4 compressed write-back(s)", said)
        self.assertIn("4.5 percent", said)
        self.assertIn("not same-frame lossless validation", said)

    def test_a_partial_run_reports_the_skips(self):
        said = driver.state_verdict(self.state(done=3, skipped=5, error=8, writebacks=1))
        self.assertIn("3 processed steps; 1 compressed write-back(s)", said)
        self.assertIn("5 frame(s) were left stock", said)
        self.assertIn("timed out", said)

    @staticmethod
    def rows(sizes, jpeg=True, distinct=True):
        return [{"frame": i + 1, "bytes": b, "shortened": b < 0x318200,
                 "payload_starts_lossless_jpeg": jpeg,
                 "sha256": f"{i:016x}" if distinct else "same"}
                for i, b in enumerate(sizes)]

    def test_short_files_with_an_SOI_are_not_a_decode_verdict(self):
        said = driver.card_verdict(self.rows([0x155000] * 8))
        self.assertIn("SHORTENED WITH EXPECTED SOI", said)
        self.assertIn("trailing IFD", said)
        self.assertIn("does not prove", said)

    def test_nothing_shortened_is_not_read_as_success(self):
        said = driver.card_verdict(self.rows([0x318200] * 8))
        self.assertIn("NOTHING WAS SHORTENED", said)
        self.assertNotIn("CHAIN HOLDS", said)

    def test_short_without_an_SOI_names_the_disagreement(self):
        said = driver.card_verdict(self.rows([0x155000] * 8, jpeg=False))
        self.assertIn("SHORTENED WITHOUT EXPECTED SOI", said)

    def test_no_file_read_claims_no_verdict(self):
        said = driver.card_verdict([{"frame": 1, "error": "not read"}])
        self.assertIn("NO VERDICT", said)

    def test_identical_contents_are_counted_and_reported(self):
        said = driver.card_verdict(self.rows([0x155000] * 8, distinct=False))
        self.assertIn("1 distinct contents", said)

    def test_every_shape_produces_a_verdict(self):
        for sizes in ([], [0x318200], [0x155000],
                      [0x155000, 0x318200]):
            for jpeg in (True, False):
                said = driver.card_verdict(self.rows(sizes, jpeg))
                self.assertTrue(said and said.strip(), (sizes, jpeg))


if __name__ == "__main__":
    unittest.main()


class TalkingCallbackTests(unittest.TestCase):
    """The engine's only answer to a refused frame has been one bit.

    The IRQ handler C0630430 reads the device status from 0x300D0004, writes it
    straight back to clear it, and hands the word it read to whatever callback
    the submit installed. That word is the only copy in existence, and the
    firmware's own callback C062FCB0 keeps bit 2 of it and discards the rest.
    These tests hold up the replacement that keeps it.
    """

    def test_the_request_installs_OUR_callback_not_the_firmwares(self):
        req = SOURCE.index("build the 0x30-byte request")
        at = SOURCE.index("str     r0, [sp, #0x2C]", req)
        window = SOURCE[at - 120:at]
        self.assertIn("ldr     r0, [r4, #S_CB_ADDR]", window)
        self.assertNotIn("LDA     r0, ENG_CALLBACK", window)

    def test_a_null_callback_address_is_refused_before_anything_is_touched(self):
        """A zero there installs a null callback: the IRQ calls nothing, the
        flag is never set and every frame passes through -- indistinguishable
        from a codec that never finishes, which is the confusion that cost a
        day. It must be refused where the other seeded guards are, which is
        before the codec is even opened."""
        code = code_only()
        guard = code.index("ldr     r0, [r4, #S_CB_ADDR]\n    cmp     r0, #0\n"
                           "    beq     shape_bad")
        self.assertLess(guard, code.index("LDA     r12, ENG_OPEN"))
        self.assertLess(guard, code.index("str     r0, [sp, #0x2C]"))

    def test_the_callback_hands_the_word_on_to_the_firmwares_own(self):
        """It precedes C062FCB1, it does not replace it: the event flag must
        still be set by the code that has always set it, with r0 unchanged."""
        body = bodies()["our_callback"] + bodies()["cb_done"]
        self.assertIn("LDA     r12, ENG_CALLBACK", body)
        self.assertIn("bx      r12", body)
        # r0 is the status word and the firmware's callback reads it, so it
        # must come back off the stack before the tail call
        pop = bodies()["cb_done"].index("pop     {r0, r1, r4, lr}")
        self.assertLess(pop, bodies()["cb_done"].index("bx      r12"))

    def test_the_callback_saves_every_register_it_writes(self):
        """It runs in IRQ context on the writer's thread. Anything it clobbers
        beyond the AAPCS scratch set corrupts whatever it interrupted."""
        body = "".join(bodies()[k] for k in ("our_callback", "cb_error", "cb_done"))
        saved = set(re.search(r"push    \{([^}]*)\}", body).group(1).split(", "))
        written = set(re.findall(r"^    \w+\s+(r\d+|lr),", body, re.M))
        scratch = {"r0", "r1", "r2", "r3", "r12"}
        self.assertTrue(written <= saved | scratch,
                        f"clobbers {written - saved - scratch}")

    def test_the_first_refusal_is_the_one_kept(self):
        """A later refusal must not overwrite the interesting one, in either
        the callback or the refusal path."""
        self.assertIn("ldr     r12, [r4, #S_ENG_STATUS]\n    cmp     r12, #0\n"
                      "    streq   r0, [r4, #S_ENG_STATUS]\n"
                      "    streq   r1, [r4, #S_ENG_ENDPOS]", code_only())
        self.assertIn("ldr     r0, [r4, #S_ENG_ERRS]\n    cmp     r0, #0\n"
                      "    ldreq   r1, [r4, #S_COUNT]\n"
                      "    streq   r1, [r4, #S_ENG_AT]", code_only())

    def test_the_end_position_is_read_at_completion_not_a_frame_later(self):
        """0x300D00F8 belongs to the job that just finished. Reading it from
        the next frame's collect would read it after another job could have
        been programmed, so it is read in the callback and nowhere else."""
        self.assertEqual(code_only().count("LDA     r1, DEV_ENDPOS"), 1)
        self.assertIn("LDA     r1, DEV_ENDPOS", bodies()["our_callback"])

    def test_the_refusal_path_still_frees_the_block_and_carries_on(self):
        """Instrumentation must not have turned a survivable refusal into a
        stopped run: block B goes back and the run continues."""
        body = bodies()["fail_engine"] + bodies()["eng_counted"] \
            if "eng_counted" in bodies() else bodies()["fail_engine"]
        self.assertIn("str     r0, [r4, #S_INFLIGHT]", body)
        self.assertIn("FAIL    ERR_ENGINE", body)
        self.assertNotIn("FAIL_STOP", body)


class UncachedBaseTests(unittest.TestCase):
    """r11 carries the camera buffer's uncached alias for the whole frame."""

    def test_it_is_set_once_from_the_node_and_before_every_use(self):
        code = code_only()
        set_at = code.index("add     r11, r5, #UNCACHED")
        self.assertEqual(code.count("add     r11,"), 1)
        self.assertGreater(set_at, code.index("ldr     r5, [r9, #0x04]"))
        # every READ of it -- as an address base or an operand -- after it is
        # set. The push and pop lists name it too and are not reads.
        for line in re.finditer(r"^    (?!push|pop)\w+.*\br11\b.*$", code, re.M):
            if "add     r11, r5" not in line.group(0):
                self.assertGreater(line.start(), set_at, line.group(0))

    def test_it_is_saved_across_the_call(self):
        """r11 is callee-saved, so the firmware calls preserve it -- but the
        WRITER's r11 has to come back, which is the push list's job."""
        for op in ("push", "pop"):
            line = re.search(rf"^    {op}\s+\{{([^}}]*)\}}", SOURCE, re.M).group(1)
            self.assertIn("r11", line.split(", "), op)

    def test_nothing_recomputes_the_alias_from_r5_any_more(self):
        """Six recomputations were what pushed the probe past its claim."""
        self.assertNotIn("add     r7, r5, #UNCACHED", code_only())
        self.assertNotIn("add     r6, r5, #UNCACHED", code_only())
