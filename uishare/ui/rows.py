"""add_row: a new settings row, as FPUI ops against the stock page.

v1 takes the row a page builder already made -- today
fpSup/lossless/menu/build_menu_candidate.py, which clones a row of the SAME
page (fp-native-ui §8b: rows from another page break the page), gives it its
own object IDs and private names, and splices it in -- and turns that whole
page back into what was ADDED to the stock page:

  - the cloned records, inserted after the donor row, with every field that
    holds one of the new object IDs (LOCAL_ID), a private string (STRING) or
    the row's Y position (a slot from the page's row counter) marked, so the
    camera fills them in at boot;
  - the allocation header: object count, component counts and the three
    budget arrays (entries appended at the end of each);
  - the parent's child count;
  - the page's ModeChange root list (one {root, root_clip} appended; 1 unless
    the cloned root's mode clip has another id).

Nothing else may differ from stock; build() proves it by applying the ops to
the stock page with the reference applier and comparing with the builder's
page, string fields compared by text.
"""
import struct

from . import fpui

ROW_STEP = 81                   # CINE row pitch in SHOOT pages (fp-native-ui §8h)


def u32(b, o):
    return struct.unpack_from('>I', b, o)[0]


def records(data):
    out, off = [], 0
    while off < len(data):
        size = u32(data, off + 4)
        fpui.check(8 <= size <= len(data) - off, 'bad record at +0x%X' % off)
        out.append((off, data[off:off + size]))
        off += size
    return out


def header_layout(rec):
    """Stock positions inside the 0x10002 header: component count fields and,
    for each of the four budget arrays, (count field, end of entries, width)."""
    k = u32(rec, 16)
    comps = [20 + 8 * i + 4 for i in range(k)]
    cur, arrays = 20 + 8 * k, []
    for width in (3, 1, 1, 1):
        n = u32(rec, cur)
        arrays.append((cur, cur + 4 + 4 * width * n, width))
        cur += 4 + 4 * width * n
    fpui.check(cur == len(rec), 'header not fully consumed')
    return comps, arrays


def PAGE_BASE(manifest):
    """Image offset of the page the builder spliced (its first record)."""
    return int(manifest['record_locations'][0]['source_record'], 16) - 0xC0000000


