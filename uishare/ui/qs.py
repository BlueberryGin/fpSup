"""The shared Quick Set recipe and its reference applier (QS_SHARE.md).

One record hook layer per sup at C05E6400. Every layer carries the same
recipe (ui/qs_recipe.json, from gen_qs_recipe.py) and applies, to the record
the firmware is parsing, the rows that are its own:

  clone / end      the big tile's third state (t = 66): the same for every
                   layer, so whoever comes first writes it, the rest see it done
  qs_max, cursor,  depend only on N, the number of resolution rows (the shared
  footer_n8        CSV's row count): the same for every layer
  footer_label     the footer label of row k: only the layer that owns row k

Each rule has ONE target per record, computed from the stock bytes:

  current == target  -> leave it      (another layer, or this one, did it)
  current == stock   -> write target
  anything else      -> leave it, report a conflict

So the result does not depend on the order of the layers, and applying a
layer twice is the same as once (test_qs.py proves both on every record).

Markers are filled from `ids`: 0..2 the shared "current format" images
(QS/SET/FONT, §4.3), 3 this layer's own SET image (its footer label).
"""
import functools
import json
import pathlib
import struct

HERE = pathlib.Path(__file__).resolve().parent
RECIPE = HERE / 'qs_recipe.json'
ROOT = HERE.parents[2]
IMAGE = ROOT / 'out' / 'MAIN_c0000000.bin'

SHARED_QS, SHARED_SET, SHARED_FONT, OWN_SET = 0, 1, 2, 3
DONE, WROTE, CONFLICT, SKIP = 'done', 'wrote', 'conflict', 'skip'


class QsError(ValueError):
    pass


@functools.lru_cache(maxsize=None)
def recipe():
    return json.loads(RECIPE.read_text())


@functools.lru_cache(maxsize=None)
def image():
    return IMAGE.read_bytes()


def stock(row, img=None):
    img = img or image()
    a = int(row['address'], 16) - 0xC0000000
    return img[a:a + row['length']]


def fn(n):
    t = recipe()['fn'].get(str(n))
    if t is None:
        raise QsError(f'N = {n} is not supported (supported: {sorted(recipe()["fn"])})')
    return t


def apply_edits(old, edits, ids):
    """Edits are in the coordinates of `old`, in order; several may share an
    offset (they insert one after another)."""
    out = bytearray()
    cur = 0
    for at, dl, ins in edits:
        if at < cur:
            raise QsError('edits out of order')
        out += old[cur:at]
        out += struct.pack('>I', ids[ins['marker']]) if isinstance(ins, dict) else bytes.fromhex(ins)
        cur = at + dl
    out += old[cur:]
    return bytes(out)


def clone_key(old, time, prop, value):
    """og3k_ui.S ui_layout_copy: every property gets a copy of its first key
    at `time`; property `prop` (or none, -1) gets `value` as that key's value.
    The record's total length (+4) follows."""
    out = bytearray(old[:28])
    count = struct.unpack('>I', old[12:16])[0]
    src = 28
    for p in range(count):
        keys = struct.unpack('>I', old[src:src + 4])[0]
        head = bytearray(old[src:src + 20])
        head[3] = keys + 1
        out += head + old[src + 20:src + 20 + 10 * keys]
        key = bytearray(old[src + 20:src + 30])
        key[2:6] = struct.pack('>I', time)
        if p == prop:
            key[6:10] = struct.pack('>I', value)
        out += key
        src += 20 + 10 * keys
    if src != len(old):
        raise QsError('record does not end with its properties')
    out[4:8] = struct.pack('>I', len(out))
    return bytes(out)


def target(row, old, n, k, ids):
    """The bytes this layer wants for `row`, or None if the row is not its own."""
    rule = row['rule']
    if rule == 'clone':
        m = row['marker']
        return clone_key(old, row['time'], row['prop'], ids[m] if m is not None else 0)
    if rule == 'end':
        return old[:36] + struct.pack('>I', row['time']) + old[40:]
    if rule == 'qs_max':
        if old[31] & 2:
            raise QsError(f'{row["address"]}: stock already has a max')
        b = bytearray(old[:32] + struct.pack('>f', fn(n)['qs_max']) + old[32:])
        b[4:8] = struct.pack('>I', len(b))
        b[31] |= 2
        return bytes(b)
    if rule == 'cursor':
        name = int(recipe()['cursor_names'][fn(n)['cursor']], 16)
        b = bytearray(old)
        for at in row['at']:
            b[at:at + 4] = struct.pack('>I', name)
        return bytes(b)
    if rule == 'footer_n8':
        return apply_edits(old, row['edits'], ids) if fn(n)['footer'] == 'n8' else old
    if rule == 'footer_label':
        return apply_edits(old, row['edits'], ids) if row['k'] == k else None
    raise QsError(f'unknown rule {rule}')


