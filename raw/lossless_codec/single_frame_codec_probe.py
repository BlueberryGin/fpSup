#!/usr/bin/env python3
"""Manage exact-site power/scratch preflights; inspect encode dry-runs.

The power and scratch preflights are live-capable, but this module never runs
either on import.
The encode-and-discard image is intentionally build/dry-run only.  It does not
yet consume and free the retained scratch allocation, so a non-dry-run
``encode`` request is refused before a CameraShell is constructed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import struct
import sys

import exact_dng_writer_probe as base
import probe_placement as placement


STATE = base.STATE
STATE_WORDS = 0x100 // 4
CODE = base.CODE
PREFLIGHT_CODE_LIMIT = 0xC072FB00
SCRATCH_CODE_LIMIT = 0xC072FD00
ENCODE_DESIGN_LIMIT = 0xC072FD00
# The consume-and-free image is larger than the self-allocating design: it adds
# proof/guard revalidation and the release step while dropping F_ALLOC/F_GET.
# Still 512 bytes below the shell's reserved end at 0xC0730000, and no other
# helper may be loaded while it is armed.
ENCODE_LIVE_LIMIT = 0xC072FE00
# PHASE=3 additionally owns a per-frame log immediately above its code, still
# inside the shell's reserved template region. No other helper may be loaded
# while it is armed, and the log must be read before one is.
SUSTAINED_CODE_LIMIT = 0xC072FF00
LOG = 0xC072FF00
# The run lasts as long as the recording. Rows ring over the last RING_FRAMES
# frames; the worst-case trackers at AGG_OFF cover every frame.
SUSTAINED_FRAMES = 4096
# Overridable per run. The first sustained compressed run should be short:
# every frame is a destructive write and a short run is a cheap diagnosis.
FRAME_LIMIT = {"frames": None}


def sustained_frames():
    return FRAME_LIMIT["frames"] or SUSTAINED_FRAMES
RING_FRAMES = 8
LOG_WORDS_PER_FRAME = 4
AGG_OFF = 0x80
AGG_WORDS = 3
BUDGET_US = 41708

PREFLIGHT_SOURCE = pathlib.Path(__file__).with_name("exact_writer_power_preflight.S")
SCRATCH_SOURCE = pathlib.Path(__file__).with_name(
    "exact_writer_scratch_preflight.S"
)
ENCODE_SOURCE = pathlib.Path(__file__).with_name(
    "single_frame_encode_discard_probe.S"
)

PREFLIGHT_MAGIC = 0x52575050
SCRATCH_MAGIC = 0x434F4C41
GUARD_MAGIC = 0xA55AA55A
PHASE_PREFLIGHT = 0
PHASE_SCRATCH = 1
PHASE_ENCODE = 2
PHASE_MIRROR = 4
PHASE_COMPRESS = 5
PHASE_COMPRESS_SUSTAINED = 6
# PHASE=7 retains too: it adopts the block, encodes into it and never frees.
# Leaving 7 out meant release refused a block it was allowed to free, and the
# only recovery was a power cycle - which is exactly the trap the release path
# was written to remove.
# PHASE=9 retains too: it adopts the block and its guard readbacks live there,
# so it must never free, and release has to recognise it afterwards.
RETAINING_PHASES = (2, 4, 5, 6, 7, 9, 11)
# 11 belongs here for the same reason it is excluded from the device's
# release path: it keeps its block across frames. It was missing, and
# release then refused a block it was allowed to free.
# PHASE=5 layout: the header shares the allocation with the codec output.
HDR_BYTES = 78848
IFD_AT = 0x13400
OUT_OFF = 0x13800
TEMPLATE_OFF = 0x3F2000
TEMPLATE_BYTES = 1024
TEMPLATE_META = 0x3F2400
# PHASE=7's three result words, in the retained block rather than the state
# block: that block is full, and the words that looked free are the scratch
# phase's guard records which scratch_preflight_passed reads as a gate.
RESULT_OFF = 0x3F2800

# ---- the two allocations' lifecycle records -----------------------------
# Both live in block A, sixteen words each, written by the camera: the scratch
# preflight writes A's where it calls the allocator, and acquire_second_block.S
# writes B's where it calls the allocator. The host reads them and never writes
# them. That is the whole point of them - the second block's handle used to
# arrive as a command-line argument, and a handle's range, alignment and
# non-overlap prove its SHAPE, not that it is ours.
LIFE_OFF = 0x3F4000
LIFE_SLOT = {"A": 0x00, "B": 0x40}
LIFE_WORDS = 15                    # ..L_BOOT; the slot stride is 16 words
L_MAGIC, L_ROLE, L_ALLOC, L_HANDLE = 0, 1, 2, 3
L_BYTES, L_END, L_TICK, L_SEQ = 4, 5, 6, 7
L_PURPOSE, L_OWNMARK, L_HELD, L_STATE = 8, 9, 10, 11
L_FREERET, L_FREETICK = 12, 13
L_BOOT = 14                        # LOAD_DONE_US, the boot that wrote it
LOAD_DONE_US = 0xC072F6F8          # stage2 stamps it at boot; cave.py's
                                   # boot identity, read here through the
                                   # SAME shell so a caller with a fake
                                   # one sees a consistent answer
LIFE_MAGIC = 0x4546494C            # "LIFE"
OWN_MAGIC = 0x4E574F42             # "BOWN"
PURPOSE = {0x4B524F57: "work+destination", 0x45435253: "generated source"}
OWN_OFF = 0x3FC000                 # the mark, inside each block itself
LIFE_STATE = {0: "no record", 1: "held", 2: "ownership unclear", 3: "freed"}
LIFE_HELD, LIFE_UNCLEAR, LIFE_FREED = 1, 2, 3

# acquire_second_block.S's status words. Distinct from the probe's ERR_*
# numbering because these are returned by a call, not left in the state block.
ACQ_STATUS = {
    0: "ok",
    1: "no retained block A in state",
    2: "block A's record is missing, not held, or names another handle",
    3: "block A does not carry our mark",
    4: "the clock went backwards: block A was acquired in another boot",
    5: "a second block is already recorded as held; release it first",
    6: "the allocator object could not be obtained",
    7: "the allocator returned no block",
    8: "the block came back the wrong shape; it was freed again",
    9: "the block overlaps block A; it was freed again",
    10: "the block would not hold our mark; it was freed again",
}
PROBE_SRC_BYTES = 0x304CB0      # what the engine reads at the product geometry
PROBE_DECLARED = 0x305000       # the destination length it declares for it
PHASE_SUSTAINED = 3
FREE_MAGIC = 0x45455246
DONE_MAGIC = base.DONE_MAGIC

ALLOC_SIZE = 0x400000
WORK_CAP = 0x3F0000
TABLE_OFFSET = 0x3F1000
TABLE_TEMP_OFFSET = 0x3F1400
TABLE_BYTES = 12 * 4
# Product geometry. The arena is sized from the worst-case output bound, so an
# expanding frame cannot reach the size tables; see tools/output_bound.py.
TILE_W = 512
TILE_H = 368
FRAME_WIDTH = 1936
FRAME_HEIGHT = 1090

S_COUNT = 0x00 // 4
S_R2 = 0x0C // 4
S_LR = 0x10 // 4
S_BUFFER = 0x14 // 4
S_LENGTH = 0x18 // 4
S_DONE = 0x1C // 4
S_RESTORED = 0x20 // 4
S_PHASE = 0x24 // 4
S_STAGE = 0x28 // 4
S_ERROR = 0x2C // 4
S_CODEC_MODE = 0x30 // 4          # power-preflight interpretation
S_SOURCE = 0x34 // 4              # power-preflight interpretation
S_CLEANUP_ERROR = 0x38 // 4       # power-preflight interpretation
S_ALLOCATOR = 0x30 // 4           # scratch-preflight interpretation
S_HANDLE = 0x34 // 4
S_ALLOC_END = 0x38 // 4
S_WORK_BASE = 0x3C // 4
S_WORK_END = 0x40 // 4
S_WORK_GUARD = 0x44 // 4
S_TABLE_BASE = 0x48 // 4
S_TABLE_TEMP = 0x4C // 4
S_SCRATCH_SOURCE = 0x50 // 4
S_SCRATCH_CODEC_MODE = 0x54 // 4
S_T0 = 0x50 // 4
S_T1 = 0x54 // 4
S_PWR_ON = 0x68 // 4
S_CLK_ON = 0x6C // 4
S_CLK_OFF = 0x70 // 4
S_PWR_OFF = 0x74 // 4
S_GUARD_WORK = 0x94 // 4
S_GUARD_TABLE = 0x98 // 4
S_GUARD_TEMP = 0x9C // 4
S_PREFLIGHT = 0xD4 // 4
S_SCRATCH_MAGIC = 0xD8 // 4
S_END_GUARD = 0xDC // 4
S_GUARD_END = 0xE0 // 4
S_TABLE_GUARD = 0xE4 // 4
S_TEMP_GUARD = 0xE8 // 4
S_FREE_ERROR = 0xEC // 4
S_FREE_DONE = 0xF0 // 4
S_ENCODE_STARTED = 0xF4 // 4
S_ADOPTED = 0xF8 // 4
S_FRAMES_DONE = 0xFC // 4
S_INIT_RET = 0x58 // 4
S_ENC_RET = 0x5C // 4
S_SIZE_RET = 0x60 // 4
S_COMPRESSED = 0x64 // 4
S_TILE_COUNT = 0xA0 // 4
S_HDR_BEFORE = 0x78 // 4
S_HDR_AFTER = 0x7C // 4
S_PIX0_BEFORE = 0x80 // 4
S_PIX0_AFTER = 0x84 // 4
S_PIXN_BEFORE = 0x88 // 4
S_PIXN_AFTER = 0x8C // 4
S_TILE0 = 0xA4 // 4
RAW_BYTES = 0x304CB0
TILE_COUNT = 12
ERR_RETAINED = 25
F_FREE = 0xC001D2B8
KEEP_KINDS = ("encode-live", "encode-sustained")

STATE_FIELDS = {
    0x00: "count",
    0x04: "first_r0",
    0x08: "first_r1",
    0x0C: "first_r2",
    0x10: "first_lr",
    0x14: "buffer",
    0x18: "segment_length",
    0x1C: "done",
    0x20: "restored_word",
    0x24: "phase",
    0x28: "stage",
    0x2C: "error",
    0x30: "codec_mode_or_allocator",
    0x34: "source_or_allocation_handle",
    0x38: "cleanup_error_or_allocation_end",
    0x3C: "work_base",
    0x40: "work_end",
    0x44: "work_guard_address",
    0x48: "size_table_base",
    0x4C: "temporary_table",
    0x50: "source_or_t0",
    0x54: "codec_mode_or_t1",
    0x58: "init_return",
    0x5C: "encode_return",
    0x60: "size_return",
    0x64: "compressed_size_sum",
    0x68: "power_on_return",
    0x6C: "clock_on_return",
    0x70: "clock_off_return",
    0x74: "power_off_return",
    0x78: "header_before",
    0x7C: "header_after",
    0x80: "first_pixel_before",
    0x84: "first_pixel_after",
    0x88: "last_pixel_before",
    0x8C: "last_pixel_after",
    0x90: "segment_length_after",
    0x94: "work_guard_after",
    0x98: "size_table_guard_after",
    0x9C: "temporary_table_guard_after",
    0xA0: "tile_count",
    **{0xA4 + index * 4: f"tile_size_{index}" for index in range(12)},
    0xD4: "power_preflight_magic",
    0xD8: "scratch_preflight_magic",
    0xDC: "allocation_end_guard_address",
    0xE0: "allocation_end_guard_after",
    0xE4: "size_table_guard_address",
    0xE8: "temporary_table_guard_address",
    0xEC: "free_error",
    0xF0: "free_done",
    0xF4: "encode_entered",
    0xF8: "block_adopted",
    0xFC: "frames_done",
}


class ProbeError(base.ProbeError):
    """A build refusal or inherited verified transport failure."""


# Kept identical to tools/output_bound.py by test_bound_matches_the_tool.
BOUND_BITS = 14
TILE_MARKER_BYTES = 96


def worst_case_output_bytes(tile_w=TILE_W, tile_h=TILE_H,
                            width=FRAME_WIDTH, height=FRAME_HEIGHT):
    """Bound from tools/output_bound.py, inlined so the build can enforce it.

    Lossless JPEG expands on high-entropy input: every sample costs a Huffman
    category plus that many extra bits. BOUND_BITS = 14 is the smallest
    integer above the maximum of E[SSSS] + H(p) + 1 over category
    distributions for 12-bit samples, which that module computes numerically.

    This used to inline that maximum as the literal 13.9998 and truncate the
    product. Both roundings went the same way, so the number this returned was
    7 bytes BELOW the one the tool prints - and a bound under the true worst
    case is not a bound. Integers only now, and a test asserts the two
    implementations return the same value, not values that are close.
    """
    cols = -(-width // tile_w)
    rows = -(-height // tile_h)
    padded_samples = cols * tile_w * rows * tile_h
    return -(-padded_samples * BOUND_BITS // 8) + cols * rows * TILE_MARKER_BYTES


# "copy-back" builds the same file as the compressed kinds but hands the writer
# nothing of ours: it copies the result into the camera's own frame buffer and
# changes only seg[1]. Its capacity bound is still the arena's, because the file
# is assembled here before it is copied out.
COMPRESSED_KINDS = ("compress", "compress-sustained", "copy-back",
                    # Two diagnostics that isolate what makes copy-back freeze.
                    # They build the same file the same way and each drops ONE
                    # of the two things copy-back does that mirror and compress
                    # do not. Both leave junk on the card and neither is a
                    # feature path.
                    "copy-back-length-only", "copy-back-copy-only")
# Every kind that drives the hardware codec, and therefore every kind that
# needs a capacity proof before it is assembled: the codec's output is bounded
# only by the worst case, never by the raster. "encode" is the dry run and is
# included because its whole purpose is to report the layout it would use.
# "mirror" is deliberately absent: it copies the stock DNG at its own fixed
# length, so the codec bound would pass there by coincidence, not by argument.
CODEC_KINDS = ("encode", "encode-live", "encode-sustained") + COMPRESSED_KINDS


def output_capacity(kind=None):
    """Bytes the codec may write. The compressed phase shares the arena with
    an owned header, so its capacity is the arena minus that header."""
    return WORK_CAP - (OUT_OFF if kind in COMPRESSED_KINDS else 0)


def require_safe_arena(tile_w=None, tile_h=None, kind=None):
    """Refuse a geometry whose worst case cannot fit below the size tables.

    This is a build-time refusal on purpose. Detecting an overrun after the
    codec has run means reading a size table the codec may already have
    destroyed, so the geometry must be rejected before anything is armed.

    The geometry defaults are read at call time, not frozen into the
    signature: a default bound at import would keep validating 512x368 after
    the constant moved, which is the shape the TILE_H bug already took once.
    """
    tile_w = TILE_W if tile_w is None else tile_w
    tile_h = TILE_H if tile_h is None else tile_h
    bound = worst_case_output_bytes(tile_w, tile_h)
    capacity = output_capacity(kind)
    if bound > capacity:
        raise ProbeError(
            f"tile {tile_w}x{tile_h} bounds at {bound:,} output bytes, above "
            f"the {capacity:,} available"
            f"{' after the header' if kind in COMPRESSED_KINDS else ''}; "
            "no safe layout exists here"
        )
    if TABLE_OFFSET < WORK_CAP or TABLE_TEMP_OFFSET < TABLE_OFFSET + TABLE_BYTES:
        raise ProbeError("size tables overlap the output arena")
    if TABLE_TEMP_OFFSET + TABLE_BYTES + 4 > ALLOC_SIZE:
        raise ProbeError("layout exceeds the allocation")
    return bound


CLAIM_NAMES = {"code": "lossless.codec.code",
               "state": "lossless.codec.state",
               "log": "lossless.codec.log"}
LOG_BYTES = 0x100
# One claim for every kind, sized for the largest image. A bump allocator
# cannot grow a claim in place, so claiming per kind would re-bump on each
# stage of the chain and exhaust the arena halfway through it.
CODE_CLAIM_BYTES = 0x700
# The arena also has to hold callfn's own tools, which cache publication
# pulls in: putfile.parm/.code, bulkload.*, dumpraw, dumpdirect. Measured at
# 1,544 bytes, which is why only one probe family fits per boot.
CALLFN_TOOL_BYTES = 1544


def place_and_build(kind: str, fpsup: pathlib.Path, claim=None):
    """Claim cave blocks for this image, then build it at those addresses.

    Two passes: the default build gives the size, the claim gives the address,
    the second build bakes it in. Every address in this image is a movw/movt
    immediate, so the size cannot change between passes -- and that is checked,
    because a size change would mean the claim was for the wrong length.
    """
    sized = build_image(kind, fpsup)
    if len(sized) > CODE_CLAIM_BYTES:
        raise ProbeError(
            f"{kind} is {len(sized)} bytes, above the {CODE_CLAIM_BYTES}-byte "
            "shared code claim; raise CODE_CLAIM_BYTES and re-check the budget")
    regions = [(CLAIM_NAMES["code"], CODE_CLAIM_BYTES),
               (CLAIM_NAMES["state"], STATE_WORDS * 4)]
    if kind in ("encode-sustained", "compress-sustained"):
        regions.append((CLAIM_NAMES["log"], LOG_BYTES))
    placed = placement.claim_regions(regions, claim=claim)
    code = build_image(kind, fpsup, placed=placed)
    if len(code) != len(sized):
        raise ProbeError(
            f"placed image is {len(code)} bytes against {len(sized)} sized; "
            "the claim would be the wrong length")
    return code, placed


RELEASE_SOURCE = pathlib.Path(__file__).with_name("release_observe_probe.S")

# The state dispatcher the observer displaces, and the word it must find there.
DISPATCH = 0xC0397038
DISPATCH_ORIG = 0xE92D4DF0          # push {r4,r5,r6,r7,r8,sl,fp,lr}
RING_OFF = 0x3F5000
RING_CAP = 1024
RING_MAGIC = 0x5642534F             # "OBSV"
FRAME_STATE_NAMES = {0x00: "free/reset", 0x08: "8", 0x09: "9", 0x0A: "a",
                     0x0D: "submitted (success or error)",
                     0x12: "card layer finished -> buffer reclaimable"}


def branch_word(site: int, target: int) -> int:
    """An unconditional B, not a BL.

    probe_placement.armed_word builds a BL, which is right for a call site: the
    hooked code returns. This displaces a FUNCTION'S FIRST INSTRUCTION, and the
    handler has to continue into that function with the caller's lr intact, so
    linking would destroy the very thing it must preserve.
    """
    offset = (target - (site + 8)) >> 2
    if not -(1 << 23) <= offset < (1 << 23):
        raise ProbeError(f"0x{target:08X} is out of branch range from "
                         f"0x{site:08X}")
    word = 0xEA000000 | (offset & 0xFFFFFF)
    if decode_branch(site, word) != target:
        raise ProbeError("branch word does not decode back to its target")
    return word


def decode_branch(site: int, word: int) -> int:
    if word >> 24 != 0xEA:
        raise ProbeError(f"0x{word:08X} is not an unconditional B")
    imm = word & 0xFFFFFF
    if imm & 0x800000:
        imm -= 0x1000000
    return (site + 8 + imm * 4) & 0xFFFFFFFF


def build_release_observer(fpsup: pathlib.Path, placed: dict = None) -> bytes:
    """Assemble release_observe_probe.S.

    Not through build_image, for the same reason the acquire routine is not:
    that function requires the image to end in the writer's tail call. This one
    ends in a branch back into the dispatcher.
    """
    assemble, symbols = base.load_assembler(fpsup)
    defines: tuple[str, ...] = ()
    if placed:
        defines = (f"STATE=0x{placed[CLAIM_NAMES['state']]:08X}",)
    code = assemble(RELEASE_SOURCE, defines=defines)
    if symbols(RELEASE_SOURCE).get("observe_entry") != 0:
        raise ProbeError("observe_entry is not at offset zero")
    if len(code) & 3:
        raise ProbeError("assembled observer is not word aligned")
    if len(code) > CODE_CLAIM_BYTES:
        raise ProbeError(f"the observer is {len(code)} bytes, above the "
                         f"{CODE_CLAIM_BYTES}-byte code claim")
    return code


def place_and_build_release(fpsup: pathlib.Path, claim=None):
    sized = build_release_observer(fpsup)
    regions = [(CLAIM_NAMES["code"], CODE_CLAIM_BYTES),
               (CLAIM_NAMES["state"], STATE_WORDS * 4)]
    placed = placement.claim_regions(regions, claim=claim)
    code = build_release_observer(fpsup, placed=placed)
    if len(code) != len(sized):
        raise ProbeError("placed observer changed size")
    return code, placed


def arm_release_observer(shell: base.CameraShell, fpsup: pathlib.Path,
                         call=None) -> dict:
    """Arm the observer on the state dispatcher.

    THIS ONE DOES NOT SELF-RESTORE. Every other probe here displaces a call for
    exactly one frame and puts the word back itself; this has to watch every
    transition of every frame for a whole clip, so the patch stays until the
    host removes it with `observe-restore`. A camera that freezes or an operator
    who forgets leaves a patched dispatcher behind - harmless across a power
    cycle, because the word lives in RAM, and nothing else.
    """
    values = shell.read_words(STATE, STATE_WORDS)
    if not scratch_preflight_success(values):
        raise ProbeError(
            "the observer needs the retained scratch block to log into; run "
            "preflight then scratch first")
    handle = values[S_HANDLE]
    present = shell.read_words(DISPATCH, 1)[0]
    if present != DISPATCH_ORIG:
        raise ProbeError(
            f"0x{DISPATCH:08X} holds 0x{present:08X}, not the "
            f"0x{DISPATCH_ORIG:08X} this probe was built to displace. Either "
            "something else is armed there or this is not the firmware it was "
            "read from; nothing has been written")

    code, placed = place_and_build_release(fpsup)
    entry = placed[CLAIM_NAMES["code"]]
    armed = branch_word(DISPATCH, entry)
    print(f"observer      {len(code)} bytes at 0x{entry:08X}")
    print(f"ring          0x{handle + RING_OFF:08X}, {RING_CAP} records")
    print(f"dispatcher    0x{DISPATCH:08X}: 0x{present:08X} -> 0x{armed:08X}")

    shell.write_words_verified(entry, base.words_from(code))
    # The ring header is the probe's own arming gate: it refuses to record
    # until the magic is there, so a stale block cannot be logged into.
    shell.write_words_verified(handle + RING_OFF,
                               [0, RING_CAP, RING_MAGIC, 0])
    if call is None:
        call = native_caller()
    for item in placement.publish(call):
        print(f"published {item['function']} -> {item['result']}")
    shell.write_word_verified(DISPATCH, armed)
    back = shell.read_words(DISPATCH, 1)[0]
    if back != armed:
        raise ProbeError(f"the dispatcher reads 0x{back:08X} after arming")
    print("observer armed. It does NOT self-restore: run `observe-restore` "
          "when the clip is done, before anything else is armed")
    return {"entry": entry, "ring": handle + RING_OFF, "armed_word": armed}


def restore_release_observer(shell: base.CameraShell) -> dict:
    """Put the dispatcher's first instruction back, and prove it."""
    present = shell.read_words(DISPATCH, 1)[0]
    if present == DISPATCH_ORIG:
        return {"already_restored": True}
    try:
        target = decode_branch(DISPATCH, present)
    except ProbeError:
        raise ProbeError(
            f"0x{DISPATCH:08X} holds 0x{present:08X}, which is neither the "
            "original instruction nor a branch this probe wrote. Refusing to "
            "guess what belongs there")
    shell.write_word_verified(DISPATCH, DISPATCH_ORIG)
    back = shell.read_words(DISPATCH, 1)[0]
    if back != DISPATCH_ORIG:
        raise ProbeError(f"restore failed: 0x{DISPATCH:08X} reads 0x{back:08X}")
    return {"restored": True, "was_branching_to": f"0x{target:08X}"}


