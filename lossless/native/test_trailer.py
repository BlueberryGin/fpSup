"""The trailing IFD, built on the camera from the frame's own header.

End to end on a REAL stock FHD frame from this camera: its pixels are
encoded into twelve lossless-JPEG tiles by the host encoder, padded exactly
as the firmware pads (zeros to 1 KiB, the pad added to the last tile), and
trailer.c then writes the trailer into the frame's own buffer. The result
must be byte-identical to projects/lossless-sup/tools/trailing_ifd.py -- the
layout decoded on 2026-09-26 and written by the camera on 2026-09-29 -- and
LibRaw must decode it to exactly the stock frame's samples.

No camera, USB or card.
"""
import ctypes as ct
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STOCK = ROOT / 'projects/open-gate/captures/opengate_ev_bug_20260914/dng/A001_083_FHD_f01.DNG'
sys.path.insert(0, str(ROOT / 'research/slimRAW'))
sys.path.insert(0, str(ROOT / 'projects/lossless-sup/tools'))
OK, INVALID, BUSY, UNSUPPORTED, NOT_READY, FAULT = range(6)
PIXELS = 0x13400


def build(directory, source=None, name='trailer.dylib'):
    out = Path(directory) / name
    subprocess.run([shutil.which('clang') or 'clang', '-shared', '-fPIC', '-O2', '-std=c11',
                    '-Wall', '-Wextra', '-Werror', '-I', str(HERE),
                    str(source or HERE / 'trailer.c'), str(HERE / 'trailer_fixture.c'),
                    '-o', str(out)], check=True, capture_output=True, text=True, timeout=60)
    lib = ct.CDLL(str(out))
    lib.fpl_fixture_build.argtypes = [ct.c_void_p, ct.c_uint32, ct.c_uint32, ct.c_void_p,
                                      ct.c_uint32, ct.c_uint32, ct.c_uint32, ct.c_uint32,
                                      ct.c_void_p]
    lib.fpl_fixture_build.restype = ct.c_uint32
    lib.fpl_fixture_plan.argtypes = [ct.c_void_p, ct.c_uint32, ct.c_uint32, ct.c_uint32]
    lib.fpl_fixture_plan.restype = ct.c_uint32
    return lib


