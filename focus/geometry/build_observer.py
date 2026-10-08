#!/usr/bin/env python3
"""Assemble raw observer stubs at EMULATOR addresses; never a card/USB builder."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent
CODE_BASE = 0x41000000
SOURCE_NAMES = ("af", "lens", "face", "aat")
STATE_BASES = {name: 0x42000000 + i * 0x100 for i, name in enumerate(SOURCE_NAMES)}
RECORD_BASES = {name: 0x42100000 + i * 0x100000 for i, name in enumerate(SOURCE_NAMES)}
NONCE = 0x13572468


def assemble_observer(capacity=2):
    if type(capacity) is not int or not 1 <= capacity <= 1024:
        raise ValueError("capacity must be an integer in 1..1024")
    assembler_path = ROOT.parents[1] / "fp_usb_shell" / "armasm.py"
    spec = importlib.util.spec_from_file_location("fp_observer_armasm", assembler_path)
    assembler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(assembler)
    defines = [f"CAPACITY={capacity}"]
    for name in SOURCE_NAMES:
        defines += [f"STATE_{name.upper()}={STATE_BASES[name]}",
                    f"RECORDS_{name.upper()}={RECORD_BASES[name]}"]
    source = ROOT / "native_observer.S"
    code = assembler.assemble(source, defines)
    symbols = assembler.symbols(source, defines)
    exports = {"native_observer_" + name for name in SOURCE_NAMES}
    if not exports <= symbols.keys() or any(symbols[name] % 4 for name in exports):
        raise ValueError("missing or unaligned observer exports")
    if not code or len(code) > 0x10000:
        raise ValueError("unexpected observer code size")
    report = dict(schema="fp.native-observer.build/v1", offline_only=True,
                  artifact_type="emulator_addressed_raw_stubs", installable=False,
                  deployment_ready=False, hardware_io_performed=False,
                  ram_ownership_verified=False, stack_headroom_verified=False,
                  timing_budget_verified=False, source_coherence_verified=False,
                  optical_validity_verified=False, arm_emulation_verified=False,
                  confirmation_authorized=False, motion_authorized=False,
                  emulation_addresses_only=True, code_base=CODE_BASE,
                  state_bases=STATE_BASES, record_bases=RECORD_BASES,
                  capacity=capacity, state_bytes=40, record_bytes=256,
                  additional_wrapper_stack_bytes=64,
                  timer_reads_enabled=False, code_bytes=len(code),
                  code_sha256=hashlib.sha256(code).hexdigest(),
                  exports={name: symbols[name] for name in sorted(exports)},
                  source_sha256={"native_observer.S": hashlib.sha256(source.read_bytes()).hexdigest(),
                                 "build_observer.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                 "fp_usb_shell/armasm.py": hashlib.sha256(assembler_path.read_bytes()).hexdigest()},
                  note="Addresses exist only in the offline emulator contract. No site patches, veneers, loader sections, entry, USB access or installation are emitted.")
    return code, symbols, report


def initial_state(capacity=2, *, enabled=False):
    if type(capacity) is not int or not 1 <= capacity <= 1024:
        raise ValueError("capacity must be an integer in 1..1024")
    return struct.pack("<10I", 0x314F4E46, 1, 256, capacity, NONCE,
                       int(enabled), 0, 0, 0, 0)


def build(output: Path, capacity=2):
    if output.exists():
        raise ValueError("output exists; refusing overwrite")
    # Verify exact source firmware before presenting any hook-address artifact.
    from verify_hook_sites import verify_files
    verified = verify_files(ROOT / "native_hook_sites.json")
    code, _, report = assemble_observer(capacity)
    report["rom_verification"] = verified
    output.mkdir(parents=True, exist_ok=False)
    (output / "observer-emulator.bin").write_bytes(code)
    (output / "state-disabled.bin").write_bytes(initial_state(capacity))
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity", type=int, default=2)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output, args.capacity), indent=2))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
