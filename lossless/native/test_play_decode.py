"""Playback of a compressed clip: frames decoded back into the stock layout.

play_decode.c + play_decode_fixture.c on the host. A small frame in OUR
layout (IFD0 at 8 describing the uncompressed strip, the root moved to a
trailing IFD with Compression 7 and a contiguous tile table) is built here;
its header is the clip's first frame at clip open, and the whole file is
"read" into the player's buffer -- not 1 KiB aligned, as on the camera. A fake
engine decodes whole tiles the way the real one does. Checked: clip open asks
for the stock frame size and takes the scratch; each frame's strip ends up as
the stock pixels, decoded bottom up so no row is written over a stream not yet
read; nothing is written past the buffer's capacity or anywhere else; the root
goes back to IFD0; and every frame that is not ours, or cannot be done safely,
is left exactly as it was read.

No camera, USB or card.
"""
import ctypes as ct
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
(R_SLOT, R_TIFF, R_ROOT, R_FORMAT, R_TILES, R_IFD0, R_ROOM, R_ORDER, R_SCRATCH, R_ENGINE,
 R_COPY, R_BUSY) = range(12)
STOCK, DECODED = 0xFE, 0xFF
SEEN, N_STOCK, N_DECODED, OOB, BAD_WRITES, OPENS, CLOSES, ALLOCS, RELEASES, DMAS, \
    SUBMITS, MISALIGNED, SCRATCH_BYTES, CLIPS_OURS, SYNCS, SCRATCH_FAILED, CLIP_FAILED = range(17)


def build(directory, replace=None, name='play.dylib'):
    source = HERE / 'play_decode.c'
    if replace:
        old, new = replace
        text = source.read_text()
        if text.count(old) != 1:
            raise AssertionError('mutation seam not found once: ' + old[:60])
        source = Path(directory) / (name + '.c')
        source.write_text(text.replace(old, new))
    out = Path(directory) / name
    subprocess.run([shutil.which('clang') or 'clang', '-shared', '-fPIC', '-O2', '-std=c11',
                    '-Wall', '-Wextra', '-Werror', '-DFPL_PLAY_DECODE_HOST_TEST', '-I', str(HERE),
                    str(source), str(HERE / 'play_decode_fixture.c'), '-o', str(out)],
                   check=True, capture_output=True, text=True, timeout=60)
    lib = ct.CDLL(str(out))
    lib.fpl_fixture_load.argtypes = [ct.c_char_p, ct.c_uint32, ct.c_uint32]
    lib.fpl_fixture_load.restype = None
    lib.fpl_fixture_clip.argtypes = [ct.c_char_p, ct.c_uint32, ct.c_uint32, ct.c_uint32]
    lib.fpl_fixture_clip.restype = ct.c_uint32
    for fn, n, ret in (('knob', 2, False), ('run', 1, True), ('byte', 1, True), ('get', 1, True),
                       ('reset', 0, False), ('end', 0, False)):
        f = getattr(lib, 'fpl_fixture_' + fn)
        f.argtypes = [ct.c_uint32] * n
        f.restype = ct.c_uint32 if ret else None
    return lib


def ifd(entries, at, extra_at):
    """entries: (tag, type, count, value-or-bytes). Out-of-line bytes go at
    extra_at. Returns (ifd bytes, extra bytes)."""
    body, extra = b'', b''
    for tag, typ, count, val in sorted(entries):
        if isinstance(val, bytes):
            body += struct.pack('<HHII', tag, typ, count, extra_at + len(extra))
            extra += val + b'\0' * (-len(val) % 4)
        elif typ == 3:
            body += struct.pack('<HHIHH', tag, typ, count, val, 0)
        else:
            body += struct.pack('<HHII', tag, typ, count, val)
    return struct.pack('<H', len(entries)) + body + b'\0\0\0\0', extra


