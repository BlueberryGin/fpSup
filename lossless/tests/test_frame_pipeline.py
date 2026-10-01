"""Execute the actual one-slot C core with explicit, synthetic adapter proofs.

No firmware, camera imports, USB, native holds, or deployable artifacts. Host
execution and ARM compilation do not prove a native defer/ownership adapter.
"""
import ctypes as ct
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
OK, INVALID, BUSY, UNSUPPORTED, NOT_READY, FAULT = range(6)
FREE, HELD, ENCODING, READY, COMMITTING, RETAINED, READY_RAW = range(7)
NONE, RAW, SELECTED = range(3)


def require_equal(actual, expected):
    # Keep setup/actions running under python -O as well as the default mode.
    if actual != expected:
        raise AssertionError((actual, expected))


class Token(ct.Structure):
    _fields_ = [(n, ct.c_uint32) for n in ('session', 'take', 'frame')]


class Lease(ct.Structure):
    _fields_ = [('token', Token), ('buffer', ct.c_size_t),
                ('metadata', ct.c_size_t), ('writer', ct.c_size_t),
                ('capacity', ct.c_uint32), ('stock_file_bytes', ct.c_uint32)]


class Output(ct.Structure):
    _fields_ = [('bytes', ct.c_size_t), ('length', ct.c_uint32),
                ('file_bytes', ct.c_uint32), ('validated', ct.c_uint32)]


class Pipeline(ct.Structure):
    _fields_ = [(n, ct.c_uint32) for n in
                ('magic', 'session', 'take', 'active', 'enabled', 'stopping', 'fault',
                 'phase', 'last_frame', 'seen', 'passed', 'selected', 'handed_off')]
    _fields_ += [('lease', Lease), ('output', Output)]


Handoff = ct.CFUNCTYPE(ct.c_uint32, ct.c_void_p, ct.POINTER(Lease),
                      ct.POINTER(Output), ct.POINTER(ct.c_uint32))


class ControlState(ct.Structure):
    _fields_ = [(n, ct.c_uint32) for n in
                ('magic', 'abi', 'requested', 'clip', 'frames', 'fault',
                 'reserved0', 'reserved1')]


class Context(ct.Structure):
    _fields_ = [(n, ct.c_uint32) for n in
                ('firmware', 'cine', 'compression', 'bits', 'width', 'height',
                 'fps_num', 'fps_den', 'media', 'ready')]


class UI(ct.Structure):
    _fields_ = [(n, ct.c_uint32) for n in
                ('magic', 'session', 'owner', 'generation', 'attached')]


def bind(lib):
    specs = {
        'init': [ct.POINTER(Pipeline), ct.c_uint32],
        'begin': [ct.POINTER(Pipeline), ct.POINTER(ControlState)],
        'arrive': [ct.POINTER(Pipeline), ct.POINTER(Lease), ct.c_uint32,
                   ct.c_uint32, ct.POINTER(ct.c_uint32)],
        'submitted': [ct.POINTER(Pipeline), ct.POINTER(Token), ct.c_uint32],
        'complete': [ct.POINTER(Pipeline), ct.POINTER(Token), ct.c_uint32,
                     ct.c_uint32, ct.POINTER(Output)],
        'handoff': [ct.POINTER(Pipeline), ct.POINTER(Token), Handoff, ct.c_void_p],
        'stop': [ct.POINTER(Pipeline)],
        'reap': [ct.POINTER(Pipeline), ct.POINTER(Token), ct.c_uint32, ct.c_uint32],
        'finish': [ct.POINTER(Pipeline), ct.c_uint32],
    }
    for name, args in specs.items():
        method = getattr(lib, 'fpl_pipeline_' + name)
        method.argtypes, method.restype = args, ct.c_uint32
    lib.fpl_boot.argtypes, lib.fpl_boot.restype = [ct.POINTER(ControlState)], None
    lib.fpl_set.argtypes = [ct.POINTER(ControlState), ct.c_uint32, ct.POINTER(Context)]
    lib.fpl_begin.argtypes = [ct.POINTER(ControlState), ct.POINTER(Context)]
    lib.fpl_begin_direct.argtypes = [ct.POINTER(ControlState), ct.POINTER(Context)]
    lib.fpl_end.argtypes = [ct.POINTER(ControlState), ct.c_uint32]
    for name in ('fpl_set', 'fpl_begin', 'fpl_begin_direct', 'fpl_end'):
        getattr(lib, name).restype = ct.c_uint32
    lib.fpl_ui_boot.argtypes = [ct.POINTER(UI), ct.c_uint32]
    lib.fpl_ui_boot.restype = None
    lib.fpl_ui_attach.argtypes = [ct.POINTER(UI)] + [ct.c_uint32] * 6
    lib.fpl_ui_attach.restype = ct.c_uint32
    lib.fpl_ui_select.argtypes = [ct.POINTER(UI)] + [ct.c_uint32] * 3 + [
        ct.POINTER(ControlState), ct.POINTER(Context), ct.c_uint32]
    lib.fpl_ui_select.restype = ct.c_uint32
    return lib


