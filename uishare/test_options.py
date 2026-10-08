"""add_option (ui/options.py), the file-redirect handler (fv_handler.S) and
ui_apply.c's file ops, on the pinned image.

  - each recipe ALONE gives what its sup does today: raw-view's three COLOR
    CSVs, its icon and its nine page words; OpenGate's resolution row, List
    max and summary text;
  - ui_apply.c gives byte for byte what the reference applier gives, for each
    recipe, for all of them together with the Lossless row, in either order;
  - the site gets a B.W to a cave veneer that enters the handler, and a site
    holding anything else is left alone;
  - a second sup that needs the same index is refused and changes nothing;
  - the handler, executed: a listed address becomes its copy and size, any
    other passes, the stack and the return are the firmware's.
"""
import ctypes as ct
import pathlib
import struct
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'projects' / 'rawview' / 'build'))
from ui import fpui, options as O         # noqa: E402
import test_ui_apply as TA                 # noqa: E402

SETS = ['MainB2', 'ColorButtonMenu', 'ColorModeAdvancedSettings', 'B2_5']
OK, BLOCK, NO_UI, PAGE, GUARD, NO_MEMORY, STRING, OP, FULL, HOOK, CONFLICT = range(11)


def raw_icon():
    import rv_uidata as U
    return U.ICONS[0][3].read_bytes()


def csv_table(data):
    lines = [l for l in data.replace(b'\xef\xbb\xbf', b'').replace(b'\r', b'').split(b'\n') if l]
    head = lines[0].split(b',')
    return [dict(zip(head, l.split(b','))) for l in lines[1:]]


class Camera(TA.Camera):
    """The C applier over the whole image, with the resource sets' entries."""

    def __init__(self, lib):
        self.lib = lib
        names = b''.join(n.encode() + b'\0' for n in SETS)
        offs = (ct.c_uint32 * len(SETS))(*[O.resource_set(n)[1] for n in SETS])
        img = TA.image()
        lib.fx_reset(img, len(img), names, offs, len(SETS))

    def entry_of(self, name):
        return self.lib.fx_peek(self.get(TA.ENTRY_TABLE) + 44 * SETS.index(name) + 8)

    def page_of(self, name):
        p = (TA.NBU + self.entry_of(name)) & 0xFFFFFFFF
        if self.lib.fx_peek((p & ~3) - 128) != 0x47505346:
            return None
        return self.read(p, self.lib.fx_peek((p & ~3) - 128 + 12))

    def table(self):
        """{stock: bytes} from the live table, found through the site."""
        w = self.lib.fx_peek(O.FV_SITE)
        if w == O.FV_STOCK:
            return None
        hw1, hw2 = w & 0xFFFF, w >> 16
        s, j1, j2 = (hw1 >> 10) & 1, (hw2 >> 13) & 1, (hw2 >> 11) & 1
        i1, i2 = 1 ^ (j1 ^ s), 1 ^ (j2 ^ s)
        off = s << 24 | i1 << 23 | i2 << 22 | (hw1 & 0x3FF) << 12 | (hw2 & 0x7FF) << 1
        if s:
            off -= 1 << 25
        veneer = O.FV_SITE + 4 + off
        assert self.lib.fx_peek(veneer) == O.THUMB_VENEER
        entry = self.lib.fx_peek(veneer + 4)
        code, e, tw = O.handler()
        table = self.lib.fx_peek(entry - e + tw)
        n = self.lib.fx_peek(table + 8)
        out = {}
        for i in range(n):
            stock, copy, size = (self.lib.fx_peek(table + 16 + 12 * i + 4 * k) for k in range(3))
            # a size no file has is a broken table, not something to read
            out[stock] = self.read(copy, size) if size <= 0x100000 else ('broken', copy, size)
        return out


def reference(blocks):
    return O.run(blocks)


