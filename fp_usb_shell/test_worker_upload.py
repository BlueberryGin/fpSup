"""Execute the real ARM upload receiver through its reply boundary offline."""
import pathlib
import platform
import struct
import subprocess
import sys
import unittest
import zlib

from armasm import assemble, symbols

try:
    from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R4, UC_ARM_REG_R6
    from unicorn.arm_const import UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_SP
except ImportError:
    Uc = None


HERE = pathlib.Path(__file__).resolve().parent
WORKER = HERE / 'camera' / 'worker.S'
CODE = 0x60000000
POOL = 0x45000000
UNCACHED = POOL + 0x40000000
STATE = 0xC072F000
STACK = 0x70000000
DEST = POOL + 0x10000
IMAGE = HERE.parents[1] / 'out' / 'MAIN_c0000000.bin'
# crc_block calls the firmware's own crc32 (worker.S FW_CRC32): a leaf in the
# image and its table.  The real bytes are mapped, and execution may enter
# that routine and nothing else outside the worker.
FW_CRC32, FW_CRC32_END = 0xC06AE998, 0xC06AE9F8
FW_TABLE = 0xC2E3B794


def unicorn_runs():
    if Uc is None:
        return False
    # (A hardcoded "macOS 27 arm64 + Unicorn 2.1.4 SIGILLs" skip stood here.
    # On 2026-10-06 that exact combination ran every unicorn suite in this
    # tree, so the guard only hid these tests; the child probe below is what
    # actually keeps a SIGILLing wheel from killing the run.)
    # Some local Unicorn wheels import but SIGILL as soon as emulation starts.
    # Probe in a child so the offline suite reports a skip instead of dying.
    probe = ("from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM; "
             "u=Uc(UC_ARCH_ARM, UC_MODE_ARM); u.mem_map(0x1000, 0x1000); "
             "u.mem_write(0x1000, b'\\x01\\x00\\xa0\\xe3'); "
             "u.emu_start(0x1000, 0x1004)")
    result = subprocess.run([sys.executable, '-B', '-c', probe],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


UNICORN_RUNS = unicorn_runs()


def packet(address, data, *, frame_crc=True, data_crc=True):
    frame = bytearray(64)
    struct.pack_into('<4sBBHIHHI', frame, 0, b'FPSH', 1, 4, 0, 101, 12, 0, 0)
    struct.pack_into('<III', frame, 20, address, len(data),
                     zlib.crc32(data) ^ (0 if data_crc else 1))
    struct.pack_into('<I', frame, 16, zlib.crc32(frame) ^ (0 if frame_crc else 1))
    return bytes(frame) + data


@unittest.skipUnless(UNICORN_RUNS, 'Unicorn ARM runtime unavailable')
@unittest.skipUnless(IMAGE.exists(), 'needs the firmware image for crc32')
class WorkerUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = assemble(WORKER)
        cls.labels = symbols(WORKER)

    def run_receiver(self, incoming, *, entry='receive_upload', last=None,
                     code_patch=None):
        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.mem_map(CODE, 0x1000)
        uc.mem_write(CODE, self.code)
        if code_patch:
            offset, word = code_patch
            uc.mem_write(CODE + offset, struct.pack('<I', word))
        uc.mem_map(POOL, 0x100000)
        uc.mem_map(UNCACHED, 0x4000)
        uc.mem_map(STATE, 0x1000)
        uc.mem_map(STACK, 0x10000)
        img = IMAGE.read_bytes()
        for page in (FW_CRC32 & ~0xFFF, FW_TABLE & ~0xFFF):
            uc.mem_map(page, 0x2000)
            uc.mem_write(page, img[page - 0xC0000000:page - 0xC0000000 + 0x2000])
        uc.mem_write(STATE + 0x50, struct.pack('<I', POOL))
        uc.mem_write(UNCACHED, incoming)
        uc.mem_write(DEST, b'\xA5' * 64)
        if last is not None:
            uc.mem_write(POOL + 0x6900, struct.pack('<IIII', *last))
        uc.reg_write(UC_ARM_REG_R4, STATE)
        uc.reg_write(UC_ARM_REG_R6, UNCACHED)
        uc.reg_write(UC_ARM_REG_SP, STACK + 0x8000)
        start = CODE + self.labels[entry]
        end = CODE + self.labels['reply_entry']
        stopped = []

        def hook(machine, address, _size, _user):
            if address == end:
                stopped.append(True)
                machine.emu_stop()
            elif not (CODE <= address < CODE + len(self.code)
                      or FW_CRC32 <= address < FW_CRC32_END):
                raise AssertionError(f'worker escaped to 0x{address:08X}')

        uc.hook_add(UC_HOOK_CODE, hook)
        uc.emu_start(start, CODE + len(self.code), count=1_000_000)
        self.assertTrue(stopped, 'worker did not reach reply boundary')
        return uc

    def test_valid_binary_chunk_copies_and_publishes_status(self):
        data = bytes(range(256)) * 4
        uc = self.run_receiver(packet(DEST, data))
        self.assertEqual(bytes(uc.mem_read(DEST, len(data))), data)
        self.assertEqual(struct.unpack('<IIII', uc.mem_read(POOL + 0x6900, 16)),
                         (101, DEST, len(data), zlib.crc32(data)))
        self.assertEqual(struct.unpack('<IIII', uc.mem_read(UNCACHED + 20, 16)),
                         (0, DEST, len(data), zlib.crc32(data)))
        self.assertEqual(uc.reg_read(UC_ARM_REG_R9), 16)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R8), UNCACHED + 20)

    def test_bad_crc_and_out_of_bounds_do_not_copy(self):
        data = bytes(range(64))
        for incoming, code in ((packet(DEST, data, data_crc=False), 3),
                               (packet(DEST, data, frame_crc=False), 1),
                               (packet(POOL + 0xFFFC, data), 2),
                               (packet(POOL + 0x100000 - 32, data), 2)):
            with self.subTest(code=code):
                uc = self.run_receiver(incoming)
                self.assertEqual(bytes(uc.mem_read(DEST, 64)), b'\xA5' * 64)
                self.assertEqual(bytes(uc.mem_read(POOL + 0x6900, 16)), b'\0' * 16)
                self.assertEqual(struct.unpack('<I', uc.mem_read(UNCACHED + 20, 4))[0],
                                 code)
                self.assertEqual(uc.reg_read(UC_ARM_REG_R9), 4)

    def test_capability_reports_last_accepted_tuple(self):
        last = (123, DEST, 64, 0xDEADBEEF)
        uc = self.run_receiver(bytes(64), entry='reply_upload_caps', last=last)
        self.assertEqual(bytes(uc.mem_read(UNCACHED + 20, 4)), b'UP01')
        self.assertEqual(struct.unpack('<IIII', uc.mem_read(UNCACHED + 24, 16)), last)
        self.assertEqual(uc.reg_read(UC_ARM_REG_R9), 20)

    def test_crc_guard_mutation_is_detected(self):
        # Mutate only the emulated code: remove the branch to upload_bad_crc.
        # The bad-data test must then observe an improper write.
        words = struct.unpack(f'<{len(self.code)//4}I', self.code)
        bad = self.labels['upload_bad_crc']
        branches = []
        for i, word in enumerate(words):
            if word >> 24 != 0x1A:  # BNE
                continue
            disp = word & 0xFFFFFF
            if disp & 0x800000:
                disp -= 0x1000000
            if i * 4 + 8 + disp * 4 == bad:
                branches.append(i * 4)
        self.assertEqual(len(branches), 1)
        data = bytes(range(64))
        uc = self.run_receiver(packet(DEST, data, data_crc=False),
                               code_patch=(branches[0], 0xE1A00000))
        self.assertEqual(bytes(uc.mem_read(DEST, 64)), data)


if __name__ == '__main__':
    unittest.main()
