#!/usr/bin/env python3
"""The shape is worked out on the host now, so the safety lives here.

Every number the probe used to carry as a constant -- where the raster starts,
how long it is, what the engine may be told about it, how many tiles it makes,
how much room the trailer needs -- comes out of geometry(). The invariants that
used to be asserted about two compiled-in values are properties of that
function, and here they are checked against MANY shapes, including ones the
camera has never produced, which is a stronger claim than the old tests made.

A header is built from scratch rather than read from a file: a fixture that
goes missing turns a test into a silent skip, and this project has been bitten
by that.
"""
import pathlib
import struct
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import inline_compress_driver as driver      # noqa: E402
import exact_flush_writer_probe as flush     # noqa: E402
import single_frame_codec_probe as codec     # noqa: E402

# The five the probe reads, at the offsets it reads them from, plus enough
# filler entries to put them there. Tags are sorted, so the filler tag numbers
# are chosen to land each real tag on its measured offset.
LAYOUT = [(256, 0x16), (257, 0x22), (258, 0x2E), (273, 0x76), (279, 0xA6)]


def header(width=1936, height=1090, bits=12, pixel_off=78848,
           raster=3165372, entries=58, shift=0):
    """A TIFF header whose five tags sit where the probe looks."""
    root = 8
    table = {}
    for tag, at in LAYOUT:
        table[at + shift] = tag
    values = {256: width, 257: height, 258: bits, 273: pixel_off, 279: raster}
    out = bytearray(pixel_off)
    out[0:4] = b"II\x2a\x00"
    struct.pack_into("<I", out, 4, root)
    struct.pack_into("<H", out, root, entries)
    filler = 1
    for i in range(entries):
        at = root + 2 + i * 12
        tag = table.get(at)
        if tag is None:
            while filler in values or filler in (t for t, _ in LAYOUT):
                filler += 1
            tag, value = filler, 0
            filler += 1
        else:
            value = values[tag]
        struct.pack_into("<HHII", out, at, tag, 4, 1, value)
    return bytes(out) + bytes(raster)


def written(**kw):
    blob = header(**kw)
    with tempfile.NamedTemporaryFile(suffix=".DNG", delete=False) as fh:
        fh.write(blob)
        return pathlib.Path(fh.name)


