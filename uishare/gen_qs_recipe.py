#!/usr/bin/env python3
"""Generate ui/qs_recipe.json: the shared Quick Set recipe (QS_SHARE.md).

    python3 -B gen_qs_recipe.py [--check]

Sources (read only, nothing is copied from them at run time):
  - the FP3K layout plan (camera-proven, research/other/external/FP3K-...):
    OpenGate's 360 records -- the third state of the big tile, the QS limits,
    the footer label of row 2 and the four cursor records;
  - Jose Hurtado's fpSup-Formats core (projects/jose-hurtado): the footer
    labels of rows 3..7 and the N = 8 values (QS limit 7.0, Cursor7, the
    footer position curve and its end). His bytes are read through his
    layout tools; only the differences against stock are kept.

Every string or image name in a record is a MARKER here, never an id value
(QS_SHARE.md §1.1): the applier fills it at run time. The build refuses any
inserted big-endian word >= 0xF0000000 (the shape of a hard-coded alias id).

--check regenerates and compares with the file on disk.
"""
import argparse
import difflib
import json
import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
IMAGE = ROOT / 'out' / 'MAIN_c0000000.bin'
FP3K = ROOT / 'research/other/external/FP3K-technical-handoff-2026-09-10/prototype/development/native-ui'
JOSE_TOOLS = ROOT / 'projects/jose-hurtado/formats/tools'
OUT = HERE / 'ui' / 'qs_recipe.json'
SETS = ROOT / 'research' / 'ui' / 'nbu_sets.json'

WANTED_GROUPS = {'20_MV_Resolution', '21_MV_Resolution'}
QS_LIMITS = (0xC237249F, 0xC25E405A)
CURSORS = (0xC237256F, 0xC23725CE, 0xC25E40DC, 0xC25E413B)
FOOTER_POS, FOOTER_END = 0xC2358981, 0xC234E465
# The footer label object of each row k (QS_CINE, QS_STILLlike). Row 2 is
# OpenGate's (and Jose's slot 1); rows 3..7 are Jose's slots 2..6.
FOOTER_LABELS = {
    2: (0xC23535A3, 0xC25B9EF1),
    3: (0xC2354087, 0xC25BAA2F),
    4: (0xC2354AF3, 0xC25BB4EB),
    5: (0xC2355569, 0xC25BBF61),
    6: (0xC2355F8F, 0xC25BC9D7),
    7: (0xC2356A55, 0xC25BD4C5),
}
# Marker slots: the shared "current format" images (§4.3) and the layer's own.
SHARED_QS, SHARED_SET, SHARED_FONT = 0, 1, 2
OWN_SET = 3
FP3K_ALIASES = {0xFFFFFFF2: SHARED_QS, 0xFFFFFFF3: SHARED_SET, 0xFFFFFFF4: SHARED_FONT}
THIRD_TIME = 66
FOOTER_TIME = 700
# Pool offsets of the stock cursor clips (QS_SHARE.md §1: Cursor2..7 exist, no Cursor8).
CURSOR_NAMES = {'Cursor2': 0xF4C8, 'Cursor3': 0xF4D0, 'Cursor4': 0xF4D8,
                'Cursor5': 0xF4E0, 'Cursor6': 0xF4E8, 'Cursor7': 0xF504}
# f(N). verified: N = 3 (OpenGate, camera), N = 8 (Jose, camera). 4..7: guesses.
FN = {
    3: {'qs_max': 2.0, 'cursor': 'Cursor3', 'footer': 'stock', 'verified': True},
    4: {'qs_max': 3.0, 'cursor': 'Cursor4', 'footer': 'stock', 'verified': False},
    5: {'qs_max': 4.0, 'cursor': 'Cursor5', 'footer': 'stock', 'verified': False},
    6: {'qs_max': 5.0, 'cursor': 'Cursor6', 'footer': 'stock', 'verified': False},
    7: {'qs_max': 6.0, 'cursor': 'Cursor7', 'footer': 'stock', 'verified': False},
    8: {'qs_max': 7.0, 'cursor': 'Cursor7', 'footer': 'n8', 'verified': True},
}


class RecipeError(RuntimeError):
    pass


def check(cond, msg):
    if not cond:
        raise RecipeError(msg)


def fnv(data):
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def image():
    return IMAGE.read_bytes()


def stock(img, a, n):
    return img[a - 0xC0000000:a - 0xC0000000 + n]


def record_len(img, a):
    return struct.unpack('>I', stock(img, a + 4, 4))[0]


def set_name(a):
    import bisect
    sets = json.loads(SETS.read_text())['sets']
    sa = sorted((int(s['address'], 16), s['name']) for s in sets)
    i = bisect.bisect_right([x for x, _ in sa], a) - 1
    return sa[i][1]