def encode_stock():
    """The stock frame's twelve tiles, padded as the firmware pads them."""
    import numpy as np
    import ljenc
    import mkcompressed as mk
    data = STOCK.read_bytes()
    _, _, _, ifds = mk.parse(str(STOCK))
    t = ifds[0]
    w, h, off = t[256][2][0], t[257][2][0], t[273][2][0]
    b = np.frombuffer(data, np.uint8, count=w * h * 3 // 2, offset=off).reshape(-1, 3)
    b = b.astype(np.uint16)
    px = np.empty(w * h, np.uint16)
    px[0::2] = (b[:, 0] << 4) | (b[:, 1] >> 4)
    px[1::2] = ((b[:, 1] & 0xF) << 8) | b[:, 2]
    pad, _, _ = mk.pad_image(px.reshape(h, w), 512, 368)
    blobs = [ljenc.encode_tile(pad[y:y + 368, x:x + 512], 12, 1)
             for y in range(0, pad.shape[0], 368) for x in range(0, pad.shape[1], 512)]
    counts = [len(x) for x in blobs]
    payload = b''.join(blobs)
    fill = (-len(payload)) % 1024           # C062F95C..C062F98C
    payload += bytes(fill)
    counts[-1] += fill
    return data, payload, counts


class TrailerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='fpl-trailer-')
        cls.lib = build(cls.tmp.name)
        cls.stock, cls.payload, cls.counts = encode_stock()
        cls.header = cls.stock[:PIXELS]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def frame(self):
        """The camera's buffer after copy-back: header, then our payload over
        the start of the old raster, then the rest of the old raster."""
        buf = bytearray(self.stock)
        buf[PIXELS:PIXELS + len(self.payload)] = self.payload
        return buf

    def write(self, buf, payload=None, counts=None, capacity=None):
        counts = self.counts if counts is None else counts
        arr = (ct.c_uint32 * len(counts))(*counts)
        raw = (ct.c_uint8 * len(buf)).from_buffer(buf)
        res = (ct.c_uint32 * 9)()
        r = self.lib.fpl_fixture_build(ct.addressof(raw), len(buf),
                                       len(self.payload) if payload is None else payload,
                                       ct.addressof(arr), len(counts), 512, 368,
                                       len(buf) if capacity is None else capacity,
                                       ct.addressof(res))
        del raw
        return r, list(res)

    def test_it_is_byte_identical_to_the_proven_host_layout(self):
        import trailing_ifd
        reference, built = trailing_ifd.assemble(self.header, self.payload, self.counts, 512, 368)
        buf = self.frame()
        r, res = self.write(buf)
        self.assertEqual(r, OK)
        self.assertEqual(res[1], len(reference))
        self.assertEqual(res[2], built['ifd_at'])
        self.assertEqual(bytes(buf[:res[1]]), reference)

    def test_libraw_decodes_it_to_exactly_the_stock_samples(self):
        import rawpy
        buf = self.frame()
        r, res = self.write(buf)
        self.assertEqual(r, OK)
        ours = Path(self.tmp.name) / 'ours.DNG'
        ours.write_bytes(bytes(buf[:res[1]]))
        with rawpy.imread(str(STOCK)) as a, rawpy.imread(str(ours)) as b:
            self.assertEqual(a.raw_image.shape, b.raw_image.shape)
            self.assertTrue((a.raw_image == b.raw_image).all(), 'samples differ')
        self.assertLess(res[1], len(self.stock), 'no smaller than stock')

    def test_every_access_is_aligned_inside_and_the_root_pointer_is_written_last(self):
        buf = self.frame()
        r, res = self.write(buf)
        self.assertEqual(r, OK)
        self.assertEqual(res[4], 0, 'unaligned access')
        self.assertEqual(res[5], 0, 'access outside the buffer')
        self.assertEqual(res[7], 4, 'the root pointer was not the last write')
        # and it was not written earlier too: until the trailer is complete the
        # file must still describe its original strips
        self.assertEqual(res[8], res[6] - 3, 'the root pointer was touched before the end')

    def test_nothing_outside_the_trailer_and_the_root_pointer_changes(self):
        buf = self.frame()
        before = bytes(buf)
        r, res = self.write(buf)
        self.assertEqual(before[:4], bytes(buf[:4]))
        self.assertEqual(before[8:res[2]], bytes(buf[8:res[2]]))
        self.assertEqual(before[res[1]:], bytes(buf[res[1]:]))

    # ---- refusals: nothing written ------------------------------------
    def refused(self, buf, expect=UNSUPPORTED, **kw):
        before = bytes(buf)
        r, res = self.write(buf, **kw)
        self.assertEqual(r, expect)
        self.assertEqual(bytes(buf), before, 'a refused trailer wrote something')
        self.assertEqual(res[6], 0)

    def entry_offset(self, buf, tag):
        root, = struct.unpack_from('<I', buf, 4)
        n, = struct.unpack_from('<H', buf, root)
        for i in range(n):
            if struct.unpack_from('<H', buf, root + 2 + 12 * i)[0] == tag:
                return root + 2 + 12 * i
        raise AssertionError(tag)

    def test_a_header_it_does_not_recognise_is_refused(self):
        cases = []
        b = self.frame(); b[0:4] = b'MM\x00*'; cases.append(b)             # big-endian
        b = self.frame(); struct.pack_into('<H', b, self.entry_offset(b, 259) + 8, 7)
        cases.append(b)                                                    # compressed
        b = self.frame(); struct.pack_into('<H', b, self.entry_offset(b, 278), 277)
        cases.append(b)                                                    # no RowsPerStrip
        b = self.frame(); struct.pack_into('<H', b, self.entry_offset(b, 279), 324)
        cases.append(b)                                                    # tile tag present
        b = self.frame(); struct.pack_into('<I', b, 4, PIXELS)             # root off the end
        cases.append(b)
        b = self.frame(); struct.pack_into('<I', b, 4, 7)                  # odd root
        cases.append(b)
        b = self.frame(); root, = struct.unpack_from('<I', b, 4)
        struct.pack_into('<H', b, root, 400); cases.append(b)             # too many entries
        b = self.frame(); o = self.entry_offset(b, 273)
        b[o:o + 2], b[o + 12:o + 14] = b[o + 12:o + 14], b[o:o + 2]; cases.append(b)  # unsorted
        for i, buf in enumerate(cases):
            with self.subTest(case=i):
                self.refused(buf)

    def test_tile_sizes_that_do_not_reach_the_ifd_are_refused(self):
        for counts in (self.counts[:-1] + [self.counts[-1] - 1],
                       self.counts[:-1] + [self.counts[-1] + 1],
                       [0] + self.counts[1:]):
            with self.subTest():
                self.refused(self.frame(), INVALID, counts=counts)

    def test_a_buffer_too_small_for_the_trailer_is_refused(self):
        import trailing_ifd
        _, built = trailing_ifd.assemble(self.header, self.payload, self.counts, 512, 368)
        self.refused(self.frame(), capacity=built['file_bytes'] - 1)
        r, _ = self.write(self.frame(), capacity=built['file_bytes'])
        self.assertEqual(r, OK)