def layer(current, row, n, k, ids, img=None):
    """One layer on one record: (new bytes, what happened)."""
    old = stock(row, img)
    want = target(row, old, n, k, ids)
    if want is None:
        return current, SKIP
    if current == want:
        return current, DONE
    if current == old:
        return want, WROTE
    return current, CONFLICT


def chain(row, layers, img=None):
    """Layers from outermost to innermost; each is (n, k, ids). Returns the
    bytes the stock parser gets and each layer's outcome."""
    cur = stock(row, img)
    seen = []
    for n, k, ids in layers:
        cur, what = layer(cur, row, n, k, ids, img)
        seen.append(what)
    return cur, seen


def check_n(n):
    """A sup that would make the row count unsupported refuses at entry."""
    t = fn(n)
    return t['verified']


def max_growth():
    """The most any record grows (all its rules applied): sizes the copy buffer."""
    img = image()
    ids = {0: 1, 1: 2, 2: 3, 3: 4}
    worst = 0
    for row in recipe()['records']:
        old = stock(row, img)
        for n in (int(x) for x in recipe()['fn']):
            for k in range(2, recipe()['k_max'] + 1):
                t = target(row, old, n, k, ids)
                if t is not None:
                    worst = max(worst, len(t))
    return worst


# ---- the table qs_apply.c reads (qs_apply.h) -------------------------------------
RULES = {'clone': 0, 'end': 1, 'qs_max': 2, 'cursor': 3, 'footer_n8': 4, 'footer_label': 5}
TABLE_VERSION = 1


def _edits(edits):
    out = bytearray()
    for at, dl, ins in edits:
        if isinstance(ins, dict):
            out += struct.pack('<HHH', at, dl, 0x8000 | ins['marker'])
        else:
            b = bytes.fromhex(ins)
            out += struct.pack('<HHH', at, dl, len(b)) + b
            out += bytes(len(b) & 1)
    return bytes(out + struct.pack('<HHH', 0xFFFF, 0, 0))


def encode():
    """The recipe as qs_apply.c's table."""
    r = recipe()
    rows, edits = bytearray(), bytearray()
    for row in r['records']:
        rule = row['rule']
        a = b = 0
        e = 0
        if rule == 'clone':
            a = row['prop'] + 1
            b = (0xFF if row['marker'] is None else row['marker']) | row['time'] << 8
        elif rule == 'end':
            b = row['time']
        elif rule == 'cursor':
            a, b = row['at']
        elif rule == 'footer_label':
            a = row['k']
        if 'edits' in row:
            e = len(edits)
            edits += _edits(row['edits'])
        if e > 0xFFFF or a > 0xFF or b > 0xFFFF:
            raise QsError(f'{row["address"]}: does not fit the table')
        rows += struct.pack('<IIHBBHH', int(row['address'], 16), int(row['fnv'], 16),
                            row['length'], RULES[rule], a, b, e)
    nmax = r['n_max']
    fns = bytearray()
    for n in range(nmax + 1):
        t = r['fn'].get(str(n))
        if t is None:
            fns += bytes(12)
        else:
            fns += struct.pack('<fIBBH', t['qs_max'], int(r['cursor_names'][t['cursor']], 16),
                               t['footer'] == 'n8', t['verified'], 0)
    head = 32
    rows_off = head
    edits_off = rows_off + len(rows)
    fn_off = edits_off + len(edits)
    fn_off += -fn_off % 4
    out = struct.pack('<8I', TABLE_VERSION, len(r['records']), rows_off, edits_off, fn_off,
                      nmax, r['k_max'], 0)
    out += rows + edits
    out += bytes(fn_off - len(out)) + fns
    return bytes(out)
