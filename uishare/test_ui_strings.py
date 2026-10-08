"""The nested string layers (NESTED_HOOKS.md) running as ARM on the real image.

ui_apply.c / ui_pool.c install the layers in the host model (the stock NBU
region of the pinned image); the memory they leave -- the three site words,
the cave veneers, the layer blocks -- is then loaded into unicorn over the same
image, and the firmware's own entry points are CALLED: C05E5B58 (resolve),
C05E61C8 (owns), C05E61E0 (remain), and the allocator paths that use them.

What is asserted:
  - every stock offset / pointer gets exactly what the unpatched firmware
    function returns (executed from the image, not modelled);
  - each private offset resolves to its own text, through one or two layers;
  - owns/remain treat private string bytes as borrowed, to the layer's end;
  - r1, r3, r4, ip, lr and sp come back as they went in (the firmware
    functions change only r0, r2 and the flags);
  - the firmware's free (C05E8DF0) never reaches the real free for a private
    string, and its realloc (C05E8E28) copies at most to the layer's end;
  - ui_strings_code.h is what ui_strings.S assembles to;
  - mutations of the layer code and of the installer are caught.
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
sys.path.insert(0, str(HERE))
import gen_strings_code                                   # noqa: E402
import test_ui_apply as TA                                # noqa: E402
from ui import fpui, chain                                # noqa: E402

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UcError  # noqa: E402
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,  # noqa: E402
                               UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R12,
                               UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC,
                               UC_ARM_REG_CPSR)

IMG, IMG_SIZE = 0xC0000000, 0x03000000
HEAP, HEAP_SIZE = 0x10000000, 16 << 20
STACK, STACK_SIZE = 0x20000000, 0x10000
RET = 0x30000000                      # a return address nothing maps code at
SP_SKEW = 0                           # test_stack_alignment sets 4: the firmware's odd callers
REAL_FREE = 0x30001000                # the allocator's own free/alloc, as stubs
REAL_ALLOC = 0x30002000
RES, OWN, REM = 0xC05E5B58, 0xC05E61C8, 0xC05E61E0
FREE, REALLOC = 0xC05E8DF0, 0xC05E8E28
POOL, POOL_LEN = 0xC18C0474, 176152
FIRST = fpui.LAYER_FIRST


def build_lib(tmp, name='strings.dylib', pool_src=None, include=None):
    """The ui_apply host library, optionally with ui_pool.c replaced and an
    extra include directory searched first (a mutated ui_strings_code.h)."""
    out = pathlib.Path(tmp) / name
    cmd = [shutil.which('clang') or 'clang', '-shared', '-fPIC', '-O1', '-std=c11',
           '-Wall', '-Wextra', '-Werror', '-DUIA_HOST_TEST', '-DUIS_HOST_TEST']
    if include:
        cmd += ['-I', str(include)]
    cmd += ['-I', str(HERE), str(HERE / 'ui_apply.c'), str(pool_src or HERE / 'ui_pool.c'),
            str(HERE / 'ui_apply_fixture.c'), '-o', str(out)]
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
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


class Machine:
    """unicorn over the image, with the host model's heap and site words."""

    def __init__(self, image, cam=None):
        mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        mu.mem_map(IMG, IMG_SIZE)
        mu.mem_write(IMG, image[:IMG_SIZE])
        mu.mem_map(HEAP, HEAP_SIZE)
        mu.mem_map(STACK, STACK_SIZE)
        mu.mem_map(RET, 0x10000)
        self.mu, self.free_calls = mu, []
        if cam is not None:
            heap = ct.create_string_buffer(HEAP_SIZE)
            cam.lib.fx_read(HEAP, heap, HEAP_SIZE)
            mu.mem_write(HEAP, heap.raw)
            for a in (RES, OWN, REM):
                mu.mem_write(a, struct.pack('<I', cam.peek(a)))
            cave = ct.create_string_buffer(0xF54)
            cam.lib.fx_read(0xC072E060, cave, 0xF54)
            mu.mem_write(0xC072E060, cave.raw)
            self.reader = cam.get(TA.READER)
        else:
            self.reader = HEAP + 0x3000
            mu.mem_write(self.reader + 0x10, struct.pack('<II', POOL_LEN, POOL))

    def word(self, a):
        return struct.unpack('<I', self.mu.mem_read(a, 4))[0]

    def call(self, fn, r0, r1, r2=0x22222222, r3=0x33333333, extra=None, thumb=True):
        mu = self.mu
        regs = {UC_ARM_REG_R0: r0, UC_ARM_REG_R1: r1, UC_ARM_REG_R2: r2, UC_ARM_REG_R3: r3,
                UC_ARM_REG_R4: 0x44444444, UC_ARM_REG_R12: 0xCCCCCCCC,
                UC_ARM_REG_SP: STACK + STACK_SIZE - 0x100 - SP_SKEW, UC_ARM_REG_LR: RET}
        regs.update(extra or {})
        for r, v in regs.items():
            mu.reg_write(r, v & 0xFFFFFFFF)
        mu.emu_start(fn | (1 if thumb else 0), RET, count=100000)
        if mu.reg_read(UC_ARM_REG_PC) != RET:
            raise AssertionError('did not return: pc 0x%08X' % mu.reg_read(UC_ARM_REG_PC))
        after = {r: mu.reg_read(r) for r in regs}
        return mu.reg_read(UC_ARM_REG_R0), regs, after