def delta(old, new):
    """Edits old -> new as [at, delete, inserted hex]."""
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != 'equal':
            out.append([i1, i2 - i1, new[j1:j2].hex()])
    return out


def mark(edits, ids):
    """Replace every big-endian id in `ids` inside inserted bytes with a marker
    {"at": .., "del": .., "marker": slot}. Inserted runs are split around it."""
    out = []
    for at, dl, ins in edits:
        b = bytes.fromhex(ins)
        i, start, first = 0, 0, True
        pieces = []
        while i + 4 <= len(b):
            w = struct.unpack('>I', b[i:i + 4])[0]
            if w in ids:
                pieces.append(('bytes', b[start:i]))
                pieces.append(('marker', ids[w]))
                i += 4
                start = i
            else:
                i += 1
        pieces.append(('bytes', b[start:]))
        # an edit replaced bytes: the delete belongs to the first piece
        for kind, val in pieces:
            if kind == 'bytes' and not val and not first:
                continue
            if kind == 'bytes':
                out.append([at, dl if first else 0, val.hex()])
            else:
                out.append([at, dl if first else 0, {'marker': val}])
            first = False
    return out


def forbid_alias_words(edits, where):
    for e in edits:
        if isinstance(e[2], dict):
            continue
        b = bytes.fromhex(e[2])
        for i in range(len(b) - 3):
            w = struct.unpack('>I', b[i:i + 4])[0]
            if w >= 0xF0000000:
                raise RecipeError(f'{where}: inserted word 0x{w:08X} looks like a hard-coded alias id')


# ---- the third state of the big tile (OpenGate's kind 1 / kind 2) -----------
def clone_key(old, time, prop_index, marker):
    """og3k_ui.S ui_layout_copy: append a key at `time` to every property,
    cloned from its first key; the property at prop_index (0-based, or -1)
    gets the marker as its value. Returns (bytes with a placeholder, marker
    offsets). Placeholder value = 0xFFFFFFF2 + marker (only to locate it)."""
    out = bytearray(old[:28])
    count = struct.unpack('>I', old[12:16])[0]
    src = 28
    marks = []
    for p in range(count):
        keys = struct.unpack('>I', old[src:src + 4])[0]
        head = bytearray(old[src:src + 20])
        head[3] = keys + 1
        out += head
        out += old[src + 20:src + 20 + 10 * keys]
        key = bytearray(old[src + 20:src + 30])
        key[2:6] = struct.pack('>I', time)
        if p == prop_index:
            marks.append(len(out) + 6)
            key[6:10] = struct.pack('>I', 0xFFFFFFF2 + marker)
        out += key
        src += 20 + 10 * keys
    check(src == len(old), 'clone_key: record does not end with its properties')
    out[4:8] = struct.pack('>I', len(out))
    return bytes(out), marks


def fp3k_plans():
    full = json.loads((FP3K / 'layout-repair-plan.json').read_text())
    legacy = json.loads((FP3K / 'ui-resource-plan.json').read_text())
    groups = [g for g in full['groups'] if g['name'] in WANTED_GROUPS]
    addrs = {g['address'] for g in groups} | {c for g in groups for c in g['clips']}
    plans = [p for p in full['plans'] if p['address'] in addrs]
    plans += [p for p in full['plans'] if p['kind'] == 'literal-delta']
    plans += [p for p in legacy['plans'] if p['kind'] == 'QS-limit']
    by = {int(p['address'], 16): p for p in plans}
    check(len(by) == len(plans) == 359, f'FP3K selection is {len(plans)} records, expected 359')
    return by


def jose_layout():
    sys.path.insert(0, str(JOSE_TOOLS))
    import fmt_layout
    return fmt_layout.Layout()


def jose_deltas(lay):
    """Jose's literal-delta records: {address: new bytes}."""
    T, N = 0xC073321C, 0x17B
    img = lay.img
    rows = [struct.unpack('<3I', img.read(T + 12 * i, 12)) for i in range(N)]
    E = T + 12 * N
    out = {}
    stockimg = image()
    for a, meta, h in rows:
        if meta >> 10 & 7 != 4:
            continue
        ln = meta & 0x3FF
        old = stock(stockimg, a, ln)
        check(fnv(old) == h, f'Jose 0x{a:08X}: stock bytes differ')
        p = E + (meta >> 13)
        new = bytearray()
        cur = 0
        while True:
            i1, n1, n2 = struct.unpack('<HHH', img.read(p, 6))
            if i1 == 0xFFFF:
                break
            new += old[cur:i1]
            new += img.read(p + 6, n2)
            cur = i1 + n1
            p += 6 + n2
            p += (p - E) % 2
        new += old[cur:]
        out[a] = bytes(new)
    return out


