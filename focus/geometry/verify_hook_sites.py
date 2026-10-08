#!/usr/bin/env python3
"""Verify native hook-site metadata against a saved ROM; never install hooks.

Checks only the supplied ROM identity and static bytes/control-flow targets.
It does not establish live reachability, ownership, stack safety or timing.
An optional JSON output is created exclusively and never replaces evidence.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
SCHEMA = "sigma-fp-native-hook-sites/v1"
SITE_IDS = {"af_terminal_pre_forward", "native_l_final_event_set_return",
            "face_parser_publication_return", "aat_color_run_return"}
U32_MAX = 0xFFFFFFFF


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, label, minimum=0, maximum=U32_MAX):
    require(type(value) is int and minimum <= value <= maximum, label + ": invalid integer")
    return value


def address(value, label):
    require(isinstance(value, str) and re.fullmatch(r"0x[0-9A-Fa-f]{8}", value),
            label + ": expected an eight-digit hexadecimal address/word")
    return int(value, 16)


def hex_bytes(value, label):
    require(isinstance(value, str) and re.fullmatch(r"(?:[0-9a-fA-F]{2})+", value),
            label + ": expected nonempty contiguous hexadecimal bytes")
    return bytes.fromhex(value)


def decode_arm_bl(word, site):
    """Decode an unconditional ARM BL immediate with PC=site+8."""
    integer(word, "BL word")
    integer(site, "BL address")
    require(site % 4 == 0, "BL address is not ARM-aligned")
    require(word & 0xFF000000 == 0xEB000000, "site is not an unconditional ARM BL immediate")
    displacement = word & 0xFFFFFF
    if displacement & 0x800000:
        displacement -= 1 << 24
    return (site + 8 + displacement * 4) & U32_MAX


def verify_manifest(document, rom):
    """Validate supplied metadata and bytes, raising ValueError on any mismatch."""
    require(isinstance(document, dict), "manifest must be an object")
    require(document.get("schema") == SCHEMA and type(document.get("version")) is int
            and document["version"] == 1, "unsupported hook manifest schema/version")
    require(isinstance(rom, bytes), "ROM must be immutable bytes")
    identity = document.get("rom")
    require(isinstance(identity, dict), "missing ROM identity")
    base = address(identity.get("base_address"), "ROM base")
    length = integer(identity.get("length_bytes"), "ROM length", 1)
    require(base % 4 == 0 and base + length <= 1 << 32, "invalid ROM address range")
    expected_hash = identity.get("sha256")
    require(isinstance(expected_hash, str) and re.fullmatch(r"[0-9a-f]{64}", expected_hash),
            "invalid ROM SHA256")
    require(len(rom) == length, "ROM length mismatch")
    actual_hash = hashlib.sha256(rom).hexdigest()
    require(actual_hash == expected_hash, "ROM SHA256 mismatch")

    def read(where, size, label):
        integer(size, label + " length", 1)
        require(where % 4 == 0, label + ": address is not ARM-aligned")
        offset = where - base
        require(0 <= offset and offset + size <= len(rom), label + ": range outside ROM")
        return rom[offset:offset + size]

    def region(metadata, label, parent=None):
        """Check every declared guard byte and instruction word, not assembly prose."""
        require(isinstance(metadata, dict), label + ": expected object")
        where = address(metadata.get("address"), label + " address")
        expected = None
        for field in ("expected_bytes_le", "bytes_le"):
            if field in metadata:
                encoded = hex_bytes(metadata[field], label + " " + field)
                require(expected is None or encoded == expected, label + ": contradictory byte encodings")
                expected = encoded
        if "word" in metadata:
            word = address(metadata["word"], label + " word")
            encoded = struct.pack("<I", word)
            require(expected is None or encoded == expected, label + ": word/bytes mismatch")
            expected = encoded
        require(expected is not None, label + ": no byte/word guard supplied")
        size = metadata.get("length_bytes", len(expected))
        integer(size, label + " length", 1)
        require(size == len(expected), label + ": declared length/bytes mismatch")
        require(size % 4 == 0, label + ": guard length is not a whole ARM instruction")
        if parent is not None:
            require(parent[0] <= where and where + size <= parent[1],
                    label + ": instruction outside its declared guard")
        actual = read(where, size, label)
        require(actual == expected, label + ": ROM bytes mismatch")
        instructions = metadata.get("instructions", [])
        require(isinstance(instructions, list), label + ": instructions must be a list")
        seen = set()
        for index, instruction in enumerate(instructions):
            child = region(instruction, f"{label}.instructions[{index}]", (where, where + size))
            require(child["length_bytes"] == 4, label + ": instruction must be one ARM word")
            require(child["address"] not in seen, label + ": duplicate instruction address")
            seen.add(child["address"])
        return dict(address=f"0x{where:08X}", length_bytes=size,
                    bytes_verified=True, instruction_words_verified=len(instructions))

    sites = document.get("sites")
    require(isinstance(sites, list) and len(sites) == 4, "exactly four selected hook sites required")
    require(all(isinstance(site, dict) for site in sites), "hook sites must be objects")
    ids = [site.get("id") for site in sites]
    require(all(isinstance(item, str) for item in ids) and set(ids) == SITE_IDS,
            "selected hook-site IDs are missing, duplicated or unsupported")
    verified_sites, seen_addresses = [], set()
    for site in sites:
        label = site["id"]
        require(site.get("instruction_set") == "ARM", label + ": only ARM sites supported")
        require(type(site.get("patch_length_bytes")) is int and site["patch_length_bytes"] == 4,
                label + ": selected site must be one four-byte instruction")
        where = address(site.get("address"), label + " address")
        require(where not in seen_addresses, "duplicate selected site address")
        seen_addresses.add(where)
        word = address(site.get("expected_word"), label + " expected_word")
        expected = hex_bytes(site.get("expected_bytes_le"), label + " expected_bytes_le")
        require(expected == struct.pack("<I", word), label + ": expected word/bytes mismatch")
        actual = read(where, 4, label)
        require(actual == expected, label + ": ROM bytes mismatch")
        target = decode_arm_bl(struct.unpack("<I", actual)[0], where)
        declared_target = address(site.get("original_target"), label + " original_target")
        require(target == declared_target, label + ": decoded BL target mismatch")
        read(target, 4, label + " target")
        continuation = address(site.get("continuation"), label + " continuation")
        require(where + 4 == continuation, label + ": continuation must equal site+4")
        read(continuation, 4, label + " continuation")
        guards = {}
        for key in ("context", "callee_entry_guard", "callee_return_guard"):
            if key not in site:
                continue
            guards[key] = region(site[key], label + "." + key)
            guard_start = int(guards[key]["address"], 16)
            guard_end = guard_start + guards[key]["length_bytes"]
            if key == "context":
                require(guard_start <= where and continuation + 4 <= guard_end,
                        label + ": context must include site and continuation")
            elif key == "callee_entry_guard":
                require(guard_start == target, label + ": entry guard must match original target")
        verified_sites.append(dict(id=label, address=f"0x{where:08X}", expected_word=f"0x{word:08X}",
                                   bytes_verified=True, branch_target_verified=True,
                                   decoded_target=f"0x{target:08X}", continuation=f"0x{continuation:08X}",
                                   continuation_verified=True, guards=guards))
    return {
        "schema": "sigma-fp-native-hook-site-verification/v1", "version": 1,
        "verification_passed": True, "verification_scope": "saved_rom_identity_and_static_instruction_bytes_only",
        "offline_only": True, "hardware_io_performed": False, "live_verified": False,
        "deployment_ready": False, "installable": False, "ownership_verified": False,
        "stack_alignment_verified": False, "stack_highwater_verified": False,
        "stack_timing_verified": False, "timing_verified": False,
        "instruction_cache_publication_verified": False, "native_event_semantics_verified": False,
        "confirmation_authorized": False, "motion_authorized": False,
        "rom": {"base_address": f"0x{base:08X}", "length_bytes": len(rom),
                "sha256": actual_hash, "identity_matches_manifest": True},
        "site_count": len(verified_sites), "sites": verified_sites,
        "limitations": ["The supplied manifest is a local reference, not an authenticated firmware signature.",
                        "Assembly descriptions, calling conventions and stack/timing claims are not proved by byte equality.",
                        "No camera state, live reachability, buffer/code ownership or patch lifecycle is inspected."],
    }


def verify_files(manifest_path, rom_path=None):
    manifest_path = Path(manifest_path)
    raw = manifest_path.read_bytes()
    document = json.loads(raw)
    if rom_path is None:
        require(isinstance(document, dict) and isinstance(document.get("rom"), dict), "missing ROM identity")
        declared = document["rom"].get("path")
        require(isinstance(declared, str) and bool(declared), "missing ROM path")
        rom_path = REPO_ROOT / declared
    rom_path = Path(rom_path)
    report = verify_manifest(document, rom_path.read_bytes())
    report.update(manifest_path=str(manifest_path.resolve()),
                  manifest_sha256=hashlib.sha256(raw).hexdigest(), rom_path=str(rom_path.resolve()))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=HERE / "native_hook_sites.json")
    parser.add_argument("--rom", type=Path, help="Override the saved ROM file; never a camera connection")
    parser.add_argument("--output", type=Path, help="Create a new JSON report; existing files are refused")
    args = parser.parse_args(argv)
    try:
        report = verify_files(args.manifest, args.rom)
        encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output is not None:
            with args.output.open("x", encoding="utf-8") as output:
                output.write(encoded)
        else:
            print(encoded, end="")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"hook verification failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
