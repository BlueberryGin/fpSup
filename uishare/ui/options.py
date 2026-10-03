"""add_option: a new entry in a stock list, as FPUI ops.

A list option is three things in the native UI (fp-native-ui §2):

  - a row in the list's CSV (and in every variant of it, the _EXCL ones);
  - the list's numeric limits in its resource set: every controller maximum
    that counts rows goes up by one, and anything else the list's layout
    keys on the row count (the COLOR list's text group end, +33 per row);
  - its labels: the collapsed summary's text for that state, or an icon.

The CSV cannot grow in place (its NBR slot is fixed), so every list's CSV is
served from a COPY through the shared file-redirect table (fv_handler.S at
C05E5BEC): each sup appends its row to the copy as it is now, so two sups
adding to one list both get a row. The index a sup's row gets is handed out
on the camera (CSV_ADD: the row count before it); a sup whose meaning is tied
to one index (raw-view's GUI 16, OpenGate's state 2) says so with EXPECT and
its row is left out, nothing else changed, when the list is not as it needs.

The limits are edited in a copy of the resource set (PAGE, as add_row), never
in the firmware image, so every boot starts from stock and two sups' +1s add
up. Labels that are per-state stock fields are set with SETSTR_SLOT.

Two recipes today, each proven against what its sup does now:
  color_raw()      COLOR list, row 17 RAW            (projects/rawview)
  resolution(lbl)  movie resolution, state 2 = OG*   (projects/open-gate)
"""
import functools
import json
import pathlib
import struct
import sys

from . import fpui

HERE = pathlib.Path(__file__).resolve().parent
FPSUP = HERE.parents[1]
ROOT = FPSUP.parent
IMAGE = ROOT / 'out' / 'MAIN_c0000000.bin'
NBU_BASE = 0xC18C0460
SETS = ROOT / 'research' / 'ui' / 'nbu_sets.json'

# The redirect site and its handler (fv_handler.S); raw-view's H1, generalised.
FV_SITE, FV_STOCK, FV_CAPACITY = 0xC05E5BEC, 0x60681840, 32
CAVE_BUMP, CAVE_ARENA_END, THUMB_VENEER = 0xC072E060, 0xC072EFB4, 0xF000F8DF


_IMAGE = None


def image():
    global _IMAGE
    if _IMAGE is None:
        _IMAGE = IMAGE.read_bytes()
    return _IMAGE


def at(img, a, n):
    return img[a - 0xC0000000:a - 0xC0000000 + n]


@functools.lru_cache(maxsize=None)
def resource_set(name):
    """(image address, runtime entry offset, length) of a resource set."""
    sets = json.loads(SETS.read_text())['sets']
    addrs = sorted(int(s['address'], 16) for s in sets)
    s = next(s for s in sets if s['name'] == name)
    a = int(s['address'], 16)
    nxt = min(x for x in addrs if x > a)
    fpui.check(int(s['offset'], 16) == a - NBU_BASE, 'set offset mismatch: ' + name)
    return a, a - NBU_BASE, nxt - a


@functools.lru_cache(maxsize=None)
def handler():
    """(bytes, entry offset, table word offset) of uishare/fv_handler.S."""
    sys.path.insert(0, str(FPSUP / 'fp_usb_shell'))
    from armasm import assemble, symbols
    src = FPSUP / 'uishare' / 'fv_handler.S'
    code, sym = assemble(src), symbols(src)
    return code, sym['fv_entry'], sym['fv_head'] + 8


def hook_fv(blk):
    code, entry, table_word = handler()
    blk.op(fpui.OP_HOOK_FV, FV_SITE, FV_STOCK, FV_CAPACITY, blk.fragment(code), len(code),
           entry, CAVE_BUMP, CAVE_ARENA_END, THUMB_VENEER, table_word)


def be_float_at(img, near, value):
    """The address of the big-endian float `value` that overlaps the word at
    `near` (the firmware's floats are unaligned; a word was written there)."""
    want = struct.pack('>f', float(value))
    hits = [a for a in range(near - 3, near + 4) if at(img, a, 4) == want]
    fpui.check(len(hits) == 1, 'no single %r near 0x%08X' % (value, near))
    return hits[0]


def page_ops(blk, img, set_name, edits, guards=()):
    """One PAGE over a resource set: edits are (op, image address, delta)."""
    a, off, n = resource_set(set_name)
    blk.op(fpui.OP_PAGE, blk.string(set_name, fpui.NAME), off, n, 1, 0, 0, 0)
    blk.op(fpui.OP_GUARD, 0, struct.unpack('>I', at(img, a, 4))[0])     # the set's header
    for g in guards:
        blk.op(fpui.OP_GUARD, g - a, struct.unpack('>I', at(img, g, 4))[0])
    for op, addr, delta in edits:
        fpui.check(a <= addr < a + n, '0x%08X outside %s' % (addr, set_name))
        blk.op(op, addr - a, delta)
    blk.op(fpui.OP_DONE)


def csv_file(blk, addr, size, adds, cells=(), growth=128):
    blk.op(fpui.OP_FILE, addr, size, growth)
    for tpl in adds:
        blk.op(fpui.OP_CSV_ADD, blk.string(tpl, fpui.NAME))
    for row, col, tpl in cells:
        blk.op(fpui.OP_CSV_CELL, row, col, blk.string(tpl, fpui.NAME))
    blk.op(fpui.OP_FILE_DONE)


