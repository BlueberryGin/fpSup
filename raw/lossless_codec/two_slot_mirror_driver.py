#!/usr/bin/env python3
"""Arm, read and restore the two-slot mirror probe.

WHAT THIS IS FOR

G5 (2026-09-23) wrote eight consecutive compressed frames from ONE buffer and
the eight files on the card came out byte-identical -- all the last frame's
content. Its own ledger says the run could not distinguish a late per-frame DMA
from a batched write at clip close, and that distinction decides the whole
architecture: with a late per-frame DMA we need N buffers and a release signal,
and with a batched write at clip close nothing of this shape can work at all.

two_slot_mirror_probe.S is the experiment. Two slots, the codec removed, each
frame copied byte for byte into a free slot and watermarked, and a slot returned
to FREE only when the frame it was handed reaches state 0x12 in frame[0x1104].
If 0x12 arrives too early the files on the card are corrupt; if it never
arrives the run stops after two frames with both slots HELD. Both are legible.

THE ARENA, AND WHY THIS SHARES THE CODEC CHAIN'S CODE CLAIM

Two camera-allocated 4 MiB blocks are wanted, and getting them means the codec
chain's preflight, scratch and acquire-second, which hold
lossless.codec.code (1,792 B) and lossless.codec.state (256 B). Adding a code
claim of our own on top of those, putfile's 768 B and the codec log's 256 B
overruns the 3,920-byte arena.

So this shares lossless.codec.code -- at the codec chain's OWN fixed size, not
at len(code). That is the whole trick: a bump allocator cannot grow a claim in
place, and on 2026-09-29 sharing that name at a SMALLER size resized it and the
codec chain then could not get its 1,792 bytes back for the rest of the boot.
Claiming the same name at the same fixed size is idempotent and costs nothing.
The 748-byte mirror image simply sits inside the 1,792-byte claim.

The consequence is honest and already true of the chain: only one of the two
images can be installed at a time. Acquire the blocks first, then install this.
Afterwards the codec image has to be placed again before `release` can run.

The state block is NOT shared: this probe's layout is its own, and the codec
chain's state still holds the block handle that `release` needs.
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

SOURCE = pathlib.Path(__file__).with_name("two_slot_mirror_probe.S")

PROOF_MAGIC = 0x324C534D                 # "MSL2", the probe's own gate

# State words, mirroring the probe's .equ block. Byte offsets / 4.
S_COUNT, S_WRITER, S_FRAME, S_LR = 0, 1, 2, 3
S_RESTORED, S_ERROR, S_STAGE, S_FRAMES = 4, 5, 6, 7
S_REFUSED, S_RELEASED, S_SLOT_A, S_SLOT_B = 8, 9, 10, 11
S_NODE_BUF, S_NODE_LEN, S_LAST_SEEN, S_SHAPE_BAD = 12, 13, 14, 15
S_PROOF = 16

# This probe's OWN state size, not the reused module's 64 words. The flush
# machinery sizes its state for a different probe, and claiming 256 bytes for a
# 68-byte layout is what would not fit: after the codec chain, acquire-second
# and the memory-read tools the arena had 72 bytes left. Claim what the .equ
# block actually uses -- S_PROOF is the last word -- and size every read, clear
# and check to match.
STATE_WORDS = S_PROOF + 1

ERRORS = {0: None, 1: "no slot base seeded", 2: "the writer's shape was refused"}

# Slot records, inside slot 0's block. Two of eight words, stride 0x20.
SLOT_OFF = 0x3F6000
SLOT_STRIDE = 0x20
SLOT_WORDS = SLOT_STRIDE // 4
L_STATE, L_FRAME, L_SEQ, L_BASE = 0, 1, 2, 3
L_HELD_TICK, L_FREE_TICK, L_LAST_STATE, L_LEN = 4, 5, 6, 7

DNG_LEN = 0x318200
MARK_OFF = DNG_LEN - 16
FRAMES = 8
ROUND_ROBIN = False     # set by --round-robin; changes the probe, not just a print

# Shared, at the codec chain's own size. Never len(code): see the docstring.
CLAIMS = {"code": codec.CLAIM_NAMES["code"], "state": "lossless.mirror.state"}
CODE_CLAIM = codec.CODE_CLAIM_BYTES


def configure():
    """Point the reused flush machinery at this probe's source, claims and size.

    STATE_WORDS is a module global in exact_flush_writer_probe, and every
    helper that clears, checks or reads the state block uses it. Setting it
    here is what keeps a 68-byte claim from being written with 256 bytes of
    zeroes -- which would run off the end of the arena.
    """
    flush.SOURCE = SOURCE
    flush.CLAIM_NAMES = CLAIMS
    flush.STATE_WORDS = STATE_WORDS
    return flush


def place(fpsup, claim=None):
    """Claim at the codec chain's fixed size and bake the addresses in."""
    import probe_placement as placement
    configure()
    sized = flush.build_probe(fpsup)
    if len(sized) > CODE_CLAIM:
        raise flush.ProbeError(
            f"the probe is {len(sized)} bytes, above the shared "
            f"{CODE_CLAIM}-byte claim")
    placed = placement.claim_regions(
        [(CLAIMS["code"], CODE_CLAIM),
         (CLAIMS["state"], STATE_WORDS * 4)], claim=claim)
    flush.CODE = placed[CLAIMS["code"]]
    flush.STATE = placed[CLAIMS["state"]]
    flush.CODE_LIMIT = flush.CODE + CODE_CLAIM
    flush.STATE_LIMIT = flush.STATE + STATE_WORDS * 4
    flush.HOOK_ARMED = placement.armed_word(flush.HOOK_SITE, flush.CODE)
    return placed


