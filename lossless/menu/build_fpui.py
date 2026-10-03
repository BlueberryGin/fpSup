#!/usr/bin/env python3
"""The Lossless RAW row as an FPUI block (fpSup/uishare/ui): what the row
ADDS to the stock MainB2 page, composed on the camera by ui_apply.c.

The row itself is still made by build_menu_candidate.py (profile
audio-toggle, the page the camera tested); this turns that page back into
additions and proves, with the reference applier, that the additions applied
to the stock page give exactly that page (private strings compared through
the shared pool, as the camera resolves them).

    python3 -B build_fpui.py --out FILE
"""
import argparse
import hashlib
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
FPSUP = HERE.parents[1]
sys.path.insert(0, str(FPSUP / 'uishare'))
sys.path.insert(0, str(HERE))
from ui import fpui, rows          # noqa: E402
import pack_menu_file as P         # noqa: E402

PAGE_START, PAGE_END = 0x2030364, 0x2055DB0      # MainB2 in seg0 (native_ui_audit)
DONOR_END, B2_OBJECT, MODE_CHANGE = 0x205460A, 0x2037C1B, 0x20318F7
ENTRY_OFFSET = 0x76FF04                          # MainB2's runtime entry, stock
NAME = 'MainB2'


def build():
    import json
    import subprocess
    seg0 = P.SEG0.read_bytes()
    if hashlib.sha256(seg0).hexdigest() != P.SEG0_SHA:
        raise SystemExit('firmware image is not the pinned one')
    with tempfile.TemporaryDirectory(prefix='fpl-fpui-') as tmp:
        c = pathlib.Path(tmp) / 'candidate'
        subprocess.run([sys.executable, '-B', str(HERE / 'build_menu_candidate.py'),
                        '--seg0', str(P.SEG0), '--audit-module', str(P.AUDIT), '--output', str(c),
                        '--profile', P.PROFILE], check=True, capture_output=True, text=True)
        page = (c / 'MainB2.fpLossless.gated.page').read_bytes()
        manifest = json.loads((c / 'manifest.json').read_text())
    stock_page = seg0[PAGE_START:PAGE_END]
    pool_stock = seg0[P.STOCK_POOL - 0xC0000000:][:P.STOCK_POOL_LEN]
    hint = lambda s: P.stock_at(pool_stock, s.encode())
    blk, info = rows.row_block(NAME, ENTRY_OFFSET, stock_page, page, manifest, pool_stock, hint,
                               insert_at=DONOR_END - PAGE_START, parent_at=B2_OBJECT - PAGE_START,
                               mode_change_at=MODE_CHANGE - PAGE_START)
    blob = blk.encode()
    rows.prove_same(blob, NAME, stock_page, page, manifest, pool_stock, hint)
    return blob, info, (stock_page, pool_stock, page)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', type=pathlib.Path, required=True)
    a = ap.parse_args()
    blob, info, _ = build()
    a.out.write_bytes(blob)
    print(f'{a.out}: {len(blob)} bytes, sha256 {hashlib.sha256(blob).hexdigest()}')
    print(info)


if __name__ == '__main__':
    main()
