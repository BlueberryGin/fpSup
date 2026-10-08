#!/usr/bin/env python3
"""FT_Y6, the SYSTEM page 6 (ALL_SUPS_MENU.md "第 6 頁"), r2: built from MainY5.

  rows       MainY5's six rows (Y5_1..Y5_6 under Y5) -> Y5_1's only, its Name
             drawText showing a private literal ("All Sups"); the word is left
             0 here and the sup's entry writes the offset its string layer got
  tab bar    HeaderActiveTab_Y's five segments -> six, at the SHOOT bar's
             places (HeaderActiveTab_B's rects, yellow): stock Tab05, bright at
             MenuTab's Y5 time, moves to place 6; place 5 is a new plain dark
             rect.  CM_MenuTabNo stays 12.
  header     allocation header: + the new segment, - what went (sl_size_page's way)

Everything else is MainY5's.  The row still asks for Y5_1 on Right / OK, which
no machine has from FT_Y6: nothing happens (the list page is r3).
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / 'projects/open-gate/build'), str(ROOT / 'research/ui/tools'),
                str(ROOT / 'research/ui/tools/nbu_probe'), str(ROOT / 'fpSup/lossless/menu')]
import nbu_format as N                    # noqa: E402
import native_ui_audit as A               # noqa: E402
import build_menu_candidate as BM         # noqa: E402
import sl_b25_page as P                   # noqa: E402
import sl_size_page as SZ                 # noqa: E402
import sl_pages as SL                     # noqa: E402
import sl_lab_page as Lab                 # noqa: E402
from sl_b25_page import check, put, u32   # noqa: E402

BASE = 'MainY5'
LABEL = 'All Sups'
ROWS_PARENT = 203                         # Y5
KEEP_ROW = 204                            # Y5_1 (日期 / 時間 / 區域)
ROW_ITEM, ROW_NAME = 3451, 3453           # its MenuItem_Jump and Name
BAR_Y = 41                                # HeaderActiveTab_Y
Y_TABS = (44, 45, 46, 47, 48)             # Tab01..Tab05
B_TABS = (22, 28, 27, 26, 25, 24)         # HeaderActiveTab_B Tab01..Tab06
Y_COLOUR, B_COLOUR = 0x554400FF, 0x114455FF


def stock_pool():
    img, base, _rows, (pb, ps) = N.load_sets()
    return img, P.Pool(img[pb - base:pb - base + ps])


def by_object(seq):
    """{object id: [indices of its records]} (the object record first)."""
    out, cur = {}, None
    for i, r in enumerate(seq):
        if u32(r, 0) == 0x10003:
            cur = u32(r, 20)
        if cur is not None:
            out.setdefault(cur, []).append(i)
    return out


def comp(seq, idx, name, pool):
    hits = [i for i in idx if u32(seq[i], 0) != 0x10003 and pool.text(u32(seq[i], 8)) == name]
    check(len(hits) == 1, f'{name}: {len(hits)} records')
    return hits[0]


def owned(rec, owner):
    """A component record of another object, re-owned (the owner word is +20)."""
    d = bytearray(rec)
    put(d, 20, owner)
    return d


def text_field(rec, pool, schemas):
    texts = [off for off, kind in P.typed_fields(bytes(rec), pool, schemas) if kind == 'text']
    check(len(texts) >= 1, 'no text field')
    return texts


class Page:
    """MainY5 with its rows gone and the six-segment bar: what both private
    pages start from.  `seq` the records (bytearrays), `removed` and `added`
    the records the allocation header must account for, `refs` the private
    strings' fields as (record, offset, text)."""

    def __init__(self, img, pool):
        self.img, self.pool = img, pool
        self.schemas = P.load_schemas(img)
        self.recs = N.split(SL.set_bytes(BASE))
        self.objs = SZ.objects(self.recs, pool)
        self.raw = [bytearray(r.to_bytes()) for r in self.recs]
        self.src = by_object(self.raw)
        self.rows = [k for k, v in self.objs.items() if v['parent'] == ROWS_PARENT]
        check(len(self.rows) == 6 and self.rows[0] == KEEP_ROW and self.objs[ROWS_PARENT]['n'] == 6,
              'MainY5 rows changed')
        check([k for k, v in self.objs.items() if v['parent'] == BAR_Y][:5] == list(Y_TABS), 'Y tab bar changed')
        check(self.objs[ROW_NAME]['parent'] == ROW_ITEM and self.objs[ROW_ITEM]['name'] == 'MenuItem_Jump',
              'row changed')
        self.next_id = max(self.objs) + 1
        self.removed, self.added, self.groups, self.refs = [], [], [], []

    def ob(self):
        return by_object(self.seq)

    def drop_rows(self, roots):
        self.drop_subtrees(roots)
        put(self.seq[self.ob()[ROWS_PARENT][0]], 16, 6 - len(roots))

    def drop_subtrees(self, roots):
        drop, gone = set(), set()
        for root in roots:
            sub = SZ.subtree(self.objs, root)
            a, b = SZ.span(self.recs, self.objs, sub, root)
            drop.update(range(a, b))
            gone |= sub
        self.removed += [bytes(self.recs[i].to_bytes()) for i in sorted(drop)]
        seq = [bytearray(r.to_bytes()) for i, r in enumerate(self.recs) if i not in drop]
        pool, schemas = self.pool, self.schemas

        # actions aimed at a row that went (Root's CameraControl_ExclChk greys
        # rows): they go and their event's action count follows; an event left
        # with none goes too (its object's record count follows)
        def names_gone(r):
            return any(kind == 'obj' and u32(r, off) in gone and u32(r, off) > 64
                       for off, kind in P.typed_fields(bytes(r), pool, schemas))
        out, k = [], 0
        while k < len(seq):
            r = seq[k]
            if u32(r, 0) != 0x10006:
                out.append(r)
                k += 1
                continue
            n_act = u32(r, 16)
            acts = seq[k + 1:k + 1 + n_act]
            check(all(u32(a, 0) == 0x10009 for a in acts), 'event without its actions')
            keep = [a for a in acts if not names_gone(a)]
            self.removed += [bytes(a) for a in acts if a not in keep]
            if keep or not n_act:            # an event with no actions of its own stays
                # the count only: +28 (the action's order) is NOT 1..n in record
                # order (HeaderActiveTab's Up: 2 then 1) -- renumbering it broke
                # the header (camera r2, 2026-10-07)
                if len(keep) != n_act:
                    put(r, 16, len(keep))
                out += [r] + keep
            else:
                self.removed.append(bytes(r))
                obj = next(j for j in range(len(out) - 1, -1, -1) if u32(out[j], 0) == 0x10003)
                put(out[obj], 8, u32(out[obj], 8) - 1)
            k += 1 + n_act
        self.seq = out
        for r in self.seq:
            for off, kind in P.typed_fields(bytes(r), pool, schemas):
                check(not (kind == 'obj' and u32(r, off) in gone and u32(r, off) > 64),
                      f'record {u32(r, 0):#x} of {u32(r, 20)} names a dropped object {u32(r, off)}')

    def tab_bar(self, bar_y=BAR_Y, bar_b=None):
        """B's places, Y's colours.  A segment's colour clip plays only for the
        objects Root's MenuTab set lists (camera r2b: a new object with a copied
        clip stayed dark), so the stock segments keep their own clips: Tab01..04
        go to places 1..4, Tab05 (bright at Y5's time) to place 6, and place 5
        gets a new plain rect in the dark colour, no clip."""
        pool, raw, src = self.pool, self.raw, self.src
        kids = lambda bar: [k for k, v in self.objs.items() if v['parent'] == bar]
        y_tabs = tuple(kids(bar_y)[:5]) if bar_y != BAR_Y else Y_TABS
        b_tabs = tuple(kids(bar_b)[:6]) if bar_b else B_TABS
        check([self.objs[t]['name'] for t in y_tabs] == [f'Tab0{i}' for i in range(1, 6)] and
              [self.objs[t]['name'] for t in b_tabs] == [f'Tab0{i}' for i in range(1, 7)], 'tab bars changed')

        def b_rec(tab, name):
            rec = owned(raw[comp(raw, src[tab], name, pool)], 0)
            if name == 'drawRect':
                k = rec.find(struct.pack('>I', B_COLOUR))
                check(k > 0 and rec.find(struct.pack('>I', B_COLOUR), k + 1) < 0, 'B colour')
                put(rec, k, Y_COLOUR)
            return rec
        ob = self.ob()
        put(self.seq[ob[bar_y][0]], 16, self.objs[bar_y]['n'] + 1)
        for y, k in zip(y_tabs, (0, 1, 2, 3, 5)):
            for name in ('objectBase', 'drawRect'):
                i = comp(self.seq, ob[y], name, pool)
                self.seq[i] = owned(b_rec(b_tabs[k], name), y)
        new_id = self.take_id()
        head = bytearray(raw[src[y_tabs[4]][0]])
        put(head, 8, 2)                                           # objectBase + drawRect
        put(head, 20, new_id)
        put(head, 28, u32(raw[src[b_tabs[5]][0]], 28))            # a name: "Tab06"
        check(pool.text(u32(head, 28)) == 'Tab06', 'Tab06 name')
        tab6 = [head, owned(b_rec(b_tabs[4], 'objectBase'), new_id),
                owned(b_rec(b_tabs[4], 'drawRect'), new_id)]
        after = ob[y_tabs[3]][-1] + 1                             # between Tab04 and Tab05
        self.seq[after:after] = tab6
        self.added += tab6

    def take_id(self):
        self.next_id += 1
        return self.next_id - 1

    def private(self, rec, off, text):
        o, new = self.pool.intern(text)
        put(rec, off, o)
        check(new or text in self.pool.added, f'{text!r} is a stock string: no need')
        self.refs.append((rec, off, text))

    def request(self, rec, text):
        """A controlAppState's request (its text at +36) -> `text`."""
        check(pool_name(self, rec) == 'controlAppState' and len(rec) == 41, 'not a request')
        if self.pool.find(text) is not None:
            put(rec, 36, self.pool.find(text))
        else:
            self.private(rec, 36, text)

    def finish(self, base=BASE):
        """(page bytes, [(page offset, private text)])."""
        recs, pool = self.recs, self.pool
        header = A.parse_header(A.Record(0, recs[0].to_bytes()), pool.text)
        plus = [A.Record(0, bytes(x)) for x in self.added]
        minus = [A.Record(0, r) for r in self.removed]
        cc = A.component_counts(plus, pool.text)
        for k, v in A.component_counts(minus, pool.text).items():
            cc[k] = cc.get(k, 0) - v
        clips = [A.parse_clip(x, pool.text) for x in plus if x.tag == 0x1000A]
        delta = {'objects': sum(1 for x in plus if x.tag == 0x10003) - sum(1 for x in minus if x.tag == 0x10003),
                 'component_counts': cc, 'group_budgets': self.groups,
                 'clip_property_counts': [len(c['properties']) for c in clips],
                 'property_key_counts': [len(p['keys']) for c in clips for p in c['properties']]}
        self.seq[0] = bytearray(BM.extend_header(A.Record(0, recs[0].to_bytes()), header, delta))
        page, abs_refs = SZ.assemble(self.seq, self.refs)
        check(len(abs_refs) == len(self.refs) and N.join(N.split(page)) == page and page.endswith(SL.END),
              'page')
        return page, abs_refs