def build_with_armed(fpsup, placed, armed):
    """Build with HOOK_ARMED actually passed, and prove that it was.

    This probe re-arms itself, so an unpassed define bakes in the sentinel
    default 0xEB000000 -- a BL with a zero offset, which targets site+8. That
    cost a whole clip's writes on 2026-09-29. Assembling twice with two
    different values and requiring the images to differ is the check.
    """
    assemble, symbols = base.load_assembler(fpsup)
    defines = [f"STATE=0x{placed[CLAIMS['state']]:08X}",
               f"HOOK_ARMED=0x{armed:08X}",
               f"FRAMES={FRAMES}"]
    if ROUND_ROBIN:
        defines.append("ROUND_ROBIN=1")
    defines = tuple(defines)
    code = assemble(SOURCE, defines=defines)
    other = assemble(SOURCE, defines=(defines[0], "HOOK_ARMED=0xDEADBEEF")
                                     + defines[2:])
    if code == other:
        raise flush.ProbeError(
            "HOOK_ARMED is not reaching the assembler: two different values "
            "produced identical images. Refusing to arm with a word this probe "
            "would then write into the hook site itself")
    if symbols(SOURCE, defines=defines).get("probe_entry") != 0:
        raise flush.ProbeError("probe_entry is not at offset zero")
    return code


def both_handles(shell):
    """Recover both block bases from the camera's own records.

    Not arguments: a hand-typed handle is how a 4 MiB DMA lands somewhere it
    was never given. The codec chain's state names block A, the record inside
    block A names block B, and check_life_record refuses either unless the
    block itself carries the mark that only the camera could have written.
    """
    # codec.STATE is a module global that only the codec driver's own place()
    # sets; read here without resolving it and you read base.STATE, which is
    # not where the chain put anything. Resolving by NAME is idempotent -- the
    # same name at the same size returns the same address -- so this costs no
    # arena and cannot move the codec chain's claim.
    import probe_placement as placement
    codec_state = placement.claim_regions(
        [(codec.CLAIM_NAMES["state"], codec.STATE_WORDS * 4)]
    )[codec.CLAIM_NAMES["state"]]
    state = shell.read_words(codec_state, codec.STATE_WORDS)
    handle_a = state[codec.S_HANDLE]
    if not handle_a:
        raise flush.ProbeError(
            "the codec chain's state holds no block A handle; run preflight, "
            "then scratch, then acquire-second before installing this probe")
    life = codec.read_lifecycle(shell, handle_a)
    codec.check_life_record(life["block_a"], life["mark_a"], handle_a, 1)
    handle_b = life["block_b"]["handle"]
    codec.check_life_record(life["block_b"], life.get("mark_b", {}), handle_b, 2)
    if not life.get("disjoint"):
        raise flush.ProbeError(
            f"blocks 0x{handle_a:08X} and 0x{handle_b:08X} are not disjoint")
    return handle_a, handle_b