def read_release_log(shell: base.CameraShell, handle: int,
                     limit: int = None) -> dict:
    """The ring, decoded, plus what it does not say.

    The timestamp is the raw word F_CLOCK is built on, in units this does not
    know. Differences are meaningful only once the rate is calibrated, which is
    a separate host measurement - read_tick_rate below.
    """
    header = shell.read_words(handle + RING_OFF, 4)
    count, cap, magic, dropped = header
    if magic != RING_MAGIC:
        raise ProbeError(
            f"no observer ring at 0x{handle + RING_OFF:08X} (magic "
            f"0x{magic:08X}); it was never armed, or the block is not the one "
            "that was armed")
    if count > cap:
        raise ProbeError(f"the ring reports {count} records in {cap} slots")
    wanted = count if limit is None else min(count, limit)
    records = []
    at = handle + RING_OFF + 0x10
    for i in range(wanted):
        tick, frame, state, reason = shell.read_words(at + i * 16, 4)
        records.append({"tick": tick, "frame": f"0x{frame:08X}",
                        "state_in": state,
                        "state_name": FRAME_STATE_NAMES.get(state, f"0x{state:X}"),
                        "reason": reason})
    return {"count": count, "capacity": cap, "dropped": dropped,
            "records": records,
            "frames_seen": sorted({r["frame"] for r in records}),
            "states_seen": sorted({r["state_in"] for r in records})}


def read_tick_rate(shell: base.CameraShell, seconds: float = 2.0) -> dict:
    """Calibrate the raw counter against the host's clock.

    Two reads and a wait. The probe records the raw word because F_CLOCK takes a
    lock and converts through two calls, which is not something to run on every
    state transition of every frame; the price is that the unit has to be
    measured here instead of assumed.
    """
    import time
    first = shell.read_words(TICK_VAR, 1)[0]
    started = time.monotonic()
    time.sleep(seconds)
    second = shell.read_words(TICK_VAR, 1)[0]
    elapsed = time.monotonic() - started
    delta = (second - first) & 0xFFFFFFFF
    return {"first": first, "second": second, "delta": delta,
            "host_seconds": round(elapsed, 4),
            "ticks_per_second": round(delta / elapsed, 1) if elapsed else None,
            "note": "one sample over a couple of seconds; it fixes the unit, "
                    "not the counter's stability"}


