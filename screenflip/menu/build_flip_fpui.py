#!/usr/bin/env python3
"""The Screen Flip row as an FPUI block (fpSup/uishare/ui): what the row ADDS
to the stock Y2_5_1 page, composed on the camera by uishare/ui_apply.c.

build_flip_page.py makes the whole page; this turns it back into additions
(rows.row_block) and proves, with the reference applier, that the additions
applied to the stock page give exactly that page (private strings compared
through the shared pool, as the camera resolves them).

The block has no file op and no hook: the donor's popup rows are fixed
objects, not a CSV. So it needs no file-redirect site and no declared word.

    python3 -B build_flip_fpui.py --out FILE
"""
import argparse
import hashlib
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
FPSUP = HERE.parents[1]
sys.path.insert(0, str(FPSUP / 'uishare'))
sys.path.insert(0, str(FPSUP / 'lossless/menu'))
sys.path.insert(0, str(HERE))
from ui import fpui, rows          # noqa: E402
import pack_menu_file as P         # noqa: E402  (stock_at: the shared-pool hint)
import build_menu_candidate as BM  # noqa: E402
import build_flip_page as F        # noqa: E402

NAME = 'Y2_5_1'
COUNTER = 'tools.rows'            # the TOOLS tab's own rows: y 405, then 486 ...


def build(seg0=None):
    seg0 = F.SEG0.read_bytes() if seg0 is None else seg0
    page, _pool, manifest = F.build(seg0, BM.load_audit(F.AUDIT))
    stock_page = seg0[F.PAGE_START:F.PAGE_END]
    pool_stock = seg0[P.STOCK_POOL - 0xC0000000:][:P.STOCK_POOL_LEN]
    hint = lambda s: P.stock_at(pool_stock, s.encode())
    blk, info = rows.row_block(NAME, F.ENTRY_OFFSET, stock_page, page, manifest, pool_stock, hint,
                               insert_at=F.INSERT_AT - F.PAGE_START,
                               parent_at=F.TAB_RECORD - F.PAGE_START,
                               mode_change_at=F.MODE_GROUP - F.PAGE_START,
                               y_role='tools_row_y', row_base_y=int(F.ROW_Y), counter=COUNTER,
                               root_clip=manifest['new_row']['mode_clip'])
    blob = blk.encode()
    rows.prove_same(blob, NAME, stock_page, page, manifest, pool_stock, hint)
    _, ops, _ = fpui.decode(blob)
    fpui.check(not any(code in (fpui.OP_HOOK_FV, fpui.OP_FILE) for code, _ in ops),
               'the Screen Flip block must not touch files or hook anything')
    return blob, info, (stock_page, pool_stock, page, manifest)


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