def seed(shell, handle_a, handle_b):
    """Zero the state, stamp the proof, and write the two slot records.

    The records live in block A at +0x3F6000 -- the same offset the async phase
    uses for its own record, which is safe only because the two never run in
    one clip. Everything but L_BASE starts at zero: the probe owns every other
    field and a leftover HELD would make it refuse both slots forever.
    """
    shell.write_words_verified(flush.STATE, [0] * STATE_WORDS)
    shell.write_words_verified(flush.STATE + S_SLOT_A * 4, [handle_a, handle_b])
    shell.write_words_verified(flush.STATE + S_PROOF * 4, [PROOF_MAGIC])
    at = handle_a + SLOT_OFF
    for index, base_addr in enumerate((handle_a, handle_b)):
        words = [0] * SLOT_WORDS
        words[L_BASE] = base_addr
        shell.write_words_verified(at + index * SLOT_STRIDE, words)
    return at


def read_slots(shell, handle_a):
    at = handle_a + SLOT_OFF
    out = []
    for index in range(2):
        w = shell.read_words(at + index * SLOT_STRIDE, SLOT_WORDS)
        held = w[L_STATE] == 1
        out.append({
            "slot": index,
            "base": f"0x{w[L_BASE]:08X}",
            "state": "HELD" if held else "FREE" if w[L_STATE] == 0 else w[L_STATE],
            "frame": f"0x{w[L_FRAME]:08X}",
            "sequence": w[L_SEQ],
            "held_tick": w[L_HELD_TICK],
            "free_tick": w[L_FREE_TICK],
            "hold_ticks": (w[L_FREE_TICK] - w[L_HELD_TICK]) & 0xFFFFFFFF
                          if w[L_FREE_TICK] else None,
            "last_frame_state": w[L_LAST_STATE],
            "length": w[L_LEN],
        })
    return out


CHUNK_WORDS = 64        # one mem get of 448 words times the shell out


def read_chunked(shell, address, words):
    out = []
    while len(out) < words:
        n = min(CHUNK_WORDS, words - len(out))
        out.extend(shell.read_words(address + len(out) * 4, n))
    return out


def take_over_shared_region(shell, code_bytes):
    """Make the shared code claim ours, having shown nothing is running in it.

    flush.require_clear_install_regions demands the region read as zero, which
    is the right check for a claim of one's own and the wrong one here: this
    claim is the codec chain's, deliberately shared at the same fixed size
    because the arena cannot hold a second one. The property that actually
    matters is that nothing is executing out of it, and that is established by
    both hook sites reading their stock word -- not by the bytes being zero.

    So: prove both sites are restored, clear the region ourselves, and prove
    the clear took. Then the standard check passes on its own terms.
    """
    for name, site, orig in (("flush", flush.HOOK_SITE, flush.HOOK_ORIG),
                             ("cinemadng", base.HOOK_SITE, base.HOOK_ORIG)):
        present = shell.read_words(site, 1)[0]
        if present != orig:
            raise flush.ProbeError(
                f"the {name} site 0x{site:08X} reads 0x{present:08X}, not its "
                f"stock 0x{orig:08X}: something is armed, so this region may "
                "be executing. Restore it before installing over it")
    words = CODE_CLAIM // 4
    shell.write_words_verified(flush.CODE, [0] * CHUNK_WORDS)
    at = CHUNK_WORDS
    while at < words:
        n = min(CHUNK_WORDS, words - at)
        shell.write_words_verified(flush.CODE + at * 4, [0] * n)
        at += n
    left = [(i, v) for i, v in enumerate(read_chunked(shell, flush.CODE, words))
            if v]
    if left:
        index, value = left[0]
        raise flush.ProbeError(
            f"the shared region did not clear: 0x{flush.CODE + index * 4:08X}="
            f"0x{value:08X} ({len(left)} non-zero words)")
    # The state claim is ours alone and brand new, but check it anyway: this is
    # what require_clear_install_regions would have checked, and it is cheap.
    present = read_chunked(shell, flush.STATE, STATE_WORDS)
    busy = [(i, v) for i, v in enumerate(present) if v]
    if busy and present[S_PROOF] != PROOF_MAGIC:
        index, value = busy[0]
        raise flush.ProbeError(
            f"the state claim is occupied by something that is not this "
            f"probe: 0x{flush.STATE + index * 4:08X}=0x{value:08X} and there "
            f"is no MSL2 proof at 0x{flush.STATE + S_PROOF * 4:08X}")
    if busy:
        print(f"state         holds THIS probe's previous run "
              f"({present[S_COUNT]} entries, {present[S_FRAMES]} redirected); "
              "seed clears it")
    print(f"cleared       0x{flush.CODE:08X}..0x{flush.CODE + CODE_CLAIM:08X} "
          f"({CODE_CLAIM} B, both hook sites verified stock first)")


