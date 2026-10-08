#!/usr/bin/env python3
"""Build the Y2_5_1 page with a "Screen Flip" row on its TOOLS tab.

System 2 > Mode Settings (Y2_5) > Custom 1..4 > More options (Y2_5_1), third
tab (TOOLS: Electronic Level / Brightness Level Monitor / Zebra / Audiometer).
The new row is the tab's fifth, y 405: "Screen Flip" Off / 180 / Mirror /
180+Mirror, bound to the private integer MV_fpScreenFlip (0..3), registered
by the card in the UI registry: memory only, 0 at every boot, never in the
settings store.

Method (.claude/skills/fp-native-ui §8, fp-ui-add): clone a row of the SAME
resource set and turn the result into FPUI additions (fpSup/uishare/ui/rows.py
row_block). This module makes the whole page; menu/build_flip_fpui.py turns it
back into what was ADDED and proves the additions give exactly this page.

Donor: tab 1's "1_04_CINE" (object 5063, Time Code display), a
MenuItem_Select_4 whose popup is four FIXED children 00..03 with their own
drawText -- no CSV, so no file redirect and no borrowed .cvm. It already sits
at y 405, the TOOLS tab's next free row. Its whole subtree is one record run
and every group it plays is owned inside it, except the page's STILL_CINE
mode group (0xC2B3E19C), which lists it with its clip 2.

Changes to the clone, each typed and checked against stock bytes:
  parent 92 (tab 1) -> 101 (tab 3); root name -> fpScreenFlip_Row;
  MV_DSPMODFIX_TimeCode (6 fields) -> MV_fpScreenFlip;
  title drawText + its clip (1181, 1181_R1, 1181_R2) -> literal "Screen Flip";
  popup texts 1182..1185 and the summary drawText + clip -> literals;
  the root's mode clip (STILL 0 / CINE 1 / STILL_Like 0) -> 1 / 1 / 1, so the
  row shows in every mode (the TOOLS tab's other rows have no mode variant).
Page-level: header budgets extended, tab 3 child count 5 -> 6, the clone's
root appended to STILL_CINE ({root, 2}), as rows.row_block adds it.

No firmware image, VSHL, AutoRun, card or camera access.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

HERE = Path(__file__).resolve().parent
FPSUP = HERE.parents[1]
ROOT = FPSUP.parent
sys.path.insert(0, str(FPSUP / 'lossless/menu'))
import build_menu_candidate as BM  # noqa: E402  (Remapper, PrivatePool, schemas, header)

SEG0 = ROOT / 'out/seg0_c0000000.bin'
AUDIT = ROOT / 'research/ui/tools/native_ui_audit.py'
NAMES = ('Y2_5_1.fpScreenFlip.page', 'Y2_5_1.fpScreenFlip.strings', 'manifest.json')

# ---- pinned Ver.5.02 facts (file offsets into seg0, base 0xC0000000) -------
DECLARATION = 0x18EC31C           # tag-10001 screen declaration "Y2_5_1"
ENTRY_OFFSET = 0x127B649          # NBU-relative page offset (runtime entry +08)
PAGE_START, PAGE_END = 0x2B3BAA9, 0x2B6B4B2      # includes FFFFFFFF/8
PAGE_SHA256 = '159f5e12c09c783d3befdeec7eee9645baa288b1d6aea6ced1cd6a7aa9fbd849'
HEADER_SHA256 = 'd5387c45e36aeaea777da316bdc8e8497cb86471fb9482d54450f7fe4eb97f28'
DONOR_START, DONOR_END = 0x2B4EE03, 0x2B50EE3    # 1_04_CINE subtree, whole records
DONOR_SHA256 = '1ec262479073d58d2ef61225ecd5d926b837afa5fbeb35f9f017829b6aa0415b'
DONOR_ROOT, DONOR_PARENT = 5063, 92
TAB_ID, TAB_RECORD = 101, 0x2B5E812             # "3" = the TOOLS tab
TAB_ROWS = (103, 6340, 6971, 7267, 7289)        # 3_01 .. 3_04_SLike
INSERT_AT = 0x2B69D8E             # the Footer object: the clone follows 3_04_SLike
MODE_GROUP = 0x2B3E19C            # STILL_CINE (STILL / CINE / STILL_Like)
ROOT_OBJECTBASE = 0x2B4EE27       # 5063 objectBase: mask 1 (position), y @36
ROOT_CLIP = 0x2B4EE4F             # 5063 clip 2: is-visible keys @54/64/74
ROW_Y = 405.0                     # the TOOLS tab's fifth row (81 per row)
TAB_HEIGHT = 486.0
ROWS = 4

VALUE_VARIABLE, ROW_NAME = 'MV_fpScreenFlip', 'fpScreenFlip_Row'
TITLE = 'Screen Flip'
LABELS = ('Off', '180', 'Mirror', '180+Mirror')   # value 0..3

# record -> (field offset, stock text, private text): one field per record
PRIVATE_FIELDS = {
    0x2B4F3AC: (40, 'MV_DSPMODFIX_TimeCode', VALUE_VARIABLE),   # MenuItem appVariableEvent
    0x2B4F3E1: (36, 'MV_DSPMODFIX_TimeCode', VALUE_VARIABLE),   # MenuItem appVariableChangeEvent
    0x2B4F40E: (54, 'MV_DSPMODFIX_TimeCode', VALUE_VARIABLE),   # summary animation apply-frame
    0x2B4FEBB: (36, 'MV_DSPMODFIX_TimeCode', VALUE_VARIABLE),   # ListItem Right -> value
    0x2B4FF4F: (36, 'MV_DSPMODFIX_TimeCode', VALUE_VARIABLE),   # ListItem OK -> value
    0x2B5032F: (32, '1182', LABELS[0]),                         # popup 00 Text01
    0x2B504D0: (32, '1183', LABELS[1]),                         # popup 01 Text01
    0x2B5066D: (32, '1184', LABELS[2]),                         # popup 02 Text01
    0x2B5080A: (32, '1185', LABELS[3]),                         # popup 03 Text01
    0x2B50947: (32, '1181', TITLE),                             # Name
    0x2B50ADE: (32, '1182', LABELS[0]),                         # IconText01 (summary)
}
LITERAL_RECORDS = [0x2B5032F, 0x2B504D0, 0x2B5066D, 0x2B5080A, 0x2B50947, 0x2B50ADE]
TEXT_ANIMATIONS = {
    0x2B50A3C: {'1181': TITLE, '1181_R1': TITLE, '1181_R2': TITLE},      # Name clip 3
    0x2B50BCF: {'1182': LABELS[0], '1183': LABELS[1],                    # IconText01 clip 4
                '1184_R1': LABELS[2], '1185_R1': LABELS[3]},
}
# Kept on purpose, as fpLossless kept its submenu_06: the row's focus request
# and the page's shared animation sets.
KEPT_STRINGS = {'submenu_05', 'MENU_Level3cu', 'submenu_width', 'submenu_width_NoIcon',
                'DSPMODFIX_TimeCodeAj', 'DSPMODFIX_TimeCodeFix'}


def f32(v):
    return struct.unpack('>I', struct.pack('>f', v))[0]


def check(condition, message):
    if not condition:
        raise BM.CandidateError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def owners(page):
    """Owner object of each record: the object itself, or a component's owner."""
    result, owner = [], None
    for record in page:
        if record.tag == 0x10003:
            owner = record.word(20)
        elif record.tag in BM.OWNER_OFFSETS:
            owner = record.word(BM.OWNER_OFFSETS[record.tag])
        elif record.tag == 0x1000B:
            owner = record.word(24)
        result.append(owner)
    return result


