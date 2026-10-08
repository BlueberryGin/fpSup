#!/usr/bin/env python3
"""QS_OPTION and IF_SLOT (QS_SHARE.md §9): ui.options.resolution_qs(), the
reference applier (ui/fpui.py) and ui_apply.c.

    python3 -B -m unittest test_qs_option

  - blocks made before these ops existed are byte for byte what they were
    (raw-view's COLOR, OpenGate's resolution, Lossless's MainB2 row);
  - one resolution_qs sup gives the B2_5 page OpenGate's resolution() gives;
  - six sups (rows 2..7) give, on the summary records, the popup offset and
    the six page names, what Jose Hurtado's Formats v0.6.1 gives (his string
    ids standing for ours); any load order gives the same page for the same k;
  - ui_apply.c == the reference on the page, the redirect table and the
    string layers, for one and for several sups, in either order;
  - QS_OPTION calls qs_pack_place, qs_check (with k, N, enum and the image
    offsets the reference computes) before the commit, and qs_hang after the
    page is switched, last; a refused check changes nothing; no Loader v3, no
    QS_OPTION.
"""
import ctypes as ct
import hashlib
import pathlib
import struct
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'lossless' / 'menu'))
sys.path.insert(0, str(ROOT / 'projects' / 'rawview' / 'build'))
from ui import chain, fpui, options as O, qs   # noqa: E402
import qs_build                                # noqa: E402
import test_ui_apply as TA                     # noqa: E402
import test_options as TO                      # noqa: E402

OK, BLOCK, NO_UI, PAGE, GUARD, NO_MEMORY, STRING, OP, FULL, HOOK, CONFLICT = range(11)
BASELINE = {   # sha256 of the blocks as built before OP_QS_OPTION / OP_IF_SLOT existed
    'color_raw': 'fc8661a52eb06573fadb148b24956b3fc89f9a77522107d4c3a463a6fc55bbc4',
    'og3k': '7c35d46221b99f8b4f4a1a51ee37dcf79d21a77737608f6342869ba9aae363ae',
    'og2k': '58db2cb73614b38e6ed7e93ab45f1052506c90c098513b7dd17f3ba160cc25dc',
    'lossless': '5556fcf5db8684768b2ea1aae6ec3376d75d4fbafd44e1c3926afe8115e335ad',
}
LAYER = None
PACK = b'NBR\0\x01\x05' + bytes(10) + bytes(range(200))
JOSE_IDS = {2: 0xFFFFFFF6, 3: 0xFFFFFFD0, 4: 0xFFFFFFD1, 5: 0xFFFFFFD2, 6: 0xFFFFFFD3, 7: 0xFFFFFFD4}
SVC_MODEL, LOAD_DONE_US, SL_SVC_AT = 0x10007000, 0xC072F6F8, 0xC072F6FC


def layer():
    global LAYER
    if LAYER is None:
        LAYER = qs_build.layer_blob()
    return LAYER


def sup(name, enum, pack=b''):
    """A test sup `name` (its file name, its image names) for the format the
    table gives `enum` (ui/enums.py: the Settings label must name it)."""
    from ui import enums
    fmt = next(k for k, v in enums.RESOLUTION.items() if v == enum)
    return O.resolution_qs(f'{fmt} 1x1', name, (f'{name}_QS', f'{name}_SET', f'{name}_FONT'),
                           enum, layer(), pack=pack).encode()


def chain_qs(cam):
    return chain.qs(cam.peek)


def b25(pages):
    return bytes(pages['B2_5'].data)