def arm(shell, fpsup):
    placed = place(fpsup)
    handle_a, handle_b = both_handles(shell)
    code = build_with_armed(fpsup, placed, flush.HOOK_ARMED)
    print(f"claims        code 0x{flush.CODE:08X} ({CODE_CLAIM} B, shared with "
          f"{CLAIMS['code']}), state 0x{flush.STATE:08X}")
    print(f"mirror probe  {len(code)} bytes at 0x{flush.CODE:08X}")
    print(f"site          0x{flush.HOOK_SITE:08X}: "
          f"0x{flush.HOOK_ORIG:08X} -> 0x{flush.HOOK_ARMED:08X}")
    print(f"slot 0        0x{handle_a:08X}   slot 1  0x{handle_b:08X}")
    print(f"records       0x{handle_a + SLOT_OFF:08X}, stride 0x{SLOT_STRIDE:X}")
    print(f"bound         {FRAMES} redirected frames, {FRAMES * 3} hook entries")
    print("mode          " + ("ROUND ROBIN: frame N overwrites slot N mod 2 "
                              "with no release signal at all"
                              if ROUND_ROBIN else
                              "free-slot: a slot is reused only after 0x12"))

    flush.verify_call_context(shell)
    # Not flush.require_clear_install_regions: it reads the whole 1,792-byte
    # claim in ONE mem get, which is 448 lines of shell text and times the
    # transport out. take_over_shared_region does the same check in chunks,
    # over both regions, and adds the two hook sites.
    take_over_shared_region(shell, len(code))
    shell.write_words_verified(flush.CODE, base.words_from(code))
    seed(shell, handle_a, handle_b)

    import probe_placement as placement
    for item in placement.publish(codec.native_caller()):
        print(f"published {item['function']} -> {item['result']}")

    flush.verify_call_context(shell)
    armed_ok = flush.arm_site(shell)
    present = shell.read_words(flush.HOOK_SITE, 1)[0]
    if armed_ok and present != flush.HOOK_ARMED:
        shell.write_word_verified(flush.HOOK_SITE, flush.HOOK_ORIG)
        raise flush.ProbeError(
            f"the site reads 0x{present:08X} after arming, not the expected "
            f"0x{flush.HOOK_ARMED:08X}; restored to stock and nothing is armed")
    print(f"site now      0x{present:08X} (expected 0x{flush.HOOK_ARMED:08X})")
    if armed_ok:
        print(f"armed. Record ONE clip of at least {FRAMES + 2} frames, then "
              "run: status")
    else:
        print("it fired during arming; run: status")