def subtree(objects, root):
    ids, grew = {root}, True
    while grew:
        grew = False
        for object_id, obj in objects.items():
            if obj['parent_id'] in ids and object_id not in ids:
                ids.add(object_id)
                grew = True
    return ids


def external_references(page, pool, objects, donor_ids, schemas):
    """Typed scan of every record outside the donor for a donor object ID."""
    probe = BM.Remapper(pool, {}, set(objects), schemas,
                        {'start': -1, 'pure_select': False, 'private_fields': {},
                         'literal_records': [], 'geometry_clip': -1})
    probe.clip = lambda data, source: None          # clips: owner checked by owners()
    hits, unscanned = [], []
    for record in page:
        if DONOR_START <= record.offset < DONOR_END:
            continue
        if record.tag == 0x10002 or record.tag >= 0x11000 or record.tag == 0xFFFFFFFF:
            continue
        before = len(probe.object_fields)
        try:
            probe.transform(record)
        except BM.CandidateError as exc:
            unscanned.append({'record': '0x%08X' % (0xC0000000 + record.offset),
                              'tag': '0x%X' % record.tag, 'why': str(exc)})
            continue
        hits += [f for f in probe.object_fields[before:] if f['old'] in donor_ids]
    return hits, unscanned, probe.unresolved