TICK_VAR = 0xC30781A4


ACQUIRE_SOURCE = pathlib.Path(__file__).with_name("acquire_second_block.S")


def build_acquire(fpsup: pathlib.Path, placed: dict = None) -> bytes:
    """Assemble acquire_second_block.S.

    Deliberately not routed through build_image. That function ends by
    requiring the image's last two words to be the writer's
    register-transparent tail call, which is right for everything that fires at
    the hook and wrong for this: it is an ordinary function the host CALLS, so
    it ends in bx lr and must not be checked against a hook image's contract.
    """
    assemble, symbols = base.load_assembler(fpsup)
    defines: tuple[str, ...] = ()
    if placed:
        defines = (f"STATE=0x{placed[CLAIM_NAMES['state']]:08X}",)
    code = assemble(ACQUIRE_SOURCE, defines=defines)
    if symbols(ACQUIRE_SOURCE).get("acquire_entry") != 0:
        raise ProbeError("acquire_entry is not at offset zero")
    if len(code) & 3:
        raise ProbeError("assembled acquire routine is not word aligned")
    words = base.words_from(code)
    if words[-1] != 0xE12FFF1E:
        raise ProbeError("the acquire routine does not end in bx lr")
    if len(code) > CODE_CLAIM_BYTES:
        raise ProbeError(
            f"the acquire routine is {len(code)} bytes, above the "
            f"{CODE_CLAIM_BYTES}-byte shared code claim")
    return code


def place_and_build_acquire(fpsup: pathlib.Path, claim=None):
    """Claim the same blocks the images use, and build at those addresses.

    The SAME code claim: this routine runs before the bound-probe image is
    written, and the arena is a bump allocator with no release, so a claim of
    its own would be 604 bytes the chain never gets back. The state claim has
    to be the same one, because the routine reads the state block to find
    block A.
    """
    sized = build_acquire(fpsup)
    regions = [(CLAIM_NAMES["code"], CODE_CLAIM_BYTES),
               (CLAIM_NAMES["state"], STATE_WORDS * 4)]
    placed = placement.claim_regions(regions, claim=claim)
    code = build_acquire(fpsup, placed=placed)
    if len(code) != len(sized):
        raise ProbeError("placed acquire routine changed size")
    return code, placed


KEEP_BLOCK = {"active": False}


def build_image(kind: str, fpsup: pathlib.Path, placed: dict = None) -> bytes:
    assemble, symbols = base.load_assembler(fpsup)
    if kind == "preflight":
        source = PREFLIGHT_SOURCE
        defines: tuple[str, ...] = ()
        limit = PREFLIGHT_CODE_LIMIT
    elif kind == "scratch":
        source = SCRATCH_SOURCE
        defines = ()
        limit = SCRATCH_CODE_LIMIT
    elif kind == "encode":
        source = ENCODE_SOURCE
        defines = ("PHASE=1",)
        limit = ENCODE_DESIGN_LIMIT
    elif kind == "encode-live":
        source = ENCODE_SOURCE
        defines = ("PHASE=2",)
        limit = ENCODE_LIVE_LIMIT
    elif kind == "encode-sustained":
        source = ENCODE_SOURCE
        defines = ("PHASE=3", f"FRAMES={sustained_frames()}")
        limit = SUSTAINED_CODE_LIMIT
    elif kind == "compress":
        # G4b: encode, build the owned header in front of the payload, and
        # redirect the node at the whole thing.
        source = ENCODE_SOURCE
        defines = ("PHASE=5",)
        limit = SUSTAINED_CODE_LIMIT
    elif kind == "compress-sustained":
        # G5: the same write, every frame, re-arming between them. One buffer:
        # whether the flush is done with it before the next frame starts is
        # precisely what this run measures.
        source = ENCODE_SOURCE
        defines = ("PHASE=6", f"FRAMES={sustained_frames()}")
        limit = SUSTAINED_CODE_LIMIT
    elif kind == "copy-back":
        # PHASE=7: encode, build the owned header, then copy the whole file
        # into the buffer the writer handed us and change only seg[1]. We keep
        # no buffer, so the aggregation batch that broke G5 stops mattering.
        # A frame whose file exceeds seg[1] is left alone and writes as stock
        # RAW - decided after the encode, from the size table, not predicted.
        source = ENCODE_SOURCE
        defines = ("PHASE=7",)
        limit = SUSTAINED_CODE_LIMIT
    elif kind == "bound-probe-two":
        # PHASE=9 with the product geometry AND a generated source, the only
        # combination that can reach the declared length: the engine refuses
        # other geometries and a real frame compresses. Source and destination
        # do not fit in one block, so this needs two.
        source = ENCODE_SOURCE
        defines = ("PHASE=9", "PROBE_TWO=1")
        limit = SUSTAINED_CODE_LIMIT
    elif kind == "copy-back-length-only":
        # PHASE=7 with the 1.5 MB copy into the camera's frame buffer removed
        # and the seg[1] change kept. The card writes a truncated stock RAW.
        source = ENCODE_SOURCE
        defines = ("PHASE=7", "COPYBACK_NO_COPY=1")
        limit = SUSTAINED_CODE_LIMIT
    elif kind == "copy-back-copy-only":
        # PHASE=7 with the copy kept and seg[1] left exactly as handed over.
        # The card writes its full length: our file, then leftover raster.
        source = ENCODE_SOURCE
        defines = ("PHASE=7", "COPYBACK_NO_LENGTH=1")
        limit = SUSTAINED_CODE_LIMIT
    elif kind == "async-inspect":
        # PHASE=11 stage one. Calls F_INIT exactly as the working phases do and
        # reads the engine's own parameter block back, because the mapping from
        # initStruct's nine words to those six fields has never been measured
        # and the whole asynchronous ABI rests on it. Submits nothing.
        source = ENCODE_SOURCE
        defines = ("PHASE=11", f"FRAMES={ASYNC_FRAMES}")
        limit = SUSTAINED_CODE_LIMIT
    elif kind == "async-submit":
        # PHASE=11 stage two. Creates the engine's completion flag (stage one
        # measured it as not existing: -42), builds the request from the mapping
        # stage one confirmed, submits, and RETURNS WITHOUT WAITING. Later frames
        # poll. The reduced 1536x736 geometry keeps source and destination inside
        # one block, and the source is the compressible pattern - an
        # incompressible one is refused outright, which would test nothing.
        source = ENCODE_SOURCE
        defines = ("PHASE=11", f"FRAMES={ASYNC_FRAMES}", "ASYNC_SUBMIT=1")
        limit = SUSTAINED_CODE_LIMIT
    elif kind == "bound-probe-two-ramp":
        # Generated but compressible: the variant that separates "synthetic
        # data" from "an output that would exceed the declared length", which
        # the LCG/real-frame pair could not.
        source = ENCODE_SOURCE
        defines = ("PHASE=9", "PROBE_TWO=1", "PROBE_RAMP=1")
        limit = SUSTAINED_CODE_LIMIT
    elif kind == "bound-probe-two-copy":
        # Not a bound test. The same two blocks, but block B is filled with the
        # CAMERA'S OWN pixels instead of generated data, to separate the two
        # things that could have made F_ENC refuse on 2026-09-28: the content of
        # the source, or the block it lives in.
        source = ENCODE_SOURCE
        defines = ("PHASE=9", "PROBE_TWO=1", "PROBE_COPY_FRAME=1")
        limit = SUSTAINED_CODE_LIMIT
    elif kind == "bound-probe-frame":
        # PHASE=9 with the camera's own frame as the source, READ ONLY, so only
        # the destination is ours and the PRODUCT geometry fits in one block.
        # A real frame compresses and never reaches the declared limit, so this
        # does not answer the bound - it answers whether the reduced geometry is
        # why the engine refused on 2026-09-26.
        source = ENCODE_SOURCE
        defines = ("PHASE=9", "PROBE_FRAME=1")
        limit = SUSTAINED_CODE_LIMIT
    elif kind == "bound-probe":
        # PHASE=9: measure whether the engine stops at the destination LENGTH
        # the firmware hands it, round_up(depth*w*h/8, 1024). Everything stays
        # inside our own retained block - the source is generated there, the
        # output goes there, guards sit at the declared length - so seg is never
        # written and the frame reaches the card untouched.
        source = ENCODE_SOURCE
        defines = ("PHASE=9",)
        limit = SUSTAINED_CODE_LIMIT
    elif kind == "mirror":
        # G4a: copy the stock DNG into the arena and redirect the writer's node
        # at the copy, same length, same bytes. No codec, no format risk.
        source = ENCODE_SOURCE
        defines = ("PHASE=4",)
        limit = ENCODE_LIVE_LIMIT
    else:
        raise ProbeError(f"unknown image kind {kind!r}")

    # One precheck for every kind that runs the codec, compressed included.
    # The compressed kinds used to be missing here entirely, so the geometry
    # that the discard phases refuse was assembled without complaint for the
    # phase that has LESS room, not more.
    if kind in CODEC_KINDS:
        require_safe_arena(kind=kind)
    if KEEP_BLOCK["active"] and kind in KEEP_KINDS:
        defines = defines + ("KEEP_BLOCK=1",)
    if placed:
        armed = placement.armed_word(base.HOOK_SITE, placed[CLAIM_NAMES["code"]])
        defines = defines + (f"STATE=0x{placed[CLAIM_NAMES['state']]:08X}",
                             f"HOOK_ARMED=0x{armed:08X}")
        if CLAIM_NAMES["log"] in placed:
            defines = defines + (f"LOG=0x{placed[CLAIM_NAMES['log']]:08X}",)
    code = assemble(source, defines=defines)
    syms = symbols(source, defines=defines)
    if syms.get("probe_entry") != 0:
        raise ProbeError(f"probe_entry is not at offset zero: {syms.get('probe_entry')}")
    if len(code) & 3:
        raise ProbeError("assembled image is not word aligned")
    if CODE + len(code) > limit:
        raise ProbeError(
            f"{kind} image 0x{CODE:08X}..0x{CODE + len(code):08X} "
            f"exceeds design limit 0x{limit:08X}"
        )
    words = base.words_from(code)
    if words[-2:] != [0xE51FF004, base.REAL_WRITE]:
        raise ProbeError("image does not end in the register-transparent tail-call")
    return code