def status(shell, fpsup):
    place(fpsup)
    v = shell.read_words(flush.STATE, STATE_WORDS)
    site = shell.read_words(flush.HOOK_SITE, 1)[0]
    if v[S_PROOF] != PROOF_MAGIC:
        print(json.dumps({"seeded": False,
                          "note": "no MSL2 proof in this state block; the "
                                  "probe would have refused every frame"},
                         indent=2))
        return
    handle_a = v[S_SLOT_A]
    out = {
        "hook": f"0x{site:08X}",
        "hook_state": ("restored" if site == flush.HOOK_ORIG
                       else "ARMED" if site == flush.HOOK_ARMED else "unknown"),
        "entries": v[S_COUNT],
        "frames_redirected": v[S_FRAMES],
        "frames_refused_no_free_slot": v[S_REFUSED],
        "releases_observed": v[S_RELEASED],
        "shape_refused": v[S_SHAPE_BAD],
        "error": ERRORS.get(v[S_ERROR], v[S_ERROR]),
        "stage": v[S_STAGE],
        "last_frame_state_seen": v[S_LAST_SEEN],
        "last_node_buffer": f"0x{v[S_NODE_BUF]:08X}",
        "last_node_length": v[S_NODE_LEN],
        "restored_readback": f"0x{v[S_RESTORED]:08X}",
        "slots": read_slots(shell, handle_a) if handle_a else [],
    }
    print(json.dumps(out, indent=2))
    print("\n" + verdict(v[S_FRAMES], v[S_RELEASED]), file=sys.stderr)


def verdict(frames: int, released: int) -> str:
    """The answer this run exists to give, in words.

    A pure function of the two numbers that matter, so it can be tested without
    a camera or a cave claim. status() prints whatever it returns; there is no
    path where the run produces numbers and no verdict.
    """
    if frames == 0:
        return ("NO VERDICT: nothing was redirected. Read shape_refused and "
                "error before concluding anything about 0x12.")
    if released > 0:
        return (f"0x12 ARRIVED, {released} times: a slot CAN be recycled on "
                "this signal, so the card consumes a buffer during the clip "
                "and the frame state word says when. Now check the card: every "
                f"file must carry MSL2 at 0x{MARK_OFF:X} with its OWN sequence, "
                "and two files sharing a sequence means the release was too "
                "early.")
    if frames >= 2:
        return ("0x12 NEVER ARRIVED. Both slots were filled and stayed HELD, "
                "so frame[0x1104] reaching 0x12 is not the release signal and "
                "the buffer lifetime is still unknown. G5's two explanations "
                "are both still open.")
    return ("INCONCLUSIVE: one frame was redirected and never released, which "
            "is what a clip too short to reach the second slot looks like. "
            f"Record at least {FRAMES + 2} frames.")


def card(fpsup, clip, date, frames):
    """Read every frame's watermark sequence off the card. The verdict.

    A slot reused before the card had read it produces a file that still
    decodes perfectly and simply holds a LATER frame's pixels, so the only
    check that catches it is the sequence the probe stamped into the trailing
    padding at the moment it filled the slot.
    """
    import subprocess
    import tempfile
    shell_dir = fpsup / "fp_usb_shell"
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for index in range(1, frames + 1):
            name = f"{clip}_{date}_{index:06d}.DNG"
            local = pathlib.Path(tmp) / name
            run = subprocess.run(
                [sys.executable, "getfile.py",
                 f"\\CINEMA\\{clip}\\{name}", str(local),
                 "--size", str(DNG_LEN)],
                cwd=shell_dir, capture_output=True, text=True)
            if not local.exists():
                out.append({"frame": index, "file": name, "error":
                            (run.stderr or run.stdout).strip()[-120:]})
                continue
            blob = local.read_bytes()
            magic, seq = struct.unpack_from("<II", blob, MARK_OFF)
            out.append({
                "frame": index,
                "ours": magic == PROOF_MAGIC,
                "sequence": seq if magic == PROOF_MAGIC else None,
                "sha256": hashlib.sha256(blob).hexdigest()[:16],
            })
    print(json.dumps(out, indent=2))
    print("\n" + card_verdict(out), file=sys.stderr)
    return out


