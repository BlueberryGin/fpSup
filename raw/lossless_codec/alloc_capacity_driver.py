#!/usr/bin/env python3
"""Ask the allocator for a block of each size and report what it gives.

WHY

The SD aggregation batch was measured live on 2026-09-29 at 64 MiB
(ledger-live-aggregation-batch-measured-20260929.json). A buffer handed to the
CinemaDNG writer is read when the batch holding it flushes, so an asynchronous
design's pool must be one batch deep: 64 MiB. Two 4 MiB blocks are the most
this project has obtained. This asks, directly, whether more is available.

WHAT IT COSTS

Nothing is recorded, no hook is armed and no frame is touched. The probe
allocates and frees inside one call and retains nothing, so a refusal and a
success leave the camera in the same state.

THE ARENA

There is no free arena after a full codec chain, so this borrows
lossless.codec.code at the codec chain's OWN fixed size and lossless.codec.state
likewise. Same name, same size, same address: claiming is idempotent and costs
nothing. It overwrites whatever image is in that claim, which is why it refuses
to run unless both hook sites read their stock word.
"""
import argparse
import json
import pathlib
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import exact_dng_writer_probe as base
import exact_flush_writer_probe as flush
import single_frame_codec_probe as codec

SOURCE = pathlib.Path(__file__).with_name("alloc_capacity_probe.S")

# Every size worth knowing, smallest first: the known-good 4 MiB anchors the
# run, and a refusal at 8 says as much as a success at 64.
SIZES = (0x00400000, 0x00800000, 0x01000000, 0x02000000, 0x04000000)

CLAIMS = {"code": codec.CLAIM_NAMES["code"], "state": codec.CLAIM_NAMES["state"]}
CODE_CLAIM = codec.CODE_CLAIM_BYTES
MAX_BLOCKS = 8            # the probe's own table bound
H_COUNT, H_ALLOC, H_FREE_RET, H_SIZE, H_WANT = 0, 1, 2, 3, 4
H_TABLE = 8               # word index of the first handle
RESULT_WORDS = H_TABLE + MAX_BLOCKS


class Refused(Exception):
    pass


def place():
    import probe_placement as placement
    placed = placement.claim_regions(
        [(CLAIMS["code"], CODE_CLAIM),
         (CLAIMS["state"], codec.STATE_WORDS * 4)])
    return placed[CLAIMS["code"]], placed[CLAIMS["state"]]


def require_nothing_armed(shell):
    """Both hook sites stock, or this does not overwrite anything.

    The claim being borrowed holds the codec chain's image. Overwriting it is
    safe only while nothing can branch into it.
    """
    for name, site, orig in (("flush", flush.HOOK_SITE, flush.HOOK_ORIG),
                             ("cinemadng", base.HOOK_SITE, base.HOOK_ORIG)):
        present = shell.read_words(site, 1)[0]
        if present != orig:
            raise Refused(
                f"the {name} site 0x{site:08X} reads 0x{present:08X}, not its "
                f"stock 0x{orig:08X}. Restore it before borrowing this claim")


def install(shell, fpsup, code_at, state_at):
    assemble, symbols = base.load_assembler(fpsup)
    defines = (f"SCRATCH=0x{state_at:08X}",)
    image = assemble(SOURCE, defines=defines)
    other = assemble(SOURCE, defines=("SCRATCH=0xDEADBE00",))
    if image == other:
        raise Refused("SCRATCH is not reaching the assembler: two different "
                      "values produced identical images, so the probe would "
                      "write its result wherever its default points")
    if symbols(SOURCE, defines=defines).get("probe_entry") != 0:
        raise Refused("probe_entry is not at offset zero")
    if len(image) > CODE_CLAIM:
        raise Refused(f"the probe is {len(image)} bytes, above the borrowed "
                      f"{CODE_CLAIM}-byte claim")
    shell.write_words_verified(code_at, base.words_from(image))
    return len(image)


