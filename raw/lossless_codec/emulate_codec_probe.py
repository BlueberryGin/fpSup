#!/usr/bin/env python3
"""Run the codec probe's ARM code under unicorn, against the real firmware image.

Why this exists: every test of PHASE=7 so far asserts on the SOURCE TEXT. That
catches a deleted line; it does not catch code that assembles, reads plausibly
and copies the wrong bytes to the wrong place. Copy-back writes into the
camera's own frame buffer, so "it looked right in the listing" is not good
enough to take to a camera.

Here the probe executes. The arena and the writer's buffer are real memory, the
codec is stubbed in Python, and afterwards the buffer is parsed by
verify_compressed_dng - the same checker that will be pointed at the card. If
the emulated buffer is not a valid compressed DNG, neither will the card's be.

Two things the aliasing has to get right, because the probe depends on them:
the arena and the writer's buffer are each mapped TWICE, at the handle address
and at handle+UNCACHED, onto ONE host buffer. That is what the camera's three
DRAM aliases do, and if the emulator faked it with two separate regions the
copy would appear to work while writing somewhere nothing reads.

What this CANNOT tell you, and must not be reported as if it could: whether the
real codec writes what the stub writes, how long any of it takes, whether the
uncached alias really is +0x40000000 for the writer's buffer on the camera, and
whether anything else holds that buffer. Emulated is not on the camera.

One gap worth naming, because a mutation proved it: because both aliases are
mapped onto ONE host buffer, a copy that went through the CACHED alias instead
passes every test here. That choice cannot be checked by emulation at all - it
is a hardware property - so it is pinned by
test_copyback_uses_the_uncached_alias_on_both_sides in the source-text tests
instead. A mutation that does not fail means the test checks less than its name
says, and this one did not fail.
"""
import ctypes
import pathlib
import struct
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import exact_dng_writer_probe as base
import single_frame_codec_probe as probe

from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE,
                     UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC)
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                               UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR,
                               UC_ARM_REG_PC)

IMAGE = pathlib.Path("/Users/dido/Developer.localized/SIGMAfp_re/out/MAIN_c0000000.bin")
IMAGE_BASE = 0xC0000000
IMAGE_SPAN = 0x03000000
UNCACHED = 0x40000000
RW = UC_PROT_READ | UC_PROT_WRITE
RWX = RW | UC_PROT_EXEC

# Values the probe requires of its inputs.
DNG_LEN = 0x318200
PIXEL_OFF = 0x13400
GUARD_MAGIC = 0xA55AA55A
POWER_MAGIC = 0x52575050
SCRATCH_MAGIC = 0x434F4C41
TILE_COUNT = 12
S_STAGE, S_ERROR = 0x28, 0x2C

# Addresses chosen to satisfy the probe's own checks: both are in the
# 0x4..0x7 quarter it demands of a handle, and both are 0x400-aligned.
ARENA = 0x45000000
BUFFER = 0x53B00000
STACK = 0x20000000
STACK_SPAN = 0x10000
RETURN_TO = 0x1000            # a distinct address that means "the probe returned"

F_INIT, F_ENC, F_SIZE, F_CLOCK = 0xC05A6890, 0xC05A6920, 0xC05A6990, 0xC002B6E0
F_ALLOC, F_GET, F_FREE = 0xC001CF78, 0xC001D038, 0xC001D2B8
CACHE_CLEAN, ICACHE_INVALIDATE = 0xC000E91C, 0xC000EABC

# Every firmware address the emulator is willing to see called. Anything else
# raises, per the rule that an unmocked call must fail loudly rather than run
# the image's real code and hand back garbage.
# The original routine the hook replaced. The probe tail-calls it, so reaching
# it means the probe ran to the end; the stub records what the writer was
# actually handed, which is the whole point of the copy-back check.
REAL_REGISTER = 0xC069AC88

STUBBED = (F_INIT, F_ENC, F_SIZE, F_CLOCK, F_ALLOC, F_GET, F_FREE,
           CACHE_CLEAN, ICACHE_INVALIDATE, REAL_REGISTER)


class Unmocked(Exception):
    """A firmware routine was called that the harness does not stub."""


