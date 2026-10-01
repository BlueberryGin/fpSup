"""The TEST card's launcher and hook shims, executed from the built bytes.

The card is built by build_card.py into a temporary directory. The real
loader and stage2 from its AutoRun and fpSup.BIN run in Unicorn against the
pinned firmware image (fp_usb_shell/test_loader_hook.Camera), which places
every section and journals every declared site. Then the lossless launcher --
the bytes that will be on the card -- runs from the staging buffer, and every
hook is entered through its veneer exactly as the firmware's branch would
enter it.

What this proves: allocation, copy, publication order, all-or-nothing arming,
branch encodings, the veneers, and that each shim reaches its C body and
returns to (or continues into) the firmware with the registers and stack the
firmware expects. What it does not: real scheduling, the codec, the SD path,
or anything the stubs stand in for. Emulated, not on the camera.
"""
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SHELL = HERE.parent / 'fp_usb_shell'
sys.path.insert(0, str(SHELL))
sys.argv[1:] = [] if __name__ == '__main__' else sys.argv[1:]
import test_loader_hook as T                     # noqa: E402

from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,  # noqa: E402
                               UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR,
                               UC_ARM_REG_R4, UC_ARM_REG_R11)

SITES = {'rec': (0xC03A33C8, 0xEBFFFC1A), 'arrive': (0xC038BFF0, 0xE12FFF33),
         'stop': (0xC0398D88, 0xE92D49F0), 'flush': (0xC03A5490, 0xEB0BD652)}
CAVE_BUMP, CAVE_ARENA, CAVE_END = 0xC072E060, 0xC072E064, 0xC072EFB4
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_card  # noqa: E402
CARD_BLOCK = build_card.BLOCK_BYTES     # the launcher's USER block, as built
BLOCK = 0x45300000                    # where our allocation lands in emulation:
                                      # clear of the staging buffer the harness
                                      # hands the loader lower in the same heap
ORIGINAL = {'prepare': 0xC03A2438, 'enqueue': 0xC037DD50, 'flush': 0xC069ADE0,
            'stop_resume': 0xC0398D8C, 'tk_cre_tsk': 0xC0016A58, 'tk_sta_tsk': 0xC0016BC0}
TASK_ID = 0x5A


def build_card():
    out = pathlib.Path(tempfile.mkdtemp(prefix='fpl-card-emu-')) / 'card'
    subprocess.run([sys.executable, '-B', str(HERE / 'build_card.py'), '--out', str(out)],
                   check=True, capture_output=True, text=True, timeout=300)
    text = (out / 'AutoRun.txt').read_text()
    import re
    sets = [(int(a, 16), int(v, 16)) for a, v in
            re.findall(r'^mem set (0x[0-9A-Fa-f]+) (0x[0-9A-Fa-f]+)', text, re.M)]
    loader = {a: v for a, v in sets if T.CAVE_LOW <= a < T.CAVE_LOW + 0x200}
    return loader, (out / 'fpSup.BIN').read_bytes(), (out / 'lossless.bin').read_bytes()