def row_block(name, entry_offset, stock_page, page, manifest, pool_stock, stock_at,
              insert_at, parent_at, mode_change_at, y_role='cine_row_y_162_to_243',
              row_base_y=243, counter='rows', root_clip=1):
    """FPUI ops adding the row in `page` (the builder's whole page) to
    `stock_page`. Offsets are page-relative. Returns (Block, info)."""
    clone_locs = [r for r in manifest['record_locations']
                  if r['provenance'].startswith('cloned')]
    fpui.check(clone_locs, 'no cloned records in the manifest')
    start = clone_locs[0]['candidate_offset']
    length = sum(r['length'] for r in clone_locs)
    stock_before = {r['source_record']: r['length'] for r in manifest['record_locations']
                    if not r['provenance'].startswith('cloned')
                    and int(r['source_record'], 16) - 0xC0000000 - PAGE_BASE(manifest) < insert_at}
    shift = sum(n for n in stock_before.values()) - insert_at
    fpui.check(start == insert_at + shift and
               all(a['candidate_offset'] + a['length'] == b['candidate_offset']
                   for a, b in zip(clone_locs, clone_locs[1:])),
               'cloned records are not one block at the insertion point')
    frag = bytearray(page[start:start + length])
    where = {}                                  # donor record -> offset inside frag
    for r in clone_locs:
        where[r['source_record']] = r['candidate_offset'] - start

    id_map = {int(k): v for k, v in manifest['new_row']['object_id_map'].items()}
    first = min(id_map.values())
    fpui.check(sorted(id_map.values()) == list(range(first, first + len(id_map))),
               'new object IDs are not one run')

    blk = fpui.Block()
    relocs = []
    for f in manifest['typed_object_fields']:
        if f['scope'] == 'cloned_object' and f['source_record'] in where:
            at = where[f['source_record']] + f['field_offset']
            fpui.check(u32(frag, at) == f['new'], 'object field mismatch')
            relocs.append((fpui.R_LOCAL_ID, f['new'] - first, at))
    stock_len = len(pool_stock)
    for f in manifest['typed_string_fields']:
        if f['source_record'] in where and f['pool_offset'] >= stock_len:
            at = where[f['source_record']] + f['field_offset']
            text = f['text']
            s = blk.string(text, stock_at(text))
            relocs.append((fpui.R_STRING, s, at))
    y = [c for c in manifest['typed_changes'] if c['role'] == y_role]
    fpui.check(len(y) <= 1, 'more than one row Y field')
    # positions inside the cloned records are found from the record list
    slot_relocs = []
    for c in y:
        at = where[c['source_record']] + c['field_offset']
        fpui.check(u32(frag, at) == fpui.int_to_f32(row_base_y), 'row Y is not the first row slot')
        slot_relocs.append((fpui.R_SLOT_F32, 0, at))
    relocs = sorted(set(relocs + slot_relocs), key=lambda r: r[2])
    fpui.check(len({r[2] for r in relocs}) == len(relocs), 'two relocations on one field')

    # ---- the allocation header, from the two headers ------------------------
    s_recs, p_recs = records(stock_page), records(page)
    sh, ph = s_recs[0][1], p_recs[0][1]
    fpui.check(u32(sh, 0) == 0x10002 and u32(ph, 0) == 0x10002, 'page does not start with its header')
    comps, arrays = header_layout(sh)
    pcomps, parrays = header_layout(ph)
    fpui.check(len(comps) == len(pcomps), 'component kinds changed')
    header_ops, grow = [], 0
    header_ops.append((fpui.OP_ADD32, 8, u32(ph, 8) - u32(sh, 8)))
    for so, po in zip(comps, pcomps):
        fpui.check(u32(sh, so - 4) == u32(ph, po - 4), 'component kind order changed')
        if u32(ph, po) != u32(sh, so):
            header_ops.append((fpui.OP_ADD32, so, u32(ph, po) - u32(sh, so)))
    inserts = []
    for (scount, send, w), (pcount, pend, pw) in zip(arrays, parrays):
        n_s, n_p = u32(sh, scount), u32(ph, pcount)
        fpui.check(n_p >= n_s and sh[scount + 4:send] == ph[pcount + 4:pcount + 4 + (send - scount - 4)],
                   'a stock budget entry changed')
        if n_p > n_s:
            added = ph[pend - 4 * w * (n_p - n_s):pend]
            inserts.append((send, bytes(added)))
            header_ops.append((fpui.OP_ADD32, scount, n_p - n_s))
            grow += len(added)
    header_ops.append((fpui.OP_ADD32, 4, grow))

    # ---- parent child count, ModeChange --------------------------------------
    pchild = parent_at + 16
    mc_count, mc_list_end = mode_change_at + 16, None
    mc = dict(s_recs)[mode_change_at]
    mc_list_end = mode_change_at + 28 + u32(mc, 8) * 21 + u32(mc, 16) * 8
    new_root = manifest['new_row']['root_id']

    # ---- the ops ---------------------------------------------------------------
    n_ids = len(id_map)
    total_insert = length + grow + 8
    page_name = blk.string(name, fpui.NAME)
    blk.op(fpui.OP_PAGE, page_name, entry_offset, len(stock_page), first, n_ids,
           total_insert, len(inserts) + 2)
    for pos in (0, insert_at, parent_at, mode_change_at):      # record tags stay put
        blk.op(fpui.OP_GUARD, pos, u32(stock_page, pos))
    blk.op(fpui.OP_ALLOC, blk.string(name + '.' + counter, fpui.NAME), row_base_y, ROW_STEP)
    for code, pos, delta in header_ops:
        blk.op(code, pos, delta)
    for pos, data in inserts:
        blk.op(fpui.OP_INSERT, pos, blk.fragment(data), len(data))
    off = blk.fragment(bytes(frag))
    args = []
    for kind, arg, at in relocs:
        args += [kind << 24 | arg, at]
    blk.op(fpui.OP_INSERT, insert_at, off, length, *args)
    blk.op(fpui.OP_ADD32, pchild, 1)
    root = blk.fragment(struct.pack('>II', 0, root_clip))     # {root, its mode clip}
    blk.op(fpui.OP_INSERT, mc_list_end, root, 8, fpui.R_LOCAL_ID << 24 | (new_root - first), 0)
    blk.op(fpui.OP_ADD32, mc_count, 1)
    blk.op(fpui.OP_ADD32, mode_change_at + 4, 8)
    blk.op(fpui.OP_DONE)
    info = {'page': name, 'objects': n_ids, 'first_local_id': first, 'row_bytes': length,
            'header_grow': grow, 'relocations': len(relocs),
            'strings': [t for t, _ in blk.strings]}
    return blk, info


def prove_same(blob, name, stock_page, page, manifest, pool_stock, stock_at):
    """Apply blob to the stock page; it must be the builder's page, with each
    private string field holding the offset its text was given (stock place or
    the block's string layer)."""
    pages = {name: fpui.PageCopy(stock_page, len(stock_page), 1)}
    pool = fpui.Strings(pool_stock)
    fpui.apply(blob, pages, pool)
    got = bytes(pages[name].data)
    want = bytearray(page)
    locs = {r['source_record']: r['candidate_offset'] for r in manifest['record_locations']
            if r['provenance'].startswith('cloned')}
    for f in manifest['typed_string_fields']:
        if f['source_record'] in locs and f['pool_offset'] >= len(pool_stock):
            at = locs[f['source_record']] + f['field_offset']
            struct.pack_into('>I', want, at, pool.offset(f['text'], stock_at(f['text'])))
    fpui.check(len(got) == len(want), 'length %d, builder %d' % (len(got), len(want)))
    diff = [i for i in range(len(got)) if got[i] != want[i]]
    fpui.check(not diff, 'differs from the builder page at %s' % [hex(i) for i in diff[:8]])
    return pages[name], pool
