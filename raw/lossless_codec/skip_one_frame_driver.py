#!/usr/bin/env python3
"""Arm, read and restore the one-frame skip probe.

The hook site, the word it displaces, the placement, the cache publication and
the guarded restore are all exact_flush_writer_probe's, unchanged: that module
already drives 0xC03A5490 and G3 proved it live on 2026-09-23. Only the payload
and the state layout are different, so this swaps those two and reuses the rest
rather than growing a second copy of machinery that has been reviewed once.

The cave claims are the CODEC probe's, not new ones: the arena has about 328
bytes free after the codec claim, the state claim and callfn's tools, which is
less than this probe needs. Sharing them means only one of the two families may
be installed at a time, which is already true of the chain.
"""
import argparse
import json
import pathlib
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import exact_dng_writer_probe as base
import exact_flush_writer_probe as flush

SOURCE = pathlib.Path(__file__).with_name("skip_one_frame_probe.S")
PROOF_MAGIC = 0x31504B53                 # "SKP1"

S_COUNT, S_WRITER, S_FRAME, S_LR = 0, 1, 2, 3
S_RESTORED, S_PROOF, S_SKIPPED, S_SKIP_ID = 4, 5, 6, 7
S_SKIP_BUF, S_SKIP_STATE, S_PASSED, S_STAGE = 8, 9, 10, 11
S_RELEASED_SET = 12


# Its OWN claims, at a FIXED size. Two lessons, both paid for on 2026-09-29:
#
#  * sharing lossless.codec.code resized that claim to this probe's length, and
#    the codec chain then could not get its own 1,792 bytes back on that boot.
#    The arena had 1,392 bytes free the whole time; the sharing was based on a
#    stale estimate of 328.
#  * claiming len(code) means every edit that changes the size re-bumps to a new
#    address and burns arena. A fixed, generous size claims once.
CODE_CLAIM = 0x200          # 512, against a 376-byte probe
CLAIMS = {"code": "lossless.skip.code", "state": "lossless.skip.state"}


def configure():
    """Point the reused machinery at this probe's source and claims."""
    flush.SOURCE = SOURCE
    flush.CLAIM_NAMES = CLAIMS
    return flush


def place(fpsup, claim=None):
    """Claim at a fixed size and bake the addresses in, as place_probe does.

    Not place_probe itself: that one claims len(code), which is what moved this
    probe's code three times in one session.
    """
    import probe_placement as placement
    configure()
    sized = flush.build_probe(fpsup)
    if len(sized) > CODE_CLAIM:
        raise flush.ProbeError(
            f"the probe is {len(sized)} bytes, above its {CODE_CLAIM}-byte claim")
    placed = placement.claim_regions(
        [(CLAIMS["code"], CODE_CLAIM),
         (CLAIMS["state"], flush.STATE_WORDS * 4)], claim=claim)
    flush.CODE = placed[CLAIMS["code"]]
    flush.STATE = placed[CLAIMS["state"]]
    flush.CODE_LIMIT = flush.CODE + CODE_CLAIM
    flush.STATE_LIMIT = flush.STATE + flush.STATE_WORDS * 4
    flush.HOOK_ARMED = placement.armed_word(flush.HOOK_SITE, flush.CODE)
    return placed


def build_with_armed(fpsup, placed, armed):
    """Build with HOOK_ARMED actually passed, and prove that it was.

    The reused builder passes only STATE, because the probe it was written for
    never re-arms. This one does, and on 2026-09-29 the unpassed define left its
    sentinel default baked in: a BL with a zero offset, which targets site+8, so
    every frame after the first lost its write and the camera waited with the
    card light on for completions that could not come.

    Assembling twice with two different values and requiring the bytes to differ
    is the check that would have caught it. A define that is not reaching the
    assembler produces identical output both times.
    """
    assemble, symbols = base.load_assembler(fpsup)
    defines = (f"STATE=0x{placed[flush.CLAIM_NAMES['state']]:08X}",
               f"HOOK_ARMED=0x{armed:08X}")
    code = assemble(SOURCE, defines=defines)
    probe_code = assemble(SOURCE, defines=(defines[0], "HOOK_ARMED=0xDEADBEEF"))
    if code == probe_code:
        raise flush.ProbeError(
            "HOOK_ARMED is not reaching the assembler: two different values "
            "produced identical images. Refusing to arm with a word this probe "
            "would then write into the hook site itself")
    if symbols(SOURCE, defines=defines).get("probe_entry") != 0:
        raise flush.ProbeError("probe_entry is not at offset zero")
    return code