def pool_name(pg, rec):
    return pg.pool.text(u32(rec, 8))


def row_actions(pg, item, key):
    """The actions of `item`'s keyEvent `key` (indices into pg.seq)."""
    ob = pg.ob()
    for i in ob[item]:
        r = pg.seq[i]
        if u32(r, 0) == 0x10006 and pool_name(pg, r) == 'keyEvent' and pg.pool.text(u32(r, 20)) == key:
            return list(range(i + 1, i + 1 + u32(r, 16)))
    raise P.BuildError(f'{item} has no {key} keyEvent')


LIST_PAGE = 'FT_LIST'
LIST_TITLE = 'All Sups'
KEY_VAR = 'FT_Key'
FOCUS_VAR = 'FT_Focus'                    # the list rows' focus (int, memory only)
KEY_OK_CODE, KEY_AEL_CODE = 0x0D, 0x13         # keyEvent's last word: the key (stock pages)
AEL_BASE = 100                            # AEL on cell k writes FT_Key = 100 + k (allsups.c)
OK_VAR = 'FT_OK'                          # the grid's footer: "[OK] Uninstall / Install  [MENU] Cancel"
ROWS = 6                                  # one stock page of rows (scrolling: later)
ROW_PITCH = 81.0


def name_var(k):
    return f'FT_N{k}'


