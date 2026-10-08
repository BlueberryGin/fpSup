#!/usr/bin/env python3
"""SYSTEM page 6 ("All Sups"): \\fpSup\\UI\\ALLSUPS.BIN, the data of Loader v3's
built-in page (fpSup/fp_usb_shell/v3/allsups.c; LOADER.BIN with
build_v3.loader_bin(allsups=True)).

    python3 -B fpSup/uishare/tab/build_ft6.py [-o ALLSUPS.BIN]

Pages: ft6_page.py (FT_Y6, FT_LIST); pack format: sl_pages.py; the record of
what was proven on the camera: projects/usb-shell-sup/notes/ALL_SUPS_MENU.md.
(r1 / r2 were the stand-alone sup 90FTAB6.BIN; its cards are in
projects/usb-shell-sup/cards/20261007-ft6-*.)
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / 'projects/open-gate/build'), str(ROOT / 'fpSup/fp_usb_shell'),
                str(ROOT / 'fpSup/fp_usb_shell/v3'), str(ROOT / 'research/ui/tools'),
                str(ROOT / 'research/ui/tools/nbu_probe'), str(ROOT / 'fpSup/uishare'), str(HERE)]
import nbr as NBR                 # noqa: E402
import sl_pages as SL             # noqa: E402
import build_v3 as B              # noqa: E402

PAGE = 'FT_Y6'
BASE = 'MainY5'
MACHINE = 'TransFtTab'            # allsups.c checks the last machine's name
INDEX_NAME = 'ft_pages'
GUI_ID = 21                       # allsups.c's layer: MainY5's
TRANSITIONS = (                   # (from, to, request); stock's are TransMainMenu's
    ('MainY5', PAGE, 'Menu01'),   # Y5 Right (stock: -> MainB1)
    ('MainB1', PAGE, 'Menu13'),   # B1 Left  (stock: -> MainY5)
    (PAGE, 'MainB1', 'Menu01'),   # the copy's Right
    (PAGE, 'MainY5', 'Menu12'),   # the copy's Left (MainY5 sends Menu12 = "to Y4")
    (PAGE, 'MainY5', 'Menu13'),
)


def build_pack(pages, machine=MACHINE, trans=TRANSITIONS, index_name=INDEX_NAME) -> bytes:
    """sl_pages.build_pack with the transitions given, not parent / Return."""
    pool, offs = bytearray(4), {}

    def intern(s):
        if s not in offs:
            offs[s] = len(pool)
            pool.extend(s.encode() + b'\0')
            pool.extend(bytes(-len(pool) % 4))
        return offs[s]

    intern(index_name)
    for n, _ in pages:
        intern(n)
    states = []
    for a, b, _ in trans:
        for s in (a, b):
            if s not in states:
                states.append(s)
    for t in [machine] + states + [e for *_, e in trans]:
        intern(t)
    sm = struct.pack('>III', 0x50001, 12, 1)
    sm += struct.pack('>II9I', 0x50002, 44, len(states), 0, 0, 0, 0, 0, 0, len(trans), offs[machine])
    for st in states:
        sm += struct.pack('>II3I', 0x50003, 20, offs[st], 0, 0)
    for a, b, e in trans:
        sm += struct.pack('>II4I', 0x50004, 24, offs[a], offs[b], offs[e], 0)
    head = b'NBR\0' + struct.pack('>II', SL.VERSION, 0)
    pool_rec = struct.pack('>II', 1, 8 + len(pool)) + pool
    index_len = 8 + 8 + 32 * len(pages)
    at = len(head) + len(pool_rec) + index_len + len(sm) + len(SL.END)
    rows, body = [], b''
    for name, page in pages:
        if not page.endswith(SL.END):
            raise ValueError(f'page {name} does not end with an end record')
        rows.append((NBR.fnv1a(name), offs[name], 1, at + len(body)) + SL.ROW_TAIL)
        body += page
    rows.sort()
    index = struct.pack('>IIII', 0x10001, index_len, offs[index_name], len(rows))
    index += b''.join(struct.pack('>8I', *r) for r in rows)
    return head + pool_rec + index + sm + SL.END + body


# ---- Loader v3's built-in page: the data file ------------------------------------
TAB_OFF, TAB_ROOM = 0, 128
FT6_MAGIC = 0x54365446                      # "FT6T"
STRING_SITES = (0xC05E5B58, 0xC05E61C8, 0xC05E61E0)


ROWS = 17                                   # allsups.c FT6_ROWS: the language grid's cells
ROW_BYTES = 272                             # struct ft6_row
TEXTS = ('Unload on restart', 'Load on restart', 'Load failed', 'Write failed',   # allsups.c T_UNLOAD .. T_BLANK
         '\\' + 'fpSup', '.BIN', '.OFF', ' ',
         # the footer's pieces (allsups.c ft6_foot): the localized Footer05's own icon markup
         '<!font_G_bt_ok_K153,CENTER>', '<!font_G_bt_AEL_K153,CENTER>', '<!font_G_bt_menu_K153,CENTER>',
         '<!28sp,CENTER>', 'Uninstall', 'Install', 'Cancel')
# the product names (the user 2026-10-07), by the sup_id in each file's header;
# a sup not here shows its file name without the load-order digits.
PRODUCTS = {'SHEL': 'USB-Shell', 'GYR2': 'Gyro2', 'LOSS': 'Lossless', 'RCUS': 'RES-Custom',
            'RAWV': 'RAW-View', 'FLIP': 'Flip'}


def versions():
    """{sup_id: version} of the newest v3 release of each (fpSup/releases,
    build_catalogue_v3's rule) -- the version a product shows is the one this
    data file was built with: build it again when a release ships."""
    sys.path.insert(0, str(ROOT / 'fpSup/tools/merge-v3'))
    import build_catalogue_v3 as C
    out = {}
    for d in (ROOT / 'fpSup/releases').glob('fpsup-*-v*'):
        if not (d / 'fpSup' / 'LOADER.BIN').is_file():
            continue
        for f in (d / 'fpSup').iterdir():
            hdr = f.read_bytes()[:32] if f.is_file() and f.suffix.upper() == '.BIN' else b''
            if hdr[:4] != b'FSB1':
                continue
            sid = hdr[20:24].rstrip(b'\0').decode('ascii', 'replace')
            v = C.version_of(d)
            if sid not in out or C.version_key(v) > C.version_key(out[sid]):
                out[sid] = v
    return out
VAR_INT, VAR_TEXT = 0, 2
VAR_PARTS = 'RENGV'                         # registration order (allsups.c ft6_slot)
BLOCK_STRINGS = 60                          # < ui_apply.c MAX_STRINGS (64)
TAB_WORDS = 11 + len(TEXTS) + 3 + 4 + 3 + 7 + 5     # struct ft6_tab
LIST_TRANSITIONS = ((PAGE, 'FT_LIST', 'FT_LIST'), ('FT_LIST', PAGE, 'Return'))


def variables():
    import ft6_page as FP
    # the posted ones first: red, green (allsups.c ft6_slot), then name, grey, version
    return ([(VAR_INT, FP.KEY_VAR), (VAR_TEXT, FP.OK_VAR)] +
            [(VAR_TEXT, FP.part_var(part, k)) for part in VAR_PARTS for k in range(1, ROWS + 1)] +
            [(VAR_INT, FP.FOCUS_VAR)])


def data_file() -> tuple[bytes, dict]:
    """\\fpSup\\UI\\ALLSUPS.BIN, the data of Loader v3's built-in page
    (fp_usb_shell/v3/allsups.c), every offset from the file's first byte:

        [struct ft6_tab][FPUI block][texts, variable names][soff][refs][vars]
        [rows: ROWS x struct ft6_row, zero][NBR pack: FT_Y6, FT_LIST]

    The FPUI block holds every private string the pages use, one STRBASE each
    (the entry points its address at soff[string#]); refs say which BE page
    field takes which string's offset."""
    import ft6_page as FP
    from ui import fpui
    img = (ROOT / 'out/MAIN_c0000000.bin').read_bytes()
    if SL.gui_id(img, BASE) != GUI_ID:
        raise SystemExit(f'{BASE} GUI id is not {GUI_ID}')
    stock = [struct.unpack('<I', B.stock(a, 4, img))[0] for a in (0xC056461C,) + STRING_SITES]
    if stock[0] != 0xEB000005:
        raise SystemExit('0xC056461C is not the stock bl FUN_c0564638')
    _img, pool = FP.stock_pool()
    y6, refs6 = FP.build(img, pool)
    lst, refsl = FP.build_grid(img, pool)
    pack = build_pack([(PAGE, y6), ('FT_LIST', lst)], trans=TRANSITIONS + LIST_TRANSITIONS)
    rows = {n: o for n, o, _h in SL.native_rows(pack)}
    refs = [(rows[PAGE] + o, t) for o, t in refs6] + [(rows['FT_LIST'] + o, t) for o, t in refsl]
    strings = list(dict.fromkeys(t for _o, t in refs))
    # A block is one layer, its offsets base, base + 1, ... in block order
    # (fpui.py), so one STRBASE -- of its first string -- says them all (a
    # block takes at most eight STRBASE and 64 strings: ui_apply.c); the cells'
    # variables need more strings than one block holds, so several blocks
    blocks = []
    for first in range(0, len(strings), BLOCK_STRINGS):
        blk = fpui.Block()
        for text in strings[first:first + BLOCK_STRINGS]:
            blk.string(text, fpui.NOT_STOCK)
        blk.op(fpui.OP_STRBASE, 0, 0)
        blocks.append((blk.encode(), first))
    ui = b''.join(b for b, _f in blocks)

    out = bytearray(4 * TAB_WORDS)
    out += b'\0' * (-len(out) % 8)

    def place(data, align=4):
        nonlocal out
        out += b'\0' * (-len(out) % align)
        at = len(out)
        out += data
        return at
    at = [(place(b, 8), len(b), first) for b, first in blocks]
    ui_off = place(b''.join(struct.pack('<3I', *e) for e in at))
    txt = []
    for text in TEXTS:
        txt.append(place(text.encode() + b'\0', 1))
    names = [place(name.encode() + b'\0', 1) for _t, name in variables()]
    vers = versions()
    prod = []
    for sid, title in PRODUCTS.items():
        v = vers.get(sid)
        prod.append((struct.unpack('<I', sid.encode().ljust(4, b'\0'))[0],
                     place(title.encode() + b'\0', 1), place(('v' + v).encode() + b'\0', 1) if v else 0))
    prod_off = place(b''.join(struct.pack('<3I', *p) for p in prod))
    soff_off = place(bytes(4 * len(strings)))
    refs_off = place(b''.join(struct.pack('<II', o, strings.index(t)) for o, t in refs))
    vars_off = place(b''.join(struct.pack('<II', ty, at) for (ty, _n), at in zip(variables(), names)))
    rows_off = place(bytes(ROWS * ROW_BYTES), 8)
    pack_off = place(pack, 8)
    tab = struct.pack('<11I', FT6_MAGIC, pack_off, len(pack), ui_off, len(blocks), len(strings), soff_off,
                      len(refs), refs_off, len(variables()), vars_off)
    tab += struct.pack(f'<{len(TEXTS)}I', *txt) + struct.pack('<III', rows_off, len(prod), prod_off)
    tab += struct.pack('<4I', *stock) + MACHINE.encode().ljust(12, b'\0')
    if len(tab) > 4 * TAB_WORDS:
        raise SystemExit('tab too big')
    out[:len(tab)] = tab
    return bytes(out), {'ui': len(ui), 'pages': (len(y6), len(lst)), 'pack': len(pack), 'versions': vers,
                        'strings': strings, 'refs': len(refs), 'file': len(out)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-o', type=Path)
    ap.add_argument('--data', action='store_true', help='(the default; kept for old commands)')
    a = ap.parse_args()
    data, info = data_file()
    a.o = a.o or HERE / 'out' / 'ALLSUPS.BIN'
    a.o.parent.mkdir(parents=True, exist_ok=True)
    a.o.write_bytes(data)
    print(a.o, info)


if __name__ == '__main__':
    main()