def card_verdict(rows: list) -> str:
    """What the sequences mean, decided here rather than by eye.

    The expected sequence for the Nth redirected frame is N-1: the probe stamps
    S_FRAMES before incrementing it. A file holding a LATER sequence than its
    own is a slot that was overwritten before the card had read it.
    """
    ours = [r for r in rows if r.get("ours")]
    if not ours:
        return ("NO VERDICT: no file carries the mark. Either the clip or the "
                "date is wrong, or the probe redirected nothing.")
    wrong = [r for r in ours if r["sequence"] != r["frame"] - 1]
    seen = [r["sequence"] for r in ours]
    if not wrong:
        return (f"POOL DEEP ENOUGH: all {len(ours)} redirected files carry "
                "their own sequence, so the card finished reading each slot "
                "before the frame two later overwrote it. The card consumes a "
                "buffer DURING the clip, within two frame periods -- about "
                "33 ms at 59.94p. G5's 'batched at clip close' is dead.")
    if len(set(seen)) <= 2 and len(ours) > 2:
        return (f"SATURATED: {len(ours)} files carry only {len(set(seen))} "
                f"distinct sequences {sorted(set(seen))}, so every file shows "
                "the LAST write to its slot and this run cannot see a batch "
                "boundary. Measured on 2026-09-29: the SD path flushes a batch "
                "every 64 MiB, which is 21 frames of FHD 12-bit, so a bound "
                "below that always saturates. Raise --bound above one batch "
                "and re-read; do NOT conclude 'the card reads at clip close'.")
    lag = max(r["sequence"] - (r["frame"] - 1) for r in wrong)
    seq_set = sorted(set(seen))
    plateaus = ("; the sequences cluster on "
                f"{seq_set}, which are batch flush points"
                if len(seq_set) * 3 <= len(ours) else "")
    return (f"POOL TOO SHALLOW by {lag}: {len(wrong)} of {len(ours)} files hold "
            f"a later frame's pixels, the worst by {lag} frames{plateaus}. A "
            "slot must survive until the batch holding it is flushed, which "
            "was measured on 2026-09-29 as 64 MiB of accumulated payload or "
            "128 frames, whichever comes first.")


def restore(shell, fpsup):
    place(fpsup)
    flush.restore_probe(shell)


def main():
    global ROUND_ROBIN, FRAMES
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action",
                    choices=("arm", "status", "restore", "build", "card"))
    ap.add_argument("--round-robin", action="store_true",
                    help="frame N overwrites slot N mod 2 with NO release "
                         "signal: measures how deep the pool must be")
    ap.add_argument("--clip", help="card: the clip folder, e.g. A001_201")
    ap.add_argument("--date", help="card: the date in the filenames, e.g. "
                                   "20260929")
    ap.add_argument("--frames", type=int, default=FRAMES,
                    help="card: how many frames to read")
    ap.add_argument("--bound", type=int, default=FRAMES,
                    help="arm: how many frames to redirect. With round robin "
                         "and M=2 the Nth file shows the sequence written at "
                         "about N+L, so a bound above the lag reads L off "
                         "directly; a bound below it only saturates")
    ap.add_argument("--fpsup", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parents[2])
    ap.add_argument("--fpsh", type=pathlib.Path, default=None)
    args = ap.parse_args()
    fpsup = args.fpsup.resolve()
    ROUND_ROBIN = args.round_robin
    FRAMES = args.bound
    if args.action == "card":
        if not args.clip or not args.date:
            ap.error("card needs --clip and --date")
        card(fpsup, args.clip, args.date, args.frames)
        return 0
    if args.action == "build":
        configure()
        code = flush.build_probe(fpsup)
        print(f"{len(code)} bytes of {CODE_CLAIM}; nothing written, no camera "
              "touched")
        return 0
    fpsh = args.fpsh.resolve() if args.fpsh else fpsup / "fp_usb_shell" / "host" / "fpsh"
    shell = base.CameraShell(fpsh)
    {"arm": arm, "status": status, "restore": restore}[args.action](shell, fpsup)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (base.ProbeError, flush.ProbeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