def state_var(k):
    return f'FT_S{k}'


# the grid's cell parts (the user 2026-10-07): the product name, white when it
# loaded this boot and grey when not; under it, small, the version (grey) and
# the pending change: red "Unload on restart" / green "Load on restart"
# (English on the camera: the user 2026-10-08)
CELL_PARTS = (('N', None), ('G', 0x8A8A8AFF), ('V', 0x9A9A9AFF), ('R', 0xFF453AFF), ('E', 0x30D158FF))
NAME_FONT, SMALL_FONT = 40, 20
NAME_RECT = (49.0, 2.0, 280.0, 46.0)
SMALL_RECT = {'V': (49.0, 48.0, 110.0, 26.0), 'R': (160.0, 48.0, 178.0, 26.0), 'E': (160.0, 48.0, 178.0, 26.0)}


def part_var(part, k):
    return f'FT_{part}{k}'


def styled_text(tmpl, owner, size, color, rect, font=None):
    """A drawText from a cell Text's 53-byte form (mask 0x40000001: text, rect),
    with font (bit 1), font-size (bit 2) and colour (bit 13) -- fields in bit
    order, as sl_lab_page.diagram builds them (camera-proven there)."""
    check(u32(tmpl, 28) == 0x40000001 and len(tmpl) == 53, 'cell Text drawText form')
    t = bytearray(tmpl[:28])
    mask = 0x40000001 | 0x4 | (0x2000 if color is not None else 0) | (0x2 if font is not None else 0)
    t += struct.pack('>I', mask) + bytes(tmpl[32:37])            # text (5 B)
    if font is not None:
        t += struct.pack('>I', font)
    t += struct.pack('>I', size)
    if color is not None:
        t += struct.pack('>I', color)
    t += struct.pack('>4f', 0.0, 0.0, rect[2], rect[3])         # its rect, in the object's frame
    put(t, 4, len(t))
    put(t, 20, owner)
    return t


