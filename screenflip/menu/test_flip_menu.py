"""The Screen Flip row, on the pinned image (out/seg0_c0000000.bin).

  - the block, applied by the reference applier to the stock Y2_5_1, gives
    the builder's whole page (build_flip_fpui.build proves it; asserted again);
  - read back from the composed bytes, the page says what the row is meant to
    be: a fifth TOOLS row at y 405 under tab "3", shown in every mode, bound to
    MV_fpScreenFlip, popup and summary Off / 180 / Mirror / 180+Mirror, title
    "Screen Flip", nothing of the Time Code row it was cloned from left bound;
  - uishare/ui_apply.c gives byte for byte what the reference gives, alone
    and on one card with the Lossless row (MainB2) in either order, and never
    writes the firmware image;
  - mutations of the builder and of row_block are caught.
"""
import contextlib
import ctypes as ct
import pathlib
import struct
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
FPSUP = HERE.parents[1]
ROOT = FPSUP.parent
sys.path.insert(0, str(FPSUP / 'uishare'))
sys.path.insert(0, str(ROOT / 'research/ui/tools'))
sys.path.insert(0, str(HERE))
from ui import fpui, rows                  # noqa: E402
import test_ui_apply as TA                 # noqa: E402
import build_flip_page as F                # noqa: E402
import build_flip_fpui as FU               # noqa: E402

POOL, POOL_LEN = 0xC18C0474 - 0xC0000000, 176152
SETS = [('MainB2', 0x76FF04), ('Y2_5_1', F.ENTRY_OFFSET)]
OK = 0


def seg0():
    return F.SEG0.read_bytes()


def facts(page, pool):
    """What the composed page says about the new row, read from its bytes."""
    import nbu_components as N
    schemas = N.BM.load_property_schemas(seg0())
    objects, components, clips = N.walk(page, pool, schemas)
    row = [i for i, (n, _) in objects.items() if n == F.ROW_NAME]
    out = {'rows_named': len(row)}
    if len(row) != 1:
        return out
    root = row[0]
    ids = N.subtree(objects, root)
    out['parent'] = objects[root][1]
    out['tab_children'] = sum(1 for _, p in objects.values() if p == F.TAB_ID)
    mine = [c for c in components if c[1] in ids]
    base = [c for c in mine if c[1] == root and c[3] == 'objectBase']
    out['y'] = struct.unpack('>f', bytes.fromhex(base[0][5]['position'])[4:])[0] if base else None
    out['mode_keys'] = [k[1] for o, owner, cid, props in clips if owner == root
                        for name, dim, keys in props if name == 'is-visible' for k in keys]
    strings = [v for c in mine for v in c[5].values() if isinstance(v, str)]
    out['bound'] = sorted({v for v in strings if v.startswith('MV_')})
    by_name = {}
    for i in ids:
        by_name.setdefault(objects[i][0], []).append(i)
    texts = {}
    for c in mine:
        if c[3] == 'drawText':
            texts.setdefault(objects[c[1]][0], []).append((c[1], c[5].get('text')))
    popup = []
    for slot in ('00', '01', '02', '03'):
        kids = [i for i in ids if objects[i][1] in by_name.get(slot, []) and objects[i][0] == 'Text01']
        popup += [t for i, t in texts.get('Text01', []) if i in kids]
    out['popup'] = popup
    out['title'] = [t for _, t in texts.get('Name', [])]
    out['summary'] = [t for _, t in texts.get('IconText01', [])]
    out['clip_texts'] = {objects[owner][0]: [k[1] for name, dim, keys in props if name == 'text' for k in keys]
                         for o, owner, cid, props in clips if owner in ids
                         and any(name == 'text' for name, _, _ in props)}
    # the page's mode group lists the row with its clip
    import native_ui_audit as A        # noqa: F401  (records only)
    out['mode_roots'] = []
    off = 0
    while off + 8 <= len(page):
        tag, size = struct.unpack_from('>II', page, off)
        if size < 8:
            break
        if tag == 0x1000B:
            rec = page[off:off + size]
            name_at = struct.unpack_from('>I', rec, 20)[0]
            if bytes(pool[name_at:name_at + 11]) == b'STILL_CINE\0':
                at = 28 + 21 * struct.unpack_from('>I', rec, 8)[0]
                n = struct.unpack_from('>I', rec, 16)[0]
                out['mode_roots'] = [struct.unpack_from('>I', rec, at + 8 * i + 4)[0] for i in range(n)
                                     if struct.unpack_from('>I', rec, at + 8 * i)[0] == root]
        off += size
    return out


