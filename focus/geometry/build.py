#!/usr/bin/env python3
"""Build an ARM relocatable geometry object, NOT a loadable camera payload."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
# -Oz introduces __aeabi_lls{l,r} calls on this toolchain. Use -O2 and audit
# every generated object; the eventual firmware integration has no C runtime.
FLAGS = ["-target", "armv7-none-eabi", "-mcpu=cortex-a9", "-marm", "-mfloat-abi=soft", "-O2",
         "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-fno-unwind-tables",
         "-fno-asynchronous-unwind-tables", "-fno-vectorize", "-fno-slp-vectorize",
         "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wvla"]


def inspect_elf(data: bytes, required_symbols=None) -> dict:
    if len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01":
        raise ValueError("expected little-endian ELF32")
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    if header[1:3] != (1, 40):
        raise ValueError("expected relocatable ARM object")
    offset, stride, count, names_index = header[6], header[11], header[12], header[13]
    if stride != 40 or not count or names_index >= count or offset + stride * count > len(data):
        raise ValueError("invalid section table")
    sections = [struct.unpack_from("<IIIIIIIIII", data, offset + stride * i) for i in range(count)]

    def content(section):
        if section[1] == 8:
            return b""  # NOBITS is not file storage.
        start, size = section[4:6]
        if start + size > len(data):
            raise ValueError("section outside object")
        return data[start:start + size]

    def string(table, index):
        if index >= len(table):
            raise ValueError("invalid string offset")
        end = table.find(b"\0", index)
        if end < 0:
            raise ValueError("unterminated ELF string")
        return table[index:end].decode("ascii")

    names = content(sections[names_index])
    sizes, undefined, exports = {}, [], []
    for section in sections:
        name = string(names, section[0])
        content(section)
        if section[2] & 2:
            sizes[name] = section[5]
            if section[2] & 1 and section[5]:
                raise ValueError("unexpected mutable global state: " + name)
        if section[1] != 2:
            continue
        if section[9] != 16 or section[5] % 16 or section[6] >= count:
            raise ValueError("invalid symbol table")
        strings = content(sections[section[6]])
        for pos in range(section[4], section[4] + section[5], 16):
            n, _, _, info, _, index = struct.unpack_from("<IIIBBH", data, pos)
            name = string(strings, n)
            # SHN_COMMON reserves mutable storage at link time, so it has no
            # writable allocated section for the section-level check above.
            if index == 0xFFF2:
                raise ValueError("unexpected COMMON mutable global state: " + (name or "<unnamed>"))
            if name and index == 0:
                undefined.append(name)
            if name and index != 0 and info >> 4 == 1:
                exports.append(name)
    if undefined:
        raise ValueError("external runtime dependencies: " + ", ".join(undefined))
    required = ({"fg_default_config", "fg_init", "fg_invalidate", "fg_confirm", "fg_update"}
                if required_symbols is None else set(required_symbols))
    if not required <= set(exports):
        raise ValueError("missing public core symbols")
    return dict(allocated_section_bytes=sizes, exported_symbols=sorted(exports),
                undefined_symbols=undefined)


def build(output: Path, compiler="clang") -> dict:
    if output.exists():
        raise ValueError("output already exists; refusing overwrite")
    with tempfile.TemporaryDirectory(prefix="fp-geometry-arm-") as folder:
        temp = Path(folder)
        obj, assembly = temp / "geometry.o", temp / "geometry.S"
        base = [compiler, *FLAGS]
        subprocess.run([*base, "-c", str(ROOT / "geometry.c"), "-o", str(obj)],
                       check=True, capture_output=True, text=True)
        subprocess.run([*base, "-S", str(ROOT / "geometry.c"), "-o", str(assembly)],
                       check=True, capture_output=True, text=True)
        data = obj.read_bytes()
        manifest = inspect_elf(data)
        manifest.update(offline_only=True, artifact_type="relocatable_object",
                        deployment_ready=False, native_adapter_complete=False,
                        optical_focus_verified=False, hardware_motion_verified=False,
                        motion_authorized=False, compiler_flags=FLAGS,
                        compiler_version=subprocess.run([compiler, "--version"], check=True,
                            capture_output=True, text=True).stdout.splitlines()[0],
                        object_sha256=hashlib.sha256(data).hexdigest(),
                        source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                       for name in ("geometry.c", "geometry.h", "build.py")})
        output.mkdir(parents=True, exist_ok=False)
        (output / obj.name).write_bytes(data)
        (output / assembly.name).write_text(assembly.read_text())
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