def describe_image(kind: str, code: bytes, show_words: bool = False) -> None:
    source = {
        "preflight": PREFLIGHT_SOURCE,
        "scratch": SCRATCH_SOURCE,
        "encode": ENCODE_SOURCE,
        "encode-live": ENCODE_SOURCE,
        "encode-sustained": ENCODE_SOURCE,
        "mirror": ENCODE_SOURCE,
        "compress": ENCODE_SOURCE,
        "compress-sustained": ENCODE_SOURCE,
        "copy-back": ENCODE_SOURCE,
        "bound-probe": ENCODE_SOURCE,
        "bound-probe-frame": ENCODE_SOURCE,
        "bound-probe-two": ENCODE_SOURCE,
        "bound-probe-two-copy": ENCODE_SOURCE,
        "bound-probe-two-ramp": ENCODE_SOURCE,
        "async-inspect": ENCODE_SOURCE,
        "async-submit": ENCODE_SOURCE,
        "copy-back-length-only": ENCODE_SOURCE,
        "copy-back-copy-only": ENCODE_SOURCE,
    }[kind]
    live = kind in ("preflight", "scratch", "encode-live", "encode-sustained",
                    "mirror", "compress", "compress-sustained", "copy-back",
                    "bound-probe", "bound-probe-frame", "bound-probe-two",
                    "bound-probe-two-copy", "bound-probe-two-ramp",
                    "async-inspect", "async-submit")
    print(f"image  : {kind}")
    print(f"source : {source}")
    print(f"sha256 : {hashlib.sha256(code).hexdigest()}")
    print(f"code   : {len(code)} bytes, 0x{CODE:08X}..0x{CODE + len(code):08X}")
    print(f"state  : 0x{STATE:08X}..0x{STATE + STATE_WORDS * 4:08X}")
    print(f"site   : 0x{base.HOOK_SITE:08X}, require 0x{base.HOOK_ORIG:08X}")
    print(f"arm    : 0x{base.HOOK_ARMED:08X} (final write)")
    if kind == "preflight":
        mode = "live-capable power-only preflight"
    elif kind == "scratch":
        mode = "live-capable scratch-only preflight; requires PPWR proof"
    elif kind == "encode-live":
        mode = ("live-capable single encode; consumes the retained block, "
                "requires PPWR + ALOC proof")
    elif kind == "encode-sustained":
        mode = (f"live-capable sustained encode over {SUSTAINED_FRAMES} frames; "
                f"log at 0x{LOG:08X}, re-arms between frames")
    elif kind == "mirror":
        mode = ("DESTRUCTIVE: redirects the writer at a byte-identical copy. "
                "No codec, no format change, block retained for the flush")
    elif kind == "compress":
        mode = ("DESTRUCTIVE: writes a COMPRESSED frame. Owned header at +0, "
                f"codec output at +0x{OUT_OFF:X}, block retained for the flush")
    elif kind == "compress-sustained":
        mode = (f"DESTRUCTIVE: writes up to {sustained_frames()} COMPRESSED "
                "frames from ONE buffer, re-arming between them")
    elif kind == "copy-back-length-only":
        mode = ("DIAGNOSTIC, DESTRUCTIVE: copy-back with the 1.5 MB copy into "
                "the camera's frame buffer REMOVED and the seg[1] change kept. "
                "The card writes a truncated stock RAW - junk, harmless. If "
                "this freezes, the length change is what copy-back cannot do")
    elif kind == "copy-back-copy-only":
        mode = ("DIAGNOSTIC, DESTRUCTIVE: copy-back with the copy KEPT and "
                "seg[1] left as handed over. The card writes our file followed "
                "by leftover raster - junk, harmless. If this freezes, the copy "
                "into the camera's frame buffer is what copy-back cannot do")
    elif kind == "async-submit":
        mode = ("NON-DESTRUCTIVE: the first self-issued encode. Creates the "
                "completion flag, submits one frame's compression and RETURNS "
                "without waiting; later frames poll with a one-tick timeout. "
                "Everything is inside our own block and seg is never written. If "
                "the poll ever reports completion, asynchronous compression is "
                "available and the architecture has its executable path")
    elif kind == "async-inspect":
        mode = ("NON-DESTRUCTIVE: calls F_INIT and reads the engine's parameter "
                "block back. It SUBMITS NOTHING, so it cannot misdirect a DMA. "
                "It answers one question: how F_INIT maps initStruct's nine "
                "words onto the six engine fields the asynchronous request has "
                "to be built from. seg is never written")
    elif kind == "bound-probe-two-ramp":
        mode = ("NON-DESTRUCTIVE: TWO blocks, a GENERATED but COMPRESSIBLE "
                "source in the second. It separates the two explanations the "
                "previous pair could not: synthetic data, or an output that "
                "would exceed the declared length. seg is never written")
    elif kind == "bound-probe-two-copy":
        mode = ("NON-DESTRUCTIVE: TWO blocks, but the source is the CAMERA'S "
                "OWN pixels copied into the second one. This is a SOURCE "
                "LOCATION test, not a bound test: a real frame compresses, so "
                "the output never approaches the declared length and the "
                "guards prove nothing. It answers one question - will the "
                "engine read from a block we allocated? The frame is read only "
                "and seg is never written")
    elif kind == "bound-probe-two":
        mode = ("NON-DESTRUCTIVE: TWO blocks. An incompressible source is "
                "generated in the second and the destination plus guards live "
                "in the first, both ours. This is the combination that can "
                "reach the declared length, so it is the one that answers "
                "whether the engine stops there. seg is never written")
    elif kind == "bound-probe-frame":
        mode = ("NON-DESTRUCTIVE: reads the camera's own frame as the source and "
                "encodes into our own block at the PRODUCT geometry, with guards "
                "at the declared length. The frame is never written. A real frame "
                "compresses, so this tests the GEOMETRY, not the bound")
    elif kind == "bound-probe":
        mode = ("NON-DESTRUCTIVE: generates an incompressible source inside our "
                "own block, encodes into our own block, and reads back guards "
                "at the declared destination length. seg is never written and "
                "the frame reaches the card exactly as it would have")
    elif kind == "copy-back":
        mode = ("DESTRUCTIVE: compresses INTO THE CAMERA'S OWN FRAME BUFFER. "
                "seg[0] is left alone and only seg[1] changes, so nothing of "
                "ours outlives the call. A file larger than seg[1] is skipped "
                "and that frame writes as stock RAW")
    else:
        mode = "DRY-RUN ONLY"
    print(f"mode   : {mode}")
    if kind in ("scratch", "encode", "encode-live", "encode-sustained", "mirror",
                "compress", "compress-sustained", "copy-back"):
        layout = encode_layout(0)
        bound = worst_case_output_bytes()
        capacity = output_capacity(kind)
        # Derived, not spelled out: this line said 0x305000 for a day after the
        # arena moved, which is exactly the kind of stale report a live
        # entry condition gets read from.
        print(f"tile   : {TILE_W}x{TILE_H}, predictor 1, {FRAME_WIDTH}x{FRAME_HEIGHT}")
        print(f"layout : work [base,+0x{WORK_CAP:X}), table +0x{TABLE_OFFSET:X}, "
              f"temporary table +0x{TABLE_TEMP_OFFSET:X}, "
              f"allocation 0x{ALLOC_SIZE:X}")
        if kind in COMPRESSED_KINDS:
            print(f"header : {HDR_BYTES:,} B copied per frame, payload at "
                  f"+0x{OUT_OFF:X}")
        print(f"bound  : worst case {bound:,} B, capacity {capacity:,} B, "
              f"margin {capacity - bound:,} B "
              f"({100 * (capacity - bound) / bound:.2f}%)")
        if not encode_layout_is_safe(layout):
            raise ProbeError("internal encode layout validation failed")
    if not live:
        print("reason : requires reviewed power and retained-scratch preflights")
    if show_words:
        for index, word in enumerate(base.words_from(code)):
            print(f"  0x{CODE + index * 4:08X}: 0x{word:08X}")


def encode_layout(base_address: int) -> dict[str, tuple[int, int]]:
    """Return the half-open ranges used by the dry-run encode design."""
    return {
        "allocation": (base_address, base_address + ALLOC_SIZE),
        "work": (base_address, base_address + WORK_CAP),
        "work_guard": (base_address + WORK_CAP, base_address + WORK_CAP + 4),
        "size_table": (
            base_address + TABLE_OFFSET,
            base_address + TABLE_OFFSET + TABLE_BYTES,
        ),
        "size_table_guard": (
            base_address + TABLE_OFFSET + TABLE_BYTES,
            base_address + TABLE_OFFSET + TABLE_BYTES + 4,
        ),
        "temporary_table": (
            base_address + TABLE_TEMP_OFFSET,
            base_address + TABLE_TEMP_OFFSET + TABLE_BYTES,
        ),
        "temporary_table_guard": (
            base_address + TABLE_TEMP_OFFSET + TABLE_BYTES,
            base_address + TABLE_TEMP_OFFSET + TABLE_BYTES + 4,
        ),
        "allocation_end_guard": (
            base_address + ALLOC_SIZE - 4,
            base_address + ALLOC_SIZE,
        ),
    }


def encode_layout_is_safe(layout: dict[str, tuple[int, int]]) -> bool:
    allocation = layout["allocation"]
    payload_names = tuple(name for name in layout if name != "allocation")
    if allocation[0] & 0x3FF:
        return False
    for name in ("work", "size_table", "temporary_table"):
        if layout[name][0] & 0x3FF:
            return False
    for name in payload_names:
        start, end = layout[name]
        if not allocation[0] <= start < end <= allocation[1]:
            return False
    ordered = sorted((layout[name][0], layout[name][1]) for name in payload_names)
    return all(left_end <= right_start for (_, left_end), (right_start, _) in zip(ordered, ordered[1:]))


def power_preflight_success(values: list[int] | tuple[int, ...]) -> bool:
    return (
        len(values) >= STATE_WORDS
        and values[S_COUNT] >= 1
        and values[S_DONE] == DONE_MAGIC
        and values[S_RESTORED] == base.HOOK_ORIG
        and values[S_PHASE] == PHASE_PREFLIGHT
        and values[S_STAGE] == 7
        and values[S_ERROR] == 0
        and values[S_CLEANUP_ERROR] == 0
        # 0xC37CF87C selects the engine's owner: 0 = closed, 1 or 2 = open
        # and held by that client.  Only the close routine C062FF68 writes 0,
        # so non-zero is normal whenever the engine is open.  [C 2026-09-29]
        and values[S_CODEC_MODE] <= 2
        and values[S_R2] == 2
        and values[S_LR] == base.EXPECTED_LR
        and values[S_BUFFER] != 0
        and values[S_LENGTH] == 0x00318200
        and values[S_SOURCE] != 0
        and values[S_SOURCE] & 0x3FF == 0
        and values[S_PWR_ON] == 0
        and values[S_CLK_ON] == 0
        and values[S_CLK_OFF] == 0
        and values[S_PWR_OFF] == 0
        and values[S_PREFLIGHT] == PREFLIGHT_MAGIC
    )


def require_power_preflight(shell: base.CameraShell) -> list[int]:
    """Read-only proof gate reserved for a future live encode implementation."""
    values = shell.read_words(STATE, STATE_WORDS)
    if not power_preflight_success(values):
        raise ProbeError("no successful exact-writer power preflight in state")
    if shell.read_word(base.HOOK_SITE) != base.HOOK_ORIG:
        raise ProbeError("power preflight hook is not restored")
    base.verify_call_context(shell)
    return values


def retained_scratch_present(values: list[int] | tuple[int, ...]) -> bool:
    """A nonzero retained handle must not be orphaned by clearing this state."""
    return (
        len(values) >= STATE_WORDS
        and values[S_PHASE] == PHASE_SCRATCH
        and (values[S_HANDLE] != 0 or values[S_SCRATCH_MAGIC] == SCRATCH_MAGIC)
    )


def scratch_preflight_success(values: list[int] | tuple[int, ...]) -> bool:
    if len(values) < STATE_WORDS:
        return False
    handle = values[S_HANDLE]
    if not 0x40000000 <= handle < 0x80000000 or handle & 0x3FF:
        return False
    end = handle + ALLOC_SIZE
    if end > 0x80000000:
        return False
    return (
        values[S_COUNT] >= 1
        and values[S_DONE] == DONE_MAGIC
        and values[S_RESTORED] == base.HOOK_ORIG
        and values[S_PHASE] == PHASE_SCRATCH
        and values[S_STAGE] == 7
        and values[S_ERROR] == 0
        and values[S_R2] == 2
        and values[S_LR] == base.EXPECTED_LR
        and values[S_BUFFER] != 0
        and values[S_LENGTH] == 0x00318200
        and values[S_ALLOCATOR] != 0
        and values[S_ALLOC_END] == end
        and values[S_WORK_BASE] == handle
        and values[S_WORK_END] == handle + WORK_CAP
        and values[S_WORK_GUARD] == handle + WORK_CAP
        and values[S_TABLE_BASE] == handle + TABLE_OFFSET
        and values[S_TABLE_TEMP] == handle + TABLE_TEMP_OFFSET
        and values[S_SCRATCH_SOURCE] != 0
        and values[S_SCRATCH_SOURCE] & 0x3FF == 0
        and values[S_SCRATCH_CODEC_MODE] <= 2   # owner selector, not busy
        and values[S_GUARD_WORK] == GUARD_MAGIC
        and values[S_GUARD_TABLE] == GUARD_MAGIC
        and values[S_GUARD_TEMP] == GUARD_MAGIC
        and values[S_PREFLIGHT] == PREFLIGHT_MAGIC
        and values[S_SCRATCH_MAGIC] == SCRATCH_MAGIC
        and values[S_END_GUARD] == end - 4
        and values[S_GUARD_END] == GUARD_MAGIC
        and values[S_TABLE_GUARD] == handle + TABLE_OFFSET + TABLE_BYTES
        and values[S_TEMP_GUARD] == handle + TABLE_TEMP_OFFSET + TABLE_BYTES
    )


def finish_arming(shell: base.CameraShell) -> bool:
    """Publish the written image, then arm. Always, in that order.

    There is no unpublished path. A read-back proves the data cache holds the
    bytes, not that the fetch path will run them, and cave.py's rule is that
    the caller passes the claimed address rather than the file naming one --
    a half-converted world is what hung getfile.S with the shell behind it.
    """
    return publish_then_arm(shell)["armed"]


def arm_power_preflight(shell: base.CameraShell, code: bytes) -> None:
    """Use the exact probe's guarded context and final-write transaction."""
    base.verify_call_context(shell)
    current = shell.read_words(STATE, STATE_WORDS)
    if retained_scratch_present(current):
        raise ProbeError(
            "retained scratch is active; encode/free it or power-cycle before "
            "clearing state"
        )
    shell.write_words_verified(CODE, base.words_from(code))
    shell.write_words_verified(STATE, [0] * STATE_WORDS)
    armed = finish_arming(shell)
    if armed:
        print("power-only preflight armed and verified")
        print("record one short FHD CinemaDNG clip, then run: status")
    else:
        print("preflight fired during arm and already restored; run: status")


def arm_scratch_preflight(shell: base.CameraShell, code: bytes) -> None:
    """Chain a retained allocation to a verified PPWR result, once per boot."""
    require_power_preflight(shell)
    seeded_state = [0] * STATE_WORDS
    seeded_state[S_PREFLIGHT] = PREFLIGHT_MAGIC

    shell.write_words_verified(CODE, base.words_from(code))
    shell.write_words_verified(STATE, seeded_state)
    armed = finish_arming(shell)
    if armed:
        print("scratch-only preflight armed and verified")
        print("record one short FHD CinemaDNG clip, then run: status")
    else:
        print("scratch preflight fired during arm and already restored; run: status")


