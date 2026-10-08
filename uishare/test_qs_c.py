#!/usr/bin/env python3
"""qs_apply.c against its reference ui/qs.py, byte for byte.

    python3 -B -m unittest test_qs_c

The C is compiled twice: for the host (QS_HOST_TEST, run here through ctypes
on the real stock records and the real table from ui/qs.py encode()), and for
the camera (Thumb, -fropi) to prove it has no relocation and no data section,
so it can be compiled into any sup's block.
"""
import ctypes
import itertools
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'research' / 'ui' / 'tools' / 'arm_text'))
from ui import qs                                  # noqa: E402

CLANG = shutil.which('clang') or 'clang'
ARM_FLAGS = ['--target=armv7a-none-eabi', '-mcpu=cortex-a9', '-mthumb', '-mfloat-abi=soft',
             '-mfpu=none', '-ffreestanding', '-fno-builtin', '-nostdlib', '-fno-jump-tables',
             '-fropi', '-fno-addrsig', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror']
OUTCOME = {0: qs.SKIP, 1: qs.DONE, 2: qs.WROTE, 3: qs.CONFLICT, 4: 'error'}


def host_lib(tmp):
    so = pathlib.Path(tmp) / 'qs.so'
    r = subprocess.run([CLANG, '-shared', '-fPIC', '-O1', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-DQS_HOST_TEST', str(HERE / 'qs_apply.c'), '-o', str(so)],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    lib = ctypes.CDLL(str(so))
    lib.qs_layer.restype = ctypes.c_uint32
    lib.qs_layer.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p, ctypes.c_uint32,
                             ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    lib.qs_find.restype = ctypes.c_uint32
    lib.qs_find.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.qs_fnv.restype = ctypes.c_uint32
    lib.qs_fnv.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.qs_n_supported.restype = ctypes.c_uint32
    lib.qs_n_supported.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    return lib


class C:
    lib = None
    tmp = None
    table = None

    @classmethod
    def get(cls):
        if cls.lib is None:
            cls.tmp = tempfile.TemporaryDirectory(prefix='qsc-')
            cls.lib = host_lib(cls.tmp.name)
            cls.table = ctypes.create_string_buffer(qs.encode())
        return cls


def c_chain(row, layers):
    """qs.chain, through qs_apply.c."""
    c = C.get()
    idx = c.lib.qs_find(c.table, int(row['address'], 16))
    assert idx, row['address']
    stock = qs.stock(row)
    sbuf = ctypes.create_string_buffer(stock, len(stock))
    cur = bytes(stock)
    seen = []
    for n, k, ids in layers:
        cbuf = ctypes.create_string_buffer(cur, max(len(cur), 1))
        ln = ctypes.c_uint32(len(cur))
        scratch = ctypes.create_string_buffer(512)
        arr = (ctypes.c_uint32 * 4)(*[ids[i] for i in range(4)])
        out = c.lib.qs_layer(c.table, idx, sbuf, cbuf, ctypes.byref(ln), scratch, n, k, arr)
        what = OUTCOME[out]
        if what == qs.WROTE:
            cur = scratch.raw[:ln.value]
        seen.append(what)
    return cur, seen


def py_chain(row, layers):
    try:
        return qs.chain(row, layers)
    except qs.QsError:
        return None


SCENARIOS = []
for n in (3, 4, 5, 8):
    ks = list(range(2, n))
    for perm in itertools.islice(itertools.permutations(ks), 6):
        SCENARIOS.append([(n, k, {0: 0x40000010, 1: 0x40000011, 2: 0x40000012, 3: 0x40000100 + k})
                          for k in perm])
SCENARIOS.append([(3, 2, {0: 1, 1: 2, 2: 3, 3: 4})] * 2)                  # twice
SCENARIOS.append([(8, k, {0: 0xFFFFFFF2, 1: 0xFFFFFFF3, 2: 0xFFFFFFF4, 3: 0x7000 + k})
                  for k in range(2, 8)])


class TestTwin(unittest.TestCase):
    def test_every_record_every_scenario(self):
        n = 0
        for row in qs.recipe()['records']:
            for layers in SCENARIOS:
                want = py_chain(row, layers)
                got = c_chain(row, layers)
                self.assertIsNotNone(want, row['address'])
                self.assertEqual(got, want, f'{row["address"]} {[(a, b) for a, b, _ in layers]}')
                n += 1
        self.assertEqual(n, len(qs.recipe()["records"]) * len(SCENARIOS))

    def test_conflict(self):
        row = next(r for r in qs.recipe()['records'] if r['rule'] == 'clone')
        c = C.get()
        idx = c.lib.qs_find(c.table, int(row['address'], 16))
        stock = qs.stock(row)
        cur = bytearray(stock)
        cur[20] ^= 1
        ln = ctypes.c_uint32(len(cur))
        arr = (ctypes.c_uint32 * 4)(1, 2, 3, 4)
        out = c.lib.qs_layer(c.table, idx, ctypes.create_string_buffer(stock, len(stock)),
                             ctypes.create_string_buffer(bytes(cur), len(cur)), ctypes.byref(ln),
                             ctypes.create_string_buffer(512), 3, 2, arr)
        self.assertEqual(OUTCOME[out], qs.CONFLICT)

    def test_find_and_fnv(self):
        c = C.get()
        rows = qs.recipe()['records']
        for i, row in enumerate(rows):
            self.assertEqual(c.lib.qs_find(c.table, int(row['address'], 16)), i + 1)
            st = qs.stock(row)
            self.assertEqual(c.lib.qs_fnv(st, len(st)), int(row['fnv'], 16))
        self.assertEqual(c.lib.qs_find(c.table, 0xC2278C02), 0)

    def test_n_supported(self):
        c = C.get()
        v = ctypes.c_uint32()
        for n in range(0, 12):
            ok = c.lib.qs_n_supported(c.table, n, ctypes.byref(v))
            self.assertEqual(bool(ok), str(n) in qs.recipe()['fn'], n)
            if ok:
                self.assertEqual(bool(v.value), qs.recipe()['fn'][str(n)]['verified'], n)

    def test_unsupported_n_is_an_error(self):
        row = qs.recipe()['records'][0]
        got, seen = c_chain(row, [(9, 2, {0: 1, 1: 2, 2: 3, 3: 4})])
        self.assertEqual(seen, ['error'])


class TestCamera(unittest.TestCase):
    def test_thumb_ropi_has_no_relocation(self):
        import elf_text
        with tempfile.TemporaryDirectory(prefix='qsarm-') as t:
            obj = pathlib.Path(t) / 'qs.o'
            r = subprocess.run([CLANG, *ARM_FLAGS, '-c', str(HERE / 'qs_apply.c'), '-o', str(obj)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = elf_text.load_text(obj)            # raises on any relocation / data
        for f in ('qs_layer', 'qs_find', 'qs_fnv', 'qs_n_supported'):
            self.assertTrue(text.functions[f] & 1, f)
        self.assertLess(len(text.code), 2048)


if __name__ == '__main__':
    unittest.main()