class TestBlocks(unittest.TestCase):
    def test_old_blocks_unchanged(self):
        import build_fpui
        got = {'color_raw': O.color_raw(b'\x01' * 533).encode(),
               'og3k': O.resolution('OG3K 3008x2000', 'OG3K').encode(),
               'og2k': O.resolution('OG2K 2000x1334', 'OG2K').encode(),
               'lossless': build_fpui.build()[0]}
        self.assertEqual({k: hashlib.sha256(v).hexdigest() for k, v in got.items()}, BASELINE)

    def test_one_sup_is_opengates_page(self):
        og = O.run([O.resolution('OG3K 3008x2000', 'OG3K').encode()])
        mine = O.run([sup('OG3K', 4)])
        self.assertEqual(b25(mine[0]), b25(og[0]))
        q = mine[3][0]['qs']
        self.assertEqual((q['k'], q['n'], q['enum']), (2, 3, 4))

    def test_six_sups_are_joses_settings(self):
        """Rows 2..7: the summary records, popup offset and page names as
        Jose's Formats has them (his ids where ours are)."""
        import gen_qs_recipe as G
        jose = G.jose_deltas(G.jose_layout())
        from ui import enums
        with_six = dict(enums.RESOLUTION, T6=6)      # six formats: one more than the table has today
        saved = dict(enums.RESOLUTION)
        enums.RESOLUTION.clear(); enums.RESOLUTION.update(with_six)
        try:
            blocks = [sup(f'F{k}', v) for k, v in zip(range(2, 8), (4, 5, 6, 7, 8, 9))]
        finally:
            enums.RESOLUTION.clear(); enums.RESOLUTION.update(saved)
        pages, files, pool, res = O.run(blocks)
        page = b25(pages)
        a = O.resource_set('B2_5')[0]
        cur = pages['B2_5'].current
        for k, (rec, grow) in O.RES_SUMMARY_K.items():
            n = len(jose[rec]) if rec in jose else 53
            got = bytearray(page[cur(rec - a):cur(rec - a) + n])
            mine_id = struct.unpack('>I', got[32:36])[0]
            self.assertGreaterEqual(mine_id, fpui.LAYER_FIRST, k)
            got[32:36] = struct.pack('>I', JOSE_IDS[k])
            want = jose.get(rec)
            if want is None:                                     # row 2: OpenGate's summary
                want = qs.image()[rec - 0xC0000000:rec - 0xC0000000 + 53]
                want = want[:32] + struct.pack('>I', JOSE_IDS[k]) + want[36:]
            self.assertEqual(bytes(got), want, hex(rec))
        y = cur(O.RES_POPUP_Y - a)
        self.assertEqual(struct.unpack('>f', page[y:y + 4])[0], -164.0)
        base, offs, want = O.RES_EXCL
        for o in offs:
            p = cur(base + o - a)
            self.assertEqual(struct.unpack('>I', page[p:p + 4])[0], want)
        self.assertEqual([r['qs']['k'] for r in res], [2, 3, 4, 5, 6, 7])
        self.assertEqual([r['qs']['enum'] for r in res], [4, 5, 6, 7, 8, 9])
        self.assertEqual({r['qs']['n'] for r in res}, set(range(3, 9)))

    def test_order_gives_the_same_rows(self):
        b = [sup(f'F{k}', v) for k, v in zip(range(2, 6), (4, 5, 7, 8))]
        p1 = O.run(b)[0]
        p2 = O.run(list(reversed(b)))[0]
        self.assertEqual(len(b25(p1)), len(b25(p2)))           # same structure; texts follow k

    def test_if_slot_skips(self):
        blk = fpui.Block()
        O.hook_fv(blk)
        name, addr, size = O.RES_CSV
        O.csv_file(blk, addr, size, ['{N},x,Popup,,1,2'])
        a, off, n = O.resource_set('B2_5')
        blk.op(fpui.OP_PAGE, blk.string('B2_5', fpui.NAME), off, n, 1, 0, 0, 0)
        O._if_slot(blk, 0, 2, lambda: blk.op(fpui.OP_ADDF, O.RES_LIST_MAX - a, 5))
        O._if_slot(blk, 0, 3, lambda: blk.op(fpui.OP_ADDF, O.RES_LIST_MAX - a, 100))
        blk.op(fpui.OP_DONE)
        page = b25(O.run([blk.encode()])[0])
        m = O.RES_LIST_MAX - a
        self.assertEqual(struct.unpack('>f', page[m:m + 4])[0], 6.0)


class Camera(TO.Camera):
    def __init__(self, lib, loader=True):
        super().__init__(lib)
        self.calls = []
        if loader:
            lib.fx_poke(SVC_MODEL, 3)
            lib.fx_poke(SVC_MODEL + 40, lib.fx_peek(LOAD_DONE_US))
            lib.fx_poke(SL_SVC_AT, SVC_MODEL)