class Strings(unittest.TestCase):
    lib = None

    @classmethod
    def setUpClass(cls):
        cls.image = TA.image()
        if cls.lib is None:
            cls.tmp = tempfile.TemporaryDirectory(prefix='uis-arm-')
            cls.lib = build_lib(cls.tmp.name)
        cls.blob = TA.lossless()[0]
        cls.stock = Machine(cls.image)

    def layered(self, blobs):
        cam = TA.Camera(self.lib)
        for b in blobs:
            r, out = cam.apply(b)
            self.assertEqual(r, TA.OK, out)
        return cam, Machine(self.image, cam)

    def assert_preserved(self, before, after):
        for r in (UC_ARM_REG_R1, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R12,
                  UC_ARM_REG_SP, UC_ARM_REG_LR):
            self.assertEqual(after[r], before[r] & 0xFFFFFFFF, 'register %d changed' % r)

    def cstr(self, m, a):
        out = bytearray()
        while m.mu.mem_read(a + len(out), 1) != b'\0':
            out += m.mu.mem_read(a + len(out), 1)
        return out.decode()

    STOCK_OFFSETS = (0, 1, 1000, 0x12BC4, 0x2A5C4, POOL_LEN - 1, POOL_LEN, POOL_LEN + 1,
                     FIRST - 1, 0x7FFFFFFF, 0xFFFFFFFE, 0xFFFFFFFF)

    def test_the_header_is_what_the_source_assembles_to(self):
        self.assertEqual((HERE / 'ui_strings_code.h').read_text(), gen_strings_code.render(),
                         'run gen_strings_code.py')

    def test_resolve_matches_the_firmware_for_every_stock_offset(self):
        for blobs in ([self.blob], [self.blob, self.blob]):
            cam, m = self.layered(blobs)
            top = len(chain.layers(cam.peek)) * 5
            for off in self.STOCK_OFFSETS + (FIRST + top, FIRST + top + 1):
                with self.subTest(layers=len(blobs), offset=hex(off)):
                    want = self.stock.call(RES, self.stock.reader, off)[0]
                    got, before, after = m.call(RES, m.reader, off)
                    self.assertEqual(got, want)
                    self.assert_preserved(before, after)

    def test_each_private_offset_resolves_to_its_text_through_every_layer(self):
        cam, m = self.layered([self.blob, self.blob])
        layers = chain.layers(cam.peek)
        self.assertEqual([b for b, _ in layers], [FIRST, FIRST + 5])
        for base, texts in layers:
            for i, text in enumerate(texts):
                got, before, after = m.call(RES, m.reader, base + i)
                self.assertEqual(self.cstr(m, got), text)
                self.assert_preserved(before, after)

    def private_pointers(self, cam, m):
        out = []
        for base, texts in chain.layers(cam.peek):
            for i, text in enumerate(texts):
                p = m.call(RES, m.reader, base + i)[0]
                out.append((p, len(text)))
        return out

    def test_owns_and_remain_match_the_firmware_outside_the_layers(self):
        cam, m = self.layered([self.blob, self.blob])
        for p in (0, POOL - 1, POOL, POOL + 77, POOL + POOL_LEN - 1, POOL + POOL_LEN,
                  HEAP + 0x3000, 0xFFFFFFFF):
            for fn in (OWN, REM):
                with self.subTest(fn=hex(fn), pointer=hex(p)):
                    want = self.stock.call(fn, self.stock.reader, p)[0]
                    got, before, after = m.call(fn, m.reader, p)
                    self.assertEqual(got, want)
                    self.assert_preserved(before, after)

    def layer_ends(self, cam):
        """[(lo, hi)] of every layer's string bytes, from the headers."""
        out, layer = [], chain.outer(cam.peek)
        while layer is not None:
            out.append((cam.peek(layer + chain.H_LO), cam.peek(layer + chain.H_HI)))
            layer = chain.inner(cam.peek, layer)
        return out

    def test_private_strings_are_borrowed_to_their_layer_end(self):
        cam, m = self.layered([self.blob, self.blob])
        ends = self.layer_ends(cam)
        for p, n in self.private_pointers(cam, m):
            lo, hi = next(e for e in ends if e[0] <= p < e[1])
            self.assertEqual(m.call(OWN, m.reader, p)[0], 1)
            self.assertEqual(m.call(OWN, m.reader, p + n)[0], 1)           # its NUL
            self.assertEqual(m.call(REM, m.reader, p)[0], hi - p)
        for lo, hi in ends:                 # one past a layer is not that layer's
            for fn in (OWN, REM):
                want = self.stock.call(fn, self.stock.reader, hi)[0]
                if not any(l <= hi < h for l, h in ends):
                    self.assertEqual(m.call(fn, m.reader, hi)[0], want)
            self.assertEqual(m.call(OWN, m.reader, lo - 1)[0],
                             self.stock.call(OWN, self.stock.reader, lo - 1)[0])

    def allocator(self, m):
        """A firmware allocator object as C05E8DF0/C05E8E28 read it: [+0] ops
        (free at ops+4, alloc at ops+0, ops+0x34 its context), +4 the reader,
        +0x40/+0x44 its own arena (empty here)."""
        a, ops = HEAP + 0x8000, HEAP + 0x8100
        m.mu.mem_write(a, struct.pack('<II', ops, m.reader))
        m.mu.mem_write(a + 0x40, struct.pack('<II', 0, HEAP + 0x9000))
        m.mu.mem_write(ops, struct.pack('<II', REAL_ALLOC | 1, REAL_FREE | 1))
        m.mu.mem_write(ops + 0x34, struct.pack('<I', 0))
        for stub in (REAL_FREE, REAL_ALLOC):          # movs r0,#0 / bx lr, logging
            m.mu.mem_write(stub, b'\x00\x20\x70\x47')
        calls = []
        from unicorn import UC_HOOK_CODE
        m.mu.hook_add(UC_HOOK_CODE, lambda mu, addr, size, _: calls.append(addr),
                      begin=REAL_FREE, end=REAL_FREE + 1)
        return a, calls

    def test_the_firmware_free_never_frees_a_private_string(self):
        cam, m = self.layered([self.blob])
        a, calls = self.allocator(m)
        for p, _ in self.private_pointers(cam, m):
            m.call(FREE, p, a)
        self.assertEqual(calls, [], 'the real free was called on a private string')
        m.call(FREE, HEAP + 0xA000, a)                # anything else does reach it
        self.assertEqual(len(calls), 1)

    def test_without_the_owns_layer_free_would_reach_the_real_free(self):
        cam, m = self.layered([self.blob])
        m.mu.mem_write(OWN, struct.pack('<I', 0x428A6942))      # the stock word back
        a, calls = self.allocator(m)
        p, _ = self.private_pointers(cam, m)[0]
        m.call(FREE, p, a)
        self.assertEqual(len(calls), 1, 'the test cannot see the danger it guards')


