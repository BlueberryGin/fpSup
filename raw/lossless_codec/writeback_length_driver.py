#!/usr/bin/env python3
"""Arm, read and restore the write-back / shorten-the-node probe.

THE QUESTION

The allocator gives 16 MiB in total and one SD aggregation batch is 64 MiB
(both measured live 2026-09-29), so a pool of our own buffers cannot be one
batch deep. Putting the compressed frame back into the camera's own buffer
costs no pool -- the write path already owns that buffer and it already
survives to the flush. Two things must hold, and neither has been tested at
the flush site:

  1. writing into the camera's own frame buffer must not freeze the camera
  2. the writer must honour a SHORTER node length

THE MODES

  2  watermark the camera's buffer, leave the length alone.  RUN THIS FIRST:
     the mark lands in the trailing padding, the clip stays valid, and only
     the risky half is under test.
  1  shorten the node, do not touch the buffer.
  3  both, the real operation.

Modes 1 and 3 truncate every altered file. The clip will not be playable and
that is the expected result, not a failure.

NOTHING IS ALLOCATED. No codec runs, no block is needed, and the arena cost is
zero: both claims are borrowed from the codec chain at its own fixed sizes,
which is idempotent.
"""
import argparse
import hashlib
import json
import pathlib
import struct
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import exact_dng_writer_probe as base
import exact_flush_writer_probe as flush
import single_frame_codec_probe as codec

SOURCE = pathlib.Path(__file__).with_name("writeback_length_probe.S")
PROOF_MAGIC = 0x334C534D                 # "MSL3"

S_COUNT, S_WRITER, S_FRAME, S_LR = 0, 1, 2, 3
S_RESTORED, S_ERROR, S_STAGE, S_DONE = 4, 5, 6, 7
S_NODE_BUF, S_NODE_LEN, S_LEN_AFTER, S_SHAPE_BAD = 8, 9, 10, 11
S_MODE, S_PROOF = 12, 13
STATE_WORDS = S_PROOF + 1

ERRORS = {0: None, 2: "the writer's shape was refused"}

DNG_LEN = 0x318200
SHORT_LEN = 0x155000            # G4b's real compressed CinemaDNG size
FRAMES = 8

CLAIMS = {"code": codec.CLAIM_NAMES["code"], "state": codec.CLAIM_NAMES["state"]}
CODE_CLAIM = codec.CODE_CLAIM_BYTES
MODE = 2


def mark_offset(mode):
    """Where the watermark goes, which depends on whether the file is cut."""
    return (DNG_LEN - 16) if mode == 2 else (SHORT_LEN - 16)


def expected_length(mode):
    return DNG_LEN if mode == 2 else SHORT_LEN


def configure():
    flush.SOURCE = SOURCE
    flush.CLAIM_NAMES = CLAIMS
    flush.STATE_WORDS = STATE_WORDS
    return flush


def place():
    import probe_placement as placement
    configure()
    placed = placement.claim_regions(
        [(CLAIMS["code"], CODE_CLAIM),
         (CLAIMS["state"], codec.STATE_WORDS * 4)])
    flush.CODE = placed[CLAIMS["code"]]
    flush.STATE = placed[CLAIMS["state"]]
    flush.CODE_LIMIT = flush.CODE + CODE_CLAIM
    flush.STATE_LIMIT = flush.STATE + codec.STATE_WORDS * 4
    flush.HOOK_ARMED = placement.armed_word(flush.HOOK_SITE, flush.CODE)
    return placed


def build_with_armed(fpsup, placed, armed, mode):
    """Prove HOOK_ARMED and MODE both reach the assembler.

    An unpassed HOOK_ARMED bakes in 0xEB000000, a BL with a zero offset, which
    targets site+8 -- that cost a clip's writes on 2026-09-29. An unpassed MODE
    silently runs a different experiment from the one that was asked for, which
    is worse, because the result would be believed.
    """
    assemble, symbols = base.load_assembler(fpsup)
    defines = (f"STATE=0x{placed[CLAIMS['state']]:08X}",
               f"HOOK_ARMED=0x{armed:08X}",
               f"FRAMES={FRAMES}",
               f"MODE={mode}")
    code = assemble(SOURCE, defines=defines)
    if code == assemble(SOURCE, defines=(defines[0], "HOOK_ARMED=0xDEADBEEF")
                                        + defines[2:]):
        raise flush.ProbeError("HOOK_ARMED is not reaching the assembler")
    other_mode = 1 if mode != 1 else 2
    if code == assemble(SOURCE, defines=defines[:3] + (f"MODE={other_mode}",)):
        raise flush.ProbeError(
            f"MODE is not reaching the assembler: {mode} and {other_mode} "
            "produced identical images, so the run would not be the "
            "experiment it reports")
    if symbols(SOURCE, defines=defines).get("probe_entry") != 0:
        raise flush.ProbeError("probe_entry is not at offset zero")
    return code


