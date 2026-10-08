"""Exercise the static verifier with real synthetic ARM instruction bytes.

No camera, installer, mocked decoder or full firmware fixture is required.
"""

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest

import verify_hook_sites as verifier


BASE = 0x10000000
NOP = 0xE1A00000


def hexword(value):
    return f"0x{value:08X}"


def instruction(where, word):
    return {"address": hexword(where), "word": hexword(word),
            "bytes_le": struct.pack("<I", word).hex()}


def fixture():
    rom = bytearray(4096)
    sites = []
    for name, offset, target_offset in zip(sorted(verifier.SITE_IDS),
                                          (0x40, 0x240, 0x140, 0x340),
                                          (0x100, 0x180, 0x280, 0x300)):
        site, target = BASE + offset, BASE + target_offset
        word = 0xEB000000 | (((target - site - 8) >> 2) & 0xFFFFFF)
        struct.pack_into("<III", rom, offset - 4, NOP, word, NOP)
        struct.pack_into("<IIII", rom, target_offset, 0xE92D4010, NOP, NOP, NOP)
        struct.pack_into("<I", rom, target_offset + 0x20, 0xE8BD8010)
        sites.append({
            "id": name, "instruction_set": "ARM", "address": hexword(site),
            "expected_word": hexword(word), "expected_bytes_le": struct.pack("<I", word).hex(),
            "original_target": hexword(target), "continuation": hexword(site + 4),
            "patch_length_bytes": 4,
            "context": {"address": hexword(site - 4), "length_bytes": 12,
                        "expected_bytes_le": bytes(rom[offset - 4:offset + 8]).hex(),
                        "instructions": [instruction(site - 4, NOP), instruction(site, word),
                                         instruction(site + 4, NOP)]},
            "callee_entry_guard": {"address": hexword(target), "length_bytes": 16,
                                   "expected_bytes_le": bytes(rom[target_offset:target_offset + 16]).hex(),
                                   "instructions": [instruction(target, 0xE92D4010)]},
            "callee_return_guard": instruction(target + 0x20, 0xE8BD8010),
        })
    document = {"schema": verifier.SCHEMA, "version": 1,
                "rom": {"base_address": hexword(BASE), "length_bytes": len(rom),
                        "sha256": hashlib.sha256(rom).hexdigest(), "path": "synthetic.bin"},
                "sites": sites}
    return document, rom


