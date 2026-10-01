"""Offline RAW workspace tests: actual ARM C and original descriptor wrappers.

Only memory-mode lookup, pool selection and the physical allocate/free services
are synthetic. C001CFD8/C001D3D0 execute the pinned firmware instructions. This
does not establish camera free space, producer exclusion, DMA drain or a usable
recording integration. All JIT work is child-isolated; unavailable execution is
an error, never a skipped or passing ARM result. No transport is imported.
"""
import hashlib
import ctypes
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HELPER = ROOT / 'research/ui/tools/arm_text/elf_text.py'
IMAGE = ROOT / 'out/seg0_c0000000.bin'
IMAGE_SHA256 = 'aaa5208a028d9c4aebb9cc8614add723d456e96b2a95914f433079954320e622'
OK, INVALID, BUSY, UNSUPPORTED, NOT_READY, FAULT = range(6)
CODE, RAM, STACK, STOP = 0x30000000, 0x45000000, 0x46010000, 0x47000000
WS, POOL = RAM + 0x100, RAM + 0x1000
HANDLE, BYTES = 0x50000000, 64 * 1024 * 1024
MODE, SELECT, GET, ALLOC, FREE, DEALLOC = (
    0xC001CF18, 0xC001CF78, 0xC001CFD8,
    0xC001D038, 0xC001D3D0, 0xC001D2B8)
NATIVE_RANGES = ((GET, ALLOC), (FREE, 0xC001D420))


def checked_image():
    image = IMAGE.read_bytes()
    if hashlib.sha256(image).hexdigest() != IMAGE_SHA256:
        raise AssertionError('firmware image hash differs; original ARM unverified')
    return image


