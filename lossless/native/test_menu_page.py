"""The Lossless RAW row installer (menu_page.c) on the real stock UI data.

The row rides in fpSup.BIN as an FPUI block (menu/build_fpui.py) and is
composed onto MainB2 by fpSup/uishare/ui_apply.c; ui_apply's own rules are
tested in fpSup/uishare/test_ui_apply.py. What is asserted here is the
installer around it: where in fpSup.BIN the block is read from, the file
object's lifetime, the private variables (registered once, OFF, with names
the registry may borrow for good), what ON means, that the page MainB2 ends
up with is exactly what the reference applier makes -- alone, and on top of
another sup's addition -- and that any failure leaves the UI as it was.
"""
import ctypes as ct
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
FPSUP = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / 'menu'))
sys.path.insert(0, str(FPSUP / 'uishare'))
import build_fpui                    # noqa: E402
import pack_menu_file as P           # noqa: E402
from ui import fpui                  # noqa: E402

INSTALLED, NO_GUI, NO_SCREEN, READER, NO_ENTRY, ROOM, FILE, FORMAT, REGISTER, VARIABLE, \
    POOL_REFUSED, UI = range(1, 13)
(RESULT, PAGE, POOL, POOL_LEN, REGISTERED, OOB, CTORS, DTORS, OPENS, READS, CLOSES,
 VOLUME, REG_CALLS, WRITES_N, PUBLISHES, REG_PUBLISHES, DESC_N, OPEN_MODE, PAGE_LEN,
 FILE_LEN, PATH_OK, R_READER, R_ENTRIES, R_AREA, R_APP, R_SCREENS, R_NBU,
 BORROWED_BAD, FILE_AT, UI_RESULT, UI_OP, FIRST_ID, ROW, NBU_WRITES, MAINB2) = range(35)
AREA_BYTES = 0x80000
NBU, NBU_SIZE = 0xC18C0460, 0x800000
STOCK_POOL_LEN = 176152
UIA_BLOCK, UIA_PAGE = 1, 3

_BUILT = None


def built():
    """(FPUI block, stock page, stock pool, image from NBU) -- once."""
    global _BUILT
    if _BUILT is None:
        blob, _, (stock_page, pool_stock, _page) = build_fpui.build()
        seg0 = P.SEG0.read_bytes()
        img = seg0[NBU - 0xC0000000:NBU - 0xC0000000 + NBU_SIZE]
        _BUILT = (blob, stock_page, pool_stock, img)
    return _BUILT


def vbin(used, at):
    """The loader's part of fpSup.BIN: a VBIN header saying `used` bytes, then
    filler that is not zero (nothing may rely on it), to file offset `at`."""
    count = 3
    head = struct.pack('<4sIII', b'VBIN', count, 0, used - 16 - 8 * count)
    return head + bytes((i * 5 + 7) & 0xFF for i in range(at - len(head)))


HEAD = vbin(0x61AC, 0xF000)


def build(directory, replace=None, name='menu.dylib'):
    source = HERE / 'menu_page.c'
    uishare = FPSUP / 'uishare'
    if replace:
        old, new = replace
        text = source.read_text()
        if text.count(old) != 1:
            raise AssertionError('mutation seam not found once: ' + old[:60])
        source = pathlib.Path(directory) / (name + '.c')
        source.write_text(text.replace(old, new))
    out = pathlib.Path(directory) / name
    subprocess.run([shutil.which('clang') or 'clang', '-shared', '-fPIC', '-O1', '-std=c11',
                    '-Wall', '-Wextra', '-Werror', '-DFPL_MENU_HOST_TEST', '-DUIS_HOST_TEST',
                    '-DUIA_HOST_TEST', '-I', str(HERE), '-I', str(uishare), str(source),
                    str(HERE / 'menu_page_fixture.c'), str(uishare / 'ui_pool.c'),
                    str(uishare / 'ui_apply.c'), '-o', str(out)],
                   check=True, capture_output=True, text=True, timeout=120)
    lib = ct.CDLL(str(out))
    for fn, args, ret in (('reset', [ct.c_char_p, ct.c_uint32], None),
                          ('file', [ct.c_char_p, ct.c_uint32, ct.c_uint32], None),
                          ('word', [ct.c_uint32] * 2, None), ('peek', [ct.c_uint32], ct.c_uint32),
                          ('byte', [ct.c_uint32], ct.c_uint32), ('reg_result', [ct.c_uint32], None),
                          ('preregister', [ct.c_uint32], None), ('set_value', [ct.c_uint32], None),
                          ('install', [ct.c_uint32], ct.c_uint32), ('on', [], ct.c_uint32),
                          ('get', [ct.c_uint32], ct.c_uint32),
                          ('other_sup', [ct.c_char_p, ct.c_uint32], ct.c_uint32),
                          ('other_block', [ct.c_char_p, ct.c_uint32], ct.c_uint32),
                          ('read', [ct.c_uint32, ct.c_char_p, ct.c_uint32], None)):
        f = getattr(lib, 'fpl_fixture_' + fn)
        f.argtypes = args
        f.restype = ret
    return lib


class MenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='fpl-menu-')
        cls.lib = build(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.blob, self.stock_page, self.pool_stock, img = built()
        self.lib.fpl_fixture_reset(img, len(img))
        self.put(self.blob)

    def put(self, data, present=1, head=None):
        """fpSup.BIN: the loader's part, then the row's block."""
        data = (HEAD if head is None else head) + data
        self.lib.fpl_fixture_file(data, len(data), present)

    def get(self, f): return self.lib.fpl_fixture_get(f)
    def peek(self, a): return self.lib.fpl_fixture_peek(a)

    def read(self, a, n):
        buf = ct.create_string_buffer(n)
        self.lib.fpl_fixture_read(a, buf, n)
        return buf.raw

    def install(self, area=AREA_BYTES):
        return self.lib.fpl_fixture_install(area)

    def page_now(self):
        p = (NBU + self.get(MAINB2)) & 0xFFFFFFFF
        n = self.peek((p & ~3) - 128 + 12) if self.peek((p & ~3) - 128) == 0x47505346 else len(self.stock_page)
        return self.read(p, n)

    def pool_now(self):
        return self.read(self.peek(self.get(R_READER) + 0x14), self.peek(self.get(R_READER) + 0x10))

    def reference(self, blobs):
        pages = {'MainB2': fpui.PageCopy(self.stock_page, len(self.stock_page), 1)}
        pool = fpui.Pool(self.pool_stock)
        for b in blobs:
            fpui.apply(b, pages, pool)
        return bytes(pages['MainB2'].data), bytes(pool.data)

    def assert_untouched(self, result):
        self.assertEqual(self.install(), result)
        self.assertEqual(self.get(MAINB2), 0x76FF04, 'MainB2 was switched')
        self.assertEqual(self.get(NBU_WRITES), 0)

    # ---- installed ----------------------------------------------------------
    def test_the_row_is_composed_exactly_as_the_reference(self):
        self.assertEqual(self.install(), INSTALLED)
        page, pool = self.reference([self.blob])
        self.assertEqual(self.page_now(), page)
        self.assertEqual(self.pool_now()[STOCK_POOL_LEN:], pool[STOCK_POOL_LEN:])
        self.assertEqual((self.get(FIRST_ID), self.get(ROW)), (34494, 243))
        self.assertEqual(self.get(PAGE), (NBU + self.get(MAINB2)) & 0xFFFFFFFF)
        self.assertEqual(self.get(NBU_WRITES), 0, 'the firmware image was written')
        self.assertEqual(self.get(OOB), 0)

    def test_stock_strings_keep_their_stock_offsets(self):
        self.install()
        pool = self.pool_now()
        for text, at in (('xxx', 0x2A5C4), ('Footer05', 0x12BC4)):
            self.assertEqual(pool[at:at + len(text) + 1], text.encode() + b'\0')
            self.assertEqual(pool.count(text.encode() + b'\0', STOCK_POOL_LEN), 0,
                             text + ' was appended although the stock pool has it')

    def test_on_top_of_another_sups_row(self):
        # another sup composed MainB2 first: this row goes after its row
        self.assertEqual(self.lib.fpl_fixture_other_block(self.blob, len(self.blob)), 0)
        self.assertEqual(self.install(), INSTALLED)
        page, _ = self.reference([self.blob, self.blob])
        self.assertEqual(self.page_now(), page)
        self.assertEqual((self.get(FIRST_ID), self.get(ROW)), (34494 + 23, 243 + 81))

    def test_a_string_another_sup_already_added_is_shared(self):
        same = self.lib.fpl_fixture_other_sup(b'Lossless RAW', 12)
        self.assertNotEqual(same, 0xFFFFFFFF)
        self.install()
        pool = self.pool_now()
        self.assertEqual(pool.count(b'Lossless RAW\0', STOCK_POOL_LEN), 1)

    def test_published_before_the_switch(self):
        self.install()
        self.assertGreaterEqual(self.get(PUBLISHES), 2)

    # ---- the variables ------------------------------------------------------
    def test_the_variables_are_registered_once_off_with_borrowed_resident_names(self):
        self.install()
        self.assertEqual((self.get(REG_CALLS), self.get(REGISTERED), self.get(BORROWED_BAD)), (3, 3, 0))
        self.assertEqual(self.lib.fpl_fixture_on(), 0)

    def test_on_follows_the_row(self):
        self.install()
        self.lib.fpl_fixture_set_value(1)
        self.assertEqual(self.lib.fpl_fixture_on(), 1)
        self.lib.fpl_fixture_set_value(2)
        self.assertEqual(self.lib.fpl_fixture_on(), 0)

    def test_an_existing_variable_of_that_name_is_used_not_registered_again(self):
        self.lib.fpl_fixture_preregister(0)
        self.assertEqual(self.install(), INSTALLED)
        self.assertEqual(self.get(REGISTERED), 2)

    def test_a_registration_that_fails_switches_nothing(self):
        self.lib.fpl_fixture_reg_result(5)
        self.assert_untouched(REGISTER)

    def test_a_variable_of_that_name_but_another_type_switches_nothing(self):
        self.lib.fpl_fixture_preregister(3)
        self.assertNotEqual(self.install(), INSTALLED)
        self.assertEqual(self.get(MAINB2), 0x76FF04)

    # ---- the file -----------------------------------------------------------
    def test_the_file_is_read_from_the_card_and_the_object_always_destroyed(self):
        self.install()
        self.assertEqual((self.get(VOLUME), self.get(OPEN_MODE), self.get(PATH_OK)), (1, 1, 1))
        self.assertEqual((self.get(CTORS), self.get(OPENS), self.get(READS), self.get(CLOSES),
                          self.get(DTORS)), (1, 1, 3, 1, 1))
        self.assertEqual(self.get(FILE_AT), 0xF000)

    def test_a_bigger_vbin_moves_the_block_to_the_next_4k(self):
        for used, at in ((0xF004, 0x10000), (0x10A00, 0x11000), (0x1F000, 0x1F000)):
            with self.subTest(used=hex(used)):
                self.setUp()
                self.put(self.blob, head=vbin(used, at))
                self.assertEqual(self.install(), INSTALLED)
                self.assertEqual(self.get(FILE_AT), at)

    def test_a_block_left_at_0xF000_behind_a_bigger_vbin_is_not_taken(self):
        self.put(self.blob, head=vbin(0x10A00, 0x11000)[:0xF000])
        self.assertNotEqual(self.install(), INSTALLED)
        self.assertEqual(self.get(MAINB2), 0x76FF04)

    def test_no_vbin_or_one_past_the_ceiling_installs_nothing(self):
        for name, head in {'not a VBIN': b'NBIV' + HEAD[4:], 'past 0x1F000': vbin(0x1F004, 0x20000),
                           'short file': HEAD[:12]}.items():
            with self.subTest(name):
                self.setUp()
                self.put(self.blob if name != 'short file' else b'', head=head)
                self.assert_untouched(FILE)
                self.assertEqual(self.get(DTORS), 1)

    def test_the_skip_is_read_in_pieces_no_bigger_than_the_room(self):
        room = len(self.blob) + 0x1000
        self.put(self.blob, head=vbin(0x1E800, 0x1F000))
        self.assertEqual(self.install(0x1000 + room), INSTALLED)
        self.assertEqual(self.get(READS), 1 + -(-(0x1F000 - 16) // room) + 1)

    def test_no_file_on_the_card(self):
        self.put(self.blob, present=0)
        self.assert_untouched(FILE)

    def test_a_block_that_is_not_ours(self):
        self.put(b'FPLM' + self.blob[4:])
        self.assert_untouched(FORMAT)

    def test_a_damaged_or_truncated_block(self):
        bad = bytearray(self.blob)
        bad[0x1000] ^= 0x40
        for name, data in {'damaged': bytes(bad), 'truncated': self.blob[:len(self.blob) // 2]}.items():
            with self.subTest(name):
                self.setUp()
                self.put(data)
                self.assert_untouched(UI)
                self.assertEqual(self.get(UI_RESULT), UIA_BLOCK)

    def test_bytes_after_the_block_are_ignored(self):
        self.put(self.blob + b'\x55' * 0x2000)
        self.assertEqual(self.install(), INSTALLED)

    def test_too_little_room(self):
        self.assertEqual(self.install(0x1000 + 0x800), ROOM)
        self.setUp()
        self.assertEqual(self.install(0x1000 + len(self.blob)), ROOM)
        self.assertEqual(self.get(MAINB2), 0x76FF04)

    # ---- the camera is not what we expect -------------------------------------
    def test_it_installs_once(self):
        self.install()
        page = self.page_now()
        self.assertEqual(self.install(), INSTALLED)
        self.assertEqual(self.page_now(), page)
        self.assertEqual(self.get(REG_CALLS), 3)

    def test_no_gui_or_no_mainb2_screen_gets_nothing(self):
        self.lib.fpl_fixture_word(0xC37B7048, 0)
        self.assert_untouched(NO_GUI)
        self.setUp()
        names = self.get(R_SCREENS)
        self.lib.fpl_fixture_word(names + 0x40 * 2 + 8, self.peek(names + 0x40 * 3 + 8))
        self.assert_untouched(NO_SCREEN)

    def test_a_pool_nobody_announced_installs_nothing(self):
        self.lib.fpl_fixture_word(self.get(R_READER) + 0x14, 0x12345678)
        self.assert_untouched(READER)

    def test_a_mainb2_that_is_neither_stock_nor_ours_is_left_alone(self):
        self.lib.fpl_fixture_word(self.get(R_ENTRIES) + 44 * 2 + 8, 0x76FF00)
        self.assertEqual(self.install(), UI)
        self.assertEqual(self.get(UI_RESULT), UIA_PAGE)
        self.assertEqual(self.get(MAINB2), 0x76FF00)


class MenuMutationTests(unittest.TestCase):
    MUTATIONS = {
        'accepts any pool': ('        !uis_pool_known(reader))              /* stock, or shared by the convention */',
                             '        0)'),
        'registers every time': ('        d = variable(app, NAME);\n        if (!d) {', '        d = 0;\n        if (!d) {'),
        'reads ON from any nonzero': ('    if (!d || peek(d + DESC_VALUE) != 1u) return 0;',
                                      '    if (!d || !peek(d + DESC_VALUE)) return 0;'),
        'hands the registry a stack name': (
            '            def[1] = (uint32_t)(uintptr_t)NAME;',
            '            def[1] = (uint32_t)(uintptr_t)screen_words;'),
        'defaults ON': ('            def[2] = 0u;                               /* OFF */',
                        '            def[2] = 1u;'),
        'leaks the file object': ('    f_dtor(fobj, 2);\n', ''),
        'reads the data right after the header': (
            '            ok = f_read(fobj, file, want, &actual) && actual == want;',
            '            ok = 1; actual = want;'),
        'skips in one read whatever the room': (
            '            uint32_t want = at - done < room ? at - done : room;',
            '            uint32_t want = at - done;'),
        'does not round up to 4 KiB': (
            '    used = (used + FPL_MENU_AT_STEP - 1u) & ~(FPL_MENU_AT_STEP - 1u);', ''),
        'always at 0xF000': ('    if (used <= FPL_MENU_AT_MIN) return FPL_MENU_AT_MIN;',
                             '    return FPL_MENU_AT_MIN;'),
        'trusts any header': ('    if (peek(h) != FPL_VBIN_MAGIC || count > FPL_MENU_AT_MAX / 8u || body > FPL_MENU_AT_MAX)',
                              '    if (0)'),
        'no ceiling': ('    return used <= FPL_MENU_AT_MAX ? used : 0;', '    return used;'),
        'registers only the value': ('    for (uint32_t n = 0; n < FPL_MENU_VARIABLES; ++n) {\n        const char *NAME',
                                     '    for (uint32_t n = 0; n < 1; ++n) {\n        const char *NAME'),
        'reads ON from the lock': ('    d = variable(m->app, (const char *)m->names[0]);',
                                   '    d = variable(m->app, (const char *)m->names[2]);'),
        'passes the padding as the block': ('    m->ui_result = uia_apply(file, actual, &ui);',
                                            '    m->ui_result = uia_apply(file + 4, actual, &ui);'),
        'installed although the block was refused': (
            '    if (m->ui_result != UIA_OK) return fail(m, FPL_MENU_UI);', ''),
        'not once': ('    if (!m || m->result) return m ? m->result : FPL_MENU_NO_GUI;   /* once */',
                     '    if (!m) return FPL_MENU_NO_GUI;'),
    }

    def test_every_mutation_is_caught(self):
        with tempfile.TemporaryDirectory(prefix='fpl-menu-mut-') as tmp:
            for index, (name, seam) in enumerate(self.MUTATIONS.items()):
                with self.subTest(mutation=name):
                    lib = build(tmp, seam, f'm{index}.dylib')

                    class Against(MenuTests):
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
        with tempfile.TemporaryDirectory() as tmp:
            for src in (HERE / 'menu_page.c', FPSUP / 'uishare' / 'ui_apply.c'):
                subprocess.run([clang, '--target=armv7a-none-eabi', '-mcpu=cortex-a9', '-mthumb',
                                '-mfloat-abi=soft', '-ffreestanding', '-fno-builtin', '-nostdlib',
                                '-fropi', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
                                '-I', str(FPSUP / 'uishare'), '-I', str(HERE),
                                '-c', str(src), '-o', str(pathlib.Path(tmp) / (src.stem + '.o'))],
                               check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
