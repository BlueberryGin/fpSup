"""fpScreenFlip on a card, from the built bytes, in Unicorn (emulated, NOT the
camera; no card is written).

The card comes from build_card.py into a temporary directory. The real
loader and stage2 run against the pinned image (fp_usb_shell/test_loader_hook
.Camera), and this time the header entry is NOT stubbed: the loader's entry
trampoline (entries.S) runs the section from the staging buffer, exactly as
on the camera. The native UI objects the sections look for
are laid out in emulated memory as read from the camera (fp-native-ui §8a);
the four firmware services the launcher calls (variable lookup / register,
subscribe, relayout) are Python stand-ins that record their arguments.

Proven here: the entry chain reaches the section and comes back; the
launcher copies itself into its own USER block, registers MV_fpScreenFlip = 0
with the name in that block, switches Y2_5_1 to a copy byte-identical to the
reference applier's, and only then subscribes the RESIDENT callback (a
foreign firmware word or a refused block: no row, no subscription); the resident callback, executed, writes the panel
register / touch flag per value and asks for one relayout per change.
Not proven: the real UI parsing that page, the real registry, the panel.
"""
import pathlib
import struct
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
FPSUP = HERE.parent
sys.path.insert(0, str(FPSUP / 'fp_usb_shell'))
sys.path.insert(0, str(FPSUP / 'uishare'))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'menu'))
sys.argv[1:] = [] if __name__ == '__main__' else sys.argv[1:]
import test_loader_hook as T                     # noqa: E402
from ui import fpui                              # noqa: E402
import build_card as BC                          # noqa: E402
import build_flip_page as F                      # noqa: E402

GUI_OBJECT, UI_AREA = 0xC37B7048, 0xC3A00000
NBU_BASE = 0xC18C0460
ARENA, ARENA_SIZE = 0x48000000, 0x01000000      # every allocation: fresh memory
LOOKUP, REGISTER, SUBSCRIBE, RELAYOUT = 0xC05DB418, 0xC05DB308, 0xC0560FB0, 0xC02E4420
LAYER, TOUCH, TOUCH_VT_AT, TOUCH_VT = 0x30190044, 0xC3760E44, 0xC3760E48, 0xC0CBE37C
DESC = UI_AREA + 0x8000                         # descriptors the register stub hands out
WANT = {0: 0x000000, 1: 0x333000, 2: 0x001000, 3: 0x332000}
TOUCHED = {0: 0, 1: 1, 2: 0, 3: 1}
SETS = ['MainB2', 'Y2_5_1']
NAME = 24                                       # struct fpf_card name[]


def build(*flags):
    out = pathlib.Path(tempfile.mkdtemp(prefix='fpflip-emu-')) / 'card'
    BC.main(['--out', str(out), *flags])
    import re
    text = (out / 'AutoRun.txt').read_text()
    sets = [(int(a, 16), int(v, 16)) for a, v in
            re.findall(r'^mem set (0x[0-9A-Fa-f]+) (0x[0-9A-Fa-f]+)', text, re.M)]
    loader = {a: v for a, v in sets if T.CAVE_LOW <= a < T.CAVE_LOW + 0x200}
    return out, loader