class OptionsAloneTests(unittest.TestCase):
    """Each recipe against what its sup does today (reference applier)."""

    def test_raw_view_alone_is_raw_view(self):
        import rv_uidata as U
        pages, files, pool, res = reference([O.color_raw(raw_icon()).encode()])
        self.assertEqual(res[0]['slots'][0], 16)
        for (name, addr, *_), blob in zip(O.COLOR_CSV, [x[2] for x in U.blobs()]):
            self.assertEqual(csv_table(files.table[addr]), csv_table(blob), name)
        self.assertEqual(files.table[O.RAW_ICON[1]], raw_icon())
        img = O.image()
        for name in ('ColorButtonMenu',):         # the AEL word is raw-view's own, in place
            a, _, n = O.resource_set(name)
            want = bytearray(O.at(img, a, n))
            for addr, old, new in U.PAGE:
                if a <= addr < a + n:
                    struct.pack_into('<I', want, addr - a, new)
            self.assertEqual(bytes(pages[name].data), bytes(want), name)

    def test_opengate_alone_is_opengate(self):
        # today's OpenGate: v0.2.7a, the last that wrote the list itself
        rel = ROOT / 'fpSup' / 'releases' / 'fpsup-og3k-v0.2.7a' / 'fpSup.BIN'
        b = rel.read_bytes()
        _, c, _, _ = struct.unpack_from('<4sIII', b)
        off, sec = 16 + 8 * c, {}
        for i in range(c):
            d, l = struct.unpack_from('<II', b, 16 + 8 * i)
            sec[d] = b[off:off + l]
            off += l + (-l % 4)
        pages, files, pool, res = reference([O.resolution('OG3K 3008x2000', 'OG3K').encode()])
        self.assertEqual(res[0]['slots'][0], 2)
        ours, theirs = csv_table(files.table[O.RES_CSV[1]]), csv_table(sec[0xC0F8E7EC].rstrip(b'\0'))
        keys = (b'NO', b'TEXT', b'IMAGE', b'Enabled', b'Enabled2')
        self.assertEqual([[r.get(k) for k in keys] for r in ours], [[r.get(k) for k in keys] for r in theirs])
        a, _, n = O.resource_set('B2_5')
        pg = bytes(pages['B2_5'].data)
        self.assertEqual(pg[O.RES_LIST_MAX - a:O.RES_LIST_MAX - a + 4], sec[0xC1A709BC])
        w = struct.unpack('>I', pg[O.RES_SUMMARY[2] - a:O.RES_SUMMARY[2] - a + 4])[0]
        self.assertEqual(pool.text(w), 'OG3K')

    def test_a_second_sup_wanting_the_same_index_is_refused(self):
        pages, files, pool, _ = reference([O.color_raw(raw_icon()).encode()])
        with self.assertRaises(fpui.FpuiError):
            fpui.apply(O.color_raw(raw_icon()).encode(), pages, pool, files)

    def test_a_third_option_without_an_index_need_gets_the_next_row(self):
        og = O.resolution('OG3K 3008x2000', 'OG3K').encode()
        blk = fpui.Block()
        O.hook_fv(blk)
        O.csv_file(blk, *O.RES_CSV[1:], ['{N},TEST,Popup,,1,2'])
        a, off, n = O.resource_set('B2_5')
        O.page_ops(blk, O.image(), 'B2_5', [(fpui.OP_ADDF, O.RES_LIST_MAX, 1)])
        pages, files, pool, res = reference([og, blk.encode()])
        self.assertEqual(res[1]['slots'][0], 3)
        rows = csv_table(files.table[O.RES_CSV[1]])
        self.assertEqual([r[b'NO'] for r in rows], [b'1', b'2', b'3', b'4'])
        pg = bytes(pages['B2_5'].data)
        self.assertEqual(pg[O.RES_LIST_MAX - a:O.RES_LIST_MAX - a + 4], struct.pack('>f', 3.0))