class ArmWorkspace:
    """Only the workspace metadata and call stack are guest-writable."""
    def __init__(self, text, image):
        from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE,
                             UC_HOOK_MEM_WRITE, UC_PROT_READ, UC_PROT_WRITE,
                             UC_PROT_EXEC, arm_const)
        self.text, self.reg = text, arm_const
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        extent = (len(text.code) + 4095) & ~4095
        self.uc.mem_map(CODE, extent, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_write(CODE, text.code)
        self.uc.mem_protect(CODE, extent, UC_PROT_READ | UC_PROT_EXEC)
        self.uc.mem_map(0xC001C000, 0x2000, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_write(0xC001C000, image[0x1C000:0x1E000])
        self.uc.mem_protect(0xC001C000, 0x2000, UC_PROT_READ | UC_PROT_EXEC)
        self.uc.mem_map(RAM, 0x1000, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_map(POOL, 0x1000, UC_PROT_READ)
        self.uc.mem_map(HANDLE, 0x1000, UC_PROT_READ)
        self.uc.mem_map(STACK - 0x10000, 0x10000, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_map(STOP, 0x1000, UC_PROT_READ | UC_PROT_EXEC)
        self.uc.hook_add(UC_HOOK_CODE, self.instruction)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.guest_write)
        self.mode = 3
        self.pool_available = True
        self.pool_return = POOL
        self.allocation_available = True
        self.return_handle = HANDLE
        self.return_capacity = BYTES
        self.pool_class = 10
        self.events, self.native_entries, self.legacy_sp = [], [], []
        self.live = {}
        self.write(POOL, self.pool_class)
        self.uc.mem_write(HANDLE, b'owned-image-untouched' * 16)

    def r(self, name):
        return self.uc.reg_read(getattr(self.reg, 'UC_ARM_REG_' + name.upper()))

    def setr(self, name, value):
        self.uc.reg_write(getattr(self.reg, 'UC_ARM_REG_' + name.upper()), value)

    def write(self, address, *words):
        self.uc.mem_write(address, struct.pack('<' + 'I' * len(words), *words))

    def words(self, address, count=1):
        return struct.unpack('<' + 'I' * count, self.uc.mem_read(address, count * 4))

    def state(self):
        return self.words(WS, 6)

    def seed(self, *words):
        if len(words) != 6:
            raise AssertionError('workspace fixture must contain six ARM32 words')
        self.write(WS, *words)

    def ret(self, value=0):
        # A real firmware call may clobber all caller-saved registers. Poison
        # them so a wrapper accidentally relying on a stub's preservation fails.
        pc = self.r('lr')
        for n in (1, 2, 3, 12):
            self.setr(f'r{n}', 0xBAD00000 + n)
        self.setr('r0', value)
        self.setr('pc', pc)

    def guest_write(self, _uc, _access, address, size, _value, _data):
        allowed = ((WS, WS + 24), (STACK - 0x10000, STACK - 0x100))
        if not any(a <= address and address + size <= z for a, z in allowed):
            raise AssertionError(f'guest write outside workspace/stack: {address:#x}+{size}')

    def instruction(self, _uc, pc, size, _data):
        if CODE <= pc and pc + size <= CODE + len(self.text.code):
            return
        if pc == STOP:
            self.stopped = True
            self.uc.emu_stop()
            return
        if pc in (MODE, SELECT, GET, FREE):
            if self.r('sp') % 8:
                raise AssertionError(f'new wrapper calls {pc:#x} with unaligned SP')
            if pc in (GET, FREE):
                self.native_entries.append(pc)
        if pc == MODE:
            self.events.append(('mode', self.mode))
            self.ret(self.mode)
            return
        if pc == SELECT:
            selected = self.r('r0')
            self.events.append(('select', selected))
            self.write(POOL, self.pool_class)
            self.ret(self.pool_return if self.pool_available else 0)
            return
        if pc == GET:
            self.events.append(('get', *(self.r(f'r{i}') for i in range(4)),
                                self.words(self.r('sp'))[0]))
        if pc == ALLOC:
            # The original descriptor wrapper uses push24/sub4, so this
            # legacy nested boundary is four-byte aligned. Do not pretend our
            # new caller used that ABI, or silently rewrite the firmware.
            self.legacy_sp.append(self.r('sp') % 8)
            if self.r('sp') % 8 != 4:
                raise AssertionError('original descriptor-wrapper stack changed')
            pool, requested, alignment, capacity = (self.r(f'r{i}') for i in range(4))
            if pool != POOL or alignment != 0x400:
                raise AssertionError('wrong physical allocator pool/alignment')
            if not (WS <= capacity <= WS + 16 or
                    STACK - 0x10000 <= capacity <= STACK - 0x104):
                raise AssertionError('allocator capacity output is not owned metadata')
            self.events.append(('allocate', requested, alignment, self.pool_class))
            if self.allocation_available:
                self.write(capacity, self.return_capacity)
                if self.return_handle:
                    self.live[self.return_handle] = self.pool_class
                self.ret(self.return_handle)
            else:
                self.ret(0)
            return
        if pc == FREE:
            self.events.append(('free_descriptor', *self.words(self.r('r0'), 3)))
        if pc == DEALLOC:
            if self.r('sp') % 8:
                raise AssertionError('native free path entered unaligned')
            pool, handle = self.r('r0'), self.r('r1')
            if pool != POOL or handle not in self.live:
                raise AssertionError('native free does not match a live allocation')
            self.events.append(('free', handle, self.live.pop(handle)))
            self.ret()
            return
        if any(a <= pc and pc + size <= z for a, z in NATIVE_RANGES):
            return
        raise AssertionError(f'unapproved external PC: {pc:#010x}')

    def call(self, name, *args):
        saved = {f'r{i}': 0xA9AA1000 + i for i in range(4, 12)}
        for register, value in saved.items():
            self.setr(register, value)
        sp = STACK - 0x100
        self.setr('sp', sp)
        self.setr('lr', STOP)
        for index in range(4):
            self.setr(f'r{index}', args[index] if index < len(args) else 0xBAD00000 + index)
        self.stopped = False
        payload = bytes(self.uc.mem_read(HANDLE, 4096))
        entry = CODE + self.text.functions['fpl_raw_workspace_' + name]
        self.uc.emu_start(entry, 0, timeout=250000, count=20000)
        if not self.stopped:
            raise AssertionError('bounded ARM workspace call did not return')
        if self.r('sp') != sp or any(self.r(k) != v for k, v in saved.items()):
            raise AssertionError('workspace/native call clobbered SP or r4-r11')
        if bytes(self.uc.mem_read(HANDLE, 4096)) != payload:
            raise AssertionError('workspace metadata operation touched image payload')
        return self.r('r0')

    def reserve(self, take=7, size=BYTES, address=WS):
        return self.call('reserve', address, take, size)

    def release(self, take=7, quiescent=1, address=WS):
        return self.call('release', address, take, quiescent)


HOST_BRIDGE = r'''
#include "raw_workspace.h"
static uint32_t (*test_mode)(void);
static uint32_t (*test_pool)(uint32_t);
static uint32_t (*test_get)(uint32_t, struct fpl_raw_descriptor *,
                           uint32_t, uint32_t, uint32_t);
static void (*test_free)(struct fpl_raw_descriptor *);
void fpl_test_bind(uint32_t (*m)(void), uint32_t (*p)(uint32_t),
                  uint32_t (*g)(uint32_t, struct fpl_raw_descriptor *,
                                uint32_t, uint32_t, uint32_t),
                  void (*f)(struct fpl_raw_descriptor *)) {
    test_mode = m; test_pool = p; test_get = g; test_free = f;
}
uint32_t fpl_raw_workspace_native_mode(void) { return test_mode(); }
uint32_t fpl_raw_workspace_native_pool(uint32_t type) { return test_pool(type); }
uint32_t fpl_raw_workspace_native_get(uint32_t pool, struct fpl_raw_descriptor *d,
                                    uint32_t bytes, uint32_t alignment, uint32_t caller) {
    return test_get(pool, d, bytes, alignment, caller);
}
void fpl_raw_workspace_native_free(struct fpl_raw_descriptor *d) { test_free(d); }
'''


class HostWorkspace:
    """Real C policy with four explicitly substituted host-native services."""
    def __init__(self, library):
        self.library = library
        self.storage = (ctypes.c_uint32 * 6)()
        self.mode = 3
        self.pool_available = True
        self.pool_return = POOL
        self.allocation_available = True
        self.return_handle, self.return_capacity, self.pool_class = HANDLE, BYTES, 10
        self.get_return = None
        self.events, self.live, self.errors = [], {}, []
        u32, pointer = ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)
        mode_type = ctypes.CFUNCTYPE(u32)
        pool_type = ctypes.CFUNCTYPE(u32, u32)
        get_type = ctypes.CFUNCTYPE(u32, u32, pointer, u32, u32, u32)
        free_type = ctypes.CFUNCTYPE(None, pointer)

        def mode():
            self.events.append(('mode', self.mode))
            return self.mode

        def pool(selected):
            self.events.append(('select', selected))
            return self.pool_return if self.pool_available else 0

        def get(pool, desc, requested, alignment, caller):
            self.events.append(('get', pool, ctypes.addressof(desc.contents),
                                requested, alignment, caller))
            if pool != POOL or alignment != 0x400:
                self.errors.append('wrong host physical allocator pool/alignment')
            self.events.append(('allocate', requested, alignment, self.pool_class))
            desc[0] = self.return_handle if self.allocation_available else 0
            desc[1] = self.return_capacity if self.allocation_available else 0
            desc[2] = self.pool_class
            if desc[0]:
                self.live[desc[0]] = desc[2]
            return desc[0] if self.get_return is None else self.get_return

        def free(desc):
            self.events.append(('free_descriptor', desc[0], desc[1], desc[2]))
            if desc[0] not in self.live:
                self.errors.append('host free does not match live allocation')
                return
            self.events.append(('free', desc[0], self.live.pop(desc[0])))

        self.callbacks = (mode_type(mode), pool_type(pool), get_type(get), free_type(free))
        library.fpl_test_bind.argtypes = [mode_type, pool_type, get_type, free_type]
        library.fpl_test_bind.restype = None
        library.fpl_test_bind(*self.callbacks)
        for name in ('reserve', 'release'):
            fn = getattr(library, 'fpl_raw_workspace_' + name)
            fn.argtypes = [ctypes.c_void_p, u32, u32]
            fn.restype = u32

    def state(self):
        return tuple(self.storage)

    def seed(self, *words):
        if len(words) != 6:
            raise AssertionError('workspace fixture must contain six words')
        self.storage[:] = words

    def call(self, name, address, *args):
        # WS is a shared fixture token, never a live camera address on host.
        pointer = (ctypes.addressof(self.storage) + address - WS
                   if WS <= address < WS + 24 else address)
        result = getattr(self.library, 'fpl_raw_workspace_' + name)(pointer, *args)
        if self.errors:
            raise AssertionError('; '.join(self.errors))
        return result

    def reserve(self, take=7, size=BYTES, address=WS):
        return self.call('reserve', address, take, size)

    def release(self, take=7, quiescent=1, address=WS):
        return self.call('release', address, take, quiescent)


def source_variant(mutation):
    source = (HERE / 'raw_workspace.c').read_text()
    changes = {
        'user-pool': ('#define RAW_CLASS 10u', '#define RAW_CLASS 0u'),
        'early-free': ('if (quiescent != 1u) return FPL_BUSY;', '(void)quiescent;'),
    }
    if mutation:
        before, after = changes[mutation]
        if source.count(before) != 1:
            raise AssertionError('mutation requires exactly one matching safety guard')
        source = source.replace(before, after)
    return source


def compile_source(directory, *, host=False, mutation=None):
    clang = shutil.which('clang')
    if clang is None:
        raise RuntimeError('clang required; no workspace tests skipped')
    source = source_variant(mutation)
    source += r'''
_Static_assert(sizeof(struct fpl_raw_workspace) == 24, "workspace test ABI");
_Static_assert(offsetof(struct fpl_raw_workspace, allocation) == 0, "allocation ABI");
_Static_assert(offsetof(struct fpl_raw_workspace, take) == 12, "take ABI");
_Static_assert(offsetof(struct fpl_raw_workspace, requested) == 16, "request ABI");
_Static_assert(offsetof(struct fpl_raw_workspace, fault) == 20, "fault ABI");
'''
    out = Path(directory) / ((mutation or 'workspace') + ('.dylib' if host else '.arm.o'))
    flags = ['-std=c11', '-O3', '-Wall', '-Wextra', '-Werror', '-Wconversion',
             '-I', str(HERE), '-x', 'c']
    if host:
        flags += ['-DFPL_RAW_WORKSPACE_HOST_TEST', '-shared', '-fPIC']
        source += HOST_BRIDGE
    else:
        flags += ['--target=armv7-none-eabi', '-mcpu=cortex-a9', '-marm',
                  '-mfloat-abi=soft', '-mfpu=none', '-ffreestanding',
                  '-fno-builtin', '-fno-addrsig', '-fno-unwind-tables',
                  '-fno-asynchronous-unwind-tables', '-c']
        source += '\n_Static_assert(sizeof(uintptr_t) == 4, "ARM32 test ABI");\n'
    result = subprocess.run([clang, *flags, '-', '-o', str(out)], input=source,
                            text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('workspace compilation failed:\n' + result.stderr)
    if host:
        return ctypes.CDLL(str(out))
    spec = importlib.util.spec_from_file_location('raw_workspace_checked_elf', HELPER)
    elf = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = elf
    spec.loader.exec_module(elf)
    return elf.load_text(out)  # No applying, deleting or ignoring relocations.


def semantic_cases(factory, mutant_factory, label):
    class WorkspaceCases(unittest.TestCase):
        def untouched(self, h, result, call):
            before, events = h.state(), list(h.events)
            self.assertEqual(call(), result)
            self.assertEqual(h.state(), before)
            self.assertEqual(h.events, events)

        def test_01_64mib_native_descriptor_and_exact_abi(self):
            h = factory()
            self.assertEqual(h.reserve(), OK)
            self.assertEqual(h.state(), (HANDLE, BYTES, 10, 7, BYTES, 0))
            self.assertEqual(h.events[:2], [('mode', 3), ('select', 10)])
            get = next(e for e in h.events if e[0] == 'get')
            self.assertEqual((get[1], *get[3:]), (POOL, BYTES, 0x400, 0))
            self.assertEqual(h.live, {HANDLE: 10})
            self.assertFalse(any(e[0] == 'free' for e in h.events))

        def test_02_wrong_modes_never_allocate(self):
            for mode in (*range(3), *range(4, 14), 0xFFFFFFFF):
                h = factory()
                h.mode = mode
                self.assertEqual(h.reserve(), NOT_READY)
                self.assertEqual(h.state(), (0,) * 6)
                self.assertEqual(h.events, [('mode', mode)])

        def test_03_bad_arguments_do_not_touch_native_or_state(self):
            for kwargs in ({'take': 0}, {'size': 0}, {'size': 1}, {'size': 1023},
                           {'size': 1025}, {'size': 0xFFFFFFFF}, {'address': 0},
                           {'address': WS + 1}, {'address': WS + 2}):
                h = factory()
                self.untouched(h, INVALID, lambda: h.reserve(**kwargs))
            for kwargs in ({'take': 0}, {'address': 0}, {'address': WS + 1}):
                h = factory()
                self.untouched(h, INVALID, lambda: h.release(**kwargs))

        def test_04_pool_failure_is_sticky_not_retryable_exhaustion(self):
            for pool in (0, POOL + 1):
                h = factory()
                h.pool_return = pool
                self.assertEqual(h.reserve(), FAULT)
                self.assertEqual(h.state(), (0, 0, 0, 7, BYTES, FAULT))
                self.assertEqual(h.events, [('mode', 3), ('select', 10)])
                self.untouched(h, FAULT, h.reserve)
                self.untouched(h, FAULT, h.release)

        def test_05_normal_native_exhaustion_is_retryable(self):
            h = factory()
            h.allocation_available = False
            self.assertEqual(h.reserve(), NOT_READY)
            self.assertEqual(h.state(), (0,) * 6)
            self.assertEqual(h.live, {})
            self.assertFalse(any(e[0] == 'free' for e in h.events))
            h.allocation_available = True
            self.assertEqual(h.reserve(), OK)
            self.assertEqual(h.state(), (HANDLE, BYTES, 10, 7, BYTES, 0))

        def test_06_normal_held_state_rejects_reserve_without_replacement(self):
            h = factory()
            self.assertEqual(h.reserve(), OK)
            self.untouched(h, BUSY, h.reserve)
            self.untouched(h, BUSY, lambda: h.reserve(take=8, size=32 * 1024 * 1024))
            self.assertEqual(h.live, {HANDLE: 10})

        def test_07_foreign_nonzero_state_is_never_adopted(self):
            for index in range(5):
                h = factory()
                words = [0] * 6
                words[index] = 1
                h.seed(*words)
                self.untouched(h, BUSY, h.reserve)
            h = factory()
            h.seed(0, 0, 0, 0, 0, 0x1234)
            self.untouched(h, FAULT, h.reserve)
            self.untouched(h, FAULT, h.release)

        def test_08_wrong_take_cannot_free_or_replace_owner(self):
            h = factory()
            self.assertEqual(h.reserve(), OK)
            for take in (0, 6, 8, 0xFFFFFFFF):
                self.untouched(h, INVALID, lambda: h.release(take=take))
            self.assertEqual(h.live, {HANDLE: 10})

        def test_09_no_exact_drain_proof_never_calls_free(self):
            h = factory()
            self.assertEqual(h.reserve(), OK)
            for proof in (0, 2, 3, 0xFFFFFFFF):
                self.untouched(h, BUSY, lambda: h.release(quiescent=proof))
            self.assertEqual(h.live, {HANDLE: 10})

        def test_10_mode_changed_fault_survives_mode_restoration(self):
            h = factory()
            self.assertEqual(h.reserve(), OK)
            h.mode = 0
            self.assertEqual(h.release(), FAULT)
            self.assertEqual(h.state(), (HANDLE, BYTES, 10, 7, BYTES, FAULT))
            self.assertEqual(h.live, {HANDLE: 10})
            h.mode = 3
            self.untouched(h, FAULT, h.release)
            self.untouched(h, FAULT, lambda: h.reserve(take=8))

        def test_11_bad_native_shapes_quarantine_without_blind_free(self):
            for handle, capacity, owner in (
                    (HANDLE + 1, BYTES, 10), (HANDLE, BYTES - 1, 10),
                    (HANDLE, 0, 10), (HANDLE, BYTES, 0), (HANDLE, BYTES, 14),
                    (0xFFFFFC00, BYTES, 10), (0, BYTES, 10)):
                h = factory()
                h.return_handle, h.return_capacity, h.pool_class = handle, capacity, owner
                self.assertEqual(h.reserve(), FAULT)
                self.assertEqual(h.state(), (handle, capacity, owner, 7, BYTES, FAULT))
                self.assertFalse(any(e[0] == 'free' for e in h.events))
                self.untouched(h, FAULT, h.reserve)
                self.untouched(h, FAULT, h.release)

        def test_12_corrupt_held_shape_cannot_reach_native_free(self):
            corruptions = ((0, HANDLE + 1), (1, BYTES - 1), (2, 0),
                           (4, BYTES + 1), (4, 0), (0, 0xFFFFFC00))
            for index, value in corruptions:
                h = factory()
                self.assertEqual(h.reserve(), OK)
                words = list(h.state())
                words[index] = value
                h.seed(*words)
                events = list(h.events)
                self.assertEqual(h.release(), FAULT)
                self.assertEqual(h.state(), (*words[:5], FAULT))
                self.assertEqual(h.events, events)
                self.assertEqual(h.live, {HANDLE: 10})

        def test_13_success_release_zeroes_all_and_never_double_frees(self):
            h = factory()
            self.assertEqual(h.reserve(), OK)
            self.assertEqual(h.release(), OK)
            self.assertEqual(h.state(), (0,) * 6)
            self.assertEqual(h.live, {})
            self.assertEqual([e for e in h.events if e[0] == 'free'], [('free', HANDLE, 10)])
            self.untouched(h, INVALID, h.release)

        def test_14_same_mode_next_rec_gets_new_owned_descriptor(self):
            h = factory()
            self.assertEqual(h.reserve(), OK)
            self.assertEqual(h.release(), OK)
            h.return_handle = HANDLE + 0x08000000
            self.assertEqual(h.reserve(take=8), OK)
            self.assertEqual(h.state(), (HANDLE + 0x08000000, BYTES, 10, 8, BYTES, 0))
            self.untouched(h, INVALID, lambda: h.release(take=7))
            self.assertEqual(h.release(take=8), OK)
            self.assertEqual(h.live, {})
            self.assertEqual(len([e for e in h.events if e[0] == 'allocate']), 2)

        def test_15_actual_capacity_not_requested_size_is_preserved(self):
            h = factory()
            h.return_capacity = BYTES + 1536
            self.assertEqual(h.reserve(take=0xFFFFFFFF), OK)
            self.assertEqual(h.state(), (HANDLE, BYTES + 1536, 10, 0xFFFFFFFF, BYTES, 0))
            self.assertEqual(h.release(take=0xFFFFFFFF), OK)
            self.assertIn(('free_descriptor', HANDLE, BYTES + 1536, 10), h.events)

        def test_16_exact_minimum_aligned_request(self):
            h = factory()
            h.return_capacity = 1024
            self.assertEqual(h.reserve(size=1024), OK)
            self.assertEqual(h.state(), (HANDLE, 1024, 10, 7, 1024, 0))
            self.assertEqual(h.release(), OK)

        def test_17_isolated_class_zero_mutation_has_wrong_pool_effect(self):
            h = mutant_factory('user-pool')
            h.pool_class = 0
            self.assertEqual(h.reserve(), OK)
            self.assertEqual(h.state(), (HANDLE, BYTES, 0, 7, BYTES, 0))
            self.assertIn(('select', 0), h.events)
            self.assertIn(('allocate', BYTES, 0x400, 0), h.events)
            with self.assertRaises(AssertionError):
                self.assertEqual(h.state()[2], 10, 'workspace must use RAW, not USER')

        def test_18_isolated_drain_mutation_really_frees_held_memory(self):
            h = mutant_factory('early-free')
            self.assertEqual(h.reserve(), OK)
            self.assertEqual(h.release(quiescent=0), OK)
            self.assertEqual(h.state(), (0,) * 6)
            self.assertEqual(h.live, {})
            self.assertIn(('free', HANDLE, 10), h.events)
            with self.assertRaises(AssertionError):
                self.assertFalse(any(e[0] == 'free' for e in h.events),
                                 'no drain proof must retain, never free')

    WorkspaceCases.__name__ = label + 'WorkspaceCases'
    WorkspaceCases.__qualname__ = WorkspaceCases.__name__
    return unittest.defaultTestLoader.loadTestsFromTestCase(WorkspaceCases)


def extra_cases(text, image, host):
    class NativeBoundaryCases(unittest.TestCase):
        def test_19_original_wrappers_execute_and_document_legacy_stack(self):
            h = ArmWorkspace(text, image)
            self.assertEqual(h.reserve(), OK)
            self.assertEqual(h.release(), OK)
            self.assertEqual(h.native_entries, [GET, FREE])
            self.assertEqual(h.legacy_sp, [4])

        def test_20_unapproved_pc_is_rejected(self):
            h = ArmWorkspace(text, image)
            with self.assertRaisesRegex(AssertionError, 'unapproved external PC'):
                h.uc.emu_start(STOP + 0x100, 0, timeout=100000, count=4)

        def test_21_code_rom_payload_are_rx_or_read_only(self):
            from unicorn import UC_PROT_READ, UC_PROT_EXEC
            h = ArmWorkspace(text, image)
            regions = list(h.uc.mem_regions())
            for address, permissions in ((CODE, UC_PROT_READ | UC_PROT_EXEC),
                                         (GET, UC_PROT_READ | UC_PROT_EXEC),
                                         (HANDLE, UC_PROT_READ), (POOL, UC_PROT_READ)):
                self.assertTrue(any(a <= address <= z and p == permissions
                                    for a, z, p in regions))
            with self.assertRaisesRegex(AssertionError, 'guest write outside'):
                h.guest_write(None, None, WS + 24, 4, 0, None)

        def test_22_host_native_result_mismatch_is_sticky(self):
            # The pinned C001CFD8 returns exactly d->handle, so this explicitly
            # corrupted service contract is host-only, not fabricated firmware.
            h = HostWorkspace(host)
            h.get_return = HANDLE + 1024
            self.assertEqual(h.reserve(), FAULT)
            before = h.state()
            self.assertEqual(before, (HANDLE, BYTES, 10, 7, BYTES, FAULT))
            self.assertEqual(h.release(), FAULT)
            self.assertEqual(h.state(), before)
            self.assertEqual(h.live, {HANDLE: 10})

    return unittest.defaultTestLoader.loadTestsFromTestCase(NativeBoundaryCases)


CASES = 40  # 18 shared scenarios x host/ARM + 4 boundary checks; no skips.


def run_worker():
    image = checked_image()
    with tempfile.TemporaryDirectory(prefix='fpl-raw-workspace-') as directory:
        text = compile_source(directory)
        host = compile_source(directory, host=True)
        mutants = {m: compile_source(directory, mutation=m) for m in ('user-pool', 'early-free')}
        host_mutants = {m: compile_source(directory, host=True, mutation=m) for m in mutants}
        suite = unittest.TestSuite([
            semantic_cases(lambda: HostWorkspace(host),
                           lambda m: HostWorkspace(host_mutants[m]), 'Host'),
            semantic_cases(lambda: ArmWorkspace(text, image),
                           lambda m: ArmWorkspace(mutants[m], image), 'Arm'),
            extra_cases(text, image, host),
        ])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.wasSuccessful() and not result.skipped and result.testsRun == CASES
    print(json.dumps({
        'kind': 'compiled_raw_workspace_host_and_original_arm_descriptor_wrappers',
        'passed': passed, 'tests_run': result.testsRun, 'skipped': len(result.skipped),
        'text_bytes': len(text.code), 'text_sha256': hashlib.sha256(text.code).hexdigest(),
        'firmware_sha256': IMAGE_SHA256,
        'source_hashes': {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                          for name in ('raw_workspace.c', 'raw_workspace.h', 'test_raw_workspace.py')},
        'native_instructions_executed': ['C001CFD8', 'C001D3D0'],
        'substitutes': ['memory mode', 'pool selection', 'physical allocation/free'],
        'new_wrapper_stack_alignment': 8, 'legacy_C001CFD8_nested_SP_mod8': 4,
        'camera_accessed': False, 'camera_capacity_proved': False,
        'producer_exclusion_proved': False, 'dma_drain_proved': False,
        'deployable': False,
    }, sort_keys=True))
    if passed:
        print(f'RAW WORKSPACE VERIFIED: {CASES} tests, 0 skipped')
    return 0 if passed else 1


class RawWorkspaceTests(unittest.TestCase):
    def test_host_and_actual_arm_in_child(self):
        optimized = ['-' + 'O' * sys.flags.optimize] if sys.flags.optimize else []
        child = subprocess.run([sys.executable, '-B', *optimized,
                                str(Path(__file__).resolve()), '--worker'],
                               capture_output=True, text=True, timeout=90)
        self.assertEqual(child.returncode, 0,
                         'workspace worker failed (negative exit means host JIT restriction); '
                         'ARM unverified.\n' + child.stdout + child.stderr)
        self.assertIn(f'RAW WORKSPACE VERIFIED: {CASES} tests, 0 skipped', child.stdout)
        print(child.stdout.strip())


if __name__ == '__main__':
    if sys.argv[1:] == ['--worker']:
        raise SystemExit(run_worker())
    unittest.main()