class Harness:
    def __init__(self, lib, enabled=1, readiness=127, direct=False):
        self.lib, self.p = lib, Pipeline()
        self.state = ControlState()
        self.ui = UI()
        self.context = Context(502, 1, 1, 12, 1936, 1090, 24000, 1001, 1, readiness)
        self.buffers = []
        self.lib.fpl_boot(ct.byref(self.state))
        if direct:
            require_equal(self.lib.fpl_begin_direct(ct.byref(self.state), ct.byref(self.context)), OK)
        else:
            self.lib.fpl_ui_boot(ct.byref(self.ui), 71)
            require_equal(self.lib.fpl_ui_attach(ct.byref(self.ui), 71, 10, 7, 1, 1, 1), OK)
            require_equal(self.choose(enabled), OK)
            require_equal(self.lib.fpl_begin(ct.byref(self.state), ct.byref(self.context)), OK)
        require_equal(self.call('init', 71), OK)
        require_equal(self.call('begin'), OK)

    def call(self, name, *args):
        if name == 'begin' and not args:
            args = (ct.byref(self.state),)
        return getattr(self.lib, 'fpl_pipeline_' + name)(ct.byref(self.p), *args)

    def choose(self, value):
        return self.lib.fpl_ui_select(ct.byref(self.ui), 71, 7, 1,
                                     ct.byref(self.state), ct.byref(self.context), value)

    def frame(self, number):
        raw = ct.create_string_buffer(bytes([number % 251]) * 4096)
        metadata = ct.create_string_buffer(('frame-%d' % number).encode())
        writer = ct.create_string_buffer(16)
        self.buffers.extend((raw, metadata, writer))
        return Lease(Token(self.p.session, self.p.take, number), ct.addressof(raw),
                     ct.addressof(metadata), ct.addressof(writer), 4096, 2048)

    def arrive(self, frame, held=1, deferred=1):
        action = ct.c_uint32(99)
        result = self.call('arrive', ct.byref(frame), held, deferred, ct.byref(action))
        return result, action.value

    def output(self):
        encoded = ct.create_string_buffer(b'compressed-source' * 16)
        self.buffers.append(encoded)
        return Output(ct.addressof(encoded), 256, 512, 1)

    def select(self, number=1):
        f = self.frame(number)
        require_equal(self.arrive(f), (OK, SELECTED))
        require_equal(self.call('submitted', ct.byref(f.token), OK), OK)
        return f

    def ready(self, number=1):
        f = self.select(number)
        out = self.output()
        require_equal(self.call('complete', ct.byref(f.token), OK, 1, ct.byref(out)), OK)
        return f, out

    def handoff(self, token, result=OK, proofs=3, seen=None):
        def transfer(_context, lease, output, reported):
            f, out = lease.contents, output.contents if output else None
            if seen is not None:
                seen.append((f.token.session, f.token.take, f.token.frame,
                             f.buffer, f.metadata, f.writer, out.bytes if out else None,
                             ct.string_at(f.metadata) if f.metadata else None))
            # Explicit synthetic adapter, NOT a native writer implementation.
            reported[0] = proofs
            return result
        callback = Handoff(transfer)
        return self.call('handoff', ct.byref(token), callback, None)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which('clang')
        if not cls.compiler:
            raise RuntimeError('clang is required; no skipped implementation checks')
        cls.temp = tempfile.TemporaryDirectory(prefix='fpl-frame-pipeline-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.lib = cls.compile(HERE / 'frame_pipeline.c', 'baseline')

    @classmethod
    def compile(cls, source, name):
        target = Path(cls.temp.name) / (name + '.dylib')
        subprocess.run([cls.compiler, '-std=c11', '-O2', '-Wall', '-Wextra',
                        '-Werror', '-shared', '-fPIC', '-I', str(HERE),
                        str(source), str(HERE / 'control.c'),
                        str(HERE / 'ui_control.c'), '-o', str(target)], check=True,
                       capture_output=True, text=True, timeout=30)
        return bind(ct.CDLL(str(target)))

    def test_arm_freestanding_compilation(self):
        obj = Path(self.temp.name) / 'pipeline.arm.o'
        asm = Path(self.temp.name) / 'pipeline.arm.s'
        args = [self.compiler, '--target=armv7-none-eabi', '-mcpu=cortex-a9',
                '-marm', '-mfloat-abi=soft', '-mfpu=none', '-std=c11', '-Os',
                '-ffreestanding', '-fno-builtin', '-Wall', '-Wextra', '-Werror']
        for flag, output in (('-c', obj), ('-S', asm)):
            subprocess.run(args + [flag, str(HERE / 'frame_pipeline.c'),
                                   '-o', str(output)], check=True,
                           capture_output=True, text=True, timeout=30)
        binary = obj.read_bytes()
        self.assertEqual(binary[:4], b'\x7fELF')
        self.assertEqual(int.from_bytes(binary[18:20], 'little'), 40)
        for runtime in ('__aeabi_', 'memcpy', 'memset', 'malloc', 'free('):
            self.assertNotIn(runtime, asm.read_text())

    def continuous_scenario(self, lib):
        h = Harness(lib)
        for number in range(1, 1201):
            f, _ = h.ready(number)
            self.assertEqual(h.handoff(f.token), OK)
        self.assertEqual((h.p.selected, h.p.handed_off, h.p.passed), (1200, 1200, 0))
        self.assertEqual((h.p.active, h.p.stopping, h.p.phase), (1, 0, FREE))

    def test_no_40_frame_or_other_probe_limit(self):
        self.continuous_scenario(self.lib)

    def test_direct_no_menu_uses_same_frame_and_busy_raw_path(self):
        h = Harness(self.lib, readiness=126, direct=True)
        self.assertEqual(h.ui.magic, 0)  # never booted, attached or consulted
        first = h.select(1)
        for number in range(2, 42):
            self.assertEqual(h.arrive(h.frame(number), held=0, deferred=0), (OK, RAW))
        out = h.output()
        self.assertEqual(h.call('complete', ct.byref(first.token), OK, 1, ct.byref(out)), OK)
        seen = []
        self.assertEqual(h.handoff(first.token, seen=seen), OK)
        self.assertEqual((seen[0][2], seen[0][3]), (1, first.buffer))
        for number in range(42, 102):
            frame, _ = h.ready(number)
            self.assertEqual(h.handoff(frame.token), OK)
        self.assertEqual((h.p.selected, h.p.passed, h.p.handed_off), (61, 40, 61))
        self.assertEqual(h.call('stop'), OK)
        self.assertEqual(h.call('finish', 1), OK)
        self.assertEqual(self.lib.fpl_end(ct.byref(h.state), 1), OK)

    def identity_scenario(self, lib):
        h = Harness(lib)
        f = h.select()
        saved = (f.buffer, f.metadata, f.writer)
        for n in (2, 3):
            later = h.frame(n)
            snapshot = ct.string_at(later.buffer, later.stock_file_bytes)
            self.assertEqual(h.arrive(later, 0, 0), (OK, RAW))
            self.assertEqual(ct.string_at(later.buffer, later.stock_file_bytes), snapshot)
        # Caller descriptor is not authoritative after admission.
        f.buffer = f.metadata = f.writer = 0
        out = h.output()
        self.assertEqual(h.call('complete', ct.byref(f.token), OK, 1, ct.byref(out)), OK)
        seen = []
        self.assertEqual(h.handoff(f.token, seen=seen), OK)
        self.assertEqual(seen, [(71, 1, 1, *saved, out.bytes, b'frame-1')])
        self.assertEqual(h.arrive(h.frame(4)), (OK, SELECTED))
        self.assertEqual((h.p.selected, h.p.passed, h.p.handed_off), (2, 2, 1))

    def test_busy_passthrough_then_same_frame_handoff_and_next_arrival(self):
        self.identity_scenario(self.lib)

    def stale_scenario(self, lib):
        h = Harness(lib)
        f = h.select()
        bad = Token(f.token.session, f.token.take, f.token.frame + 1)
        before = bytes(h.p)
        self.assertEqual(h.call('complete', ct.byref(bad), OK, 1,
                                ct.byref(h.output())), INVALID)
        self.assertEqual(bytes(h.p), before)

    def test_rejects_later_frame_completion(self):
        self.stale_scenario(self.lib)

    def quiescence_scenario(self, lib):
        h = Harness(lib)
        f = h.select()
        before = bytes(h.p)
        self.assertEqual(h.call('complete', ct.byref(f.token), OK, 0,
                                ct.byref(h.output())), BUSY)
        self.assertEqual(bytes(h.p), before)
        self.assertEqual(h.call('stop'), OK)
        self.assertEqual(h.call('finish', 1), BUSY)
        self.assertEqual(h.call('begin'), BUSY)
        self.assertEqual(h.p.phase, ENCODING)

    def test_unknown_dma_does_not_release_or_restart(self):
        self.quiescence_scenario(self.lib)

    def handoff_proof_scenario(self, lib):
        for proof in (0, 1, 2, 7):
            h = Harness(lib)
            f, _ = h.ready()
            self.assertEqual(h.handoff(f.token, proofs=proof), FAULT)
            self.assertEqual((h.p.phase, h.p.stopping, h.p.handed_off), (RETAINED, 1, 0))
            self.assertEqual(h.p.lease.buffer, f.buffer)
            self.assertEqual(h.call('finish', 1), BUSY)

    def test_partial_handoff_or_output_borrow_retains_the_slot(self):
        self.handoff_proof_scenario(self.lib)

    def codec_not_handoff_scenario(self, lib):
        h = Harness(lib)
        f, _ = h.ready()
        self.assertEqual(h.p.phase, READY)
        self.assertEqual(h.arrive(h.frame(2), 0, 0), (OK, RAW))
        self.assertEqual(h.p.lease.token.frame, f.token.frame)
        self.assertEqual(h.handoff(f.token), OK)
        # No SD-completion event: original writer owns source, output is free.
        self.assertEqual(h.arrive(h.frame(3)), (OK, SELECTED))

    def test_codec_done_is_not_output_or_source_release(self):
        self.codec_not_handoff_scenario(self.lib)

    def reentrant_scenario(self, lib):
        h = Harness(lib)
        f, _ = h.ready()
        nested = []

        def transfer(_context, _lease, _output, proofs):
            nested.append(h.handoff(f.token))
            proofs[0] = 3
            return OK

        callback = Handoff(transfer)
        self.assertEqual(h.call('handoff', ct.byref(f.token), callback, None), OK)
        self.assertEqual(nested, [INVALID])
        self.assertEqual((h.p.handed_off, h.p.phase), (1, FREE))

    def test_inline_native_notification_cannot_handoff_twice(self):
        self.reentrant_scenario(self.lib)

    def test_off_on_menu_selection_drives_pipeline_without_new_facade(self):
        h = Harness(self.lib, enabled=0, readiness=0)
        self.assertEqual((h.state.requested, h.state.clip, h.p.enabled), (0, 1, 0))
        for n in range(1, 65):
            f = Lease(Token(71, 1, n), 0, 0, 0, 0, 0)
            self.assertEqual(h.arrive(f, 0, 0), (OK, RAW))
        self.assertEqual(h.choose(1), BUSY)  # GUI remains locked during REC
        self.assertEqual(h.call('stop'), OK)
        self.assertEqual(h.call('finish', 1), OK)
        self.assertEqual(h.lib.fpl_end(ct.byref(h.state), 1), OK)
        self.assertEqual(h.choose(1), NOT_READY)  # policy is not a native proof
        h.context.ready = 127  # explicit synthetic complete adapter, host only
        self.assertEqual(h.choose(1), OK)
        self.assertEqual(h.lib.fpl_begin(ct.byref(h.state), ct.byref(h.context)), OK)
        self.assertEqual(h.call('begin'), OK)
        self.assertEqual(h.p.enabled, 1)
        self.assertEqual(h.arrive(h.frame(1)), (OK, SELECTED))
        self.assertEqual(h.choose(0), BUSY)

    def test_begin_rejects_control_without_successful_rec_gate(self):
        h = Harness(self.lib)
        self.assertEqual(h.call('stop'), OK)
        self.assertEqual(h.call('finish', 1), OK)
        original = bytes(h.state)
        for field, value in (('magic', 0), ('abi', 99), ('requested', 2),
                             ('requested', 0), ('clip', 0), ('clip', 3),
                             ('reserved0', 1), ('reserved1', 1), ('fault', 9)):
            ct.memmove(ct.byref(h.state), original, len(original))
            setattr(h.state, field, value)
            before = bytes(h.p)
            self.assertEqual(h.call('begin'), INVALID)
            self.assertEqual(bytes(h.p), before)

    def test_faulted_lossless_can_return_to_off_after_proven_cleanup(self):
        h = Harness(self.lib)
        f, _ = h.ready()
        self.assertEqual(h.handoff(f.token, result=9), FAULT)
        self.assertEqual(h.call('reap', ct.byref(f.token), 1, 1), OK)
        self.assertEqual(h.call('finish', 1), OK)
        self.assertEqual(h.lib.fpl_end(ct.byref(h.state), 1), OK)
        self.assertEqual(h.choose(0), OK)
        self.assertEqual(h.lib.fpl_begin(ct.byref(h.state), ct.byref(h.context)), OK)
        self.assertEqual(h.call('begin'), OK)
        self.assertEqual(h.arrive(h.frame(1), 0, 0), (OK, RAW))
        self.assertEqual((h.p.enabled, h.p.fault), (0, 9))

    def test_handoff_error_retains_and_requires_external_cleanup(self):
        h = Harness(self.lib)
        f, _ = h.ready()
        self.assertEqual(h.handoff(f.token, result=55, proofs=3), FAULT)
        for idle, resolved in ((0, 0), (0, 1), (1, 0), (2, 1)):
            before = bytes(h.p)
            self.assertEqual(h.call('reap', ct.byref(f.token), idle, resolved), BUSY)
            self.assertEqual(bytes(h.p), before)
        self.assertEqual(h.call('reap', ct.byref(f.token), 1, 1), OK)
        self.assertEqual(h.call('finish', 1), OK)
        self.assertEqual(h.call('begin'), FAULT)

    def test_failed_hold_or_defer_does_not_adopt_source(self):
        for held, deferred in ((0, 1), (1, 0), (0, 0), (2, 1)):
            h = Harness(self.lib)
            f = h.frame(1)
            before = bytes(h.p)
            self.assertEqual(h.arrive(f, held, deferred), (NOT_READY, NONE))
            self.assertEqual(bytes(h.p), before)

    def test_failed_start_is_not_assumed_atomic_or_quiescent(self):
        h = Harness(self.lib)
        f = h.frame(1)
        self.assertEqual(h.arrive(f), (OK, SELECTED))
        self.assertEqual(h.call('submitted', ct.byref(f.token), 9), FAULT)
        self.assertEqual((h.p.phase, h.p.lease.buffer), (RETAINED, f.buffer))
        self.assertEqual(h.call('reap', ct.byref(f.token), 0, 1), BUSY)
        self.assertEqual(h.call('finish', 1), BUSY)

    def test_stop_drains_existing_job_then_restarts_with_fresh_take(self):
        h = Harness(self.lib)
        f = h.select()
        self.assertEqual(h.call('stop'), OK)
        self.assertEqual(h.call('stop'), OK)
        self.assertEqual(h.arrive(h.frame(2), 0, 0), (OK, RAW))
        self.assertEqual(h.call('complete', ct.byref(f.token), OK, 1,
                                ct.byref(h.output())), OK)
        self.assertEqual(h.handoff(f.token), OK)
        self.assertEqual(h.call('finish', 0), BUSY)
        self.assertEqual(h.call('finish', 1), OK)
        self.assertEqual(h.lib.fpl_end(ct.byref(h.state), 1), OK)
        self.assertEqual(h.lib.fpl_begin(ct.byref(h.state), ct.byref(h.context)), OK)
        self.assertEqual(h.call('begin'), OK)
        current = h.select(1)
        self.assertEqual(current.token.take, 2)
        before = bytes(h.p)
        self.assertEqual(h.call('complete', ct.byref(f.token), OK, 1,
                                ct.byref(h.output())), INVALID)
        self.assertEqual(bytes(h.p), before)

    def test_all_token_fields_and_frame_order_are_required(self):
        h = Harness(self.lib)
        f = h.select(3)
        for field in ('session', 'take', 'frame'):
            bad = Token(f.token.session, f.token.take, f.token.frame)
            setattr(bad, field, getattr(bad, field) + 1)
            before = bytes(h.p)
            self.assertEqual(h.call('complete', ct.byref(bad), OK, 1,
                                    ct.byref(h.output())), INVALID)
            self.assertEqual(bytes(h.p), before)
        for number in (0, 1, 3):
            self.assertEqual(h.arrive(h.frame(number)), (INVALID, NONE))

    def test_duplicate_completion_and_handoff_are_refused(self):
        h = Harness(self.lib)
        f, out = h.ready()
        self.assertEqual(h.call('complete', ct.byref(f.token), OK, 1, ct.byref(out)), INVALID)
        self.assertEqual(h.handoff(f.token), OK)
        self.assertEqual(h.handoff(f.token), INVALID)
        self.assertEqual(h.p.handed_off, 1)

    def test_output_validation_and_engine_error_are_not_success(self):
        for field, value in (('bytes', 0), ('length', 0), ('length', 513),
                             ('validated', 0), ('validated', 2)):
            h = Harness(self.lib)
            f, out = h.select(), h.output()
            setattr(out, field, value)
            self.assertEqual(h.call('complete', ct.byref(f.token), OK, 1, ct.byref(out)), FAULT)
            self.assertEqual((h.p.phase, h.p.handed_off), (RETAINED, 0))
        h = Harness(self.lib)
        f = h.select()
        self.assertEqual(h.call('complete', ct.byref(f.token), 77, 1, None), FAULT)
        self.assertEqual((h.p.fault, h.p.phase), (77, RETAINED))

    def no_benefit_scenario(self, lib):
        for file_bytes in (2048, 2049, 8192):
            h = Harness(lib)
            f, out = h.select(), h.output()
            original = ct.string_at(f.buffer, f.stock_file_bytes)
            out.file_bytes = file_bytes
            if file_bytes > f.capacity:
                # A genuinely separate owned output can exceed native frame
                # capacity; RAW handoff must not attempt to copy any of it.
                large_output = ct.create_string_buffer(b'x' * file_bytes)
                h.buffers.append(large_output)
                out.bytes = ct.addressof(large_output)
                out.length = file_bytes - 64
            self.assertEqual(h.call('complete', ct.byref(f.token), OK, 1, ct.byref(out)), OK)
            self.assertEqual((h.p.phase, h.p.fault), (READY_RAW, 0))
            seen = []
            self.assertEqual(h.handoff(f.token, seen=seen), OK)
            self.assertEqual(seen, [(71, 1, 1, f.buffer, f.metadata, f.writer,
                                    None, b'frame-1')])
            self.assertEqual(ct.string_at(f.buffer, f.stock_file_bytes), original)
            self.assertEqual((h.p.handed_off, h.p.fault, h.p.stopping), (1, 0, 0))
            following, _ = h.ready(2)
            self.assertEqual(h.handoff(following.token), OK)
            self.assertEqual(h.call('stop'), OK)
            self.assertEqual(h.call('finish', 1), OK)
            self.assertEqual(h.lib.fpl_end(ct.byref(h.state), 1), OK)
            self.assertEqual(h.lib.fpl_begin(ct.byref(h.state), ct.byref(h.context)), OK)
            self.assertEqual(h.call('begin'), OK)
            self.assertEqual(h.arrive(h.frame(1)), (OK, SELECTED))

    def test_equal_or_larger_result_transfers_same_unchanged_raw_without_fault(self):
        self.no_benefit_scenario(self.lib)

    def test_no_benefit_still_requires_dma_quiescence_and_handoff_proofs(self):
        for status, proofs in ((9, 3), (OK, 0), (OK, 1), (OK, 2)):
            h = Harness(self.lib)
            f, out = h.select(), h.output()
            out.file_bytes = 8192
            before = bytes(h.p)
            self.assertEqual(h.call('complete', ct.byref(f.token), OK, 0, ct.byref(out)), BUSY)
            self.assertEqual(bytes(h.p), before)
            self.assertEqual(h.call('complete', ct.byref(f.token), OK, 1, ct.byref(out)), OK)
            self.assertEqual(h.p.phase, READY_RAW)
            self.assertEqual(h.handoff(f.token, result=status, proofs=proofs), FAULT)
            self.assertEqual((h.p.phase, h.p.handed_off), (RETAINED, 0))
            self.assertEqual(h.p.lease.buffer, f.buffer)
            self.assertEqual(h.call('finish', 1), BUSY)

    def test_invalid_output_cannot_claim_ordinary_no_benefit(self):
        for field, value in (('bytes', 0), ('length', 0), ('validated', 0),
                             ('length', 8193)):
            h = Harness(self.lib)
            f, out = h.select(), h.output()
            out.file_bytes = 8192
            setattr(out, field, value)
            self.assertEqual(h.call('complete', ct.byref(f.token), OK, 1, ct.byref(out)), FAULT)
            self.assertEqual(h.p.phase, RETAINED)

    def test_invalid_lease_and_busy_pointer_fields(self):
        for field, value in (('buffer', 0), ('metadata', 0), ('writer', 0),
                             ('stock_file_bytes', 0), ('capacity', 1),
                             ('buffer', ct.c_size_t(-1).value)):
            h = Harness(self.lib)
            f = h.frame(1)
            setattr(f, field, value)
            self.assertEqual(h.arrive(f), (INVALID, NONE))
            self.assertEqual(h.p.phase, FREE)
        h = Harness(self.lib)
        h.select()
        f = Lease(Token(71, 1, 2), 0, 0, 0, 0, 0)
        self.assertEqual(h.arrive(f, 0, 0), (OK, RAW))

    def test_statistics_saturate_without_stopping_recording(self):
        h = Harness(self.lib)
        h.p.seen = h.p.selected = h.p.handed_off = 0xffffffff
        f, _ = h.ready()
        self.assertEqual(h.handoff(f.token), OK)
        self.assertEqual((h.p.seen, h.p.selected, h.p.handed_off), (0xffffffff,) * 3)
        self.assertEqual((h.p.phase, h.p.stopping), (FREE, 0))

    def test_init_and_shutdown_require_real_lifecycle(self):
        h = Harness(self.lib)
        self.assertEqual(h.call('init', 72), INVALID)
        self.assertEqual(h.call('finish', 1), INVALID)
        self.assertEqual(h.call('begin'), BUSY)
        self.assertEqual(h.call('stop'), OK)
        self.assertEqual(h.call('finish', 1), OK)
        self.assertEqual(h.call('stop'), INVALID)
        h.p.take = 0xffffffff
        self.assertEqual(h.call('begin'), INVALID)

    def test_isolated_mutations_are_caught(self):
        original = (HERE / 'frame_pipeline.c').read_text()
        mutations = [
            ('frame-identity', 'a->frame == b->frame', '1', self.stale_scenario),
            ('dma-proof', 'if (quiescent != 1) return FPL_BUSY;',
             '(void)quiescent;', self.quiescence_scenario),
            ('handoff-proof', 'result != FPL_OK || proofs != FPL_HANDOFF_ALL',
             'result != FPL_OK', self.handoff_proof_scenario),
            ('forty-frame-limit', 'if (!p->enabled || p->stopping || p->phase != FPL_SLOT_FREE)',
             'if (p->selected >= 40 || !p->enabled || p->stopping || p->phase != FPL_SLOT_FREE)',
             self.continuous_scenario),
            ('wrong-metadata', 'p->lease = *f;',
             'p->lease = *f; p->lease.metadata = 0;', self.identity_scenario),
            ('early-slot-release', 'p->phase = FPL_SLOT_READY;',
             'p->phase = FPL_SLOT_FREE;', self.codec_not_handoff_scenario),
            ('nested-handoff', 'p->phase = FPL_SLOT_COMMITTING;',
             '/* deliberately allow callback reentry */', self.reentrant_scenario),
            ('no-benefit-fault', 'p->phase = FPL_SLOT_READY_RAW;\n        return FPL_OK;',
             'return retain(p, FPL_INVALID);', self.no_benefit_scenario),
        ]
        for name, old, new, scenario in mutations:
            with self.subTest(mutation=name):
                self.assertEqual(original.count(old), 1)
                source = Path(self.temp.name) / (name + '.c')
                source.write_text(original.replace(old, new, 1))
                mutated = self.compile(source, name)
                with self.assertRaises(AssertionError):
                    scenario(mutated)


if __name__ == '__main__':
    unittest.main()
