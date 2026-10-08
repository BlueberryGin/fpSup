#!/usr/bin/env python3
"""Stock creator abort can discard numbered frames without dispatching them.

Real C037DD50, C0381090/C0381008, C037DEE0 and C037E008 instructions;
explicit mailbox, resource-release and registry-reclaim boundary stubs.
The 50 seeded frames model active filename 60, queued 59/61..106/108 and
external pending 107. Native slot IDs are NOT filename numbers. This is a
mechanism model, not a replay or explanation of a particular camera take.
Filename generation, allocator frees, codec scheduling and SD writes are
not simulated. Reclaim snapshots each slot and pathname BEFORE its free;
no writer/filesystem stub reports success. Child isolation makes JIT failure
an error, never a skip. No card build, USB access or production source edits.
"""
import json
import pathlib
import subprocess
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_native_defer_contract import (  # noqa: E402
    NativeDefer, IMAGE, SEG0_SHA256, pinned_image, CREATOR, REGISTRY,
    COMPLETE, RECLAIM, STACK, STOP, word,
)

ABORT = 0xC037DEE0
ABORT_RECLAIM_CALL = 0xC037DF18
ROM = 0xC0000000
FRAME_ARENA = 0x45100000
MISSING = (59, *range(61, 107), 108)
NUMBERS = (60, *MISSING, 107)
FRAME_BYTES = len(NUMBERS) * 0x2000
WRITER_ENTRIES = (0xC037E0F8, 0xC03A5348, 0xC069ADE0, 0xC0365FB0)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class AbortFIFO(NativeDefer):
    def __init__(self, image):
        from unicorn import UC_HOOK_MEM_WRITE, UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC

        super().__init__(image)
        self.frame_addresses = tuple(FRAME_ARENA + i * 0x2000 for i in range(len(NUMBERS)))
        self.snapshots = []
        self.writer_calls = []
        self.stock_sp4_calls = 0
        self.mu.mem_map(FRAME_ARENA, FRAME_BYTES, UC_PROT_READ | UC_PROT_WRITE)
        # Remove the base fixture's registry entries. All 50 slots are below
        # the native registry's limit of 96; no synthetic slot 107 or 108.
        self.mu.mem_write(REGISTRY, b"\0" * 0x200)
        for slot, (frame, number) in enumerate(zip(self.frame_addresses, NUMBERS)):
            self.put(REGISTRY + 4 + 4 * slot, frame)
            self.put(frame, 1)
            self.put(frame + 4, 1)
            self.put(frame + 0x10FC, slot)
            self.put(frame + 0x1100, 77)  # take discriminator, NOT filename number
            self.put(frame + 0x1104, 8)
            self.put(frame + 0x1118, 0xFFFFFFFF)
            self.mu.mem_write(frame + 0x1030, self.path(number) + b"\0")
        self.allowed.append((ABORT, COMPLETE))
        self.stubs[RECLAIM] = self.reclaim_snapshot
        for address in WRITER_ENTRIES:
            self.stubs[address] = self.unexpected_writer
        # ARM code is RX, RAM is NX; only host API writes can inject a mutant.
        self.mu.mem_protect(ROM, (len(image) + 0xFFF) & ~0xFFF, UC_PROT_READ | UC_PROT_EXEC)
        self.mu.mem_protect(0x45000000, 0x10000, UC_PROT_READ | UC_PROT_WRITE)
        self.mu.mem_protect(STACK - 0x10000, 0x10000, UC_PROT_READ | UC_PROT_WRITE)
        self.mu.mem_protect(STOP, 0x1000, UC_PROT_READ | UC_PROT_EXEC)
        self.mu.hook_add(UC_HOOK_MEM_WRITE, self.owned_write)

    @staticmethod
    def path(number):
        return ("\\DCIM\\100SIGMA\\A001_%06d.DNG" % number).encode("ascii")

    def pathname(self, frame):
        return bytes(self.mu.mem_read(frame + 0x1030, 0x30)).split(b"\0", 1)[0]

    def _instruction(self, mu, address, size, user):
        if address in self.stubs:
            # Existing invoke() checks the 8-aligned entry SP is restored and
            # r4-r11 survive. Do not impose a new-C ABI on stock internals:
            # DD50 pushes 28 + reserves 24 bytes; E008 pushes 12. These real
            # instructions make some internal calls with SP % 8 == 4.
            sp = self.r("sp")
            require(sp % 4 == 0 and STACK - 0x200 <= sp <= STACK - 0x100,
                    "native call escaped its bounded word-aligned stack")
            self.stock_sp4_calls += sp % 8 == 4
        return super()._instruction(mu, address, size, user)

    def owned_write(self, _mu, _access, address, size, _value, _user):
        spans = ((FRAME_ARENA, FRAME_ARENA + FRAME_BYTES),
                 (REGISTRY, REGISTRY + 0x200), (CREATOR, CREATOR + 0x20),
                 (STACK - 0x10000, STACK))
        require(any(lo <= address and address + size <= hi for lo, hi in spans),
                "ARM write escaped owned fixture memory: %#x" % address)

    def release_resource(self):
        pointer = self.r("r0")
        require(pointer in tuple(f + 0xFE8 for f in self.frame_addresses),
                "resource release does not belong to a seeded frame")
        self.resources.append(("file-arena-release", pointer))
        self.ret()

    def reclaim_snapshot(self):
        require(self.r("r0") == REGISTRY, "wrong registry at reclaim")
        slot = self.r("r1")
        require(slot < len(NUMBERS), "reclaim slot outside seeded registry")
        frame = self.get(REGISTRY + 4 + 4 * slot)
        require(frame == self.frame_addresses[slot], "registry identity changed")
        require(self.get(frame + 0x10FC) == slot, "native frame slot identity changed")
        self.snapshots.append((slot, self.pathname(frame).decode("ascii")))
        self.reclaims.append(slot)
        self.ret()  # snapshot only; does not free a real firmware allocation

    def unexpected_writer(self):
        self.writer_calls.append(self.r("pc"))
        raise AssertionError("abort unexpectedly reached a writer/file-open entry")


