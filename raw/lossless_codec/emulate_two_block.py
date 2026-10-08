#!/usr/bin/env python3
"""Execute the two-block code under unicorn: the acquire routine and PHASE 9.

Why, when both are already covered by tests: those tests read the SOURCE TEXT.
They catch a deleted line. They do not catch a record written to the wrong
offset, a check that compares the wrong register, a failure path that frees
nothing, or an allocation that is refused and left allocated. Both routines
call the allocator and one of them hands a 4 MiB block to a DMA engine, so
"it reads correctly in the listing" is not enough to take to a camera.

Here they run. The allocator, the clock and the codec are stubbed in Python, so
what is tested is our code's arithmetic, ordering and branching against real
memory - and any firmware address that is not stubbed stops the run loudly
rather than executing whatever the image happens to hold there.

What this cannot say, and must not be reported as if it could: whether the real
allocator behaves like the stub, whether the real codec writes what the stub
writes, how long anything takes, and whether the uncached alias really is
+0x40000000 on the camera. Emulated is not on the camera.
"""
import ctypes
import pathlib
import struct
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import exact_dng_writer_probe as base
import single_frame_codec_probe as probe
from emulate_codec_probe import (IMAGE, IMAGE_BASE, IMAGE_SPAN, UNCACHED, RW,
                                 RWX, DNG_LEN, PIXEL_OFF, GUARD_MAGIC,
                                 POWER_MAGIC, SCRATCH_MAGIC, RETURN_TO, STACK,
                                 STACK_SPAN, F_INIT, F_ENC, F_SIZE, F_CLOCK,
                                 F_ALLOC, F_GET, F_FREE, CACHE_CLEAN,
                                 ICACHE_INVALIDATE, REAL_REGISTER, Unmocked)

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                               UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR,
                               UC_ARM_REG_PC)

# Two blocks, far apart, both in the quarter the code demands of a handle and
# both 1 KiB aligned.
BLOCK_A = 0x45000000
BLOCK_B = 0x53B00000
BLOCK_SPAN = probe.ALLOC_SIZE
FRAME = 0x60000000                 # the writer's DNG buffer, disjoint from both

LIFE_MAGIC = 0x4546494C
OWN_MAGIC = 0x4E574F42
PURPOSE_WORK = 0x4B524F57
PURPOSE_SRC = 0x45435253
LIFE_HELD, LIFE_UNCLEAR, LIFE_FREED = 1, 2, 3

S_COUNT, S_BUF, S_LEN = 0x00, 0x14, 0x18
S_STAGE, S_ERROR = 0x28, 0x2C
S_ALLOCATOR, S_HANDLE, S_ALLOC_END = 0x30, 0x34, 0x38
S_INIT_RET, S_ENC_RET, S_SIZE_RET, S_COMP_SIZE = 0x58, 0x5C, 0x60, 0x64
S_TILE0 = 0xA4
S_POWER_PROOF, S_SCRATCH_PROOF = 0xD4, 0xD8
S_ENC_STARTED, S_ADOPTED = 0xF4, 0xF8

ALLOCATOR_OBJECT = 0xC0FFEE00
# The firmware's own destination-length variable, which PHASE 9 reads back and
# records. It lives above the mapped image, in DRAM the writer owns, so the
# harness has to map it - and seeding it is what lets a test tell a real read
# from the constant the probe also stores.
DECL_VAR = 0xC302CDE0
LOAD_DONE_US = 0xC072F6F8       # stage2's boot stamp; the acquire code
BOOT_VALUE = 0x0BADB001         # reads it into the lifecycle record so a
                                # record from a previous boot can be told
                                # from a live one
DECL_VALUE = 0x305000
STUBBED = (F_INIT, F_ENC, F_SIZE, F_CLOCK, F_ALLOC, F_GET, F_FREE,
           CACHE_CLEAN, ICACHE_INVALIDATE, REAL_REGISTER)