PATHS = 0x10007100
_PATH_SLOTS = {}


def loader(lib, me, holders):
    """fx_loader: this sup's path and the books' holders (first = the QS issuer)."""
    addrs = []
    for name in holders:
        a = PATHS + 0x40 * _PATH_SLOTS.setdefault(name, len(_PATH_SLOTS))   # one place per name
        data = b'\\fpSup\\' + name.encode() + b'.BIN\0'
        data += bytes(-len(data) % 4)
        for j in range(0, len(data), 4):
            lib.fx_poke(a + j, struct.unpack('<I', data[j:j + 4])[0])
        addrs.append(a)
    arr = (ct.c_uint32 * len(addrs))(*addrs)
    lib.fx_loader(addrs[holders.index(me)], arr, len(addrs))
    return dict(zip(holders, addrs))


class TestApply(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='uiq-')
        cls.lib = TA.build(cls.tmp.name)
        cls.check_result = 0
        cls.calls = []

        @ct.CFUNCTYPE(ct.c_uint32, ct.c_uint64, ct.c_uint64, ct.c_uint64, ct.c_uint64, ct.c_uint64)
        def qs_call(fn, a, b, c, d):
            blob, sym = layer()
            off = fn - cls.layer_at
            which = {sym['qs_check']: 'check', sym['qs_hang']: 'hang',
                     sym['qs_pack_place']: 'place'}.get(off, '?')
            entries = cls.lib.fx_get(TA.ENTRY_WRITES)
            if which == 'place':
                to = (c + 63) & ~63
                cls.calls.append(('place', a, b, c, d))
                return to
            opt = struct.unpack('<11I', ct.string_at(c, 44))
            cls.calls.append((which, a, b, opt, entries))
            return cls.check_result if which == 'check' else 0
        cls.qs_call = qs_call
        ct.c_void_p.in_dll(cls.lib, 'uia_test_qs_call').value = ct.cast(qs_call, ct.c_void_p).value

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.cam = Camera(self.lib)
        type(self).calls.clear()
        type(self).check_result = 0

    def fpui_at(self, blob):
        """Where fx_apply puts the block, and the layer inside it."""
        strings, ops, frag = fpui.decode(blob)
        return strings, ops, frag

    def apply(self, blob):
        # the layer's address: the block's fragment start + the op's offset
        strings, ops, frag = fpui.decode(blob)
        q = [a for c, a in ops if c == fpui.OP_QS_OPTION][0]
        frag_at = 0x10010000 + len(blob) - len(frag) - (-len(frag) % 4)
        type(self).layer_at = frag_at + q[3]
        return self.cam.apply(blob)

    def test_same_as_reference_and_calls(self):
        blocks = [sup('F2', 7, pack=PACK)]
        loader(self.lib, 'F2', ['F2'])
        r, out = self.apply(blocks[0])
        self.assertEqual(r, OK, out)
        pages, files, pool, res = O.run(blocks)
        self.assertEqual(self.cam.page_of('B2_5'), b25(pages))
        self.assertEqual(self.cam.table(), files.table)
        self.assertEqual(self.cam.layers(), pool.layers)
        names = [c[0] for c in self.calls]
        self.assertEqual(names, ['place', 'check', 'hang'])
        q = res[0]['qs']
        chk, hang = self.calls[1], self.calls[2]
        table, k, i0, i1, i2, i3, enum, flags, pack, pack_len, n = chk[3]
        self.assertEqual((k, [i0, i1, i2, i3], enum, flags, n), (q['k'], q['ids'], q['enum'], q['flags'], q['n']))
        self.assertEqual(flags, 1, 'resolution_qs re-parses on a switch by default (camera 2026-10-07)')
        self.assertEqual(table, self.layer_at + layer()[1]['table'])
        self.assertEqual((pack % 64, pack_len), (0, len(PACK)))
        self.assertEqual(chk[4], 0, 'checked before any page switch')
        self.assertGreater(hang[4], 0, 'hung after the page switch')
        self.assertEqual(hang[3], chk[3])
        self.assertEqual([x[1:] for x in chain_qs(self.cam)], pool.qs)

    def test_refused_check_changes_nothing(self):
        type(self).check_result = 2                              # QS_NMAX
        loader(self.lib, 'F2', ['F2'])
        r, out = self.apply(sup('F2', 7))
        self.assertEqual(r, GUARD)
        self.assertIsNone(self.cam.page_of('B2_5'))
        self.assertFalse(self.cam.table())                         # the handler may be up; no file in it
        self.assertEqual([c[0] for c in self.calls], ['check'])

    def test_no_loader_no_qs(self):
        self.cam = Camera(self.lib, loader=False)
        r, out = self.apply(sup('F2', 7))
        self.assertEqual(r, OP)
        self.assertEqual(self.calls, [])

    def test_two_sups_either_order(self):
        """The first QS sup hands out the shared ids; the second finds them in
        its layer; each layer answers them with its own names for its enum."""
        a, b = sup('F2', 7), sup('F3', 8)
        for order, names in (([a, b], ['F2', 'F3']), ([b, a], ['F3', 'F2'])):
            self.setUp()
            for blk, me in zip(order, names):
                loader(self.lib, me, names)
                r, out = self.apply(blk)
                self.assertEqual(r, OK, out)
            pages, files, pool, res = O.run(order)
            self.assertEqual(self.cam.page_of('B2_5'), b25(pages))
            self.assertEqual(self.cam.table(), files.table)
            self.assertEqual(self.cam.layers(), pool.layers)
            self.assertEqual([x[1:] for x in chain_qs(self.cam)], pool.qs)
            checks = [c[3] for c in self.calls if c[0] == 'check']
            self.assertEqual([c[1] for c in checks], [2, 3])
            self.assertEqual(checks[0][2:5], checks[1][2:5])          # the same shared ids
            shared = checks[0][2]
            reader = self.cam.get(TA.READER)
            for value, who in ((7, 'F2'), (8, 'F3'), (3, names[0])):   # UHD: the issuer's own
                peek = lambda a, v=value: v if a == chain.RES_RAW_A else self.cam.peek(a)  # noqa: E731
                self.assertEqual(chain.resolve(peek, shared, reader), f'{who}_QS', (names, value))
                self.assertEqual(chain.resolve(peek, shared + 2, reader), f'{who}_FONT')

    def test_issuer_is_the_books_first_by_whole_name(self):
        """Names alike in their first four bytes are different sups: A holds the
        resource first (and has no layer), so B is not the issuer."""
        paths = loader(self.lib, '31FMTB', ['31FMTA', '31FMTB'])
        self.lib.fx_claim_res(paths['31FMTA'], 0x55435351)
        r, out = self.apply(sup('31FMTB', 8))
        self.assertEqual(r, HOOK)

    def test_shared_ids_only_from_the_first_holder(self):
        """A listed sup that never claimed the resource is not asked; one that
        holds it first without a layer makes the next refuse."""
        loader(self.lib, 'F2', ['F2'])
        self.assertEqual(self.apply(sup('F2', 7))[0], OK)
        loader(self.lib, 'F3', ['F9', 'F2', 'F3'])
        self.assertEqual(self.apply(sup('F3', 8))[0], OK)       # F9 never claimed: F2 is first
        self.setUp()
        paths = loader(self.lib, 'F2', ['F9', 'F2'])
        self.lib.fx_claim_res(paths['F9'], 0x55435351)
        self.assertEqual(self.apply(sup('F2', 7))[0], HOOK)

    def test_a_listed_sup_that_never_claimed_is_not_the_issuer(self):
        loader(self.lib, 'F3', ['F2', 'F3'])                          # F2: not a QS sup
        r, out = self.apply(sup('F3', 8))
        self.assertEqual(r, OK, out)
        self.assertEqual(chain.qs(self.cam.peek)[0][0], 'F3')

    def test_the_cameras_old_holder_is_caught(self):
        """svc_holder skipping resource claims (the camera's loader before
        d9's fix): no first holder, so QS_OPTION refuses."""
        loader(self.lib, 'F2', ['F2'])
        self.lib.fx_holder_skips_res(1)
        self.assertEqual(self.apply(sup('F2', 7))[0], HOOK)

    def test_the_arm_layers_answer_by_value(self):
        """ui_strings.S, executed (unicorn): each QS layer answers the shared
        ids with its own names while the resolution value is its enum; every
        other value falls to the issuer's own strings; registers preserved."""
        import struct as st
        import test_ui_strings as TS
        from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R12, \
            UC_ARM_REG_SP, UC_ARM_REG_LR
        names = ['F2', 'F3']
        for blk, me in zip([sup('F2', 7), sup('F3', 8)], names):
            loader(self.lib, me, names)
            self.assertEqual(self.apply(blk)[0], OK)
        shared = chain.qs(self.cam.peek)[0][1]
        m = TS.Machine(TA.image(), self.cam)
        m.mu.mem_map(0xC3100000, 0x100000)                     # the cache, the storage
        for value, who in ((7, 'F2'), (8, 'F3'), (3, 'F2'), (2, 'F2')):
            m.mu.mem_write(chain.RES_RAW_A, st.pack('<I', value))
            for i, kind in enumerate(('QS', 'SET', 'FONT')):
                got, before, after = m.call(TS.RES, m.reader, shared + i)
                out = bytearray()
                while m.mu.mem_read(got + len(out), 1) != b'\0':
                    out += m.mu.mem_read(got + len(out), 1)
                self.assertEqual(out.decode(), f'{who}_{kind}', (value, kind))
                for r in (UC_ARM_REG_R1, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R12,
                          UC_ARM_REG_SP, UC_ARM_REG_LR):
                    self.assertEqual(after[r], before[r] & 0xFFFFFFFF)
        # an ordinary private string of the outer layer still resolves through it
        base, texts = chain.layers(self.cam.peek)[1]
        got = m.call(TS.RES, m.reader, base)[0]
        out = bytearray()
        while m.mu.mem_read(got + len(out), 1) != b'\0':
            out += m.mu.mem_read(got + len(out), 1)
        self.assertEqual(out.decode(), texts[0])

    def test_the_value_is_the_setting_not_the_lagging_cache(self):
        """Camera 2026-10-07: the QS image followed the value one change late.
        The property object's +0x14 is a cache; the setting is in the storage's
        working bank (+0x790; bank B when the select byte is set)."""
        import struct as st
        import test_ui_strings as TS
        names = ['F2', 'F3']
        for blk, me in zip([sup('F2', 7), sup('F3', 8)], names):
            loader(self.lib, me, names)
            self.assertEqual(self.apply(blk)[0], OK)
        shared = chain.qs(self.cam.peek)[0][1]
        m = TS.Machine(TA.image(), self.cam)
        m.mu.mem_map(0xC3100000, 0x100000)

        def name(off):
            got = m.call(TS.RES, m.reader, off)[0]
            out = bytearray()
            while m.mu.mem_read(got + len(out), 1) != b'\0':
                out += m.mu.mem_read(got + len(out), 1)
            return out.decode()
        for sel, raw in ((0, chain.RES_RAW_A), (1, chain.RES_RAW_B)):
            m.mu.mem_write(chain.RES_BANK_SEL, bytes([sel]))
            for now, stale, who in ((8, 7, 'F3'), (7, 8, 'F2')):
                m.mu.mem_write(chain.RES_CACHE, st.pack('<I', stale))      # one change behind
                m.mu.mem_write(chain.RES_RAW_A, st.pack('<I', now if raw == chain.RES_RAW_A else 99))
                m.mu.mem_write(chain.RES_RAW_B, st.pack('<I', now if raw == chain.RES_RAW_B else 99))
                self.assertEqual(name(shared), f'{who}_QS', (sel, now, stale))

    def test_the_setting_address_from_its_derivation(self):
        """0xC31AE3B0 (MenuSettingStorage) + 0x4F0C / 0xA7D4 (the banks) + 0x790
        (resolution); the select byte at +0xB19C. 2026-10-07: bank A was
        miscomputed as 0xC31AFA4C and read 0 on the camera. All three copies
        (ui_strings.S, qs_layer.c, ui/chain.py) must agree with the derivation."""
        import re
        S = 0xC31AE3B0
        want = {'RES_BANK_SEL': S + 0xB19C, 'RES_RAW_A': S + 0x4F0C + 0x790, 'RES_RAW_B': S + 0xA7D4 + 0x790}
        self.assertEqual({k: getattr(chain, k) for k in want}, want)
        asm = (HERE / 'ui_strings.S').read_text()
        c = (HERE / 'qs_layer.c').read_text()
        for k, v in want.items():
            self.assertEqual(int(re.search(r'\.equ %s,\s*(0x[0-9A-Fa-f]+)' % k, asm).group(1), 16), v, k)
            self.assertEqual(int(re.search(r'#define %s\s+(0x[0-9A-Fa-f]+)u' % k, c).group(1), 16), v, k)

    def test_the_cameras_memory_resolves_to_og3k(self):
        """The camera, 2026-10-07 (read-only): SetMovRecSize 4; cache 4; select
        byte 0; bank B +0x790 = 3; the old wrong address 0; storage +0x4F0C.. =
        1, 1, 0xFA, 0xE10, 0xDC. Value 4 must be found (OG3K's names), not the
        issuer's (OG2K). ⚠ 0xC31B3A4C = 4 is the derivation, not yet a reading."""
        import struct as st
        import test_ui_strings as TS
        names = ['F2', 'F3']                             # F2 = the issuer, enum 7; F3 enum 4
        for blk, me in zip([sup('F2', 7), sup('F3', 4)], names):
            loader(self.lib, me, names)
            self.assertEqual(self.apply(blk)[0], OK)
        shared = chain.qs(self.cam.peek)[0][1]
        m = TS.Machine(TA.image(), self.cam)
        m.mu.mem_map(0xC3100000, 0x100000)
        S = 0xC31AE3B0
        snap = {0xC31ACC30: 4, 0xC31AFA4C: 0, 0xC31B9314: 3, S + 0x790: 0xFFFFFF00,
                S + 0x4F0C: 1, S + 0x4F10: 1, S + 0x4F14: 0xFA, S + 0x4F18: 0xE10, S + 0x4F1C: 0xDC,
                0xC31B3A4C: 4}
        for a, v in snap.items():
            m.mu.mem_write(a, st.pack('<I', v))
        m.mu.mem_write(0xC31B954C, bytes([0]))
        got = m.call(TS.RES, m.reader, shared)[0]
        out = bytearray()
        while m.mu.mem_read(got + len(out), 1) != b'\0':
            out += m.mu.mem_read(got + len(out), 1)
        self.assertEqual(out.decode(), 'F3_QS')