def mode_group_roots(data):
    states, count = BM.u32(data, 8), BM.u32(data, 16)
    at = 28 + 21 * states
    return at, [struct.unpack_from('>II', data, at + 8 * i) for i in range(count)]


def build(source, audit):
    check(len(source) == audit.SEG0_SIZE and digest(source) == audit.SEG0_SHA256,
          'input is not the pinned Ver.5.02 seg0')
    pool = BM.PrivatePool(source[audit.POOL:audit.POOL_END])
    check(pool.resolve(BM.u32(source, DECLARATION + 4)) == 'Y2_5_1', 'Y2_5_1 declaration name mismatch')
    check(BM.u32(source, DECLARATION + 12) == ENTRY_OFFSET and audit.NBU + ENTRY_OFFSET == PAGE_START,
          'Y2_5_1 declaration offset mismatch')
    check(digest(source[PAGE_START:PAGE_END]) == PAGE_SHA256, 'Y2_5_1 page hash mismatch')
    check(digest(source[DONOR_START:DONOR_END]) == DONOR_SHA256, '1_04_CINE donor hash mismatch')

    page = audit.records(source, PAGE_START, PAGE_END)
    check((page[-1].tag, len(page[-1].data)) == (0xFFFFFFFF, 8), 'page does not end with FFFFFFFF/8')
    check(digest(page[0].data) == HEADER_SHA256, 'allocation header hash mismatch')
    header = audit.parse_header(page[0], pool.resolve)
    objects = audit.parse_objects(page, pool.resolve)
    check(header['objects'] == len(objects), 'object budget mismatch')
    groups = [r for r in page if r.tag == 0x1000B]
    check(len(groups) == len(header['groups']), 'group budget mismatch')
    by_offset = {r.offset: r for r in page}
    check(all(a in by_offset for a in (DONOR_START, DONOR_END, INSERT_AT, TAB_RECORD, MODE_GROUP,
                                       ROOT_OBJECTBASE, ROOT_CLIP)),
          'a pinned boundary is not a whole record')

    # ---- the tab and its rows ---------------------------------------------
    tab = objects[TAB_ID]
    check((tab['parent_id'], tab['name'], tab['child_count']) == (83, '3', len(TAB_ROWS)) and
          by_offset[TAB_RECORD].word(20) == TAB_ID, 'TOOLS tab identity mismatch')
    check(sorted(i for i, o in objects.items() if o['parent_id'] == TAB_ID) == sorted(TAB_ROWS),
          'TOOLS tab children mismatch')
    check([objects[i]['name'] for i in TAB_ROWS] == ['3_01', '3_02', '3_03', '3_04_CINE', '3_04_SLike'],
          'TOOLS row names mismatch')
    owner_of = owners(page)
    last_row = subtree(objects, TAB_ROWS[-1])
    at = [r.offset for r in page].index(INSERT_AT)
    check(owner_of[at - 1] in last_row and by_offset[INSERT_AT].tag == 0x10003 and
          by_offset[INSERT_AT].word(20) == 37 and objects[37]['name'] == 'Footer',
          'the TOOLS tab is not followed by Footer')

    # ---- the donor --------------------------------------------------------
    donor_ids = subtree(objects, DONOR_ROOT)
    check(objects[DONOR_ROOT]['name'] == '1_04_CINE' and objects[DONOR_ROOT]['parent_id'] == DONOR_PARENT,
          'donor root identity mismatch')
    inside = [i for i, r in enumerate(page) if DONOR_START <= r.offset < DONOR_END]
    check(all(owner_of[i] in donor_ids for i in inside), 'donor range holds a foreign record')
    check(all(DONOR_START <= r.offset < DONOR_END for i, r in enumerate(page)
              if 0x10003 <= r.tag < 0x11000 and owner_of[i] in donor_ids),
          'a donor-owned record lies outside the donor range')
    check(not any(r.tag == 0x10008 for r in page if DONOR_START <= r.offset < DONOR_END),
          'the donor has a CSV list: this builder expects fixed popup rows')
    schemas = BM.load_property_schemas(source)
    hits, unscanned, untyped_outside = external_references(page, pool, objects, donor_ids, schemas)
    expected_hit = [('0x%08X' % (0xC0000000 + MODE_GROUP), DONOR_ROOT, 'group_clip_owner')]
    check([(h['source_record'], h['old'], h['role']) for h in hits] == expected_hit,
          'records outside the donor reference it: %r' % hits[:4])
    mg = by_offset[MODE_GROUP].data
    check(pool.resolve(BM.u32(mg, 20)) == 'STILL_CINE' and BM.u32(mg, 24) == 1,
          'mode group identity mismatch')
    roots_at, roots = mode_group_roots(mg)
    check((DONOR_ROOT, 2) in roots and roots_at + 8 * len(roots) + 8 == len(mg),
          'the mode group does not list the donor with clip 2')

    root_base = by_offset[ROOT_OBJECTBASE].data
    check(pool.resolve(BM.u32(root_base, 8)) == 'objectBase' and BM.u32(root_base, 20) == DONOR_ROOT and
          BM.u32(root_base, 28) == 1 and BM.u32(root_base, 32) == 0 and
          BM.u32(root_base, 36) == f32(ROW_Y), 'donor row is not at y 405')
    check(ROW_Y + 81.0 <= TAB_HEIGHT, 'the fifth row does not fit the tab')
    clip = audit.parse_clip(by_offset[ROOT_CLIP], pool.resolve)
    check(BM.u32(by_offset[ROOT_CLIP].data, 20) == DONOR_ROOT and BM.u32(by_offset[ROOT_CLIP].data, 24) == 2 and
          len(by_offset[ROOT_CLIP].data) == 78, 'donor mode clip identity mismatch')
    check([BM.u32(by_offset[ROOT_CLIP].data, o) for o in (54, 64, 74)] == [0, 1, 0] and
          [BM.u32(by_offset[ROOT_CLIP].data, o - 4) for o in (54, 64, 74)] == [0, 66, 133],
          'donor mode clip is not STILL 0 / CINE 1 / STILL_Like 0: %r' % clip)

    # ---- the clone ----------------------------------------------------------
    donor = [page[i] for i in inside]
    mapping = BM.allocate_ids(list(objects), [r.word(20) for r in donor if r.tag == 0x10003])
    new_root = mapping[DONOR_ROOT]
    profile = {'start': -1, 'pure_select': False, 'private_fields': PRIVATE_FIELDS,
               'literal_records': LITERAL_RECORDS, 'geometry_clip': -1,
               'text_animation_values': TEXT_ANIMATIONS,
               'word_writes': {
                   DONOR_START: [(24, DONOR_PARENT, TAB_ID, 'reparent_tab1_to_tools_tab')],
                   ROOT_CLIP: [(54, 0, 1, 'visible_in_still'), (74, 0, 1, 'visible_in_still_like')]}}
    remapper = BM.Remapper(pool, mapping, set(objects), schemas, profile)
    clone = []
    for record in donor:
        data = record.data
        if record.offset == DONOR_START:           # the row's root object: private name
            check(record.tag == 0x10003 and record.word(20) == DONOR_ROOT, 'donor root mismatch')
            data = bytearray(data)
            check(pool.resolve(BM.u32(data, 28)) == '1_04_CINE', 'donor root name mismatch')
            struct.pack_into('>I', data, 28, pool.intern(ROW_NAME))
            data = bytes(data)
        clone.append((record, remapper.transform(audit.Record(record.offset, data))))
    check(not remapper.unresolved, 'donor has untyped property bodies: %r' % remapper.unresolved[:2])
    # The row's y: unchanged (the donor already sits in the fifth slot), but
    # named, so row_block hands it out from the tab's row counter at boot.
    remapper.changes.append({'source_record': '0x%08X' % (0xC0000000 + ROOT_OBJECTBASE),
                             'field_offset': 36, 'role': 'tools_row_y', 'old': f32(ROW_Y), 'new': f32(ROW_Y)})
    texts = [f['text'] for f in remapper.string_fields]
    check('MV_DSPMODFIX_TimeCode' not in texts and '1_04_CINE' not in texts and
          not {'1181', '1181_R1', '1181_R2', '1182', '1183', '1184', '1185', '1184_R1', '1185_R1'} & set(texts),
          'a stock binding survived')
    check(KEPT_STRINGS <= set(texts), 'expected shared strings missing from the clone')
    redirected = [c for c in remapper.changes if c['role'] == 'private_string_redirect']
    check(len(redirected) == len(PRIVATE_FIELDS), 'not every private field was redirected')
    literal = [c for c in remapper.changes if c['role'] == 'literal_text_not_variable_resolver']
    check(len(literal) == len(LITERAL_RECORDS), 'not every literal text left the resolver')
    animated = [c for c in remapper.changes if c['role'] == 'literal_text_animation']
    check(len(animated) == sum(len(v) for v in TEXT_ANIMATIONS.values()), 'a text animation key was missed')
    outside = sorted({f['old'] for f in remapper.object_fields
                      if f['scope'] == 'original_page_or_null' and f['old'] not in (0,)})

    group_indexes = [i for i, r in enumerate(groups) if DONOR_START <= r.offset < DONOR_END]
    donor_clips = [audit.parse_clip(r, pool.resolve) for r in donor if r.tag == 0x1000A]
    delta = {'objects': len(donor_ids),
             'component_counts': audit.component_counts(donor, pool.resolve),
             'source_group_budget_indexes': group_indexes,
             'group_budgets': [header['groups'][i] for i in group_indexes],
             'clip_property_counts': [len(c['properties']) for c in donor_clips],
             'property_key_counts': [len(p['keys']) for c in donor_clips for p in c['properties']]}
    new_header = BM.extend_header(page[0], header, delta)

    parts, locations, cursor = [], [], 0
    for record in page:
        if record.offset == INSERT_AT:             # the clone follows 3_04_SLike
            for original, data in clone:
                locations.append({'source_record': audit.address(original.offset), 'candidate_offset': cursor,
                                  'length': len(data), 'provenance': 'cloned_tools_row'})
                parts.append(data)
                cursor += len(data)
        if record.offset == PAGE_START:
            data = new_header
        elif record.offset == TAB_RECORD:
            data = bytearray(record.data)
            check(BM.u32(data, 16) == len(TAB_ROWS), 'TOOLS tab child count mismatch')
            struct.pack_into('>I', data, 16, len(TAB_ROWS) + 1)
            data = bytes(data)
        elif record.offset == MODE_GROUP:
            data = bytearray(record.data)
            data[roots_at + 8 * len(roots):roots_at + 8 * len(roots)] = struct.pack('>II', new_root, 2)
            struct.pack_into('>I', data, 16, len(roots) + 1)
            struct.pack_into('>I', data, 4, len(data))
            data = bytes(data)
        else:
            data = record.data
        locations.append({'source_record': audit.address(record.offset), 'candidate_offset': cursor,
                          'length': len(data),
                          'provenance': 'stock_modified' if data != record.data else 'stock_unchanged'})
        parts.append(data)
        cursor += len(data)
    candidate = b''.join(parts)

    # ---- the result, re-read from its bytes ---------------------------------
    parsed = audit.records(candidate, 0, len(candidate))
    after = audit.parse_objects(parsed, pool.resolve)
    counts = Counter(o['parent_id'] for o in after.values())
    check(len(parsed) == len(page) + len(clone) and len(after) == len(objects) + len(donor_ids),
          'candidate structure count mismatch')
    check(all(counts[o['id']] == o['child_count'] for o in after.values()), 'child counts inconsistent')
    check(after[new_root]['parent_id'] == TAB_ID and after[TAB_ID]['child_count'] == len(TAB_ROWS) + 1 and
          after[new_root]['name'] == ROW_NAME, 'the new row is not connected under the TOOLS tab')
    check(set(mapping.values()).isdisjoint(objects) and max(mapping.values()) < 0x10000,
          'new IDs collide with stock or leave 16 bits')
    after_header = audit.parse_header(parsed[0], pool.resolve)
    check(after_header['objects'] == len(after), 'object budget mismatch after')
    for key in ('groups', 'clip_property_counts', 'property_key_counts', 'trailing_budget'):
        check(after_header[key][:len(header[key])] == header[key], 'stock reservation changed: ' + key)
    for kind, count in delta['component_counts'].items():
        check(after_header['component_counts'][kind] == header['component_counts'][kind] + count,
              'component budget not extended: ' + kind)
    check(bytes(pool.data[:pool.original_length]) == source[audit.POOL:audit.POOL_END],
          'stock pool prefix changed')
    check((parsed[-1].tag, len(parsed[-1].data)) == (0xFFFFFFFF, 8), 'candidate lost its end record')
    stock_changed = [l['source_record'] for l in locations if l['provenance'] == 'stock_modified']
    check(stock_changed == [audit.address(PAGE_START), audit.address(MODE_GROUP), audit.address(TAB_RECORD)],
          'unexpected stock record change: %r' % stock_changed)

    manifest = {
        'schema_version': 1, 'kind': 'offline_y2_5_1_page_candidate', 'product': 'fpScreenFlip',
        'status': 'OFFLINE_ONLY_NOT_CAMERA_TESTED', 'camera_accessed': False,
        'source': {'seg0_sha256': audit.SEG0_SHA256, 'page': audit.address(PAGE_START),
                   'page_end_exclusive': audit.address(PAGE_END), 'page_sha256': PAGE_SHA256,
                   'nbu_offset': ENTRY_OFFSET, 'declaration': audit.address(DECLARATION),
                   'donor': '1_04_CINE Time Code display (MenuItem_Select_4, fixed popup rows)',
                   'donor_start': audit.address(DONOR_START), 'donor_end_exclusive': audit.address(DONOR_END),
                   'donor_sha256': DONOR_SHA256},
        'outputs': {NAMES[0]: {'length': len(candidate), 'sha256': digest(candidate)},
                    NAMES[1]: {'length': len(pool.data), 'sha256': digest(bytes(pool.data))}},
        'new_row': {'root_id': new_root, 'name': ROW_NAME, 'parent_id': TAB_ID, 'y': ROW_Y,
                    'inserted_before': audit.address(INSERT_AT), 'mode_clip': 2,
                    'title': TITLE, 'variable': VALUE_VARIABLE,
                    'values': dict((str(i), label) for i, label in enumerate(LABELS)),
                    'objects': len(donor_ids), 'records': len(clone),
                    'length': sum(len(d) for _, d in clone),
                    'object_id_map': {str(k): v for k, v in sorted(mapping.items())},
                    'stock_objects_still_referenced': outside},
        'string_pool': {'original_length': pool.original_length, 'added': pool.added},
        'budget': {'before_header_length': len(page[0].data), 'after_header_length': len(new_header),
                   'objects': after_header['objects'], 'donor_delta': delta},
        'variables_to_register': [{'name': VALUE_VARIABLE, 'type': 0, 'initial_value': 0}],
        'typed_changes': remapper.changes,
        'typed_object_fields': remapper.object_fields,
        'typed_string_fields': remapper.string_fields,
        'external_reference_scan': {'hits': hits, 'unscanned_records': unscanned,
                                    'untyped_bodies_outside': untyped_outside},
        'record_locations': locations,
        'unverified': [
            'runtime page switch for Y2_5_1 (proven on the camera for MainB2 and B2_5 only)',
            'a row cloned from tab 1 into tab 3 of the same resource set (§8b proved same-page rows)',
            'focus order: Up/Down from 3_04 reaching the fifth row, and back',
            'the kept focus request submenu_05 now also sent by a second row',
            'literal ASCII labels in the popup and summary (fpLossless proved a literal title)',
        ],
    }
    return candidate, bytes(pool.data), manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--seg0', type=Path, default=SEG0)
    parser.add_argument('--output', type=Path, required=True, help='new directory; never overwritten')
    args = parser.parse_args(argv)
    try:
        audit = BM.load_audit(AUDIT)
        page, pool, manifest = build(args.seg0.read_bytes(), audit)
        manifest['builder_sha256'] = digest(Path(__file__).read_bytes())
        encoded = json.dumps(manifest, indent=2, ensure_ascii=False).encode() + b'\n'
        check(not args.output.exists(), 'output directory must be new; refusing to overwrite')
        args.output.mkdir(parents=True)
        for name, data in zip(NAMES, (page, pool, encoded)):
            (args.output / name).write_bytes(data)
    except (OSError, ValueError) as exc:
        print('screenflip page failed: %s' % exc, file=sys.stderr)
        return 2
    print(json.dumps({'output': str(args.output.resolve()), 'page_length': len(page),
                      'pool_length': len(pool), 'new_root': manifest['new_row']['root_id']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
