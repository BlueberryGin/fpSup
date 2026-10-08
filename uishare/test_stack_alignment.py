#!/usr/bin/env python3
"""sp is 8-aligned (absolutely) at every instruction of our hook code's body
(memory arm-stack-alignment: an interrupt stores the context with STRD on the
interrupted task's stack; the camera hung on a misaligned sp twice).

    python3 -B -m unittest test_stack_alignment

The firmware does not always call our hook points at an aligned sp: callers
with an odd frame (push {r4, r5, lr} and the like) reach C05E5B58, C05E6400
and C05D90F0 at sp = 4 mod 8 (QS_SHARE.md / NESTED_HOOKS.md §4). So every
piece runs twice, entered at sp = 0 and at sp = 4 mod 8, and a code hook
watches every instruction. A stretch of misaligned sp that begins in our code
is allowed only as

  - the entry window: from an entry point, the few instructions that take the
    caller's sp and align it (at most 8, nothing called);
  - the exit: the return (bx lr / pop {.., pc} / ldr pc) after sp has been
    put back to the caller's -- the caller's parity, its business;
  - a compiler prologue/epilogue window: an odd push and the `sub sp` that
    evens it, or `add sp` / `mov sp` and the pop (exception 1 of the memory);
  - a replayed firmware prologue (`displaced`, exception 2): skipped.

Pieces: the nested string layers (ui_strings.S: resolve / owns / remain, hits
in the outer and the inner layer through the tail call, misses down to the
firmware rule, the QS branch) and the Quick Set layers (qs_layer.S +
qs_apply.c + qs_layer.c: record copy and in-place paths, layout, pack).
Mutations: the v1/v2 ENTER (12 bytes) and the first fix (16 bytes, aligned
only on an aligned entry) must both fail; so must the C without -mstackrealign
(see test_qs_layer below and qs_build.CFLAGS).
"""
import pathlib
import shutil
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from unicorn import UC_HOOK_CODE                     # noqa: E402
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_CPSR   # noqa: E402
import test_ui_strings as TS                          # noqa: E402
import test_ui_apply as TA                            # noqa: E402
import test_qs_layer as TQ                            # noqa: E402
from ui import chain, qs                              # noqa: E402

_CS = {}


def _insn(mu, addr):
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
    thumb = bool(mu.reg_read(UC_ARM_REG_CPSR) & 0x20)
    if thumb not in _CS:
        _CS[thumb] = Cs(CS_ARCH_ARM, CS_MODE_THUMB if thumb else CS_MODE_ARM)
    for i in _CS[thumb].disasm(bytes(mu.mem_read(addr, 4)), addr, 1):
        m = i.mnemonic.split('.')[0]
        m = {'addw': 'add', 'subw': 'sub', 'movs': 'mov', 'adds': 'add', 'subs': 'sub'}.get(m, m)
        return m, i.op_str
    return '?', ''


def _is_return(m, ops):
    m = m.rstrip('eqnehilsgtlt') if m.startswith(('ldr', 'bx', 'pop')) else m
    return (m == 'bx' and ops == 'lr') or (m.startswith('pop') and 'pc' in ops) or \
        (m.startswith('ldr') and ops.startswith('pc'))


def watch(mu, lo, hi, entries=(), skip=()):
    """Every disallowed stretch of misaligned sp that began in our code."""
    bad, st = _Bad(), {'run': [], 'ours': False, 'start': 0}
    entries = set(entries)

    def close():
        run = st['run']
        if run and st['ours']:
            m0, o0 = run[0]
            if st['start'] in entries and len(run) <= 8:
                ok = True                                  # the entry window
            elif _is_return(m0, o0):
                ok = True                                  # the exit, at the caller's sp
            else:                                          # a prologue/epilogue window
                body, (me, oe) = run[:-1], run[-1]
                ok = len(run) <= 4 and all(m in ('add', 'sub', 'mov', 'bfc') for m, _ in body) and \
                    (me in ('sub', 'add', 'mov') or me.startswith('pop'))
            if not ok:
                bad.append((hex(st['start']), run[:6]))
        st['run'] = []

    def hook(mu, addr, size, user):
        ours = lo <= addr < hi and not any(a <= addr < b for a, b in skip)
        if mu.reg_read(UC_ARM_REG_SP) & 7:
            if st['run'] and ours != st['ours']:
                close()                                    # into or out of our code: a new stretch
            if not st['run']:
                st['ours'], st['start'] = ours, addr
            st['run'].append(_insn(mu, addr))
        elif st['run']:
            close()
    mu.hook_add(UC_HOOK_CODE, hook)
    bad.close = close                                   # a call's end ends its stretch
    return bad


