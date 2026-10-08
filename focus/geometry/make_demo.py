#!/usr/bin/env python3
"""Create labeled SYNTHETIC geometry events. This is not a camera capture."""
import argparse
import json
from pathlib import Path


def events():
    yield dict(type="metadata", schema="fp.geometry.events", version=1,
               provenance="synthetic", clock="common_epoch_ms",
               scale_semantics="normalized_linear_size",
               note="Generated reciprocal-size motion and explicit assumed optical confirmations; no camera data.")
    seq = 0

    def sample(t, relative_depth=1.0, **changes):
        nonlocal seq
        seq += 1
        data = dict(now_ms=t, sample_ms=t, source_seq=seq, target_generation=1,
                    context_generation=1, scale_generation=1, source_kind=1,
                    flags=31, scale_q16=round(0.25 * 65536 / relative_depth),
                    lens_position=10000)
        data.update(changes)
        return data

    for t in range(0, 240, 40):
        yield dict(type="sample", sample=sample(t), label="stationary",
                   note="No optical confirmation: must stay unknown.")
    yield dict(type="focus_confirmed", sample=sample(240), focus_event_seq=1,
               confirmation_flags=3, note="Synthetic optical confirmation, not a motor settled event.")
    for t in range(280, 640, 40):
        yield dict(type="sample", sample=sample(t), label="stationary")
    for t in range(640, 1440, 40):
        depth = 1.0 - 0.20 * (t - 600) / 1000
        yield dict(type="sample", sample=sample(t, depth), label="approach")
    for t in range(1440, 2240, 40):
        depth = 0.84 + 0.25 * (t - 1400) / 1000
        yield dict(type="sample", sample=sample(t, depth), label="recede")
    yield dict(type="sample", sample=sample(2240, flags=15), label="unknown",
               note="Lens moving: geometry is withheld until a new stationary window.")
    for t in range(2280, 2560, 40):
        yield dict(type="sample", sample=sample(t, 1.05), label="stationary")
    yield dict(type="sample", sample=sample(2600, target_generation=2), label="unknown",
               note="New subject invalidates first-focus anchor.")
    yield dict(type="sample", sample=sample(2640, target_generation=2), label="unknown")
    yield dict(type="focus_confirmed", sample=sample(2680, target_generation=2),
               focus_event_seq=2, confirmation_flags=3)
    for t in range(2720, 3080, 40):
        yield dict(type="sample", sample=sample(t, target_generation=2), label="stationary")
    yield dict(type="invalidate", reason=16, note="Explicit stop drops anchor.")
    yield dict(type="sample", sample=sample(3120, target_generation=2), label="unknown")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open("x") as out:
            for event in events():
                out.write(json.dumps(event) + "\n")
    except FileExistsError:
        parser.error("output exists; refusing overwrite")
    print(str(args.output.resolve()))
