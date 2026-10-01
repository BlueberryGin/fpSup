"""The Lossless RAW row installer (menu_page.c) against a model of the camera.

menu_page.c + menu_page_fixture.c on the host. The fixture holds the native
objects exactly as read from the camera on 2026-09-30 (GUI word C37B7048 ->
app; screen vector; the shared reader with its pool, NBU base and 44-byte
runtime entries), the stock pool at its firmware address, a fake card file
and a fake variable registry. What is asserted: all or nothing -- on any
mismatch MainB2's page offset does not change and no variable is registered
-- and, when installed, that every private string the page names resolves,
by the firmware's own rule, to that string through the SHARED pool
(fpSup/uishare), whoever extended it first.
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
sys.path.insert(0, str(HERE.parent / 'menu'))
import pack_menu_file as P  # noqa: E402

INSTALLED, NO_GUI, NO_SCREEN, READER, NO_ENTRY, ROOM, FILE, FORMAT, REGISTER, VARIABLE, \
    POOL_REFUSED = range(1, 12)
(RESULT, PAGE, POOL, POOL_LEN, REGISTERED, OOB, CTORS, DTORS, OPENS, READS, CLOSES,
 VOLUME, REG_CALLS, WRITES_N, PUBLISHES, REG_PUBLISHES, DESC_N, OPEN_MODE, PAGE_LEN,
 FILE_LEN, PATH_OK, R_READER, R_ENTRIES, R_AREA, R_APP, R_SCREENS, R_NBU,
 BORROWED_BAD) = range(28)
AREA_BYTES = 0x80000
POOL_LEN_STOCK = 176152


def stock_pool():
    return bytes(((i * 7 + 3) & 0xFF) for i in range(POOL_LEN_STOCK))


PAGE_BYTES = bytes((i * 13 + 1) & 0xFF for i in range(181976))
STRINGS = b'fpLossless_Row_GATED\0MV_fpLossless\0xxx\0Lossless RAW\0'
S = {s: STRINGS.index(s.encode() + b'\0') for s in
     ('fpLossless_Row_GATED', 'MV_fpLossless', 'xxx', 'Lossless RAW')}
REFS = [(0x2440E, 'fpLossless_Row_GATED'), (0x24A9D, 'MV_fpLossless'),
        (0x24AD3, 'MV_fpLossless'), (0x25012, 'xxx'), (0x2A9A8, 'Lossless RAW'),
        (0x2AA97, 'Lossless RAW')]


def menu_file(page=PAGE_BYTES, strings=STRINGS, refs=None, body_only=False):
    """fpSup.BIN's row data, format 2 (menu/pack_menu_file.py)."""
    refs = REFS if refs is None else refs
    blob = b''.join(struct.pack('<II', at, S[s] if isinstance(s, str) else s) for at, s in refs)
    head = struct.pack('<8I', P.MAGIC, P.VERSION, len(page), len(strings), len(refs),
                       P.MAINB2_OFFSET, P.fnv(page + strings + blob), 0)
    data = head + page + strings + blob
    return data if body_only else data + b'\0' * (P.FILE_BYTES - len(data))


BODY = len(menu_file(body_only=True))


