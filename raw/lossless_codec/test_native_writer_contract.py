#!/usr/bin/env python3
"""Run bounded SIGMA fp 5.02 writer paths from the real firmware image.

The September 29 skip experiment treated a zero C069ADE0 result as success,
assumed CinemaDNG had no observers, and called frame+1030 a writer descriptor.
These tests execute the stock instructions that contradict those assumptions.
They do NOT establish a legal deferred-write API, frame ownership, SD completion
or real-time scheduling. No camera imports, builds, card writes or deployment.

Only the listed firmware ranges may execute. Logging, memcpy, a synthetic
observer and the media constructor are bounded stubs; execution stops BEFORE
the native open routine, so no filesystem backend is simulated as successful.
Mutations affect only a fresh emulator's private instruction memory.

    python3 -B -m unittest -v test_native_writer_contract
"""
import pathlib
import struct
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
IMAGE = HERE.parents[2] / "out" / "seg0_c0000000.bin"
ROM = 0xC0000000
FRAME = 0x45000000
MANAGER = 0x45002000
WRITER = 0x45003000
OBSERVER = 0x45004000
VTABLE = 0x45004100
STACK = 0x46010000
STOP = 0x47000000
OBSERVER_CALL = STOP + 0x100

RESULT_BRANCH = 0xC03A54F4
RESULT_CONDITION = 0xC03A54F8
RESULT_END = 0xC03A55C0
REGISTER = 0xC03A5248
NOTIFY = 0xC03A52D0
WRITER_INIT_CALL = 0xC03A538C
WRITER_INIT_DONE = 0xC03A53A4
WRITER_INIT = 0xC03813D0
FLUSH = 0xC069ADE0
FILE_OPEN = 0xC0365FB0
LOG_GET = 0xC0019DC0
LOG_WRITE = 0xC001A048
MEMCPY = 0xC001510C
MEDIA_CTOR = 0xC0365E90


def word(data, address):
    return struct.unpack_from("<I", data, address - ROM)[0]


class StockWriter:
    """Small, strict instruction runner, not a camera or a permissive ROM stub."""

    def __init__(self, image):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
        from unicorn import arm_const

        self.reg = arm_const
        self.mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.mu.mem_map(ROM, (len(image) + 0xFFF) & ~0xFFF)
        self.mu.mem_write(ROM, image)
        self.mu.mem_map(FRAME, 0x10000)
        self.mu.mem_map(STACK - 0x10000, 0x10000)
        self.mu.mem_map(STOP, 0x1000)
        self.allowed = []
        self.stops = set()
        self.stubs = {}
        self.reached = None
        self.notifications = []
        self.trace = []
        self.mu.hook_add(UC_HOOK_CODE, self._instruction)
        self.put(FRAME + 0x10FC, 37)
        self.put(FRAME + 0x1104, 0xC)
        self.put(OBSERVER, VTABLE)
        self.put(VTABLE + 0xC, OBSERVER_CALL)
        self.mu.mem_write(MANAGER + 0xC, b"\x01")
        self.mu.mem_write(FRAME + 0x1030, b"\\DCIM\\100SIGMA\\A001_000037.DNG\0")

    def get(self, address):
        return struct.unpack("<I", self.mu.mem_read(address, 4))[0]

    def put(self, address, value):
        self.mu.mem_write(address, struct.pack("<I", value))

    def r(self, name):
        return self.mu.reg_read(getattr(self.reg, "UC_ARM_REG_" + name.upper()))

    def setr(self, name, value):
        self.mu.reg_write(getattr(self.reg, "UC_ARM_REG_" + name.upper()), value)

    def ret(self, value=0):
        self.setr("r0", value)
        self.setr("pc", self.r("lr"))

    def _instruction(self, _mu, address, _size, _user):
        self.trace.append(address)
        if address in self.stops:
            self.reached = address
            self.mu.emu_stop()
        elif address in self.stubs:
            self.stubs[address]()
        elif not any(start <= address < end for start, end in self.allowed):
            raise AssertionError(f"unapproved firmware execution: {address:#010x}")

    def run(self, start, *, ranges, stops=(STOP,), stubs=None, registers=None):
        self.allowed, self.stops = ranges, set(stops)
        self.stubs = {} if stubs is None else stubs
        self.reached = None
        self.setr("sp", STACK - 0x100)
        self.setr("lr", STOP)
        for name, value in (registers or {}).items():
            self.setr(name, value)
        self.mu.emu_start(start, 0, count=1024)
        if self.reached not in self.stops:
            raise AssertionError("instruction budget exhausted before an approved stop")

    def observe(self):
        self.notifications.append(tuple(self.r(f"r{i}") for i in range(4)))
        self.ret()

    def copy_nodes(self):
        # C03813D0's only external effect: initialize its eight-node arena.
        destination, source, length = (self.r(f"r{i}") for i in range(3))
        if source != 0xC0B9978C or length != 0x80:
            raise AssertionError("unexpected writer initializer memcpy")
        if not FRAME <= destination <= FRAME + 0x10000 - length and not (
                STACK - 0x10000 <= destination <= STACK - length):
            raise AssertionError("writer initializer copy escaped owned memory")
        self.mu.mem_write(destination, bytes(self.mu.mem_read(source, length)))
        self.ret(destination)

    def result(self, low, high=0, active=True):
        self.put(MANAGER + 4, OBSERVER)
        self.mu.mem_write(MANAGER + 0xC, bytes((int(active),)))
        self.run(RESULT_BRANCH,
                 ranges=[(RESULT_BRANCH, RESULT_END), (NOTIFY, 0xC03A5344)],
                 stops=(RESULT_END,),
                 stubs={OBSERVER_CALL: self.observe,
                        LOG_GET: lambda: self.ret(0x1234),
                        LOG_WRITE: self.ret},
                 registers={"r4": FRAME, "r5": 0x318200,
                            "r6": low, "r7": high, "r11": MANAGER})


class NativeWriterContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not IMAGE.is_file():
            raise unittest.SkipTest(f"missing firmware image: {IMAGE}; unverified")
        # A child isolates a host JIT SIGILL from test results. It runs this
        # actual branch workload, not a generic one-instruction smoke test.
        sys.path.insert(0, str(HERE))
        import emulation_capability
        capability = emulation_capability.capability(emulator=pathlib.Path(__file__))
        if not capability:
            raise unittest.SkipTest(capability.reason + "; native writer unverified")
        cls.image = IMAGE.read_bytes()

    def assert_zero_is_failure(self, runner):
        self.assertEqual(runner.get(FRAME + 0x1104), 0xD)
        self.assertEqual(runner.notifications, [(OBSERVER, 37, 4, 1)])
        self.assertEqual(runner.r("r5"), 0)

    def assert_nonzero_is_pending(self, runner):
        self.assertEqual(runner.get(FRAME + 0x1104), 0xC)
        self.assertEqual(runner.notifications, [])
        self.assertEqual(runner.r("r5"), 0x318200)

    def test_zero_flush_result_reports_failure_and_zero_written_length(self):
        runner = StockWriter(self.image)
        runner.result(0)
        self.assert_zero_is_failure(runner)

    def test_zero_result_overwrites_a_forged_released_state_with_failure(self):
        # The historical skip's second variant stored 0x12 before returning
        # zero. Stock code overwrites it, so that trial did not isolate reclaim.
        runner = StockWriter(self.image)
        runner.put(FRAME + 0x1104, 0x12)
        runner.result(0)
        self.assert_zero_is_failure(runner)

    def test_nonzero_low_word_keeps_native_completion_pending(self):
        runner = StockWriter(self.image)
        runner.result(1)
        self.assert_nonzero_is_pending(runner)

    def test_nonzero_high_word_is_also_a_successful_64_bit_result(self):
        runner = StockWriter(self.image)
        runner.result(0, 1)
        self.assert_nonzero_is_pending(runner)

    def test_inactive_manager_notifies_success_for_nonzero_result(self):
        runner = StockWriter(self.image)
        runner.result(123, active=False)
        self.assertEqual(runner.get(FRAME + 0x1104), 0xD)
        self.assertEqual(runner.notifications, [(OBSERVER, 37, 4, 0)])
        self.assertEqual(runner.r("r5"), 0x318200)

    def test_reversing_the_success_branch_is_detected_for_both_results(self):
        # Stock BNE -> BEQ, same target. Patch private emulated bytes only.
        original = word(self.image, RESULT_CONDITION)
        self.assertEqual(original, 0x1A000016)
        for result, assertion in ((0, self.assert_zero_is_failure),
                                  (1, self.assert_nonzero_is_pending)):
            with self.subTest(result=result):
                runner = StockWriter(self.image)
                runner.put(RESULT_CONDITION, original ^ 0x10000000)
                runner.result(result)
                with self.assertRaises(AssertionError):
                    assertion(runner)
        self.assertEqual(word(self.image, RESULT_CONDITION), original)

    def test_observer_registration_is_real_and_idempotent(self):
        runner = StockWriter(self.image)
        for _ in range(2):
            runner.run(REGISTER, ranges=[(REGISTER, NOTIFY)],
                       registers={"r0": MANAGER, "r1": OBSERVER})
            self.assertEqual(runner.get(MANAGER + 4), OBSERVER)
            self.assertEqual(runner.get(MANAGER + 8), 0)
            self.assertEqual(runner.r("sp"), STACK - 0x100)
        # Actual raw initialization passes stage+0x0C's observer to this API.
        self.assertEqual(word(self.image, 0xC0378FD0), 0xE596100C)
        registration_bl = word(self.image, 0xC0378FD8)
        displacement = registration_bl & 0xFFFFFF
        if displacement & 0x800000:
            displacement -= 0x1000000
        self.assertEqual(registration_bl >> 24, 0xEB)
        self.assertEqual(0xC0378FD8 + 8 + displacement * 4, REGISTER)

    def test_current_frame_path_is_not_a_node_descriptor(self):
        runner = StockWriter(self.image)
        runner.put(MANAGER + 0x10, 7)
        runner.run(WRITER_INIT_CALL,
                   ranges=[(WRITER_INIT_CALL, WRITER_INIT_DONE),
                           (WRITER_INIT, 0xC0381420)],
                   stops=(WRITER_INIT_DONE,),
                   stubs={MEMCPY: runner.copy_nodes},
                   registers={"r4": FRAME, "r11": MANAGER})
        writer = STACK - 0x100 + 0x20
        self.assertEqual(runner.get(writer), FRAME + 0x1030)
        self.assertEqual(runner.get(writer + 4), 7)
        self.assertEqual(runner.get(writer + 0x8C), 0)
        self.assertEqual(runner.get(writer + 0x90), 0)
        self.assertEqual(runner.r("sp"), STACK - 0x100)
        # Follow stock flush only as far as open. Its input is the pathname,
        # not writer+0x0C and not a descriptor hidden inside frame+0x1030.
        runner.put(WRITER, FRAME + 0x1030)
        runner.put(WRITER + 4, 7)
        runner.run(FLUSH, ranges=[(FLUSH, 0xC069AE58)], stops=(FILE_OPEN,),
                   stubs={MEDIA_CTOR: runner.ret},
                   registers={"r0": WRITER, "r1": FRAME})
        self.assertEqual(runner.r("r1"), FRAME + 0x1030)
        self.assertEqual(runner.r("r2"), 0x406)
        self.assertEqual(bytes(runner.mu.mem_read(runner.r("r1"), 6)), b"\\DCIM\\")

    def test_unapproved_firmware_execution_is_an_error(self):
        runner = StockWriter(self.image)
        with self.assertRaisesRegex(AssertionError, "unapproved firmware execution"):
            runner.run(RESULT_BRANCH, ranges=[])


def selfcheck():
    image = IMAGE.read_bytes()
    for result in (0, 1):
        runner = StockWriter(image)
        runner.result(result)
    # Capability is execution, not a semantic pass. Contract assertions belong
    # to unittest so a changed result cannot be mislabeled an environment skip.
    print("SELFCHECK OK: actual native writer result branches executed")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selfcheck"]:
        selfcheck()
    else:
        unittest.main()
