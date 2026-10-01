#!/usr/bin/env python3
"""Read what the fpLossless card recorded, after a take.

    python3 -B lossless/card_status.py --layout <build>/layout.json --dry-run
    python3 -B lossless/card_status.py --layout <build>/layout.json

THIS READS CAMERA MEMORY (reads only; nothing is ever written). --dry-run
prints exactly what would be read and reads nothing.

What it reads: the cave arena 0xC072E064..0xC072EFB4 (to find the card's
48-byte record, whose word +32 is "FLPC"), then one word per field of the card
state, at offsets the ARM build itself reported in layout.json.
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'fp_usb_shell'))

ARENA, ARENA_END = 0xC072E064, 0xC072EFB4
MAGIC = 0x43504C46                      # "FLPC"


def find_record(mem_get):
    words = mem_get(ARENA, (ARENA_END - ARENA) // 4)
    for i in range(0, len(words) - 12):
        if words[i + 8] == MAGIC:
            block = ARENA + 4 * i
            state, base = words[i + 9], words[i + 10]
            if 0x40000000 <= base < 0xC0000000 and base < state and not base & 7:
                return block, state, base
    return None



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--layout', type=pathlib.Path, required=True)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--state', type=lambda s: int(s, 0),
                    help='the card state address, if already known (skips the scan)')
    a = ap.parse_args()
    layout = json.loads(a.layout.read_text())
    fields = layout['fields']
    if a.dry_run:
        print('would read (nothing written):')
        if a.state is None:
            print(f'  0x{ARENA:08X}..0x{ARENA_END:08X}  the cave arena, '
                  f'{(ARENA_END - ARENA) // 4} words, to find the "FLPC" record')
            print('  then one word at <state> + each offset below')
        else:
            print(f'  one word at 0x{a.state:08X} + each offset below')
        for name, off in fields.items():
            print(f'    +0x{off:04X}  {name}')
        return 0

    from putfile import mem_get
    if a.state is None:
        found = find_record(mem_get)
        if not found:
            print('no FLPC record in the cave: the card did not arm (or is not loaded)')
            return 1
        block, state, base = found
        print(f'record at 0x{block:08X}: state 0x{state:08X}, resident base 0x{base:08X}')
    else:
        state = a.state
    arrays = {'hold.refused_by': 8}
    v = {name: mem_get(state + off, 1)[0] for name, off in fields.items() if name not in arrays}
    arr = {name: mem_get(state + fields[name], n) for name, n in arrays.items() if name in fields}
    for name, value in v.items():
        print(f'  {name:42s} {value:>12}  0x{value:08X}')
    why = ['descriptor', 'reserve', 'handle', 'raster', 'short', 'output', 'no promise slot', '-']
    turned = {why[i]: n for i, n in enumerate(arr.get('hold.refused_by', [])) if n}

    print('\nreading:')
    if v['magic'] != 0x44524143:
        print('  the state is not initialised: the launcher never reached init')
        return 1
    menu = {0: 'never tried', 1: 'INSTALLED', 2: 'no GUI', 3: 'no MainB2 screen',
            4: 'reader not stock', 5: 'no MainB2 entry', 6: 'no room', 7: 'no menu data',
            8: 'menu data damaged', 9: 'registration failed', 10: 'variable not usable',
            11: 'shared string pool refused'}
    print(f'  menu row: {menu.get(v["menu.result"], v["menu.result"])}; takes left stock '
          f'because OFF {v["rec_menu_off"]}')
    w, h, bits = (v['rec.facts.seen.width'], v['rec.facts.seen.height'],
                  v['rec.facts.seen.bits'])
    if w and h and bits:
        print(f'  producer {w}x{h} {bits}-bit, raster {v["rec.facts.seen.raster"]:,} B')
    tid = v['task_id'] - (1 << 32) if v['task_id'] & 0x80000000 else v['task_id']
    lanes = ['hold'] + (['hold_b'] if v['hold_b.magic'] else [])
    print(f'  takes admitted {v["rec_admitted"]}, recorded RAW {v["rec_raw"]}; codec task '
          + (f'id {tid}' if tid > 0 else 'none (one lane)') + '; second lane '
          + ('ON' if v['hold_b.magic'] else f'off (failed {v["lane_b_failed"]})'))
    held = sum(v[f'{x}.held'] for x in lanes)
    print(f'  frames {v["hold.arrivals"]}: held {held}, compressed '
          f'{sum(v[f"{x}.compressed"] for x in lanes)}, passed because both lanes busy '
          f'{v["hold.lanes_full"]}, not eligible {v["hold.not_eligible"]}'
          + (f' {turned}' if turned else ''))
    print(f'  engine refused {sum(v[f"{x}.refused"] for x in lanes)}, no saving '
          f'{sum(v[f"{x}.no_benefit"] for x in lanes)}, faults '
          f'{sum(v[f"{x}.faults"] for x in lanes)}; header DMA refused '
          f'{sum(v[f"{x}.dma_failed"] for x in lanes)}')
    print(f'  zero copy: swapped {sum(v[f"{x}.swapped"] for x in lanes)}, declined '
          f'{v["hold.swap_declined"]}, undone {v["hold.swap_undone"]}; spare '
          f'{v["spare_bytes"]:,} B, failed {v["spare_failed"]}, freed {v["spares_freed"]}')
    print(f'  stop: stops {v["stops"]}, finished {v["finishes"]}, busy {v["finish_busy"]}, '
          f'lanes now {v["hold.lane"]}/{v["hold_b.lane"]}, result {v["lane_stop_result"]}')
    print(f'  flush: trailers applied {v["flush.applied"]}, trailer refused '
          f'{v["flush.trailer_failed"]}, other length {v["flush.length_mismatch"]}; '
          f'promises a previous take left {v["stale_promises"]}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