def ask(shell, call, code_at, state_at, size, count=1):
    """Hold `count` blocks of `size` AT ONCE, then give them all back."""
    shell.write_words_verified(state_at, [0] * RESULT_WORDS)
    ok, returned = call(code_at, size, 0, count)
    v = shell.read_words(state_at, RESULT_WORDS)
    if v[H_SIZE] != size or v[H_WANT] != min(count, MAX_BLOCKS):
        raise Refused(
            f"the probe echoed size 0x{v[H_SIZE]:08X} count {v[H_WANT]} where "
            f"0x{size:08X} x {count} was asked for: the result area is not the "
            "one it wrote")
    got = v[H_COUNT]
    handles = [v[H_TABLE + i] for i in range(min(got, MAX_BLOCKS))]
    free_ret = (v[H_FREE_RET] - (1 << 32)) if v[H_FREE_RET] >> 31 else v[H_FREE_RET]
    if got and free_ret != 0:
        raise Refused(
            f"{got} block(s) of 0x{size:08X} were allocated and the free "
            f"returned {free_ret}. Nothing else may run until that is resolved")
    return {
        "bytes": size,
        "MiB": size // (1 << 20),
        "asked_for": count,
        "granted_count": got,
        "granted": got >= count,
        "total_MiB": got * size // (1 << 20),
        "handles": [f"0x{h:08X}" for h in handles],
        "allocator": f"0x{v[H_ALLOC]:08X}",
        "freed": free_ret == 0,
        "free_return": free_ret,
        "call_ok": bool(ok),
        "call_returned": returned,
    }


def verdict(rows):
    # "granted" means the whole request was met, which is the wrong question
    # here: one block of eight is still 16 MiB obtained, and reading that as
    # NOTHING GRANTED is how this first reported an all-refusal that had not
    # happened. The verdict is about the largest TOTAL actually held.
    granted = [r for r in rows if r.get("granted_count")]
    if not granted:
        return ("NOTHING GRANTED AT ALL, not even 4 MiB, which this project "
                "has obtained many times. Suspect the borrowed claim or the "
                "probe, not the allocator.")
    biggest = max(r["total_MiB"] for r in granted)
    unfreed = [r for r in granted if not r["freed"]]
    note = ("" if not unfreed else
            f"  WARNING: {len(unfreed)} block(s) were not freed.")
    if biggest >= 64:
        return ("64 MiB IS AVAILABLE. The asynchronous design's pool fits in "
                "one allocation, so the batch lag needs no copy-back and no "
                "synchronous encode." + note)
    return (f"THE LARGEST GRANTED IS {biggest} MiB, short of the 64 MiB one "
            "aggregation batch needs. Either several blocks must be combined "
            "-- they are not contiguous, so the pool would be a list -- or the "
            "compressed frame goes back into the camera's own buffer, which "
            "costs one scratch block and forces a synchronous encode." + note)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fpsup", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parents[2])
    ap.add_argument("--fpsh", type=pathlib.Path, default=None)
    ap.add_argument("--sizes", help="comma-separated MiB, default "
                                    + ",".join(str(s >> 20) for s in SIZES))
    ap.add_argument("--count", type=int, default=1,
                    help="how many blocks of each size to hold AT ONCE; the "
                         "pool may be a list, so this is the question that "
                         "matters once one block is known to be capped")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    fpsup = args.fpsup.resolve()
    sizes = ([int(s) << 20 for s in args.sizes.split(",")] if args.sizes
             else list(SIZES))

    if args.dry_run:
        assemble, _ = base.load_assembler(fpsup)
        image = assemble(SOURCE, defines=("SCRATCH=0xC072F700",))
        print(f"{len(image)} bytes; nothing written, no camera touched")
        return 0

    fpsh = args.fpsh.resolve() if args.fpsh else fpsup / "fp_usb_shell" / "host" / "fpsh"
    shell = base.CameraShell(fpsh)
    code_at, state_at = place()
    require_nothing_armed(shell)
    size = install(shell, fpsup, code_at, state_at)
    print(f"probe   {size} bytes at 0x{code_at:08X} (borrowed "
          f"{CLAIMS['code']}), result at 0x{state_at:08X}")

    sys.path.insert(0, str(fpsup / "fp_usb_shell"))
    import callfn
    rows = []
    for want in sizes:
        row = ask(shell, callfn.call, code_at, state_at, want, args.count)
        rows.append(row)
        print(f"  {row['MiB']:3d} MiB x {row['asked_for']}  ->  "
              f"{row['granted_count']} granted, {row['total_MiB']} MiB total"
              + ("" if row["freed"] else "   NOT FREED"))
    print(json.dumps(rows, indent=2))
    print("\n" + verdict(rows), file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Refused, base.ProbeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