class _Bad(list):
    pass


def flushing(m, bad):
    """m.call, closing the watcher's stretch when each call returns."""
    orig = m.call

    def call(*a, **k):
        try:
            return orig(*a, **k)
        finally:
            bad.close()
    m.call = call


def string_layers(lib, skew):
    """Two lossless layers through the C host model, then run in unicorn."""
    cam = TA.Camera(lib)
    blob = TA.lossless()[0]
    for _ in range(2):
        r, out = cam.apply(blob)
        assert r == TA.OK, out
    m = TS.Machine(TA.image(), cam)
    entries = []
    layer = chain.outer(cam.peek, 'resolve')
    while layer is not None:
        entries += [layer + cam.peek(layer + chain.SITES[k][2]) for k in chain.SITES]
        layer = chain.inner(cam.peek, layer)
    bad = watch(m.mu, TS.HEAP, TS.HEAP + TS.HEAP_SIZE, entries)
    flushing(m, bad)
    TS.SP_SKEW = skew
    try:
        layers = chain.layers(cam.peek)
        for base, texts in layers:
            for i in range(len(texts)):
                m.call(TS.RES, m.reader, base + i)          # outer hit, and inner through the tail call
        for off in (0, 1000, TS.POOL_LEN + 5, 0xFFFFFFFF):
            m.call(TS.RES, m.reader, off)                   # down to the firmware rule
        for p in (0, TS.POOL + 7, TS.HEAP + 0x3000):
            m.call(TS.OWN, m.reader, p)
            m.call(TS.REM, m.reader, p)
        ptr = m.call(TS.RES, m.reader, layers[0][0])[0]
        m.call(TS.OWN, m.reader, ptr)
        m.call(TS.REM, m.reader, ptr)
    finally:
        TS.SP_SKEW = 0
    return bad


def _offsets(header_text):
    import re
    return {k: int(re.search(r'UIS_LAYER_%s\s+(\d+)u' % k.upper(), header_text).group(1))
            for k in ('resolve', 'owns', 'remain')}


def with_offsets(offs, fn, *a):
    """Headers describe their own entries (v5): nothing to patch any more."""
    return fn(*a)


def old_lib(tmp, replacements):
    d = pathlib.Path(tmp)
    src = (HERE / 'ui_strings.S').read_text()
    for old, new in replacements:
        assert src.count(old) == 1, old
        src = src.replace(old, new)
    (d / 'ui_strings.S').write_text(src)
    import gen_strings_code
    saved = gen_strings_code.SOURCE
    gen_strings_code.SOURCE = d / 'ui_strings.S'
    try:
        (d / 'ui_strings_code.h').write_text(gen_strings_code.render())
    finally:
        gen_strings_code.SOURCE = saved
    shutil.copy(HERE / 'ui_pool.c', d / 'ui_pool.c')
    lib = TS.build_lib(tmp, name='old.dylib', pool_src=d / 'ui_pool.c', include=d)
    return lib, _offsets((d / 'ui_strings_code.h').read_text())


ENTER_NOW = ('    tst     sp, #4\n    subeq   sp, sp, #8\n    subne   sp, sp, #12\n    push    {r3, r4}\n'
             '    add     r3, sp, #16\n    addne   r3, r3, #4                  @ the flags are still tst\'s\n'
             '    str     r3, [sp, #12]\n    adr     r3, hdr\n')
LEAVE_NOW = '    ldr     r2, [sp, #12]\n    pop     {r3, r4}\n    mov     sp, r2\n'
NEXT_NOW = ('    ldr     r2, [sp, #12]\n    tst     r2, #4                      @ which frame: 16 or 20 bytes\n'
            '    pop     {r3, r4}\n    ldreq   pc, [sp], #8                @ the next layer, r0/r1 as they came, at the caller\'s sp\n'
            '    ldrne   pc, [sp], #12\n')