def build(img=None, pool=None):
    """FT_Y6: (page bytes, [(page offset, private text)])."""
    if img is None:
        img, pool = stock_pool()
    pg = Page(img, pool)
    pg.drop_rows(pg.rows[1:])
    pg.tab_bar()
    ob = pg.ob()
    # the row's label: a literal, its offset written at load
    name_rec = pg.seq[comp(pg.seq, ob[ROW_NAME], 'drawText', pool)]
    off = text_field(name_rec, pool, pg.schemas)[0]
    name_rec[off + 4] = 0                                       # literal, not a text id (sl_size_page)
    pg.private(name_rec, off, LABEL)
    # Right / OK open the list page (r3b)
    for key in ('Right', 'OK'):
        for i in row_actions(pg, ROW_ITEM, key):
            if pool_name(pg, pg.seq[i]) == 'controlAppState':
                pg.request(pg.seq[i], LIST_PAGE)
    return pg.finish()


def clone_row(pg, k):
    """Row k (1..ROWS) of the list page: a copy of Y5_1 with fresh ids, at
    y = (k - 1) * 81, its Name and IconText01 showing FT_N<k> / FT_S<k>, its
    "..." blank, Right / OK writing FT_Key = k, MENU asking for Return."""
    pool, schemas, raw = pg.pool, pg.schemas, pg.raw
    sub = SZ.subtree(pg.objs, KEEP_ROW)
    a, b = SZ.span(pg.recs, pg.objs, sub, KEEP_ROW)
    # row 1 keeps Y5_1's ids (all of MainY5's rows went, so they are free):
    # FT_Y6's row writes MENU_Level1 = its MenuItem_Jump (3451) on focus, and
    # the list page focuses whatever MENU_Level1 names -- a clone id there
    # left the focus on no object (camera r3b)
    mapping = {old: (old if k == 1 else pg.take_id()) for old in sorted(sub)}
    out = []
    for r in raw[a:b]:
        rec = bytearray(r)
        for off, kind in P.typed_fields(bytes(rec), pool, schemas):
            if kind == 'obj' and u32(rec, off) in mapping:
                put(rec, off, mapping[u32(rec, off)])
        out.append(rec)
    pg.groups += [pg.header_groups[i] for i in range(a, b) if pg.recs[i].tag == 0x1000B]
    by = by_object(out)
    m = mapping
    # where: the row container's objectBase in Y5_2's (positioned) form
    if k > 1:
        y5_2 = pg.rows[1]
        base = owned(raw[comp(raw, pg.src[y5_2], 'objectBase', pool)], m[KEEP_ROW])
        pos = bytes(base).find(struct.pack('>f', ROW_PITCH))
        check(pos > 0, 'row position field')
        struct.pack_into('>f', base, pos, ROW_PITCH * (k - 1))
        out[comp(out, by[m[KEEP_ROW]], 'objectBase', pool)] = base
    # Name and IconText01 bound to the row's variables
    item = m[ROW_ITEM]
    kids = {pool.text(u32(out[by[o][0]], 28)): o for o in by if pg.objs.get(
        next(old for old, new in m.items() if new == o), {}).get('parent') == ROW_ITEM}
    for obj_name, var in (('Name', name_var(k)), ('IconText01', state_var(k))):
        o = kids[obj_name]
        t = comp(out, by[o], 'drawText', pool)
        f = text_field(out[t], pool, schemas)[0]
        put(out[t], f, 0)                                     # the empty string (pool offset 0)
        out[t][f + 4] = 0
        locals_ = P.local_ids(out, by[o])
        bind = Lab._binding(o, max(locals_ | {0}) + 1, var, u32(out[t], 24), 'text', pool, pg.refs, 0)
        out.insert(by[o][-1] + 1, bind)
        put(out[by[o][0]], 8, u32(out[by[o][0]], 8) + 1)
        by = by_object(out)
    # IconText01's objectBase carries one more field than Name's (mask 0x43,
    # the last word 0): stock shows it only on rows that have a value, and it
    # stayed invisible bound to FT_S<k> (camera r3b).  Name's form (mask 3:
    # x, y, w, h) at IconText01's place.
    it = kids['IconText01']
    ib = comp(out, by[it], 'objectBase', pool)
    nb = bytearray(out[comp(out, by[kids['Name']], 'objectBase', pool)])
    check(u32(out[ib], 28) == 0x43 and u32(nb, 28) == 3 and len(nb) == 48, 'objectBase forms')
    x, y, w, h = struct.unpack_from('>4f', out[ib], 32)
    put(nb, 20, it)
    struct.pack_into('>4f', nb, 32, x, y, w, h)
    out[ib] = nb
    icon = kids['Icon02']
    img_rec = out[comp(out, by[icon], 'drawImage', pool)]
    names = [off for off, kind in P.typed_fields(bytes(img_rec), pool, schemas)
             if kind in ('str', 'text') and pool.text(u32(img_rec, off)) == 'font_G_3dot']
    check(len(names) == 1, 'the "..." image name')
    put(img_rec, names[0], pool.find('blank'))
    # focus: the row remembers itself in FT_Focus, not MENU_Level1 -- the
    # stock pages and FT_Y6 restore their focus from MENU_Level1, and an id of
    # this page there left them with none (camera r3b: MENU back to FT_Y6, no
    # row lit, MENU dead).  MENU_Level1 keeps FT_Y6's row, which is row 1 here.
    level1 = pool.find('MENU_Level1')
    moved = 0
    for i in by[item]:
        r = out[i]
        if u32(r, 0) == 0x10009 and pool.text(u32(r, 8)) == 'controlAppVariable' and u32(r, 36) == level1:
            o, new = pool.intern(FOCUS_VAR)
            put(r, 36, o)
            pg.refs.append((r, 36, FOCUS_VAR))
            moved += 1
    check(moved == 1, f'the row writes MENU_Level1 {moved} times')
    # keys
    for key in ('Right', 'OK'):
        ev = next(i for i in by[item] if u32(out[i], 0) == 0x10006 and pool.text(u32(out[i], 20)) == key)
        for i in range(ev + 1, ev + 1 + u32(out[ev], 16)):
            r = out[i]
            if pool.text(u32(r, 8)) == 'controlAppVariable' and pool.text(u32(r, 36)) in (
                    'SoftKeyboard_MenuName', 'SoftKeyboard_InputID'):
                o, new = pool.intern(KEY_VAR)
                put(r, 36, o)
                if new or KEY_VAR in pool.added:
                    pg.refs.append((r, 36, KEY_VAR))
                check(pool.find(str(k)) is not None, f'"{k}" in the stock pool')
                put(r, 41, pool.find(str(k)))
    return out