class Camera(T.Camera):
    def __init__(self, loader, binary, touch_vt=TOUCH_VT):
        super().__init__(loader, binary)
        mu = self.mu
        mu.mem_map(ARENA, ARENA_SIZE)
        mu.mem_map(LAYER & ~0xFFF, 0x1000)
        self.next = ARENA
        self.h_get, self.lookups, self.regs, self.subs, self.relayouts = [], [], [], [], 0
        self.vars = {}                          # name -> descriptor
        self.allocs = []                        # (size asked by the last H_GET, address)
        self.publishes_at_sub = None
        w = lambda a, v: mu.mem_write(a, struct.pack('<I', v))
        self.w = w
        app, screens, screen, reader, entries, names, registry = (
            UI_AREA + o for o in (0, 0x1000, 0x2000, 0x3000, 0x4000, 0x6000, 0x7000))
        w(GUI_OBJECT, app); w(app + 0x80, 1); w(app + 0x8C, screens); w(screens, screen)
        w(screen + 0x24, reader); w(app + 0x888, registry)
        w(reader + 0x10, 176152); w(reader + 0x14, 0xC18C0474); w(reader + 0x24, NBU_BASE)
        w(reader + 0xA8, len(SETS)); w(reader + 0xAC, entries)
        sets = {'MainB2': 0x76FF04, 'Y2_5_1': F.ENTRY_OFFSET}
        for i, name in enumerate(SETS):
            mu.mem_write(names + 0x40 * i, name.encode() + b'\0')
            w(entries + 44 * i + 4, names + 0x40 * i)
            w(entries + 44 * i + 8, sets[name])
        self.entries, self.reader = entries, reader
        w(LAYER, 0x00000ABC)                    # other layers' bits: must survive
        w(TOUCH_VT_AT, touch_vt)

    def _hook(self, mu, addr, size, _):
        if self.entry is not None and addr == self.entry:
            self.calls.append('ENTRY')         # run it: the entry chain is under test
            self.entry = None
            return
        r = [self.r(x) for x in (T.UC_ARM_REG_R0, T.UC_ARM_REG_R1, T.UC_ARM_REG_R2, T.UC_ARM_REG_R3)]
        if addr == T.F['H_GET']:
            self.h_get.append(tuple(r))
        if addr == T.F['H_ADDR']:
            self.calls.append('H_ADDR')
            got = self.next
            self.next += 0x100000
            self.allocs.append((self.h_get[-1][2] if self.h_get else None, got))
            return self._ret(got)
        if addr == LOOKUP:
            name = self.cstr(r[1])
            self.lookups.append(name)
            mu.mem_write(r[2], struct.pack('<I', self.vars.get(name, 0)))
            return self._ret(0)
        if addr == REGISTER:
            t, p, v = struct.unpack('<3I', mu.mem_read(r[2], 12))
            self.regs.append((r[0], r[1], t, p, v))
            d = DESC + 16 * len(self.vars)
            mu.mem_write(d, struct.pack('<3I', t, p, v))
            self.vars[self.cstr(p)] = d
            return self._ret(0)
        if addr == SUBSCRIBE:
            self.subs.append((r[0], r[1], r[2]))
            self.publishes_at_sub = self.calls.count('DCACHE')
            return self._ret(0)
        if addr == RELAYOUT:
            self.relayouts += 1
            return self._ret(0)
        return super()._hook(mu, addr, size, _)


