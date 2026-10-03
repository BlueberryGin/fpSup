"""ui_apply.c against the reference applier (ui/fpui.py), on the real stock
NBU region of the pinned image.

What is asserted: a block applied by the C code gives the same page bytes,
the same shared string pool and the same handed-out values as the reference;
several blocks stack in either order; the page entry is switched once, last,
and only when every op succeeded; the firmware image is never written; and a
damaged block, a page that is not stock or ours, a failed guard or a failed
allocation leave the UI exactly as it was.
"""
import ctypes as ct
import hashlib
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
FPSUP = HERE.parent
ROOT = FPSUP.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(FPSUP / 'lossless' / 'menu'))
sys.path.insert(0, str(ROOT / 'research' / 'ui' / 'tools'))
from ui import fpui                 # noqa: E402

SEG0 = ROOT / 'out/seg0_c0000000.bin'
NBU, NBU_SIZE = 0xC18C0460, 0x00800000
POOL, POOL_LEN = 0xC18C0474, 176152
ENTRIES = [('MainB1', 0x74D796), ('MainB2', 0x76FF04), ('MainY4', 0x889C60), ('B2_5', 0x19C130)]
OK, BLOCK, NO_UI, PAGE, GUARD, NO_MEMORY, STRING, OP, FULL = range(9)
OOB, ALLOCS, PUBLISHES, NBU_WRITES, ENTRY_WRITES, LAST_ENTRY, READER, ENTRY_TABLE, PLEN, PPTR, DIRTY, BYTE_READS = range(12)


def build(directory, replace=None, name='apply.dylib'):
    source = HERE / 'ui_apply.c'
    if replace:
        old, new = replace
        text = source.read_text()
        if text.count(old) != 1:
            raise AssertionError('mutation seam not found once: ' + old[:60])
        source = pathlib.Path(directory) / (name + '.c')
        source.write_text(text.replace(old, new))
    out = pathlib.Path(directory) / name
    subprocess.run([shutil.which('clang') or 'clang', '-shared', '-fPIC', '-O1', '-std=c11',
                    '-Wall', '-Wextra', '-Werror', '-DUIA_HOST_TEST', '-DUIS_HOST_TEST',
                    '-I', str(HERE), str(source), str(HERE / 'ui_pool.c'),
                    str(HERE / 'ui_apply_fixture.c'), '-o', str(out)],
                   check=True, capture_output=True, text=True, timeout=120)
    lib = ct.CDLL(str(out))
    lib.fx_reset.argtypes = [ct.c_char_p, ct.c_uint32, ct.c_char_p, ct.POINTER(ct.c_uint32), ct.c_uint32]
    lib.fx_apply.argtypes = [ct.c_char_p, ct.c_uint32, ct.POINTER(ct.c_uint32)]
    lib.fx_apply.restype = ct.c_uint32
    lib.fx_peek.argtypes = [ct.c_uint32]
    lib.fx_peek.restype = ct.c_uint32
    lib.fx_poke.argtypes = [ct.c_uint32, ct.c_uint32]
    lib.fx_read.argtypes = [ct.c_uint32, ct.c_char_p, ct.c_uint32]
    lib.fx_get.argtypes = [ct.c_uint32]
    lib.fx_get.restype = ct.c_uint32
    lib.fx_alloc_fail.argtypes = [ct.c_uint32]
    return lib


_LOSSLESS = None


def lossless():
    """(block, stock page, stock pool, builder page) -- built once."""
    global _LOSSLESS
    if _LOSSLESS is None:
        import build_fpui
        blob, info, (stock_page, pool_stock, page) = build_fpui.build()
        _LOSSLESS = (blob, stock_page, pool_stock, page)
    return _LOSSLESS


def image():
    return SEG0.read_bytes()[:0x03000000]


