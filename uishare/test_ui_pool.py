"""The shared UI string pool (ui_pool.c) against a model of the camera.

What is asserted is what every sup relies on: the stock strings keep their
offsets; a string a sup adds resolves, through the firmware's own rule
(offset < reader+0x10, address = reader+0x14 + offset), to exactly that
string for as long as the boot lasts; several sups interning in any order get
consistent offsets; and a pool this convention did not make is never touched.
"""
import ctypes as ct
import pathlib
import shutil
import subprocess
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
OK, NO_READER, UNKNOWN_POOL, NO_MEMORY, INVALID = range(5)
STOCK_POOL, STOCK_LEN, HEADROOM = 0xC18C0474, 176152, 0x4000
MAGIC = 0x4C505346
OOB, ALLOCS, PUBLISHES, LOG_N, LEN, POOL, READER, ALLOC, BYTE_READS = range(9)
NOT_STOCK = 0xFFFFFFFF


def build(directory, replace=None, name='pool.dylib'):
    source = HERE / 'ui_pool.c'
    if replace:
        old, new = replace
        text = source.read_text()
        if text.count(old) != 1:
            raise AssertionError('mutation seam not found once: ' + old[:60])
        source = pathlib.Path(directory) / (name + '.c')
        source.write_text(text.replace(old, new))
    out = pathlib.Path(directory) / name
    subprocess.run([shutil.which('clang') or 'clang', '-shared', '-fPIC', '-O1', '-std=c11',
                    '-Wall', '-Wextra', '-Werror', '-DUIS_HOST_TEST', '-I', str(HERE),
                    str(source), str(HERE / 'ui_pool_fixture.c'), '-o', str(out)],
                   check=True, capture_output=True, text=True, timeout=60)
    lib = ct.CDLL(str(out))
    for fn, args, ret in (('reset', [], None), ('alloc_fail', [ct.c_uint32], None),
                          ('word', [ct.c_uint32] * 2, None), ('peek', [ct.c_uint32], ct.c_uint32),
                          ('byte', [ct.c_uint32], ct.c_uint32),
                          ('intern', [ct.c_char_p, ct.c_uint32, ct.POINTER(ct.c_uint32)], ct.c_uint32),
                          ('intern_hinted', [ct.c_char_p, ct.c_uint32, ct.c_uint32,
                                             ct.POINTER(ct.c_uint32)], ct.c_uint32),
                          ('known', [], ct.c_uint32),
                          ('resolve', [ct.c_uint32, ct.c_char_p, ct.c_uint32], ct.c_uint32),
                          ('get', [ct.c_uint32], ct.c_uint32)):
        f = getattr(lib, 'fx_' + fn)
        f.argtypes = args
        f.restype = ret
    return lib


class PoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='uis-')
        cls.lib = build(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.lib.fx_reset()

    def get(self, f): return self.lib.fx_get(f)

    def intern(self, s, expect=OK):
        off = ct.c_uint32(0xDEADBEEF)
        b = s.encode()
        self.assertEqual(self.lib.fx_intern(b, len(b), ct.byref(off)), expect)
        return off.value

    def hinted(self, s, at, expect=OK):
        off = ct.c_uint32(0xDEADBEEF)
        b = s.encode()
        self.assertEqual(self.lib.fx_intern_hinted(b, len(b), at, ct.byref(off)), expect)
        return off.value

    def reads(self, fn):
        before = self.get(BYTE_READS)
        out = fn()
        return out, self.get(BYTE_READS) - before

    def resolve(self, off):
        buf = ct.create_string_buffer(64)
        if not self.lib.fx_resolve(off, buf, 64):
            return None
        return buf.value.decode()

    def stock_sample(self):
        return [(o, self.resolve(o)) for o in (0, 3, 1000, 50000, STOCK_LEN - 20)]

    # ---- the stock pool -------------------------------------------------
    def test_a_stock_string_is_found_where_it_is_and_nothing_moves(self):
        self.assertEqual(self.intern('Footer05'), 1000)
        self.assertEqual((self.get(POOL), self.get(LEN), self.get(ALLOCS)),
                         (STOCK_POOL, STOCK_LEN, 0))

    def test_the_first_new_string_copies_the_pool_once_and_switches_the_reader(self):
        before = self.stock_sample()
        off = self.intern('MV_fpLossless')
        pool = self.get(POOL)
        self.assertNotEqual(pool, STOCK_POOL)
        self.assertEqual(self.get(ALLOCS), 1)
        self.assertGreaterEqual(off, STOCK_LEN)
        self.assertEqual(off % 4, 0)
        self.assertEqual(self.resolve(off), 'MV_fpLossless')
        self.assertEqual(self.stock_sample(), before, 'a stock offset changed meaning')
        # the convention's header, immediately before the pool
        self.assertEqual([self.lib.fx_peek(pool - 16 + 4 * i) for i in (0, 1, 3)], [MAGIC, 1, 0])
        self.assertGreaterEqual(self.lib.fx_peek(pool - 8), off + 14 + HEADROOM)
        self.assertEqual(self.get(LEN), off + 14)
        self.assertEqual(self.lib.fx_known(), 1)
        self.assertEqual(self.get(OOB), 0)

    def test_the_pointer_is_switched_before_the_length(self):
        self.intern('MV_fpLossless')
        log = [self.get(100 + i) for i in range(self.get(LOG_N))]
        self.assertEqual(log, [0x14, 0x10])

    # ---- many at once (Sensor Lab's installer) -----------------------------
    def many(self, strings, expect=OK, stock_at=0):
        arr = (ct.c_char_p * len(strings))(*[t.encode() for t in strings])
        offs = (ct.c_uint32 * len(strings))(*([0xDEADBEEF] * len(strings)))
        self.assertEqual(self.lib.fx_intern_many(arr, len(strings), ct.c_uint32(stock_at), offs), expect)
        return list(offs)

    def test_many_not_stock_reads_only_what_was_appended(self):
        self.intern('MV_fpLossless')                              # another sup's string
        full, full_reads = self.reads(lambda: self.many(['SL_X', 'MV_fpLossless']))
        self.lib.fx_reset(); self.intern('MV_fpLossless')
        fast, fast_reads = self.reads(lambda: self.many(['SL_X', 'MV_fpLossless'], stock_at=NOT_STOCK))
        self.assertEqual([self.resolve(o) for o in fast], ['SL_X', 'MV_fpLossless'])
        self.assertEqual(fast, full)                               # the same offsets
        self.assertLess(fast_reads * 100, full_reads)              # without the stock scan

    def test_many_agree_with_one_by_one_and_publish_once(self):
        words = ['Footer05', 'SL_A', 'Width / Height', 'SL_A', '3:2+', 'MV_fpLossless']
        offs = self.many(words)
        many_pub = self.get(PUBLISHES)
        self.assertEqual([self.resolve(o) for o in offs], words)
        self.assertEqual(offs[0], 1000)                            # stock: where it is
        self.assertEqual(offs[1], offs[3])                         # repeated: one copy
        self.assertTrue(all(o % 4 == 0 for o in offs[1:]))
        self.assertEqual(self.get(OOB), 0)
        # the same strings one by one, from a fresh camera: same layout, more publishes
        self.lib.fx_reset()
        one = [self.intern(w) for w in words]
        self.assertEqual(one, offs)
        self.assertLess(many_pub, self.get(PUBLISHES))
        self.assertLessEqual(many_pub, 4)                          # grow (2) + append (2)
        # and once the copy exists: exactly one pair for a whole batch
        before = self.get(PUBLISHES)
        more = self.many(['SL_B', 'SL_C', 'SL_D'])
        self.assertEqual(self.get(PUBLISHES) - before, 2)
        self.assertEqual([self.resolve(o) for o in more], ['SL_B', 'SL_C', 'SL_D'])

    def test_many_failing_changes_nothing(self):
        self.intern('MV_fpLossless')
        length = self.get(LEN)
        self.lib.fx_alloc_fail(1)
        self.many(['x' * 40] * 1 + ['y' * 60000], expect=3)        # does not fit: no memory
        self.assertEqual(self.get(LEN), length)
        self.many(['ok', ''], expect=4)                            # empty string: invalid
        self.assertEqual(self.get(LEN), length)

    # ---- hinted: the builder knows the stock pool ---------------------------
    def test_a_right_stock_hint_is_checked_and_nothing_is_scanned(self):
        unhinted, full = self.reads(lambda: self.intern('Footer05'))
        self.setUp()
        off, cost = self.reads(lambda: self.hinted('Footer05', 1000))
        self.assertEqual(off, unhinted)
        self.assertLessEqual(cost, len('Footer05') + 1)
        self.assertGreater(full, 1000, 'the unhinted call should have scanned')
        self.assertEqual((self.get(POOL), self.get(ALLOCS)), (STOCK_POOL, 0))

    def test_a_wrong_stock_hint_falls_back_to_the_scan(self):
        for hint in (999, 1001, STOCK_LEN - 4, STOCK_LEN + 100):
            with self.subTest(hint=hint):
                self.setUp()
                self.assertEqual(self.hinted('Footer05', hint), 1000)
                self.assertEqual(self.get(ALLOCS), 0)

    def test_a_prefix_is_not_a_hit(self):
        # "Footer0" at 1000 is followed by '5', not NUL
        off = self.hinted('Footer0', 1000)
        self.assertNotEqual(off, 1000)
        self.assertEqual(self.resolve(off), 'Footer0')

    def test_not_stock_scans_only_what_was_appended(self):
        a, first = self.reads(lambda: self.hinted('MV_fpLossless', NOT_STOCK))
        self.assertEqual(self.resolve(a), 'MV_fpLossless')
        self.assertLess(first, 1000, 'the stock bytes were scanned')
        b, again = self.reads(lambda: self.hinted('MV_fpLossless', NOT_STOCK))
        self.assertEqual(b, a)
        self.assertLess(again, 1000)
        self.assertEqual(self.get(ALLOCS), 1)

    def test_hinted_and_unhinted_sups_agree(self):
        a = self.intern('Lossless RAW')                 # an old sup, no hints
        self.assertEqual(self.hinted('Lossless RAW', NOT_STOCK), a)
        b = self.hinted('OG3K', NOT_STOCK)              # a new sup first
        self.assertEqual(self.intern('OG3K'), b)
        self.assertEqual(self.intern('Footer05'), self.hinted('Footer05', 1000))

    # ---- several sups, any order -----------------------------------------
    def test_later_strings_are_appended_in_the_same_copy(self):
        a = self.intern('MV_fpLossless')
        pool = self.get(POOL)
        b = self.intern('OG3K')
        c = self.intern('Lossless RAW')
        self.assertEqual(self.get(POOL), pool)
        self.assertEqual(self.get(ALLOCS), 1)
        self.assertTrue(a < b < c)
        for off, s in ((a, 'MV_fpLossless'), (b, 'OG3K'), (c, 'Lossless RAW')):
            self.assertEqual(self.resolve(off), s)

    def test_a_string_that_only_begins_like_a_stock_one_is_its_own_entry(self):
        off = self.intern('Footer0')                # stock has "Footer05" at 1000
        self.assertNotEqual(off, 1000)
        self.assertEqual(self.resolve(off), 'Footer0')

    def test_the_same_string_from_two_sups_is_one_entry(self):
        a = self.intern('Lossless RAW')
        length = self.get(LEN)
        self.assertEqual(self.intern('Lossless RAW'), a)
        self.assertEqual(self.get(LEN), length)

    def test_a_string_that_ends_an_existing_one_is_reused(self):
        a = self.intern('SUB_MV_fpLossless')
        self.assertEqual(self.intern('MV_fpLossless'), a + 4)

    def test_full_room_moves_to_a_bigger_copy_and_keeps_every_offset(self):
        first = self.intern('first')
        old = self.get(POOL)
        offsets = [(first, 'first')]
        i = 0
        while self.get(POOL) == old and i < 100:
            s = ('%04d' % i) * 250                # 1000 bytes: fills 16 KiB fast
            offsets.append((self.intern(s), s))
            i += 1
        self.assertNotEqual(self.get(POOL), old, 'never moved to a bigger copy')
        self.assertEqual(self.get(ALLOCS), 2)
        for off, s in offsets:
            self.assertEqual(self.resolve(off), s[:63])
        # the old copy is left as it was: the UI may still borrow from it
        self.assertEqual(self.lib.fx_peek(old - 16), MAGIC)
        self.assertEqual(bytes(self.lib.fx_byte(old + first + k) for k in range(5)), b'first')

    # ---- what is not ours ------------------------------------------------
    def test_a_pool_nobody_announced_is_left_alone(self):
        r = self.get(READER)
        for case, (addr, value) in {
                'other address': (r + 0x14, 0x12345678),
                'stock address, other length': (r + 0x10, STOCK_LEN + 8)}.items():
            with self.subTest(case=case):
                self.setUp()
                self.lib.fx_word(addr, value)
                pool, length = self.get(POOL), self.get(LEN)
                self.intern('MV_fpLossless', UNKNOWN_POOL)
                self.assertEqual((self.get(POOL), self.get(LEN), self.get(ALLOCS)), (pool, length, 0))

    def test_a_header_of_another_version_is_not_ours(self):
        self.intern('a')
        pool = self.get(POOL)
        self.lib.fx_word(pool - 12, 2)
        length = self.get(LEN)
        self.intern('b', UNKNOWN_POOL)
        self.assertEqual(self.get(LEN), length)

    def test_no_ui_no_change(self):
        self.lib.fx_word(0xC37B7048, 0)
        self.intern('a', NO_READER)
        self.lib.fx_reset()
        self.lib.fx_word(self.get(READER) + 0x24, 0xC18C0000)
        self.intern('a', NO_READER)

    def test_no_memory_changes_nothing(self):
        self.lib.fx_alloc_fail(1)
        self.intern('MV_fpLossless', NO_MEMORY)
        self.assertEqual((self.get(POOL), self.get(LEN)), (STOCK_POOL, STOCK_LEN))

    def test_bad_arguments(self):
        off = ct.c_uint32()
        self.assertEqual(self.lib.fx_intern(b'', 0, ct.byref(off)), INVALID)
        self.assertEqual(self.lib.fx_intern(b'a\0b', 3, ct.byref(off)), INVALID)
        self.assertEqual(self.get(ALLOCS), 0)


class PoolMutationTests(unittest.TestCase):
    MUTATIONS = {
        'does not copy the stock bytes': ('    copy(fresh, pool, len);', '    copy(fresh, pool, len - 4096);'),
        'switches the length first': (
            '    poke(reader + READER_POOL, fresh);',
            '    poke(reader + READER_LEN, len + 1);\n    poke(reader + READER_POOL, fresh);'),
        'forgets the header magic': ('    poke(block + 0, UIS_MAGIC);\n', ''),
        'matches without the terminating NUL': (
            '        if (k == n && peek8(pool + i + n) == 0) return i;',
            '        if (k == n) return i;'),
        'accepts any pool': (
            '    if (!uis_pool_known(reader)) return UIS_UNKNOWN_POOL;\n', ''),
        'never grows': ('    if (end > capacity) {', '    if (end > capacity && capacity == 0) {'),
        'forgets to terminate': ('    poke8(pool + at + n, 0);\n', ''),
        'returns a hint unchecked': (
            '        while (k < n && peek8(pool + stock_at + k) == (uint8_t)s[k]) ++k;\n'
            '        if (k == n && peek8(pool + stock_at + n) == 0)',
            '        (void)k;\n        if (1)'),
        'takes a prefix for a hit': (
            '        if (k == n && peek8(pool + stock_at + n) == 0) { *offset = stock_at; return UIS_OK; }',
            '        if (k == n) { *offset = stock_at; return UIS_OK; }'),
        'not-stock still scans the stock bytes': (
            '        from = UIS_STOCK_LEN;               /* the stock bytes never change */', '        from = 0;'),
        'the scan ignores where to start': ('    for (uint32_t i = from; i + n < len; ++i) {',
                                            '    for (uint32_t i = 0 * from; i + n < len; ++i) {'),
        'does not publish the length': (
            '    poke(reader + READER_LEN, end);             /* the new string exists now */\n', ''),
    }

    def test_every_mutation_is_caught(self):
        with tempfile.TemporaryDirectory(prefix='uis-mut-') as tmp:
            for index, (name, seam) in enumerate(self.MUTATIONS.items()):
                with self.subTest(mutation=name):
                    lib = build(tmp, seam, f'm{index}.dylib')

                    class Against(PoolTests):
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
    def test_it_builds_for_the_camera_without_relocations_outside_text(self):
        clang = shutil.which('clang') or 'clang'
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / 'p.o'
            subprocess.run([clang, '--target=armv7a-none-eabi', '-mcpu=cortex-a9', '-mthumb',
                            '-mfloat-abi=soft', '-ffreestanding', '-fno-builtin', '-nostdlib',
                            '-fropi', '-fno-addrsig', '-O2', '-std=c11', '-Wall', '-Wextra',
                            '-Werror', '-c', str(HERE / 'ui_pool.c'), '-o', str(out)],
                           check=True, capture_output=True, text=True)
            heads = subprocess.run(['objdump', '-h', str(out)], capture_output=True, text=True).stdout
            self.assertNotIn('.rodata', heads)
            relocs = subprocess.run(['objdump', '-r', str(out)], capture_output=True, text=True).stdout
            self.assertNotIn('[.text]', relocs)


if __name__ == '__main__':
    unittest.main()
