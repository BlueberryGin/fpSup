#!/usr/bin/env python3
"""Where a probe's code and state live, and how a write becomes executable.

Two things the fixed-address probes got wrong, both of which bit us:

1. They named cave addresses by hand. `0xC072F700` is at once this project's
   probe state block, `callfn`'s parameter block, `putfile`'s and `dump`'s --
   which is why "do not run getfile while armed" had to be a written rule
   instead of an impossibility. `cave.claim()` hands out the camera's own bump
   allocations by name, so two tools cannot land on one address. The products
   stopped hand-naming cave addresses on 2026-09-22; this follows.

2. They armed the hook straight after a read-back. A read-back proves the data
   cache holds the bytes, not that the instruction fetch path will see them,
   and `DSB` is not a D-cache clean. The publication path here is the ROM pair
   the AF tooling proved in task context: `0xC000E91C` (D-cache clean and
   invalidate by set/way) then `0xC000EABC` (I-cache invalidate). The
   hard-lock warning on cache maintenance in USB_COMPLETION_PATHS.md is about
   IRQ context; `callfn` borrows the echo slot and runs in task context.

Offline-testable parts are pure functions; claiming and publication need a
live shell and are exercised against a fake here.
"""
import pathlib
import sys

SHELL_DIR = pathlib.Path(__file__).resolve().parents[2] / "fp_usb_shell"
DCACHE_MAINTENANCE = 0xC000E91C
ICACHE_INVALIDATE = 0xC000EABC
CACHE_PUBLICATION_FUNCTIONS = (DCACHE_MAINTENANCE, ICACHE_INVALIDATE)
PUBLICATION_METHOD = "rom-dcache-icache/callfn/v1"

# ARM BL: offset counts words from the pipeline-relative PC, which is the
# instruction address plus 8, and is a signed 24-bit field.
BL_OPCODE = 0xEB000000
BL_RANGE = 1 << 25


class PlacementError(RuntimeError):
    pass


def armed_word(site: int, target: int) -> int:
    """The BL word that sends `site` to `target`.

    The probes carried this as the constant 0xEB00333F, which silently encoded
    both a fixed hook site and a fixed code address. A claimed address changes
    every boot, so it has to be computed and range-checked.

    The encoding itself is exact_dng_writer_probe.arm_bl, which the reviewed
    arming transaction already uses; this only adds the alignment check for
    both operands and a message naming the two addresses.
    """
    import exact_dng_writer_probe as base
    for name, value in (("site", site), ("target", target)):
        if value & 3:
            raise PlacementError(f"{name} 0x{value:08X} is not word aligned")
    try:
        return base.arm_bl(site, target)
    except base.ProbeError as exc:
        raise PlacementError(
            f"0x{site:08X} cannot reach 0x{target:08X}: {exc}") from exc


def decode_bl(site: int, word: int) -> int:
    """The target a BL word at `site` branches to; inverse of armed_word."""
    if word & 0xFF000000 != BL_OPCODE:
        raise PlacementError(f"0x{word:08X} is not a BL")
    offset = word & 0xFFFFFF
    if offset & 0x800000:
        offset -= 0x1000000
    return site + 8 + offset * 4


def load_cave():
    """The shell's cave module, imported from its own directory."""
    if str(SHELL_DIR) not in sys.path:
        sys.path.insert(0, str(SHELL_DIR))
    try:
        import cave
    except ImportError as exc:
        raise PlacementError(f"cave.py not found under {SHELL_DIR}") from exc
    return cave


def claim_regions(regions, claim=None):
    """Claim each named region, returning {name: address}.

    `regions` is a sequence of (name, size). Names are namespaced by the
    caller so two probes cannot collide by accident. Sizes are the size of the
    thing: a bump allocator cannot grow a claim in place, so asking for the
    real size once is cheaper than asking twice.
    """
    if claim is None:
        claim = load_cave().claim
    placed = {}
    for name, size in regions:
        if size <= 0 or size % 4:
            raise PlacementError(f"{name}: {size} is not a positive word size")
        placed[name] = claim(name, size)
    addresses = sorted(placed.items(), key=lambda item: item[1])
    for (a_name, a_at), (b_name, b_at) in zip(addresses, addresses[1:]):
        a_size = dict(regions)[a_name]
        if a_at + a_size > b_at:
            raise PlacementError(
                f"{a_name} at 0x{a_at:08X}+{a_size} overlaps {b_name} at "
                f"0x{b_at:08X}; the register and the camera disagree"
            )
    return placed


def publish(call, functions=CACHE_PUBLICATION_FUNCTIONS):
    """Make freshly written bytes visible to the instruction fetch path.

    Order matters: the data cache has to reach memory before the instruction
    cache is told to forget what it has. Returns the per-function results so a
    caller can record them; a failure here must abort arming, because an
    unpublished hook is a hook that may or may not run.
    """
    results = []
    for function in functions:
        outcome = call(function)
        results.append({"function": f"0x{function:08X}", "result": outcome})
    return results