def read_life_record(shell: base.CameraShell, handle: int, slot: str) -> dict:
    """One lifecycle record, decoded, exactly as the camera wrote it.

    Read at the CACHED address, which is the view the shell has; the camera
    writes these through the uncached alias and the result words at RESULT_OFF
    have been read back this way since C2.
    """
    if slot not in LIFE_SLOT:
        raise ProbeError(f"no such record slot {slot!r}")
    words = shell.read_words(handle + LIFE_OFF + LIFE_SLOT[slot], LIFE_WORDS)
    return {
        "slot": slot,
        "present": words[L_MAGIC] == LIFE_MAGIC,
        "role": words[L_ROLE],
        "allocator": words[L_ALLOC],
        "handle": words[L_HANDLE],
        "bytes": words[L_BYTES],
        "end": words[L_END],
        "acquired_tick": words[L_TICK],
        "acquired_seq": words[L_SEQ],
        "purpose": PURPOSE.get(words[L_PURPOSE], f"0x{words[L_PURPOSE]:08X}"),
        "own_mark": words[L_OWNMARK] == OWN_MAGIC,
        "held_tick": words[L_HELD],
        "state": LIFE_STATE.get(words[L_STATE], words[L_STATE]),
        "state_word": words[L_STATE],
        "boot": words[L_BOOT],
        "free_result": words[L_FREERET],
        "free_tick": words[L_FREETICK],
        "raw": words,
    }


def read_block_mark(shell: base.CameraShell, handle: int) -> dict:
    """The mark inside a block: the magic plus the block's own address.

    This is the part a host cannot fake by typing a handle, and it is read
    from the block being described rather than from the record describing it.
    """
    words = shell.read_words(handle + OWN_OFF, 2)
    return {"magic_ok": words[0] == OWN_MAGIC,
            "names_itself": words[1] == handle,
            "raw": words}


def read_lifecycle(shell: base.CameraShell, handle_a: int) -> dict:
    """Both records plus both marks: acquisition to reclaim, in one place."""
    out = {"block_a": read_life_record(shell, handle_a, "A"),
           "block_b": read_life_record(shell, handle_a, "B")}
    out["mark_a"] = read_block_mark(shell, handle_a)
    handle_b = out["block_b"]["handle"]
    if out["block_b"]["present"] and handle_b:
        out["mark_b"] = read_block_mark(shell, handle_b)
        out["disjoint"] = (handle_a + ALLOC_SIZE <= handle_b
                           or handle_b + ALLOC_SIZE <= handle_a)
    return out


def check_life_record(record: dict, mark: dict, handle: int, role: int,
                      boot: int = None) -> None:
    """Refuse a record that does not describe the block it sits in.

    Repeated on the host although the camera checks the same things, for the
    same reason the old handle argument was checked twice: this one decides
    whether a 4 MiB block is handed to a DMA engine.
    """
    where = f"block {'AB'[role - 1]}"
    if not record["present"]:
        raise ProbeError(f"{where}: no lifecycle record (the camera writes it "
                         "at acquisition; run scratch, then acquire-second)")
    if record["role"] != role:
        raise ProbeError(f"{where}: record says role {record['role']}")
    if record["state_word"] != LIFE_HELD:
        raise ProbeError(f"{where}: record state is {record['state']!r}, not "
                         "held; nothing may use or free it on this evidence")
    if record["handle"] != handle:
        raise ProbeError(f"{where}: record names 0x{record['handle']:08X}, "
                         f"not 0x{handle:08X}")
    if not record["allocator"]:
        raise ProbeError(f"{where}: record has no allocator, so nothing could "
                         "free it")
    if record["bytes"] != ALLOC_SIZE or record["end"] != handle + ALLOC_SIZE:
        raise ProbeError(f"{where}: record says {record['bytes']:,} bytes "
                         f"ending 0x{record['end']:08X}, which is not the "
                         f"{ALLOC_SIZE:,} we ask for")
    if not record["own_mark"]:
        raise ProbeError(f"{where}: the record does not carry the mark magic")
    # The boot stamp. Everything else in this record survives a short power
    # cycle intact -- DRAM keeps its contents -- so a record from the previous
    # boot names itself, carries the mark, has the right role and says held.
    # On 2026-09-30 one of those let an arm through on a block this boot never
    # owned, and the only thing that caught it was reading the ticks by hand.
    #
    # The identity is PASSED IN, not read here: this function decides, it does
    # not do I/O, and a validator that talks to the camera cannot be tested
    # against a record someone hands it.
    if boot is not None and record.get("boot") != boot:
        raise ProbeError(
            f"{where}: the record was written by boot 0x{record.get('boot', 0):08X} "
            f"and this is boot 0x{boot:08X}. A power cycle frees every "
            "allocation, so this record describes a block nobody holds")
    if not mark["magic_ok"] or not mark["names_itself"]:
        raise ProbeError(
            f"{where}: the block does not hold our mark at +0x{OWN_OFF:X} "
            f"(read {[hex(w) for w in mark['raw']]}). The record describes a "
            "block; this is the block. They disagree, so it is not ours")


def acquire_second_block(shell: base.CameraShell, fpsup: pathlib.Path,
                         call=None) -> dict:
    """Have the CAMERA acquire the bound probe's second block and record it.

    Nothing about this block reaches the camera from the host: it calls the
    allocator, checks the shape, checks it does not overlap block A, writes a
    mark into it and reads that mark back, and writes the record the hook image
    later reads. The host's part is to run it and then check its work.

    Runs in task context through callfn, like release's F_FREE call, with no
    frame in flight and nothing armed.
    """
    values = shell.read_words(STATE, STATE_WORDS)
    if not scratch_preflight_success(values):
        raise ProbeError(
            "acquiring the second block requires a complete, error-free "
            "scratch preflight in state; run scratch first")
    handle_a = values[S_HANDLE]
    before = read_lifecycle(shell, handle_a)
    boot = shell.read_words(LOAD_DONE_US, 1)[0]
    check_life_record(before["block_a"], before["mark_a"], handle_a,
                      role=1, boot=boot)
    if before["block_b"]["present"] and before["block_b"]["state_word"] == LIFE_HELD:
        raise ProbeError(
            f"a second block is already recorded as held "
            f"(0x{before['block_b']['handle']:08X}); release it before "
            "acquiring another, or this one becomes an orphan no record names")

    code, placed = place_and_build_acquire(fpsup)
    entry = placed[CLAIM_NAMES["code"]]
    print(f"acquire routine  {len(code)} bytes at 0x{entry:08X}")
    shell.write_words_verified(entry, base.words_from(code))
    if call is None:
        call = native_caller()
    published = placement.publish(call)
    for item in published:
        print(f"published {item['function']} -> {item['result']}")
    returned, status = call(entry)
    if not returned:
        raise ProbeError(
            "the acquire routine did not return. Do not re-run it and do not "
            "release: a block may have been allocated with no record. "
            "Power-cycle to reclaim")
    status &= 0xFFFFFFFF
    meaning = ACQ_STATUS.get(status, f"unknown status {status}")
    print(f"acquire status   {status} ({meaning})")
    after = read_lifecycle(shell, handle_a)
    if status != 0:
        return {"acquired": False, "status": status, "meaning": meaning,
                "lifecycle": after}
    record, handle_b = after["block_b"], after["block_b"]["handle"]
    check_life_record(record, after.get("mark_b", {"magic_ok": False,
                                                  "names_itself": False,
                                                  "raw": [0, 0]}),
                      handle_b, role=2)
    if not after.get("disjoint"):
        raise ProbeError(
            f"the recorded blocks overlap: 0x{handle_a:08X} and "
            f"0x{handle_b:08X}. The camera refuses this too; a record saying "
            "otherwise means one of the two is not what it claims")
    print(f"block A  0x{handle_a:08X}..0x{handle_a + ALLOC_SIZE:08X}  "
          f"{before['block_a']['purpose']}")
    print(f"block B  0x{handle_b:08X}..0x{handle_b + ALLOC_SIZE:08X}  "
          f"{record['purpose']}")
    print("both carry the mark, both records say held, both ours to release")
    return {"acquired": True, "status": 0, "handle_a": handle_a,
            "handle_b": handle_b, "lifecycle": after}


def arm_bound_probe_two(shell: base.CameraShell, code: bytes) -> None:
    """Arm the two-block bound probe against the block the camera acquired.

    There is no source-handle argument any more. It used to be required, and
    the operator carried it forward from an earlier run's log; every check on
    it - the 0x4..0x7 quarter, 1 KiB alignment, non-overlap with block A - said
    something about the shape of a number and nothing about whether the memory
    belonged to us. Now the camera acquires the block itself in this boot
    (`acquire-second`) and records it, and both the record and the mark inside
    the block are checked here and again on the camera before the generated
    source is written into it.
    """
    values = shell.read_words(STATE, STATE_WORDS)
    if not scratch_preflight_success(values):
        raise ProbeError(
            "the bound probe requires a complete, error-free scratch preflight "
            "in state; run scratch first")
    first = values[S_HANDLE]
    life = read_lifecycle(shell, first)
    check_life_record(life["block_a"], life["mark_a"], first, role=1)
    second = life["block_b"]["handle"]
    if not life["block_b"]["present"]:
        raise ProbeError(
            "no second block is recorded. Run `acquire-second` first: the "
            "camera allocates it, checks it and writes the record this probe "
            "reads. Passing a handle by hand is no longer possible, which is "
            "the point")
    check_life_record(life["block_b"],
                      life.get("mark_b", {"magic_ok": False,
                                          "names_itself": False, "raw": [0, 0]}),
                      second, role=2)
    if not life["disjoint"]:
        raise ProbeError(
            f"the recorded blocks overlap: 0x{first:08X} and 0x{second:08X}")
    if PROBE_DECLARED + 0x404 > WORK_CAP:
        raise ProbeError("the guards would sit above WORK_CAP")
    if PROBE_SRC_BYTES > WORK_CAP:
        raise ProbeError("the generated source does not fit below WORK_CAP")

    # The seeded state carries block A only, exactly as before. Block B is not
    # seeded anywhere: the probe reads it from the record.
    seeded_state = [0] * STATE_WORDS
    seeded_state[S_PREFLIGHT] = PREFLIGHT_MAGIC
    seeded_state[S_SCRATCH_MAGIC] = SCRATCH_MAGIC
    seeded_state[S_ALLOCATOR] = values[S_ALLOCATOR]
    seeded_state[S_HANDLE] = first

    print(f"destination 0x{first:08X}, guards at +0x{PROBE_DECLARED:X}")
    print(f"source      0x{second:08X}, {PROBE_SRC_BYTES:,} bytes generated "
          "on the camera")
    print(f"records     A acquired at tick {life['block_a']['acquired_tick']}, "
          f"B at tick {life['block_b']['acquired_tick']}; both marked, both held")
    # Clear the result words. The state block is seeded from scratch every time
    # it is armed; these are not, and they live in a retained block, so a run
    # that fails before reading the guards would otherwise report the PREVIOUS
    # run's five words as this run's - unchanged guards being exactly what a
    # pass looks like. Cleared here, not on the camera, because the image has
    # twenty bytes of headroom and the host is already writing at arming time.
    shell.write_words_verified(first + RESULT_OFF + 0x14, [0] * 8)
    shell.write_words_verified(CODE, base.words_from(code))
    shell.write_words_verified(STATE, seeded_state)
    armed = finish_arming(shell)
    if armed:
        print("bound probe armed; record one short FHD CinemaDNG clip")
    else:
        print("it fired during arming and already restored; run: status")


def arm_live_encode(shell: base.CameraShell, code: bytes) -> None:
    """One encode into the block the scratch phase retained, then release it.

    Nothing is allocated. The seeded handle is only the scratch result's own
    handle, and the probe still re-derives the whole layout and re-checks every
    guard before it lets the codec touch the block.
    """
    values = shell.read_words(STATE, STATE_WORDS)
    if not scratch_preflight_success(values):
        raise ProbeError(
            "live encode requires a complete, error-free scratch preflight in "
            "state; run scratch first, or power-cycle and redo both preflights"
        )
    seeded_state = [0] * STATE_WORDS
    seeded_state[S_PREFLIGHT] = PREFLIGHT_MAGIC
    seeded_state[S_SCRATCH_MAGIC] = SCRATCH_MAGIC
    seeded_state[S_ALLOCATOR] = values[S_ALLOCATOR]
    seeded_state[S_HANDLE] = values[S_HANDLE]

    shell.write_words_verified(CODE, base.words_from(code))
    shell.write_words_verified(STATE, seeded_state)
    armed = finish_arming(shell)
    if armed:
        print("live encode armed and verified against the retained block")
        print(f"allocator 0x{values[S_ALLOCATOR]:08X}  handle 0x{values[S_HANDLE]:08X}")
        print("record one short FHD CinemaDNG clip, then run: status")
    else:
        print("live encode fired during arm and already restored; run: status")