def build():
    img = image()
    plans = fp3k_plans()
    lay = jose_layout()
    jd = jose_deltas(lay)
    rows = []

    def row(a, rule, **kw):
        n = record_len(img, a)
        old = stock(img, a, n)
        rows.append(dict(address=f'0x{a:08X}', set=set_name(a), length=n, fnv=f'0x{fnv(old):08X}',
                         rule=rule, **kw))

    special = set(QS_LIMITS) | set(CURSORS) | {a for v in FOOTER_LABELS.values() for a in v}
    for a, p in sorted(plans.items()):
        if a in special:
            continue
        old = bytes.fromhex(p['old'])
        check(old == stock(img, a, len(old)), f'0x{a:08X}: FP3K old bytes differ from the image')
        new = bytes.fromhex(p['new'])
        if p['kind'] == 'layout-keys':
            check(p['time'] == THIRD_TIME, f'0x{a:08X}: time {p["time"]}')
            aliases = p.get('aliases', {})
            check(len(aliases) <= 1, f'0x{a:08X}: more than one alias')
            pi = int(next(iter(aliases), '-1'))
            name = next(iter(aliases.values()), None)
            marker = {'FP3K_QS': SHARED_QS, 'FP3K_SET': SHARED_SET, 'FP3K_FONT': SHARED_FONT,
                      None: None}[name]
            mine, marks = clone_key(old, THIRD_TIME, pi, marker if marker is not None else 0)
            # the FP3K plan carries the alias as FFFFFFF2+slot: same bytes
            check(mine == new, f'0x{a:08X}: clone_key does not reproduce the FP3K plan')
            row(a, 'clone', time=THIRD_TIME, prop=pi, marker=marker)
        elif p['kind'] == 'state-end':
            check(len(old) == len(new) and old[:36] == new[:36] and old[40:] == new[40:],
                  f'0x{a:08X}: state-end is not one word at +36')
            check(struct.unpack('>I', new[36:40])[0] == THIRD_TIME, f'0x{a:08X}: end')
            row(a, 'end', time=THIRD_TIME)
        else:
            raise RecipeError(f'0x{a:08X}: unexpected FP3K kind {p["kind"]}')

    # f(N): QS limit, cursors, footer position and end
    for a in QS_LIMITS:
        row(a, 'qs_max')
    for a in CURSORS:
        n = record_len(img, a)
        old = stock(img, a, n)
        check(struct.unpack('>I', old[36:40])[0] == CURSOR_NAMES['Cursor2'] and
              struct.unpack('>I', old[41:45])[0] == CURSOR_NAMES['Cursor2'], f'0x{a:08X}: not Cursor2')
        row(a, 'cursor', at=[36, 41])
    for a in (FOOTER_POS, FOOTER_END):
        n = record_len(img, a)
        e = delta(stock(img, a, n), jd[a])
        forbid_alias_words(e, f'0x{a:08X}')
        row(a, 'footer_n8', edits=e)

    # the footer label of row k
    for k, addrs in FOOTER_LABELS.items():
        for a in addrs:
            n = record_len(img, a)
            old = stock(img, a, n)
            new = jd[a]
            ids = {w: OWN_SET for w in range(0xFFFFFFD0, 0xFFFFFFF7)}
            e = mark(delta(old, new), ids)
            check(sum(isinstance(x[2], dict) for x in e) == 1, f'0x{a:08X}: one image id expected')
            forbid_alias_words(e, f'0x{a:08X}')
            if k == 2:   # OpenGate's: must be the FP3K plan with F3 -> marker
                fp = plans[a]
                check(mark(delta(old, bytes.fromhex(fp['new'])), {0xFFFFFFF3: OWN_SET}) == e,
                      f'0x{a:08X}: row 2 differs between OpenGate and Jose')
            row(a, 'footer_label', k=k, edits=e)

    rows.sort(key=lambda r: int(r['address'], 16))
    check(len({r['address'] for r in rows}) == len(rows), 'duplicate record')
    return {
        'comment': 'generated by gen_qs_recipe.py; QS_SHARE.md',
        'third_time': THIRD_TIME, 'footer_time': FOOTER_TIME,
        'markers': {'shared_qs': SHARED_QS, 'shared_set': SHARED_SET,
                    'shared_font': SHARED_FONT, 'own_set': OWN_SET},
        'cursor_names': {k: f'0x{v:X}' for k, v in CURSOR_NAMES.items()},
        'fn': {str(k): v for k, v in FN.items()},
        'n_max': max(FN), 'k_max': max(FOOTER_LABELS),
        'records': rows,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args(argv)
    text = json.dumps(build(), indent=1) + '\n'
    if a.check:
        if OUT.read_text() != text:
            raise SystemExit(f'{OUT} is stale')
        print('qs_recipe.json up to date')
        return
    OUT.write_text(text)
    print(f'wrote {OUT}')


if __name__ == '__main__':
    main()