def arm(shell, fpsup):
    placed = place(fpsup)
    code = build_with_armed(fpsup, placed, flush.HOOK_ARMED)
    if len(code) != len(base.words_from(code)) * 4:
        raise flush.ProbeError("image is not word aligned")
    print(f"claims        code 0x{flush.CODE:08X} ({CODE_CLAIM} B), "
          f"state 0x{flush.STATE:08X}")
    print(f"skip probe    {len(code)} bytes at 0x{flush.CODE:08X}")
    print(f"state         0x{flush.STATE:08X}")
    print(f"site          0x{flush.HOOK_SITE:08X}: "
          f"0x{flush.HOOK_ORIG:08X} -> 0x{flush.HOOK_ARMED:08X}")

    # arm_probe's own sequence, with the proof word inserted between clearing
    # the state and arming: the probe refuses to skip without it, so a state
    # left over from anything else cannot make it fire.
    flush.verify_call_context(shell)
    flush.require_clear_install_regions(shell)
    shell.write_words_verified(flush.CODE, base.words_from(code))
    shell.write_words_verified(flush.STATE, [0] * flush.STATE_WORDS)
    shell.write_words_verified(flush.STATE + S_PROOF * 4, [PROOF_MAGIC])

    import probe_placement as placement
    from single_frame_codec_probe import native_caller
    for item in placement.publish(native_caller()):
        print(f"published {item['function']} -> {item['result']}")

    flush.verify_call_context(shell)
    armed_ok = flush.arm_site(shell)
    # Read the site back and compare with the word we MEANT to arm. The probe
    # re-arms itself with the same word, so if this does not match, every later
    # frame would be re-armed wrong too.
    present = shell.read_words(flush.HOOK_SITE, 1)[0]
    if armed_ok and present != flush.HOOK_ARMED:
        shell.write_word_verified(flush.HOOK_SITE, flush.HOOK_ORIG)
        raise flush.ProbeError(
            f"the site reads 0x{present:08X} after arming, not the expected "
            f"0x{flush.HOOK_ARMED:08X}; restored to stock and nothing is armed")
    print(f"site now      0x{present:08X} (expected 0x{flush.HOOK_ARMED:08X})")
    if armed_ok:
        print("armed. Record one short clip of at least four frames; the THIRD "
              "flush is the one that will not happen")
    else:
        print("it fired during arming; run: status")


def status(shell, fpsup):
    place(fpsup)
    v = shell.read_words(flush.STATE, flush.STATE_WORDS)
    site = shell.read_words(flush.HOOK_SITE, 1)[0]
    out = {
        "hook": f"0x{site:08X}",
        "hook_state": ("restored" if site == flush.HOOK_ORIG
                       else "ARMED" if site == flush.HOOK_ARMED else "unknown"),
        "entries": v[S_COUNT],
        "passed_through_before_the_skip": v[S_PASSED],
        "skipped": v[S_SKIPPED] == PROOF_MAGIC,
        "skipped_frame_id": v[S_SKIP_ID],
        "skipped_frame_buffer": f"0x{v[S_SKIP_BUF]:08X}",
        "skipped_frame_state_word": v[S_SKIP_STATE],
        "stage": v[S_STAGE],
        "wrote_0x12_ourselves": bool(v[S_RELEASED_SET]),
        "restored_readback": f"0x{v[S_RESTORED]:08X}",
        "last_writer": f"0x{v[S_WRITER]:08X}",
        "last_frame": f"0x{v[S_FRAME]:08X}",
    }
    print(json.dumps(out, indent=2))
    if not out["skipped"]:
        print("NOTE: no skip recorded. Either the clip was too short or the "
              "probe never reached its third entry.", file=sys.stderr)


def restore(shell, fpsup):
    place(fpsup)
    flush.restore_probe(shell)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("arm", "status", "restore", "build"))
    ap.add_argument("--fpsup", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parents[2])
    ap.add_argument("--fpsh", type=pathlib.Path, default=None)
    args = ap.parse_args()
    fpsup = args.fpsup.resolve()
    if args.action == "build":
        configure()
        code = flush.build_probe(fpsup)
        print(f"{len(code)} bytes; nothing written, no camera touched")
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
