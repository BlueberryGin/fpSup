#!/usr/bin/env python3
"""Bounded real-instruction evidence for the CinemaDNG defer boundary.

This does not install a hook or establish a source-buffer lease. In particular,
skipping enqueue is a negative experiment, not an approved implementation.
The native registry, FIFO, creator dispatch, completion, and stop classification
instructions run from the supplied firmware. Logging, native mutex calls and
the mailbox send are explicit substitutes; the mailbox stub only records the
copied slot id. No synthetic SD write or codec completion is reported.

The extended tests execute the original movie-task dispatch and the RAWCD IRQ
callback. They establish distinct call paths, NOT real scheduler liveness,
DMA quiescence, or a safe wait for final file completion on the movie task.
"""
import hashlib
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_native_writer_contract import IMAGE, StockWriter, STOP, STACK, word

FRAMES = (0x45000000, 0x45002000, 0x45004000)
REGISTRY = 0x45008000
CREATOR = 0x45008400
VTABLE = 0x45008500
MOVIE = 0x45008800
EVENT = 0x45008900
META = 0x45008B00
FORMAT = 0x45009000
FORMAT_VTABLE = 0x45009100
FORMAT_DESTROY = STOP + 0x180
TASK = 0x4500A000
SEG1 = IMAGE.with_name("seg1_c2ef6e00.bin")
SEG0_SHA256 = "aaa5208a028d9c4aebb9cc8614add723d456e96b2a95914f433079954320e622"
SEG1_SHA256 = "0dcaca8f5441fe4ddc6ed801cb888e325df4e76006e4f53a1c3219809b22ce7b"

ENQUEUE = 0xC037DD50
COMPLETE = 0xC037DF40
ADVANCE = 0xC0376F20
CLASSIFY = 0xC0376300
CLEANUP = 0xC03752A0
MOVIE_ARRIVE = 0xC038BD98
MOVIE_COMPLETE = 0xC038C058
STOP_CHECK = 0xC0398D88
DISPATCH = 0xC0377008
RECLAIM = 0xC0375030
ARRIVE_CALL = 0xC038BFF0