class CardCamera(T.Camera):
    """The shared harness, with our allocation given its own block and the
    firmware functions our shims continue into recorded and returned."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        assert T.HEAP <= BLOCK and BLOCK + CARD_BLOCK <= T.HEAP + T.HEAP_SIZE
        self.h_get = []
        self.originals = []
        self.launching = False        # only the launcher's own request is ours
        for name, at in ORIGINAL.items():
            self.by_addr[at] = 'ORIG_' + name
        self.returns = {'ORIG_prepare': 7, 'ORIG_enqueue': 1, 'ORIG_flush': 0,
                        'ORIG_tk_cre_tsk': TASK_ID, 'ORIG_tk_sta_tsk': 0}
        self.task_descriptors = []

    def _hook(self, mu, addr, size, _):
        name = self.by_addr.get(addr)
        if name == 'H_GET':
            self.h_get.append(tuple(self.r(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1,
                                                        UC_ARM_REG_R2, UC_ARM_REG_R3)))
        if name == 'H_ADDR' and self.launching and self.h_get and self.h_get[-1][2] == CARD_BLOCK:
            self.calls.append(name)
            return self._ret(BLOCK)
        if name and name.startswith('ORIG_'):
            self.calls.append(name)
            self.originals.append((name, tuple(self.r(r) for r in (
                UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)),
                self.r(UC_ARM_REG_SP), self.r(UC_ARM_REG_LR)))
            if name == 'ORIG_tk_cre_tsk':
                d = self.r(UC_ARM_REG_R0)
                self.task_descriptors.append(struct.unpack('<8I', bytes(mu.mem_read(d, 32))))
            if name == 'ORIG_stop_resume':
                mu.emu_stop()                 # mid-function: look, do not run on
                return
            return self._ret(self.returns[name])
        return super()._hook(mu, addr, size, _)


class CardEmulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader, cls.bin, cls.blob = build_card()

    def boot(self, mutate_site=None):
        cam = CardCamera(self.loader, self.bin)
        cam.mu.mem_write(T.SITE, struct.pack('<I', 0xEB000000 | ((((T.CAVE_LOW + 4)
                         - T.SITE - 8) >> 2) & 0xFFFFFF)))
        cam.mu.mem_write(CAVE_BUMP, struct.pack('<I', CAVE_ARENA))
        cam.call(T.CAVE_LOW + 4, r0=0xC0BABDD8, r1=1, lr=T.AR_RET, sp=T.STACK - 0x1004)
        # AFTER stage2: it writes every declared site back to its stock word,
        # which is the journal's repair and would undo an earlier change.
        if mutate_site:
            cam.mu.mem_write(SITES[mutate_site][0], struct.pack('<I', 0xE1A00000))
        self.assertEqual(cam.calls.count('ENTRY'), 1, 'stage2 never reached the entry')
        # our section: destination zero, byte-identical to lossless.bin
        staging = cam.entry - struct.unpack_from('<I', self.bin, 8)[0]
        n = struct.unpack_from('<I', self.bin, 4)[0]
        offset, found = 16 + 8 * n, None
        for i in range(n):
            dest, length = struct.unpack_from('<II', self.bin, 16 + 8 * i)
            if dest == 0 and self.bin[offset:offset + length] == self.blob:
                found = staging + offset
            offset += length + (-length % 4)
        self.assertIsNotNone(found, 'the launcher is not a section of fpSup.BIN')
        cam.launcher = found
        return cam

    def launch(self, cam):
        cam.calls.clear()
        cam.h_get.clear()
        cam.launching = True
        try:
            r0, sp = cam.call(cam.launcher, r0=cam.launcher, sp=T.STACK - 0x1000)
        finally:
            cam.launching = False
        self.assertEqual(sp, T.STACK - 0x1000, 'the launcher left the stack moved')
        return r0

    def cave_block(self, cam):
        return cam.word(CAVE_BUMP) - 48

    # ---- the launcher -------------------------------------------------
    def test_the_sites_are_journaled_by_stage2_before_the_entry(self):
        cam = self.boot()
        for name, (site, stock) in SITES.items():
            self.assertEqual(cam.word(site), stock, name)

    def test_it_allocates_its_own_user_block_copies_publishes_then_arms(self):
        cam = self.boot()
        self.assertEqual(self.launch(cam), 0)
        self.assertEqual(cam.h_get[-1][1:3], (0, CARD_BLOCK), 'not its own USER block')
        # identical but for the six words the launcher fills in the copy
        from armasm import symbols
        words = symbols(HERE / 'native' / 'card.S',
                        ['BLOB_LEN=4', 'BLOCK_BYTES=4', 'STATE_OFF=4'] +
                        [f'OFF_{k}=1' for k in ('INIT', 'REC', 'ARRIVE', 'STOP', 'FLUSH', 'TASK')])
        lo, hi = words['g_card'], words['stop_resume']
        self.assertEqual(hi - lo, 24)
        resident = bytes(cam.mu.mem_read(BLOCK, len(self.blob)))
        self.assertEqual(resident[:lo], self.blob[:lo])
        self.assertEqual(resident[hi:], self.blob[hi:])
        self.assertEqual(self.blob[lo:hi], b'\0' * 24)
        filled = struct.unpack('<6I', resident[lo:hi])
        self.assertTrue(all(filled), 'a resident word was left empty')
        calls = [c for c in cam.calls if c in ('H_GET', 'H_ADDR', 'DCACHE', 'ICACHE')]
        self.assertEqual(calls, ['H_GET', 'H_ADDR', 'DCACHE', 'ICACHE',
                                 'DCACHE', 'ICACHE', 'DCACHE', 'ICACHE'])

    def test_init_starts_the_codec_task_at_the_resident_task_shim(self):
        cam = self.boot()
        self.assertEqual(self.launch(cam), 0)
        from armasm import symbols
        words = symbols(HERE / 'native' / 'card.S',
                        ['BLOB_LEN=4', 'BLOCK_BYTES=4', 'STATE_OFF=4'] +
                        [f'OFF_{k}=1' for k in ('INIT', 'REC', 'ARRIVE', 'STOP', 'FLUSH', 'TASK')])
        self.assertEqual(len(cam.task_descriptors), 1)
        exinf, atr, entry, pri, stksz, n0, n1, tail = cam.task_descriptors[0]
        self.assertEqual((exinf, atr, pri, stksz, tail), (0, 0x41, 12, 0x2000, 0))
        self.assertEqual(struct.pack('<II', n0, n1), b'FPLCODE\0')
        self.assertEqual(entry, BLOCK + words['task_shim'], 'not the resident shim')
        starts = [o for o in cam.originals if o[0] == 'ORIG_tk_sta_tsk']
        self.assertEqual(starts[0][1][:2], (TASK_ID, 0))
        # the shim hands the task the card state and enters fpl_card_task
        resident = bytes(cam.mu.mem_read(BLOCK, len(self.blob)))
        g_card, w_task = struct.unpack_from('<I', resident, words['g_card'])[0], \
            struct.unpack_from('<I', resident, words['w_task'])[0]
        self.assertTrue(g_card and w_task)

    def test_every_site_branches_to_its_veneer_and_every_veneer_to_its_shim(self):
        cam = self.boot()
        self.launch(cam)
        cave = self.cave_block(cam)
        self.assertEqual(cave, CAVE_ARENA)
        for i, (name, (site, _)) in enumerate(SITES.items()):
            word = cam.word(site)
            op = 0xEA000000 if name == 'stop' else 0xEB000000
            self.assertEqual(word & 0xFF000000, op, name)
            disp = word & 0xFFFFFF
            disp = disp - 0x1000000 if disp & 0x800000 else disp
            self.assertEqual(site + 8 + 4 * disp, cave + 8 * i, name)
            self.assertEqual(cam.word(cave + 8 * i), 0xE51FF004, name)
            shim = cam.word(cave + 8 * i + 4)
            self.assertTrue(BLOCK <= shim < BLOCK + len(self.blob), f'{name} shim {shim:#x}')
        self.assertEqual(cam.word(cave + 32), 0x43504C46)
        self.assertEqual(cam.word(cave + 40), BLOCK)
        state = cam.word(cave + 36)
        self.assertEqual(cam.word(state), 0x44524143, 'card state not initialised')

    def test_a_site_that_is_not_stock_arms_nothing(self):
        for which in SITES:
            with self.subTest(site=which):
                cam = self.boot(mutate_site=which)
                self.launch(cam)
                for name, (site, stock) in SITES.items():
                    expect = 0xE1A00000 if name == which else stock
                    self.assertEqual(cam.word(site), expect, f'{name} armed anyway')
                self.assertEqual(cam.word(CAVE_BUMP), CAVE_ARENA, 'cave taken anyway')

    def test_a_full_cave_arms_nothing(self):
        cam = self.boot()
        cam.mu.mem_write(CAVE_BUMP, struct.pack('<I', CAVE_END - 40))
        self.launch(cam)
        for name, (site, stock) in SITES.items():
            self.assertEqual(cam.word(site), stock, name)

    # ---- through the veneers ------------------------------------------
    def enter(self, cam, name, regs, lr=T.DONE):
        site = SITES[name][0]
        word = cam.word(site)
        disp = word & 0xFFFFFF
        disp = disp - 0x1000000 if disp & 0x800000 else disp
        veneer = site + 8 + 4 * disp
        mu = cam.mu
        for reg, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), regs):
            mu.reg_write(reg, v)
        mu.reg_write(UC_ARM_REG_R4, 0x44444444)
        mu.reg_write(UC_ARM_REG_R11, 0xBBBBBBBB)
        mu.reg_write(UC_ARM_REG_SP, T.STACK - 0x2000)
        mu.reg_write(UC_ARM_REG_LR, lr)
        cam.originals.clear()
        mu.emu_start(veneer, T.DONE, count=2_000_000)
        return cam.r(UC_ARM_REG_R0)

    def armed(self):
        cam = self.boot()
        self.launch(cam)
        return cam

    def test_rec_passes_any_other_request_to_the_original(self):
        cam = self.armed()
        request = T.STACK - 0x3000
        cam.mu.mem_write(request, struct.pack('<I', 1))
        self.assertEqual(self.enter(cam, 'rec', (0x1234, request)), 7)
        (name, regs, _sp, lr), = cam.originals
        self.assertEqual((name, regs[:2]), ('ORIG_prepare', (0x1234, request)))
        self.assertEqual(lr, T.DONE, 'the firmware return address was lost')

    def test_arrive_calls_an_unknown_vtable_target_untouched(self):
        cam = self.armed()
        self.enter(cam, 'arrive', (0xC0DE, 5, 1, T.DONE))
        self.assertEqual(cam.originals, [])
        self.assertEqual((cam.r(UC_ARM_REG_R0), cam.r(UC_ARM_REG_R1)), (0xC0DE, 5))

    def test_arrive_without_a_take_is_the_original_enqueue(self):
        cam = self.armed()
        self.assertEqual(self.enter(cam, 'arrive', (0xC0DE, 5, 1, ORIGINAL['enqueue'])), 1)
        (name, regs, _sp, lr), = cam.originals
        self.assertEqual((name, regs[:3]), ('ORIG_enqueue', (0xC0DE, 5, 1)))
        self.assertEqual(lr, T.DONE)
        self.assertEqual(cam.r(UC_ARM_REG_R4), 0x44444444, 'r4 not preserved')
        self.assertEqual(cam.r(UC_ARM_REG_R11), 0xBBBBBBBB, 'r11 not preserved')

    def test_flush_keeps_every_argument_for_the_real_flush(self):
        cam = self.armed()
        writer = T.STACK - 0x3000
        cam.mu.mem_write(writer, b'\0' * 0x100)
        self.enter(cam, 'flush', (writer, 0x11, 0x22, 0x33))
        (name, regs, sp, lr), = cam.originals
        self.assertEqual(name, 'ORIG_flush')
        self.assertEqual(regs, (writer, 0x11, 0x22, 0x33))
        self.assertEqual((sp, lr), (T.STACK - 0x2000, T.DONE))
        self.assertEqual(cam.r(UC_ARM_REG_R4), 0x44444444)

    def test_stop_runs_the_displaced_push_and_continues_into_the_original(self):
        cam = self.armed()
        self.enter(cam, 'stop', (1, 0x77, 0, 0))
        (name, regs, sp, _lr), = cam.originals
        self.assertEqual((name, regs[:2]), ('ORIG_stop_resume', (1, 0x77)))
        # exactly push {r4-r8, fp, lr}: seven words, lr on top
        self.assertEqual(sp, T.STACK - 0x2000 - 28)
        pushed = struct.unpack('<7I', cam.mu.mem_read(sp, 28))
        self.assertEqual(pushed[0], 0x44444444)            # r4
        self.assertEqual(pushed[5], 0xBBBBBBBB)            # fp
        self.assertEqual(pushed[6], T.DONE)                # the caller's lr


if __name__ == '__main__':
    unittest.main()