class TrailerMutationTests(unittest.TestCase):
    MUTATIONS = {
        'keeps RowsPerStrip': ('tag == TAG_STRIP_OFFSETS || tag == TAG_ROWS_PER_STRIP ||',
                               'tag == TAG_STRIP_OFFSETS ||'),
        'leaves Compression at 1': ('put_entry(read, write, at, tag, TYPE_SHORT, 1, LOSSLESS_JPEG)',
                                    'put_entry(read, write, at, tag, TYPE_SHORT, 1, 1)'),
        'swaps the tables': ('t == TAG_TILE_OFFSETS ? plan.offset_table : plan.count_table',
                             't == TAG_TILE_OFFSETS ? plan.count_table : plan.offset_table'),
        'root pointer first': (
            '    position = FPL_TRAILER_PIXELS;',
            '    wr32(read, write, file + 4, plan.ifd_at);\n    position = FPL_TRAILER_PIXELS;'),
        'trusts the tile sum': ('    if (sum != payload) return FPL_INVALID;', ''),
        'writes a word unaligned': ('    write(word, (read(word) & ~(0xffu << shift)) | ((v & 0xffu) << shift));',
                                    '    write(a, (read(word) & ~(0xffu << shift)) | ((v & 0xffu) << shift));'),
        'accepts an unsorted IFD': ('        if (n && tag <= previous) return FPL_UNSUPPORTED;',
                                    '        if (n && tag <= previous) (void)0;'),
    }

    def test_every_mutation_is_caught(self):
        text = (HERE / 'trailer.c').read_text()
        with tempfile.TemporaryDirectory(prefix='fpl-trailer-mut-') as tmp:
            for i, (name, (old, new)) in enumerate(self.MUTATIONS.items()):
                with self.subTest(mutation=name):
                    self.assertEqual(text.count(old), 1, name)
                    src = Path(tmp) / f'm{i}.c'
                    src.write_text(text.replace(old, new))
                    lib = build(tmp, src, f'm{i}.dylib')

                    class Against(TrailerTests):
                        @classmethod
                        def setUpClass(cls):
                            cls.tmp = tempfile.TemporaryDirectory(prefix='fpl-trailer-m-')
                            cls.lib = lib
                            cls.stock, cls.payload, cls.counts = SHARED
                            cls.header = cls.stock[:PIXELS]
                    result = unittest.TestResult()
                    unittest.defaultTestLoader.loadTestsFromTestCase(Against).run(result)
                    self.assertFalse(result.wasSuccessful(), f'{name} was not caught')


SHARED = None


def setUpModule():
    global SHARED
    SHARED = encode_stock()


class ArmCompileTests(unittest.TestCase):
    def test_it_builds_freestanding_for_the_camera(self):
        clang = shutil.which('clang') or 'clang'
        nm = shutil.which('llvm-nm') or shutil.which('nm') or 'nm'
        with tempfile.TemporaryDirectory(prefix='fpl-trailer-arm-') as tmp:
            out = Path(tmp) / 'trailer.o'
            subprocess.run([clang, '--target=armv7a-none-eabi', '-mcpu=cortex-a9', '-mthumb',
                            '-mfloat-abi=soft', '-mfpu=none', '-ffreestanding', '-fno-builtin',
                            '-nostdlib', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror', '-c',
                            '-I', str(HERE), str(HERE / 'trailer.c'), '-o', str(out)],
                           check=True, capture_output=True, text=True, timeout=30)
            undefined = subprocess.run([nm, '-u', str(out)], capture_output=True,
                                       text=True).stdout
            for forbidden in ('memcpy', 'memset', '__aeabi'):
                self.assertNotIn(forbidden, undefined, undefined)


if __name__ == '__main__':
    unittest.main()