def pinned_image(path, expected):
    """Missing/mismatched evidence is an error, never a skipped green suite."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise RuntimeError("required firmware image unavailable: " + str(path)) from error
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise RuntimeError("firmware SHA-256 mismatch: %s: %s" % (path, actual))
    return data


class NativeDefer(StockWriter):
    """Strict bounds and named substitutes; never a permissive ROM fallback."""

    def __init__(self, image):
        super().__init__(image)
        self.dispatches = []
        self.reclaims = []
        self.resources = []
        self.events = []
        self.put(CREATOR, VTABLE)
        self.put(VTABLE + 0x10, ENQUEUE)
        self.put(VTABLE + 0x14, ADVANCE)
        self.put(CREATOR + 0x1C, 0xFFFFFFFF)
        self.put(MOVIE + 0xC, 0x55)
        self.put(MOVIE + 0x14, 0)
        self.put(META + 0xC, 0xA000)
        self.put(FORMAT + 8, FORMAT_VTABLE)
        self.put(FORMAT_VTABLE + 8, FORMAT_DESTROY)
        for slot, frame in enumerate(FRAMES, 10):
            self.put(REGISTRY + 4 + slot * 4, frame)
            self.put(frame, 1)
            self.put(frame + 4, 1)
            self.put(frame + 0x10FC, slot)
            self.put(frame + 0x1100, 77)
            self.put(frame + 0x1104, 8)
            self.put(frame + 0x1118, 0xFFFFFFFF)
        self.allowed = [
            (ENQUEUE, 0xC037DEE0), (COMPLETE, 0xC037E060),
            (ADVANCE, 0xC0376F88), (0xC0381008, 0xC0381110),
            (0xC03753C0, 0xC0375400), (CLEANUP, 0xC0375348),
            (CLASSIFY, 0xC0376488), (MOVIE_ARRIVE, 0xC038C058),
            (MOVIE_COMPLETE, 0xC038C228), (STOP_CHECK, 0xC0399118),
        ]
        self.stubs = {
            0xC0019DC0: lambda: self.ret(0x1234),
            0xC001A048: self.ret,
            # Mutex substitutions do not establish real scheduler exclusion.
            0xC0010298: self.ret,
            0xC00102D0: self.ret,
            0xC0374220: lambda: self.ret(REGISTRY),
            0xC037DD08: lambda: self.ret(CREATOR),
            DISPATCH: self.send,
            0xC0374180: self.release_resource,
            FORMAT_DESTROY: self.destroy_format,
            0xC038A0F8: lambda: self.ret(META),
            0xC038C5F8: self.metadata,
            0xC03A92B0: lambda: self.ret(0),
            RECLAIM: self.reclaim_boundary,
            0xC0377348: lambda: self.ret(0x45009500),
            0xC0377730: self.raw_completion_boundary,
        }

    def send(self):
        self.assert_arg(self.r("r0") == CREATOR, "mailbox owner")
        self.dispatches.append(self.get(self.r("r1") + 4))
        self.ret(1)

    @staticmethod
    def assert_arg(condition, text):
        if not condition:
            raise AssertionError("unexpected native argument: " + text)

    def release_resource(self):
        pointer = self.r("r0")
        self.assert_arg(pointer in tuple(f + 0xFE8 for f in FRAMES), "file arena")
        self.resources.append(("file-arena-release", pointer))
        self.ret()

    def destroy_format(self):
        self.assert_arg((self.r("r0"), self.r("r1")) == (FORMAT, 3), "format destroy")
        self.resources.append(("format-destroy", FORMAT))
        self.ret()

    def metadata(self):
        # Only prove the order/arguments of this call, not filename generation.
        self.events.append(("metadata", self.r("r0"), self.r("r1")))
        self.ret()

    def reclaim_boundary(self):
        # No allocation is actually freed. Reaching the original reclaim API
        # with this slot is the unsafe condition asserted by the tests.
        self.assert_arg(self.r("r0") == REGISTRY, "registry reclaim owner")
        self.reclaims.append(self.r("r1"))
        self.ret()

    def raw_completion_boundary(self):
        self.events.append(("raw-complete", self.r("r0"), self.r("r1")))
        self.ret(1)

    def invoke(self, address, *arguments, stops=(STOP,)):
        self.stops = set(stops)
        self.reached = None
        self.setr("sp", STACK - 0x100)
        self.setr("lr", STOP)
        saved = {"r%d" % i: 0xA5000000 + i for i in range(4, 12)}
        for register, value in saved.items():
            self.setr(register, value)
        for i, value in enumerate(arguments):
            self.setr("r%d" % i, value)
        self.mu.emu_start(address, 0, count=20000)
        if self.reached not in self.stops:
            raise AssertionError("bounded execution did not reach approved stop")
        if self.reached == STOP:
            self.assert_arg(self.r("sp") == STACK - 0x100, "restored stack")
            for register, value in saved.items():
                self.assert_arg(self.r(register) == value, "preserved " + register)
        return self.r("r0")

    def enqueue(self, slot):
        return self.invoke(ENQUEUE, CREATOR, slot, 1)

    def movie_arrive(self, slot):
        self.put(EVENT + 0xE8, slot)
        return self.invoke(MOVIE_ARRIVE, MOVIE, EVENT, 0x45009400)

    def movie_complete(self, slot, event):
        self.put(EVENT + 0xE8, slot)
        self.put(EVENT + 0xC, event)
        self.put(EVENT + 0xE4, 0)
        self.invoke(MOVIE_COMPLETE, MOVIE, EVENT, 0x45009400,
                    stops=(0xC038C228,))

    def map_initialized_globals(self):
        data = pinned_image(SEG1, SEG1_SHA256)
        # The first 0x200 bytes share the rounded final seg0 page.
        length = (len(data) - 0x200 + 0xFFF) & ~0xFFF
        self.mu.mem_map(0xC2EF7000, length)
        self.mu.mem_write(0xC2EF6E00, data)

    def prepare_codec_irq(self, *, mode=2, status=1):
        from unicorn import UC_HOOK_MEM_WRITE
        self.map_initialized_globals()
        self.mu.mem_map(0xC37CF000, 0x1000)
        self.mu.mem_map(0x300D0000, 0x1000)
        self.put(0xC37CF87C, mode)
        self.put(0xC37CF880, 0xC062FCB1)
        self.put(0xC37CF8B4, 0xC062FCB1)
        self.put(0xC2F2E60C, 47)
        self.put(0x300D0004, status)
        self.put(0x300D03FC, 0xFF)
        self.allowed.extend([(0xC0630430, 0xC0630480),
                             (0xC062FCB0, 0xC062FCE0),
                             (0xC062FE90, 0xC062FE98),
                             (0xC062FFC0, 0xC062FFF8)])

        def written(_mu, _access, address, size, value, _user):
            if address in (0x300D0004, 0x300D03FC):
                self.events.append(("device-write", address, size, value))

        def set_flag():
            self.events.append(("set-flag", self.r("r0"), self.r("r1")))
            self.ret(0)

        self.mu.hook_add(UC_HOOK_MEM_WRITE, written)
        self.stubs[0xC0016B9C] = set_flag


class NativeDeferContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = pinned_image(IMAGE, SEG0_SHA256)
        pinned_image(SEG1, SEG1_SHA256)
        import emulation_capability
        capable = emulation_capability.capability(emulator=pathlib.Path(__file__))
        if not capable:
            raise RuntimeError(capable.reason + "; native defer unverified")

    def test_movie_metadata_precedes_real_creator_enqueue(self):
        r = NativeDefer(self.image)
        self.assertEqual(r.movie_arrive(10), 0x55)
        self.assertEqual(r.events, [("metadata", MOVIE, FRAMES[0])])
        self.assertLess(r.trace.index(0xC038C5F8), r.trace.index(ENQUEUE))
        self.assertEqual(r.get(FRAMES[0] + 0x14C), 0xA000)
        self.assertEqual(r.dispatches, [10])
        self.assertEqual(r.get(CREATOR + 0x1C), 10)
        self.assertEqual(r.get(FRAMES[0] + 0x1104), 0xB)

    def test_busy_creator_queues_later_raw_in_order(self):
        r = NativeDefer(self.image)
        for slot in (10, 11, 12):
            self.assertEqual(r.enqueue(slot), 1)
        self.assertEqual(r.dispatches, [10])
        self.assertEqual(r.get(CREATOR + 0x14), FRAMES[1])
        self.assertEqual(r.get(CREATOR + 0x18), FRAMES[2])
        self.assertEqual(r.get(FRAMES[1] + 0x110C), FRAMES[2])
        # Merely returning from the mailbox worker cannot advance this FIFO.
        self.assertEqual(r.get(CREATOR + 0x1C), 10)

    def test_native_completion_reclaims_then_dispatches_next_once(self):
        r = NativeDefer(self.image)
        r.put(FRAMES[0] + 0x138, FORMAT)
        for slot in (10, 11, 12):
            r.enqueue(slot)
        r.invoke(COMPLETE, CREATOR, 10)
        self.assertEqual(r.resources, [("file-arena-release", FRAMES[0] + 0xFE8),
                                       ("format-destroy", FORMAT)])
        self.assertEqual(r.get(FRAMES[0] + 0x138), 0)
        self.assertEqual(r.dispatches, [10, 11])
        self.assertEqual(r.get(CREATOR + 0x1C), 11)

    def test_late_duplicate_completion_advances_unrelated_active_frame(self):
        r = NativeDefer(self.image)
        for slot in (10, 11, 12):
            r.enqueue(slot)
        r.invoke(COMPLETE, CREATOR, 10)
        r.invoke(COMPLETE, CREATOR, 10)
        # This is a demonstrated hazard, NOT an exactly-once guarantee.
        self.assertEqual(r.dispatches, [10, 11, 12])
        self.assertEqual(r.get(CREATOR + 0x1C), 12)
        self.assertEqual(r.resources, [("file-arena-release", FRAMES[0] + 0xFE8)] * 2)

    def test_event8_advances_without_reclaiming_the_frame(self):
        r = NativeDefer(self.image)
        for slot in (10, 11):
            r.enqueue(slot)
        r.movie_complete(10, 8)
        self.assertEqual(r.dispatches, [10, 11])
        self.assertEqual(r.resources, [])
        self.assertEqual(r.reclaims, [])

    def test_event9_reclaims_and_advances_even_after_event8(self):
        r = NativeDefer(self.image)
        for slot in (10, 11, 12):
            r.enqueue(slot)
        r.movie_complete(10, 8)
        r.movie_complete(10, 9)
        self.assertEqual(r.dispatches, [10, 11, 12])
        self.assertEqual(r.reclaims, [10])
        self.assertEqual(r.events[-1], ("raw-complete", 0x45009500, 10))

    def test_pre_enqueue_skip_does_not_block_later_creator_but_is_not_a_hold(self):
        r = NativeDefer(self.image)
        # Deliberately remove only this call in private emulated memory. There
        # is no invented success value: C038BD98 ignores its returned value.
        self.assertEqual(word(self.image, ARRIVE_CALL), 0xE12FFF33)
        r.put(ARRIVE_CALL, 0xE1A00000)  # NOP; negative defer experiment.
        r.movie_arrive(10)
        self.assertEqual(r.dispatches, [])
        self.assertEqual(r.get(CREATOR + 0x1C), 0xFFFFFFFF)
        self.assertEqual(r.get(FRAMES[0] + 0x1104), 8)
        r.enqueue(11)
        self.assertEqual(r.dispatches, [11])
        r.invoke(CLEANUP, REGISTRY)
        self.assertIn(10, r.reclaims)  # original stop cleanup calls reclaim!

    def test_stop_cleanup_retains_native_pending_b_but_targets_raw_state8(self):
        r = NativeDefer(self.image)
        r.enqueue(11)
        r.invoke(CLEANUP, REGISTRY)
        self.assertEqual(r.reclaims, [10, 12])
        self.assertEqual(r.get(FRAMES[1] + 0x1104), 0xB)

    def test_stop_classification_distinguishes_pending_and_written_states(self):
        for state, expected in ((0, 3), (8, 0), (9, 0), (0xA, 0),
                                (0xB, 1), (0xC, 2), (0xD, 2), (0x12, 0)):
            with self.subTest(state=state):
                r = NativeDefer(self.image)
                for frame in FRAMES:
                    r.put(frame + 0x1104, state)
                self.assertEqual(r.invoke(CLASSIFY, REGISTRY, 77), expected)

    def test_stop_check_executes_cleanup_before_classifying_pending(self):
        r = NativeDefer(self.image)
        # Isolate legitimate pending frames; cleanup is still real and has
        # nothing to reclaim. Native stop returns "not finished", not success.
        for slot in (10, 11, 12):
            r.enqueue(slot)
        self.assertEqual(r.invoke(STOP_CHECK, 1, 77), 0)
        self.assertLess(r.trace.index(CLEANUP), r.trace.index(CLASSIFY))
        self.assertEqual(r.reclaims, [])

    def test_split_native_pending_prefix_then_later_enqueue_keeps_creator_free(self):
        r = NativeDefer(self.image)
        r.put(REGISTRY + 4 + 12 * 4, 0)
        # Execute the original enqueue prefix, then stop BEFORE it touches
        # creator active/FIFO state. This is a feasibility experiment, not a
        # callable native API or proof that an external worker owns the frame.
        r.invoke(ENQUEUE, CREATOR, 10, 1, stops=(0xC037DE08,))
        self.assertEqual(r.get(FRAMES[0] + 0x1104), 0xB)
        self.assertEqual(r.get(CREATOR + 0x1C), 0xFFFFFFFF)
        self.assertEqual(r.dispatches, [])
        r.enqueue(11)
        self.assertEqual(r.invoke(STOP_CHECK, 1, 77), 0)
        self.assertEqual(r.reclaims, [])
        r.invoke(COMPLETE, CREATOR, 11)
        self.assertEqual(r.get(CREATOR + 0x1C), 0xFFFFFFFF)
        # A later *real* enqueue of the same frame works in this bounded
        # sequential case and does not require a synthetic completion event.
        r.enqueue(10)
        r.invoke(COMPLETE, CREATOR, 10)
        self.assertEqual(r.dispatches, [11, 10])
        self.assertEqual(r.get(CREATOR + 0x1C), 0xFFFFFFFF)
        self.assertEqual(r.resources, [("file-arena-release", FRAMES[1] + 0xFE8),
                                       ("file-arena-release", FRAMES[0] + 0xFE8)])

    def test_pending_state_b_is_not_an_explicit_registry_release_lease(self):
        r = NativeDefer(self.image)
        r.invoke(ENQUEUE, CREATOR, 10, 1, stops=(0xC037DE08,))
        del r.stubs[RECLAIM]
        r.allowed.append((RECLAIM, 0xC0375178))
        # Stop before destructive removal: real C0375030 passes state B to
        # C03768D8 exactly as it does any unreferenced original frame.
        r.invoke(RECLAIM, REGISTRY, 10, stops=(0xC03768D8,))
        self.assertEqual((r.r("r0"), r.r("r1")), (REGISTRY, 10))
        self.assertEqual(r.get(FRAMES[0] + 0x1104), 0xB)

    def test_global_drain_targets_pending_b_without_state_or_fifo_test(self):
        r = NativeDefer(self.image)
        r.invoke(ENQUEUE, CREATOR, 10, 1, stops=(0xC037DE08,))
        r.allowed.append((0xC0398D18, STOP_CHECK))
        # These substitutes stand for other native stages' teardown. We prove
        # only that this final registry sweep reaches our pending frame.
        r.stubs.update({0xC0377620: r.ret, 0xC037AED8: r.ret,
                        0xC037D580: r.ret, 0xC0377118: r.ret,
                        0xC037DEE0: r.ret})
        r.invoke(0xC0398D18)
        self.assertEqual(r.reclaims, [10, 11, 12])

    def test_other_stop_classifier_does_not_wait_for_pending_b(self):
        for state, expected in ((8, 0), (0xB, 1)):
            with self.subTest(state=state):
                r = NativeDefer(self.image)
                for frame in FRAMES:
                    r.put(frame + 0x1104, state)
                r.allowed.append((0xC0375BE0, 0xC0375DE8))
                self.assertEqual(r.invoke(0xC0375BE0, REGISTRY), expected)

    def test_movie_task_event29_reaches_thr_teardown_before_registry_drain(self):
        r = NativeDefer(self.image)
        r.map_initialized_globals()
        # Actual initialized event table, not an invented vtable dispatch.
        self.assertEqual(r.get(0xC2F1F774), 0)
        self.assertEqual(r.get(0xC2F1F778), 0xFFFFFFFF)
        self.assertEqual(r.get(0xC2F1F77C), 0xC03AB750)
        self.assertEqual(r.get(0xC0B9CF00), 0xC0393D10)
        r.put(MOVIE + 0xC, 1)  # MovRecFuncStateTHR, not CinemaDNG state.
        r.put(MOVIE + 0x10, 0xC0B9CEA4)
        r.put(TASK + 8, 23)
        r.put(TASK + 0x144, 1)
        r.put(TASK + 0x148, 1)
        r.put(TASK + 0xFC, MOVIE)
        r.put(EVENT + 4, 29)
        r.put(EVENT + 0xC, 0)
        r.allowed.extend([(0xC03889E0, 0xC0388CB0),
                          (0xC036D9C0, 0xC036DA48),
                          (0xC038ACA8, 0xC038AD58),
                          (0xC03AB750, 0xC03AB788),
                          (0xC0393D10, 0xC0393D30),
                          (0xC0398BC0, 0xC0398C78)])

        def receive_message():
            # Only this declared kernel receive is substituted. The original
            # task and message-wrapper instructions consume this message.
            self.assertEqual((r.r("r0"), r.r("r2")), (23, 0xFFFFFFFF))
            r.put(r.r("r1"), EVENT)
            r.events.append(("receive", 23, EVENT))
            r.ret(0)

        def noop_named(name, result=0):
            def call():
                r.events.append((name,))
                r.ret(result)
            return call

        r.stubs.update({
            0xC0389488: r.ret, 0xC01F8B74: receive_message,
            0xC0388CB0: noop_named("message-preprocess"),
            0xC03A9970: r.ret, 0xC03AB240: lambda: r.ret(META),
            0xC0014DAC: noop_named("config-copy"),
            0xC0021C00: r.ret, 0xC0021C50: noop_named("config-update"),
            0xC03214D0: r.ret, 0xC03215C0: noop_named("unregister-callback"),
            0xC0399188: r.ret, 0xC04D84F8: r.ret,
            0xC0399B60: noop_named("remove-uvc-observer"),
            0xC04D75D8: noop_named("uvc-stop"),
        })
        r.invoke(0xC03889E0, TASK, stops=(0xC0398D18,))
        for address in (0xC01F8B74, 0xC038ACA8, 0xC03AB750,
                        0xC0393D10, 0xC0398BC0, 0xC04D75D8):
            self.assertIn(address, r.trace)
        self.assertEqual(r.events[-1], ("uvc-stop",))
        self.assertEqual(r.reclaims, [])
        self.assertEqual(r.resources, [])

    def test_codec_success_irq_sets_flag_without_movie_task_dispatch(self):
        for mode in (1, 2):
            with self.subTest(mode=mode):
                r = NativeDefer(self.image)
                r.prepare_codec_irq(mode=mode, status=1)
                r.invoke(0xC0630431)
                self.assertEqual(r.events, [("device-write", 0x300D0004, 4, 1),
                                            ("set-flag", 47, 1)])
                self.assertNotIn(0xC036D9C0, r.trace)
                self.assertNotIn(0xC03889E0, r.trace)
                self.assertEqual(r.dispatches, [])

    def test_codec_error_irq_resets_then_sets_error_flag_without_movie_task(self):
        r = NativeDefer(self.image)
        r.prepare_codec_irq(status=4)
        r.invoke(0xC0630431)
        self.assertEqual(r.events, [("device-write", 0x300D0004, 4, 4),
                                    ("device-write", 0x300D03FC, 4, 0xFF),
                                    ("device-write", 0x300D03FC, 4, 0xFE),
                                    ("set-flag", 47, 4)])
        # Error notification is not DMA-idle or source-release proof.
        self.assertEqual(r.resources, [])

    def test_closed_codec_irq_does_not_invent_completion(self):
        r = NativeDefer(self.image)
        r.prepare_codec_irq(mode=0, status=1)
        r.invoke(0xC0630431)
        self.assertEqual(r.events, [("device-write", 0x300D0004, 4, 1)])

    def test_dropping_native_irq_callback_is_detected(self):
        r = NativeDefer(self.image)
        r.prepare_codec_irq(status=1)
        self.assertEqual(bytes(r.mu.mem_read(0xC0630474, 2)), b"\x88\x47")
        r.mu.mem_write(0xC0630474, b"\x00\xbf")  # private Thumb NOP
        r.invoke(0xC0630431)
        with self.assertRaises(AssertionError):
            self.assertIn(("set-flag", 47, 1), r.events)

    def test_fifo_active_guard_mutation_is_detected(self):
        original = word(self.image, 0xC037DE14)
        self.assertEqual(original, 0x0A000007)
        r = NativeDefer(self.image)
        # BEQ -> unconditional B: pretend busy is idle, bypass queue.
        r.put(0xC037DE14, (original & 0x0FFFFFFF) | 0xE0000000)
        r.enqueue(10)
        r.enqueue(11)
        with self.assertRaises(AssertionError):
            self.assertEqual(r.dispatches, [10])
        self.assertEqual(r.dispatches, [10, 11])

    def test_stop_state8_cleanup_mutation_is_detected(self):
        r = NativeDefer(self.image)
        self.assertEqual(word(self.image, 0xC03752F8), 0xE3510008)
        r.put(0xC03752F8, 0xE3510007)  # CMP state,#7 instead of #8.
        r.invoke(CLEANUP, REGISTRY)
        with self.assertRaises(AssertionError):
            self.assertEqual(r.reclaims, [10, 11, 12])
        self.assertEqual(r.reclaims, [])

    def test_unknown_execution_fails_closed(self):
        r = NativeDefer(self.image)
        with self.assertRaisesRegex(AssertionError, "unapproved firmware execution"):
            r.invoke(0xC0375000)


def selfcheck():
    r = NativeDefer(pinned_image(IMAGE, SEG0_SHA256))
    pinned_image(SEG1, SEG1_SHA256)
    r.movie_arrive(10)
    r.enqueue(11)
    r.invoke(COMPLETE, CREATOR, 10)
    r.invoke(CLEANUP, REGISTRY)
    r.invoke(CLASSIFY, REGISTRY, 77)
    r.prepare_codec_irq(status=1)
    r.invoke(0xC0630431)
    print("SELFCHECK OK: real creator/FIFO/completion/stop/IRQ instructions executed")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selfcheck"]:
        selfcheck()
    else:
        unittest.main()