V2 = [(ENTER_NOW, '    sub     sp, sp, #4\n    push    {r3, r4}\n    adr     r3, hdr\n'),
      (LEAVE_NOW, '    pop     {r3, r4}\n    add     sp, sp, #4\n'),
      (NEXT_NOW, '    pop     {r3, r4}\n    pop     {pc}\n')]
FIRST_FIX = [(ENTER_NOW, '    sub     sp, sp, #8\n    push    {r3, r4}\n    adr     r3, hdr\n'),
             (LEAVE_NOW, '    pop     {r3, r4}\n    add     sp, sp, #8\n'),
             (NEXT_NOW, '    pop     {r3, r4}\n    ldr     pc, [sp], #8\n')]


class TestStringLayers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='sa-')
        cls.lib = TS.build_lib(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_aligned_entry(self):
        self.assertEqual(string_layers(self.lib, 0), [])

    def test_misaligned_entry(self):
        self.assertEqual(string_layers(self.lib, 4), [])

    def test_contract_holds_at_both_parities(self):
        """r1, r3, r4, ip, lr and sp come back as they went in."""
        for skew in (0, 4):
            TS.SP_SKEW = skew
            try:
                case = TS.Strings('test_each_private_offset_resolves_to_its_text_through_every_layer')
                TS.Strings.setUpClass()
                case.test_each_private_offset_resolves_to_its_text_through_every_layer()
                case2 = TS.Strings('test_owns_and_remain_match_the_firmware_outside_the_layers')
                case2.test_owns_and_remain_match_the_firmware_outside_the_layers()
            finally:
                TS.SP_SKEW = 0

    def test_v2_layer_is_caught(self):
        with tempfile.TemporaryDirectory(prefix='sa-v2-') as t:
            lib, offs = old_lib(t, V2)
            self.assertNotEqual(with_offsets(offs, string_layers, lib, 0), [])

    def test_first_fix_is_caught_on_a_misaligned_entry(self):
        with tempfile.TemporaryDirectory(prefix='sa-f1-') as t:
            lib, offs = old_lib(t, FIRST_FIX)
            self.assertEqual(with_offsets(offs, string_layers, lib, 0), [])
            self.assertNotEqual(with_offsets(offs, string_layers, lib, 4), [])


def qs_run(skew):
    m = TQ.Machine(n_rows=4)
    lays = m.layout_model()
    blocks = []
    for k in (2, 3):
        assert m.add_layer(f'3{k}T', k, TQ.ids(k), pack=TQ.PACK) == 0
        blocks.append(m.last_block)
    sym = TQ.Machine.sym
    skip = [(b + (sym['qs_replay_record'] & ~1), b + 348) for b in blocks]    # the firmware's prologues
    entries = [b + sym[e] for b in blocks for e in ('qs_entry_record', 'qs_entry_layout', 'qs_entry_load')]
    bad = watch(m.mu, TQ.HEAP, TQ.HEAP + 0x100000, entries, skip)
    flushing(m, bad)
    TQ.SP_SKEW = skew
    try:
        for row in qs.recipe()['records'][:60] + \
                [r for r in qs.recipe()['records'] if r['rule'] == 'footer_label']:
            m.parse(int(row['address'], 16), ctx=0x4444)          # copy path, in-place path, pack
        m.nbr_load(0x5555, TQ.STOCK_NBR)
        m.load_layout(lays[0])
        m.load_layout(lays[1])
    finally:
        TQ.SP_SKEW = 0
    assert len(m.loads) > 2                                       # the pack path did run
    return bad


class TestQsLayers(unittest.TestCase):
    def test_aligned_entry(self):
        self.assertEqual(qs_run(0), [])

    def test_misaligned_entry(self):
        self.assertEqual(qs_run(4), [])


class TestHandlers(unittest.TestCase):
    def test_fv_handler_and_section_entry(self):
        """Static: fv_handler.S never moves sp; section.S moves it by 16 and 64
        (it is called from the loader's C, at an aligned sp)."""
        fv = (HERE / 'fv_handler.S').read_text()
        self.assertNotIn('push', fv)
        self.assertNotIn('pop', fv)
        self.assertNotRegex(fv, r'\b(add|sub)\s+sp')
        sec = (HERE / 'section.S').read_text()
        self.assertIn('push    {r4, r5, r6, lr}', sec)
        self.assertIn('.equ OUTCOME, 64', sec)


if __name__ == '__main__':
    unittest.main()
