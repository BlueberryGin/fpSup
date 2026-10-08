#!/usr/bin/env python3
"""Two builds of uishare on one card (string layer v5, 2026-10-07).

    python3 -B -m unittest test_layer_compat

A layer describes itself: its header holds its length and its entries'
offsets, and the word before every entry is that entry's offset. So a sup
built from a later uishare whose code moved (other entry offsets) and one
built from today's recognise each other's layers, walk the chain and find the
QS shared ids -- no rebuild of the others because uishare's code changed.

The host model links TWO builds into one library: today's, and one from a
ui_strings.S with padding before `owns` (owns/remain move; resolve stays
right after the header, as gen_strings_code.py requires). The middle sup is
applied with build B, the others with A; then the chain is read back (ui/
chain.py, which also reads only header offsets) and run in unicorn (the ARM
layer code of both builds, through the firmware sites).
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
from ui import chain                      # noqa: E402
import gen_strings_code                   # noqa: E402
import test_ui_apply as TA                # noqa: E402
import test_qs_option as TQO              # noqa: E402

PUBLIC = ['uia_apply', 'uia_apply_in_arena', 'uis_reader', 'uis_pool_known', 'uis_intern', 'uis_intern_hinted',
          'uis_intern_many', 'uis_layer_base', 'uis_layer_add', 'uis_layer_add_qs',
          'uis_layer_fixed', 'uis_stock_has', 'uis_qs_shared', 'uia_test_qs_call']
PAD = '    .rept 7\n    .word 0xE7F000F0\n    .endr\n'      # udf: a wrong entry offset traps


def moved_header(d):
    src = (HERE / 'ui_strings.S').read_text()
    seam = '    .word   owns - hdr'
    assert src.count(seam) == 1
    (d / 'ui_strings.S').write_text(src.replace(seam, PAD + seam))
    saved = gen_strings_code.SOURCE
    gen_strings_code.SOURCE = d / 'ui_strings.S'
    try:
        (d / 'ui_strings_code.h').write_text(gen_strings_code.render())
    finally:
        gen_strings_code.SOURCE = saved


def build_two(tmp, header_of_mutation=None):
    """A host library with today's uishare and build B (moved entries)."""
    tmp = pathlib.Path(tmp)
    b = tmp / 'b'
    b.mkdir()
    moved_header(b)
    for f in ('ui_pool.c', 'ui_apply.c', 'ui_pool.h', 'ui_apply.h'):
        shutil.copy(HERE / f, b / f)
    a = tmp / 'a'
    a.mkdir()
    for f in ('ui_pool.c', 'ui_apply.c', 'ui_pool.h', 'ui_apply.h', 'ui_strings_code.h'):
        shutil.copy(HERE / f, a / f)
    if header_of_mutation:
        old, new = header_of_mutation
        for d in (a, b):
            text = (d / 'ui_pool.c').read_text()
            assert text.count(old) == 1, old
            (d / 'ui_pool.c').write_text(text.replace(old, new))
    clang = shutil.which('clang') or 'clang'
    base = [clang, '-c', '-fPIC', '-O1', '-std=c11', '-Wall', '-Wextra', '-Werror',
            '-DUIA_HOST_TEST', '-DUIS_HOST_TEST']
    objs = []
    for d, ren in ((a, False), (b, True)):
        for f in ('ui_pool.c', 'ui_apply.c'):
            o = d / (f + '.o')
            defs = [f'-D{p}={p}_b' for p in PUBLIC] if ren else []
            subprocess.run(base + defs + ['-I', str(d), str(d / f), '-o', str(o)], check=True,
                           capture_output=True, text=True)
            objs.append(str(o))
    fx = tmp / 'fx.o'
    subprocess.run(base + ['-I', str(a), str(HERE / 'ui_apply_fixture.c'), '-o', str(fx)],
                   check=True, capture_output=True, text=True)
    out = tmp / 'two.dylib'
    subprocess.run([clang, '-shared', *objs, str(fx), '-o', str(out)], check=True,
                   capture_output=True, text=True)
    lib = ct.CDLL(str(out))
    for name, args, res in (('fx_reset', [ct.c_char_p, ct.c_uint32, ct.c_char_p, ct.POINTER(ct.c_uint32), ct.c_uint32], None),
                            ('fx_apply', [ct.c_char_p, ct.c_uint32, ct.POINTER(ct.c_uint32)], ct.c_uint32),
                            ('fx_peek', [ct.c_uint32], ct.c_uint32), ('fx_poke', [ct.c_uint32, ct.c_uint32], None),
                            ('fx_read', [ct.c_uint32, ct.c_char_p, ct.c_uint32], None),
                            ('fx_get', [ct.c_uint32], ct.c_uint32),
                            ('fx_loader', [ct.c_uint32, ct.POINTER(ct.c_uint32), ct.c_uint32], None),
                            ('fx_claim_res', [ct.c_uint32, ct.c_uint32], None),
                            ('fx_holder_skips_res', [ct.c_uint32], None)):
        f = getattr(lib, name)
        f.argtypes = args
        f.restype = res
    return lib