def build_list(img=None, pool=None):
    """FT_LIST: (page bytes, [(page offset, private text)])."""
    if img is None:
        img, pool = stock_pool()
    pg = Page(img, pool)
    pg.header_groups = {}
    header = A.parse_header(A.Record(0, pg.recs[0].to_bytes()), pool.text)
    gi = [i for i, r in enumerate(pg.recs) if r.tag == 0x1000B]
    pg.header_groups = {i: header['groups'][n] for n, i in enumerate(gi)}
    pg.drop_rows(pg.rows)
    pg.tab_bar()
    rows = [clone_row(pg, k) for k in range(1, ROWS + 1)]
    ob = pg.ob()
    at = ob[ROWS_PARENT][-1] + 1
    pg.seq[at:at] = [r for row in rows for r in row]
    pg.added += [r for row in rows for r in row]
    put(pg.seq[pg.ob()[ROWS_PARENT][0]], 16, ROWS)
    # MENU anywhere: back to FT_Y6 ("Return"), not out of the menu
    ret = 0
    for i, r in enumerate(pg.seq):
        if u32(r, 0) == 0x10006 and pool_name(pg, r) == 'keyEvent' and pool.text(u32(r, 20)) == 'Menu':
            for j in range(i + 1, i + 1 + u32(r, 16)):
                a = pg.seq[j]
                if pool_name(pg, a) == 'controlAppState':
                    req = bytearray(pg.raw[row_actions_raw(pg, ROW_ITEM, 'Right')])
                    for off in (20, 24, 28):
                        put(req, off, u32(a, off))
                    put(req, 36, pool.find('Return'))
                    pg.removed.append(bytes(a))
                    pg.added.append(req)
                    pg.seq[j] = req
                    ret += 1
    check(ret >= ROWS, f'MENU requests replaced: {ret}')
    return pg.finish()


GRID_BASE = 'Y5_2'                        # 語言 / Language: a 3 x 6 grid, 17 cells
GRID_CELLS = tuple(range(103, 120))       # English .. Suomi, in focus order
GRID_TEXT = dict(zip(GRID_CELLS, (139, 140, 141, 142, 143, 144, 145, 146, 147, 148, 149, 150,
                                  151, 153, 152, 154, 155)))
