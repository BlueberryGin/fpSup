#!/usr/bin/env python3
"""Loader v3's built-in SYSTEM page 6 (allsups.c) under unicorn: the real
loader.S and a LOADER.BIN built with allsups=True load \\fpSup\\UI\\ALLSUPS.BIN
from a card, against the real image.  The UI app is test_ft6's fake (the stock
NBR loader C05E84D8 is Python); uishare's string layer and the page-id layer
run for real.

    cd fpSup/fp_usb_shell && python3 -B -m unittest v3.test_allsups
"""
import pathlib
import struct
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
TAB = HERE.parents[1] / 'uishare' / 'tab'
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(TAB))
import test_ft6 as F6                                         # noqa: E402 (fake app, sys.path)
import build_ft6 as FT                                        # noqa: E402
import build_v3 as B                                          # noqa: E402
import test_v3 as T                                           # noqa: E402

DATA_PATH = '\\fpSup\\UI\\' + B.ALLSUPS_FILE
SELF_PATH = '\\fpSup\\LOADER.BIN'

_cache = {}


def built():
    if not _cache:
        _cache['vbin'], _cache['info'] = B.loader_bin(allsups=True)
        _cache['data'], _cache['dinfo'] = FT.data_file()
    return _cache


def loader_with(ids=()):
    """LOADER.BIN with these sup_ids on its disabled list (sloader.c DS_*)."""
    v = bytearray(built()['vbin'])
    at = len(v) - (8 + 4 * B.DS_MAX)
    assert struct.unpack_from('<II', v, at) == (B.DS_MAGIC, 0)
    struct.pack_into('<II', v, at, B.DS_MAGIC, len(ids))
    for i, sid in enumerate(ids):
        v[at + 8 + 4 * i:at + 12 + 4 * i] = sid.encode()
    return bytes(v)


def disabled(blob):
    at = len(blob) - (8 + 4 * B.DS_MAX)
    magic, n = struct.unpack_from('<II', blob, at)
    assert magic == B.DS_MAGIC
    return [blob[at + 8 + 4 * i:at + 12 + 4 * i].decode() for i in range(n)]


def boot(data=True, sups=(), vbin=None, **kw):
    b = built()
    vbin = vbin or b['vbin']
    files = {'\\fpSup\\LOADER.BIN': vbin}
    ents = [('LOADER.BIN', 0x20, len(vbin)), ('UI', 0x10, 0)]
    if data:
        files[DATA_PATH] = data if isinstance(data, bytes) else b['data']
    for name, blob in sups:
        files['\\fpSup\\' + name] = blob
        ents.append((name, 0x20, len(blob)))
    cam = F6.Cam(files, {'\\fpSup': ents}, **kw)
    cam.boot()
    return cam


def log(cam):
    return [(code, a, b, name) for code, a, b, name in cam.log()]


def block(cam):
    return next(a for code, a, _b, name in cam.log() if code == 'LOADED' and name == 'LOADER.BIN')


def lines(cam):
    return [t for code, _a, _b, t in cam.log() if t.startswith('FT6')]


def tab_word(cam, i):
    return cam.w(block(cam) + 4 * i)


def soff(cam):
    """{text: offset} the string layer handed out (struct ft6_tab soff)."""
    strings = built()['dinfo']['strings']
    return {t: cam.w(block(cam) + tab_word(cam, 6) + 4 * i) for i, t in enumerate(strings)}


def card_sups():
    """A sup that loads, one that does not (bad header), one that is off, one
    entered that answered RELEASE (its claim refused: kind 9)."""
    ok = T.test_sup('TOKA', [])
    loss = T.test_sup('LOSS', [])                         # a product the table knows
    img = T.IMAGE.read_bytes()
    rel = T.test_sup('TREL', [(T.SITE_A, T.word(img, T.SITE_A), 9, 0)])
    dis = T.test_sup('DSBL', [])                          # on the disabled list (DIS_CARD)
    return [('10TEST.BIN', loss), ('20BAD.BIN', b'NOPE' * 8), ('30OLD.OFF', ok), ('40REL.BIN', rel),
            ('50DIS.BIN', dis)]