class ReadsTheShapeTests(unittest.TestCase):

    def test_it_reads_a_frame_this_camera_actually_makes(self):
        g = driver.geometry(written())
        self.assertEqual((g["width"], g["height"], g["bits"]), (1936, 1090, 12))
        self.assertEqual(g["pixel_off"], 78848)
        self.assertEqual(g["depth"], 0)
        self.assertEqual(g["tiles"], 12)

    def test_it_reads_a_shape_the_camera_has_never_made(self):
        g = driver.geometry(written(width=2048, height=1152, bits=14,
                                    pixel_off=0x14000, raster=2048 * 1152 * 14 // 8))
        self.assertEqual(g["depth"], 1)
        self.assertEqual(g["tiles"], 4 * 4)
        self.assertEqual(g["pixel_off"], 0x14000)

    def test_a_layout_whose_tags_moved_is_refused(self):
        with self.assertRaises(flush.ProbeError) as caught:
            driver.geometry(written(shift=12))
        self.assertIn("layout", str(caught.exception))

    def test_a_depth_the_engine_cannot_take_is_refused_by_name(self):
        with self.assertRaises(flush.ProbeError) as caught:
            driver.geometry(written(bits=8))
        self.assertIn("8 bits", str(caught.exception))
        self.assertIn("depth table", str(caught.exception))

    def test_every_depth_the_engine_takes_is_accepted(self):
        for bits, code in ((12, 0), (14, 1), (16, 2), (10, 3)):
            # small enough that even 16 bits fits the block
            g = driver.geometry(written(width=1024, height=576, bits=bits,
                                        raster=1024 * 576 * bits // 8))
            self.assertEqual(g["depth"], code, bits)


class SafetyTests(unittest.TestCase):
    """What the probe used to guarantee with constants, over many shapes."""

    # Shapes whose raster fits the 4 MiB block the chain allocates.
    SHAPES = [
        dict(width=1936, height=1090, bits=12),     # FHD, what we record
        dict(width=1920, height=1080, bits=12),
        dict(width=2048, height=1152, bits=14),     # 4,128,768: only just
        dict(width=1024, height=576, bits=16),
        dict(width=512, height=368, bits=12),       # one tile
    ]

    # Rasters far bigger than any block. Read in place, so they are fine.
    TOO_BIG = [
        dict(width=3856, height=2170, bits=10),     # 10,459,400
        dict(width=3856, height=2170, bits=12),     # 12,551,280
        dict(width=4096, height=2160, bits=10),     # 11,059,200
    ]

    def test_a_big_raster_is_read_in_place_and_no_longer_refused(self):
        """The source copy was what made UHD impossible: 10 MB into a 4 MiB
        block. The engine reads the camera's own buffer now, so the raster's
        size stops mattering -- only the OUTPUT has to fit block B, and the
        engine refuses work that will not."""
        for s in self.TOO_BIG:
            raster = s["width"] * s["height"] * s["bits"] // 8
            g = driver.geometry(written(raster=raster, **s))
            self.assertEqual(g["declared"] % 1024, 0, s)
            self.assertLessEqual(g["dst_cap"], codec.ALLOC_SIZE, s)

    def shapes(self):
        for s in self.SHAPES:
            raster = s["width"] * s["height"] * s["bits"] // 8
            yield s, driver.geometry(written(raster=raster, **s))

    def test_the_raster_copy_is_a_multiple_of_the_copy_stride(self):
        """copy_in moves sixteen bytes and ends on a zero count."""
        for s, g in self.shapes():
            self.assertEqual(g["raw_bytes"] % 16, 0, s)

    def test_the_raster_copy_never_leaves_the_cameras_buffer(self):
        for s, g in self.shapes():
            self.assertLessEqual(g["pixel_off"] + g["raw_bytes"], g["dng_len"], s)

    def test_the_declared_length_is_1_KiB_aligned_and_inside_the_frame(self):
        """C062FFF8 refuses a length whose low ten bits are set, and the
        source is the camera's own buffer now: rounding UP would read past its
        end -- 512 bytes at FHD -- so it is rounded DOWN and must still land
        inside the frame."""
        for s, g in self.shapes():
            self.assertEqual(g["declared"] % 1024, 0, s)
            self.assertLessEqual(g["pixel_off"] + g["declared"], g["dng_len"], s)
            self.assertLess(g["raw_bytes"] - g["declared"], 1024, s)

    def test_the_destination_capacity_is_the_block_we_own(self):
        """Nothing checked this before: at any format bigger than FHD the
        engine's own figure would have written past the end of block B."""
        for s, g in self.shapes():
            self.assertLessEqual(g["dst_cap"], codec.ALLOC_SIZE, s)
            self.assertEqual(g["dst_cap"] % 1024, 0, s)

    def test_the_payload_cap_is_the_room_after_the_header(self):
        for s, g in self.shapes():
            self.assertEqual(g["payload_cap"], g["dng_len"] - g["pixel_off"], s)

    def test_the_tile_count_covers_the_whole_frame(self):
        for s, g in self.shapes():
            across = -(-g["width"] // driver.TILE_W)
            down = -(-g["height"] // driver.TILE_H)
            self.assertEqual(g["tiles"], across * down, s)
            self.assertGreaterEqual(across * driver.TILE_W, g["width"], s)
            self.assertGreaterEqual(down * driver.TILE_H, g["height"], s)

    def test_the_tile_count_fits_the_state_block(self):
        for s, g in self.shapes():
            self.assertLessEqual(g["tiles"], driver.TILE_MAX, s)

    def test_a_frame_needing_more_tiles_than_the_state_holds_is_refused(self):
        big = 512 * 13         # thirteen tiles across
        tall = 368 * 13        # thirteen down: 169 > TILE_MAX
        with self.assertRaises(flush.ProbeError) as caught:
            driver.geometry(written(width=big, height=tall,
                                    raster=big * tall * 12 // 8))
        self.assertIn("tiles", str(caught.exception))

    def test_the_engines_own_limits_on_the_geometry_hold(self):
        """width 8..0x4000 and a multiple of 8; height 2..0x4000 and even;
        tile width 32..512 a multiple of 32; tile height 2..512 and even."""
        self.assertTrue(32 <= driver.TILE_W <= 512 and driver.TILE_W % 32 == 0)
        self.assertTrue(2 <= driver.TILE_H <= 512 and driver.TILE_H % 2 == 0)
        for s, g in self.shapes():
            self.assertTrue(8 <= g["width"] <= 0x4000 and g["width"] % 8 == 0, s)
            self.assertTrue(2 <= g["height"] <= 0x4000 and g["height"] % 2 == 0, s)


if __name__ == "__main__":
    unittest.main()