def raster_len(w=96, h=74, bits=12, **_):
    pitch = ((w * bits // 8) + 3) & ~3
    return (h - 1) * pitch + ((-(-w // 12) * 12 * bits // 8 + 3) & ~3)


def frame(w=96, h=74, bits=12, tw=32, th=32, strip=0x400, ours=True, comp=7,
          ifd0_w=None, gap_at=None, tiles_n=None, cut=0, tile_len=None):
    """A file in our layout; returns (bytes, expected strip bytes, pitch)."""
    across, down = -(-w // tw), -(-h // th)
    tiles = []
    for t in range(across * down):
        n = tile_len or 16 + 4 * ((t * 13) % 11)
        tiles.append(struct.pack('<II', n, t) + b'\x5a' * (n - 8))
    pitch = ((w * bits // 8) + 3) & ~3
    # as the recorder lays it out (C0135D40): the last row rounded to 12 px
    raster = (h - 1) * pitch + ((-(-w // 12) * 12 * bits // 8 + 3) & ~3)
    head = bytearray(b'II*\0' + b'\0\0\0\0')
    e0 = [(256, 4, 1, ifd0_w or w), (257, 4, 1, h), (258, 3, 1, bits), (259, 3, 1, 1),
          (273, 4, 1, strip), (279, 4, 1, raster)]
    blk, extra = ifd(e0, 8, 8 + 2 + 12 * len(e0) + 4)
    head += blk + extra
    head += b'\0' * (strip - len(head))
    stream = b''.join(tiles)
    offs, at = [], strip
    for t in tiles:
        offs.append(at)
        at += len(t)
    if gap_at is not None:
        offs[gap_at] += 4
    counts = [len(t) for t in tiles]
    count_n = tiles_n or len(tiles)
    trailer_at = strip + len(stream)
    e1 = [(256, 4, 1, w), (257, 4, 1, h), (258, 3, 1, bits), (259, 3, 1, comp),
          (322, 4, 1, tw), (323, 4, 1, th),
          (324, 4, count_n, struct.pack(f'<{len(offs)}I', *offs)[:4 * count_n]),
          (325, 4, count_n, struct.pack(f'<{len(counts)}I', *counts)[:4 * count_n])]
    blk, extra = ifd(e1, trailer_at, trailer_at + 2 + 12 * len(e1) + 4)
    data = bytes(head) + stream + blk + extra
    data = bytearray(data[:len(data) - cut] if cut else data)
    struct.pack_into('<I', data, 4, trailer_at if ours else 8)
    expected = bytearray(pitch * h)     # what is decoded; the raster's last-row
                                        # padding past it is not written
    for y in range(h):
        ty, yi = divmod(y, th)
        for tx in range(across):
            x0, x1 = tx * tw, min(w, tx * tw + tw)
            for b in range(x0 * bits // 8, x1 * bits // 8):
                expected[y * pitch + b] = ((ty * across + tx) * 7 + yi + b) & 0xFF
    return bytes(data), bytes(expected), pitch


class PlayDecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='fpl-play-')
        cls.lib = build(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def get(self, f): return self.lib.fpl_fixture_get(f)

    def setUp(self):
        self.lib.fpl_fixture_reset()

    def open_clip(self, data, size=None, busy=0):
        return self.lib.fpl_fixture_clip(data, len(data), size or len(data), busy)

    def load(self, data, cap):
        self.data = data
        self.lib.fpl_fixture_load(data, len(data), cap)

    def strip(self, at, n):
        return bytes(self.lib.fpl_fixture_byte(at + i) for i in range(n))

    def untouched(self):
        self.assertEqual(self.strip(0, len(self.data)), self.data, 'the frame was changed')
        self.assertEqual((self.get(OOB), self.get(BAD_WRITES)), (0, 0))
        self.assertEqual(self.get(OPENS), self.get(CLOSES))

    def decode(self, **kw):
        data, expected, pitch = frame(**kw)
        strip = kw.get('strip', 0x400)
        self.open_clip(data)
        cap = max(strip + max(len(expected), raster_len(**kw)), len(data))  # the stock size
        self.load(data, cap)
        return data, expected, cap, strip

    # ---- clip open --------------------------------------------------------
    def test_a_clip_of_ours_asks_for_the_stock_frame_and_takes_the_scratch(self):
        data, expected, pitch = frame()
        stock = 0x400 + ((len(expected) + 0x1FF) & ~0x1FF)
        self.assertEqual(self.open_clip(data), stock)
        self.assertEqual((self.get(CLIPS_OURS), self.get(ALLOCS)), (1, 1))
        self.assertEqual(len(expected), raster_len())
        self.assertGreaterEqual(self.get(SCRATCH_BYTES), 2 * pitch * 32)
        self.assertEqual(self.open_clip(data), stock)
        self.assertEqual(self.get(ALLOCS), 0, 'the scratch was taken twice')
        self.lib.fpl_fixture_end()
        self.assertEqual((self.get(RELEASES), self.get(SCRATCH_BYTES)), (1, 0))
        self.lib.fpl_fixture_end()
        self.assertEqual(self.get(RELEASES), 1, 'freed twice')

    def test_a_stock_clip_is_left_as_it_is(self):
        data, expected, pitch = frame(ours=False)
        self.assertEqual(self.open_clip(data, 123456), 123456)
        self.assertEqual((self.get(CLIPS_OURS), self.get(ALLOCS)), (0, 0))

    def test_no_clip_change_while_a_take_is_live(self):
        data, expected, pitch = frame()
        self.assertEqual(self.open_clip(data, 777, busy=1), 777)
        self.assertEqual(self.get(ALLOCS), 0)

    def test_a_first_frame_that_cannot_be_read_changes_nothing(self):
        data, expected, pitch = frame()
        self.lib.fpl_fixture_knob(4, 1)
        self.assertEqual(self.open_clip(data, 999), 999)
        self.assertEqual((self.get(CLIP_FAILED), self.get(ALLOCS)), (1, 0))

    def test_a_clip_whose_ifd0_is_not_a_stock_strip_changes_nothing(self):
        data, expected, pitch = frame(bits=8)
        self.assertEqual(self.open_clip(data, 999), 999)
        self.assertEqual(self.get(ALLOCS), 0)

    def test_no_scratch_at_clip_open_still_gives_the_stock_size(self):
        data, expected, pitch = frame()
        self.lib.fpl_fixture_knob(2, 1)
        self.assertEqual(self.open_clip(data), 0x400 + ((len(expected) + 0x1FF) & ~0x1FF))
        self.assertEqual(self.get(SCRATCH_FAILED), 1)

    # ---- frames of ours, decoded -------------------------------------------
    def check_decoded(self, data, expected, strip):
        self.assertEqual(self.lib.fpl_fixture_run(0), DECODED)
        self.assertEqual(self.strip(strip, len(expected)), expected)
        self.assertEqual(struct.unpack('<I', self.strip(4, 4))[0], 8, 'root not back on IFD0')
        self.assertEqual(self.strip(8, strip - 8), data[8:strip], 'IFD0 changed')
        self.assertEqual((self.get(OOB), self.get(BAD_WRITES)), (0, 0), 'wrote out of bounds')
        self.assertEqual(self.get(OPENS), self.get(CLOSES))
        self.assertEqual(self.get(MISALIGNED), 0)
        self.assertEqual(self.get(SYNCS), 1)

    def test_a_frame_of_ours_becomes_the_stock_frame(self):
        data, expected, cap, strip = self.decode()
        self.check_decoded(data, expected, strip)
        self.assertEqual(self.get(OPENS), 3, 'one decode per tile row')

    def test_a_frame_that_compresses_badly_is_still_decoded_bottom_up(self):
        # every tile row's stream almost as long as its raw rows: a row
        # written top down would land on the next row's stream
        data, expected, cap, strip = self.decode(h=64, tile_len=1480)
        self.check_decoded(data, expected, strip)

    def test_whole_tile_rows(self):
        data, expected, cap, strip = self.decode(h=64)
        self.check_decoded(data, expected, strip)

    def test_a_frame_shorter_than_one_tile_row(self):
        data, expected, cap, strip = self.decode(h=20)
        self.check_decoded(data, expected, strip)

    def test_a_partial_last_column(self):
        data, expected, cap, strip = self.decode(w=88)
        self.check_decoded(data, expected, strip)

    def test_a_width_whose_last_raster_row_is_padded_to_12_pixels(self):
        # FHD is 1936 wide: the recorder's last row is 1944 px, 12 B more
        data, expected, pitch = frame(w=88)
        raster = raster_len(w=88)
        self.assertGreater(raster, pitch * 74)
        self.assertEqual(self.open_clip(data), 0x400 + ((raster + 0x1FF) & ~0x1FF))
        self.assertEqual(self.get(CLIPS_OURS), 1, 'not taken for ours')

    def test_nothing_is_written_past_an_exact_capacity(self):
        data, expected, cap, strip = self.decode()
        self.lib.fpl_fixture_run(0)
        self.assertEqual(self.lib.fpl_fixture_byte(cap), 0xA5)
        self.assertEqual(self.get(BAD_WRITES), 0)

    # ---- not ours, or not safe: left exactly as read ----------------------
    def refused(self, why, busy=0, cap_delta=0, **kw):
        data, expected, pitch = frame(**kw)
        strip = kw.get('strip', 0x400)
        self.open_clip(frame()[0])
        cap = strip + max(len(expected), raster_len(**kw)) + cap_delta
        self.load(data, cap if why == R_ROOM else max(cap, len(data)))
        self.assertEqual(self.lib.fpl_fixture_run(busy), why)
        self.untouched()
        if why < 12:
            self.assertEqual(self.get(100 + why), 1)

    def test_a_stock_frame_is_left_alone(self):
        self.refused(STOCK, ours=False)
        self.assertEqual(self.get(N_STOCK), 1)

    def test_while_a_take_is_live(self):
        self.refused(R_BUSY, busy=1)

    def test_a_trailer_that_is_not_compression_7(self):
        self.refused(R_FORMAT, comp=1)

    def test_a_tile_table_with_a_gap(self):
        self.refused(R_TILES, gap_at=4)

    def test_a_tile_table_of_the_wrong_length(self):
        self.refused(R_TILES, tiles_n=8)

    def test_a_file_read_short_of_its_tile_table(self):
        self.refused(R_TILES, cut=40)

    def test_a_file_read_short_of_its_trailer(self):
        self.refused(R_ROOT, cut=150)

    def test_an_ifd0_that_is_not_this_frame(self):
        self.refused(R_IFD0, ifd0_w=88)

    def test_a_buffer_one_byte_too_small(self):
        self.refused(R_ROOM, cap_delta=-1)

    def test_an_unsupported_depth(self):
        self.refused(R_FORMAT, bits=8)

    def test_a_tile_row_worse_than_raw(self):
        self.refused(R_ORDER, h=64, tile_len=2000)

    def test_no_scratch(self):
        data, expected, pitch = frame()
        self.open_clip(data)
        self.lib.fpl_fixture_end()
        self.load(data, 0x400 + len(expected))
        self.assertEqual(self.lib.fpl_fixture_run(0), R_SCRATCH)
        self.untouched()

    def test_not_a_tiff(self):
        data, expected, pitch = frame()
        self.open_clip(data)
        self.load(b'MM' + data[2:], 0x400 + len(expected))
        self.assertEqual(self.lib.fpl_fixture_run(0), R_TIFF)

    def test_no_buffer(self):
        data, expected, pitch = frame()
        self.load(data, 0x400 + len(expected))
        self.lib.fpl_fixture_knob(3, 0)
        self.assertEqual(self.lib.fpl_fixture_run(0), R_SLOT)

    # ---- failures on the way ----------------------------------------------
    def test_a_failed_first_copy_changes_nothing(self):
        data, expected, cap, strip = self.decode()
        self.lib.fpl_fixture_knob(0, 1)
        self.assertEqual(self.lib.fpl_fixture_run(0), R_COPY)
        self.untouched()

    def test_an_engine_failure_closes_and_keeps_the_root(self):
        data, expected, cap, strip = self.decode()
        self.lib.fpl_fixture_knob(1, 1)
        self.assertEqual(self.lib.fpl_fixture_run(0), R_ENGINE)
        self.assertEqual(self.get(OPENS), self.get(CLOSES))
        self.assertEqual(self.strip(4, 4), data[4:8], 'root moved for a frame not decoded')
        self.assertEqual(self.get(BAD_WRITES), 0)


class PlayDecodeMutationTests(unittest.TestCase):
    MUTATIONS = {
        'top down': ('    for (uint32_t r = down; r-- > 0;) {',
                     '    for (uint32_t r = 0; r < down; ++r) {'),
        'no order check': ('            return refuse(p, FPL_PLAY_R_ORDER);', '            (void)0;'),
        'leaves the root on the trailer': ('    poke(buf + 4, ROOT_STOCK);\n', ''),
        'no cache sync': ('    cache_sync();\n    p->last_us', '    p->last_us'),
        'trusts the capacity': ('cap - f0.soff < strip_bytes(&f0)',
                                'cap - f0.soff < strip_bytes(&f0) - strip_bytes(&f0)'),
        'no slack for the padded last row': ('f0->scnt <= pitch_of(f0->w, f0->bits) * f0->h + LAST_ROW_SLACK',
                                             'f0->scnt <= pitch_of(f0->w, f0->bits) * f0->h'),
        'ignores a gap in the tiles': ('        if (o != end || !n', '        if (!n'),
        'decodes while recording': ('    if (busy) return refuse(p, FPL_PLAY_R_BUSY);\n',
                                    '    (void)busy;\n'),
        'decodes straight into the strip': ('        if (decode_row(&f, across, o_at, s_at) != 0)',
                                            '        if (decode_row(&f, across, strip + r * raw_row, s_at) != 0)'),
        'copies whole tile rows in': ('(r == down - 1u ? rem : f.th) * pitch',
                                      '(r == down - 1u ? rem + 2u : f.th) * pitch'),
        'asks the stock size for a stock clip': (
            '    if (rd32(h, 1) != TIFF_LE || rd32(h + 4, 1) == ROOT_STOCK) return size;   /* stock */',
            '    if (rd32(h, 1) != TIFF_LE) return size;'),
        'takes a second scratch': ('    if (!p->scratch) {\n        p->scratch = scratch_get',
                                   '    {\n        p->scratch = scratch_get'),
        'no clip check while recording': ('    if (busy || !desc) return size;',
                                          '    (void)busy;\n    if (!desc) return size;'),
    }

    def test_every_mutation_is_caught(self):
        with tempfile.TemporaryDirectory(prefix='fpl-play-mut-') as tmp:
            for index, (name, seam) in enumerate(self.MUTATIONS.items()):
                with self.subTest(mutation=name):
                    lib = build(tmp, seam, f'm{index}.dylib')

                    class Against(PlayDecodeTests):
                        @classmethod
                        def setUpClass(cls):
                            cls.lib = lib

                        @classmethod
                        def tearDownClass(cls):
                            pass
                    result = unittest.TestResult()
                    unittest.defaultTestLoader.loadTestsFromTestCase(Against).run(result)
                    self.assertFalse(result.wasSuccessful(), f'{name} was not caught')


class ArmCompileTests(unittest.TestCase):
    def test_it_builds_freestanding_for_the_camera(self):
        clang = shutil.which('clang') or 'clang'
        nm = shutil.which('llvm-nm') or shutil.which('nm') or 'nm'
        with tempfile.TemporaryDirectory(prefix='fpl-play-arm-') as tmp:
            out = Path(tmp) / 'play.o'
            subprocess.run([clang, '--target=armv7a-none-eabi', '-mcpu=cortex-a9', '-mthumb',
                            '-mfloat-abi=soft', '-mfpu=none', '-ffreestanding', '-fno-builtin',
                            '-nostdlib', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-Wpedantic', '-c', '-I', str(HERE), str(HERE / 'play_decode.c'),
                            '-o', str(out)], check=True, capture_output=True, text=True,
                           timeout=30)
            undefined = subprocess.run([nm, '-u', str(out)], capture_output=True,
                                       text=True).stdout
            for forbidden in ('memcpy', 'memset', '__aeabi', '_test_natives'):
                self.assertNotIn(forbidden, undefined, undefined)


if __name__ == '__main__':
    unittest.main()