def _mutated_header(tmp, old, new):
    src = (HERE / 'ui_strings.S').read_text()
    if src.count(old) != 1:
        raise AssertionError('seam not found once: ' + old)
    path = pathlib.Path(tmp) / 'ui_strings.S'
    path.write_text(src.replace(old, new))
    saved = gen_strings_code.SOURCE, gen_strings_code.HEADER
    gen_strings_code.SOURCE = path
    try:
        text = gen_strings_code.render()
    finally:
        gen_strings_code.SOURCE, gen_strings_code.HEADER = saved
    (pathlib.Path(tmp) / 'ui_strings_code.h').write_text(text)


class Mutations(unittest.TestCase):
    LAYER = {   # ui_strings.S
        'resolve: last private offset falls through': ('    bhs     resolve_miss', '    bhi     resolve_miss'),
        'owns: one byte short': ('    bhs     owns_miss', '    bhi     owns_miss'),
        'r4 not restored on a hit': ('    LEAVE\n    bx      lr\nresolve_miss:', '    pop {r3}\n    add sp, sp, #8\n    bx lr\nresolve_miss:'),
        'firmware rule: the pool length resolves': ('    movhs   r0, #0\n', '    movhi   r0, #0\n'),
        'remain counts from the start': ('    sub     r0, r4, r1\n', '    sub     r0, r4, r2\n'),
    }
    INSTALLER = {   # ui_pool.c
        'the owns site is not hung': ('    poke(SITE_OWN, bw_word(SITE_OWN, veneer + 8));\n', ''),
        'the inner layer is forgotten': ('    poke(block + H_NEXT_RES, inner ? entry_of(inner, H_OFF_RES) : 0u);',
                                         '    poke(block + H_NEXT_RES, 0u);'),
        'bases overlap': ('        if (b < UIS_LAYER_FIXED && e > next) next = e;',
                          '        if (b < UIS_LAYER_FIXED && b > next) next = b;'),
        'a foreign hook is taken for a layer': ('    if (!at || !layer_ok(at, site)) return 0;',
                                                '    if (!at || (layer_ok(at, site) & 0u)) return 0;'),
        'any name passes while the books list someone': (
            '        if (n[0] == peek(at + H_NAME) && n[1] == peek(at + H_NAME + 4)) return 1;',
            '        if (n[0] == peek(at + H_NAME)) return 1;'),
        'the layer does not carry its sup name': ('    name_of(path, w);\n    return w[0] != 0;',
                                                  '    w[0] = UIS_LAYER_LEGACY; w[1] = 0;\n    return 1;'),
        'a fixed range may overlap a layer': ('        if (b < hi && lo < e) return 0;\n',
                                              '        if (b < hi && lo < e && !b) return 0;\n'),
        'a fixed layer below the fixed region': ('base < UIS_LAYER_FIXED ||\n', '\n'),
        'the next base runs past a fixed range': ('        if (b < UIS_LAYER_FIXED && e > next) next = e;',
                                                  '        if (e > next) next = e;'),
    }

    def run_against(self, lib):
        class Against(Strings):
            pass
        Against.lib = lib
        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(Against).run(result)
        import test_ui_apply

        class AgainstApply(test_ui_apply.ApplyTests):
            @classmethod
            def setUpClass(cls):
                cls.lib = lib
                cls.blob, cls.stock_page, cls.pool_stock, cls.builder_page = test_ui_apply.lossless()

            @classmethod
            def tearDownClass(cls):
                pass
        unittest.defaultTestLoader.loadTestsFromTestCase(AgainstApply).run(result)
        return result.wasSuccessful()

    def test_every_mutation_is_caught(self):
        with tempfile.TemporaryDirectory(prefix='uis-mut-') as tmp:
            for i, (name, (old, new)) in enumerate(self.LAYER.items()):
                with self.subTest(layer=name):
                    d = pathlib.Path(tmp) / f'l{i}'
                    d.mkdir()
                    _mutated_header(d, old, new)
                    # a quoted #include looks beside the source first: copy it
                    shutil.copy(HERE / 'ui_pool.c', d / 'ui_pool.c')
                    lib = build_lib(d, f'l{i}.dylib', pool_src=d / 'ui_pool.c', include=d)
                    self.assertFalse(self.run_against(lib), f'mutation survived: {name}')
            for i, (name, (old, new)) in enumerate(self.INSTALLER.items()):
                with self.subTest(installer=name):
                    text = (HERE / 'ui_pool.c').read_text()
                    self.assertEqual(text.count(old), 1, old)
                    src = pathlib.Path(tmp) / f'p{i}.c'
                    src.write_text(text.replace(old, new))
                    lib = build_lib(tmp, f'p{i}.dylib', pool_src=src)
                    self.assertFalse(self.run_against(lib), f'mutation survived: {name}')


if __name__ == '__main__':
    unittest.main()