# ---- COLOR: raw-view's RAW row ----------------------------------------------
COLOR_CSV = [   # (name, address, size, new row template, cell edits)
    ('ListColorButtonMenu', 0xC0D566EC, 453, '{N},set_flash_1,0,{Q},0,{P}',
     [(fpui.ROW_FIRST, 3, '{P}'), (fpui.ROW_LAST_BEFORE, 4, '{P}')]),
    ('ListColorButtonMenu_EXCL', 0xC0D568B4, 483, '{N},set_common_1,1,15,15,15', []),
    ('ListColorButtonMenu_EXCL2', 0xC0D56A98, 452, '{N},set_common_1,1,14,0,15', []),
]
COLOR_ROW_MAX = [0xC1D63010, 0xC1D631AC, 0xC1D5F200, 0xC1D62A7C, 0xC1D63F8C, 0xC1D65B24]
COLOR_TEXT_END = 0xC1D66CBC               # BE word: text group end, +33 per row
RAW_ICON = ('set_flash_1.xci', 0xC14EBAAC, 533)
RAWVIEW_EFFECT_END = 0xC1D640A8           # raw-view's: the Effect box covers its row
RAWVIEW_ADVANCED = 0xC1D6ED48             # raw-view's: AdvancedSettings Normal <= 16


def color_raw(icon_bytes, expect_index=16):
    """raw-view's COLOR row (its UI, without its semantics)."""
    img = image()
    blk = fpui.Block()
    hook_fv(blk)
    for name, addr, size, tpl, cells in COLOR_CSV:
        csv_file(blk, addr, size, [tpl], cells)
    blk.op(fpui.OP_EXPECT, 0, expect_index)
    name, addr, size = RAW_ICON
    blk.op(fpui.OP_FILE, addr, size, max(0, len(icon_bytes) - size))
    blk.op(fpui.OP_FILE_SET, blk.fragment(icon_bytes), len(icon_bytes))
    blk.op(fpui.OP_FILE_DONE)
    edits = [(fpui.OP_ADDF, be_float_at(img, w, 15), 1) for w in COLOR_ROW_MAX]
    edits.append((fpui.OP_ADD32, COLOR_TEXT_END, 33))
    edits.append((fpui.OP_ADDF, be_float_at(img, RAWVIEW_EFFECT_END, 15), 1))
    page_ops(blk, img, 'ColorButtonMenu', edits)
    # RAWVIEW_ADVANCED (ColorModeAdvancedSettings, the AEL page's MenuMode
    # range) is NOT done here: a copy of that set was not picked up on the
    # camera ("more options" missing, v0.2.3test, 2026-10-03), the in-place
    # word of v0.2.2test was. raw-view's launcher writes it in place again;
    # stage2 journals it (declared stock word) and power-off restores it.
    return blk


# ---- movie resolution: OpenGate's third state ----------------------------------
RES_CSV = ('B2_5_4', 0xC0F8E7EC, 89)
RES_LIST_MAX = 0xC1A709BC                 # BE float 1.0: List obj 4259, rows - 1
RES_SUMMARY = {2: 0xC1A7246B + 32}        # IconText03 drawText text, per state


def resolution(label, summary, expect_state=2):
    """OpenGate's resolution entry: a row whose state is 2 (the stock
    converter's spare pair (4, 2)), its summary text, the List max."""
    img = image()
    blk = fpui.Block()
    hook_fv(blk)
    name, addr, size = RES_CSV
    csv_file(blk, addr, size, ['{N},%s,Popup,,1,2' % label])
    blk.op(fpui.OP_EXPECT, 0, expect_state)
    a, off, n = resource_set('B2_5')
    s = blk.string(summary, fpui.NOT_STOCK)
    blk.op(fpui.OP_PAGE, blk.string('B2_5', fpui.NAME), off, n, 1, 0, 0, 0)
    blk.op(fpui.OP_GUARD, 0, struct.unpack('>I', at(img, a, 4))[0])
    blk.op(fpui.OP_ADDF, RES_LIST_MAX - a, 1)
    states = sorted(RES_SUMMARY)
    blk.op(fpui.OP_SETSTR_SLOT, 0, states[0], len(states), s,
           *[RES_SUMMARY[k] - a for k in states])
    blk.op(fpui.OP_DONE)
    return blk


# ---- the reference, run against the image ---------------------------------------
def run(blocks, img=None, pool_stock=None):
    """Apply blocks (encoded) in order with the reference applier against the
    stock image. Returns (pages, files, pool, results)."""
    img = img or image()
    pool_stock = pool_stock or at(img, 0xC18C0474, 176152)
    sets = json.loads(SETS.read_text())['sets']
    pages = {}
    for s in sets:
        a = int(s['address'], 16)
        if s['name'] in ('ColorButtonMenu', 'ColorModeAdvancedSettings', 'B2_5', 'MainB2'):
            _, _, n = resource_set(s['name'])
            pages[s['name']] = fpui.PageCopy(at(img, a, n), n, 1)
    files = fpui.Files(lambda a, n: at(img, a, n))
    pool = fpui.Pool(pool_stock)
    results = [fpui.apply(b, pages, pool, files) for b in blocks]
    return pages, files, pool, results