class Machine:
    """Image, two blocks, a stack, and a trap to return to.

    Each block is mapped TWICE onto ONE host buffer - at the handle and at
    handle+UNCACHED - because that is what the camera's DRAM aliases do and
    every one of these routines writes through the uncached view. Two separate
    regions would let a wrong-alias write appear to work.
    """

    def __init__(self, *, blocks=(BLOCK_A, BLOCK_B), declared=DECL_VALUE):
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.uc.mem_map(IMAGE_BASE, IMAGE_SPAN, RWX)
        self.uc.mem_write(IMAGE_BASE, IMAGE.read_bytes()[:IMAGE_SPAN])
        self.uc.mem_map(DECL_VAR & ~0xFFF, 0x1000, RW)
        self.uc.mem_write(DECL_VAR, struct.pack("<I", declared))
        # Already inside the image mapping; only the value is needed.
        self.uc.mem_write(LOAD_DONE_US, struct.pack("<I", BOOT_VALUE))
        self.stores = {}
        for handle in blocks:
            self.map_block(handle)
        self.uc.mem_map(STACK, STACK_SPAN, RW)
        # The frame, mapped TWICE onto one host buffer like the blocks: the
        # copy-frame variant reads it through +UNCACHED, which is the view the
        # engine is handed, and two separate regions would let a wrong-alias
        # read appear to work.
        frame_span = (DNG_LEN + 0xFFF) & ~0xFFF
        self.frame_store = ctypes.create_string_buffer(frame_span)
        for alias in (FRAME, FRAME + UNCACHED):
            self.uc.mem_map_ptr(alias, frame_span, RW,
                                ctypes.addressof(self.frame_store))
        self.uc.mem_map(RETURN_TO & ~0xFFF, 0x1000, RWX)
        self.uc.mem_write(RETURN_TO, struct.pack("<I", 0xEAFFFFFE))   # b .
        self.calls = []
        self.frees = []
        self.handed_on = None
        self.unmocked = None
        self.now = 0x1000
        self.uc.hook_add(UC_HOOK_CODE, self._dispatch)

    def map_block(self, handle, span=BLOCK_SPAN):
        store = ctypes.create_string_buffer(span)
        self.stores[handle] = store
        for alias in (handle, handle + UNCACHED):
            self.uc.mem_map_ptr(alias, span, RW, ctypes.addressof(store))
        return handle

    # ---- memory ----

    def word(self, address):
        return struct.unpack("<I", self.uc.mem_read(address, 4))[0]

    def set_word(self, address, value):
        self.uc.mem_write(address, struct.pack("<I", value & 0xFFFFFFFF))

    def state(self, offset):
        return self.word(probe.STATE + offset)

    def life(self, handle_a, slot, field):
        at = handle_a + probe.LIFE_OFF + probe.LIFE_SLOT[slot] + field * 4
        return self.word(at)

    def life_record(self, handle_a, slot):
        at = handle_a + probe.LIFE_OFF + probe.LIFE_SLOT[slot]
        return [self.word(at + i * 4) for i in range(probe.LIFE_WORDS)]

    def mark(self, handle):
        return [self.word(handle + probe.OWN_OFF),
                self.word(handle + probe.OWN_OFF + 4)]

    def write_life(self, handle_a, slot, *, handle, role, magic=LIFE_MAGIC,
                   allocator=ALLOCATOR_OBJECT, state=LIFE_HELD, tick=0x900,
                   size=BLOCK_SPAN, own=True, purpose=None, seq=1):
        """Seed a record the way the camera would have written it."""
        words = [0] * probe.LIFE_WORDS
        words[probe.L_MAGIC] = magic
        words[probe.L_ROLE] = role
        words[probe.L_ALLOC] = allocator
        words[probe.L_HANDLE] = handle
        words[probe.L_BYTES] = size
        words[probe.L_END] = handle + size
        words[probe.L_TICK] = tick
        words[probe.L_SEQ] = seq
        words[probe.L_PURPOSE] = purpose if purpose is not None else (
            PURPOSE_WORK if role == 1 else PURPOSE_SRC)
        words[probe.L_OWNMARK] = OWN_MAGIC if own else 0
        words[probe.L_HELD] = tick
        words[probe.L_STATE] = state
        at = handle_a + probe.LIFE_OFF + probe.LIFE_SLOT[slot]
        for i, word in enumerate(words):
            self.set_word(at + i * 4, word)

    def write_mark(self, handle, *, magic=OWN_MAGIC, names=None):
        self.set_word(handle + probe.OWN_OFF, magic)
        self.set_word(handle + probe.OWN_OFF + 4,
                      handle if names is None else names)

    # ---- stubs ----

    def _dispatch(self, uc, address, size, user):
        if address == RETURN_TO:
            uc.emu_stop()
            return
        if IMAGE_BASE <= address < IMAGE_BASE + IMAGE_SPAN:
            if self.in_our_code(address):
                return
            if address in STUBBED:
                self.calls.append(address)
                getattr(self, f"_stub_{address:08x}")(uc)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
                return
            self.unmocked = address
            uc.emu_stop()

    def in_our_code(self, address):
        return probe.CODE <= address < probe.CODE + len(self.code_bytes)

    def _stub_c002b6e0(self, uc):                      # F_CLOCK
        uc.reg_write(UC_ARM_REG_R0, self.now)
        self.now += 1000

    def _stub_c001cf78(self, uc):                      # F_ALLOC
        uc.reg_write(UC_ARM_REG_R0, self.allocator_result)

    def _stub_c001d038(self, uc):                      # F_GET
        uc.reg_write(UC_ARM_REG_R0, self.get_result)

    def _stub_c001d2b8(self, uc):                      # F_FREE
        self.frees.append((uc.reg_read(UC_ARM_REG_R0),
                           uc.reg_read(UC_ARM_REG_R1)))
        uc.reg_write(UC_ARM_REG_R0, 0)

    def _stub_c069ac88(self, uc):                      # the writer's own routine
        """The tail call every hook image ends in. Reached once, at the end,
        with the arguments the writer was called with: this phase must hand the
        frame on exactly as it arrived, so what it was handed is recorded."""
        self.handed_on = (uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1),
                          uc.reg_read(UC_ARM_REG_R2))
        uc.reg_write(UC_ARM_REG_R0, 0)

    def _stub_c000e91c(self, uc):
        uc.reg_write(UC_ARM_REG_R0, 0)

    def _stub_c000eabc(self, uc):
        uc.reg_write(UC_ARM_REG_R0, 0)


