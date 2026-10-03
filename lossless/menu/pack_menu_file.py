#!/usr/bin/env python3
"""Pack the private MainB2 page into FPLMENU.BIN for native/menu_page.c.

The card reads this file at boot (the loader's own file API) and switches
MainB2 to it; see menu_page.h. Layout, little-endian words:

  +00 "FPLM"   +04 version 3   +08 page bytes   +0C private string bytes
  +10 reference count           +14 stock MainB2 page offset (0x76FF04)
  +18 FNV-1a of page + strings + references    +1C 0
  +20 the page, then the private strings (NUL-terminated), then one
      {page offset, string offset, stock_at} per field that names a private
      string, then zeros to FILE_BYTES. stock_at is where the stock pool
      already holds that string -- the first place uis_intern's scan would
      find it -- or 0xFFFFFFFF; the camera then never scans the stock pool
      (uis_intern_hinted, fpSup/uishare/ui_pool.h).

No string offset is fixed here: on the camera each private string gets its
offset from the shared pool (fpSup/uishare/ui_pool.h) and is written into
the page, so this row composes with any other sup's strings.

The candidate is built fresh by build_menu_candidate.py (profile
audio-toggle: a clone of SHOOT 2's own Audio row) and must carry the stock pool unchanged as its
prefix, which is checked here against the pinned firmware image.
"""
import argparse
import hashlib
import json
import pathlib
import struct
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SEG0 = ROOT / 'out/seg0_c0000000.bin'
AUDIT = ROOT / 'research/ui/tools/native_ui_audit.py'
SEG0_SHA = 'aaa5208a028d9c4aebb9cc8614add723d456e96b2a95914f433079954320e622'
MAGIC, VERSION = 0x4D4C5046, 3
NOT_STOCK = 0xFFFFFFFF
FILE_BYTES = 0x30000
STOCK_POOL, STOCK_POOL_LEN, MAINB2_OFFSET = 0xC18C0474, 176152, 0x76FF04
NAMES = (b'MV_fpLossless\0', b'SUB_MV_fpLossless\0', b'EXCL_fpLossless\0')
PROFILE = 'audio-toggle'       # see build_menu_candidate.py: the MainY4-donor
                               # row froze SHOOT 2 on the camera; this did not


def fnv(data, h=2166136261):
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def references(page, pool, manifest):
    """(page offset, private string) for every field naming a private string.

    From the builder's own typed record, then checked against the page
    itself: every listed word holds that string's builder offset, and no
    other word of the page does (a word that only happens to is refused)."""
    clone = {int(r['source_record'], 16): r['candidate_offset']
             for r in manifest['record_locations'] if r['provenance'] != 'stock_unchanged'
             and r['provenance'] != 'stock_modified'}
    refs = []
    for f in manifest['typed_string_fields']:
        if f['pool_offset'] < STOCK_POOL_LEN or f['role'] == 'private_string_redirect':
            continue
        at = clone[int(f['source_record'], 16)] + f['field_offset']
        if struct.unpack_from('>I', page, at)[0] != f['pool_offset']:
            raise SystemExit(f'page word at {at:#x} is not {f["text"]!r}')
        refs.append((at, f['pool_offset']))
    private = {o for _, o in refs}
    listed = {a for a, _ in refs}
    stray = [a for a in range(len(page) - 3)
             if struct.unpack_from('>I', page, a)[0] in private and a not in listed]
    if stray:
        raise SystemExit(f'unlisted words equal to private offsets at {stray[:5]}')
    return sorted(set(refs))


def stock_at(stock, s):
    """Where ui_pool.c find() would first match `s` in the stock pool: s
    followed by a NUL, at any offset (a string that ends another counts)."""
    i = stock.find(s + b'\0')
    return NOT_STOCK if i < 0 else i


def pack(page, pool, seg0, manifest):
    prefix = seg0[STOCK_POOL - 0xC0000000:][:STOCK_POOL_LEN]
    if pool[:STOCK_POOL_LEN] != prefix:
        raise SystemExit('candidate pool does not keep the stock pool as its prefix')
    tail = pool[STOCK_POOL_LEN:]
    for name in NAMES:              # the card registers exactly these
        if name not in tail:
            raise SystemExit(f'{name!r} is not in the private strings')
    refs = references(page, pool, manifest)
    def text(off):
        return pool[off:pool.index(b'\0', off)]
    blob = b''.join(struct.pack('<III', at, off - STOCK_POOL_LEN, stock_at(prefix, text(off)))
                    for at, off in refs)
    head = struct.pack('<8I', MAGIC, VERSION, len(page), len(tail), len(refs),
                       MAINB2_OFFSET, fnv(page + tail + blob), 0)
    data = head + page + tail + blob
    # A fixed size, zero padded: putfile cannot shorten a file on the card,
    # and the camera reads only what the header declares.
    if len(data) > FILE_BYTES:
        raise SystemExit(f'{len(data)} bytes do not fit the {FILE_BYTES}-byte file')
    return data + b'\0' * (FILE_BYTES - len(data))


def build(out):
    seg0 = SEG0.read_bytes()
    if hashlib.sha256(seg0).hexdigest() != SEG0_SHA:
        raise SystemExit('firmware image is not the pinned one')
    with tempfile.TemporaryDirectory(prefix='fpl-menu-') as tmp:
        c = pathlib.Path(tmp) / 'candidate'
        subprocess.run([sys.executable, '-B', str(HERE / 'build_menu_candidate.py'),
                        '--seg0', str(SEG0), '--audit-module', str(AUDIT), '--output', str(c),
                        '--profile', PROFILE],
                       check=True, capture_output=True, text=True)
        page = (c / 'MainB2.fpLossless.gated.page').read_bytes()
        pool = (c / 'MainB2.fpLossless.strings').read_bytes()
        manifest = json.loads((c / 'manifest.json').read_text())
    data = pack(page, pool, seg0, manifest)
    out.write_bytes(data)
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', type=pathlib.Path, required=True)
    a = ap.parse_args()
    if a.out.exists():
        raise SystemExit(f'{a.out} exists: this never overwrites')
    data = build(a.out)
    print(f'{a.out}: {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}')


if __name__ == '__main__':
    main()