class CodecEmulator:
    """One run of the probe. Construct, seed, call, then read memory."""

    def __init__(self, kind="copy-back", *, tile_sizes=None, stock=None):
        self.kind = kind
        self.tile_sizes = list(tile_sizes) if tile_sizes else [100016] * TILE_COUNT
        self.calls = []
        self.now = 0x1000
        self.encode_output = None
        self.registered = None
        self.size_table = None
        self.unmocked = None

        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        # The image, writable: the probe restores the hook word inside it, and
        # its own code and state live in the cave region.
        self.uc.mem_map(IMAGE_BASE, IMAGE_SPAN, RWX)
        self.uc.mem_write(IMAGE_BASE, IMAGE.read_bytes()[:IMAGE_SPAN])

        # One host buffer behind two addresses, the way the DRAM aliases work.
        def page(n):
            return (n + 0xFFF) & ~0xFFF
        self.arena_span = page(probe.ALLOC_SIZE)
        self.buffer_span = page(DNG_LEN + 0x1000)
        self.arena_store = ctypes.create_string_buffer(self.arena_span)
        self.buffer_store = ctypes.create_string_buffer(self.buffer_span)
        for addr, store, span in ((ARENA, self.arena_store, self.arena_span),
                                  (BUFFER, self.buffer_store, self.buffer_span)):
            for alias in (addr, addr + UNCACHED):
                self.uc.mem_map_ptr(alias, span, RW, ctypes.addressof(store))

        self.uc.mem_map(STACK, STACK_SPAN, RW)
        self.uc.mem_map(RETURN_TO & ~0xFFF, 0x1000, RWX)

        self.uc.hook_add(UC_HOOK_CODE, self._dispatch)
        self._seed(stock)
        self._place()

    # ---- memory helpers, independent of the probe's own bookkeeping ----

    def word(self, addr):
        return struct.unpack("<I", self.uc.mem_read(addr, 4))[0]

    def set_word(self, addr, value):
        self.uc.mem_write(addr, struct.pack("<I", value & 0xFFFFFFFF))

    def state(self, offset):
        return self.word(probe.STATE + offset)

    def result(self):
        """PHASE=7's three words, which live in the retained block rather than
        the state block - the state block is full and the words that looked
        free are a gate's input."""
        at = ARENA + probe.RESULT_OFF
        return {"file_bytes": self.word(at), "copy_bytes": self.word(at + 4),
                "fit": self.word(at + 8), "t_after": self.word(at + 12)}

    def arena_bytes(self, offset, length):
        return bytes(self.uc.mem_read(ARENA + offset, length))

    def buffer_bytes(self, offset=0, length=None):
        return bytes(self.uc.mem_read(BUFFER + offset,
                                      DNG_LEN if length is None else length))

    # ---- setup ----

    def _seed(self, stock):
        """The state the host seeds, the guards the scratch phase left, the
        source DNG the writer hands over, and the IFD template."""
        for i in range(probe.STATE_WORDS):
            self.set_word(probe.STATE + i * 4, 0)
        self.set_word(probe.STATE + 0x30, 0xC0FFEE00)        # S_ALLOCATOR
        self.set_word(probe.STATE + 0x34, ARENA)             # S_HANDLE
        self.set_word(probe.STATE + 0xD4, POWER_MAGIC)       # S_POWER_PROOF
        self.set_word(probe.STATE + 0xD8, SCRATCH_MAGIC)     # S_SCRATCH_PROOF

        # The three guard words the scratch phase wrote. The probe refuses to
        # encode into a block whose guards are gone.
        self.set_word(ARENA + probe.WORK_CAP, GUARD_MAGIC)
        self.set_word(ARENA + probe.TABLE_OFFSET + 0x30, GUARD_MAGIC)
        self.set_word(ARENA + probe.TABLE_TEMP_OFFSET + 0x30, GUARD_MAGIC)

        # The source frame. A real header so the header copy and the template
        # patch operate on genuine camera metadata.
        header = self._stock_header(stock)
        self.uc.mem_write(BUFFER, header)
        self.uc.mem_write(BUFFER + len(header),
                          bytes(((i * 31 + 7) & 0xFF)
                                for i in range(DNG_LEN - len(header))))
        self.stock_source = self.buffer_bytes()

        block, offset_table, count_table = probe.build_ifd_template(self._stock_path(stock))
        self.uc.mem_write(ARENA + probe.TEMPLATE_OFF, bytes(block))
        self.set_word(ARENA + probe.TEMPLATE_META, offset_table)
        self.set_word(ARENA + probe.TEMPLATE_META + 4, count_table)

        # The writer's segment: {buffer, length}. The probe requires kind 2.
        self.seg = STACK + 0x100
        self.set_word(self.seg, BUFFER)
        self.set_word(self.seg + 4, DNG_LEN)

    @staticmethod
    def _stock_path(stock):
        if stock is not None:
            return pathlib.Path(stock)
        return pathlib.Path("/Users/dido/Developer.localized/SIGMAfp_re/projects/open-gate/"
                            "captures/opengate_ev_bug_20260914/dng/A001_083_FHD_f07.DNG")

    def _stock_header(self, stock):
        data = self._stock_path(stock).read_bytes()
        if len(data) < PIXEL_OFF:
            raise RuntimeError("stock frame is shorter than one header")
        return data[:PIXEL_OFF]

    def _place(self):
        code = probe.build_image(self.kind, base.DEFAULT_FPSUP)
        self.uc.mem_write(probe.CODE, code)
        self.code_bytes = code
        # The probe restores the hook word, so the site must hold the armed
        # value first - exactly as it would on the camera.
        self.set_word(base.HOOK_SITE, base.HOOK_ARMED)
        self.uc.mem_write(RETURN_TO, struct.pack("<I", 0xEAFFFFFE))  # b .

    # ---- stubs ----

    def _dispatch(self, uc, address, size, user):
        if address == RETURN_TO:
            uc.emu_stop()
            return
        if IMAGE_BASE <= address < IMAGE_BASE + IMAGE_SPAN:
            if probe.CODE <= address < probe.CODE + len(self.code_bytes):
                return
            if address in STUBBED:
                self.calls.append(address)
                getattr(self, f"_stub_{address:08x}")(uc)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
                return
            self.unmocked = address
            uc.emu_stop()

    def _stub_c002b6e0(self, uc):                      # F_CLOCK
        uc.reg_write(UC_ARM_REG_R0, self.now)
        self.now += 13652

    def _stub_c05a6890(self, uc):                      # F_INIT
        init = uc.reg_read(UC_ARM_REG_R0)
        self.init_struct = [self.word(init + i * 4) for i in range(9)]
        self.encode_output = self.init_struct[6]
        self.size_table = self.init_struct[7]
        uc.reg_write(UC_ARM_REG_R0, 1)

    def _stub_c05a6920(self, uc):                      # F_ENC
        """Write framed tiles where F_INIT was told to put them. Framed the way
        the codec frames them, so verify_compressed_dng can check the result."""
        position = self.encode_output
        for size in self.tile_sizes:
            body = bytes(((i * 7 + 3) & 0xFF) for i in range(size - 6))
            uc.mem_write(position, b"\xff\xd8\xff\xc3" + body + b"\xff\xd9")
            position += size
        self.encoded_end = position
        uc.reg_write(UC_ARM_REG_R0, 1)

    def _stub_c05a6990(self, uc):                      # F_SIZE
        destination = uc.reg_read(UC_ARM_REG_R0)
        for i, size in enumerate(self.tile_sizes):
            self.set_word(destination + i * 4, size)
        uc.reg_write(UC_ARM_REG_R0, 1)

    def _stub_c069ac88(self, uc):                      # the real registration
        """Record exactly what the writer is handed. seg[0] must still be the
        camera's own buffer and seg[1] must be the compressed length."""
        seg = uc.reg_read(UC_ARM_REG_R1)
        self.registered = {"writer": uc.reg_read(UC_ARM_REG_R0),
                           "seg": seg,
                           "buffer": self.word(seg),
                           "length": self.word(seg + 4),
                           "kind": uc.reg_read(UC_ARM_REG_R2)}
        uc.reg_write(UC_ARM_REG_R0, 1)

    def _stub_c001cf78(self, uc):                      # F_ALLOC
        raise Unmocked("F_ALLOC: this phase must adopt the retained block")

    def _stub_c001d038(self, uc):                      # F_GET
        raise Unmocked("F_GET: this phase must adopt the retained block")

    def _stub_c001d2b8(self, uc):                      # F_FREE
        raise Unmocked("F_FREE: this phase must not free")

    def _stub_c000e91c(self, uc):
        uc.reg_write(UC_ARM_REG_R0, 0)

    def _stub_c000eabc(self, uc):
        uc.reg_write(UC_ARM_REG_R0, 0)

    # ---- running ----

    def run(self, writer=0xC3A6F3BC, kind=2):
        sp = STACK + STACK_SPAN - 0x100
        self.uc.reg_write(UC_ARM_REG_R0, writer)
        self.uc.reg_write(UC_ARM_REG_R1, self.seg)
        self.uc.reg_write(UC_ARM_REG_R2, kind)
        self.uc.reg_write(UC_ARM_REG_R3, 0)
        self.uc.reg_write(UC_ARM_REG_SP, sp)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN_TO)
        self.uc.emu_start(probe.CODE, RETURN_TO, count=20_000_000)
        if self.unmocked is not None:
            raise Unmocked(f"unstubbed firmware call at 0x{self.unmocked:08X}")
        self.sp_after = self.uc.reg_read(UC_ARM_REG_SP)
        return self


def selfcheck():
    """One full copy-back run, used as the capability gate.

    This exists so that a machine where unicorn cannot execute this workload
    reports a SKIP rather than killing the test process with a bare exit 132.
    It is run in a SUBPROCESS by emulation_capability, because a fault inside
    unicorn's JIT is a signal, not a Python exception, and nothing in-process
    can catch it.

    A pass here says only "unicorn ran the probe on this machine". It says
    nothing about the camera, and nothing about whether the result is correct -
    that is what the tests are for.
    """
    emu = CodecEmulator("copy-back").run()
    error, result = emu.state(S_ERROR), emu.result()
    if error != 0 or result["fit"] != 1:
        print(f"SELFCHECK RAN BUT WRONG error={error} fit={result['fit']}")
        return 3
    print("SELFCHECK OK")
    return 0


if __name__ == "__main__":
    if "--selfcheck" in sys.argv[1:]:
        raise SystemExit(selfcheck())
    emu = CodecEmulator().run()
    print("stage      ", emu.state(S_STAGE))
    print("error      ", emu.state(S_ERROR))
    print("result     ", emu.result())
    print("seg[1]     ", emu.word(emu.seg + 4))
    print("registered ", emu.registered)
    print("calls      ", [hex(a) for a in emu.calls])