def apply_placement(kind: str, fpsup: pathlib.Path, claim=None):
    """Claim cave blocks, build there, and point the shared base at them.

    Returns (code, placed). After this the reviewed arming/restore routines in
    exact_dng_writer_probe operate on the claimed addresses, because they read
    CODE/STATE/HOOK_ARMED as module globals and this rebinds those once.
    """
    code, placed = place_and_build(kind, fpsup, claim=claim)
    base.configure_placement(placed[CLAIM_NAMES["code"]],
                             placed[CLAIM_NAMES["state"]])
    global STATE, CODE, LOG
    STATE = placed[CLAIM_NAMES["state"]]
    CODE = placed[CLAIM_NAMES["code"]]
    if CLAIM_NAMES["log"] in placed:
        LOG = placed[CLAIM_NAMES["log"]]
    return code, placed


def release_retained_scratch(shell: base.CameraShell, call=None) -> dict:  # noqa: C901
    """Free a retained scratch block and record that it happened.

    The chain could retain a 4 MiB block but had no way to give it back, so
    the only recoveries were a power cycle or an out-of-band F_FREE that the
    state block never learned about -- which then left the retained-scratch
    guard refusing forever on a block that no longer exists.

    The two documented-safe cases are allowed, and only those:

      * the codec was never entered for this block; or
      * it was entered and returned success, which is what --keep produces.

    Entered without success means the result is unknown and the engine may
    still own the buffer as a DMA target. That block is not freed here at all;
    a power cycle is the answer (ROADMAP 5.4).
    """
    values = shell.read_words(STATE, STATE_WORDS)
    # A block can be retained in two states: the scratch phase allocated it and
    # nothing consumed it, or a --keep encode consumed it and deliberately did
    # not free it. The arming guard only knows the first, because that is the
    # one it must refuse to orphan; release has to know both.
    retained = (retained_scratch_present(values)
                or (values[S_PHASE] in RETAINING_PHASES
                    and values[S_SCRATCH_MAGIC] == SCRATCH_MAGIC
                    and values[S_HANDLE] != 0
                    and values[S_FREE_DONE] != FREE_MAGIC))
    if not retained:
        raise ProbeError("no retained scratch recorded in state")
    if values[S_FREE_DONE] == FREE_MAGIC:
        return {"already_released": True}
    entered = bool(values[S_ENCODE_STARTED])
    succeeded = values[S_ENC_RET] == 1 and values[S_ERROR] == 0
    if entered and not succeeded:
        raise ProbeError(
            f"the codec was entered and did not succeed (enc_ret="
            f"{values[S_ENC_RET]}, error={values[S_ERROR]}); it may still own "
            "the buffer. Power-cycle instead of freeing")
    allocator, handle = values[S_ALLOCATOR], values[S_HANDLE]
    if not allocator or not 0x40000000 <= handle < 0x80000000 or handle & 0x3FF:
        raise ProbeError(
            f"refusing to free allocator 0x{allocator:08X} handle "
            f"0x{handle:08X}: not the shape the allocator hands out")
    if call is None:
        call = native_caller()

    # The SECOND block first, and for a mechanical reason: its record lives
    # inside the first one. Free A first and the only handle by which B could
    # ever be freed goes with it.
    #
    # Whether the codec may still own a buffer was decided above, for both:
    # entered-and-not-succeeded refuses the whole reclaim rather than freeing
    # the block that happens not to have been the destination. The engine was
    # handed both - one as source, one as destination - so "it may still be
    # reading" applies to both.
    second = None
    try:
        record_b = read_life_record(shell, handle, "B")
    except (ProbeError, base.ProbeError) as exc:
        record_b = None
        print(f"note: block B's record could not be read ({exc}); freeing only "
              "the first block, and B, if it exists, needs a power cycle")
    if record_b and record_b["present"] and record_b["state_word"] == LIFE_HELD:
        handle_b, allocator_b = record_b["handle"], record_b["allocator"]
        mark_b = read_block_mark(shell, handle_b)
        check_life_record(record_b, mark_b, handle_b, role=2)
        returned_b, result_b = call(F_FREE, r0=allocator_b, r1=handle_b)
        if not returned_b:
            raise ProbeError(
                "F_FREE did not return for the second block. Block A has NOT "
                "been freed and neither record has been changed; do not retry, "
                "power-cycle")
        _write_life_field(shell, handle, "B", L_FREERET, result_b & 0xFFFFFFFF)
        _write_life_field(shell, handle, "B", L_STATE, LIFE_FREED)
        second = {"allocator": allocator_b, "handle": handle_b,
                  "free_result": result_b & 0xFFFFFFFF, "recorded": True}
    elif record_b and record_b["present"]:
        second = {"handle": record_b["handle"], "skipped": record_b["state"]}

    # Block A's own record lives INSIDE block A, so its reclaim cannot be
    # recorded after the free: once the allocator has it back, those words
    # belong to whoever gets the block next. It is therefore marked freed
    # BEFORE the call, and the state block - which survives - carries the
    # confirmation afterwards. If the free then does not return, the record
    # says freed while the block may still be held, and that is the safe
    # direction: freed means nothing may use it again.
    if read_life_record(shell, handle, "A")["state_word"] == LIFE_HELD:
        _write_life_field(shell, handle, "A", L_STATE, LIFE_FREED)
    returned, result = call(F_FREE, r0=allocator, r1=handle)
    if not returned:
        raise ProbeError("F_FREE did not return; do not retry, power-cycle")
    # Record it only after the free returned. Without this the retained-scratch
    # guard refuses forever on a block that no longer exists, which is exactly
    # what an out-of-band free did on 2026-09-23.
    shell.write_words_verified(STATE + S_FREE_DONE * 4, [FREE_MAGIC])
    return {"allocator": allocator, "handle": handle, "entered": entered,
            "free_result": result & 0xFFFFFFFF, "recorded": True,
            "second_block": second}


def _write_life_field(shell: base.CameraShell, handle_a: int, slot: str,
                      field: int, value: int) -> None:
    """The one place the host writes into a record, and only after a free.

    Acquisition fields are the camera's and are never touched here. What is
    written is the outcome of the reclaim the host just performed, which no
    camera-side code is in a position to record - and leaving it unwritten is
    what made a stale guard refuse forever on a block that no longer existed.
    """
    if field not in (L_STATE, L_FREERET, L_FREETICK):
        raise ProbeError(f"field {field} is the camera's to write, not ours")
    at = handle_a + LIFE_OFF + LIFE_SLOT[slot] + field * 4
    shell.write_words_verified(at, [value])


def read_copyback_result(shell: base.CameraShell, handle: int) -> dict:
    """The three words PHASE=7 leaves in the retained block.

    `fit` is the verdict: 1 means the file was copied into the camera's own
    frame buffer and seg[1] changed, 0 means it did not fit and that frame
    writes as stock RAW. Nothing else writes these, and unlike the state block
    they do not collide with a gate.

    `t_after` is a clock reading taken once the copy is done. Subtract S_T1,
    the end of the encode, to get everything that happens after it: the
    78,848-byte header copy, the template patch, the payload copy and the
    stores. That is the figure the frame budget needs.
    """
    words = shell.read_words(handle + RESULT_OFF, 4)
    return {"file_bytes": words[0], "copy_bytes": words[1], "fit": words[2],
            "t_after": words[3]}


def _source_hash(variant: str, bound_criterion) -> str:
    """The hash of the pattern THIS variant generates, or a statement instead.

    A variant whose source is the camera's own frame has no host-reproducible
    pattern. It gets a sentence, not a hash: the criterion then refuses to
    compare it against a demand, which is correct - a bound verdict on a source
    the host cannot reproduce is not available.
    """
    generator = bound_criterion.SOURCE_GENERATORS.get(variant, "unknown")
    if generator == "unknown":
        raise ProbeError(f"no source generator is recorded for {variant!r}")
    if generator is None:
        return "not host-reproducible: the source was the camera's own frame"
    return hashlib.sha256(generator()).hexdigest()


ASYNC_FRAMES = 8
ASYNC_OFF = 0x3F6000
ASYNC_MAGIC = 0x31595341            # "ASY1"
ASYNC_WORDS = 0x20                  # header + the request area
ASYNC_STAGES = {0: "never ran", 1: "F_INIT done, engine block read, nothing submitted",
                2: "submitted, not yet seen to finish", 3: "complete",
                4: "F_SIZE refused", 5: "F_INIT refused",
                6: "the completion flag could not be created; nothing submitted"}


def read_async_result(shell: base.CameraShell, handle: int) -> dict:
    """What PHASE=11 recorded: the engine's parameter block as F_INIT left it.

    The six fields are the whole point. tools/codec_request.py assumes
    +0x00 source, +0x08 destination, +0x0C the declared length, +0x10 the size
    table and +0x14 its length, with +0x04 unexplained - all read out of the
    driver's use of them, never measured. This is the measurement.
    """
    w = shell.read_words(handle + ASYNC_OFF, 20)   # ..A_START_RET at 0x4C
    if w[0] != ASYNC_MAGIC:
        raise ProbeError(
            f"no async record at 0x{handle + ASYNC_OFF:08X}: magic reads "
            f"0x{w[0]:08X}. The area was never seeded, so nothing here is this "
            "probe's - which is exactly how the first run's 'engine fields' "
            "turned out to be another owner's pixels")
    stage = w[1]
    out = {"stage": stage, "stage_name": ASYNC_STAGES.get(stage, stage),
           "init_ret": w[2],
           "engine": {"+0x00": f"0x{w[3]:08X}", "+0x04": f"0x{w[4]:08X}",
                      "+0x08": f"0x{w[5]:08X}", "+0x0C": f"0x{w[6]:08X}",
                      "+0x10": f"0x{w[7]:08X}", "+0x14": f"0x{w[8]:08X}"},
           "flag_id": f"0x{w[9]:08X}",
           "submit_ret": w[10],
           # C062FFF8 only PROGRAMS the engine; the GO bit is
           # [0x300D0000] |= 2 and only the mode-1 starter C0630208 sets it.
           # 0 = started, 1 = the owner word was not 1.  Proven live on
           # 2026-09-29: start_ret 0 and the encode completed.  [L]
           "start_ret": w[19],
           "t_submit": w[11], "polls": w[12],
           "last_twai_return": w[13] - (1 << 32) if w[13] >> 31 else w[13],
           "pattern": w[14], "t_done": w[15],
           "size_ret": w[16], "sum": w[17]}
    if stage >= 2:
        out["microseconds_to_completion"] = (w[15] - w[11]) & 0xFFFFFFFF
    return out


def seed_async_record(shell: base.CameraShell, handle: int) -> None:
    """Clear the record area and stamp it, before arming.

    The area is inside the retained block and the allocator does not zero it.
    Without this the probe reads a previous owner's bytes as its own state.
    """
    shell.write_words_verified(handle + ASYNC_OFF, [ASYNC_MAGIC] + [0] * (ASYNC_WORDS - 1))
    if shell.read_words(handle + ASYNC_OFF, 1)[0] != ASYNC_MAGIC:
        raise ProbeError("the async record did not read back")


def read_bound_result(shell: base.CameraShell, fpsup: pathlib.Path,
                      variant: str = "bound-probe-two") -> dict:
    """Everything a bound verdict needs, in one record, before judging it.

    Saved whole and saved once: the raw state block, every tile size, all three
    return codes, the error word, the five guard words, the declared length as a
    constant AND as the value read from the firmware's own variable, both
    lifecycle records, and hashes of the pattern and of the image that ran. A
    partial record is what lets a run be re-read later as whatever the reader
    hoped for, and the criterion in tools/bound_criterion.py refuses a record
    with anything missing rather than filling it in.

    This reads. It decides nothing - bound_criterion.verdict does that, and it
    was written before any of these runs.
    """
    values = shell.read_words(STATE, STATE_WORDS)
    handle = values[S_HANDLE]
    if not handle:
        raise ProbeError("no block A handle in state; nothing to read")
    result = shell.read_words(handle + RESULT_OFF, 14)
    code = build_image(variant, fpsup)
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]
                           / "projects/lossless-sup/tools"))
    import bound_criterion

    tiles = list(values[S_TILE0:S_TILE0 + TILE_COUNT])
    return {
        "variant": variant,
        "image_sha256": hashlib.sha256(code).hexdigest(),
        "image_bytes": len(code),
        # The pattern is generated on the camera from a few constants, so the
        # host can reproduce it exactly and the criterion checks the two agree.
        # Which generator depends on the variant: hashing the LCG's output for
        # a run whose source was the camera's own frame would put a number in
        # the record that describes data that run never saw.
        "source_sha256": _source_hash(variant, bound_criterion),
        "state": list(values),
        "stage": values[S_STAGE],
        "error": values[S_ERROR],
        "init_ret": values[S_INIT_RET],
        "enc_ret": values[S_ENC_RET],
        "size_ret": values[S_SIZE_RET],
        "tile_count": values[S_TILE_COUNT],
        "tile_sizes": tiles,
        "tile_sum": sum(tiles),
        "compressed_size": values[S_COMPRESSED],
        "encode_microseconds": (values[S_T1] - values[S_T0]) & 0xFFFFFFFF,
        "guards": result[5:10],
        "declared_constant": result[10],
        "declared_readback_address": result[11],
        "declared_readback_value": result[12],
        "lifecycle": read_lifecycle(shell, handle),
    }