class TwoBuilds(unittest.TestCase):
    def run_three(self, lib):
        """F2 (build A), F3 (build B), F4 (build A): every apply must succeed."""
        cam = TQO.Camera(lib)
        calls = []

        @ct.CFUNCTYPE(ct.c_uint32, ct.c_uint64, ct.c_uint64, ct.c_uint64, ct.c_uint64, ct.c_uint64)
        def qs_call(fn, a, b, c, d):
            calls.append(fn)
            return (c + 63) & ~63 if d else 0       # qs_pack_place / qs_check / qs_hang
        self._keep = qs_call
        for v in ('uia_test_qs_call', 'uia_test_qs_call_b'):
            ct.c_void_p.in_dll(lib, v).value = ct.cast(qs_call, ct.c_void_p).value
        apply_a = ct.c_void_p.in_dll(lib, 'fx_apply_fn').value
        apply_b = ct.cast(lib.uia_apply_b, ct.c_void_p).value
        names = ['F2', 'F3', 'F4']
        results = []
        for me, enum, build in (('F2', 7, apply_a), ('F3', 8, apply_b), ('F4', 9, apply_a)):
            TQO.loader(lib, me, names)
            ct.c_void_p.in_dll(lib, 'fx_apply_fn').value = build
            r, out = cam.apply(TQO.sup(me, enum))
            results.append(r)
        ct.c_void_p.in_dll(lib, 'fx_apply_fn').value = apply_a
        return cam, results

    def test_layers_of_two_builds_recognise_each_other(self):
        with tempfile.TemporaryDirectory(prefix='compat-') as t:
            lib = build_two(t)
            cam, results = self.run_three(lib)
            self.assertEqual(results, [TA.OK] * 3)
            layers, hs = [], []
            h = chain.outer(cam.peek)
            while h is not None:
                hs.append(h)
                h = chain.inner(cam.peek, h)
            owns = [cam.peek(h + 64) for h in hs]          # H_OFF_OWN of F4, F3, F2
            self.assertNotEqual(owns[1], owns[0])          # build B's entries moved
            self.assertEqual(owns[0], owns[2])
            texts = chain.layers(cam.peek)
            self.assertEqual(len(texts), 3)
            reader = cam.get(TA.READER)
            for base, strings in texts:
                for i, s in enumerate(strings):
                    self.assertEqual(chain.resolve(cam.peek, base + i, reader), s)
            q = chain.qs(cam.peek)                          # one shared id set, three answerers
            self.assertEqual(len({x[1] for x in q}), 1)
            self.assertEqual([x[2] for x in q], [7, 8, 9])

    def test_the_arm_code_of_both_builds_answers(self):
        """unicorn: the firmware site -> F4 (A) -> F3 (B) -> F2 (A), by value."""
        import test_ui_strings as TS
        with tempfile.TemporaryDirectory(prefix='compat-arm-') as t:
            lib = build_two(t)
            cam, results = self.run_three(lib)
            self.assertEqual(results, [TA.OK] * 3)
            shared = chain.qs(cam.peek)[0][1]
            m = TS.Machine(TA.image(), cam)
            m.mu.mem_map(0xC3100000, 0x100000)
            for value, who in ((7, 'F2'), (8, 'F3'), (9, 'F4'), (3, 'F2')):
                m.mu.mem_write(chain.RES_RAW_A, struct.pack('<I', value))
                got = m.call(TS.RES, m.reader, shared)[0]
                out = bytearray()
                while m.mu.mem_read(got + len(out), 1) != b'\0':
                    out += m.mu.mem_read(got + len(out), 1)
                self.assertEqual(out.decode(), f'{who}_QS', value)
            for base, strings in chain.layers(cam.peek):    # owns/remain: through both builds
                ptr = m.call(TS.RES, m.reader, base)[0]
                self.assertEqual(m.call(TS.OWN, m.reader, ptr)[0], 1)

    def test_a_newer_build_on_top(self):
        """B outermost: every site's veneer leads into build B's entries."""
        with tempfile.TemporaryDirectory(prefix='compat-top-') as t:
            lib = build_two(t)
            cam = TQO.Camera(lib)

            @ct.CFUNCTYPE(ct.c_uint32, ct.c_uint64, ct.c_uint64, ct.c_uint64, ct.c_uint64, ct.c_uint64)
            def qs_call(fn, a, b, c, d):
                return 0
            self._keep2 = qs_call
            for v in ('uia_test_qs_call', 'uia_test_qs_call_b'):
                ct.c_void_p.in_dll(lib, v).value = ct.cast(qs_call, ct.c_void_p).value
            apply_a = ct.c_void_p.in_dll(lib, 'fx_apply_fn').value
            apply_b = ct.cast(lib.uia_apply_b, ct.c_void_p).value
            names = ['F2', 'F3']
            for me, enum, build in (('F2', 7, apply_a), ('F3', 8, apply_b)):
                TQO.loader(lib, me, names)
                ct.c_void_p.in_dll(lib, 'fx_apply_fn').value = build
                self.assertEqual(cam.apply(TQO.sup(me, enum))[0], TA.OK)
            ct.c_void_p.in_dll(lib, 'fx_apply_fn').value = apply_a
            self.assertEqual(len(chain.layers(cam.peek)), 2)     # all three sites read through B

    def test_fixed_offsets_would_break_it(self):
        """The mutation: find the header with this build's own offsets."""
        old = '    h = entry - off;\n'
        new = ('    h = entry - (field == H_OFF_RES ? UIS_LAYER_RESOLVE : field == H_OFF_OWN ? '
               'UIS_LAYER_OWNS : UIS_LAYER_REMAIN);\n    (void)off;\n')
        with tempfile.TemporaryDirectory(prefix='compat-mut-') as t:
            lib = build_two(t, (old, new))
            cam, results = self.run_three(lib)
            self.assertNotEqual(results, [TA.OK] * 3)


if __name__ == '__main__':
    unittest.main()