def prepared(image):
    r = AbortFIFO(image)
    for slot in range(49):
        require(r.enqueue(slot) == 1, "native enqueue refused seeded frame")
    # Seed an external pending hold, not a claim that Lossless ran here.
    r.put(r.frame_addresses[49] + 0x1104, 0xB)
    require(r.dispatches == [0], "only active filename 60 should be dispatched")
    require(r.get(CREATOR + 0x1C) == 0, "wrong active native slot")
    require(r.get(CREATOR + 0x14) == r.frame_addresses[1], "wrong FIFO head")
    require(r.get(CREATOR + 0x18) == r.frame_addresses[48], "wrong FIFO tail")
    return r


def check_abort(r):
    expected = [(slot, r.path(NUMBERS[slot]).decode("ascii")) for slot in range(1, 49)]
    require(r.snapshots == expected, "discard snapshots do not cover all 48 numbered FIFO entries")
    require(r.resources == [("file-arena-release", r.frame_addresses[i] + 0xFE8)
                            for i in range(1, 49)], "queued resources not released exactly once")
    require((r.get(CREATOR + 0x14), r.get(CREATOR + 0x18)) == (0, 0), "FIFO not empty")
    require(r.get(CREATOR + 0x1C) == 0, "abort changed active native slot")
    require(r.dispatches == [0], "abort dispatched queued frames instead of discarding them")
    require(r.writer_calls == [], "abort reached writer")
    require(all(a not in r.trace for a in WRITER_ENTRIES), "writer executed")
    require(r.get(r.frame_addresses[49] + 0x1104) == 0xB, "outside pending frame changed")
    require(r.pathname(r.frame_addresses[0]) == r.path(60), "active filename changed")
    require(r.pathname(r.frame_addresses[49]) == r.path(107), "outside pending filename changed")


def scenario(name):
    image = pinned_image(IMAGE, SEG0_SHA256)
    r = prepared(image)
    if name == "normal_advance":
        for slot in range(49):
            r.invoke(COMPLETE, CREATOR, slot)
        require(r.dispatches == list(range(49)), "normal completion did not dispatch the FIFO")
        require(r.reclaims == [], "normal advancement unexpectedly called registry reclaim")
    elif name == "missing_reclaim_mutation":
        require(word(image, ABORT_RECLAIM_CALL) == 0xEBFFDC44, "mutation instruction drift")
        r.put(ABORT_RECLAIM_CALL, 0xE1A00000)  # BL C0375030 -> NOP, private ROM only
        r.invoke(ABORT, CREATOR)
        require(len(r.resources) == 48 and not r.snapshots,
                "mutation did not produce the exact missing-reclaim effect")
        try:
            check_abort(r)
        except AssertionError as error:
            require("discard snapshots" in str(error), "mutation caught for unrelated reason")
        else:
            raise AssertionError("missing-reclaim mutation escaped the baseline assertion")
        require(word(image, ABORT_RECLAIM_CALL) == 0xEBFFDC44, "source firmware mutated")
    else:
        require(r.invoke(ABORT, CREATOR) == 1, "native abort did not return success")
        check_abort(r)
        if name == "late_return":
            # Explicitly schedule completion and a late return. This proves
            # dispatch eligibility after abort, NOT actual media completion.
            r.invoke(COMPLETE, CREATOR, 0)
            require(r.enqueue(49) == 1, "outside pending frame could not return")
            require(r.dispatches == [0, 49], "late filename 107 did not dispatch")
            require(r.get(CREATOR + 0x1C) == 49, "late frame is not active")
        else:
            require(name == "abort_fifo", "unknown scenario")
    return {"case": name, "discarded": len(r.snapshots), "writer_calls": len(r.writer_calls),
            "dispatch_filenames": [NUMBERS[i] for i in r.dispatches],
            "stock_internal_sp4_calls": r.stock_sp4_calls}


class NativeAbortFIFOTests(unittest.TestCase):
    def run_child(self, name):
        run = subprocess.run([sys.executable, "-B", str(pathlib.Path(__file__).resolve()),
                              "--case", name], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, f"native abort case {name} failed; "
                         f"returncode={run.returncode}\n{run.stdout}\n{run.stderr}")
        result = json.loads(run.stdout)
        self.assertEqual(result["case"], name)
        self.assertEqual(result["writer_calls"], 0)

    def test_abort_discards_48_numbered_fifo_entries_without_writer(self):
        self.run_child("abort_fifo")

    def test_late_number_can_dispatch_after_the_fifo_was_discarded(self):
        self.run_child("late_return")

    def test_no_abort_advances_the_same_fifo_instead_of_discarding_it(self):
        self.run_child("normal_advance")

    def test_missing_native_reclaim_mutation_is_detected(self):
        self.run_child("missing_reclaim_mutation")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--case":
        print(json.dumps(scenario(sys.argv[2])))
    else:
        unittest.main()