KEY_OK = 1000 + 1 * 10 + 1          # written, the list found at boot (allsups.c)


def parts(cam, k):
    v = cam.vars
    return tuple(v[f'FT_{p}{k}'][1] for p in 'NGVRE')


class AllSupsTests(unittest.TestCase):
    def test_loads_last_as_the_loader(self):
        cam = boot()
        self.assertEqual(lines(cam), ['FT6 s=00'])
        loaded = [name for code, _a, _b, name in cam.log() if code == 'LOADED']
        self.assertEqual(loaded[-1], 'LOADER.BIN')
        (r, sp, pack), = cam.nbr_calls
        self.assertEqual(r[0], F6.APP)
        self.assertEqual(sp & 7, 0)
        self.assertEqual(cam.machines()[0], FT.MACHINE)
        self.assertNotEqual(cam.w(F6.SITE), 0xEB000005)

    def test_every_private_field_gets_its_layer_offset(self):
        from ui import chain
        cam = boot()
        (_r, _sp, pack), = cam.nbr_calls
        offs = soff(cam)
        for text, off in offs.items():
            self.assertGreaterEqual(off, 0x40000000, text)
            self.assertEqual(chain.resolve(cam.w, off, F6.READER), text)
        data = built()['data']
        n_refs, refs_off = struct.unpack_from('<II', data, 28)
        strings = built()['dinfo']['strings']
        for i in range(n_refs):
            at, k = struct.unpack_from('<II', data, refs_off + 8 * i)
            self.assertEqual(struct.unpack_from('>I', pack, at)[0], offs[strings[k]])   # pack offsets
        names = [chain.name(cam.w, chain.outer(cam.w, 'resolve'))]
        self.assertEqual(names, ['LOADER'], "the layer carries the loader's name")

    def test_the_list_and_its_variables(self):
        cam = boot(sups=card_sups())
        v = cam.vars
        self.assertEqual(v['FT_Key'], (0, 0))
        self.assertEqual(v['FT_Focus'], (0, 0))
        ver = 'v' + built()['dinfo']['versions']['LOSS']
        self.assertEqual(parts(cam, 1), ('Lossless', ' ', ver, ' ', ' '), 'loaded: white name, version')
        self.assertEqual(parts(cam, 2), (' ', 'BAD', ' ', 'Load failed', ' '), 'no header: file name, grey')
        self.assertEqual(parts(cam, 3), (' ', 'OLD', ' ', ' ', ' '), 'off: grey, no digits')
        self.assertEqual(parts(cam, 4), (' ', 'REL', ' ', 'Load failed', ' '), 'entered, answered RELEASE')
        self.assertEqual(parts(cam, 5), ('DIS', ' ', ' ', ' ', ' '), 'the list is empty: it loaded')
        for k in range(6, 18):
            self.assertEqual(parts(cam, k), (' ',) * 5)
        self.assertEqual(len([n for n in v if n.startswith('FT_')]), 2 + 5 * 17 + 1)
        self.assertEqual(list(v)[:4], ['FT_Key', 'FT_OK', 'FT_R1', 'FT_R2'], 'the posted ones registered first')
        self.assertIn(('RELEASED', 1, 0, '40REL.BIN'), log(cam))
        for obj, name, fn in cam.subs:
            self.assertEqual(obj, F6.GUI_OBJECT)
            self.assertTrue(fn & 1, f'{name}: a Thumb subscriber')

    def test_the_footer_follows_the_cursor(self):
        cam = boot(sups=card_sups(), vbin=loader_with(['DSBL']))
        ok, ins, none = (f'<!font_G_bt_ok_K153,CENTER>{w}<!28sp,CENTER><!font_G_bt_menu_K153,CENTER>Cancel'
                         for w in ('Uninstall', 'Install', '')) 
        none = '<!font_G_bt_menu_K153,CENTER>Cancel'
        self.assertEqual(cam.vars['FT_OK'][1], ok, 'cell 1 at boot: a loaded sup')
        self.assertEqual(sorted(n for _o, n, _f in cam.subs), ['FT_Focus', 'FT_Key'])
        for focus, want in ((4, ins), (1, none), (2, none), (0, ok), (5, none), (16, none)):
            cam.posts.clear()
            cam.notify('FT_Focus', focus)
            self.assertEqual(cam.posts, [('FT_OK', want)], focus)
        cam.posts.clear()
        cam.press(1)                                          # uninstalled: OK now installs
        self.assertIn(('FT_OK', ins), cam.posts)

    def test_a_sup_takes_its_cells_ael(self):
        from armasm import assemble
        btn = B.make_sup(assemble(HERE / 'button_sup.S'), B.SL_HEADER_LEN, 'BTNS', min_svc=1)
        cam = boot(sups=card_sups() + [('60BTN.BIN', btn)])
        foot6 = ('<!font_G_bt_ok_K153,CENTER>Uninstall<!28sp,CENTER>'
                 '<!font_G_bt_AEL_K153,CENTER>Settings<!28sp,CENTER><!font_G_bt_menu_K153,CENTER>Cancel')
        cam.posts.clear()
        cam.notify('FT_Focus', 5)
        self.assertEqual(cam.posts, [('FT_OK', foot6)], 'cell 6 (60BTN) offers AEL')
        for n in (1, 2):
            cam.posts.clear()
            cam.press(100 + 6)
            self.assertEqual(cam.posts, [('FT_Key', 2000 + 6 + 100 * n)], 'its handler ran, answered n')
        cam.posts.clear()
        cam.press(100 + 1)                                   # Lossless took nothing: nothing runs
        self.assertEqual(cam.posts, [('FT_Key', 2000 + 1 - 100)])
        self.assertEqual(disabled(cam.written.get('\\fpSup\\LOADER.BIN', built()['vbin'])), [],
                         'AEL is not OK: nothing went on the list')

    def test_a_sup_on_the_list_is_not_loaded(self):
        cam = boot(sups=card_sups(), vbin=loader_with(['DSBL']))
        self.assertIn((19, struct.unpack('<I', b'DSBL')[0], 0, '50DIS.BIN'), log(cam))   # L_DISABLED
        self.assertNotIn('50DIS.BIN', [n for c, _a, _b, n in cam.log() if c in ('LOADED', 'RELEASED')])
        self.assertEqual(parts(cam, 5), (' ', 'DIS', ' ', ' ', ' '), 'grey, nothing pending')

    def test_pressing_a_cell_writes_the_list_into_loader_bin(self):
        cam = boot(sups=card_sups(), vbin=loader_with(['DSBL']))
        original = loader_with(['DSBL'])
        steps = [(1, ['DSBL', 'LOSS'], 'Unload on restart', ' '),       # loaded -> off at the next boot
                 (1, ['DSBL'], ' ', ' '),                        # and back
                 (5, [], ' ', 'Load on restart'),                      # on the list -> on at the next boot
                 (4, ['TREL'], ' ', ' '),                        # failed: off (nothing to say)
                 (4, [], 'Load failed', ' ')]
        for k, want, red, green in steps:
            cam.posts.clear()
            cam.press(k)
            got = cam.written['\\fpSup\\LOADER.BIN']
            self.assertEqual(len(got), len(original), 'the same length: no tail can survive')
            self.assertEqual(got[:-(8 + 4 * B.DS_MAX)], original[:-(8 + 4 * B.DS_MAX)], 'only the list changes')
            self.assertEqual(sorted(disabled(got)), sorted(want), k)
            self.assertEqual(cam.posts[:2], [(f'FT_R{k}', red), (f'FT_E{k}', green)])
            self.assertEqual(cam.posts[2][0], 'FT_OK')
            self.assertEqual(cam.posts[3], ('FT_Key', KEY_OK))
        self.assertEqual(cam.renames, [], 'no file is renamed')
        for k in (0, 2, 3, 6, 17, 18):          # no header id, a .OFF file, blank cells, past the cells
            cam.posts.clear()
            before = cam.written['\\fpSup\\LOADER.BIN']
            cam.press(k)
            self.assertEqual(cam.posts, [], k)
            self.assertEqual(cam.written['\\fpSup\\LOADER.BIN'], before, k)

    def test_a_failed_write_says_so_and_keeps_the_list(self):
        cam = boot(sups=card_sups(), write_ok=False)
        self.assertEqual(cam.posts, [('FT_Key', 3100 + 0)], 'boot: 3000 + list found x 100 + subscribe (0)')
        cam.posts.clear()
        cam.press(1)
        self.assertEqual([p for p in cam.posts if p[0] != 'FT_OK'],
                         [('FT_R1', 'Write failed'), ('FT_E1', ' '), ('FT_Key', 1000 + 6 * 10 + 1)])
        cam.write_ok = True
        cam.press(5)                                      # 50DIS is not on the list: it loaded
        self.assertEqual(disabled(cam.written['\\fpSup\\LOADER.BIN']), ['DSBL'], 'LOSS was not left on it')

    def test_page_ids(self):
        cam = boot()
        for name in ('FT_Y6', 'FT_LIST'):
            self.assertEqual(cam.page_id(name), FT.GUI_ID)
        img = T.IMAGE.read_bytes()
        import sl_pages as SL
        for name in ('MainY5', 'MainB1'):
            self.assertEqual(cam.page_id(name), SL.gui_id(img, name), name)

    def test_no_data_file_changes_nothing(self):
        cam = boot(data=False)
        self.assertIn(('OPEN_FAIL', 0, 0, 'UI\\' + B.ALLSUPS_FILE), log(cam))
        self.assertEqual(cam.w(F6.SITE), 0xEB000005)
        self.assertEqual((cam.nbr_calls, cam.vars, cam.subs), ([], {}, []))
        self.assertEqual([c for c, *_ in cam.log()][-1], 'DONE')

    def test_bad_data_file_changes_nothing(self):
        bad = bytearray(built()['data'])
        bad[0] ^= 1
        cam = boot(data=bytes(bad))
        self.assertIn('BAD_HEADER', [c for c, *_ in cam.log()])
        self.assertEqual(cam.w(F6.SITE), 0xEB000005)

    def test_refused_registration_keeps_only_the_string_layer(self):
        cam = boot(nbr_result=1)
        self.assertEqual(lines(cam), ['FT6 s=06'])
        self.assertEqual(cam.w(F6.SITE), 0xEB000005)
        self.assertNotIn('RELEASED', [c for c, *_ in cam.log()])

    def test_site_held_exclusively_releases_the_page(self):
        img = T.IMAGE.read_bytes()
        other = T.test_sup('OTHR', [(F6.SITE, T.word(img, F6.SITE), 2, 0)])
        cam = boot(sups=[('00OTHR.BIN', other)])
        self.assertEqual(lines(cam), ['FT6 s=01'])
        self.assertIn(('RELEASED', 1, 0, 'LOADER.BIN'), log(cam))
        for site in FT.STRING_SITES:
            self.assertEqual(cam.w(site), T.word(img, site))
        self.assertEqual((cam.vars, cam.subs), ({}, []))
        self.assertEqual([c for c, *_ in cam.log()][-1], 'DONE')

    def test_power_off_puts_every_site_back(self):
        cam = boot()
        img = T.IMAGE.read_bytes()
        cam.power_off()
        for site in (F6.SITE,) + FT.STRING_SITES:
            self.assertEqual(cam.w(site), T.word(img, site), hex(site))

    def test_without_allsups_the_loader_is_byte_for_byte_the_releases(self):
        import hashlib
        vbin, _ = B.loader_bin()
        self.assertEqual(hashlib.sha256(vbin).hexdigest()[:8], '41f3218a',
                         'the v3 releases share this LOADER.BIN (sigmafp-re-45, 2026-10-07)')


if __name__ == '__main__':
    unittest.main()