class AcquireEmulator(Machine):
    """Run acquire_second_block.S and read back what it did.

    Every failure mode is a constructor argument, because the cases worth
    testing are the ones where the routine must refuse AND leave nothing
    allocated behind.
    """

    def __init__(self, *, allocator=ALLOCATOR_OBJECT, handle=BLOCK_B,
                 record_a=True, record_a_kwargs=None, mark_a=True,
                 mark_a_names=None, clock_start=0x1000, record_a_tick=0x900,
                 scratch_proof=True, handle_a=BLOCK_A, mark_sticks=True,
                 block_b_record=None, extra_blocks=()):
        blocks = [handle_a]
        # A handle the routine will refuse before it writes anything needs no
        # memory behind it - and cannot have any: unicorn maps whole pages and
        # will not overlap two regions, which is exactly what a misaligned or
        # overlapping handle would ask for. Mapping it would also hide the
        # thing under test, by making the block look usable.
        if (handle and handle != handle_a and not handle & 0xFFF
                and not (handle_a <= handle < handle_a + BLOCK_SPAN)):
            blocks.append(handle)
        blocks.extend(extra_blocks)
        super().__init__(blocks=tuple(dict.fromkeys(blocks)))
        self.handle_a = handle_a
        self.allocator_result = allocator
        self.get_result = handle
        self.now = clock_start
        self.code_bytes = probe.build_acquire(
            base.DEFAULT_FPSUP,
            placed={probe.CLAIM_NAMES["code"]: probe.CODE,
                    probe.CLAIM_NAMES["state"]: probe.STATE})
        self.uc.mem_write(probe.CODE, self.code_bytes)

        for i in range(probe.STATE_WORDS):
            self.set_word(probe.STATE + i * 4, 0)
        self.set_word(probe.STATE + S_COUNT, 3)
        self.set_word(probe.STATE + S_ALLOCATOR, ALLOCATOR_OBJECT)
        self.set_word(probe.STATE + S_HANDLE, handle_a)
        self.set_word(probe.STATE + S_ALLOC_END, handle_a + BLOCK_SPAN)
        if scratch_proof:
            self.set_word(probe.STATE + S_SCRATCH_PROOF, SCRATCH_MAGIC)
        if record_a:
            fields = {"handle": handle_a, "role": 1, "tick": record_a_tick}
            fields.update(record_a_kwargs or {})
            self.write_life(handle_a, "A", **fields)
        if mark_a:
            self.write_mark(handle_a, names=mark_a_names)
        if block_b_record is not None:
            self.write_life(handle_a, "B", **block_b_record)
        if not mark_sticks and handle:
            self._break_the_mark(handle)

    def _break_the_mark(self, handle):
        """Memory that takes the write and does not hold it.

        A read hook that zeroes the word before the read completes: the mark is
        stored, then read back as zero. This is the failure a block we cannot
        really write would produce, and the routine has to free the block and
        say so rather than carry on with a record nothing backs.
        """
        target = handle + UNCACHED + probe.OWN_OFF

        def wipe(uc, access, address, size, value, user):
            uc.mem_write(target, b"\x00" * 4)
        self.uc.hook_add(UC_HOOK_MEM_READ, wipe, begin=target, end=target + 3)

    def run(self):
        sp = STACK + STACK_SPAN - 0x100
        self.uc.reg_write(UC_ARM_REG_SP, sp)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN_TO)
        self.uc.emu_start(probe.CODE, RETURN_TO, count=5_000_000)
        if self.unmocked is not None:
            raise Unmocked(f"unstubbed firmware call at 0x{self.unmocked:08X}")
        self.status = self.uc.reg_read(UC_ARM_REG_R0)
        self.sp_after = self.uc.reg_read(UC_ARM_REG_SP)
        self.sp_returned = self.sp_after == sp
        return self


