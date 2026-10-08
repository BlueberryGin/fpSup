#!/usr/bin/env python3
"""Historical inline-compression probe: offline build and diagnostic readers.

ARMING IS DISABLED, including direct calls to arm(). The async probe writes an
earlier frame's payload into a later frame, does not retain a native source
lease across writer batches, and has no verified async drain/release path.
Raising its frame limit cannot turn it into a continuous product. The product
pipeline needs a same-frame native adapter with proven buffer ownership before
live installation; there is deliberately no force/unsafe bypass here.

The old setup body below is retained only to audit historical experiments.
Build is offline; status/card/restore/close remain live diagnostic or recovery
operations requiring their own authorization. They are not deployment gates.

HISTORICAL DESIGN NOTES (not current safety or decoding claims)

WHAT IS BEING JOINED

Eight things, every one of them measured live on 2026-09-29 and none of them
ever run together:

  the codec completes when we drive it          polls 1, pattern 1
  its output is a real lossless JPEG            ff d8 ff c3, 12 bit, 512x368
  per-tile sizes come back through F_SIZE       141,805 bytes
  the camera's own frame buffer is writable     eight files, each its own
  a shortened node is honoured                  files exactly 1,396,736 bytes
  the engine needs OPEN before it can signal    0xC37CF878 read 0
  the engine needs START after the submit       [0x300D0000] |= 2
  the allocator gives 16 MiB in total           hence write-back, not a pool

SYNCHRONOUS ON PURPOSE

The asynchronous encode is already proven; this run is about the joins, so it
waits. One frame's work is one frame's call and a failure has one place to be.
Making it asynchronous afterwards moves the encode to frame N and writes its
result at frame N+1 -- the late-frame model -- and touches none of the joins.
At 59.94p a 13.65 ms encode is 82 percent of the period, so a stutter is an
expected outcome of THIS build.

THE FILE IT PRODUCES DOES NOT DECODE

The frame's own header is kept and the tiles are written after it, but the
header still describes an uncompressed frame. Making it decode is the trailing
IFD, which is a separate tool and a separate run. What this proves is the
chain; what it produces is a file of the right length whose payload begins
with a lossless-JPEG SOI.

BEFORE ARMING: preflight, then scratch, then acquire-second, so the camera has
acquired and recorded both blocks. This borrows the codec chain's claims at its
own fixed sizes, so it overwrites that image -- which is why the blocks must
exist first and why `release` needs the codec image placed again afterwards.
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

SOURCE = pathlib.Path(__file__).with_name("inline_compress_probe.S")
PROOF_MAGIC = 0x344C534D                 # "MSL4"

S_COUNT, S_WRITER, S_LR, S_RESTORED = 0, 1, 2, 3
S_ERROR, S_STAGE, S_DONE, S_BLOCK_A = 4, 5, 6, 7
S_BLOCK_B, S_CAM_BUF, S_OPEN_RET, S_FLGID = 8, 9, 10, 11
S_INIT_RET, S_SUBMIT_RET, S_START_RET, S_WAIT_RET = 12, 13, 14, 15
S_PATTERN, S_SIZE_RET, S_SUM, S_NEW_LEN = 16, 17, 18, 19
S_LEN_BACK, S_SHAPE_BAD, S_SKIPPED, S_IFD_SIZE = 20, 21, 22, 23
S_PATCH_OFF, S_PATCH_CNT, S_IFD_AT, S_FILE_BYTES = 24, 25, 26, 27
S_ENTRIES, S_GIVE_UP = 28, 29
S_INFLIGHT, S_NOT_READY, S_LATE = 30, 31, 32
S_WIDTH, S_HEIGHT, S_BITS, S_DEPTH = 33, 34, 35, 36
S_PIXEL_OFF, S_RAW_BYTES, S_DECLARED = 37, 38, 39
S_DNG_LEN, S_PAYLOAD_CAP, S_TILES = 40, 41, 42
S_DST_CAP, S_WRONG_SHAPE, S_PROOF = 43, 44, 45
S_TILE0 = 46
TILE_MAX = 160                   # the probe's own bound; 6064x4042 is 132

# Instrumentation, past the tile array so that adding it moved no offset the
# probe and this file already agreed on. The engine's only answer to a refused
# frame has always been one bit; these fields are the rest of that answer.
S_AFTER_TILES = S_TILE0 + TILE_MAX
(S_CB_ADDR, S_CB_HITS, S_GOOD_STATUS, S_ENG_STATUS, S_ENG_ERRS,
 S_ENG_AT, S_ENG_ENDPOS, S_GOOD_ENDPOS) = range(S_AFTER_TILES,
                                                S_AFTER_TILES + 8)

TEMPLATE_OFF = 0x310000          # where the IFD template lives in block A
TILE_W, TILE_H = 512, 368

DEPTH_CODE = {12: 0, 14: 1, 16: 2, 10: 3}   # the engine's table; no 8-bit
STATE_WORDS = S_GOOD_ENDPOS + 1

ERRORS = {0: None, 2: "the writer's shape was refused",
          3: "the codec would not open", 4: "the completion flag could not be made",
          5: "F_INIT failed", 6: "the submit was refused",
          7: "the starter refused: the owner word was not 1",
          8: "the wait timed out",
          9: "the engine refused a frame: bit 2 of its status came back set. "
             "Status and end-position fields are raw evidence, not a "
             "validated cause",
          10: "F_SIZE failed or returned all zeroes",
          11: "the compressed payload and its trailer would not fit after "
              "the header",
          12: "the frame's own root IFD is not the shape the template was "
              "built from"}

FRAMES = 8

ARM_DISABLED = (
    "inline compression arming is disabled: the historical probe writes an "
    "earlier frame into a later frame, has no retained native source lease "
    "across writer batches, and has no verified async drain/release path. "
    "Use the new same-frame product pipeline only after its native adapter "
    "and buffer ownership are validated. No force/unsafe bypass is provided."
)

# The CODE claim is borrowed -- the arena cannot hold a second 1,792-byte
# one -- but the STATE is this probe's own. Borrowing the codec chain's
# state wrote this layout over the handle that `release` and
# `acquire-second` both read, which cost two rebuilds of the chain and
# once left an arm running on a handle from the PREVIOUS boot.
# Only the CODE comes from the arena, and it is borrowed at the codec chain's
# own fixed size because the arena cannot hold a second 1,792-byte claim. The
# STATE lives inside BLOCK A: the arena had 72 bytes left and this needs 172,
# and borrowing the codec chain's state instead wrote this layout over the
# handle that `release` and `acquire-second` both read -- which once left an
# arm running on a block handle from the PREVIOUS boot. Block A has megabytes
# spare and nothing else can reach it.
CLAIMS = {"code": codec.CLAIM_NAMES["code"]}
STATE_OFF = 0x320000             # in block A, clear of source, template,
                                 # tables, records, mark and every guard
CODE_CLAIM = codec.CODE_CLAIM_BYTES

ENG_CLOSE = 0xC062FF69                   # balances the open the probe does


def configure():
    flush.SOURCE = SOURCE
    flush.CLAIM_NAMES = CLAIMS
    flush.STATE_WORDS = STATE_WORDS
    return flush


def place(handle_a=None):
    """Claim the code, and point the state at block A once its base is known."""
    import probe_placement as placement
    configure()
    placed = placement.claim_regions([(CLAIMS["code"], CODE_CLAIM)])
    flush.CODE = placed[CLAIMS["code"]]
    flush.CODE_LIMIT = flush.CODE + CODE_CLAIM
    flush.HOOK_ARMED = placement.armed_word(flush.HOOK_SITE, flush.CODE)
    if handle_a is not None:
        flush.STATE = handle_a + STATE_OFF
        flush.STATE_LIMIT = flush.STATE + STATE_WORDS * 4
        placed["state"] = flush.STATE
    return placed


def build_with_armed(fpsup, placed, armed):
    assemble, symbols = base.load_assembler(fpsup)
    defines = (f"STATE=0x{placed['state']:08X}",
               f"HOOK_ARMED=0x{armed:08X}",
               f"FRAMES={FRAMES}",
               )
    code = assemble(SOURCE, defines=defines)
    if code == assemble(SOURCE, defines=(defines[0], "HOOK_ARMED=0xDEADBEEF")
                                        + defines[2:]):
        raise flush.ProbeError("HOOK_ARMED is not reaching the assembler")
    table = symbols(SOURCE, defines=defines)
    if table.get("probe_entry") != 0:
        raise flush.ProbeError("probe_entry is not at offset zero")
    if len(code) > CODE_CLAIM:
        raise flush.ProbeError(f"the probe is {len(code)} bytes, above the "
                               f"borrowed {CODE_CLAIM}-byte claim")
    # Where our completion callback landed. The probe is assembled at zero and
    # cannot know where it was placed, so the address is seeded -- and it is
    # entered by a blx from the firmware's Thumb IRQ handler, which means bit 0
    # must be CLEAR for the ARM state and the word must be inside the claim.
    at = table.get("our_callback")
    if at is None:
        raise flush.ProbeError("our_callback is not in the symbol table: the "
                               "probe would install a null callback and no "
                               "frame would ever complete")
    if at % 4 or not 0 < at < len(code):
        raise flush.ProbeError(
            f"our_callback is at +{at}, which is not a word-aligned offset "
            f"inside the {len(code)}-byte probe")
    return code, flush.CODE + at


def codec_block_a(shell):
    """Block A's base, from the codec chain's own state. That state is no
    longer overwritten by this probe, so it is simply there."""
    import probe_placement as placement
    at = placement.claim_regions(
        [(codec.CLAIM_NAMES["state"], codec.STATE_WORDS * 4)]
    )[codec.CLAIM_NAMES["state"]]
    handle = shell.read_words(at, codec.STATE_WORDS)[codec.S_HANDLE]
    if not handle:
        raise flush.ProbeError(
            "no block A handle in the codec chain's state: run preflight, "
            "then scratch, then acquire-second")
    return handle


def both_handles(shell):
    """Both block bases, out of the camera's own records. Never typed in.

    The codec chain's state names block A -- until this probe borrows that
    claim and writes its own layout over it, which is exactly what happens on
    the second run of a boot. So fall back to the handle THIS probe recorded
    last time. Either way the answer is only trusted after check_life_record
    has confirmed, against the record the CAMERA wrote inside the block, that
    it names itself, carries the mark, has the right role and is still held.
    That check is the safety; where the number came from is not.
    """
    import probe_placement as placement
    codec_state = placement.claim_regions(
        [(codec.CLAIM_NAMES["state"], codec.STATE_WORDS * 4)]
    )[codec.CLAIM_NAMES["state"]]
    handle_a = shell.read_words(codec_state, codec.STATE_WORDS)[codec.S_HANDLE]
    if not handle_a:
        raise flush.ProbeError(
            "no block A handle in the codec chain's state: run preflight, "
            "then scratch, then acquire-second")
    life = codec.read_lifecycle(shell, handle_a)
    codec.check_life_record(life["block_a"], life["mark_a"], handle_a, 1)
    handle_b = life["block_b"]["handle"]
    codec.check_life_record(life["block_b"], life.get("mark_b", {}), handle_b, 2)
    if not life.get("disjoint"):
        raise flush.ProbeError(
            f"blocks 0x{handle_a:08X} and 0x{handle_b:08X} overlap")
    return handle_a, handle_b


def geometry(reference: pathlib.Path) -> dict:
    """Everything the shape decides, worked out from one frame of it.

    Nothing about the format is compiled into the probe: it is checked against
    this and any other shape goes to the card untouched. So a new resolution
    needs one reference frame and no code.
    """
    blob = reference.read_bytes()
    root = struct.unpack_from("<I", blob, 4)[0]
    entries = struct.unpack_from("<H", blob, root)[0]
    tags = {}
    for i in range(entries):
        tag, kind, count, value = struct.unpack_from("<HHII", blob, root + 2 + i * 12)
        tags[tag] = (root + 2 + i * 12, value)
    for tag, at in ((256, 0x16), (257, 0x22), (258, 0x2E),
                    (273, 0x76), (279, 0xA6)):
        if tag not in tags:
            raise flush.ProbeError(f"{reference.name}: no tag {tag}")
        if tags[tag][0] != at:
            raise flush.ProbeError(
                f"{reference.name}: tag {tag}'s entry is at 0x{tags[tag][0]:X}, "
                f"not the 0x{at:X} the probe reads. The header layout has "
                "changed and the probe's fixed offsets no longer hold")
    width, height = tags[256][1], tags[257][1]
    bits, pixel_off, raster = tags[258][1], tags[273][1], tags[279][1]
    if bits not in DEPTH_CODE:
        raise flush.ProbeError(
            f"{reference.name} is {bits} bits per sample and the engine's "
            f"depth table has only {sorted(DEPTH_CODE)}. Nothing can compress "
            "this format")
    tiles = -(-width // TILE_W) * -(-height // TILE_H)
    if tiles > TILE_MAX:
        raise flush.ProbeError(
            f"{width}x{height} needs {tiles} tiles and the state block holds "
            f"{TILE_MAX}")
    raw16 = raster & ~15                       # the copy moves sixteen at a time
    # Rounded DOWN, not up: the source is now the camera's own frame buffer
    # and pixel_off + round_up(raster, 1024) lands past its end -- 512 bytes
    # at FHD. Rounding down keeps the read inside the buffer at the cost of
    # ending up to 1023 bytes short of the raster, which is why the first live
    # run of this build compares a compressed frame against a stock neighbour.
    declared = raster & ~1023
    # The raster is copied into block A below its size table, and the engine is
    # told the source runs for `declared` bytes -- +0x18 is ADDED to the source
    # pointer. Both have to stay inside the block. Found by the shape tests
    # before a camera ever saw it: UHD at 10 bits is a 10 MB raster and the
    # block is 4 MiB, so the copy would have gone straight through the guards,
    # the lifecycle record and the mark.
    if pixel_off + declared > len(blob):
        raise flush.ProbeError(
            f"the declared source runs to {pixel_off + declared:,} and the "
            f"frame is {len(blob):,} bytes")
    return {
        "width": width, "height": height, "bits": bits,
        "depth": DEPTH_CODE[bits],
        "pixel_off": pixel_off,
        "raw_bytes": raw16,
        "declared": declared,
        "dng_len": len(blob),
        "payload_cap": len(blob) - pixel_off,
        "tiles": tiles,
        # What block B can take, 1 KiB aligned because the engine wants its
        # lengths that way. The engine refuses work whose output exceeds the
        # capacity it is told, so this is also the compression threshold: a
        # frame that will not fit goes to the card as the camera made it.
        "dst_cap": codec.ALLOC_SIZE & ~1023,
    }


def build_template(fpsup, reference: pathlib.Path):
    """The trailing IFD for this camera's header, and the two patch offsets.

    The blob is constant for a clip: three consecutive frames measured
    2026-09-29 had 58 byte-identical tag entries, and the six bytes that do
    differ live outside them, reached through offsets into the header region
    the probe leaves alone. Only the two value fields carrying file offsets
    change per frame, and the device patches those.
    """
    sys.path.insert(0, str(fpsup.parent / "projects" / "lossless-sup" / "tools"))
    import trailing_ifd as ti
    geom = geometry(reference)
    header = reference.read_bytes()[:geom["pixel_off"]]
    # Any plausible sizes: the blob's BYTES do not depend on them except in the
    # two fields the device patches, and build() checks that itself.
    placeholder = [100000] * geom["tiles"]
    built = ti.build(header, placeholder, TILE_W, TILE_H,
                     pixel_off=geom["pixel_off"])
    if len(built["ifd"]) != built["ifd_size"]:
        raise flush.ProbeError("the template's size disagrees with its bytes")
    return built


def take_over_shared_region(shell):
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
    at, left = 0, []
    while at < words:
        n = min(64, words - at)
        left += [(at + i, v) for i, v in
                 enumerate(shell.read_words(flush.CODE + at * 4, n)) if v]
        at += n
    if left:
        index, value = left[0]
        raise flush.ProbeError(
            f"the borrowed region did not clear: "
            f"0x{flush.CODE + index * 4:08X}=0x{value:08X}")


def arm(shell, fpsup, reference):
    # Fail before placement (which may claim camera memory), any transport,
    # or ENG_CLOSE. Keep the historical setup below for offline source audit.
    raise flush.ProbeError(ARM_DISABLED)

    place()
    handle_a, handle_b = both_handles(shell)
    placed = place(handle_a)
    geom = geometry(reference)
    template = build_template(fpsup, reference)
    print(f"geometry      {geom['width']}x{geom['height']} {geom['bits']}-bit "
          f"(depth code {geom['depth']}), header 0x{geom['pixel_off']:X}, "
          f"raster {geom['raw_bytes']:,}, frame {geom['dng_len']:,}, "
          f"{geom['tiles']} tiles of {TILE_W}x{TILE_H}")
    print("shape         read from the reference, checked on every frame; any "
          "other format goes to the card untouched")
    code, callback_at = build_with_armed(fpsup, placed, flush.HOOK_ARMED)
    print(f"template      {template['ifd_size']} bytes, "
          f"{template['entries']} entries, patch at "
          f"+{template['patch_offset_table_at']} and "
          f"+{template['patch_count_table_at']}, seeded at "
          f"0x{handle_a + TEMPLATE_OFF:08X}")
    print(f"claims        code 0x{flush.CODE:08X} ({CODE_CLAIM} B, borrowed "
          f"{CLAIMS['code']}), state 0x{flush.STATE:08X}")
    print(f"probe         {len(code)} bytes at 0x{flush.CODE:08X}")
    print(f"site          0x{flush.HOOK_SITE:08X}: "
          f"0x{flush.HOOK_ORIG:08X} -> 0x{flush.HOOK_ARMED:08X}")
    print(f"block A       0x{handle_a:08X}  size table, IFD template, state")
    print(f"block B       0x{handle_b:08X}  compressed output")
    print(f"bound         {FRAMES} compressed frames, {FRAMES * 3} entries")
    print("mode          ASYNCHRONOUS: poll for the PREVIOUS frame's encode, "
          "size it, write the finished "
          "payload back after this frame's own header, trail it, shorten the "
          "node, then submit THIS frame and return without waiting. Each "
          "file therefore carries an EARLIER frame's picture, and a frame "
          "that finds the codec still working goes through untouched")

    # Close the codec before installing, whatever state it is in. The probe
    # opens it on its first frame and treats a failed open as fatal -- which
    # is right -- and C0708C60(1) fails when the power is ALREADY on. A run
    # that left it open (this one has no close of its own) therefore poisoned
    # the next run's very first frame. Closing here makes the probe's open the
    # only one that matters.
    sys.path.insert(0, str(fpsup / "fp_usb_shell"))
    import callfn
    ok, value = callfn.call(ENG_CLOSE, verbose=False)
    print(f"codec         closed first (C062FF69 -> {value}), so the probe's "
          "own open starts from a known state")

    flush.verify_call_context(shell)
    take_over_shared_region(shell)
    shell.write_words_verified(flush.CODE, base.words_from(code))
    shell.write_words_verified(flush.STATE, [0] * STATE_WORDS)
    shell.write_words_verified(flush.STATE + S_BLOCK_A * 4,
                               [handle_a, handle_b])
    shell.write_words_verified(flush.STATE + S_WIDTH * 4, [
        geom["width"], geom["height"], geom["bits"], geom["depth"],
        geom["pixel_off"], geom["raw_bytes"], geom["declared"],
        geom["dng_len"], geom["payload_cap"], geom["tiles"],
        geom["dst_cap"]])
    shell.write_words_verified(flush.STATE + S_IFD_SIZE * 4,
                               [template["ifd_size"],
                                template["patch_offset_table_at"],
                                template["patch_count_table_at"]])
    blob = template["ifd"]
    blob += bytes((-len(blob)) % 16)        # copy_ifd moves sixteen at a time
    shell.write_words_verified(
        handle_a + TEMPLATE_OFF,
        list(struct.unpack("<" + "I" * (len(blob) // 4), blob)))
    # Our own completion callback, in place of the firmware's C062FCB1. The
    # IRQ handler hands it the device's status word, which is the only copy
    # that ever exists and which the firmware's callback reduces to one bit.
    shell.write_words_verified(flush.STATE + S_CB_ADDR * 4, [callback_at])
    print(f"callback      0x{callback_at:08X}, ours; it keeps the engine's "
          "status word and 0x300D00F8, then tails into C062FCB1")
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
    place(codec_block_a(shell))
    v = shell.read_words(flush.STATE, STATE_WORDS)
    site = shell.read_words(flush.HOOK_SITE, 1)[0]
    out = status_report(v, site)
    print(json.dumps(out, indent=2))
    if v[S_PROOF] == PROOF_MAGIC:
        print("\n" + state_verdict(v), file=sys.stderr)


def status_report(v, site):
    """Interpret a captured state without imports, claims or camera I/O."""
    if v[S_PROOF] != PROOF_MAGIC:
        return {"seeded": False,
                "note": "no MSL4 proof; this is not a validated probe snapshot"}
    tiles = v[S_TILE0:S_TILE0 + v[S_TILES]]
    out = {
        "hook": f"0x{site:08X}",
        "hook_state": ("restored" if site == flush.HOOK_ORIG
                       else "ARMED" if site == flush.HOOK_ARMED else "unknown"),
        "entries": v[S_COUNT],
        # S_DONE counts both submissions and completed write-backs. S_LATE
        # counts actual compressed write-backs, still with wrong frame identity.
        "processed_steps": v[S_DONE],
        "frames_compressed": v[S_LATE],
        "frames_skipped": v[S_SKIPPED],
        "frames_the_codec_was_not_ready_for": v[S_NOT_READY],
        "frames_written_from_an_earlier_frame": v[S_LATE],
        "job_still_in_flight": bool(v[S_INFLIGHT]),
        "shape_refused": v[S_SHAPE_BAD],
        "error": ERRORS.get(v[S_ERROR], v[S_ERROR]),
        "stage": v[S_STAGE],
        "block_a": f"0x{v[S_BLOCK_A]:08X}",
        "block_b": f"0x{v[S_BLOCK_B]:08X}",
        "camera_buffer": f"0x{v[S_CAM_BUF]:08X}",
        "open_return": v[S_OPEN_RET],
        "flag_id": v[S_FLGID],
        "init_return": v[S_INIT_RET],
        "submit_return": v[S_SUBMIT_RET],
        "start_return": v[S_START_RET],
        "wait_return": (v[S_WAIT_RET] - (1 << 32)) if v[S_WAIT_RET] >> 31
                       else v[S_WAIT_RET],
        "pattern": v[S_PATTERN],
        "size_return": v[S_SIZE_RET],
        "compressed_bytes": v[S_SUM],
        "ratio_percent": (round(100.0 * v[S_SUM] / v[S_RAW_BYTES], 2)
                          if v[S_SUM] and v[S_RAW_BYTES] else None),
        "ifd_at": v[S_IFD_AT],
        "file_bytes": v[S_FILE_BYTES],
        "stock_ifd_entries": v[S_ENTRIES],
        "gave_up": bool(v[S_GIVE_UP]),
        "new_node_length": v[S_NEW_LEN],
        "node_length_readback": v[S_LEN_BACK],
        "tile_sizes": tiles,
        "restored_readback": f"0x{v[S_RESTORED]:08X}",
        # Raw observations from a refused frame, not a decoded cause. Our own
        # completion callback keeps the status word the firmware discards,
        # and 0x300D00F8, which the firmware reads only after success.
        "callback_at": f"0x{v[S_CB_ADDR]:08X}",
        "callback_ran": v[S_CB_HITS],
        "engine_refusals": v[S_ENG_ERRS],
        "first_refusal_at_hook_entry": v[S_ENG_AT] or None,
        "refused_status": f"0x{v[S_ENG_STATUS]:08X}" if v[S_ENG_STATUS] else None,
        "refused_end_position": v[S_ENG_ENDPOS],
        "good_status": f"0x{v[S_GOOD_STATUS]:08X}" if v[S_GOOD_STATUS] else None,
        "good_end_position": v[S_GOOD_ENDPOS],
        "reading": refusal_reading(v),
    }
    return out


def refusal_reading(v) -> str:
    """What the new fields mean, in words, so a live run needs no arithmetic.

    The status word is read by the IRQ handler C0630430 from 0x300D0004 and
    handed to the installed callback; the firmware's own callback keeps bit 2
    and throws the rest away. 0x300D00F8 is where the engine says the stream
    ended -- C062F6C0 clears its low ten bits and uses the rest as an offset
    into the destination -- and the firmware reads it only on success, so a
    refused job's value has never been looked at before.
    """
    if not v[S_CB_HITS]:
        return ("no callback was recorded; this snapshot does not establish "
                "whether a job completed or what the engine produced.")
    if not v[S_ENG_ERRS]:
        return (f"no engine refusal was recorded. A good job's status is "
                f"0x{v[S_GOOD_STATUS]:08X} and its end position "
                f"{v[S_GOOD_ENDPOS]}, which is the reference a refusal would "
                f"be read against.")
    bits = v[S_ENG_STATUS] & ~0x4           # only bit 2's error meaning is known
    said = (f"{v[S_ENG_ERRS]} frame(s) refused, the first at hook entry "
            f"{v[S_ENG_AT]}. Status 0x{v[S_ENG_STATUS]:08X}")
    said += (f" carries additional bits {bin(bits)} beyond error bit 2; "
             "their meaning is not established"
             if bits else " carries no additional bits beyond error bit 2; "
             "this does not identify the cause")
    if v[S_ENG_ENDPOS] == 0:
        said += (". Its recorded end position is 0; failure-path register "
                 "semantics are unverified, so this does not prove that no "
                 "output was produced or that capacity caused the refusal")
    else:
        said += (f". Its recorded end position is {v[S_ENG_ENDPOS]} "
                 f"(0x{v[S_ENG_ENDPOS]:X}); failure-path register semantics "
                 "are unverified, so a nonzero value does not prove valid "
                 "output or truncation and may be stale")
        if v[S_GOOD_ENDPOS]:
            said += (f", against {v[S_GOOD_ENDPOS]} (0x{v[S_GOOD_ENDPOS]:X}) "
                     f"on a frame that worked")
    return said + "."


def state_verdict(v) -> str:
    if v[S_LATE] == 0:
        err = ERRORS.get(v[S_ERROR], v[S_ERROR])
        return (f"NOTHING COMPRESSED. error: {err}. stage {v[S_STAGE]}, "
                f"{v[S_DONE]} processed steps, "
                f"{v[S_SKIPPED]} skipped, {v[S_SHAPE_BAD]} shape refusals. "
                "No compressed write-back was recorded; processed steps "
                "include submissions, not just completed compression.")
    ratio = (f"{100.0 * v[S_SUM] / v[S_RAW_BYTES]:.1f} percent of the raster"
             if v[S_RAW_BYTES] else "raster ratio unavailable")
    said = (f"{v[S_DONE]} processed steps; {v[S_LATE]} compressed write-back(s) "
            f"reported. These carry an EARLIER frame's picture, "
            f"{v[S_NOT_READY]} found the codec still working. "
            f"{v[S_SUM]:,} bytes of tiles, {ratio}, node "
            f"{v[S_NEW_LEN]:,}.")
    if v[S_SKIPPED]:
        said += (f" {v[S_SKIPPED]} frame(s) were left stock after a failure: "
                 f"error {ERRORS.get(v[S_ERROR], v[S_ERROR])}.")
    said += (" This is not same-frame lossless validation. Card file sizes "
             "and SOI alone do not prove decoding or pixel equality.")
    return said


def card(fpsup, clip, date, frames, reference=None):
    """File sizes, and whether the payload starts with a lossless-JPEG SOI."""
    import subprocess
    import tempfile
    geom = geometry(reference) if reference else None
    dng_len = geom["dng_len"] if geom else 0x318200
    pixel_off = geom["pixel_off"] if geom else 0x13400
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for index in range(1, frames + 1):
            name = f"{clip}_{date}_{index:06d}.DNG"
            local = pathlib.Path(tmp) / name
            run = subprocess.run(
                [sys.executable, "getfile.py",
                 f"\\CINEMA\\{clip}\\{name}", str(local),
                 "--size", str(dng_len)],
                cwd=fpsup / "fp_usb_shell", capture_output=True, text=True)
            actual = None
            for line in (run.stdout or "").splitlines():
                if "actual" in line:
                    parts = line.split()
                    actual = int(parts[parts.index("actual") + 1])
            if not local.exists() or actual is None:
                rows.append({"frame": index, "error": "not read"})
                continue
            blob = local.read_bytes()[:actual]
            payload = blob[pixel_off:pixel_off + 4]
            rows.append({
                "frame": index,
                "bytes": actual,
                "shortened": actual < dng_len,
                "payload_starts_lossless_jpeg": payload == b"\xff\xd8\xff\xc3",
                "payload_head": payload.hex(" "),
                "sha256": hashlib.sha256(blob).hexdigest()[:16],
            })
    print(json.dumps(rows, indent=2))
    print("\n" + card_verdict(rows), file=sys.stderr)
    return rows


def card_verdict(rows) -> str:
    read = [r for r in rows if "bytes" in r]
    if not read:
        return "NO VERDICT: no file was read. Check the clip name and the date."
    short = [r for r in read if r["shortened"]]
    jpeg = [r for r in short if r["payload_starts_lossless_jpeg"]]
    if not short:
        return (f"NOTHING WAS SHORTENED: all {len(read)} files are the stock "
                "length. This size check alone does not establish their "
                "compression or decoding state.")
    if len(jpeg) == len(short):
        distinct = len({r["sha256"] for r in short})
        return (f"SHORTENED WITH EXPECTED SOI: {len(short)} file(s) are "
                "shortened and every "
                "one of them has a lossless-JPEG SOI where the raster used to "
                f"be, with {distinct} distinct contents by recorded hash. "
                "This does not prove that the trailing IFD or JPEG decodes, "
                "that pixels match the original frame, or that frame identity "
                "was preserved; decode and same-frame comparison are required.")
    return (f"SHORTENED WITHOUT EXPECTED SOI: {len(short)} file(s) are short "
            f"and only {len(jpeg)} carry the expected SOI. Inspect the IFD "
            "and payload offsets before drawing a compression verdict.")


def restore(shell, fpsup):
    place()                      # the hook only needs the code address
    flush.restore_probe(shell)
    # And close the codec. A run that leaves it open has its power on, its
    # IRQ enabled and its owner word latched, and the next arm would have to
    # undo that before its own open could succeed.
    close_codec(fpsup)


def close_codec(fpsup):
    """Balance the open the probe does; the probe has no close of its own."""
    sys.path.insert(0, str(fpsup / "fp_usb_shell"))
    import callfn
    ok, value = callfn.call(ENG_CLOSE)
    print(f"C062FF69 (close) -> {value} ok={ok}")


def main():
    global FRAMES
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("arm", "status", "restore", "build",
                                       "card", "close"))
    ap.add_argument("--clip")
    ap.add_argument("--date")
    ap.add_argument("--frames", type=int, default=FRAMES,
                    help="card: how many files to read back")
    ap.add_argument("--bound", type=int, default=FRAMES,
                    help="historical build: processed-step bound (submits "
                         "plus write-backs), not a compressed-frame count; "
                         "arm is disabled")
    ap.add_argument("--reference", type=pathlib.Path,
                    help="arm: a stock DNG from this camera whose header the "
                         "trailing IFD is built from")
    ap.add_argument("--fpsup", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parents[2])
    ap.add_argument("--fpsh", type=pathlib.Path, default=None)
    args = ap.parse_args()
    if args.action == "arm":
        # Reject before path resolution, transport construction or close/claim.
        # This CLI gate is independent of the direct arm() API's own refusal.
        raise flush.ProbeError(ARM_DISABLED)
    fpsup = args.fpsup.resolve()
    FRAMES = args.bound
    if args.action == "build":
        configure()
        assemble, _ = base.load_assembler(fpsup)
        code = assemble(SOURCE, defines=("STATE=0xC072F700",
                                         "HOOK_ARMED=0xEB00333F",
                                         f"FRAMES={FRAMES}",
                                         ))
        print(f"{len(code)} bytes of {CODE_CLAIM}; nothing written, no camera "
              "touched")
        return 0
    if args.action == "card":
        if not args.clip or not args.date:
            ap.error("card needs --clip and --date")
        card(fpsup, args.clip, args.date, args.frames,
             args.reference.resolve() if args.reference else None)
        return 0
    if args.action == "close":
        close_codec(fpsup)
        return 0
    fpsh = args.fpsh.resolve() if args.fpsh else fpsup / "fp_usb_shell" / "host" / "fpsh"
    shell = base.CameraShell(fpsh)
    if args.action == "arm":
        if not args.reference:
            ap.error("arm needs --reference, a stock DNG from this camera")
        arm(shell, fpsup, args.reference.resolve())
    else:
        {"status": status, "restore": restore}[args.action](shell, fpsup)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (base.ProbeError, flush.ProbeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
