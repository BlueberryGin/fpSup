#!/usr/bin/env python3
"""Actual ARM32 bridge + pinned native header/terminal/selector instructions.

Intermediate component records are explicitly substituted, not a rendered-page
or allocator/OS proof. No device imports, installer, or deployable output.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PAGE = POOL = IMAGE = TEXT = PROBE = ELF = None
PINS = None
COVERAGE = set()
INVALID, NOT_TARGET, REFUSED, POISONED = range(0x100, 0x104)


def fnv(data):
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def compile_text(directory, source=None):
    clang = shutil.which("clang")
    if not clang:
        raise RuntimeError("clang is required; compilation cannot be skipped")
    obj = Path(directory) / "native_reader.o"
    subprocess.run([clang, "--target=armv7-none-eabi", "-mcpu=cortex-a9", "-mthumb",
                    "-mfloat-abi=soft", "-mfpu=none", "-std=c11", "-O2", "-ffreestanding",
                    "-fno-builtin", "-fno-addrsig", "-fno-unwind-tables",
                    "-fno-asynchronous-unwind-tables", "-Wall", "-Wextra", "-Werror",
                    "-I", str(HERE), "-c", str(source or HERE / "native_reader.c"),
                    "-o", str(obj)], check=True, capture_output=True, text=True)
    return ELF.load_text(obj)


def probe_class():
    from unicorn import UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_PC,
                                   UC_ARM_REG_SP, UC_ARM_REG_R4, UC_ARM_REG_R5,
                                   UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8,
                                   UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11)
    saved = (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
             UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11)

    class NativeReaderProbe(PROBE.PageProbe):
        RAM_SIZE = 0x180000
        STACK = PROBE.PageProbe.RAM + RAM_SIZE - 0x10
        CODE = 0x30000000
        STATE = PROBE.PageProbe.RAM + 0x3600
        CONFIG = PROBE.PageProbe.RAM + 0x3700
        PRIVATE_PAGE = PROBE.PageProbe.RAM + 0x80000
        PRIVATE_POOL = PROBE.PageProbe.RAM + 0xC0000
        MAX_INSTRUCTIONS = 12_000_000
        TIMEOUT_US = 15_000_000

        def __init__(self, text=None):
            self.text = text or TEXT
            self.native_records = []
            self.mode = "success"
            self.outer_bridge = False
            self.bridge_entered = False
            super().__init__(IMAGE)
            length = (len(self.text.code) + 4095) & ~4095
            self.uc.mem_map(self.CODE, length, UC_PROT_READ | UC_PROT_WRITE)
            self.uc.mem_write(self.CODE, self.text.code)
            self.uc.mem_protect(self.CODE, length, UC_PROT_READ | UC_PROT_EXEC)
            self.write(self.PRIVATE_PAGE, PAGE)
            self.write(self.PRIVATE_POOL, POOL)
            self.write_words(self.CONFIG, self.PRIVATE_PAGE, self.PRIVATE_POOL,
                             len(PAGE), len(POOL), PINS[0], PINS[1], PINS[2], 3288,
                             214, PROBE.PARSE | 1)
            self.write_words(self.READER + 4, 0x76FF04)
            self.write_words(self.READER + 12, self.TABLE + 12)

        def _instruction(self, uc, address, size, data):
            if self.CODE <= address < self.CODE + len(self.text.code):
                self.instructions += 1
                return
            if address == PROBE.PARSE:
                if uc.reg_read(UC_ARM_REG_SP) & 7:
                    raise PROBE.registry.ProbeError("unaligned native parser call")
                if self.outer_bridge and not self.bridge_entered:
                    self.bridge_entered = True
                    uc.reg_write(UC_ARM_REG_R1, uc.reg_read(UC_ARM_REG_R0))
                    uc.reg_write(UC_ARM_REG_R0, self.STATE)
                    uc.reg_write(UC_ARM_REG_PC, self.CODE + self.text.functions["fp_nr_parse"])
                    return
                reader = uc.reg_read(UC_ARM_REG_R0)
                pos = self.words(reader + 4)[0]
                base = self.words(reader + 0x24)[0]
                tag, length = struct.unpack(">2I", uc.mem_read(base + pos, 8))
                self.native_records.append((pos, tag, length))
                if pos and tag != 0xFFFFFFFF:
                    self.write_words(reader + 0x50, 3)
                    self.write_words(reader + 0xB4, self.ROOT_OBJECT)
                    if self.mode == "reentry":
                        # Execute the actual compiled guard on the current stack.
                        uc.reg_write(UC_ARM_REG_R0, self.STATE)
                        uc.reg_write(UC_ARM_REG_R1, reader)
                        uc.reg_write(UC_ARM_REG_PC, self.CODE + self.text.functions["fp_nr_parse"])
                        return
                    if self.mode == "context_change":
                        self.write_words(reader + 12, self.TABLE + 16)
                    self.write_words(reader + 4, pos + length + (4 if self.mode == "bad_cursor" else 0))
                    self._return(1 if self.mode == "early_stop" else
                                 0xBAD if self.mode == "arbitrary_error" else 0)
                    return
                if tag == 0xFFFFFFFF:
                    self.write_words(reader + 0x50, 213 if self.mode == "short_count" else 214)
                    self.write_words(reader + 0xB4, 0 if self.mode == "missing_root" else self.ROOT_OBJECT)
                    if self.mode == "terminal_zero":
                        self.write_words(reader + 4, pos + length)
                        self._return(0)
                        return
                # Header and terminal execute pinned original instructions.
            super()._instruction(uc, address, size, data)

        def call(self, name, *args):
            values = [0xABCD1000 + i for i in range(len(saved))]
            for reg, value in zip(saved, values):
                self.uc.reg_write(reg, value)
            result = self.run(self.CODE + (self.text.functions["fp_nr_" + name] & ~1), *args)
            if [self.uc.reg_read(reg) for reg in saved] != values:
                raise PROBE.registry.ProbeError("callee-saved r4-r11 changed")
            return result

        def initialize(self):
            return self.call("init", self.STATE, self.CONFIG)

        def parse_private(self):
            return self.call("parse", self.STATE, self.READER)

    return NativeReaderProbe


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.p = probe_class()()
        self.assertEqual(self.p.initialize(), 0)

    def tearDown(self):
        COVERAGE.update(self.p.visited)
        self.p.close()

    def test_fresh_init_and_parser_pointer_contract(self):
        p = self.p
        self.assertEqual(p.initialize(), INVALID)
        p.write_words(p.CONFIG + 36, 0)
        self.assertEqual(p.call("init", p.STATE + 0x200, p.CONFIG), INVALID)
        self.assertEqual(p.native_records, [])

    def test_first_boundary_rejections_do_not_touch_reader(self):
        p = self.p
        for offset, value, expected in ((4, 0x76FF08, NOT_TARGET), (0x24, 0xC18C0464, NOT_TARGET),
                                      (0x14, 0xC18C0478, REFUSED), (0x10, 176151, REFUSED),
                                      (0x18, 1, REFUSED), (0x28, 1, REFUSED),
                                      (12, 0, REFUSED), (0x50, 1, REFUSED), (0xB4, p.ROOT_OBJECT, REFUSED)):
            original = p.words(p.READER + offset)[0]
            p.write_words(p.READER + offset, value)
            before = bytes(p.uc.mem_read(p.READER, 0xB8))
            self.assertEqual(p.parse_private(), expected)
            self.assertEqual(bytes(p.uc.mem_read(p.READER, 0xB8)), before)
            p.write_words(p.READER + offset, original)
        self.assertEqual(p.native_records, [])

    def test_prevalidation_rejects_malformed_record_even_if_hash_is_updated(self):
        p = self.p
        changed = bytearray(PAGE)
        struct.pack_into(">I", changed, 3288 + 4, 0)
        p.write(p.PRIVATE_PAGE, changed)
        p.write_words(p.STATE + 8 + 16, fnv(changed))
        self.assertEqual(p.parse_private(), REFUSED)
        self.assertEqual(p.native_records, [])

    def test_page_fingerprint_and_record_count_are_pinned(self):
        p = self.p
        p.write(p.PRIVATE_PAGE + 100, bytes([PAGE[100] ^ 1]))
        self.assertEqual(p.parse_private(), REFUSED)
        p.write(p.PRIVATE_PAGE, PAGE)
        p.write_words(p.STATE + 8 + 24, PINS[2] - 1)
        self.assertEqual(p.parse_private(), REFUSED)
        self.assertEqual(p.native_records, [])

    def test_stock_pool_prefix_required_even_with_matching_new_hash(self):
        p = self.p
        changed = bytes([POOL[0] ^ 1]) + POOL[1:]
        p.write(p.PRIVATE_POOL, changed)
        p.write_words(p.STATE + 8 + 20, fnv(changed))
        self.assertEqual(p.parse_private(), REFUSED)
        self.assertEqual(p.native_records, [])

    def test_whole_candidate_original_header_terminal_and_restore(self):
        p = self.p
        before = p.words(p.READER, 12)
        self.assertEqual(p.parse_private(), 1)
        after = list(p.words(p.READER, 12)); after[1] = before[1]
        self.assertEqual(tuple(after), before)
        self.assertEqual(p.words(p.READER + 4)[0], 0x795950)
        self.assertEqual(p.words(p.READER + 0x48)[0], 214)
        self.assertEqual(p.words(p.READER + 0x50)[0], 214)
        self.assertEqual(p.words(p.READER + 0xB4)[0], p.ROOT_OBJECT)
        self.assertEqual(len(p.native_records), PINS[2])
        self.assertEqual(p.native_records[0], (0, 0x10002, 3288))
        self.assertEqual(p.native_records[-1], (len(PAGE) - 8, 0xFFFFFFFF, 8))
        self.assertIn("record_parser", p.visited)
        self.assertIn("reader_be32", p.visited)
        self.assertEqual(p.parse_private(), REFUSED)

    def test_partial_root_is_quarantined_and_cannot_be_retried(self):
        p = self.p; p.mode = "arbitrary_error"
        self.assertEqual(p.parse_private(), POISONED)
        self.assertEqual(p.words(p.STATE + 4)[0], 4)
        self.assertEqual(p.words(p.STATE + 100, 3), (3, p.words(p.READER + 0x54)[0], p.ROOT_OBJECT))
        self.assertEqual(p.words(p.READER + 0x50)[0], 0)
        self.assertEqual(p.words(p.READER + 0xB4)[0], 0)
        self.assertEqual(p.words(p.READER + 0x24)[0], p.PRIVATE_PAGE)
        calls = len(p.native_records)
        self.assertEqual(p.parse_private(), POISONED)
        self.assertEqual(len(p.native_records), calls)
        self.assertEqual(p.initialize(), INVALID)

    def test_cursor_mismatch_poison(self):
        self.p.mode = "bad_cursor"
        self.assertEqual(self.p.parse_private(), POISONED)
        self.assertEqual(self.p.words(self.p.READER + 0xB4)[0], 0)

    def test_early_stop_poison(self):
        self.p.mode = "early_stop"
        self.assertEqual(self.p.parse_private(), POISONED)
        self.assertEqual(len(self.p.native_records), 2)

    def test_context_change_poison(self):
        self.p.mode = "context_change"
        self.assertEqual(self.p.parse_private(), POISONED)

    def test_reentry_executes_guard_and_preserves_first_partial_diagnostics(self):
        p = self.p; p.mode = "reentry"
        self.assertEqual(p.parse_private(), POISONED)
        self.assertEqual(len(p.native_records), 2)
        self.assertEqual(p.words(p.STATE + 100, 3),
                         (3, p.words(p.READER + 0x54)[0], p.ROOT_OBJECT))
        self.assertEqual(p.words(p.READER + 0x50)[0], 0)
        self.assertEqual(p.words(p.READER + 0xB4)[0], 0)

    def test_terminal_must_return_one(self):
        self.p.mode = "terminal_zero"
        self.assertEqual(self.p.parse_private(), POISONED)

    def test_root_and_full_object_count_required(self):
        self.p.mode = "short_count"
        self.assertEqual(self.p.parse_private(), POISONED)

    def test_post_restore_string_scope(self):
        p = self.p
        self.assertEqual(p.call("string", p.STATE, p.READER, 176152), 0)
        self.assertEqual(p.parse_private(), 1)
        self.assertEqual(p.call("string", p.STATE, p.READER, 176152), p.PRIVATE_POOL + 176152)
        for offset in (0, 176151, len(POOL), 0xFFFFFFFF):
            self.assertEqual(p.call("string", p.STATE, p.READER, offset), 0)
        self.assertEqual(p.call("string", p.STATE, p.READER + 0x100, 176152), 0)
        p.write_words(p.READER + 0x14, p.PRIVATE_POOL)
        self.assertEqual(p.call("string", p.STATE, p.READER, 176152), 0)

    def test_native_selector_cannot_publish_failed_partial_root(self):
        p = self.p; p.mode = "arbitrary_error"; p.outer_bridge = True
        self.assertEqual(p.select(), 2)
        self.assertEqual(p.words(p.SCREEN + 12)[0], 0)
        self.assertEqual(p.words(p.STATE + 108)[0], p.ROOT_OBJECT)

    def test_mutation_omitting_quarantine_exposes_native_partial_publication(self):
        # Isolated temp source; production source is never edited/restored.
        with tempfile.TemporaryDirectory(prefix="fp-reader-mutation-") as temp:
            source = Path(temp) / "native_reader.c"
            source.write_text((HERE / "native_reader.c").read_text().replace(
                "WORD(r, 0x50) = 0;", "/* mutation: count not cleared */").replace(
                "WORD(r, 0xb4) = 0;", "/* mutation: root not cleared */"))
            mutant = probe_class()(compile_text(temp, source))
            try:
                self.assertEqual(mutant.initialize(), 0)
                mutant.mode = "arbitrary_error"; mutant.outer_bridge = True
                self.assertEqual(mutant.select(), 0)
                self.assertEqual(mutant.words(mutant.SCREEN + 12)[0], mutant.ROOT_OBJECT)
            finally:
                mutant.close()


def main():
    global PAGE, POOL, IMAGE, TEXT, PROBE, ELF, PINS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seg0", type=Path, default=ROOT / "out/seg0_c0000000.bin")
    parser.add_argument("--candidate", type=Path, default=ROOT /
                        "projects/lossless-sup/build/mainb2-pure-fixed-20260916")
    parser.add_argument("--profile", choices=("pure-fixed-gated", "native-permission-gated"),
                        default="pure-fixed-gated")
    args = parser.parse_args()
    ELF = load_module("reader_strict_elf", ROOT / "research/ui/tools/arm_text/elf_text.py")
    PROBE = load_module("reader_native_page", ROOT / "research/ui/tools/native_page_probe/page_probe.py")
    IMAGE = PROBE.registry.load_firmware(args.seg0)
    PAGE = (args.candidate / "MainB2.fpLossless.gated.page").read_bytes()
    POOL = (args.candidate / "MainB2.fpLossless.strings").read_bytes()
    pins = {
        "pure-fixed-gated": (179725, 176240, 0x1AC9054D, 0x6BAB62D9, 1538,
                             "33946d01b1fd9bca7cba72ef55ef53e16a0469a00e5a29f35896a46e43023633",
                             "ea773283be884f8c40ca1f08d759d94157d3d58200398a92805ef3f8cd09ad96"),
        "native-permission-gated": (179907, 176300, 0x05CDA725, 0x2EF40778, 1541,
                                    "e602a154162790287fed8a182e07fda0af5d02509b59300a25725fe923c71dea",
                                    "3ff3ea5264f8a16aec7fac30160cc8065dfe1d2b896fb7b40c3212de5cf7d3ba")}
    length, pool_length, page_fnv, pool_fnv, records, page_sha, pool_sha = pins[args.profile]
    if (len(PAGE), len(POOL), fnv(PAGE), fnv(POOL), hashlib.sha256(PAGE).hexdigest(),
        hashlib.sha256(POOL).hexdigest()) != (length, pool_length, page_fnv, pool_fnv, page_sha, pool_sha):
        raise RuntimeError("candidate does not match exact build-pinned profile")
    PINS = (page_fnv, pool_fnv, records)
    with tempfile.TemporaryDirectory(prefix="fp-native-reader-") as temp:
        TEXT = compile_text(temp)
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(ReaderTests))
    passed = result.wasSuccessful() and not result.skipped
    print(json.dumps({"kind": "compiled_arm_native_reader_bridge", "passed": passed,
                      "tests": result.testsRun, "skipped": len(result.skipped), "profile": args.profile,
                      "camera_accessed": False, "installer_ready": False, "deployed": False,
                      "seg0_sha256": hashlib.sha256(IMAGE).hexdigest(),
                      "candidate_page_sha256": hashlib.sha256(PAGE).hexdigest(),
                      "candidate_pool_sha256": hashlib.sha256(POOL).hexdigest(),
                      "text_sha256": hashlib.sha256(TEXT.code).hexdigest(), "text_bytes": len(TEXT.code),
                      "function_offsets": TEXT.functions, "covered_native_intervals": sorted(COVERAGE),
                      "source_hashes": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                        for path in (HERE / "native_reader.c", HERE / "native_reader.h",
                                                     HERE / "test_native_reader.py", Path(ELF.__file__),
                                                     Path(PROBE.__file__), Path(PROBE.registry.__file__))},
                      "bounds": {"instructions_per_call": 12000000, "timeout_us": 15000000,
                                 "callee_saved_r4_r11_checked": True, "sp_restoration_checked": True,
                                 "native_parser_call_sp8_checked": True},
                      "substitutes": ["intermediate component records", "heap/arena/component sizes",
                                      "opaque app preflight", "root release/owner services"],
                      "remaining": ["native outer-entry initialization/retry/drain gate",
                                    "actual component construction and native GUI lifetime",
                                    "hook coexistence/installation/publication/journal", "rendering"]}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
