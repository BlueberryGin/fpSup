#!/usr/bin/env python3
"""Measure the codec time of several tile grids on the camera, one take each.

    python3 -B lossless/tile_sweep.py LAYOUT.json SECONDS WxH [WxH ...]

For each grid: write card.c `tile_force` (w << 16 | h) and read it back, record
one take with rec_until.py --counted (nothing on USB while it records), then
read the take's mean codec job time, its grid and its uncompressed count.
`0x0` = the default grid. Leaves `tile_force` at 0. One JSON line per take.
"""
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'fp_usb_shell'))
import putfile as pf                                    # noqa: E402
from card_status import find_record                     # noqa: E402


def main():
    layout = json.loads(pathlib.Path(sys.argv[1]).read_text())
    fields = layout.get('fields', layout)
    seconds = sys.argv[2]
    found = find_record(pf.mem_get)
    if not found:
        raise SystemExit('no lossless record on the camera')
    _, state, _ = found

    def get(name):
        return pf.mem_get(state + fields[name], 1)[0]

    def force(value):
        for _ in range(5):                  # mem set can drop a write: read it back
            pf.mem_set(state + fields['tile_force'], value)
            if get('tile_force') == value:
                return
        raise SystemExit(f'tile_force would not take 0x{value:08X}')

    try:
        for grid in sys.argv[3:]:
            w, h = (int(x) for x in grid.lower().split('x'))
            force(w << 16 | h)
            out = subprocess.run([sys.executable, '-B', str(HERE / 'rec_until.py'),
                                  '--counted', seconds], capture_output=True, text=True)
            if out.returncode:
                raise SystemExit(f'take failed: {out.stdout} {out.stderr}')
            take = json.loads(out.stdout.strip().splitlines()[-1])
            n = get('hold.job_count') + get('hold_b.job_count')
            total = get('hold.job_sum_us') + get('hold_b.job_sum_us')
            def both(f):
                return get('hold.' + f) + get('hold_b.' + f)

            def per(f):
                return round(both(f) / n / 1000, 3) if n else None
            cycles = both('cycle_count')
            print(json.dumps({
                'asked': grid, 'grid': f'{get("hold.job.tile_width")}x{get("hold.job.tile_height")}',
                'tiles': get('hold.job.tiles'), 'jobs': n,
                'mean_ms': round(total / n / 1000, 2) if n else None,
                'max_ms': round(max(get('hold.job_max_us'), get('hold_b.job_max_us')) / 1000, 2),
                'frames': get('take_frames'),
                'submit_ms': per('submit_sum_us'), 'finish_ms': per('finish_sum_us'),
                'last_gap_ms': per('gap_sum_us'), 'polls': round(both('poll_sum') / n, 1) if n else None,
                'lane_cycle_ms': round(both('cycle_sum_us') / cycles / 1000, 2) if cycles else None,
                'lanes_full': get('hold.lanes_full'), 'stalls': get('hold.stalls') + get('hold_b.stalls'),
                'take': take}), flush=True)
    finally:
        force(0)


if __name__ == '__main__':
    main()
