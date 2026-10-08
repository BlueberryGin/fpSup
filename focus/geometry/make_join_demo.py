#!/usr/bin/env python3
"""Explicitly synthetic final-drive/AF/scale pairing fixtures; no camera I/O."""
import argparse
import json
from pathlib import Path

from geometry import SAMPLE_FIELDS
from join_replay import SCHEMA


def events():
    yield dict(type="metadata", schema=SCHEMA, provenance="synthetic",
               clock="common_epoch_ms", scale_semantics="normalized_linear_size",
               simulate_confirmation=True,
               note="All validity, identity and optical-settle assertions are synthetic assumptions.")
    sequence, source_seq, episode, drive, now = 0, 0, 0, 0, 0

    def event(kind, *, dt=20, flags=3, depth=1.0, **changes):
        nonlocal sequence, source_seq, now
        sequence += 1
        now += dt
        sample = dict.fromkeys(SAMPLE_FIELDS, 0)
        if kind == 5:
            source_seq += 1
            sample.update(now_ms=now, sample_ms=now, source_seq=source_seq,
                          target_generation=1, context_generation=1,
                          scale_generation=1, source_kind=1, flags=31,
                          scale_q16=round(16384 / depth), lens_position=10000)
        data = dict(kind=kind, stream_seq=sequence, event_ms=now, now_ms=now,
                    episode_generation=episode, drive_generation=drive,
                    target_generation=1, context_generation=1, scale_generation=1,
                    source_kind=1, flags=flags, error_code=0,
                    position_ms=now if kind == 4 else 0,
                    end_position=10000 if kind == 4 else 0, sample=sample)
        data.update(changes)
        return {"event": data}

    def begin():
        nonlocal episode, drive
        episode += 1
        yield event(1)
        drive += 1
        yield event(2)

    yield from begin()
    yield event(3, flags=11)  # success before final end
    yield event(4, flags=3)   # backlash intermediate
    yield event(5)            # no candidate until true final end
    yield event(4, flags=55)
    yield event(5)
    for i in range(1, 31):
        yield event(5, dt=40, depth=1 - i * 0.008)
    yield event(6)            # cancellation invalidates active anchor
    yield event(5)
    yield from begin()
    yield event(4, flags=55)  # reverse arrival order
    yield event(3, flags=11)
    yield event(5)
    for i in range(1, 21):
        yield event(5, dt=40, depth=1 + i * 0.008)
    changed = event(5)
    changed["event"]["target_generation"] = 2
    changed["event"]["sample"]["target_generation"] = 2
    yield changed
    yield from begin()
    yield event(3, flags=11 | 256)  # no-op never borrows a drive end
    yield event(4, flags=55)
    yield event(5)
    yield from begin()
    sequence += 1  # intentional transport loss
    yield event(3, flags=11)
    yield event(4, flags=55)
    yield event(5)
    yield from begin()
    yield event(4, flags=55)
    yield event(3, flags=11, error_code=1)
    yield event(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open("x") as stream:
            for row in events():
                stream.write(json.dumps(row) + "\n")
    except FileExistsError:
        parser.error("output exists; refusing overwrite")