class Camera:
    """One host 'camera': the C library over the stock NBU region."""

    def __init__(self, lib):
        self.lib = lib
        names = b''.join(n.encode() + b'\0' for n, _ in ENTRIES)
        offs = (ct.c_uint32 * len(ENTRIES))(*[o for _, o in ENTRIES])
        img = image()
        lib.fx_reset(img, len(img), names, offs, len(ENTRIES))

    def apply(self, blob):
        out = (ct.c_uint32 * 13)()
        r = self.lib.fx_apply(blob, len(blob), out)
        return r, list(out)

    def get(self, f): return self.lib.fx_get(f)

    def read(self, a, n):
        buf = ct.create_string_buffer(n)
        self.lib.fx_read(a, buf, n)
        return buf.raw

    def entry(self, name):
        i = [n for n, _ in ENTRIES].index(name)
        return self.lib.fx_peek(self.get(ENTRY_TABLE) + 44 * i + 8)

    def page(self, name):
        """(bytes, header words) of the page the entry points at now."""
        p = (NBU + self.entry(name)) & 0xFFFFFFFF
        hdr = struct.unpack('<32I', self.read((p & ~3) - 128, 128))
        return self.read(p, hdr[3]), hdr, p

    def pool(self):
        return self.read(self.get(PPTR), self.get(PLEN))


class ApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='uia-')
        cls.lib = build(cls.tmp.name)
        cls.blob, cls.stock_page, cls.pool_stock, cls.builder_page = lossless()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.cam = Camera(self.lib)

    def reference(self, blobs):
        pages = {'MainB2': fpui.PageCopy(self.stock_page, len(self.stock_page), 1)}
        pool = fpui.Pool(self.pool_stock)
        outs = [fpui.apply(b, pages, pool) for b in blobs]
        return pages['MainB2'], pool, outs

    def assert_untouched(self):
        self.assertEqual(self.cam.entry('MainB2'), 0x76FF04)
        self.assertEqual(self.cam.get(ENTRY_WRITES), 0)
        self.assertEqual(self.cam.get(NBU_WRITES), 0)

    # ---- one block: the Lossless row --------------------------------------
    def test_the_lossless_row_matches_the_reference_byte_for_byte(self):
        r, out = self.cam.apply(self.blob)
        self.assertEqual(r, OK, out)
        page, hdr, p = self.cam.page('MainB2')
        ref, pool, _ = self.reference([self.blob])
        self.assertEqual(page, bytes(ref.data))
        self.assertEqual(self.cam.pool()[POOL_LEN:], bytes(pool.data[POOL_LEN:]))
        self.assertEqual(hdr[0], 0x47505346)
        self.assertEqual((hdr[4], hdr[5]), (len(self.stock_page), ref.next_id))
        self.assertEqual(out[2], 34494)                 # first id, as the builder chose
        self.assertEqual(out[4:6], [1, 243])            # row slot 0 -> y 243
        self.assertEqual(self.cam.get(NBU_WRITES), 0, 'the firmware image was written')
        self.assertEqual(self.cam.get(OOB), 0)

    def test_and_that_is_the_page_the_camera_tested(self):
        self.cam.apply(self.blob)
        page, _, _ = self.cam.page('MainB2')
        self.assertEqual(len(page), len(self.builder_page))
        # equal except the private string fields, which the reference already
        # proved resolve to the builder's text (rows.prove_same)
        differ = sum(1 for a, b in zip(page, self.builder_page) if a != b)
        self.assertLess(differ, 4 * 20)

    def test_the_entry_is_switched_once_and_last(self):
        self.cam.apply(self.blob)
        self.assertEqual(self.cam.get(ENTRY_WRITES), 1)
        _, _, p = self.cam.page('MainB2')
        self.assertEqual((NBU + self.cam.entry('MainB2')) & 0xFFFFFFFF, p)
        self.assertGreaterEqual(self.cam.get(PUBLISHES), 2)
        self.assertEqual(self.cam.get(DIRTY), 0, 'the page was switched in before it was published')

    def test_the_page_is_moved_a_word_at_a_time(self):
        # MainB2 is 154 KB and five insertions each move its tail: byte by byte
        # that is ~0.9 M byte reads (the camera pays ~1 us each); a word at a
        # time, only the unaligned edges and the block itself go byte-wise.
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        self.assertLess(self.cam.get(BYTE_READS), 120000, self.cam.get(BYTE_READS))

    # ---- several blocks ----------------------------------------------------
    def test_two_rows_stack_like_the_reference(self):
        for _ in range(2):
            r, out = self.cam.apply(self.blob)
            self.assertEqual(r, OK)
        page, hdr, _ = self.cam.page('MainB2')
        ref, pool, outs = self.reference([self.blob, self.blob])
        self.assertEqual(page, bytes(ref.data))
        self.assertEqual(out[2], 34494 + 23)            # second block: the next ids
        self.assertEqual(out[4:6], [1, 243 + 81])       # and the next row
        self.assertEqual(hdr[6], len(ref.deltas))
        self.assertEqual(self.cam.get(ENTRY_WRITES), 2)

    def test_two_rows_make_a_consistent_page(self):
        import native_ui_audit as A
        for _ in range(2):
            self.cam.apply(self.blob)
        page, _, _ = self.cam.page('MainB2')
        pool = self.cam.pool()

        def resolve(off):
            return pool[off:pool.index(b'\0', off)].decode()
        recs = A.records(page, 0, len(page))
        objects = A.parse_objects(recs, resolve)
        from collections import Counter
        kids = Counter(o['parent_id'] for o in objects.values())
        self.assertTrue(all(kids[o['id']] == o['child_count'] for o in objects.values()))
        head = A.parse_header(recs[0], resolve)
        self.assertEqual(head['objects'], len(objects))
        mc = [r for r in recs if r.tag == 0x1000B and len(r.data) == 126 + 16]
        self.assertEqual(len(mc), 1, 'ModeChange did not take both roots')
        self.assertEqual(len(A.parse_mode_change(mc[0])), 8)

    # ---- nothing changes when something is wrong --------------------------
    def test_a_damaged_block_changes_nothing(self):
        bad = bytearray(self.blob)
        bad[0x400] ^= 1
        self.assertEqual(self.cam.apply(bytes(bad))[0], BLOCK)
        self.assert_untouched()

    def test_a_failed_guard_changes_nothing(self):
        # the stock page's ModeChange tag, as if another tool had moved records
        self.lib.fx_poke(0xC20318F8, 0)          # bytes 1..3 of its tag
        self.assertEqual(self.cam.apply(self.blob)[0], GUARD)
        self.assertEqual(self.cam.get(ENTRY_WRITES), 0)

    def test_a_page_nobody_announced_is_left_alone(self):
        i = [n for n, _ in ENTRIES].index('MainB2')
        self.lib.fx_poke(self.cam.get(ENTRY_TABLE) + 44 * i + 8, 0x76FF00)
        self.assertEqual(self.cam.apply(self.blob)[0], PAGE)
        self.assertEqual(self.cam.get(ENTRY_WRITES), 0)

    def test_no_memory_changes_nothing(self):
        self.lib.fx_alloc_fail(1)
        self.assertEqual(self.cam.apply(self.blob)[0], NO_MEMORY)
        self.assert_untouched()

    def test_a_second_block_that_fails_leaves_the_first_in_place(self):
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        first = self.cam.page('MainB2')[0]
        self.lib.fx_alloc_fail(self.cam.get(ALLOCS) + 1)
        self.assertEqual(self.cam.apply(self.blob)[0], NO_MEMORY)
        self.assertEqual(self.cam.page('MainB2')[0], first)
        self.assertEqual(self.cam.get(ENTRY_WRITES), 1)

    def test_no_page_entry_no_change(self):
        blob = self.blob.replace(b'MainB2\0', b'MainX2\0', 1)
        blob = fix_fnv(blob)
        self.assertEqual(self.cam.apply(blob)[0], NO_UI)
        self.assert_untouched()


def fix_fnv(blob):
    b = bytearray(blob)
    struct.pack_into('<I', b, 12, fpui.fnv(bytes(b[0x20:])))
    return bytes(b)