def build_ifd_template(reference_frame: pathlib.Path):
    """The 1 KiB IFD block the probe copies, plus where its tile tables sit.

    Every frame of this format shares one IFD: the entries are byte-identical
    across frames and only the out-of-line values differ, which the probe
    copies verbatim from the frame it is writing. Verified on two frames of
    clip A001_281, 58 tags, zero differing entries.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]
                           / "projects/lossless-sup/tools"))
    import canonical_header as ch
    header = reference_frame.read_bytes()[:ch.IFD_AT]
    placeholder = [1] * 12
    built = ch.build(header, placeholder, TILE_W, TILE_H)
    block = built["segment0"][IFD_AT:IFD_AT + TEMPLATE_BYTES]
    if len(block) != TEMPLATE_BYTES:
        raise ProbeError("template block is the wrong size")
    offset_table = IFD_AT + built["ifd_size"]
    count_table = offset_table + 4 * 12
    if count_table + 4 * 12 > OUT_OFF:
        raise ProbeError("tile tables do not fit below the payload")
    return block, offset_table, count_table


def arm_compress(shell: base.CameraShell, code: bytes,
                 reference_frame: pathlib.Path) -> None:
    """Load the IFD template into the arena, then arm the compressed write."""
    values = shell.read_words(STATE, STATE_WORDS)
    if not scratch_preflight_success(values):
        raise ProbeError("compress requires a complete scratch preflight")
    handle = values[S_HANDLE]
    block, offset_table, count_table = build_ifd_template(reference_frame)

    words = list(struct.unpack(f"<{TEMPLATE_BYTES // 4}I", block))
    print(f"loading {TEMPLATE_BYTES}-byte IFD template into the arena")
    shell.write_words_verified(handle + TEMPLATE_OFF, words)
    shell.write_words_verified(handle + TEMPLATE_META, [offset_table, count_table])
    print(f"tile tables at +0x{offset_table:X} / +0x{count_table:X}")

    seeded = [0] * STATE_WORDS
    seeded[S_PREFLIGHT] = PREFLIGHT_MAGIC
    seeded[S_SCRATCH_MAGIC] = SCRATCH_MAGIC
    seeded[S_ALLOCATOR] = values[S_ALLOCATOR]
    seeded[S_HANDLE] = handle
    shell.write_words_verified(CODE, base.words_from(code))
    shell.write_words_verified(STATE, seeded)
    armed = finish_arming(shell)
    print("compressed write armed" if armed else "fired during arm; run status")


def native_caller(fpsh: pathlib.Path = None):
    """A callable that runs one firmware function in task context via callfn."""
    import sys
    shell_dir = placement.SHELL_DIR
    if str(shell_dir) not in sys.path:
        sys.path.insert(0, str(shell_dir))
    import callfn

    def call(function, **registers):
        return callfn.call(function, verbose=False, **registers)
    return call


def publish_then_arm(shell: base.CameraShell, call=None) -> dict:
    """Make the written image executable, then arm. Never the other way round.

    A verified read-back proves the data cache holds the bytes. It says
    nothing about what the instruction fetch path will run, and a barrier is
    not a cache clean. If publication fails the hook is NOT armed: a half
    published hook may or may not run, which is the one state with no safe
    recovery.
    """
    if call is None:
        call = native_caller()
    published = placement.publish(call)
    # A mandatory step that prints nothing cannot be audited afterwards, and
    # this one is the difference between an image the fetch path runs and one
    # it may not.
    for entry in published:
        print(f"published {entry['function']} -> {entry['result']}")
    base.verify_call_context(shell)
    armed = base.arm_site(shell)
    return {"published": published, "method": placement.PUBLICATION_METHOD,
            "armed": armed}


def unfired_seed_present(values: list[int] | tuple[int, ...]) -> bool:
    """A seeded, never-fired block from an earlier arm of this same phase.

    Re-arming over it swaps the image without allocating a second 4 MiB block
    and without discarding a result: both proofs must be present, the handle
    must still look like the allocator's, and nothing may have run yet.
    """
    if len(values) < STATE_WORDS:
        return False
    handle = values[S_HANDLE]
    return (
        values[S_PREFLIGHT] == PREFLIGHT_MAGIC
        and values[S_SCRATCH_MAGIC] == SCRATCH_MAGIC
        and values[S_ALLOCATOR] != 0
        and 0x40000000 <= handle < 0x80000000
        and handle & 0x3FF == 0
        and values[S_COUNT] == 0
        and values[S_FRAMES_DONE] == 0
        and values[S_DONE] == 0
    )


def arm_sustained_encode(shell: base.CameraShell, code: bytes,
                         reuse_seed: bool = False) -> None:
    """Encode FRAMES consecutive frames into the retained block, then release.

    The probe re-arms itself between frames, so unlike the one-shot images an
    interrupted run leaves the site armed. Always finish with `status` and, if
    it still reports armed, `restore`.
    """
    values = shell.read_words(STATE, STATE_WORDS)
    if reuse_seed:
        if not unfired_seed_present(values):
            raise ProbeError(
                "--reuse-seed needs an unfired seed from an earlier arm of this "
                "phase: both proofs, a plausible handle, and nothing run yet"
            )
        print("reusing the retained block; no second allocation")
    elif not scratch_preflight_success(values):
        raise ProbeError(
            "sustained encode requires a complete, error-free scratch preflight "
            "in state; run scratch first, or power-cycle and redo both preflights"
        )
    seeded_state = [0] * STATE_WORDS
    seeded_state[S_PREFLIGHT] = PREFLIGHT_MAGIC
    seeded_state[S_SCRATCH_MAGIC] = SCRATCH_MAGIC
    seeded_state[S_ALLOCATOR] = values[S_ALLOCATOR]
    seeded_state[S_HANDLE] = values[S_HANDLE]

    shell.write_words_verified(CODE, base.words_from(code))
    # Stale records must not be readable as results.
    shell.write_words_verified(LOG, [0] * (AGG_OFF // 4 + AGG_WORDS))
    shell.write_words_verified(STATE, seeded_state)
    base.verify_call_context(shell)
    armed = base.arm_site(shell)
    if armed:
        print(f"sustained encode armed; runs until recording stops "
              f"(ceiling {SUSTAINED_FRAMES} frames)")
        print(f"allocator 0x{values[S_ALLOCATOR]:08X}  handle 0x{values[S_HANDLE]:08X}")
        print("record one clip of at least a second, then run: status")
    else:
        print("sustained encode fired during arm; run: status")


def read_aggregates(shell: base.CameraShell) -> dict[str, int]:
    """Worst case over every frame of the run, whatever its length."""
    words = shell.read_words(LOG + AGG_OFF, AGG_WORDS)
    return {"max_elapsed_us": words[0], "max_compressed_bytes": words[1],
            "frames_over_budget": words[2]}


def read_log(shell: base.CameraShell, frames: int) -> list[dict[str, int]]:
    """One 16-byte record per frame: elapsed, compressed, buffer, first pixel.

    The last two identify the frame. Identical compressed sizes across records
    mean nothing unless the buffer or the pixel word differ.
    """
    frames = min(frames, RING_FRAMES)
    words = shell.read_words(LOG, frames * LOG_WORDS_PER_FRAME)
    records = []
    for index in range(frames):
        base_index = index * LOG_WORDS_PER_FRAME
        elapsed, compressed, buffer_address, pixel = words[base_index:base_index + 4]
        if not elapsed and not compressed and not buffer_address:
            continue  # never written
        records.append({"frame": index, "elapsed_us": elapsed,
                        "compressed_bytes": compressed,
                        "buffer": buffer_address, "first_pixel": pixel})
    return records


def print_sustained_report(shell: base.CameraShell,
                           values: list[int] | tuple[int, ...]) -> None:
    done = values[S_FRAMES_DONE]
    print("")
    print(f"sustained encode: {done} frames encoded")
    aggregates = read_aggregates(shell)
    if done:
        worst = aggregates["max_elapsed_us"]
        print(f"  WORST frame            {worst} us  "
              f"({worst / BUDGET_US:.2f} of the 24p budget, "
              f"{'fits' if worst < BUDGET_US else 'OVER'})")
        print(f"  largest compressed     {aggregates['max_compressed_bytes']} bytes")
        print(f"  frames over budget     {aggregates['frames_over_budget']} of {done}")
    if values[S_ERROR]:
        print(f"  run ended on error {values[S_ERROR]} after {done} frames")
    print(f"  last {RING_FRAMES} frames in detail:")
    records = read_log(shell, RING_FRAMES)
    if not records:
        print("  no records written")
        return
    print("  frame  elapsed_us   Mpix/s  compressed   ratio      buffer   pixel0")
    pixels = 1936 * 1090
    times, ratios = [], []
    for record in records:
        elapsed = record["elapsed_us"]
        compressed = record["compressed_bytes"]
        rate = pixels / elapsed if elapsed else 0.0
        ratio = RAW_BYTES / compressed if compressed else 0.0
        if elapsed:
            times.append(elapsed)
            ratios.append(ratio)
        print(f"  {record['frame']:5d}  {elapsed:10d}  {rate:7.1f}  "
              f"{compressed:10d}  {ratio:6.3f}  0x{record['buffer']:08X}  "
              f"0x{record['first_pixel']:08X}")
    distinct_buffers = {record["buffer"] for record in records}
    distinct_pixels = {record["first_pixel"] for record in records}
    if len(records) > 1 and len(distinct_buffers) == 1 and len(distinct_pixels) == 1:
        print("  WARNING: every record has the same buffer AND the same first")
        print("  pixel. These may be re-encodes of one frame, not distinct frames.")
    else:
        print(f"  distinct buffers {len(distinct_buffers)}, "
              f"distinct first pixels {len(distinct_pixels)} of {len(records)}")
    if times:
        print(f"  elapsed us   min {min(times)}  max {max(times)}  "
              f"mean {sum(times) // len(times)}")
        print(f"  ratio        min {min(ratios):.3f}  max {max(ratios):.3f}")
        print(f"  worst frame is {max(times) / 41708:.2f} of the 24p budget "
              f"({'fits' if max(times) < 41708 else 'OVER'})")


def encode_report(values: list[int] | tuple[int, ...]) -> dict[str, object] | None:
    """Decode a finished PHASE=2 result. Returns None if this is not one."""
    if len(values) < STATE_WORDS or values[S_PHASE] != PHASE_ENCODE:
        return None
    if values[S_DONE] != DONE_MAGIC:
        return None
    report: dict[str, object] = {
        "error": values[S_ERROR],
        "stage": values[S_STAGE],
        "adopted": values[S_ADOPTED] == 1,
        "encode_entered": values[S_ENCODE_STARTED] == 1,
        "init_return": values[S_INIT_RET],
        "encode_return": values[S_ENC_RET],
        "size_return": values[S_SIZE_RET],
        "compressed_bytes": values[S_COMPRESSED],
        "tile_sizes": [values[S_TILE0 + n] for n in range(TILE_COUNT)],
        "block_released": values[S_FREE_DONE] == FREE_MAGIC,
        "release_refused": values[S_FREE_ERROR] == ERR_RETAINED,
        # Four sampled words only: the TIFF magic, the first and last pixel
        # words, and the segment length. This is NOT a full-buffer comparison
        # and must never be reported as byte-identical (roadmap P0-2). A real
        # proof needs a hash of all 3,244,544 bytes before and after, plus the
        # buffer identity, so two different ring slots cannot be compared.
        "sampled_source_markers_unchanged": (
            values[S_HDR_BEFORE] == values[S_HDR_AFTER]
            and values[S_PIX0_BEFORE] == values[S_PIX0_AFTER]
            and values[S_PIXN_BEFORE] == values[S_PIXN_AFTER]
        ),
        "source_words_compared": 4,
        "source_bytes_compared": 16,
        "source_bytes_total": 0x318200,
        "full_source_hash_available": False,
    }
    if values[S_ENC_RET] == 1 and values[S_ERROR] == 0:
        # The tick is a free-running 1 MHz counter, so a wrap is a plain
        # 32-bit subtraction. One frame only: this is not sustained throughput.
        elapsed = (values[S_T1] - values[S_T0]) & 0xFFFFFFFF
        report["elapsed_us"] = elapsed
        if 0 < elapsed < 10_000_000:
            pixels = 1936 * 1090
            report["mpix_per_second"] = round(pixels / elapsed, 1)
            report["frame_budget_24p_us"] = 41708
        if values[S_COMPRESSED]:
            report["ratio"] = round(RAW_BYTES / values[S_COMPRESSED], 3)
    return report


def print_encode_report(values: list[int] | tuple[int, ...]) -> None:
    report = encode_report(values)
    if report is None:
        return
    print("")
    print("live encode result")
    for key in ("error", "stage", "adopted", "encode_entered", "init_return",
                "encode_return", "size_return", "compressed_bytes", "ratio",
                "elapsed_us", "mpix_per_second", "frame_budget_24p_us",
                "sampled_source_markers_unchanged", "source_bytes_compared",
                "source_bytes_total", "block_released", "release_refused"):
        if key in report:
            print(f"  {key:22s} {report[key]}")
    print(f"  tile_sizes             {report['tile_sizes']}")
    if report["release_refused"]:
        print("  NOTE: the encode did not succeed, so the block was deliberately")
        print("        retained. Power-cycle the camera to reclaim it.")


def show_status(shell: base.CameraShell) -> None:
    site = shell.read_word(base.HOOK_SITE)
    values = shell.read_words(STATE, STATE_WORDS)
    if site == base.HOOK_ORIG:
        site_note = "original/restored"
    elif site == base.HOOK_ARMED:
        site_note = "armed"
    else:
        site_note = "UNKNOWN"
    print(f"hook                     0x{site:08X}  {site_note}")
    for offset, name in STATE_FIELDS.items():
        value = values[offset // 4]
        note = ""
        if name == "done":
            note = " DONE" if value == DONE_MAGIC else ""
        elif name == "restored_word":
            note = " verified" if value == base.HOOK_ORIG else ""
        elif name == "power_preflight_magic":
            note = " SUCCESS" if value == PREFLIGHT_MAGIC else ""
        elif name == "scratch_preflight_magic":
            note = " SUCCESS" if value == SCRATCH_MAGIC else ""
        elif name == "free_done":
            note = " RELEASED" if value == FREE_MAGIC else ""
        print(f"{name:25s} 0x{value:08X}{note}")
    state_pass = power_preflight_success(values)
    print(
        "power_preflight           "
        + ("PASS" if state_pass and site == base.HOOK_ORIG else "NOT PROVEN")
    )
    scratch_pass = scratch_preflight_success(values)
    print(
        "scratch_preflight         "
        + ("PASS" if scratch_pass and site == base.HOOK_ORIG else "NOT PROVEN")
    )
    print("hook_state                "
          + ("ARMED — run restore" if site == base.HOOK_ARMED else "restored"))
    if values[S_PHASE] == PHASE_ENCODE:
        print_encode_report(values)
    if values[S_PHASE] == PHASE_SUSTAINED:
        print_sustained_report(shell, values)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage exact-writer power/scratch preflights and encode dry-run."
    )
    parser.add_argument("--fpsup", type=pathlib.Path, default=base.DEFAULT_FPSUP)
    parser.add_argument("--fpsh", type=pathlib.Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    actions = parser.add_subparsers(dest="action", required=True)
    for action, help_text in (
        ("preflight", "build and arm the power/clock-only one-shot"),
        ("scratch", "reserve and verify the retained 4 MiB codec scratch"),
        ("encode", "build/inspect the self-allocating design; dry-run only"),
        ("encode-live", "consume and free the retained scratch in one encode"),
        ("encode-sustained", f"encode {SUSTAINED_FRAMES} consecutive frames"),
        ("mirror", "DESTRUCTIVE: redirect the writer at a byte-identical copy"),
        ("compress", "DESTRUCTIVE: write one COMPRESSED frame"),
        ("compress-sustained",
         f"DESTRUCTIVE: write up to {SUSTAINED_FRAMES} COMPRESSED frames"),
        ("copy-back",
         "DESTRUCTIVE: compress into the CAMERA'S OWN frame buffer; only "
         "seg[1] changes, and a file larger than it writes as stock RAW"),
        ("bound-probe",
         "NON-DESTRUCTIVE: does the engine stop at its declared output length?"),
        ("bound-probe-frame",
         "NON-DESTRUCTIVE: the same, at the product geometry, reading the frame"),
        ("bound-probe-two",
         "NON-DESTRUCTIVE: two blocks, generated source; ANSWERS the bound"),
        ("copy-back-length-only",
         "DIAGNOSTIC: copy-back without the copy; isolates the length change"),
        ("copy-back-copy-only",
         "DIAGNOSTIC: copy-back without the length change; isolates the copy"),
        ("async-submit",
         "NON-DESTRUCTIVE: submit one encode without waiting, then poll"),
        ("async-inspect",
         "NON-DESTRUCTIVE: read the engine parameter block after F_INIT"),
        ("async-result", "read what async-inspect recorded; changes nothing"),
        ("bound-probe-two-ramp",
         "NON-DESTRUCTIVE: generated but compressible source; separates size "
         "from synthetic"),
        ("bound-probe-two-copy",
         "NON-DESTRUCTIVE: source-location test; the camera's own pixels, in "
         "our block. NOT a bound test"),
        ("observe-release",
         "arm the release observer on the state dispatcher; does NOT "
         "self-restore"),
        ("release-log", "read the observer's ring; changes nothing"),
        ("observe-restore", "put the dispatcher's first instruction back"),
        ("tick-rate", "calibrate the raw counter the observer timestamps with"),
        ("acquire-second",
         "have the CAMERA allocate and record the bound probe's second block"),
        ("lifecycle", "read both allocations' records; changes nothing"),
        ("bound-result",
         "read a bound run's whole record and judge it against the criterion"),
        ("release", "free the retained blocks, second one first, and record it"),
        ("status", "read the common state block"),
        ("restore", "guardedly restore this probe's hook word"),
    ):
        child = actions.add_parser(action, help=help_text)
        if action in ("encode-sustained", "compress-sustained"):
            child.add_argument("--frames", type=int,
                               help="frames for this run; the default is the "
                                    "ceiling, and a first run should be small")
        # COMPRESSED_KINDS, not a second hand-written list: the two freeze
        # diagnostics build the same header from the same reference frame, and
        # a list that has to be edited twice gets edited once.
        if action in COMPRESSED_KINDS:
            child.add_argument("--reference-frame", type=pathlib.Path,
                               required=True,
                               help="a stock FHD DNG to build the IFD from")
        if action in KEEP_KINDS:
            child.add_argument(
                "--keep",
                action="store_true",
                help="retain the arena after a successful encode so the "
                     "payload can be read back; owes an explicit release",
            )
        if action == "encode-sustained":
            child.add_argument(
                "--reuse-seed",
                action="store_true",
                help="re-arm over an unfired seed instead of allocating again",
            )
        child.add_argument(
            "--dry-run",
            action="store_true",
            default=argparse.SUPPRESS,
            help="perform no camera reads or writes",
        )
    return parser


def main() -> int:
    args = make_parser().parse_args()
    fpsup = args.fpsup.resolve()
    fpsh = (
        args.fpsh.resolve()
        if args.fpsh is not None
        else fpsup / "fp_usb_shell" / "host" / "fpsh"
    )
    try:
        if getattr(args, "frames", None):
            if not 1 <= args.frames <= SUSTAINED_FRAMES:
                raise ProbeError(f"frames must be 1..{SUSTAINED_FRAMES}")
            FRAME_LIMIT["frames"] = args.frames
        if getattr(args, "keep", False):
            KEEP_BLOCK["active"] = True
        if args.action in ("preflight", "scratch", "encode", "encode-live",
                           "encode-sustained", "mirror",
                           "bound-probe", "bound-probe-frame",
                           "bound-probe-two",
                           "bound-probe-two-copy",
                           "bound-probe-two-ramp",
                           "async-inspect",
                           "async-submit") + COMPRESSED_KINDS:
            code = build_image(args.action, fpsup)
            describe_image(args.action, code, show_words=args.dry_run)
            if args.dry_run:
                print("placed : would claim cave blocks and publish caches "
                      "(needs a live camera to claim)")
            if args.action == "encode" and not args.dry_run:
                raise ProbeError("encode is dry-run only; no camera access attempted")
            if args.dry_run:
                print("dry-run: no camera reads or writes performed")
                return 0
            shell = base.CameraShell(fpsh)
            code, placed = apply_placement(args.action, fpsup)
            for name, at in sorted(placed.items(), key=lambda kv: kv[1]):
                print(f"claimed {name:24s} 0x{at:08X}")
            if args.action == "preflight":
                arm_power_preflight(shell, code)
            elif args.action == "scratch":
                arm_scratch_preflight(shell, code)
            elif args.action in COMPRESSED_KINDS:
                # copy-back builds the same owned header, so it needs the same
                # IFD template loaded into the arena before it is armed.
                arm_compress(shell, code, args.reference_frame.resolve(strict=True))
            elif args.action in ("bound-probe-two", "bound-probe-two-copy",
                                 "bound-probe-two-ramp"):
                arm_bound_probe_two(shell, code)
            elif args.action in ("async-inspect", "async-submit"):
                values = shell.read_words(STATE, STATE_WORDS)
                if not scratch_preflight_success(values):
                    raise ProbeError("async-inspect needs the retained scratch; "
                                     "run preflight then scratch first")
                seed_async_record(shell, values[S_HANDLE])
                print(f"record area   0x{values[S_HANDLE] + ASYNC_OFF:08X} "
                      f"cleared and stamped")
                arm_live_encode(shell, code)
            elif args.action in ("encode-live", "mirror", "bound-probe",
                                 "bound-probe-frame"):
                # the bound probe adopts the retained block exactly as the live
                # encode does; it just never hands anything to the writer
                arm_live_encode(shell, code)
            else:
                arm_sustained_encode(shell, code,
                                     reuse_seed=getattr(args, "reuse_seed", False))
            return 0

        if args.dry_run:
            if args.action == "observe-release":
                code = build_release_observer(fpsup)
                print(f"would write {len(code)} bytes of observer, seed the "
                      f"ring, and displace 0x{DISPATCH:08X}")
                print("dry-run: no camera reads or writes performed")
                return 0
            if args.action == "observe-restore":
                print(f"would write 0x{DISPATCH_ORIG:08X} to 0x{DISPATCH:08X}")
                print("dry-run: no camera reads or writes performed")
                return 0
            if args.action == "acquire-second":
                code = build_acquire(fpsup)
                print(f"would write {len(code)} bytes of acquire routine into "
                      "the code claim, publish it, and call it once")
                print("dry-run: no camera reads or writes performed")
                return 0
            if args.action in ("status", "lifecycle", "bound-result",
                               "release-log", "tick-rate",
                               "async-result"):
                print(
                    f"would read hook 0x{base.HOOK_SITE:08X} and "
                    f"state 0x{STATE:08X}..0x{STATE + STATE_WORDS * 4:08X}"
                )
            else:
                base.dry_run_restore()
            print("dry-run: no camera reads or writes performed")
            return 0

        shell = base.CameraShell(fpsh)
        # status and restore must name the same blocks arming claimed, or they
        # read a stale fixed address and report a probe that never fired.
        apply_placement("encode-sustained", fpsup)
        if args.action == "status":
            show_status(shell)
        elif args.action == "observe-release":
            print(json.dumps(arm_release_observer(shell, fpsup), indent=2))
        elif args.action == "release-log":
            values = shell.read_words(STATE, STATE_WORDS)
            handle = values[S_HANDLE]
            if not handle:
                raise ProbeError("no block handle in state; nothing to read")
            log = read_release_log(shell, handle)
            print(json.dumps(log, indent=2))
            present = shell.read_words(DISPATCH, 1)[0]
            if present != DISPATCH_ORIG:
                print(f"NOTE: 0x{DISPATCH:08X} is still armed "
                      f"(0x{present:08X}); run observe-restore", file=sys.stderr)
        elif args.action == "observe-restore":
            print(json.dumps(restore_release_observer(shell), indent=2))
        elif args.action == "tick-rate":
            print(json.dumps(read_tick_rate(shell), indent=2))
        elif args.action == "acquire-second":
            print(json.dumps(acquire_second_block(shell, fpsup), indent=2))
        elif args.action == "async-result":
            values = shell.read_words(STATE, STATE_WORDS)
            handle = values[S_HANDLE]
            if not handle:
                raise ProbeError("no block handle in state; nothing to read")
            print(json.dumps(read_async_result(shell, handle), indent=2))
        elif args.action == "bound-result":
            record = read_bound_result(shell, fpsup)
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]
                                   / "projects/lossless-sup/tools"))
            import bound_criterion
            demand = bound_criterion.host_demand()
            try:
                judged = bound_criterion.verdict(record, demand)
            except bound_criterion.Undecidable as exc:
                judged = {"verdict": "undecidable", "reasons": [str(exc)]}
            print(json.dumps({"record": record, "demand": demand,
                              "verdict": judged}, indent=2))
        elif args.action == "lifecycle":
            values = shell.read_words(STATE, STATE_WORDS)
            handle = values[S_HANDLE]
            if not handle:
                raise ProbeError("no block A handle in state; nothing to read")
            print(json.dumps(read_lifecycle(shell, handle), indent=2))
        elif args.action == "release":
            print(json.dumps(release_retained_scratch(shell), indent=2))
        else:
            base.restore_probe(shell)
        return 0
    except (base.ProbeError, ProbeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