def build(directory, replace=None, name='menu.dylib'):
    source = HERE / 'menu_page.c'
    uishare = HERE.parents[1] / 'uishare'
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
                    '-I', str(HERE), '-I', str(uishare), str(source),
                    str(HERE / 'menu_page_fixture.c'), str(uishare / 'ui_pool.c'), '-o', str(out)],
                   check=True, capture_output=True, text=True, timeout=60)
    lib = ct.CDLL(str(out))
    for fn, args, ret in (('reset', [], None), ('file', [ct.c_char_p, ct.c_uint32, ct.c_uint32], None),
                          ('word', [ct.c_uint32] * 2, None), ('peek', [ct.c_uint32], ct.c_uint32),
                          ('byte', [ct.c_uint32], ct.c_uint32), ('reg_result', [ct.c_uint32], None),
                          ('preregister', [ct.c_uint32], None), ('set_value', [ct.c_uint32], None),
                          ('install', [ct.c_uint32], ct.c_uint32), ('on', [], ct.c_uint32),
                          ('get', [ct.c_uint32], ct.c_uint32),
                          ('other_sup', [ct.c_char_p, ct.c_uint32], ct.c_uint32),
                          ('resolve_page', [ct.c_uint32, ct.c_char_p, ct.c_uint32], ct.c_uint32)):
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
        self.lib.fpl_fixture_reset()
        self.put(menu_file())

    HEAD = bytes((i * 5 + 7) & 0xFF for i in range(0xF000))   # the loader's part

    def put(self, data, present=1, head=None):
        """fpSup.BIN: the loader's 61440 bytes, then the row's data."""
        data = (self.HEAD if head is None else head) + data
        self.lib.fpl_fixture_file(data, len(data), present)

    def get(self, f): return self.lib.fpl_fixture_get(f)
    def peek(self, a): return self.lib.fpl_fixture_peek(a)

    def switched(self):
        r, e = self.get(R_READER), self.get(R_ENTRIES)
        return (self.peek(r + 0x14), self.peek(r + 0x10), self.peek(e + 44 * 2 + 8))

    STOCK = (0xC18C0474, POOL_LEN_STOCK, 0x76FF04)

    def bytes_at(self, a, n):
        return bytes(self.lib.fpl_fixture_byte(a + i) for i in range(n))

    def assert_untouched(self, result):
        self.assertEqual(self.lib.fpl_fixture_install(AREA_BYTES), result)
        self.assertEqual(self.switched(), self.STOCK, 'a word was switched')
        self.assertEqual(self.get(REG_CALLS), 0, 'a variable was registered')
        self.assertEqual(self.lib.fpl_fixture_on(), 0)

    # ---- installed ---------------------------------------------------------
    def resolved(self, page_off):
        buf = ct.create_string_buffer(64)
        return buf.value.decode() if self.lib.fpl_fixture_resolve_page(page_off, buf, 64) else None

    def test_every_private_string_the_page_names_resolves_through_the_shared_pool(self):
        self.assertEqual(self.lib.fpl_fixture_install(AREA_BYTES), INSTALLED)
        page = self.get(PAGE)
        self.assertEqual((0xC18C0460 + self.switched()[2]) & 0xFFFFFFFF, page)
        for at, s in REFS:
            self.assertEqual(self.resolved(at), s, f'page word {at:#x}')
        # the rest of the page is the file's
        self.assertEqual(self.bytes_at(page, 4096), PAGE_BYTES[:4096])
        # the pool is the convention's copy, stock prefix unchanged
        pool = self.switched()[0]
        self.assertEqual(self.peek(pool - 16), 0x4C505346)
        self.assertEqual(self.bytes_at(pool, 4096), stock_pool()[:4096])
        self.assertEqual(self.get(OOB), 0)

    def test_a_string_another_sup_already_added_is_shared_and_theirs_still_resolves(self):
        theirs = self.lib.fpl_fixture_other_sup(b'OG3K', 4)
        same = self.lib.fpl_fixture_other_sup(b'Lossless RAW', 12)
        self.assertNotEqual(theirs, 0xFFFFFFFF)
        pool = self.switched()[0]
        self.assertEqual(self.lib.fpl_fixture_install(AREA_BYTES), INSTALLED)
        self.assertEqual(self.switched()[0], pool, 'a second copy of the pool')
        for at, s in REFS:
            self.assertEqual(self.resolved(at), s)
        page = self.get(PAGE)
        word = int.from_bytes(self.bytes_at(page + 0x2A9A8, 4), 'big')
        self.assertEqual(word, same, 'the same string got a second entry')
        buf = ct.create_string_buffer(16)
        # their string, by the firmware's rule, is still theirs
        length, base = self.switched()[1], self.switched()[0]
        self.assertLess(theirs, length)
        self.assertEqual(self.bytes_at(base + theirs, 5), b'OG3K\0')
        del buf

    def test_only_mainb2_changes(self):
        e = self.get(R_ENTRIES)
        before = [self.peek(e + 44 * i + 8) for i in range(4)]
        self.lib.fpl_fixture_install(AREA_BYTES)
        after = [self.peek(e + 44 * i + 8) for i in range(4)]
        self.assertEqual([b for i, b in enumerate(before) if i != 2],
                         [a for i, a in enumerate(after) if i != 2])
        self.assertNotEqual(before[2], after[2])

    def test_the_page_is_switched_last(self):
        self.lib.fpl_fixture_install(AREA_BYTES)
        e = self.get(R_ENTRIES)
        log = [self.get(100 + i) for i in range(self.get(WRITES_N))]
        self.assertEqual(log[-1], e + 44 * 2 + 8 - 0x10000000)
        self.assertEqual(log.count(e + 44 * 2 + 8 - 0x10000000), 1)

    def test_published_before_and_after_the_switch(self):
        self.lib.fpl_fixture_install(AREA_BYTES)
        self.assertGreaterEqual(self.get(PUBLISHES), 2)

    def test_the_variable_is_registered_once_off_with_a_borrowed_resident_name(self):
        self.assertEqual(self.lib.fpl_fixture_install(AREA_BYTES), INSTALLED)
        self.assertEqual((self.get(REG_CALLS), self.get(REGISTERED), self.get(DESC_N)), (3, 3, 3))
        names = [self.bytes_at(self.peek(0x10009000 + 32 * i + 4), 20).split(b'\0')[0] for i in range(3)]
        self.assertEqual(names, [b'MV_fpLossless', b'SUB_MV_fpLossless', b'EXCL_fpLossless'])
        self.assertEqual([self.peek(0x10009000 + 32 * i + 8) for i in range(3)], [0, 0, 0])
        self.assertEqual(self.get(BORROWED_BAD), 0, 'the registry was given a temporary name')
        self.assertEqual(self.lib.fpl_fixture_on(), 0, 'not OFF by default')

    def test_on_follows_the_row(self):
        self.lib.fpl_fixture_install(AREA_BYTES)
        self.lib.fpl_fixture_set_value(1)
        self.assertEqual(self.lib.fpl_fixture_on(), 1)
        self.lib.fpl_fixture_set_value(0)
        self.assertEqual(self.lib.fpl_fixture_on(), 0)
        self.lib.fpl_fixture_set_value(7)                  # not a value the row writes
        self.assertEqual(self.lib.fpl_fixture_on(), 0)

    def test_an_existing_variable_of_that_name_is_used_not_registered_again(self):
        self.lib.fpl_fixture_preregister(0)
        self.assertEqual(self.lib.fpl_fixture_install(AREA_BYTES), INSTALLED)
        self.assertEqual((self.get(REG_CALLS), self.get(REGISTERED)), (2, 2))

    def test_the_file_is_read_from_the_card_and_the_object_always_destroyed(self):
        self.lib.fpl_fixture_install(AREA_BYTES)
        self.assertEqual((self.get(VOLUME), self.get(OPEN_MODE), self.get(PATH_OK)), (1, 1, 1))
        self.assertEqual((self.get(CTORS), self.get(OPENS), self.get(READS), self.get(CLOSES),
                          self.get(DTORS)), (1, 1, 2, 1, 1))

    def test_it_installs_once(self):
        self.lib.fpl_fixture_install(AREA_BYTES)
        switched = self.switched()
        self.assertEqual(self.lib.fpl_fixture_install(AREA_BYTES), INSTALLED)
        self.assertEqual(self.switched(), switched)
        self.assertEqual(self.get(OPENS), 1)

    # ---- nothing installed -------------------------------------------------
    def test_a_camera_whose_objects_differ_gets_nothing(self):
        app, scr, r, e = self.get(R_APP), self.get(R_SCREENS), self.get(R_READER), self.get(R_ENTRIES)
        cases = {
            'no GUI app': (0xC37B7048, 0, NO_GUI),
            'screen count wild': (app + 0x80, 100000, NO_GUI),
            'MainB2 of another app': (scr + 0x40 * 2 + 4, app + 0x100, NO_SCREEN),
            'reader not the NBU': (r + 0x24, 0xC18C0000, READER),
            'pool moved already': (r + 0x14, 0x12345678, READER),
            'pool of another length': (r + 0x10, POOL_LEN_STOCK + 8, READER),
            'entry at another offset': (e + 44 * 2 + 8, 0x76FF08, NO_ENTRY),
        }
        for name, (addr, value, result) in cases.items():
            with self.subTest(case=name):
                self.setUp()
                self.lib.fpl_fixture_word(addr, value)
                self.assertEqual(self.lib.fpl_fixture_install(AREA_BYTES), result)
                self.assertEqual(self.get(REG_CALLS), 0)
                self.assertEqual(self.get(OPENS), 0, 'the card was read before the checks')
                self.assertEqual(self.get(WRITES_N), 0)

    def test_no_mainb2_screen_gets_nothing(self):
        # rename the MainB2 screen/entry name string
        scr = self.get(R_SCREENS)
        name = self.peek(scr + 0x40 * 2 + 8)
        self.lib.fpl_fixture_word(name, 0x6E69614D)       # "Main" then garbage
        self.lib.fpl_fixture_word(name + 4, 0x00585842)
        self.assert_untouched(NO_SCREEN)

    def test_no_file_on_the_card(self):
        self.put(menu_file(), present=0)
        self.assert_untouched(FILE)
        self.assertEqual(self.get(DTORS), 1)

    def test_a_file_that_is_not_ours_or_is_damaged(self):
        good = bytearray(menu_file())
        cases = {
            'magic': (0, b'XXXX'),
            'version': (4, struct.pack('<I', 3)),
            'string length': (0x0C, struct.pack('<I', len(STRINGS) + 1)),
            'stock page offset': (0x14, struct.pack('<I', 0x76FF00)),
            'one page byte': (0x20 + 5000, bytes([good[0x20 + 5000] ^ 1])),
            'one string byte': (0x20 + len(PAGE_BYTES) + 10, bytes([good[0x20 + len(PAGE_BYTES) + 10] ^ 1])),
            'one reference byte': (BODY - 3, bytes([good[BODY - 3] ^ 1])),
        }
        for name, (off, patch) in cases.items():
            with self.subTest(case=name):
                self.setUp()
                bad = bytearray(good)
                bad[off:off + len(patch)] = patch
                self.put(bytes(bad))
                self.assert_untouched(FORMAT)

    def test_a_truncated_file(self):
        for data in (menu_file()[:BODY - 1], menu_file()[:16]):
            with self.subTest(length=len(data)):
                self.setUp()
                self.put(data)
                self.assert_untouched(FORMAT)

    def test_bytes_after_the_declared_end_are_ignored(self):
        # putfile cannot shorten a file: an older, longer one leaves its tail
        data = menu_file()[:BODY] + b'\xAA' * 20
        self.put(data)
        self.assertEqual(self.lib.fpl_fixture_install(AREA_BYTES), INSTALLED)

    def test_a_bin_without_the_row_data_installs_nothing(self):
        for head, data in ((self.HEAD[:0x8000], b''), (self.HEAD, b'')):
            with self.subTest(length=len(head)):
                self.setUp()
                self.put(data, head=head)
                self.assertIn(self.lib.fpl_fixture_install(AREA_BYTES), (FILE, FORMAT))
                self.assertEqual(self.switched(), self.STOCK)

    def test_the_data_is_taken_from_past_the_loaders_part_only(self):
        # a data block at offset 0 (the old FPLMENU.BIN layout) is not ours
        self.lib.fpl_fixture_file(menu_file(), len(menu_file()), 1)
        self.assertEqual(self.lib.fpl_fixture_install(AREA_BYTES), FORMAT)

    def test_too_little_room(self):
        self.assertEqual(self.lib.fpl_fixture_install(0x1000 + 0xF000 - 8), ROOM)
        self.assertEqual(self.switched(), self.STOCK)
        self.setUp()
        self.assertEqual(self.lib.fpl_fixture_install(0x1000 + P.FILE_BYTES), ROOM)
        self.assertEqual(self.switched(), self.STOCK)

    def test_references_that_do_not_fit_change_nothing(self):
        for case, refs in {'past the page': [(len(PAGE_BYTES) - 2, 'xxx')],
                           'past the strings': [(0x100, len(STRINGS))],
                           'into a terminator': [(0x100, len(STRINGS) - 1)]}.items():
            with self.subTest(case=case):
                self.setUp()
                self.put(menu_file(refs=refs))
                self.assert_untouched(FORMAT)

    def test_a_pool_nobody_announced_installs_nothing(self):
        self.lib.fpl_fixture_word(self.get(R_READER) + 0x14, 0x12345678)
        self.assertEqual(self.lib.fpl_fixture_install(AREA_BYTES), READER)
        self.assertEqual(self.get(REG_CALLS), 0)
        self.assertEqual(self.switched()[2], 0x76FF04)

    def test_a_registration_that_fails_switches_nothing(self):
        self.lib.fpl_fixture_reg_result(3)
        self.assertEqual(self.lib.fpl_fixture_install(AREA_BYTES), REGISTER)
        self.assertEqual(self.switched()[2], 0x76FF04)
        self.assertEqual(self.get(REG_CALLS), 1, 'retried a non-transactional registration')

    def test_a_variable_of_that_name_but_another_type_switches_nothing(self):
        self.lib.fpl_fixture_preregister(1)
        self.assertEqual(self.lib.fpl_fixture_install(AREA_BYTES), VARIABLE)
        self.assertEqual(self.switched()[2], 0x76FF04)