WANT = {'rows_named': 1, 'parent': F.TAB_ID, 'tab_children': 6, 'y': 405.0, 'mode_keys': [1, 1, 1],
        'bound': ['MV_fpScreenFlip'], 'popup': list(F.LABELS), 'title': [F.TITLE],
        'summary': [F.LABELS[0]], 'mode_roots': [2],
        'clip_texts': {'Name': [F.TITLE] * 3, 'IconText01': list(F.LABELS)}}


def reference(blocks):
    img = seg0()
    pages = {}
    for name, off in SETS:
        a = 0xC18C0460 + off - 0xC0000000
        n = F.PAGE_END - F.PAGE_START if name == 'Y2_5_1' else 0x2055DB0 - 0x2030364
        pages[name] = fpui.PageCopy(img[a:a + n], n, 1)
    pool = fpui.Strings(img[POOL:POOL + POOL_LEN])
    for b in blocks:
        fpui.apply(b, pages, pool)
    return pages, pool


@unittest.skipIf(not F.SEG0.exists(), 'needs the pinned image')
class RowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob, cls.info, (cls.stock, cls.pool_stock, cls.page, cls.manifest) = FU.build()

    def test_the_block_gives_the_builder_page(self):
        rows.prove_same(self.blob, 'Y2_5_1', self.stock, self.page, self.manifest,
                        self.pool_stock, lambda s: FU.P.stock_at(self.pool_stock, s.encode()))

    def test_the_composed_page_is_the_row_we_meant(self):
        pages, pool = reference([self.blob])
        self.assertEqual(facts(bytes(pages['Y2_5_1'].data), pool.view()), WANT)

    def test_the_block_neither_hooks_nor_touches_files(self):
        _, ops, _ = fpui.decode(self.blob)
        codes = {c for c, _ in ops}
        self.assertFalse(codes & {fpui.OP_HOOK_FV, fpui.OP_FILE, fpui.OP_FILE_SET, fpui.OP_CSV_ADD})
        self.assertEqual(sum(1 for c, _ in ops if c == fpui.OP_PAGE), 1)

    def test_a_second_row_on_the_tab_gets_the_next_slot(self):
        pages, pool = reference([self.blob, self.blob])
        ys = sorted(struct.unpack('>f', bytes.fromhex(c[5]['position'])[4:])[0]
                    for c in self._row_bases(bytes(pages['Y2_5_1'].data), pool.view()))
        self.assertEqual(ys, [405.0, 486.0])

    def _row_bases(self, page, pool):
        import nbu_components as N
        objects, components, _ = N.walk(page, pool, N.BM.load_property_schemas(seg0()))
        roots = {i for i, (n, _) in objects.items() if n == F.ROW_NAME}
        return [c for c in components if c[1] in roots and c[3] == 'objectBase']


class Camera(TA.Camera):
    def __init__(self, lib):
        self.lib = lib
        names = b''.join(n.encode() + b'\0' for n, _ in SETS)
        offs = (ct.c_uint32 * len(SETS))(*[o for _, o in SETS])
        img = TA.image()
        lib.fx_reset(img, len(img), names, offs, len(SETS))

    def page_of(self, name):
        i = [n for n, _ in SETS].index(name)
        p = (TA.NBU + self.lib.fx_peek(self.get(TA.ENTRY_TABLE) + 44 * i + 8)) & 0xFFFFFFFF
        if self.lib.fx_peek((p & ~3) - 128) != 0x47505346:
            return None
        return self.read(p, self.lib.fx_peek((p & ~3) - 128 + 12))


