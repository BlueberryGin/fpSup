"""Execute the real ARM MEM1 handlers (CMD 6-9) offline, through to next_round.

The firmware's USB calls are stubs that record what the worker asked for:
StartTransfer snapshots the TRB (and, for EP82, the frame it points at), Wait
plays the host -- for a MEMW data phase it is the DMA landing the bytes -- and
the cache routine counts.  crc32 is the firmware's own, from the image.
"""
import struct
import unittest
import zlib

from armasm import assemble, symbols
from test_worker_upload import IMAGE, UNICORN_RUNS, WORKER, FW_CRC32, FW_TABLE

try:
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R4,
                                   UC_ARM_REG_R6, UC_ARM_REG_SP)
except ImportError:
    pass

CODE = 0x60000000                   # also WCODE_PTR: inside DMA_LO..DMA_HI
POOL = 0x45000000
UNCACHED = POOL + 0x40000000
STATE = 0xC072F000
STACK = 0x70000000
STAGE = POOL + 0x10000
WIN = POOL + 0x6A00
OTHER = 0x50000000                  # some other allocation, for windows
TRBS = 0xC31E3800                   # where the two TRB pointers point
TRB_OUT_AT, TRB_IN_AT = 0xC31E3268, 0xC31E326C
TRB_OUT, TRB_IN = TRBS, TRBS + 0x10

F_DCACHE, FN_STARTX, FN_WAIT, FN_ENDX, FN_DELAY = (
    0xC000E91C, 0xC01E6B80, 0xC01E4E80, 0xC01E6D48, 0xC01F89C4)
THUMB_STUBS = (FN_STARTX, FN_WAIT, FN_ENDX)
XFER_MAX = 0xFFFC00


def frame(cmd, *words, seq=77, flags=0, good_crc=True):
    f = bytearray(64)
    struct.pack_into('<4sBBHIHHI', f, 0, b'FPSH', 1, cmd, flags, seq,
                     4 * len(words), 0, 0)
    struct.pack_into(f'<{len(words)}I', f, 20, *words)
    struct.pack_into('<I', f, 16, zlib.crc32(f) ^ (0 if good_crc else 1))
    return bytes(f)


class Run:
    """What one handler did: calls in order, frames sent, final memory."""
    def __init__(self):
        self.calls = []             # (name, args...)
        self.sent = []              # (status, payload_len, words) per EP82 frame
        self.blocks = []            # (bus address, length) per EP82 non-frame arm
        self.out_arms = []          # (bus address, length) per EP01 arm