def take_over_shared_region(shell, code_words):
    """Both hook sites stock, then clear the borrowed claim, in 64-word chunks.

    The property that matters is that nothing is executing out of this claim,
    which the two stock words establish; the bytes being zero is a consequence
    we then create. One mem get of the whole claim is 448 lines of shell text
    and times the transport out.
    """
    for name, site, orig in (("flush", flush.HOOK_SITE, flush.HOOK_ORIG),
                             ("cinemadng", base.HOOK_SITE, base.HOOK_ORIG)):
        present = shell.read_words(site, 1)[0]
        if present != orig:
            raise flush.ProbeError(
                f"the {name} site 0x{site:08X} reads 0x{present:08X}, not its "
                f"stock 0x{orig:08X}: restore it before borrowing this claim")
    words, at = CODE_CLAIM // 4, 0
    while at < words:
        n = min(64, words - at)
        shell.write_words_verified(flush.CODE + at * 4, [0] * n)
        at += n
    left, at = [], 0
    while at < words:
        n = min(64, words - at)
        left += [(at + i, v)
                 for i, v in enumerate(shell.read_words(flush.CODE + at * 4, n))
                 if v]
        at += n
    if left:
        index, value = left[0]
        raise flush.ProbeError(
            f"the borrowed region did not clear: "
            f"0x{flush.CODE + index * 4:08X}=0x{value:08X}")