@unittest.skipIf(not F.SEG0.exists(), 'needs the pinned image')
class ApplierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='fpflip-')
        cls.lib = TA.build(cls.tmp.name)
        cls.flip = FU.build()[0]
        cls.lossless = TA.lossless()[0]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def same(self, blocks):
        cam = Camera(self.lib)
        for b in blocks:
            r, out = cam.apply(b)
            self.assertEqual(r, OK, out)
        pages, pool = reference(blocks)
        for name, _ in SETS:
            want = bytes(pages[name].data) if pages[name].deltas else None
            self.assertEqual(cam.page_of(name), want, name)
        self.assertEqual(cam.layers(), pool.layers)
        self.assertTrue(cam.pool_untouched(), 'the stock pool was replaced')
        self.assertEqual(cam.get(TA.NBU_WRITES), 0, 'the firmware image was written')
        self.assertEqual(cam.get(TA.DIRTY), 0, 'switched in before published')
        return pages, pool

    def test_alone(self):
        pages, pool = self.same([self.flip])
        self.assertEqual(facts(bytes(pages['Y2_5_1'].data), pool.view()), WANT)

    def test_with_lossless_either_order(self):
        for order in ([self.lossless, self.flip], [self.flip, self.lossless]):
            with self.subTest(first=len(order[0])):
                pages, pool = self.same(order)
                self.assertEqual(facts(bytes(pages['Y2_5_1'].data), pool.view()), WANT)


@contextlib.contextmanager
def patched(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


@unittest.skipIf(not F.SEG0.exists(), 'needs the pinned image')
class MutationTests(unittest.TestCase):
    """Each mutation must make the row wrong (facts) or the build refuse."""

    def outcome(self):
        try:
            blob = FU.build()[0]
        except (ValueError, fpui.FpuiError):
            return 'refused'
        pages, pool = reference([blob])
        return facts(bytes(pages['Y2_5_1'].data), pool.view())

    def test_every_mutation_is_caught(self):
        fields = dict(F.PRIVATE_FIELDS)
        one_var_less = {k: v for k, v in fields.items() if k != 0x2B4FEBB}
        labels = dict(F.TEXT_ANIMATIONS)
        labels[0x2B50BCF] = {**labels[0x2B50BCF], '1184_R1': F.LABELS[1]}
        popup_wrong = dict(fields)
        popup_wrong[0x2B5066D] = (32, '1184', F.LABELS[1])
        real_row_block = rows.row_block

        def clip1(*a, **kw):
            kw['root_clip'] = 1
            return real_row_block(*a, **kw)
        cases = [
            ('one variable field left on the Time Code value', F, 'PRIVATE_FIELDS', one_var_less),
            ('the summary says 180 for Mirror', F, 'TEXT_ANIMATIONS', labels),
            ('the row stays on tab 1', F, 'TAB_ID', 92),
            ('the mode group gets clip 1', rows, 'row_block', clip1),
            ('popup row 02 says 180', F, 'PRIVATE_FIELDS', popup_wrong),
        ]
        for why, obj, name, value in cases:
            with self.subTest(why):
                with patched(obj, name, value):
                    got = self.outcome()
                self.assertNotEqual(got, WANT, why)

    def test_the_visibility_keys_matter(self):
        real = F.build

        def cine_only(source, audit):
            ww = {F.DONOR_START: [(24, F.DONOR_PARENT, F.TAB_ID, 'reparent_tab1_to_tools_tab')]}
            orig = F.BM.Remapper.__init__

            def init(self, pool, mapping, ids, schemas=None, profile=None, shared_calls=None):
                if profile is not None and 'word_writes' in profile:
                    profile = dict(profile, word_writes=ww)
                orig(self, pool, mapping, ids, schemas, profile, shared_calls)
            with patched(F.BM.Remapper, '__init__', init):
                return real(source, audit)
        with patched(F, 'build', cine_only):
            got = self.outcome()
        self.assertNotEqual(got, WANT)
        self.assertEqual(got.get('mode_keys'), [0, 1, 0])


if __name__ == '__main__':
    unittest.main()
