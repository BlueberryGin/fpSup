"""ui_apply.c against the reference applier (ui/fpui.py), on the real stock
NBU region of the pinned image.

What is asserted: a block applied by the C code gives the same page bytes,
the same string layers (NESTED_HOOKS.md) and the same handed-out values as the
reference; the stock pool is never copied, scanned or repointed;
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
from ui import fpui, chain          # noqa: E402

SEG0 = ROOT / 'out/seg0_c0000000.bin'
NBU, NBU_SIZE = 0xC18C0460, 0x00800000
POOL, POOL_LEN = 0xC18C0474, 176152
ENTRIES = [('MainB1', 0x74D796), ('MainB2', 0x76FF04), ('MainY4', 0x889C60), ('B2_5', 0x19C130)]
OK, BLOCK, NO_UI, PAGE, GUARD, NO_MEMORY, STRING, OP, FULL = range(9)
OOB, ALLOCS, PUBLISHES, NBU_WRITES, ENTRY_WRITES, LAST_ENTRY, READER, ENTRY_TABLE, PLEN, PPTR, DIRTY, BYTE_READS, ICACHES = range(13)


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
    lib.fx_loader.argtypes = [ct.c_uint32, ct.POINTER(ct.c_uint32), ct.c_uint32]
    lib.fx_claim_res.argtypes = [ct.c_uint32, ct.c_uint32]
    lib.fx_holder_skips_res.argtypes = [ct.c_uint32]
    lib.fx_fixed.argtypes = [ct.c_uint32] * 5
    lib.fx_fixed.restype = ct.c_uint32
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

    def peek(self, a):
        return self.lib.fx_peek(a & 0xFFFFFFFF)

    def layers(self):
        return chain.layers(self.peek)

    def text(self, offset):
        return chain.resolve(self.peek, offset, self.get(READER))

    def pool_untouched(self):
        return (self.get(PPTR), self.get(PLEN)) == (POOL, POOL_LEN)


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
        pool = fpui.Strings(self.pool_stock)
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
        self.assertEqual(self.cam.layers(), pool.layers)
        self.assertTrue(self.cam.pool_untouched(), 'the stock pool was replaced')
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
        self.assertEqual(self.cam.layers(), pool.layers)

    def test_a_block_without_strings_is_published_before_the_switch(self):
        blk = fpui.Block()
        n = len(self.stock_page)
        blk.op(fpui.OP_PAGE, blk.string('MainB2', fpui.NAME), 0x76FF04, n, 1, 0, 4, 1)
        blk.op(fpui.OP_INSERT, n, blk.fragment(b'ABCD'), 4)
        blk.op(fpui.OP_DONE)
        self.assertEqual(self.cam.apply(blk.encode())[0], OK)
        self.assertEqual(self.cam.layers(), [])
        self.assertEqual(self.cam.get(13), 0, 'a string site was touched')
        self.assertEqual(self.cam.get(DIRTY), 0, 'the page was switched in before it was published')

    # ---- strings: nested layers, no pool copy ------------------------------
    def test_private_strings_get_a_layer_and_the_pool_is_left_alone(self):
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        (base, texts), = self.cam.layers()
        self.assertEqual(base, fpui.LAYER_FIRST)
        self.assertEqual(texts, ['fpLossless_Row_GATED', 'MV_fpLossless', 'EXCL_fpLossless',
                                 'SUB_MV_fpLossless', 'Lossless RAW'])
        self.assertTrue(self.cam.pool_untouched())
        for i, s in enumerate(texts):
            self.assertEqual(self.cam.text(base + i), s)
        self.assertEqual(self.cam.text(0x12BC4), 'Footer05')       # a stock string, hinted
        self.assertIsNone(self.cam.text(base + len(texts)))
        self.assertIsNone(self.cam.text(0xFFFFFFFF))

    def test_the_pool_is_never_scanned(self):
        # 176 KB a string would be ~0.9 M byte reads for the five private strings
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        self.assertLess(self.cam.get(BYTE_READS), 120000)

    def test_a_second_block_layers_on_the_first(self):
        for _ in range(2):
            self.assertEqual(self.cam.apply(self.blob)[0], OK)
        (b0, t0), (b1, t1) = self.cam.layers()
        self.assertEqual((b0, b1), (fpui.LAYER_FIRST, fpui.LAYER_FIRST + 5))
        self.assertEqual(t0, t1)
        self.assertEqual(self.cam.text(b1 + 4), 'Lossless RAW')

    # ---- whose layer: Loader v3's books (header v2, 2026-10-06) ------------
    PATHS = 0x10008000                  # past the fixture's NAMES, below BLOCK

    def loader(self, me, *holders):
        """A running Loader v3 in the model: `me` is the sup whose entry runs,
        `holders` the files the books list on the string sites."""
        addr = {}
        for i, n in enumerate(dict.fromkeys((me,) + holders)):
            a = addr[n] = self.PATHS + 0x40 * i
            raw = ('\\fpSup\\' + n).encode() + b'\0'
            raw += b'\0' * (-len(raw) % 4)
            for k in range(0, len(raw), 4):
                self.lib.fx_poke(a + k, struct.unpack_from('<I', raw, k)[0])
        hs = (ct.c_uint32 * 8)(*[addr[h] for h in holders])
        self.lib.fx_loader(addr[me], hs, len(holders))

    def names(self):
        out, layer = [], chain.outer(self.cam.peek)
        while layer is not None:
            out.append(chain.name(self.cam.peek, layer))
            layer = chain.inner(self.cam.peek, layer)
        return out[::-1]

    def test_without_a_loader_a_layer_is_named_fsdl(self):
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        self.assertEqual(self.names(), ['FSDL'])

    def test_with_a_loader_a_layer_carries_its_file_name(self):
        self.loader('10LOSS.BIN', '10LOSS.BIN')
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        self.loader('31FMT.BIN', '10LOSS.BIN', '31FMT.BIN')
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        self.assertEqual(self.names(), ['10LOSS', '31FMT'])
        self.assertEqual(self.cam.text(fpui.LAYER_FIRST + 5 + 4), 'Lossless RAW')

    def test_a_layer_whose_sup_is_not_in_the_books_is_refused(self):
        self.loader('10LOSS.BIN', '10LOSS.BIN')
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        before = self.cam.get(13)
        self.loader('31FMT.BIN', '31FMT.BIN')               # 10LOSS is not a holder
        self.assertEqual(self.cam.apply(self.blob)[0], STRING)
        self.assertEqual(self.cam.get(13), before, 'a site was rewritten')
        self.assertEqual(self.names(), ['10LOSS'])

    def test_a_layer_counts_only_by_a_name_in_the_books(self):
        self.assertEqual(self.cam.apply(self.blob)[0], OK)   # no loader: "FSDL"
        self.loader('40X.BIN', '40X.BIN')                    # a loader's books lack it
        self.assertEqual(self.cam.apply(self.blob)[0], STRING)
        self.loader('31FMT.BIN', '31FMT.BIN', 'FSDL.BIN')    # a file of that name holds it
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        self.assertEqual(self.names(), ['FSDL', '31FMT'])
        self.loader('31FMTAB1.BIN', '31FMTAB2.BIN')          # the second word counts too
        self.cam = Camera(self.lib)
        self.loader('31FMTAB2.BIN', '31FMTAB2.BIN')
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        self.loader('40Y.BIN', '31FMTAB1.BIN', '40Y.BIN')
        self.assertEqual(self.cam.apply(self.blob)[0], STRING)

    def fixed(self, base=0xFFFFFFD0, count=39):
        table, lo = 0x10008800, 0x10008C00
        for i in range(count):
            self.lib.fx_poke(table + 4 * i, lo + 16 * (i % 8))
        for i in range(8):
            for k, w in enumerate(struct.unpack('<4I', ('fixed%02d' % i).encode().ljust(16, b'\0'))):
                self.lib.fx_poke(lo + 16 * i + 4 * k, w)
        return self.lib.fx_fixed(table, count, base, lo, lo + 128)

    def test_a_fixed_layer_answers_its_ids_and_the_next_layers_skip_it(self):
        self.loader('10LOSS.BIN', '10LOSS.BIN')
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        self.loader('31FMT.BIN', '10LOSS.BIN', '31FMT.BIN')
        self.assertEqual(self.fixed(), OK)
        self.loader('50X.BIN', '10LOSS.BIN', '31FMT.BIN', '50X.BIN')
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        self.assertEqual(self.names(), ['10LOSS', '31FMT', '50X'])
        bases = [b for b, _ in self.cam.layers()]
        self.assertEqual(bases, [fpui.LAYER_FIRST, 0xFFFFFFD0, fpui.LAYER_FIRST + 5])
        self.assertEqual(self.cam.text(0xFFFFFFD0 + 9), 'fixed01')
        self.assertEqual(self.cam.text(fpui.LAYER_FIRST + 5 + 4), 'Lossless RAW')
        self.assertEqual(self.cam.text(0xFFFFFFF7), None)    # one past the fixed range

    def test_a_fixed_layer_refuses_a_bad_range(self):
        self.loader('31FMT.BIN', '31FMT.BIN')
        self.assertEqual(self.fixed(base=0x40000000), 4)     # below UIS_LAYER_FIXED
        self.assertEqual(self.fixed(base=0xFFFFFFF0, count=16), 4)   # would reach FFFFFFFF
        self.assertEqual(self.fixed(), OK)
        self.loader('32FMT.BIN', '31FMT.BIN', '32FMT.BIN')
        self.assertEqual(self.fixed(base=0xFFFFFFE0, count=4), 5)    # overlaps: UIS_HOOK
        self.assertEqual(len(self.cam.layers()), 1)

    def test_a_site_holding_someone_elses_code_refuses_the_strings(self):
        self.lib.fx_poke(0xC05E5B58, 0xBF00BF00)       # not stock, not a layer
        self.assertEqual(self.cam.apply(self.blob)[0], STRING)
        self.assert_untouched()
        self.assertEqual(self.cam.peek(0xC05E61C8), 0x428A6942)
        self.assertEqual(self.cam.peek(0xC05E61E0), 0x69406902)

    def test_strbase_hands_a_sup_its_offsets_at_commit(self):
        blk = fpui.Block()
        a = blk.string('OG3K_QS', fpui.NOT_STOCK)
        b = blk.string('Footer05', 0x12BC4)                     # stock: its stock offset
        blk.op(fpui.OP_STRBASE, b, 0xC072EF84)
        blk.op(fpui.OP_STRBASE, a, 0xC072EF80)
        r, out = self.cam.apply(blk.encode())
        self.assertEqual(r, OK, out)
        self.assertEqual((self.cam.peek(0xC072EF80), self.cam.peek(0xC072EF84)),
                         (fpui.LAYER_FIRST, 0x12BC4))
        self.assertEqual(self.cam.text(self.cam.peek(0xC072EF80)), 'OG3K_QS')
        ref = fpui.apply(blk.encode(), {}, fpui.Strings(self.pool_stock))
        self.assertEqual(ref['strbase'], [(0xC072EF84, 0x12BC4), (0xC072EF80, fpui.LAYER_FIRST)])

    def test_a_failed_block_hands_out_nothing(self):
        blk = fpui.Block()
        blk.op(fpui.OP_STRBASE, blk.string('OG3K_QS', fpui.NOT_STOCK), 0xC072EF80)
        blk.op(fpui.OP_EXPECT, 0, 1)                            # no slot 0: refused
        self.assertNotEqual(self.cam.apply(blk.encode())[0], OK)
        self.assertEqual(self.cam.peek(0xC072EF80), 0)
        self.assertEqual(self.cam.layers(), [])

    def test_a_veneer_to_something_without_a_layer_header_is_refused(self):
        # all three sites agree on one "layer" -- but nothing announces it
        fake, cave = 0x10007000, 0xC072EF00
        # a whole v2 header, but a name nobody answers to ("XXXX", not "FSDL")
        for k, v in enumerate((0x58585858, 0, chain.VERSION, fpui.LAYER_FIRST, 3)):
            self.lib.fx_poke(fake + 4 * k, v)
        for i, (site, _, entry) in enumerate(chain.SITES.values()):
            v = cave + 8 * i
            self.lib.fx_poke(v, chain.THUMB_VENEER)
            self.lib.fx_poke(v + 4, fake + entry)
            self.lib.fx_poke(site, bw(site, v))
        self.assertEqual(self.cam.apply(self.blob)[0], STRING)
        self.assertEqual(self.cam.get(ENTRY_WRITES), 0)
        self.assertEqual(self.cam.get(13), 0, 'a site was rewritten')     # fx_poke is not logged

    def test_sites_that_disagree_refuse_the_strings(self):
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        self.lib.fx_poke(0xC05E61E0, 0x69406902)       # one site back to stock
        self.assertEqual(self.cam.apply(self.blob)[0], STRING)
        self.assertEqual(self.cam.get(ENTRY_WRITES), 1)

    def test_the_sites_are_hung_last_checks_before_resolve(self):
        icaches = self.cam.get(ICACHES)
        self.assertEqual(self.cam.apply(self.blob)[0], OK)
        n = self.cam.get(13)
        self.assertEqual([self.cam.get(20 + i) for i in range(n)],
                         [0xC05E61C8, 0xC05E61E0, 0xC05E5B58])
        self.assertEqual([self.cam.get(30 + i) for i in range(n)], [0, 0, 0],
                         'a site went live before the layer was published')
        self.assertGreaterEqual(self.cam.get(ICACHES) - icaches, 3)

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


def bw(site, to):
    """Thumb B.W at site to `to` (ui_pool.c bw_word)."""
    off = (to - (site + 4)) & 0xFFFFFFFF
    s, i1, i2 = off >> 31, (off >> 23) & 1, (off >> 22) & 1
    j1, j2 = (1 ^ i1) ^ s, (1 ^ i2) ^ s
    hw1 = 0xF000 | s << 10 | ((off >> 12) & 0x3FF)
    hw2 = 0x9000 | j1 << 13 | j2 << 11 | ((off >> 1) & 0x7FF)
    return hw1 | hw2 << 16


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
            fpui.apply(blk.encode(), pages, fpui.Strings(b'x\0'))
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
