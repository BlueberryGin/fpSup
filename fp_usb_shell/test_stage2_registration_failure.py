"""Fault-inject both shared power-off registrations before stage2 pass 1.

The tests run the real loader/stage2 against the firmware image through the
existing Unicorn harness.  A broken native Unicorn library is reported as a
skip, never as emulation success.  The source contract check remains active so
the fail-closed branch and forced-result check cannot disappear unnoticed.
"""
from __future__ import annotations

import pathlib
import struct
import subprocess
import sys
import unittest

import test_loader_hook as T

HERE = pathlib.Path(__file__).resolve().parent
STAGE2 = HERE / "asm/stage2.S"


def _unicorn_usable():
    if T.Uc is None or not T.IMAGE.is_file():
        return False
    probe = subprocess.run(
        [sys.executable, "-B", "-c",
         "from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM; "
         "u=Uc(UC_ARCH_ARM,UC_MODE_ARM);u.mem_map(0x1000,0x1000)"],
        capture_output=True, timeout=10)
    return probe.returncode == 0


UNICORN_USABLE = _unicorn_usable()


def require_source_gate(source: str):
    setup = source.index("    bl      lh_setup")
    pass1 = source.index("    bl      s2_pass", setup)
    prefix = source[setup:pass1]
    if "cmp     r9, #0\n    beq     .Ls2_done" not in prefix:
        raise AssertionError("stage2 must stop before pass 1 if journal setup failed")
    forced = source.index("    mov     r2, #1", source.index("lh_setup:"))
    armed = source.index("    mov     r9, r4", forced)
    final = source[forced:armed]
    if "blx     r3\n    cmp     r0, #0\n    beq     8f" not in final:
        raise AssertionError("forced registration result must gate r9")


class Stage2SourceContract(unittest.TestCase):
    def test_gate_is_before_all_product_placement(self):
        source = STAGE2.read_text()
        require_source_gate(source)
        with self.assertRaises(AssertionError):
            require_source_gate(source.replace("    beq     .Ls2_done", "", 1))
        forced = source.index("    mov     r2, #1", source.index("lh_setup:"))
        result_check = "    cmp     r0, #0\n    beq     8f"
        with self.assertRaises(AssertionError):
            require_source_gate(source[:forced] + source[forced:].replace(result_check, "", 1))


class RejectingCamera(T.Camera):
    def __init__(self, loader, binfile, reject):
        self.reject = reject
        self.manager_calls = 0
        super().__init__(loader, binfile)

    def _hook(self, mu, addr, size, data):
        if addr == T.F["POFF_MGR"]:
            self.manager_calls += 1
            if self.reject == "manager" or (self.reject == "forced_manager"
                                            and self.manager_calls == 2):
                self.calls.append("POFF_MGR")
                return self._ret(0)
        if addr == T.F["MEM_HEAP"] and self.reject == "heap":
            self.calls.append("MEM_HEAP")
            return self._ret(0)
        if addr == T.F["MEM_GET"] and self.reject == "get":
            self.calls.append("MEM_GET")
            return self._ret(0)
        if addr == T.F["POFF_ADD"]:
            forced = self.r(T.UC_ARM_REG_R2)
            if self.reject == ("forced_add" if forced else "ordinary_add"):
                self.calls.append("POFF_ADD")
                return self._ret(0)
        return super()._hook(mu, addr, size, data)


def broken_stage2_branch(binfile: bytes, which: str) -> bytes:
    """Make a private VBIN mutation; never edit the shared stage2 source."""
    count = struct.unpack_from("<I", binfile, 4)[0]
    start = 16 + 8 * count
    destination, length = struct.unpack_from("<II", binfile, 16)
    if destination != 0 or length % 4:
        raise AssertionError("first VBIN section is not aligned stage2")
    words = struct.unpack_from(f"<{length // 4}I", binfile, start)
    if which == "setup_gate":
        hits = [i + 1 for i, word in enumerate(words[:-1])
                if word == 0xE3590000 and words[i + 1] >> 24 == 0x0A]
        if len(hits) != 2:              # initial guard and final loader hook
            raise AssertionError(f"unexpected r9 guards: {hits}")
        offset = hits[0]
    elif which == "forced_result":
        hits = [i - 1 for i, word in enumerate(words)
                if word == 0xE1A09004 and i >= 2 and
                words[i - 2] == 0xE3500000 and words[i - 1] >> 24 == 0x0A]
        if len(hits) != 1:
            raise AssertionError(f"unexpected forced registration gate: {hits}")
        offset = hits[0]
    else:
        raise ValueError(which)
    broken = bytearray(binfile)
    struct.pack_into("<I", broken, start + 4 * offset, 0xE1A00000)  # ARM NOP
    return bytes(broken)


@unittest.skipUnless(UNICORN_USABLE, "Unicorn/firmware unavailable or native library failed its smoke test")
class Stage2RegistrationFailure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader, cls.bin = T.build(["--store-boot"])

    def test_every_setup_failure_skips_sections_entry_and_fast_mutations(self):
        for reject in ("heap", "get", "manager", "ordinary_add",
                       "forced_manager", "forced_add"):
            with self.subTest(reject=reject):
                cam = RejectingCamera(self.loader, self.bin, reject)
                sections = [(address, size, bytes(cam.mu.mem_read(address, size)))
                            for address, size in cam.sections()
                            if address >= 0x40000000]
                self.assertTrue(sections)
                before = (cam.word(T.SITE), cam.word(T.ECHO_SLOT), cam.word(T.STORE))
                mark_before = cam.word(T.MARK + 8)
                cam.call(T.CAVE_LOW, r0=0, r1=0)
                self.assertNotIn("ENTRY", cam.calls)
                for address, size, original in sections:
                    self.assertEqual(bytes(cam.mu.mem_read(address, size)), original,
                                     (reject, hex(address)))
                self.assertEqual((cam.word(T.SITE), cam.word(T.ECHO_SLOT),
                                  cam.word(T.STORE)), before)
                self.assertEqual(cam.word(T.MARK + 8), mark_before)
                self.assertEqual(cam.word(T.MARK + 12), 0)

    def test_real_branch_mutations_would_reach_the_product_entry(self):
        # Prove the independent memory/entry assertions above detect these
        # exact safety regressions in executed ARM, not merely in source text.
        for branch, reject in (("setup_gate", "heap"),
                               ("forced_result", "forced_add")):
            with self.subTest(branch=branch):
                broken = broken_stage2_branch(self.bin, branch)
                cam = RejectingCamera(self.loader, broken, reject)
                cam.call(T.CAVE_LOW, r0=0, r1=0)
                self.assertIn("ENTRY", cam.calls)


if __name__ == "__main__":
    unittest.main()