class TestEnumTable(unittest.TestCase):
    """ui/enums.py: the one table of resolution values (user decision 2026-10-07, A)."""

    def test_values(self):
        from ui import enums
        self.assertEqual(enums.RESOLUTION, {'FHD': 2, 'UHD': 3, 'OG3K': 4, 'S16': 5, 'OG2K': 7,
                                            'OG4K': 8, 'OG3.5K': 9})

    def test_build_refuses_unlisted_or_wrong_values(self):
        from ui import enums
        for label, value in (('OG3K 3008x2000', 5), ('NEW 1x1', 6), ('FHD 1x1', 2), ('OG2K 2000x1334', 4)):
            with self.assertRaises(enums.EnumError, msg=(label, value)):
                O.resolution_qs(label, 'x', ('a_QS', 'a_SET', 'a_FONT'), value, layer())
        O.resolution_qs('OG2K 2000x1334', 'OG2K', ('a_QS', 'a_SET', 'a_FONT'), 7, layer())

    def test_table_refuses_a_duplicate(self):
        from ui import enums
        saved = dict(enums.RESOLUTION)
        try:
            enums.RESOLUTION['DUP'] = 4
            with self.assertRaises(enums.EnumError):
                enums._check_table()
        finally:
            enums.RESOLUTION.clear(); enums.RESOLUTION.update(saved)


if __name__ == '__main__':
    unittest.main()