@unittest.skipUnless(UNICORN_RUNS, 'Unicorn ARM runtime unavailable')
@unittest.skipUnless(IMAGE.exists(), 'needs the firmware image for crc32')
class WorkerMemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = assemble(WORKER)
        cls.labels = symbols(WORKER)

    def run_cmd(self, request, *, windows=(), data=None, start_rc=0,
                wait_out_rc=0, wait_in_rc=0, short=0, early=False, entry=None):
        """`data`: what the host sends in a MEMW data phase (None = nothing
        arrives, Wait times out).  `short`: bytes the TRB reports left over.
        `early`: the data lands while the worker is still in READY's IN Wait,
        and that Wait eats the OUT bit -- every OUT Wait after it times out."""
        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.mem_map(CODE, 0x1000)
        uc.mem_write(CODE, self.code)
        uc.mem_map(POOL, 0x100000)
        uc.mem_map(UNCACHED, 0x4000)
        uc.mem_map(OTHER, 0x20000)
        uc.mem_map(STATE, 0x1000)
        uc.mem_map(STACK, 0x10000)
        uc.mem_map(0xC31E3000, 0x1000)
        img = IMAGE.read_bytes()
        for page in (FW_CRC32 & ~0xFFF, FW_TABLE & ~0xFFF):
            uc.mem_map(page, 0x2000)
            uc.mem_write(page, img[page - 0xC0000000:page - 0xC0000000 + 0x2000])
        for stub in (F_DCACHE, FN_STARTX, FN_WAIT, FN_ENDX, FN_DELAY):
            page = stub & ~0xFFF
            try:
                uc.mem_map(page, 0x1000)
            except Exception:
                pass
            uc.mem_write(stub, b'\x70\x47' if stub in THUMB_STUBS
                         else struct.pack('<I', 0xE12FFF1E))
        uc.mem_write(STATE + 0x50, struct.pack('<III', POOL, 0x100000, CODE))
        uc.mem_write(TRB_OUT_AT, struct.pack('<II', TRB_OUT, TRB_IN))
        for slot, (base, length, perm) in enumerate(windows):
            uc.mem_write(WIN + 12 * slot, struct.pack('<III', base, length, perm))
        uc.mem_write(UNCACHED, request)
        uc.mem_write(STAGE, b'\xA5' * 0x2000)
        uc.reg_write(UC_ARM_REG_R4, STATE)
        uc.reg_write(UC_ARM_REG_R6, UNCACHED)
        uc.reg_write(UC_ARM_REG_SP, STACK + 0x8000)

        run = Run()
        end = CODE + self.labels['next_round']
        stopped = []

        def to_cpu(bus):
            return (bus + 0x40000000) & 0xFFFFFFFF

        def hook(m, address, _size, _user):
            r0 = m.reg_read(UC_ARM_REG_R0)
            if address == end:
                stopped.append(True)
                m.emu_stop()
            elif address == F_DCACHE:
                run.calls.append(('dcache',))
            elif address == FN_DELAY:
                run.calls.append(('delay', r0))
            elif address == FN_ENDX:
                run.calls.append(('endx', r0))
            elif address == FN_STARTX:
                trb = TRB_OUT if r0 == 2 else TRB_IN
                buf, _, size, ctl = struct.unpack('<IIII', m.mem_read(trb, 16))
                run.calls.append(('startx', r0, buf, size, ctl))
                if r0 == 2:
                    run.out_arms.append((buf, size))
                elif to_cpu(buf) == POOL and size == 64:
                    f = bytes(m.mem_read(UNCACHED, 64))
                    _, _, _, _, seq, plen, status, crc = struct.unpack_from(
                        '<4sBBHIHHI', f)
                    check = bytearray(f)
                    struct.pack_into('<I', check, 16, 0)
                    self.assertEqual(zlib.crc32(check), crc, 'reply frame CRC')
                    self.assertEqual(seq, 77, 'reply keeps the sequence')
                    run.sent.append((status, plen,
                                     struct.unpack_from(f'<{plen // 4}I', f, 20)))
                else:
                    run.blocks.append((buf, size))
                m.reg_write(UC_ARM_REG_R0, start_rc)
            elif address == FN_WAIT:
                run.calls.append(('wait', r0, m.reg_read(UC_ARM_REG_R1)))

                def land():
                    buf, _, size, ctl = struct.unpack('<IIII', m.mem_read(TRB_OUT, 16))
                    if data is None or not ctl & 1 or not run.out_arms:
                        return False
                    m.mem_write(to_cpu(buf), data)
                    left = size - len(data) + short
                    m.mem_write(TRB_OUT + 8, struct.pack('<II', 0x3A000000 | left,
                                                         ctl & ~1))   # HWO clear
                    return True
                if r0 == 1:             # EP01 OUT: the MEMW data phase
                    ok = not early and land()
                    m.reg_write(UC_ARM_REG_R0, wait_out_rc if ok else 0xFFFFFFCE)
                else:
                    if early and wait_in_rc == 0:
                        land()
                    if wait_in_rc == 0:     # the host took it: HWO comes back
                        ctl, = struct.unpack('<I', m.mem_read(TRB_IN + 12, 4))
                        m.mem_write(TRB_IN + 12, struct.pack('<I', ctl & ~1))
                    m.reg_write(UC_ARM_REG_R0, wait_in_rc)
            elif not (CODE <= address < CODE + len(self.code)
                      or FW_CRC32 <= address < FW_CRC32 + 0x60
                      or address in (F_DCACHE + 4, FN_STARTX + 2)):
                raise AssertionError(f'worker escaped to 0x{address:08X}')

        uc.hook_add(UC_HOOK_CODE, hook)
        cmd = request[5]
        entry = entry or {6: 'mem_write', 7: 'mem_read', 8: 'win_set', 9: 'mem_caps'}[cmd]
        # win_set/mem_caps go on through the ordinary reply path; it arms EP82
        # once per 44-byte frame, which the stub records the same way.
        uc.emu_start(CODE + self.labels[entry], CODE + len(self.code),
                     count=2_000_000)
        self.assertTrue(stopped, 'handler did not come back to next_round')
        run.uc = uc
        run.state = struct.unpack('<16I', uc.mem_read(STATE, 64))
        run.out_busy = struct.unpack('<I', uc.mem_read(0xC31E3974, 4))[0]
        run.windows = [struct.unpack_from('<III', uc.mem_read(WIN + 12 * i, 12))
                       for i in range(8)]
        return run

    # --- windows ------------------------------------------------------------
    def test_window_grants_and_refusals(self):
        R, W = 1, 2
        cases = [
            ((0, OTHER, 0x10000, R | W), 0),
            ((0, 0x40001000, 0x1000, R), 0),          # read low: allowed
            ((0, 0x40001000, 0x1000, W), 2),          # write below WRITE_LO
            ((0, POOL + 0x8000, 0x10000, W), 2),      # overlaps the pool header
            ((0, POOL + 0x10000, 0x1000, W), 0),      # the staging region: fine
            ((0, CODE + 0x800, 0x100, W), 2),         # this worker's code
            ((0, CODE - 0x100, 0x200, W), 2),         # straddles its start
            ((0, CODE + 0x800, 0x100, R), 0),         # reading the code: fine
            ((0, 0x7FFFF000, 0x2000, R), 2),          # past DMA_HI
            ((0, 0xC0000000, 0x1000, R), 2),          # the image
            ((0, OTHER, 0, R), 2),
            ((0, OTHER + 2, 0x100, R), 2),
            ((0, OTHER, 0x100, 4), 2),                # unknown permission bit
            ((8, OTHER, 0x100, R), 2),                # no such slot
            ((0, 0xFFFFF000, 0x2000, R), 2),          # wraps
        ]
        for words, want in cases:
            with self.subTest(words=[hex(w) for w in words]):
                run = self.run_cmd(frame(8, *words))
                self.assertEqual(run.sent[-1][2][0], want)
                stored = run.windows[words[0]] if words[0] < 8 else None
                if want == 0:
                    self.assertEqual(stored, tuple(words[1:]))
                elif stored is not None:
                    self.assertEqual(stored, (0, 0, 0))

    def test_window_revoke_and_bad_frame(self):
        run = self.run_cmd(frame(8, 3, 0, 0, 0), windows=[(0, 0, 0)] * 3 +
                           [(OTHER, 0x100, 3)])
        self.assertEqual(run.windows[3], (0, 0, 0))
        run = self.run_cmd(frame(8, 0, OTHER, 0x100, 1, good_crc=False))
        self.assertEqual(run.sent[-1][2][0], 1)
        self.assertEqual(run.windows[0], (0, 0, 0))

    def test_caps(self):
        run = self.run_cmd(frame(9), windows=[(OTHER, 0x100, 3)])
        raw = b''.join(struct.pack(f'<{len(w)}I', *w) for _, _, w in run.sent)
        self.assertEqual(raw[:4], b'MEM1')
        head = struct.unpack_from('<III', raw, 4)
        self.assertEqual(head, (XFER_MAX, POOL, 0x100000))
        self.assertEqual(struct.unpack_from('<III', raw, 16), (OTHER, 0x100, 3))
        self.assertEqual(len(raw), 16 + 96)

    def test_serve_start_forgets_windows(self):
        """A fresh worker (boot or hot swap) must not inherit grants."""
        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.mem_map(CODE, 0x1000)
        uc.mem_write(CODE, self.code)
        uc.mem_map(POOL, 0x100000)
        uc.mem_map(STATE, 0x1000)
        uc.mem_map(STACK, 0x10000)
        uc.mem_write(STATE + 0x50, struct.pack('<I', POOL))
        uc.mem_write(WIN, struct.pack('<III', OTHER, 0x1000, 3) * 8)
        uc.reg_write(UC_ARM_REG_SP, STACK + 0x8000)
        end = CODE + self.labels['next_round']
        uc.hook_add(UC_HOOK_CODE, lambda m, a, *_: m.emu_stop() if a == end else None)
        uc.emu_start(CODE + self.labels['serve'], CODE + len(self.code), count=10_000)
        self.assertEqual(bytes(uc.mem_read(WIN, 96)), b'\0' * 96)

    def test_arm_out_forgets_the_last_command(self):
        """A wake with no new frame must not find the old one to run again."""
        from unicorn.arm_const import UC_ARM_REG_LR
        run = self.run_cmd(frame(9))                 # any machine, then reuse it
        uc = run.uc
        uc.mem_write(UNCACHED, frame(6, STAGE, 0x400, 0) + b'shl echo')
        uc.mem_write(UNCACHED + 20, b'shl ')
        uc.reg_write(UC_ARM_REG_R4, STATE)
        uc.mem_write(STATE + 8, b'\0' * 4)
        uc.reg_write(UC_ARM_REG_SP, STACK + 0x8000)
        uc.reg_write(UC_ARM_REG_LR, CODE + len(self.code))
        uc.emu_start(CODE + self.labels['arm_out'], CODE + len(self.code), count=10_000)
        head = bytes(uc.mem_read(UNCACHED, 24))
        self.assertEqual(head[:8], b'\0' * 8)
        self.assertEqual(head[20:24], b'\0' * 4)

    def run_wait_done(self, ep, ms, hwo_clears_after):
        """wait_done alone: every firmware Wait times out (the bit went to
        somebody else); the controller hands the TRB back after N of them."""
        from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1
        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.mem_map(CODE, 0x1000); uc.mem_write(CODE, self.code)
        uc.mem_map(STACK, 0x10000); uc.mem_map(0xC31E3000, 0x1000)
        uc.mem_map(FN_WAIT & ~0xFFF, 0x1000); uc.mem_write(FN_WAIT, b'\x70\x47')
        uc.mem_write(TRB_OUT_AT, struct.pack('<II', TRB_OUT, TRB_IN))
        trb = TRB_OUT if ep == 1 else TRB_IN
        uc.mem_write(trb + 12, struct.pack('<I', 0x813))
        waits = []

        def hook(m, a, *_):
            if a == FN_WAIT:
                waits.append(m.reg_read(UC_ARM_REG_R1))
                if len(waits) == hwo_clears_after:
                    m.mem_write(trb + 12, struct.pack('<I', 0x812))
                m.reg_write(UC_ARM_REG_R0, 0xFFFFFFFB)            # always "timeout"
        uc.hook_add(UC_HOOK_CODE, hook)
        uc.reg_write(UC_ARM_REG_R0, ep); uc.reg_write(UC_ARM_REG_R1, ms)
        uc.reg_write(UC_ARM_REG_SP, STACK + 0x8000)
        uc.reg_write(UC_ARM_REG_LR, CODE + len(self.code))
        uc.emu_start(CODE + self.labels['wait_done'], CODE + len(self.code), count=100_000)
        return uc.reg_read(UC_ARM_REG_R0), waits

    def test_wait_done_believes_the_trb_not_the_bit(self):
        for ep in (1, 2):
            with self.subTest(ep=ep):
                rc, waits = self.run_wait_done(ep, 100, 2)
                self.assertEqual(rc, 0)                  # done, though Wait said no
                self.assertEqual(waits, [20, 20])
                rc, waits = self.run_wait_done(ep, 100, 99)
                self.assertEqual(rc, 0xFFFFFFFF)         # still out: give up
                self.assertEqual(len(waits), 5)          # 100 ms in 20 ms slices
                rc, waits = self.run_wait_done(ep, 100, 0)
                self.assertEqual(waits, [20] * 5)        # never cleared...
        # ...and the other endpoint's TRB is not the one looked at
        rc, _ = self.run_wait_done(2, 40, 1)
        self.assertEqual(rc, 0)

    # --- reads --------------------------------------------------------------
    def test_read_inside_pool_is_one_block_no_frame(self):
        run = self.run_cmd(frame(7, STAGE + 0x40, 0x3000, 1))
        self.assertEqual(run.blocks, [(STAGE + 0x40 - 0x40000000, 0x3000)])
        self.assertEqual(run.sent, [])
        self.assertIn(('dcache',), run.calls)
        self.assertLess(run.calls.index(('dcache',)),
                        [c[0] for c in run.calls].index('startx'))
        self.assertEqual(run.state[8], 1)                  # served

    def test_read_without_sync_skips_cache(self):
        run = self.run_cmd(frame(7, POOL, 0x100, 0))
        self.assertNotIn(('dcache',), run.calls)
        self.assertEqual(run.blocks, [(POOL - 0x40000000, 0x100)])

    def test_read_refusals_send_only_a_status_frame(self):
        for words, code in (((OTHER, 0x100, 0), 4),
                            ((POOL + 0xFFF00, 0x200, 0), 4),   # runs off the pool
                            ((POOL, XFER_MAX + 4, 0), 2),
                            ((POOL, 0, 0), 2)):
            with self.subTest(words=[hex(w) for w in words]):
                run = self.run_cmd(frame(7, *words))
                self.assertEqual(run.blocks, [])
                self.assertEqual(run.sent, [(code, 4, (code,))])

    def test_read_through_a_granted_window(self):
        run = self.run_cmd(frame(7, OTHER + 0x100, 0x1000, 0),
                           windows=[(OTHER, 0x2000, 1)])
        self.assertEqual(run.blocks, [(OTHER + 0x100 - 0x40000000, 0x1000)])
        run = self.run_cmd(frame(7, OTHER + 0x100, 0x2000, 0),
                           windows=[(OTHER, 0x2000, 1)])
        self.assertEqual(run.sent[0][0], 4)                # 0x100 too far
        run = self.run_cmd(frame(7, OTHER + 4, 0x2000, 0),
                           windows=[(OTHER, 0x2000, 1)])
        self.assertEqual(run.sent[0][0], 4)                # one word too far
        run = self.run_cmd(frame(7, OTHER + 4, 0x1FFC, 0),
                           windows=[(OTHER, 0x2000, 1)])
        self.assertEqual(run.blocks, [(OTHER + 4 - 0x40000000, 0x1FFC)])
        run = self.run_cmd(frame(7, OTHER, 0x100, 0),
                           windows=[(OTHER, 0x2000, 2)])   # write-only window
        self.assertEqual(run.sent[0][0], 4)

    # --- writes -------------------------------------------------------------
    def test_write_lands_and_reports(self):
        data = bytes((i * 7) & 0xFF for i in range(5000))
        run = self.run_cmd(frame(6, STAGE, len(data), 3), data=data)
        self.assertEqual(run.out_arms, [(STAGE - 0x40000000, 5120)])
        self.assertEqual(run.sent[0], (0, 12, (0, STAGE, 5120)))     # READY
        self.assertEqual(run.sent[1], (0, 12, (0, len(data), zlib.crc32(data))))
        self.assertEqual(bytes(run.uc.mem_read(STAGE, len(data))), data)
        names = [c[0] for c in run.calls]
        # cache before the arm, again after the data, and READY before the wait
        self.assertEqual(names[:2], ['dcache', 'startx'])
        out_wait = run.calls.index(('wait', 1, 20))
        self.assertLess(names.index('dcache', 2), len(names))
        self.assertGreater(names.index('dcache', 2), out_wait)
        self.assertEqual(run.state[8], 1)

    def test_out_busy_is_set_after_every_completed_out(self):
        """The driver arms its own buffer on XferNotReady while 0xC31E3974 is 0."""
        data = b'\x33' * 2000
        self.assertEqual(self.run_cmd(frame(6, STAGE, 2000, 0), data=data).out_busy, 1)
        self.assertEqual(self.run_cmd(frame(6, STAGE, 2000, 0), data=data,
                                      early=True).out_busy, 1)
        run = self.run_cmd(frame(9), entry='got_command')    # any command
        self.assertEqual(run.out_busy, 1)
        self.assertTrue(run.sent)                            # and it was served

    def test_write_data_that_beat_the_wait_still_counts(self):
        data = bytes(range(200)) * 5
        run = self.run_cmd(frame(6, STAGE, len(data), 2), data=data, early=True)
        self.assertEqual(run.sent[1], (0, 12, (0, len(data), zlib.crc32(data))))
        self.assertNotIn(('endx', 1), run.calls)
        self.assertEqual(run.state[8], 1)

    def test_write_short_data_is_reported_not_hidden(self):
        data = b'\x11' * 1024
        run = self.run_cmd(frame(6, STAGE, 2048, 0), data=data)
        self.assertEqual(run.sent[1][2][:2], (0, 1024))
        self.assertEqual(run.sent[1][2][2], 0)                       # no CRC asked

    def test_write_refusals_arm_nothing(self):
        cases = [
            ((POOL + 0x100, 0x100, 0), 4),            # pool header
            ((POOL + 0xFFF00, 0x100, 0), 4),          # rounded to 1 KiB, runs off
            ((OTHER, 0x100, 0), 4),                   # no window
            ((STAGE, 0, 0), 2),
            ((STAGE, XFER_MAX + 1, 0), 2),
        ]
        for words, code in cases:
            with self.subTest(words=[hex(w) for w in words]):
                run = self.run_cmd(frame(6, *words), data=b'')
                self.assertEqual(run.out_arms, [])
                self.assertEqual(run.sent, [(code, 4, (code,))])
                self.assertEqual(bytes(run.uc.mem_read(STAGE, 16)), b'\xA5' * 16)

    def test_write_window_must_hold_the_rounded_length(self):
        win = [(OTHER, 0x1000, 2)]
        run = self.run_cmd(frame(6, OTHER + 0xC00, 0x200, 0), windows=win,
                           data=b'\x22' * 0x200)
        self.assertEqual(run.sent[1][2][:2], (0, 0x200))             # 1 KiB fits
        run = self.run_cmd(frame(6, OTHER + 0xE00, 0x100, 0), windows=win,
                           data=b'')
        self.assertEqual(run.sent, [(4, 4, (4,))])                   # 1 KiB doesn't
        run = self.run_cmd(frame(6, OTHER, 0x100, 0), windows=[(OTHER, 0x1000, 1)],
                           data=b'')
        self.assertEqual(run.sent, [(4, 4, (4,))])                   # read-only

    def test_write_data_that_never_comes_is_ended(self):
        run = self.run_cmd(frame(6, STAGE, 0x400, 0), data=None)
        self.assertEqual(run.calls.count(('wait', 1, 20)), 500 // 20)  # MEM_TMO, sliced
        self.assertIn(('endx', 1), run.calls)
        self.assertEqual(run.sent[-1], (6, 4, (6,)))
        self.assertEqual(run.state[9], 1)                            # faults
        self.assertEqual(run.state[8], 0)                            # not served
        count, = struct.unpack('<I', run.uc.mem_read(POOL + 0x6C00, 4))
        entry = struct.unpack('<8I', run.uc.mem_read(POOL + 0x6C10, 32))
        self.assertEqual(count, 1)
        self.assertEqual(entry[1:4], (3, 6, 77))       # READY out, MEMW, its seq
        self.assertEqual(run.state[13], 0)             # phase reset for the next

    def test_write_refused_start_is_reported(self):
        run = self.run_cmd(frame(6, STAGE, 0x400, 0), data=b'', start_rc=1)
        self.assertEqual(run.sent[-1][0], 5)
        self.assertEqual(run.state[2], 1)          # next arm_out ends the TRB

    def test_write_ready_lost_ends_the_data_trb(self):
        run = self.run_cmd(frame(6, STAGE, 0x400, 0), data=b'',
                           wait_in_rc=0xFFFFFFCE)
        self.assertIn(('endx', 1), run.calls)
        self.assertNotIn(('wait', 1, 20), run.calls)

    def test_write_bad_frame(self):
        run = self.run_cmd(frame(6, STAGE, 0x400, 0, good_crc=False), data=b'')
        self.assertEqual(run.sent, [(1, 4, (1,))])
        self.assertEqual(run.out_arms, [])
        run = self.run_cmd(frame(6, STAGE, 0x400, 0, flags=1), data=b'')
        self.assertEqual(run.sent, [(1, 4, (1,))])


if __name__ == '__main__':
    unittest.main()