class PackTests(unittest.TestCase):
    def test_the_real_candidate_packs_against_the_pinned_image(self):
        if not P.SEG0.exists():
            self.skipTest('pinned firmware image not present')
        with tempfile.TemporaryDirectory() as tmp:
            data = P.build(pathlib.Path(tmp) / 'FPLMENU.BIN')
        magic, version, page, strings, refs, offset, _, zero = struct.unpack_from('<8I', data)
        self.assertEqual((magic, version, offset, zero), (P.MAGIC, 2, 0x76FF04, 0))
        self.assertEqual((page, refs), (181976, 18))
        self.assertEqual(len(data), P.FILE_BYTES)
        self.assertEqual(set(data[0x20 + page + strings + 8 * refs:]), {0})
        for name in P.NAMES:
            self.assertIn(name, data[0x20 + page:0x20 + page + strings])


class MenuMutationTests(unittest.TestCase):
    MUTATIONS = {
        'skips the checksum': ('    if (fnv(page, page_len + strings_len + 8u * reloc_count, 2166136261u) != peek(file + 0x18))',
                               '    if (fnv(page, page_len + strings_len + 8u * reloc_count, 2166136261u) != peek(file + 0x18) && 0)'),
        'leaves the builder offsets in the page': ('        put_be(page + at, off);',
                                                   '        put_be(page + at, s + 176152u);'),
        'writes the offset little-endian': ('        put_be(page + at, off);',
                                            '        put_be(page + at, __builtin_bswap32(off));'),
        'uses one cache entry for every string': (
            '        for (k = 0; k < cached && cache_from[k] != s; ++k) {}',
            '        for (k = 0; k < cached && cache_from[k] != s && 0; ++k) {}'),
        'checks references only while using them': (
            '        if (at > page_len - 4u || s >= strings_len) return fail(m, FPL_MENU_FORMAT);',
            '        if ((at > page_len - 4u || s >= strings_len) && 0) return fail(m, FPL_MENU_FORMAT);'),
        'accepts any pool': ('        !uis_pool_known(reader))              /* stock, or shared by the convention */',
                             '        0)'),
        'does not check the entry offset': (
            '    if (!entry || peek(entry + ENTRY_OFFSET) != MAINB2_OFFSET)', '    if (!entry)'),
        'registers every time': ('        d = variable(app, NAME);\n        if (!d) {', '        d = 0;\n        if (!d) {'),
        'reads ON from any nonzero': ('    if (!d || peek(d + DESC_VALUE) != 1u) return 0;',
                                      '    if (!d || !peek(d + DESC_VALUE)) return 0;'),
        'hands the registry a stack name': (
            '            def[1] = (uint32_t)(uintptr_t)NAME;',
            '            def[1] = (uint32_t)(uintptr_t)screen_words;'),
        'defaults ON': ('            def[2] = 0u;                               /* OFF */',
                        '            def[2] = 1u;'),
        'leaks the file object': ('    f_dtor(fobj, 2);\n', ''),
        'reads the data from the start of the BIN': (
            '        ok = f_read(fobj, file, FPL_MENU_AT, &actual) && actual == FPL_MENU_AT;\n', ''),
        'registers only the value': ('    for (uint32_t n = 0; n < FPL_MENU_VARIABLES; ++n) {\n        const char *NAME',
                                     '    for (uint32_t n = 0; n < 1; ++n) {\n        const char *NAME'),
        'reads ON from the lock': ('    d = variable(m->app, (const char *)m->names[0]);',
                                   '    d = variable(m->app, (const char *)m->names[2]);'),
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
            out = pathlib.Path(tmp) / 'menu.o'
            subprocess.run([clang, '--target=armv7a-none-eabi', '-mcpu=cortex-a9', '-mthumb',
                            '-mfloat-abi=soft', '-ffreestanding', '-fno-builtin', '-nostdlib',
                            '-fropi', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(HERE.parents[1] / 'uishare'),
                            '-c', str(HERE / 'menu_page.c'), '-o', str(out)],
                           check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