class BoundProbeEmulator(Machine):
    """Run the PHASE=9 two-block image at the writer hook.

    The source is generated into block B by the image itself, the destination
    and the guards are in block A, and the codec is stubbed. seg is never
    written by this phase, which is the property that lets it run at all, so
    the frame is checked afterwards for exactly that.
    """

    def __init__(self, *, init_ret=1, enc_ret=1, size_ret=1, tile_sizes=None,
                 record_b=True, record_b_kwargs=None, mark_b=True,
                 mark_b_names=None, record_a=True, record_a_kwargs=None,
                 mark_a=True, handle_b=BLOCK_B, overwrite=0,
                 declared=DECL_VALUE, variant="bound-probe-two"):
        blocks = [BLOCK_A]
        if handle_b:
            blocks.append(handle_b)
        super().__init__(blocks=tuple(dict.fromkeys(blocks)), declared=declared)
        self.init_ret, self.enc_ret, self.size_ret = init_ret, enc_ret, size_ret
        self.tile_sizes = list(tile_sizes) if tile_sizes else [140000] * 12
        self.overwrite = overwrite
        self.allocator_result = ALLOCATOR_OBJECT
        self.get_result = 0                    # this phase allocates nothing
        self.init_struct = None
        self.variant = variant
        self.code_bytes = probe.build_image(variant, base.DEFAULT_FPSUP)
        self.uc.mem_write(probe.CODE, self.code_bytes)
        self.set_word(base.HOOK_SITE, base.HOOK_ARMED)

        for i in range(probe.STATE_WORDS):
            self.set_word(probe.STATE + i * 4, 0)
        self.set_word(probe.STATE + S_ALLOCATOR, ALLOCATOR_OBJECT)
        self.set_word(probe.STATE + S_HANDLE, BLOCK_A)
        self.set_word(probe.STATE + S_POWER_PROOF, POWER_MAGIC)
        self.set_word(probe.STATE + S_SCRATCH_PROOF, SCRATCH_MAGIC)
        for offset in (probe.WORK_CAP, probe.TABLE_OFFSET + 0x30,
                       probe.TABLE_TEMP_OFFSET + 0x30):
            self.set_word(BLOCK_A + offset, GUARD_MAGIC)
        if record_a:
            fields = {"handle": BLOCK_A, "role": 1}
            fields.update(record_a_kwargs or {})
            self.write_life(BLOCK_A, "A", **fields)
        if mark_a:
            self.write_mark(BLOCK_A)
        if record_b:
            fields = {"handle": handle_b, "role": 2}
            fields.update(record_b_kwargs or {})
            self.write_life(BLOCK_A, "B", **fields)
        if mark_b and handle_b:
            self.write_mark(handle_b, names=mark_b_names)

        # A frame with a TIFF header, which the common prologue requires, and
        # recognisable pixels so a copy of them can be told from anything else.
        self.uc.mem_write(FRAME, struct.pack("<I", 0x002A4949))
        self.frame_pixels = bytes(((i * 37 + 11) & 0xFF) for i in range(0x1000))
        self.uc.mem_write(FRAME + PIXEL_OFF, self.frame_pixels)
        self.seg = STACK + 0x100
        self.set_word(self.seg, FRAME)
        self.set_word(self.seg + 4, DNG_LEN)
        self.frame_before = bytes(self.uc.mem_read(FRAME, 0x1000))

    def result(self, offset):
        return self.word(BLOCK_A + probe.RESULT_OFF + offset)

    def guards(self):
        """The five words, as the probe read them back into the result block."""
        return [self.result(0x14), self.result(0x18), self.result(0x1C),
                self.result(0x20), self.result(0x24)]

    def declared(self):
        return {"constant": self.result(0x28),
                "readback_address": self.result(0x2C),
                "readback_value": self.result(0x30)}

    def _stub_c05a6890(self, uc):                      # F_INIT
        init = uc.reg_read(UC_ARM_REG_R0)
        self.init_struct = [self.word(init + i * 4) for i in range(9)]
        self.encode_output = self.init_struct[6]
        self.size_table = self.init_struct[7]
        uc.reg_write(UC_ARM_REG_R0, self.init_ret)

    def _stub_c05a6920(self, uc):                      # F_ENC
        if self.overwrite:
            # An engine that writes past its declared length, which is the
            # whole question the guards are there to answer.
            at = self.encode_output + probe.PROBE_DECLARED
            self.uc.mem_write(at, b"\xEE" * self.overwrite)
        uc.reg_write(UC_ARM_REG_R0, self.enc_ret)

    def _stub_c05a6990(self, uc):                      # F_SIZE
        table = uc.reg_read(UC_ARM_REG_R0)
        if self.size_ret == 1:
            for i, size in enumerate(self.tile_sizes):
                self.set_word(table + i * 4, size)
        uc.reg_write(UC_ARM_REG_R0, self.size_ret)

    def run(self):
        sp = STACK + STACK_SPAN - 0x100
        self.uc.reg_write(UC_ARM_REG_R0, 0xC3A6F3BC)
        self.uc.reg_write(UC_ARM_REG_R1, self.seg)
        self.uc.reg_write(UC_ARM_REG_R2, 2)
        self.uc.reg_write(UC_ARM_REG_R3, 0)
        self.uc.reg_write(UC_ARM_REG_SP, sp)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN_TO)
        self.uc.emu_start(probe.CODE, RETURN_TO, count=60_000_000)
        if self.unmocked is not None:
            raise Unmocked(f"unstubbed firmware call at 0x{self.unmocked:08X}")
        self.sp_after = self.uc.reg_read(UC_ARM_REG_SP)
        self.frame_after = bytes(self.uc.mem_read(FRAME, 0x1000))
        self.seg_after = [self.word(self.seg), self.word(self.seg + 4)]
        return self


def selfcheck():
    """One clean run of each, used as the capability gate for these tests."""
    acquire = AcquireEmulator().run()
    if acquire.status != 0:
        print(f"SELFCHECK RAN BUT WRONG acquire status={acquire.status}")
        return 3
    bound = BoundProbeEmulator().run()
    if bound.state(S_ERROR) != 0:
        print(f"SELFCHECK RAN BUT WRONG bound error={bound.state(S_ERROR)}")
        return 3
    print("SELFCHECK OK")
    return 0


if __name__ == "__main__":
    if "--selfcheck" in sys.argv[1:]:
        raise SystemExit(selfcheck())
    a = AcquireEmulator().run()
    print("acquire status", a.status, "frees", a.frees)
    print("record B      ", [hex(w) for w in a.life_record(BLOCK_A, "B")])
    b = BoundProbeEmulator().run()
    print("bound error   ", b.state(S_ERROR), "stage", b.state(S_STAGE))
    print("guards        ", [hex(g) for g in b.guards()])
    print("declared      ", {k: hex(v) for k, v in b.declared().items()})