def arm(shell, fpsup, mode):
    placed = place()
    code = build_with_armed(fpsup, placed, flush.HOOK_ARMED, mode)
    print(f"claims        code 0x{flush.CODE:08X} ({CODE_CLAIM} B, borrowed "
          f"{CLAIMS['code']}), state 0x{flush.STATE:08X}")
    print(f"probe         {len(code)} bytes at 0x{flush.CODE:08X}")
    print(f"site          0x{flush.HOOK_SITE:08X}: "
          f"0x{flush.HOOK_ORIG:08X} -> 0x{flush.HOOK_ARMED:08X}")
    print(f"bound         {FRAMES} frames altered, {FRAMES * 3} hook entries")
    print("mode          " + {
        1: f"SHORTEN ONLY: node length {DNG_LEN} -> {SHORT_LEN}, the camera's "
           "buffer untouched. Every altered file is TRUNCATED and the clip "
           "will not be playable",
        2: "WRITE ONLY: a mark in the camera's own buffer at "
           f"0x{mark_offset(2):X}, in the trailing padding, length untouched. "
           "The clip stays valid",
        3: "BOTH, the real operation: mark at "
           f"0x{mark_offset(3):X} and length -> {SHORT_LEN}. Every altered "
           "file is TRUNCATED",
    }[mode])

    flush.verify_call_context(shell)
    take_over_shared_region(shell, len(code) // 4)
    shell.write_words_verified(flush.CODE, base.words_from(code))
    shell.write_words_verified(flush.STATE, [0] * STATE_WORDS)
    shell.write_words_verified(flush.STATE + S_PROOF * 4, [PROOF_MAGIC])

    import probe_placement as placement
    for item in placement.publish(codec.native_caller()):
        print(f"published {item['function']} -> {item['result']}")

    flush.verify_call_context(shell)
    armed_ok = flush.arm_site(shell)
    present = shell.read_words(flush.HOOK_SITE, 1)[0]
    if armed_ok and present != flush.HOOK_ARMED:
        shell.write_word_verified(flush.HOOK_SITE, flush.HOOK_ORIG)
        raise flush.ProbeError(
            f"the site reads 0x{present:08X} after arming, not "
            f"0x{flush.HOOK_ARMED:08X}; restored to stock, nothing is armed")
    print(f"site now      0x{present:08X}")
    print("armed." if armed_ok else "it fired during arming; run: status")


def status(shell, fpsup):
    place()
    v = shell.read_words(flush.STATE, STATE_WORDS)
    site = shell.read_words(flush.HOOK_SITE, 1)[0]
    if v[S_PROOF] != PROOF_MAGIC:
        print(json.dumps({"seeded": False,
                          "note": "no MSL3 proof; the probe refused every "
                                  "frame"}, indent=2))
        return
    out = {
        "hook": f"0x{site:08X}",
        "hook_state": ("restored" if site == flush.HOOK_ORIG
                       else "ARMED" if site == flush.HOOK_ARMED else "unknown"),
        "mode": v[S_MODE],
        "entries": v[S_COUNT],
        "frames_altered": v[S_DONE],
        "shape_refused": v[S_SHAPE_BAD],
        "error": ERRORS.get(v[S_ERROR], v[S_ERROR]),
        "stage": v[S_STAGE],
        "camera_buffer": f"0x{v[S_NODE_BUF]:08X}",
        "node_length_before": v[S_NODE_LEN],
        "node_length_readback": v[S_LEN_AFTER],
        "restored_readback": f"0x{v[S_RESTORED]:08X}",
    }
    print(json.dumps(out, indent=2))
    print("\n" + state_verdict(v), file=sys.stderr)


def state_verdict(v) -> str:
    """What the state block alone can say. The card says the rest."""
    mode = v[S_MODE]
    if v[S_DONE] == 0:
        return ("NOTHING ALTERED. Read shape_refused and error: a shape "
                "refusal means the flush did not look the way G3 measured it, "
                "and no conclusion about freezing follows from this run.")
    said = (f"THE CAMERA SURVIVED {v[S_DONE]} altered frame(s) and the hook "
            "restored itself, so writing into the camera's own buffer at the "
            "flush site does not freeze the way copy-back did at 0xC0722AFC.")
    if mode != 2:
        if v[S_LEN_AFTER] == SHORT_LEN:
            said += (f" The node read back as {SHORT_LEN} after the store, so "
                     "the shortened length reached the structure. Whether the "
                     "WRITER honoured it is a question only the file size on "
                     "the card answers.")
        else:
            said += (f" But the node read back as {v[S_LEN_AFTER]}, not "
                     f"{SHORT_LEN}: the store did not stick, and nothing about "
                     "shortening is established.")
    return said


def card(fpsup, clip, date, frames, mode):
    """The file sizes and marks on the card. The other half of the answer."""
    import subprocess
    import tempfile
    want = expected_length(mode)
    at = mark_offset(mode)
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for index in range(1, frames + 1):
            name = f"{clip}_{date}_{index:06d}.DNG"
            local = pathlib.Path(tmp) / name
            subprocess.run(
                [sys.executable, "getfile.py",
                 f"\\CINEMA\\{clip}\\{name}", str(local), "--size", str(want)],
                cwd=fpsup / "fp_usb_shell", capture_output=True, text=True)
            if not local.exists():
                rows.append({"frame": index, "error": "not read"})
                continue
            blob = local.read_bytes()
            row = {"frame": index, "bytes": len(blob),
                   "sha256": hashlib.sha256(blob).hexdigest()[:16]}
            if len(blob) >= at + 8:
                magic, seq = struct.unpack_from("<II", blob, at)
                row["ours"] = magic == PROOF_MAGIC
                row["sequence"] = seq if magic == PROOF_MAGIC else None
            rows.append(row)
    print(json.dumps(rows, indent=2))
    print("\n" + card_verdict(rows, mode), file=sys.stderr)
    return rows


def card_verdict(rows, mode) -> str:
    want = expected_length(mode)
    ours = [r for r in rows if r.get("ours")]
    if not ours:
        return ("NO VERDICT: no file carries the mark. Check the clip name and "
                "the date before concluding anything.")
    sized = [r for r in ours if r["bytes"] == want]
    if mode == 2:
        return (f"{len(ours)} file(s) carry the mark at full length, so a CPU "
                "write into the camera's own frame buffer reaches the card. "
                "The buffer the write path owns is writable by us, which is "
                "what removes the need for a pool.")
    if len(sized) == len(ours):
        return (f"THE WRITER HONOURS A SHORTER NODE: all {len(ours)} altered "
                f"files are exactly {want} bytes and carry the mark at their "
                "end. Putting a compressed frame back into the camera's own "
                "buffer is viable, and it costs no pool.")
    return (f"THE LENGTH DID NOT TAKE: {len(sized)} of {len(ours)} files are "
            f"{want} bytes. The node accepted the store but the writer used "
            "something else, so the compressed payload would need its size "
            "carried somewhere this probe has not found.")


def restore(shell, fpsup):
    place()
    flush.restore_probe(shell)


def main():
    global MODE
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("arm", "status", "restore", "build",
                                       "card"))
    ap.add_argument("--mode", type=int, choices=(1, 2, 3), default=2,
                    help="2 = write only (safe, run first), 1 = shorten only, "
                         "3 = both")
    ap.add_argument("--clip")
    ap.add_argument("--date")
    ap.add_argument("--frames", type=int, default=FRAMES)
    ap.add_argument("--fpsup", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parents[2])
    ap.add_argument("--fpsh", type=pathlib.Path, default=None)
    args = ap.parse_args()
    fpsup = args.fpsup.resolve()
    MODE = args.mode
    if args.action == "build":
        configure()
        assemble, _ = base.load_assembler(fpsup)
        code = assemble(SOURCE, defines=("STATE=0xC072F700",
                                         "HOOK_ARMED=0xEB00333F",
                                         f"FRAMES={FRAMES}",
                                         f"MODE={args.mode}"))
        print(f"{len(code)} bytes; nothing written, no camera touched")
        return 0
    if args.action == "card":
        if not args.clip or not args.date:
            ap.error("card needs --clip and --date")
        card(fpsup, args.clip, args.date, args.frames, args.mode)
        return 0
    fpsh = args.fpsh.resolve() if args.fpsh else fpsup / "fp_usb_shell" / "host" / "fpsh"
    shell = base.CameraShell(fpsh)
    if args.action == "arm":
        arm(shell, fpsup, args.mode)
    else:
        {"status": status, "restore": restore}[args.action](shell, fpsup)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (base.ProbeError, flush.ProbeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