class HookSiteTests(unittest.TestCase):
    def setUp(self):
        self.document, self.rom = fixture()

    def verify(self):
        return verifier.verify_manifest(self.document, bytes(self.rom))

    def rehash(self):
        self.document["rom"]["sha256"] = hashlib.sha256(self.rom).hexdigest()

    def change_site_word(self, word, index=0):
        site = self.document["sites"][index]
        offset = int(site["address"], 16) - BASE
        struct.pack_into("<I", self.rom, offset, word)
        site["expected_word"], site["expected_bytes_le"] = hexword(word), struct.pack("<I", word).hex()
        site["context"]["expected_bytes_le"] = bytes(self.rom[offset - 4:offset + 8]).hex()
        site["context"]["instructions"][1] = instruction(BASE + offset, word)
        self.rehash()

    def test_real_arm_bytes_verify_forward_and_backward_branches_and_guards(self):
        result = self.verify()
        self.assertTrue(result["verification_passed"])
        self.assertEqual(result["site_count"], 4)
        self.assertEqual(result["rom"]["sha256"], hashlib.sha256(self.rom).hexdigest())
        for source, verified in zip(self.document["sites"], result["sites"]):
            self.assertEqual(verified["decoded_target"], source["original_target"])
            self.assertTrue(verified["continuation_verified"])
            self.assertEqual(set(verified["guards"]), {"context", "callee_entry_guard", "callee_return_guard"})
            self.assertEqual(verified["guards"]["context"]["instruction_words_verified"], 3)

    def test_static_success_never_copies_live_or_deployment_claims(self):
        for key in ("live_verified", "deployment_ready", "installable", "ownership_verified",
                    "stack_alignment_verified", "stack_highwater_verified", "stack_timing_verified",
                    "timing_verified", "hardware_io_performed", "confirmation_authorized", "motion_authorized"):
            self.document[key] = True
        result = self.verify()
        self.assertTrue(result["offline_only"])
        for key in self.document:
            if key.endswith("verified") or key in ("deployment_ready", "installable", "hardware_io_performed",
                                                   "confirmation_authorized", "motion_authorized"):
                self.assertIs(result[key], False, key)

    def test_tampering_outside_selected_sites_is_caught_by_full_rom_hash(self):
        self.rom[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            self.verify()

    def test_rom_truncation_and_appended_bytes_fail_size_check(self):
        for changed in (bytes(self.rom[:-1]), bytes(self.rom) + b"\x00"):
            with self.subTest(size=len(changed)), self.assertRaisesRegex(ValueError, "length mismatch"):
                verifier.verify_manifest(self.document, changed)

    def test_wrong_identity_or_schema_and_bool_integers_are_rejected(self):
        for section, field, value in ((None, "schema", "unknown/v1"), (None, "version", True),
                                      ("rom", "sha256", "z" * 64), ("rom", "length_bytes", True),
                                      ("rom", "base_address", "0x10000001"),
                                      ("rom", "base_address", "0xFFFFFFFF")):
            with self.subTest(field=field, value=value):
                document = copy.deepcopy(self.document)
                (document if section is None else document[section])[field] = value
                with self.assertRaises(ValueError):
                    verifier.verify_manifest(document, bytes(self.rom))

    def test_missing_duplicate_or_extra_selected_sites_are_rejected(self):
        variants = [self.document["sites"][:-1], self.document["sites"] + [self.document["sites"][0]],
                    [self.document["sites"][0]] * 4]
        for sites in variants:
            with self.subTest(ids=[s["id"] for s in sites]):
                document = copy.deepcopy(self.document)
                document["sites"] = sites
                with self.assertRaises(ValueError):
                    verifier.verify_manifest(document, bytes(self.rom))

    def test_site_alignment_range_isa_and_patch_size_are_checked(self):
        for field, value in (("address", hexword(BASE + 0x41)), ("address", hexword(BASE - 4)),
                             ("address", hexword(BASE + len(self.rom))),
                             ("instruction_set", "Thumb"), ("patch_length_bytes", 2),
                             ("patch_length_bytes", True)):
            with self.subTest(field=field, value=value):
                document = copy.deepcopy(self.document)
                document["sites"][0][field] = value
                with self.assertRaises(ValueError):
                    verifier.verify_manifest(document, bytes(self.rom))

    def test_expected_word_bytes_and_actual_rom_are_independent_guards(self):
        site = self.document["sites"][0]
        site["expected_word"] = hexword(NOP)
        with self.assertRaisesRegex(ValueError, "word/bytes mismatch"):
            self.verify()
        site["expected_bytes_le"] = struct.pack("<I", NOP).hex()
        with self.assertRaisesRegex(ValueError, "ROM bytes mismatch"):
            self.verify()

    def test_rehashed_matching_non_bl_instruction_still_fails_branch_decode(self):
        for word in (NOP, 0xEA000000, 0x1B000000, 0xFA000000, 0xE12FFF30):
            with self.subTest(word=hexword(word)):
                self.document, self.rom = fixture()
                self.change_site_word(word)
                with self.assertRaisesRegex(ValueError, "unconditional ARM BL"):
                    self.verify()

    def test_wrong_branch_target_or_continuation_fails_even_with_matching_site_bytes(self):
        for field in ("original_target", "continuation"):
            with self.subTest(field=field):
                document = copy.deepcopy(self.document)
                site = document["sites"][0]
                site[field] = hexword(int(site[field], 16) + 4)
                with self.assertRaisesRegex(ValueError, "target mismatch|continuation must"):
                    verifier.verify_manifest(document, bytes(self.rom))

    def test_correct_bl_encoding_cannot_target_outside_saved_rom(self):
        site = self.document["sites"][0]
        address = int(site["address"], 16)
        target = BASE - 0x100
        self.change_site_word(0xEB000000 | (((target - address - 8) >> 2) & 0xFFFFFF))
        site["original_target"] = hexword(target)
        with self.assertRaisesRegex(ValueError, "target: range outside ROM"):
            self.verify()

    def test_branch_sign_extension_and_32bit_pc_wrap(self):
        self.assertEqual(verifier.decode_arm_bl(0xEB000000, 0x1000), 0x1008)
        self.assertEqual(verifier.decode_arm_bl(0xEBFFFFFF, 0x1000), 0x1004)
        self.assertEqual(verifier.decode_arm_bl(0xEB800000, 0x04000000), 0x02000008)
        self.assertEqual(verifier.decode_arm_bl(0xEB7FFFFF, 0x04000000), 0x06000004)
        self.assertEqual(verifier.decode_arm_bl(0xEB000000, 0xFFFFFFFC), 4)

    def test_context_entry_and_return_bytes_are_verified_despite_updated_whole_rom_hash(self):
        for guard in ("context", "callee_entry_guard", "callee_return_guard"):
            with self.subTest(guard=guard):
                self.document, self.rom = fixture()
                where = int(self.document["sites"][0][guard]["address"], 16)
                self.rom[where - BASE] ^= 1
                self.rehash()
                with self.assertRaisesRegex(ValueError, guard + ": ROM bytes mismatch"):
                    self.verify()

    def test_instruction_word_cannot_disagree_with_correct_enclosing_bytes(self):
        self.document["sites"][0]["context"]["instructions"][0]["word"] = "0xE1A01001"
        with self.assertRaisesRegex(ValueError, "word/bytes mismatch"):
            self.verify()

    def test_instruction_guard_cannot_point_outside_parent_or_repeat_an_address(self):
        context = self.document["sites"][0]["context"]
        outside = instruction(int(context["address"], 16) - 4, 0)
        context["instructions"].append(outside)
        with self.assertRaisesRegex(ValueError, "outside its declared guard"):
            self.verify()
        context["instructions"][-1] = copy.deepcopy(context["instructions"][0])
        with self.assertRaisesRegex(ValueError, "duplicate instruction address"):
            self.verify()

    def test_context_must_include_the_continuation_and_entry_guard_match_the_target(self):
        document = copy.deepcopy(self.document)
        context = document["sites"][0]["context"]
        context["length_bytes"] = 8
        context["expected_bytes_le"] = context["expected_bytes_le"][:16]
        context["instructions"] = context["instructions"][:2]
        with self.assertRaisesRegex(ValueError, "include site and continuation"):
            verifier.verify_manifest(document, bytes(self.rom))
        document = copy.deepcopy(self.document)
        document["sites"][0]["callee_entry_guard"] = copy.deepcopy(document["sites"][1]["callee_entry_guard"])
        with self.assertRaisesRegex(ValueError, "entry guard must match"):
            verifier.verify_manifest(document, bytes(self.rom))

    def test_bad_guard_length_hex_and_range_are_rejected(self):
        for field, value in (("length_bytes", 4), ("length_bytes", True),
                             ("expected_bytes_le", "zz"), ("expected_bytes_le", ""),
                             ("address", hexword(BASE + len(self.rom)))):
            with self.subTest(field=field, value=value):
                document = copy.deepcopy(self.document)
                document["sites"][0]["callee_entry_guard"][field] = value
                with self.assertRaises(ValueError):
                    verifier.verify_manifest(document, bytes(self.rom))

    def write_fixture(self, directory):
        folder = Path(directory)
        manifest, rom = folder / "sites.json", folder / "rom.bin"
        manifest.write_text(json.dumps(self.document), encoding="utf-8")
        rom.write_bytes(self.rom)
        return manifest, rom

    def test_files_and_cli_create_new_report_without_touching_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest, rom = self.write_fixture(folder)
            originals = manifest.read_bytes(), rom.read_bytes()
            target = Path(folder) / "report.json"
            result = verifier.verify_files(manifest, rom)
            self.assertEqual(result["manifest_sha256"], hashlib.sha256(originals[0]).hexdigest())
            self.assertEqual(verifier.main(["--manifest", str(manifest), "--rom", str(rom),
                                            "--output", str(target)]), 0)
            self.assertEqual(json.loads(target.read_text())["site_count"], 4)
            self.assertEqual((manifest.read_bytes(), rom.read_bytes()), originals)

    def test_cli_refuses_existing_report_source_manifest_and_source_rom_as_output(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest, rom = self.write_fixture(folder)
            target = Path(folder) / "report.json"
            target.write_bytes(b"existing evidence\n")
            for destination in (target, manifest, rom):
                with self.subTest(destination=destination.name):
                    original = destination.read_bytes()
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                        verifier.main(["--manifest", str(manifest), "--rom", str(rom),
                                       "--output", str(destination)])
                    self.assertEqual(raised.exception.code, 2)
                    self.assertEqual(destination.read_bytes(), original)

    def test_failed_verification_never_creates_a_success_report(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest, rom = self.write_fixture(folder)
            rom.write_bytes(b"wrong ROM")
            target = Path(folder) / "report.json"
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                verifier.main(["--manifest", str(manifest), "--rom", str(rom), "--output", str(target)])
            self.assertEqual(raised.exception.code, 2)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