GRID_DROP = (4242, 4243, 174)             # KeyMaskCheck, LangCheck (first-boot flow), CursorFix
GRID_TITLE = 74                           # Header Text01: "語言 / Language"
GRID_FOOTER = 40                          # Footer Text01: the localized "[OK] OK [MENU] Cancel" (Footer05),
GRID_FOOTER_BOX = 71                      #   its text switched by a clip -- hidden, a bound copy in its place
GRID_BAR_Y, GRID_BAR_B = 94, 85
LANG_VARS = ('CM_Language', 'CM_LanguageAj')


def build_grid(img=None, pool=None):
    """FT_LIST from the language page (the user 2026-10-07: "嘗試語言列的呈現"):
    17 cells, each one text variable FT_N<k> ("NAME" + state, allsups.c), OK
    writing FT_Key = k and staying; MENU is the page's own cancel (Return ->
    FT_Y6).  What made it the language page goes: the first-boot language
    flow (writes SYS_LangSetting, jumps to LV), the current-language marker,
    the title's binding; CM_Language / CM_LanguageAj (the setting and its
    cursor) become the private FT_Focus -- nothing on the page writes a setting.
    (page bytes, [(page offset, private text)])."""
    if img is None:
        img, pool = stock_pool()
    schemas = P.load_schemas(img)
    recs = N.split(SL.set_bytes(GRID_BASE))
    objs = SZ.objects(recs, pool)
    check([o for o in GRID_CELLS if objs.get(o, {}).get('n', None) == 3] == list(GRID_CELLS)
          and all(objs[t]['parent'] == c for c, t in GRID_TEXT.items()), 'language page changed')
    pg = Page.__new__(Page)
    pg.img, pg.pool, pg.schemas, pg.recs, pg.objs = img, pool, schemas, recs, objs
    pg.raw = [bytearray(r.to_bytes()) for r in recs]
    pg.src = by_object(pg.raw)
    pg.next_id = max(objs) + 1
    pg.removed, pg.added, pg.groups, pg.refs = [], [], [], []
    pg.rows = list(GRID_DROP)
    # drop the subtrees (Page.drop_rows without the row count)
    keep_rows = objs[ROWS_PARENT]['n'] if ROWS_PARENT in objs else None
    pg.drop_subtrees(GRID_DROP)
    for parent in {objs[o]['parent'] for o in GRID_DROP}:
        i = pg.ob()[parent][0]
        put(pg.seq[i], 16, u32(pg.seq[i], 16) - sum(1 for o in GRID_DROP if objs[o]['parent'] == parent))
    pg.tab_bar(GRID_BAR_Y, GRID_BAR_B)
    # the footer (the user 2026-10-08: OK says Uninstall / Install, MENU says
    # Cancel): stock's Text01 shows one localized string with the button icons
    # in it (<!font_G_bt_ok_K153,CENTER>OK<!28sp,CENTER><!font_G_bt_menu...>
    # Cancel) and a clip switches it, so it would win over a binding: it goes
    # off the screen, and a copy without the clip shows FT_OK (allsups.c composes it with
    # the same icon markup)
    ob = pg.ob()
    fb = comp(pg.seq, ob[GRID_FOOTER], 'objectBase', pool)
    check(u32(pg.seq[fb], 28) == 3 and objs[GRID_FOOTER]['parent'] == GRID_FOOTER_BOX, 'footer Text01 shape')
    copy_recs = [bytearray(pg.seq[i]) for i in ob[GRID_FOOTER]
                 if u32(pg.seq[i], 0) in (0x10003, 0x10004, 0x10005)]
    check([u32(r, 0) for r in copy_recs] == [0x10003, 0x10004, 0x10005], 'footer Text01 records')
    # off the screen: a 0 x 0 box still drew its text (camera r3f)
    struct.pack_into('>f', pg.seq[fb], 32, 3000.0)
    oid = pg.take_id()
    o, b, t = copy_recs
    put(o, 20, oid); put(o, 8, 3); put(o, 16, 0)
    put(b, 20, oid); put(t, 20, oid)
    f = text_field(t, pool, schemas)[0]
    put(t, f, 0)
    t[f + 4] = 0
    bd = Lab._binding(oid, 2, OK_VAR, u32(t, 24), 'text', pool, pg.refs, 0)
    at = ob[GRID_FOOTER][-1] + 1
    pg.seq[at:at] = [o, b, t, bd]
    pg.added += [o, b, t, bd]
    bi = pg.ob()[GRID_FOOTER_BOX][0]
    put(pg.seq[bi], 16, u32(pg.seq[bi], 16) + 1)
    ob = pg.ob()
    # the title: a literal, without the binding that sets it
    t = pg.seq[comp(pg.seq, ob[GRID_TITLE], 'drawText', pool)]
    f = text_field(t, pool, schemas)[0]
    t[f + 4] = 0
    pg.private(t, f, LIST_TITLE)
    b = comp(pg.seq, ob[GRID_TITLE], 'appVariableEvent', pool)
    check(u32(pg.seq[b], 16) == 0, 'the title binding has actions')
    pg.removed.append(bytes(pg.seq.pop(b)))
    o = pg.ob()[GRID_TITLE][0]
    put(pg.seq[o], 8, u32(pg.seq[o], 8) - 1)
    # the cells
    tmpl = None
    for i in pg.src[GRID_CELLS[1]]:
        r = pg.raw[i]
        if pool_name(pg, r) == 'controlAppVariable' and pool.text(u32(r, 36)) == 'CM_Language' and len(r) == 46:
            tmpl = r
    check(tmpl is not None, 'the 46-byte value form')
    for k, cell in enumerate(GRID_CELLS, 1):
        ob = pg.ob()
        tid = GRID_TEXT[cell]
        ti = comp(pg.seq, ob[tid], 'drawText', pool)
        # every cell from English's 53-byte form (日本語 / 中文 cells carry a font)
        text_tmpl = bytes(pg.raw[comp(pg.raw, pg.src[GRID_TEXT[GRID_CELLS[0]]], 'drawText', pool)])
        text_tmpl = text_tmpl[:24] + pg.seq[ti][24:28] + text_tmpl[28:32] + struct.pack('>I', 0) + b'\0' + text_tmpl[37:]
        # the cell's own Text: the white name
        base_i = comp(pg.seq, ob[tid], 'objectBase', pool)
        check(u32(pg.seq[base_i], 28) == 3, 'cell Text objectBase form')
        struct.pack_into('>4f', pg.seq[base_i], 32, *NAME_RECT)
        pg.removed.append(bytes(pg.seq[ti]))
        pg.seq[ti] = styled_text(text_tmpl, tid, NAME_FONT, None, NAME_RECT)
        pg.added.append(pg.seq[ti])
        bind = Lab._binding(tid, 2, part_var('N', k), u32(pg.seq[ti], 24), 'text', pool, pg.refs, 0)
        pg.seq.insert(ob[tid][-1] + 1, bind)
        pg.added.append(bind)
        put(pg.seq[ob[tid][0]], 8, u32(pg.seq[ob[tid][0]], 8) + 1)
        # the other four parts: new objects, children of the cell, after its Text
        ob = pg.ob()
        at = ob[tid][-1] + 1
        new = []
        for part, color in CELL_PARTS[1:]:
            oid = pg.take_id()
            o = bytearray(pg.seq[ob[tid][0]])
            put(o, 20, oid); put(o, 8, 3); put(o, 16, 0)
            b = bytearray(pg.seq[comp(pg.seq, ob[tid], 'objectBase', pool)])
            put(b, 20, oid)
            rect = NAME_RECT if part == 'G' else SMALL_RECT[part]
            struct.pack_into('>4f', b, 32, *rect)
            t = styled_text(text_tmpl, oid, NAME_FONT if part == 'G' else SMALL_FONT, color, rect)
            bd = Lab._binding(oid, 2, part_var(part, k), u32(t, 24), 'text', pool, pg.refs, 0)
            new += [o, b, t, bd]
        pg.seq[at:at] = new
        pg.added += new
        ci = pg.ob()[cell][0]
        put(pg.seq[ci], 16, u32(pg.seq[ci], 16) + len(CELL_PARTS) - 1)
        # OK: FT_Key = k, and no request (the page stays)
        ob = pg.ob()
        ev = next(i for i in ob[cell] if u32(pg.seq[i], 0) == 0x10006 and pool_name(pg, pg.seq[i]) == 'keyEvent'
                  and pool.text(u32(pg.seq[i], 20)) == 'OK')
        acts = list(range(ev + 1, ev + 1 + u32(pg.seq[ev], 16)))
        names = [pool_name(pg, pg.seq[i]) for i in acts]
        check(names == ['controlAppVariable', 'controlAppVariable', 'controlAppState'], f'cell {k} OK {names}')
        old = pg.seq[acts[0]]
        check(pool.text(u32(old, 36)) == 'CM_Language', 'OK writes the language')
        new = bytearray(tmpl)
        for off in (20, 24, 28):
            put(new, off, u32(old, off))
        check(pool.find(str(k)) is not None, f'"{k}" in the stock pool')
        put(new, 41, pool.find(str(k)))
        pg.private(new, 36, KEY_VAR)
        pg.removed.append(bytes(old))
        pg.added.append(new)
        pg.seq[acts[0]] = new
        pg.removed.append(bytes(pg.seq[acts[2]]))
        del pg.seq[acts[2]]
        put(pg.seq[ev], 16, 2)
        # AEL: the cell's sup gets the key (the user 2026-10-08: "loader 只負責把
        # 按鈕交給 sup"; allsups.c svc->button): FT_Key = AEL_BASE + k
        ob = pg.ob()
        local = max(P.local_ids(pg.seq, ob[cell]) | {0}) + 1
        ev_rec = bytearray(pg.seq[ev])
        put(ev_rec, 16, 1)
        put(ev_rec, 20, pool.find('AEL'))
        put(ev_rec, 28, local)
        # the key is the LAST word (OK 0x0D, AEL 0x13, Up 0x26, QS 0x14 ...), the
        # name only a label: copied from OK it still listened to OK (camera r3g)
        check(u32(ev_rec, len(ev_rec) - 4) == KEY_OK_CODE, 'OK keyEvent code')
        put(ev_rec, len(ev_rec) - 4, KEY_AEL_CODE)
        act = bytearray(new)
        put(act, 24, local)
        put(act, 28, 1)
        pg.private(act, 36, KEY_VAR)
        val = str(AEL_BASE + k)
        if pool.find(val) is not None:
            put(act, 41, pool.find(val))
        else:
            pg.private(act, 41, val)
        at = ev + 1 + 2
        pg.seq[at:at] = [ev_rec, act]
        pg.added += [ev_rec, act]
        put(pg.seq[ob[cell][0]], 8, u32(pg.seq[ob[cell][0]], 8) + 1)
    # what is left of the language: the private focus
    n = 0
    for r in pg.seq:
        for off, kind in P.typed_fields(bytes(r), pool, schemas):
            if kind in ('str', 'text') and pool.text(u32(r, off)) in LANG_VARS:
                pg.private(r, off, FOCUS_VAR)
                n += 1
    check(n == 17 + 3 + 1, f'language variables left: {n}')   # cells' focus, Menu (event + 2 animations), FirstFocus
    for r in pg.seq:
        for off, kind in P.typed_fields(bytes(r), pool, schemas):
            v = pool.text(u32(r, off)) if kind in ('str', 'text') else None
            check(v not in LANG_VARS + ('SYS_LangSetting', 'SYS_DateSetting'), f'{v} still on the page')
    return pg.finish(base=GRID_BASE)