@unittest.skipIf(T.Uc is None or not T.IMAGE.exists(), 'needs unicorn and the image')
class CardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The entry chain runs for real, so the card is the shell-less one: a
        # dev card's first entry is the USB shell worker, which needs the USB
        # stack. The dev card's layout is checked against its own reference.
        cls.out, cls.loader = build('--release')
        cls.binary = (cls.out / 'fpSup.BIN').read_bytes()
        cls.dev = (build()[0] / 'fpSup.BIN').read_bytes()
        cls.launcher, cls.block, cls.linfo = BC.build_blob()

    @staticmethod
    def sections(binary):
        n = struct.unpack_from('<I', binary, 4)[0]
        off, out = 16 + 8 * n, []
        for i in range(n):
            dest, length = struct.unpack_from('<II', binary, 16 + 8 * i)
            out.append((dest, binary[off:off + length]))
            off += length + (-length % 4)
        return out

    def boot(self, before=None, **kw):
        cam = Camera(self.loader, self.binary, **kw)
        if before:
            before(cam)
        cam.mu.mem_write(T.SITE, struct.pack('<I', 0xEB000000 | ((((T.CAVE_LOW + 4)
                         - T.SITE - 8) >> 2) & 0xFFFFFF)))
        cam.mu.mem_write(0xC072E060, struct.pack('<I', 0xC072E064))
        r0, sp = cam.call(T.CAVE_LOW + 4, r0=0xC0BABDD8, r1=1, lr=T.AR_RET, sp=T.STACK - 0x1004)
        self.assertEqual(cam.calls.count('ENTRY'), 1, 'stage2 never reached the entry')
        return cam

    def block_of(self, cam):
        """The launcher's block: the allocation that asked for BLOCK_BYTES."""
        got = [a for size, a in cam.allocs if size == self.linfo['block_bytes']]
        self.assertEqual(len(got), 1, 'the launcher did not allocate its block once')
        return got[0]

    def test_the_section_is_on_the_card_and_adds_no_fixed_one(self):
        import subprocess
        for flags, binary in (([], self.dev), (['--no-shell'], self.binary)):
            with self.subTest(flags=flags):
                zero = [data for dest, data in self.sections(binary) if dest == 0]
                self.assertEqual(zero.count(self.launcher), 1)
                # every fixed-address section is the shared build's own: the
                # same card without the product has exactly the same ones
                ref = pathlib.Path(tempfile.mkdtemp(prefix='fpflip-ref-'))
                subprocess.run([sys.executable, '-B', str(FPSUP / 'fp_usb_shell/build_autorun.py'),
                                '--loader', '--out', str(ref / 'AutoRun.txt'),
                                *(flags or ['--no-ep-patches'])], check=True, capture_output=True)
                base = (ref / 'fpSup.BIN').read_bytes()
                mine = {d: data for d, data in self.sections(binary) if d}
                theirs = sorted(d for d, _ in self.sections(base) if d)
                # but the three string sites, declared with their stock words
                # so stage2 journals them (uishare/NESTED_HOOKS.md)
                from ui import chain
                strings = {site: stock.to_bytes(4, 'little') for site, stock, _ in chain.SITES.values()}
                self.assertEqual({d: mine[d] for d in strings if d in mine}, strings)
                self.assertEqual(sorted(d for d in mine if d not in strings), theirs,
                                 'the product adds a fixed-address section')

    def test_boot_registers_subscribes_and_adds_the_row(self):
        cam = self.boot()
        # ---- the launcher ----------------------------------------------------
        base = self.block_of(cam)
        self.assertEqual([g[1] for g in cam.h_get if g[2] == self.linfo['block_bytes']], [0], 'not the USER class')
        self.assertEqual(bytes(cam.mu.mem_read(base, len(self.launcher))), self.launcher)
        state = base + self.linfo['state_off']
        self.assertEqual(cam.word(state + 8), 0x50494C46, 'no "FLIP" state')
        self.assertEqual(cam.word(state + 12), 1, 'result is not READY')
        self.assertEqual(cam.word(state + 16), 1, 'it did not register the variable')
        self.assertEqual(cam.cstr(state + NAME), 'MV_fpScreenFlip')
        self.assertEqual((cam.word(state), cam.word(state + 4)),
                         (base + self.linfo['ui_off'], self.linfo['ui_len']),
                         'the block it applied is not the resident copy of its own')
        self.assertEqual(cam.word(state + 44), 0, 'ui_apply did not return OK')
        self.assertEqual(cam.word(state + 56), 405, 'the row did not get y 405')
        self.assertEqual(len(cam.regs), 1)
        app, count, t, p, v = cam.regs[0]
        self.assertEqual((app, count, t, p, v), (UI_AREA, 1, 0, state + NAME, 0),
                         'not one integer 0 whose name lives in the resident block')
        self.assertEqual(len(cam.subs), 1)
        gui, name, fn = cam.subs[0]
        self.assertEqual((gui, name), (GUI_OBJECT, state + NAME))
        self.assertEqual(fn, base + self.linfo['c_base'] + self.linfo['functions']['fpf_changed'],
                         'the subscribed callback is not the resident one (Thumb)')
        # ---- the UI section ---------------------------------------------------
        y = SETS.index('Y2_5_1')
        entry = cam.word(cam.entries + 44 * y + 8)
        self.assertNotEqual(entry, F.ENTRY_OFFSET, 'Y2_5_1 was not switched')
        self.assertEqual(cam.word(cam.entries + 8), 0x76FF04, 'MainB2 was touched')
        page = (NBU_BASE + entry) & 0xFFFFFFFF
        self.assertEqual(cam.word((page & ~3) - 128), 0x47505346, 'no FSPG header')
        length = cam.word((page & ~3) - 128 + 12)
        stock = cam.stock[F.PAGE_START:F.PAGE_END]
        pages = {'Y2_5_1': fpui.PageCopy(stock, len(stock), 1)}
        pool = fpui.Strings(cam.stock[0x18C0474:0x18C0474 + 176152])
        fpui.apply(self.block, pages, pool)
        self.assertEqual(bytes(cam.mu.mem_read(page, length)), bytes(pages['Y2_5_1'].data),
                         'the composed page is not the reference applier\'s')
        from ui import chain
        self.assertEqual((cam.word(cam.reader + 0x14), cam.word(cam.reader + 0x10)),
                         (0xC18C0474, 176152), 'the stock pool was replaced')
        self.assertEqual(chain.layers(cam.word), pool.layers,
                         'the string layers differ from the reference')
        for base, texts in pool.layers:                 # through the firmware's own resolver
            for i, text in enumerate(texts):
                r0, _ = cam.call(0xC05E5B58 | 1, r0=cam.reader, r1=base + i, sp=T.STACK - 0x1000)
                self.assertEqual(cam.cstr(r0), text)
        self.assertEqual(bytes(cam.mu.mem_read(0xC0000000 + F.PAGE_START, len(stock))), stock,
                         'the firmware image of the page was written')

    def callback(self, cam, value):
        state = self.block_of(cam) + self.linfo['state_off']
        desc = UI_AREA + 0x9000
        cam.mu.mem_write(desc, struct.pack('<3I', 0, state + NAME, value))
        fn = cam.subs[0][2]
        r0, sp = cam.call(fn, r0=desc, r1=0, sp=T.STACK - 0x1000)
        self.assertEqual(sp, T.STACK - 0x1000, 'the callback left the stack moved')
        self.assertEqual(r0, 0)

    def test_the_resident_callback_drives_the_panel(self):
        cam = self.boot()
        for value in (1, 2, 3, 0, 3, 3, 9):
            with self.subTest(value=value):
                before = cam.relayouts
                old = cam.word(LAYER)
                self.callback(cam, value)
                want = WANT.get(value, 0)
                self.assertEqual(cam.word(LAYER), 0xABC | want, hex(cam.word(LAYER)))
                self.assertEqual(cam.word(TOUCH), TOUCHED.get(value, 0))
                self.assertEqual(cam.relayouts - before, 0 if old == 0xABC | want else 1)

    def test_an_unknown_touch_object_refuses_a_turned_ui(self):
        cam = self.boot(touch_vt=0x12345678)
        self.callback(cam, 2)
        self.assertEqual(cam.word(LAYER), 0xABC | WANT[2], 'mirror leaves the UI alone: allowed')
        for value in (1, 3):
            self.callback(cam, value)
            self.assertEqual(cam.word(LAYER), 0xABC | WANT[2], 'turned the UI without touch')
        self.assertEqual(cam.word(TOUCH), 0, 'the unknown touch object was written')
        self.callback(cam, 0)
        self.assertEqual(cam.word(LAYER), 0xABC)


    def test_an_already_registered_variable_is_not_registered_again(self):
        def pre(cam):
            name = UI_AREA + 0x7800
            cam.mu.mem_write(name, b'MV_fpScreenFlip\0')
            cam.mu.mem_write(DESC + 0x100, struct.pack('<3I', 0, name, 2))
            cam.vars['MV_fpScreenFlip'] = DESC + 0x100
        cam = self.boot(before=pre)
        state = self.block_of(cam) + self.linfo['state_off']
        self.assertEqual(cam.regs, [])
        self.assertEqual((cam.word(state + 12), cam.word(state + 16)), (1, 0))
        self.assertEqual(len(cam.subs), 1)

    def test_a_foreign_firmware_word_stands_down(self):
        cam = self.boot(before=lambda cam: cam.w(SUBSCRIBE, 0xE12FFF1E))
        state = self.block_of(cam) + self.linfo['state_off']
        self.assertEqual(cam.word(state + 12), 5, 'result is not FIRMWARE')
        self.assertEqual((cam.regs, cam.subs, cam.lookups), ([], [], []))
        y = SETS.index('Y2_5_1')
        self.assertEqual(cam.word(cam.entries + 44 * y + 8), F.ENTRY_OFFSET,
                         'a row without its variable was added')

    def test_a_refused_block_leaves_no_subscription(self):
        # Y2_5_1 is not the stock page (another tool replaced it): GUARD fails
        def foreign(cam):
            y = SETS.index('Y2_5_1')
            cam.w(cam.entries + 44 * y + 8, 0x76FF04)
        cam = self.boot(before=foreign)
        state = self.block_of(cam) + self.linfo['state_off']
        self.assertEqual(cam.word(state + 12), 7, 'result is not UI')
        self.assertNotEqual(cam.word(state + 44), 0)
        self.assertEqual(cam.subs, [], 'subscribed although the row is not there')


if __name__ == '__main__':
    unittest.main()