class ReferenceTests(unittest.TestCase):
    """The reference applier's own rules (no C involved)."""

    def test_insertions_at_one_position_stack_in_order(self):
        pages = {'P': fpui.PageCopy(bytes(16), 16, 1)}
        for tag in (b'AAAA', b'BBBB'):
            blk = fpui.Block()
            blk.op(fpui.OP_PAGE, blk.string('P', fpui.NAME), 0, 16, 1, 0, 4, 1)
            blk.op(fpui.OP_INSERT, 8, blk.fragment(tag), 4)
            blk.op(fpui.OP_DONE)
            fpui.apply(blk.encode(), pages, fpui.Pool(b'x\0'))
        self.assertEqual(bytes(pages['P'].data)[8:16], b'AAAABBBB')
        self.assertEqual(pages['P'].current(8), 16)

    def test_floats_hold_integers_only(self):
        self.assertEqual(fpui.f32_to_int(fpui.int_to_f32(243)), 243)
        with self.assertRaises(fpui.FpuiError):
            fpui.f32_to_int(0x3FC00000)                # 1.5


class ApplyMutationTests(unittest.TestCase):
    MUTATIONS = {
        'switches before the copy is published': (
            '    publish();\n    for (i = 0; i < n_files; ++i) {\n        uint32_t cnt = peek(table + 8), at = cnt;',
            '    for (i = 0; i < n_files; ++i) {\n        uint32_t cnt = peek(table + 8), at = cnt;'),
        'inserts before earlier insertions at a position': (
            '        if (peek(w->deltas + 8u * i) <= pos) c += peek(w->deltas + 8u * i + 4u);',
            '        if (peek(w->deltas + 8u * i) < pos) c += peek(w->deltas + 8u * i + 4u);'),
        'ignores guards': ('            else if (be(w.page + at) != args[1]) r = UIA_GUARD;',
                           '            else if (be(w.page + at) != args[1] && 0) r = UIA_GUARD;'),
        'skips the checksum': ('fnv(b + 0x20, total - 0x20) != le(b + 12) ||',
                               'fnv(b + 0x20, total - 0x20) != le(b + 12) * 0 + fnv(b + 0x20, total - 0x20) ||'),
        'forgets earlier copies': (
            '    if (old_n) move(w->deltas, peek(hdr + 32), 8u * old_n);', '    old_n = 0;'),
        'reuses object ids': ('        if (old_next < a[3]) old_next = a[3];', '        old_next = a[3];'),
        'counters restart': ('            w->counters[i][1] = peek(hdr + 40 + 8 * i);',
                             '            w->counters[i][1] = 0;'),
        'backward word move off by one word': ('            for (; n >= 4u; n -= 4u) poke(to + n - 4u, peek(from + n - 4u));',
                                               '            for (; n >= 4u; n -= 4u) poke(to + n - 4u, peek(from + n));'),
        'copy at another alignment than its source': (
            '    w->page = block + 8u * deltas_cap + UIA_HEADER + (old & 3u);   /* the source\'s alignment */',
            '    w->page = block + 8u * deltas_cap + UIA_HEADER + ((old + 1u) & 3u);'),
        'edits the live page': ('    move(w->page, old, old_len);',
                                '    move(w->page, old, old_len); w->page = old;'),
    }

    def test_every_mutation_is_caught(self):
        blob, stock_page, pool_stock, page = lossless()
        with tempfile.TemporaryDirectory(prefix='uia-mut-') as tmp:
            for index, (name, seam) in enumerate(self.MUTATIONS.items()):
                with self.subTest(mutation=name):
                    lib = build(tmp, seam, f'm{index}.dylib')

                    class Against(ApplyTests):
                        @classmethod
                        def setUpClass(cls):
                            cls.lib = lib
                            cls.blob, cls.stock_page, cls.pool_stock, cls.builder_page = blob, stock_page, pool_stock, page

                        @classmethod
                        def tearDownClass(cls):
                            pass
                    result = unittest.TestResult()
                    unittest.defaultTestLoader.loadTestsFromTestCase(Against).run(result)
                    self.assertFalse(result.wasSuccessful(), f'mutation survived: {name}')


if __name__ == '__main__':
    unittest.main()