def row_actions_raw(pg, item, key):
    """The stock row's request record (controlAppState 'Y5_1') of `key`."""
    for i in pg.src[item]:
        r = pg.raw[i]
        if u32(r, 0) == 0x10006 and pool_name(pg, r) == 'keyEvent' and pg.pool.text(u32(r, 20)) == key:
            for j in range(i + 1, i + 1 + u32(r, 16)):
                if pool_name(pg, pg.raw[j]) == 'controlAppState' and len(pg.raw[j]) == 41:
                    return j
    raise P.BuildError('no request')


def check_page(page, img=None, pool=None, base=BASE):
    """Header vs records the way the stock page has them, object graph, every
    object reference resolves, and the original interpreter walks it."""
    if img is None:
        img, pool = stock_pool()
    old, new = P.page_shape(SL.set_bytes(base), pool), P.page_shape(page, pool)
    hn, ho = dict(new['header_minus_actual']), dict(old['header_minus_actual'])
    check(all(n >= o for n, o in zip(hn.pop('group_slots'), ho.pop('group_slots'))), 'group slots')
    # dropped rows keep their group / clip / key budgets: reserve may grow, never shrink
    for k in ('groups', 'clips', 'clip_properties', 'keys'):
        check(hn.pop(k) >= ho.pop(k), f'{k} budget below the page')
    check(hn == ho, f'allocation header: {hn} vs stock {ho}')
    check(not new['orphans'] and not new['bad_child_counts'],
          f"object graph: orphans {new['orphans']} child counts {new['bad_child_counts']}")
    objs = new['objects']
    schemas = P.load_schemas(img)
    for r in N.split(page):
        d = r.to_bytes()
        for off, kind in P.typed_fields(d, pool, schemas):
            v = u32(d, off)
            if kind == 'obj' and v not in (0, 0xFFFFFFFF) and v > 64:
                check(v in objs, f'dangling object {v} in tag {r.tag:#x}')
    P.native_parse(page, pool.data, img)
    return new


if __name__ == '__main__':
    for fn, base in ((build, BASE), (build_list, BASE), (build_grid, GRID_BASE)):
        page, refs = fn()
        shape = check_page(page, base=base)
        print(fn.__name__, len(page), 'bytes;', len(refs), 'private fields;', len(shape['objects']), 'objects')
