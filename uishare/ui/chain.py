"""Read the nested string layers back out of a camera's memory (for tests).

    from ui import chain
    layers = chain.layers(peek)        # [(base, [text, ...]), ...], innermost first
    text = chain.resolve(peek, offset) # what the chain answers, None for 0

`peek(address)` returns the little-endian word at an address. This follows the
words exactly as the firmware's call would: the site's B.W, the cave veneer,
each layer's next pointer, and the firmware's rule at the bottom.
"""
import struct

SITES = {                       # site, stock word, the header field holding that entry's offset
    'resolve': (0xC05E5B58, 0x3FFFF1B1, 60),
    'owns': (0xC05E61C8, 0x428A6942, 64),
    'remain': (0xC05E61E0, 0x69406902, 68),
}
# header v5 (ui_strings.S): +0 name (8 bytes), +8 version, +12 base, +16 count,
# +20 lo, +24 hi, +28/+32/+36 next, +40 table, +44 QS id, +48 QS enum,
# +52 QS names, +56 header length, +60/+64/+68 the entries' offsets. The word
# before every entry is its own offset: a layer is found from any entry without
# knowing its build's code layout.
NEXT = {'resolve': 28, 'owns': 32, 'remain': 36}
H_NAME, H_VERSION, H_BASE, H_COUNT, H_LO, H_HI, H_TABLE = 0, 8, 12, 16, 20, 24, 40
H_QSID, H_QSENUM, H_QSNAMES = 44, 48, 52
H_LEN, H_MIN_LEN = 56, 72
# the resolution value as ui_strings.S reads it: the working bank of the
# menu-setting storage, not the property object's +0x14 cache
RES_BANK_SEL, RES_RAW_A, RES_RAW_B = 0xC31B954C, 0xC31B3A4C, 0xC31B9314
RES_CACHE = 0xC31ACC30          # the lagging cache, NOT read (camera 2026-10-07)


def res_value(peek):
    sel = peek(RES_BANK_SEL & ~3) >> (8 * (RES_BANK_SEL & 3)) & 0xFF
    return peek(RES_RAW_B if sel else RES_RAW_A)
VERSION = 5
LEGACY = b'FSDL'                # the name without a running Loader v3
THUMB_VENEER = 0xF000F8DF
READER_LEN, READER_POOL = 0x10, 0x14


class ChainError(ValueError):
    pass


def bw_target(site, w):
    hw1, hw2 = w & 0xFFFF, w >> 16
    if (hw1 & 0xF800) != 0xF000 or (hw2 & 0xD000) != 0x9000:
        return None
    s, j1, j2 = (hw1 >> 10) & 1, (hw2 >> 13) & 1, (hw2 >> 11) & 1
    i1, i2 = 1 ^ (j1 ^ s), 1 ^ (j2 ^ s)
    off = s << 24 | i1 << 23 | i2 << 22 | (hw1 & 0x3FF) << 12 | (hw2 & 0x7FF) << 1
    if s:
        off -= 1 << 25
    return (site + 4 + off) & 0xFFFFFFFF


def header_of(peek, entry, field):
    """The header of the layer whose `field` entry is `entry` (ui_pool.c header_of)."""
    if not entry or entry & 3:
        return None
    off = peek(entry - 4)
    h = (entry - off) & 0xFFFFFFFF
    if off & 3 or off < H_MIN_LEN or off > 0x10000 or peek(h + H_VERSION) != VERSION or \
            peek(h + H_LEN) < H_MIN_LEN or peek(h + field) != off:
        return None
    return h


def outer(peek, kind='resolve'):
    """The outermost layer on a site, or None if the stock word is there."""
    site, stock, field = SITES[kind]
    w = peek(site)
    if w == stock:
        return None
    veneer = bw_target(site, w)
    if veneer is None or peek(veneer) != THUMB_VENEER:
        raise ChainError('site 0x%08X holds 0x%08X, not a layer' % (site, w))
    layer = header_of(peek, peek(veneer + 4), field)
    if layer is None or not peek(layer + H_NAME):
        raise ChainError('no layer header behind 0x%08X' % peek(veneer + 4))
    return layer


def inner(peek, layer):
    """The next layer inward, or None."""
    nxt = peek(layer + NEXT['resolve'])
    if nxt == 0:
        return None
    h = header_of(peek, nxt, SITES['resolve'][2])
    if h is None:
        raise ChainError('the next of 0x%08X is not a layer' % layer)
    return h


def name(peek, layer):
    """The layer's owner: '10LOSS', or 'FSDL' without a Loader v3."""
    raw = struct.pack('<II', peek(layer + H_NAME), peek(layer + H_NAME + 4))
    return raw.split(b'\0', 1)[0].decode()


def cstr(peek, a):
    out = bytearray()
    while True:
        b = struct.pack('<I', peek(a & ~3))[a & 3]
        if not b:
            return out.decode()
        out.append(b)
        a += 1


def layers(peek):
    """[(base, [text, ...]), ...] innermost first; the three sites must agree."""
    top = {k: outer(peek, k) for k in SITES}
    if len(set(top.values())) != 1:
        raise ChainError('the three sites disagree: %r' % top)
    out, layer = [], top['resolve']
    while layer is not None:
        base, count, table = peek(layer + H_BASE), peek(layer + H_COUNT), peek(layer + H_TABLE)
        out.append((base, [cstr(peek, peek(table + 4 * i)) for i in range(count)]))
        layer = inner(peek, layer)
    return out[::-1]


def qs(peek):
    """[(name, qsid, enum, [three names])] innermost first, for layers with QS ids."""
    out = []
    layer = outer(peek, 'resolve')
    while layer is not None:
        if peek(layer + H_QSID):
            names = peek(layer + H_QSNAMES)
            out.append((name(peek, layer), peek(layer + H_QSID), peek(layer + H_QSENUM),
                        [cstr(peek, peek(names + 4 * i)) for i in range(3)]))
        layer = inner(peek, layer)
    return out[::-1]


def resolve(peek, offset, reader):
    """The string the chain gives `offset`, or None where it answers 0: each
    layer from the outermost in, as the calls go (ui_strings.S resolve)."""
    layer = outer(peek, 'resolve')
    while layer is not None:
        qsid = peek(layer + H_QSID)
        if qsid and 0 <= offset - qsid < 3 and res_value(peek) == peek(layer + H_QSENUM):
            return cstr(peek, peek(peek(layer + H_QSNAMES) + 4 * (offset - qsid)))
        base, count, table = peek(layer + H_BASE), peek(layer + H_COUNT), peek(layer + H_TABLE)
        if base <= offset < base + count:
            return cstr(peek, peek(table + 4 * (offset - base)))
        layer = inner(peek, layer)
    if offset == 0xFFFFFFFF or offset >= peek(reader + READER_LEN):
        return None
    return cstr(peek, peek(reader + READER_POOL) + offset)
