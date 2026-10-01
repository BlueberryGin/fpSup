#!/usr/bin/env python3
"""Read what the fpLossless TEST card recorded, after a take.

    python3 -B lossless/card_status.py --layout <build>/layout.json --dry-run
    python3 -B lossless/card_status.py --layout <build>/layout.json

THIS READS CAMERA MEMORY. The project rule is that every memory read needs the
operator's consent, naming the addresses, each time. --dry-run prints exactly
what would be read and reads nothing; run it first and quote its output when
asking. Only reads: nothing is ever written.

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
RESERVE = 0x15400


def find_record(mem_get):
    words = mem_get(ARENA, (ARENA_END - ARENA) // 4)
    for i in range(0, len(words) - 12):
        if words[i + 8] == MAGIC:
            block = ARENA + 4 * i
            state, base = words[i + 9], words[i + 10]
            if 0x40000000 <= base < 0xC0000000 and base < state and not base & 7:
                return block, state, base
    return None


def told_bytes(w, h, bits):
    return ((w * h * bits) // 8 + 0x3FF) & ~0x3FF


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
    arrays = {'req_hist': 48, 'req_last': 16, 'hold.refused_by': 8, 'arr_log': 64}
    v = {name: mem_get(state + off, 1)[0] for name, off in fields.items() if name not in arrays}
    arr = {name: mem_get(state + fields[name], n) for name, n in arrays.items() if name in fields}
    for name, value in v.items():
        print(f'  {name:42s} {value:>12}  0x{value:08X}')

    if 'hold.refused_by' in arr:
        why = ['descriptor', 'reserve', 'handle', 'raster', 'short, no copy span',
               'output', 'no promise slot', '-']
        print('  frames turned away by: ' + str({why[i]: n for i, n in
                                                 enumerate(arr['hold.refused_by']) if n}))
    if 'req_hist' in arr:
        seen = {f'0x{i:02X}' if i < 47 else '>=0x2F': n for i, n in enumerate(arr['req_hist']) if n}
        print(f'  request codes at C03A33C8 (count): {seen}')
        k = v.get('req_n', 0)
        last = [arr['req_last'][(k - 1 - j) & 15] for j in range(min(k, 16))]
        print(f'  most recent first: {[hex(x) for x in last]}')
    if 'arr_log' in arr and v.get('arr_n'):
        k = v['arr_n']
        rows = [arr['arr_log'][(k - min(k, 64) + j) & 63] for j in range(min(k, 64))]
        phase = ['free', 'held', 'enc', 'ready', 'commit', 'retained', 'raw']
        print(f'  arrivals logged {k}; the last {len(rows)} (gap us / phase found / held):')
        print('   ' + ' '.join(f'{r & 0xFFFFFF}/{phase[(r >> 24) & 15] if ((r >> 24) & 15) < 7 else "?"}'
                               f'{"*" if r >> 28 & 1 else ""}' for r in rows))
        gaps = sorted(r & 0xFFFFFF for r in rows[1:])
        if gaps:
            print(f'  gap us: min {gaps[0]}, median {gaps[len(gaps) // 2]}, max {gaps[-1]}')
        print(f'  job out us: last {v["job_us_last"]}, min {v["job_us_min"]}, '
              f'max {v["job_us_max"]} over {v["job_seen"]} jobs')
    print('\nreading:')
    if v['magic'] != 0x44524143:
        print('  the state is not initialised: the launcher never reached init')
        return 1
    w, h, bits = (v['rec.facts.seen.width'], v['rec.facts.seen.height'],
                  v['rec.facts.seen.bits'])
    if w and h and bits:
        told = told_bytes(w, h, bits)
        cap = v['hold.last_capacity']
        print(f'  producer {w}x{h} {bits}-bit, raster {v["rec.facts.seen.raster"]:,} B, '
              f'engine told {told:,} B')
        if cap:
            room = cap - RESERVE
            span = ((cap + 0x3FF) & ~0x3FF) - RESERVE
            print(f'  a frame\'s own allocation: 0x{cap:X} ({cap:,} B), {room:,} B after the '
                  f'header, {span:,} B to its 1 KiB boundary: '
                  + ('read in place' if span >= told else f'{told - span} B SHORT: not held'))
    print(f'  takes admitted {v["rec_admitted"]}, recorded RAW {v["rec_raw"]}; frames held '
          f'{v["hold.held"]} (read in place {v["hold.direct"]}), '
          f'passed {v["hold.passed"]}')
    print(f'  compressed {v["hold.compressed"]}, engine refused {v["hold.refused"]}, '
          f'no saving {v["hold.no_benefit"]}, faults {v["hold.faults"]}')
    names = {0: 'never tried', 1: 'INSTALLED', 2: 'no GUI', 3: 'no MainB2 screen',
             4: 'reader not stock', 5: 'no MainB2 entry', 6: 'no room', 7: 'no FPLMENU.BIN',
             8: 'FPLMENU.BIN not ours/damaged', 9: 'registration failed',
             10: 'variable not usable', 11: 'shared string pool refused'}
    print(f'  menu row: {names.get(v["menu.result"], v["menu.result"])}, registered this boot '
          f'{v["menu.registered"]}, file {v["menu.file_len"]:,} B; REC reads {v["menu.reads"]} '
          f'(ON {v["menu.on_reads"]}), takes left stock because OFF {v["rec_menu_off"]}')
    # both lanes on the one engine: busy over the span from the first start to
    # the last finish, each job timed by the task's 1 ms check
    lanes = ['hold'] + (['hold_b'] if v['hold_b.magic'] else [])
    jobs = sum(v[f'{h}.us.jobs'] for h in lanes)
    busy = sum(v[f'{h}.us.busy_total'] for h in lanes)
    starts = [v[f'{h}.us.first_submit'] & ~1 for h in lanes if v[f'{h}.us.jobs']]
    ends = [v[f'{h}.us.last_done'] for h in lanes if v[f'{h}.us.jobs']]
    span = (max(ends) - min(starts)) & 0xFFFFFFFF if starts else 0
    if jobs and span:
        fastest = min(v[f'{h}.us.engine_min'] for h in lanes if v[f'{h}.us.jobs'])
        print(f'  engine load: {jobs} jobs over {span / 1e6:.2f} s; busy '
              f'{100 * min(busy, span) / span:.0f}% (start to found done), fastest job '
              f'{fastest / 1000:.1f} ms; frames held '
              f'{sum(v[f"{h}.held"] for h in lanes)}/{v["arrive_calls"]} arrivals')
    tid = v['task_id'] - (1 << 32) if v['task_id'] & 0x80000000 else v['task_id']
    print(f'  codec task: id {tid}' + (' (none: one lane, old way)' if tid < 1 else '') +
          f', passes {v["task_alive"]:,}; second lane '
          + ('ON' if v['hold_b.magic'] else f'off (failed {v["lane_b_failed"]})') +
          f'; lanes now {v["hold.lane"]}/{v["hold_b.lane"]}, stop {v["lane_stop_result"]}')
    chained = sum(v[f'{h}.chained'] for h in lanes)
    gap = sum(v[f'{h}.us.gap_total'] for h in lanes)
    if chained:
        print(f'  chained starts {chained} (waiting frame started when the engine finished): '
              f'gap mean {gap / chained / 1000:.2f} ms, max '
              f'{max(v[f"{h}.us.gap_max"] for h in lanes) / 1000:.2f} ms')
    print(f'  passed because both lanes busy {v["hold.lanes_full"]}; held-to-start max '
          f'{max(v[f"{h}.us.wait_max"] for h in lanes) / 1000:.1f} ms')
    if v['hold_b.magic']:
        print(f'  lane B: held {v["hold_b.held"]}, compressed {v["hold_b.compressed"]}, '
              f'refused {v["hold_b.refused"]}, no saving {v["hold_b.no_benefit"]}, '
              f'faults {v["hold_b.faults"]}, swapped {v["hold_b.swapped"]}')
    if v['stale_promises']:
        print(f'  promises a previous take left (frames the firmware dropped): {v["stale_promises"]}')
    print(f'  engine power: opened {v["hold.job.opens"] + v["hold_b.job.opens"]} times, '
          f'started still powered {v["hold.job.kept"] + v["hold_b.job.kept"]}; word now '
          f'{v["codec_power"]}, close at stop {v["hold.power_off_result"]}; submit last/max '
          f'A {v["hold.us.submit_last"] / 1000:.2f}/{v["hold.us.submit_max"] / 1000:.2f} ms, '
          f'B {v["hold_b.us.submit_last"] / 1000:.2f}/{v["hold_b.us.submit_max"] / 1000:.2f} ms')
    print(f'  header copy (DMA): A last {v["hold.us.copy_last"] / 1000:.2f} ms, max '
          f'{v["hold.us.copy_max"] / 1000:.2f} ms; B last {v["hold_b.us.copy_last"] / 1000:.2f} ms, '
          f'max {v["hold_b.us.copy_max"] / 1000:.2f} ms; DMA refused '
          f'{v["hold.dma_failed"] + v["hold_b.dma_failed"]} (CPU copy used)')
    print(f'  zero copy: swapped {v["hold.swapped"]}, declined {v["hold.swap_declined"]}, '
          f'undone {v["hold.swap_undone"]}; spare asked {v["spare_bytes"]:,} B, '
          f'failed {v["spare_failed"]}, freed {v["spares_freed"]}, now '
          f'0x{v["hold.workspace.spare.handle"]:08X}/{v["hold.workspace.spare.capacity"]:,} B')
    print(f'  flush: trailers applied {v["flush.applied"]}, trailer refused '
          f'{v["flush.trailer_failed"]}, other length {v["flush.length_mismatch"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