class OptionsApplyTests(unittest.TestCase):
    """ui_apply.c against the reference, with files."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='uio-')
        cls.lib = TA.build(cls.tmp.name)
        cls.lossless = TA.lossless()[0]
        cls.color = O.color_raw(raw_icon()).encode()
        cls.res = O.resolution('OG3K 3008x2000', 'OG3K').encode()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.cam = Camera(self.lib)

    def same_as_reference(self, blocks):
        for b in blocks:
            r, out = self.cam.apply(b)
            self.assertEqual(r, OK, out)
        pages, files, pool, _ = reference(blocks)
        for name in SETS:
            got = self.cam.page_of(name)
            if name in {n for b in blocks for n in names_in(b)}:
                self.assertEqual(got, bytes(pages[name].data), name)
        self.assertEqual(self.cam.table(), files.table)
        self.assertEqual(self.cam.layers(), pool.layers)
        self.assertTrue(self.cam.pool_untouched(), 'the stock pool was replaced')
        self.assertEqual(self.cam.get(TA.NBU_WRITES), 0, 'the firmware image was written')
        self.assertEqual(self.cam.get(TA.DIRTY), 0, 'switched in before published')

    def test_raw_view_alone(self):
        self.same_as_reference([self.color])

    def test_opengate_alone(self):
        self.same_as_reference([self.res])

    def test_three_sups_on_one_card_either_order(self):
        for order in ([self.lossless, self.color, self.res], [self.res, self.color, self.lossless]):
            with self.subTest(order=[len(b) for b in order]):
                self.setUp()
                self.same_as_reference(order)

    def test_the_site_gets_a_bw_to_a_cave_veneer_once(self):
        self.cam.apply(self.color)
        bump = self.lib.fx_peek(O.CAVE_BUMP)
        self.assertEqual(bump, O.CAVE_BUMP + 4 + 8, 'one veneer, claimed from the bump')
        veneer = O.CAVE_BUMP + 4
        self.assertEqual(self.lib.fx_peek(veneer), O.THUMB_VENEER)
        off = veneer - (O.FV_SITE + 4)
        s, i1, i2 = off >> 31 & 1, off >> 23 & 1, off >> 22 & 1
        hw1 = 0xF000 | s << 10 | (off >> 12) & 0x3FF
        hw2 = 0x9000 | ((1 ^ i1) ^ s) << 13 | ((1 ^ i2) ^ s) << 11 | (off >> 1) & 0x7FF
        self.assertEqual(self.lib.fx_peek(O.FV_SITE), hw1 | hw2 << 16)
        site = self.lib.fx_peek(O.FV_SITE)
        self.cam.apply(self.res)                       # the second finds it: no new FV veneer,
        self.assertEqual(self.lib.fx_peek(O.FV_SITE), site)
        self.assertEqual(self.lib.fx_peek(O.CAVE_BUMP), bump + 24)    # only its string layer's three

    def test_a_site_holding_something_else_is_left_alone(self):
        self.lib.fx_poke(O.FV_SITE, 0xBA86F148)       # an older raw-view's own hook
        r, out = self.cam.apply(self.color)
        self.assertEqual(r, HOOK)
        self.assertEqual(self.lib.fx_peek(O.FV_SITE), 0xBA86F148)
        self.assertIsNone(self.cam.page_of('ColorButtonMenu'))

    def test_a_refused_second_block_changes_nothing(self):
        self.assertEqual(self.cam.apply(self.color)[0], OK)
        before = (self.cam.table(), self.cam.page_of('ColorButtonMenu'))
        r, out = self.cam.apply(self.color)
        self.assertEqual(r, GUARD)                     # wants index 16, would get 17
        self.assertEqual((self.cam.table(), self.cam.page_of('ColorButtonMenu')), before)

    def test_a_whole_file_replaced_twice_is_a_conflict(self):
        blk = fpui.Block()
        O.hook_fv(blk)
        name, addr, size = O.RAW_ICON
        blk.op(fpui.OP_FILE, addr, size, 1024)
        blk.op(fpui.OP_FILE_SET, blk.fragment(b'XC\0\0' + b'\0' * 60), 64)
        blk.op(fpui.OP_FILE_DONE)
        self.assertEqual(self.cam.apply(self.color)[0], OK)
        self.assertEqual(self.cam.apply(blk.encode())[0], CONFLICT)


def names_in(blob):
    strings, ops, _ = fpui.decode(blob)
    return {strings[a[0]][0] for code, a in ops if code == fpui.OP_PAGE}


class HandlerTests(unittest.TestCase):
    """fv_handler.S executed in unicorn, entered the way the veneer enters it."""

    def run_handler(self, r0, r1, table_entries):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM
        from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R5,
                                       UC_ARM_REG_SP, UC_ARM_REG_PC, UC_ARM_REG_CPSR)
        code, entry, tw = O.handler()
        mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        BLOCK, TABLE, VIEW, STACK = 0x45000000, 0x45001000, 0x45002000, 0x45010000
        mu.mem_map(0x45000000, 0x20000)
        mu.mem_map(0xC05E5000, 0x1000)                 # the return lands here
        mu.mem_write(BLOCK, code)
        mu.mem_write(BLOCK + tw, struct.pack('<I', TABLE))
        mu.mem_write(TABLE, struct.pack('<4I', 0x56465346, 1, len(table_entries), 8))
        for i, e in enumerate(table_entries):
            mu.mem_write(TABLE + 16 + 12 * i, struct.pack('<3I', *e))
        mu.mem_write(STACK, struct.pack('<I', 0x1234))   # [sp] = the stock size
        mu.reg_write(UC_ARM_REG_R0, r0)
        mu.reg_write(UC_ARM_REG_R1, r1)
        mu.reg_write(UC_ARM_REG_R5, VIEW)
        mu.reg_write(UC_ARM_REG_SP, STACK)
        mu.emu_start(BLOCK + entry, 0xC05E5BF0, count=200)
        thumb = mu.reg_read(UC_ARM_REG_CPSR) >> 5 & 1
        landed = [mu.reg_read(UC_ARM_REG_PC)] if thumb else []
        view = struct.unpack('<I', mu.mem_read(VIEW + 4, 4))[0]
        size = struct.unpack('<I', mu.mem_read(STACK, 4))[0]
        return view, size, mu.reg_read(UC_ARM_REG_SP), landed, mu.reg_read(UC_ARM_REG_PC)

    def test_a_listed_file_becomes_its_copy(self):
        view, size, sp, landed, pc = self.run_handler(0x342EC, 0xC0D22400,
                                                      [(0xC0BBBB00, 1, 2), (0xC0D566EC, 0x45008000, 459)])
        self.assertEqual((view, size, sp), (0x45008000, 459, 0x45010000))
        self.assertEqual(landed, [0xC05E5BF0], 'did not return to Thumb C05E5BF0')

    def test_any_other_file_passes_untouched(self):
        view, size, sp, landed, _ = self.run_handler(0x100, 0xC0D22400, [(0xC0D566EC, 0x45008000, 459)])
        self.assertEqual((view, size, sp), (0xC0D22500, 0x1234, 0x45010000))
        self.assertEqual(landed, [0xC05E5BF0])

    def test_an_empty_table_passes(self):
        view, size, *_ = self.run_handler(0x342EC, 0xC0D22400, [])
        self.assertEqual((view, size), (0xC0D566EC, 0x1234))


class OptionsMutationTests(unittest.TestCase):
    C_MUTATIONS = {
        'new row numbered from zero': ('    if (!(n = expand(tpl, tn, rows + 1u, line, sizeof line))) return UIA_OP;',
                                       '    if (!(n = expand(tpl, tn, rows, line, sizeof line))) return UIA_OP;'),
        'cell edits the wrong row': ("                row = args[0] == 0u ? 1u : args[0] == 1u ? f.rows_before : 0u;",
                                     "                row = args[0] == 0u ? 2u : args[0] == 1u ? f.rows_before : 0u;"),
        'P is N': ("if (k == 'N') v = n; else if (k == 'P') v = n - 1u;",
                   "if (k == 'N') v = n; else if (k == 'P') v = n;"),
        'no line ending after the row': (
            '    if (!file_append(f, line, n) || !(crlf ? file_append(f, "\\r\\n", 2) : file_append(f, "\\n", 1)))',
            '    if (!file_append(f, line, n))'),
        'appends to the stock file, not the copy': ('            table_find(table, args[0], &copy, &size);', ''),
        # Not listed, kept as defences: the B.W sign (the cave is always above
        # the site, so the offset is positive on this firmware) and the veneer
        # word check (the FSHK header check behind it refuses any foreign B.W
        # on its own) -- neither can change an outcome on the real layout.
        'index check ignored': ('            else if (out->slots[args[0]] != args[1]) r = UIA_GUARD;', ''),
        'replaces a file twice': ('                if (table_find(table, f.stock, &copy, &size)) { r = UIA_CONFLICT; goto done; }',
                                  '                (void)copy; (void)size;'),
        'table entry pointer and size swapped': ('            poke(table + 20 + 12 * at, files[i].buf);\n            poke(table + 24 + 12 * at, files[i].len);',
                                                 '            poke(table + 20 + 12 * at, files[i].len);\n            poke(table + 24 + 12 * at, files[i].buf);'),
    }
    ASM_MUTATIONS = {
        'size not handed over': ('    str     r1, [sp]                @ and its size, where the reader takes it\n', ''),
        'returns in ARM state': ('    .word   0xC05E5BF1              @ Thumb C05E5BF0', '    .word   0xC05E5BF0'),
        'compares before adding': ('    add     r0, r0, r1              @ the displaced adds\n', ''),
    }

    def test_every_c_mutation_is_caught(self):
        with tempfile.TemporaryDirectory(prefix='uio-mut-') as tmp:
            for index, (name, seam) in enumerate(self.C_MUTATIONS.items()):
                with self.subTest(mutation=name):
                    lib = TA.build(tmp, seam, f'm{index}.dylib')

                    class Against(OptionsApplyTests):
                        @classmethod
                        def setUpClass(cls):
                            cls.lib = lib
                            cls.lossless = TA.lossless()[0]
                            cls.color = O.color_raw(raw_icon()).encode()
                            cls.res = O.resolution('OG3K 3008x2000', 'OG3K').encode()

                        @classmethod
                        def tearDownClass(cls):
                            pass
                    result = unittest.TestResult()
                    unittest.defaultTestLoader.loadTestsFromTestCase(Against).run(result)
                    self.assertFalse(result.wasSuccessful(), f'mutation survived: {name}')

    def test_every_handler_mutation_is_caught(self):
        src = HERE / 'fv_handler.S'
        text = src.read_text()
        real = O.handler
        with tempfile.TemporaryDirectory(prefix='uio-asm-') as tmp:
            for name, (old, new) in self.ASM_MUTATIONS.items():
                with self.subTest(mutation=name):
                    self.assertEqual(text.count(old), 1, name)
                    mutated = pathlib.Path(tmp) / 'fv_handler.S'
                    mutated.write_text(text.replace(old, new))

                    def fake():
                        sys.path.insert(0, str(ROOT / 'fpSup' / 'fp_usb_shell'))
                        from armasm import assemble, symbols
                        sym = symbols(mutated)
                        return assemble(mutated), sym['fv_entry'], sym['fv_head'] + 8
                    O.handler = fake
                    try:
                        result = unittest.TestResult()
                        unittest.defaultTestLoader.loadTestsFromTestCase(HandlerTests).run(result)
                    finally:
                        O.handler = real
                    self.assertFalse(result.wasSuccessful(), f'mutation survived: {name}')


if __name__ == '__main__':
    unittest.main()
