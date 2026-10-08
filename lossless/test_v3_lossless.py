"""fpLossless as a Loader v3 sup, loaded by the real 加載器.

    cd fpSup/lossless && python3 -B -m unittest test_v3_lossless

The real loader.S (as the AutoRun spells it), the real LOADER.BIN and the
built 10LOSS.BIN run in Unicorn against the reference image; the firmware's
file, directory, allocator, power-off and task routines are Python
(fp_usb_shell/v3/test_v3.Camera). The shims themselves are covered by
test_card_emulation.py on the old launcher -- they are the same bytes; this
covers what V3=1 changes: claim before anything, the layers below, the cave
from the service table, release with nothing written, and power-off.

Emulated, not on the camera. The row is not installed here (no GUI object in
the image's RAM), so the menu path stops at its first check.
"""
import pathlib
import struct
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
V3 = HERE.parent / 'fp_usb_shell' / 'v3'
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(V3))
sys.path.insert(0, str(V3.parent))

import test_v3 as T                                       # noqa: E402
import build_v3 as B                                      # noqa: E402
import build_v3_lossless as L                             # noqa: E402
from build_card import SITES                              # noqa: E402

from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,  # noqa: E402
                               UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR)

REC, ARRIVE, STOP, FLUSH = 0xC03A33C8, 0xC038BFF0, 0xC0398D88, 0xC03A5490
PLAY, CLIP, END, POOL = 0xC05C0EA4, 0xC05BDDAC, 0xC05C2E90, 0xC05C2D10
EVENT, DISCARD = 0xC038BD08, 0xC037DEF8
ENGINE = 0x300D0000
VENEER_AT = {REC: 0, ARRIVE: 8, STOP: 16, FLUSH: 24, PLAY: 48, CLIP: 56,
             END: 64, POOL: 72, EVENT: 80, DISCARD: 88}
B_FORM = {STOP, END, POOL, EVENT}                         # b: the caller's lr survives
RESUME = {STOP: 'stop_resume', END: 'end_resume', POOL: 'pool_resume',
          EVENT: 'event_resume', PLAY: 'play_next'}
REPLAY = {STOP: 'stop_replay', END: 'end_replay', POOL: 'pool_replay',
          EVENT: 'event_replay', PLAY: 'play_replay'}
LOWER = 0xC0100000                 # a layer below's target: inside branch reach


def branch(site, target, op):
    return op | (((target - site - 8) >> 2) & 0xFFFFFF)


def target_of(site, word):
    d = word & 0xFFFFFF
    d -= 0x1000000 if d & 0x800000 else 0
    return site + 8 + 4 * d


class LosslessCamera(T.Camera):
    """test_v3's camera, with the firmware routines lossless reaches at load."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for at, name in ((0xC000EC14, 'IRQ_OFF'), (0xC000EC24, 'IRQ_ON')):
            self.mu.mem_write(at, struct.pack('<I', 0xE12FFF1E))
            self.by_addr[at] = name


@unittest.skipIf(T.Uc is None or not T.IMAGE.exists(), 'needs unicorn and the image')
class LosslessV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix='fpl-v3-test-') as t:
            cls.sup, cls.facts = L.sup_file(pathlib.Path(t))
        from armasm import symbols
        cls.sym = symbols(HERE / 'native' / 'card.S', cls.facts['defines'])
        cls.img = T.IMAGE.read_bytes()

    def boot(self, *inner, sup=None, stale=None):
        files, dirs = T.card(*inner, ('10LOSS.BIN', sup or self.sup))
        cam = LosslessCamera(files, dirs, stale=stale)
        r0, sp = cam.boot()
        self.assertEqual(sp, T.STACK - 0x1004)
        self.assertEqual(cam.misaligned, [])
        return cam

    def outcome(self, cam, name='10LOSS.BIN'):
        return {t: c for c, _, _, t in cam.log() if c in ('LOADED', 'RELEASED')}.get(name)

    def block(self, cam):
        return next(a for c, a, _, t in cam.log() if c == 'LOADED' and t == '10LOSS.BIN')

    def blob(self, cam):
        return self.block(cam) + B.SL_HEADER_LEN            # card.S `entry`

    def stock(self, a):
        return T.word(self.img, a)

    def other(self, sid, *rows):
        """A sup loaded before lossless: rows (address, kind, new word or 0)."""
        return T.test_sup(sid, [(a, self.stock(a), k, new) for a, k, new in rows])

    # ---- loaded ---------------------------------------------------------
    def test_loads_claims_every_site_and_the_engine_then_arms(self):
        cam = self.boot()
        self.assertEqual(self.outcome(cam), 'LOADED')
        self.assertEqual(sorted(a for a, n, _ in cam.journal()), sorted(SITES))
        for a, n, data in cam.journal():
            self.assertEqual((n, data), (4, struct.pack('<I', SITES[a][0])), hex(a))
        st = cam.state()
        claims = [struct.unpack('<IIBBBB', cam.mu.mem_read(st + T.O_CLAIMS + 12 * i, 12))
                  for i in range(cam.w(st + T.O_NCLAIMS))]
        self.assertIn((ENGINE, 0, 2, 0, 1, 0), claims)         # claim_res, EXCL
        kinds = {a: k for a, n, k, _, res, _ in claims if not res}
        self.assertEqual(kinds, {a: L.WANT_KIND.get(a, 1) for a in SITES})
        cave = cam.w(T.cave.CAVE_BUMP) - 96                      # card.S CAVE_BYTES
        self.assertEqual(cave, (T.cave.CAVE_ARENA + 7) & ~7)     # cave_alloc: 8-aligned
        base, blob = self.block(cam), self.blob(cam)
        for site, off in VENEER_AT.items():
            word = cam.w(site)
            op = 0xEA000000 if site in B_FORM else 0xEB000000
            self.assertEqual(word & 0xFF000000, op, hex(site))
            self.assertEqual(target_of(site, word), cave + off, hex(site))
            self.assertEqual(cam.w(cave + off), 0xE51FF004)
            shim = cam.w(cave + off + 4)
            self.assertTrue(blob <= shim < base + B.SL_HEADER_LEN + self.facts['c_base'],
                            f'{site:#x} shim {shim:#x}')
        self.assertEqual(cam.w(cave + 32), 0x43504C46)          # "FLPC"
        self.assertEqual(cam.w(cave + 40), blob)
        state = cam.w(cave + 36)
        self.assertEqual(state, blob + self.facts['state_off'])
        self.assertEqual(cam.w(state), 0x44524143)              # "CARD": init ran
        self.assertEqual(cam.tasks, [blob + self.sym['task_shim']])
        # the call sites call what was below them: the firmware
        self.assertEqual(cam.w(blob + self.sym['flush_real']), 0xC069ADE0)
        self.assertEqual(cam.w(blob + self.sym['discard_real']), 0xC037E008)

    def test_power_off_puts_every_site_back(self):
        cam = self.boot()
        cam.power_off()
        for a, (stock, _) in SITES.items():
            self.assertEqual(cam.w(a), stock, hex(a))

    def test_a_patch_left_by_last_boot_is_repaired_then_armed(self):
        stale = {REC: 0xEB0DEAD0, STOP: 0xEA0DEAD0, CLIP: 0xE1A00000}
        cam = self.boot(stale=stale)
        self.assertEqual(self.outcome(cam), 'LOADED')
        repaired = {a for c, a, _, t in cam.log() if c == 'REPAIRED'}
        self.assertEqual(repaired, set(stale))
        cam.power_off()
        for a in stale:
            self.assertEqual(cam.w(a), SITES[a][0])

    # ---- released: nothing written ---------------------------------------
    def assert_released_clean(self, cam, held=()):
        self.assertEqual(self.outcome(cam), 'RELEASED')
        self.assertEqual(cam.tasks, [], 'a task was started before the decision')
        self.assertEqual(cam.w(T.cave.CAVE_BUMP), T.cave.CAVE_ARENA, 'cave taken')
        for a, (stock, _) in SITES.items():
            if a not in held:
                self.assertEqual(cam.w(a), stock, f'{a:#x} written')
        st = cam.state()
        self.assertEqual(sorted(j[0] for j in cam.journal()), sorted(held))
        self.assertFalse(any(cam.w(st + T.O_CLAIMS + 12 * i) == ENGINE
                             for i in range(cam.w(st + T.O_NCLAIMS))))

    def test_clip_size_is_exclusive(self):
        o = self.other('OTHR', (CLIP, 1, 0))                    # even a CHAIN claim
        cam = self.boot(('05OTHER.BIN', o))
        self.assert_released_clean(cam, held=(CLIP,))

    def test_arrive_is_exclusive(self):
        o = self.other('OTHR', (ARRIVE, 1, 0))
        cam = self.boot(('05OTHER.BIN', o))
        self.assert_released_clean(cam, held=(ARRIVE,))

    def test_an_exclusive_hold_on_a_chain_site_releases(self):
        o = self.other('OTHR', (FLUSH, 2, 0))
        cam = self.boot(('05OTHER.BIN', o))
        self.assert_released_clean(cam, held=(FLUSH,))

    def test_the_engine_held_by_another_releases(self):
        o = B.patch_sup('ENGX', [(ENGINE, None, 2, 'res')])
        cam = self.boot(('05ENGINE.BIN', o))
        self.assertEqual(self.outcome(cam, '05ENGINE.BIN'), 'LOADED')
        self.assertEqual(self.outcome(cam), 'RELEASED')
        self.assertEqual(cam.tasks, [])
        for a, (stock, _) in SITES.items():
            self.assertEqual(cam.w(a), stock, hex(a))
        self.assertEqual(cam.journal(), [])

    def test_a_shared_ui_site_held_exclusively_releases(self):
        o = self.other('OTHR', (0xC05E5B58, 2, 0))
        cam = self.boot(('05OTHER.BIN', o))
        self.assert_released_clean(cam, held=(0xC05E5B58,))

    def test_a_full_cave_releases(self):
        n = T.cave.CAVE_ARENA_END - T.cave.CAVE_ARENA - 64
        hog = self.cave_hog(n)
        cam = self.boot(('05HOG.BIN', hog))
        self.assertEqual(self.outcome(cam, '05HOG.BIN'), 'LOADED')
        self.assertEqual(self.outcome(cam), 'RELEASED')
        self.assertEqual(cam.tasks, [])
        for a, (stock, _) in SITES.items():
            self.assertEqual(cam.w(a), stock, hex(a))

    def cave_hog(self, n):
        """A sup whose entry only asks svc->cave_alloc for n bytes."""
        import armasm
        src = pathlib.Path(tempfile.mkdtemp(prefix='hog-')) / 'hog.S'
        src.write_text(f'''.syntax unified
.arm
.text
entry:
    push {{r4, lr}}
    mov r0, r1
    movw r1, #:lower16:{n}
    movt r1, #:upper16:{n}
    ldr ip, [r0, #20]
    blx ip
    mov r0, #0
    pop {{r4, pc}}
''')
        return B.make_sup(armasm.assemble(src), B.SL_HEADER_LEN, 'HOGG')

    # ---- the layers below ------------------------------------------------
    def test_rec_with_a_layer_below_releases(self):
        o = self.other('OTHR', (REC, 1, branch(REC, LOWER, 0xEB000000)))
        cam = self.boot(('05OTHER.BIN', o))
        self.assertEqual(self.outcome(cam), 'RELEASED')
        self.assertEqual(cam.tasks, [])
        self.assertEqual(cam.w(REC), branch(REC, LOWER, 0xEB000000))   # the other's

    def test_call_sites_call_the_layer_below(self):
        o = self.other('OTHR', (FLUSH, 1, branch(FLUSH, LOWER, 0xEB000000)),
                       (DISCARD, 1, branch(DISCARD, LOWER + 0x40, 0xEB000000)))
        cam = self.boot(('05OTHER.BIN', o))
        self.assertEqual(self.outcome(cam), 'LOADED')
        blob = self.blob(cam)
        self.assertEqual(cam.w(blob + self.sym['flush_real']), LOWER)
        self.assertEqual(cam.w(blob + self.sym['discard_real']), LOWER + 0x40)
        cam.power_off()
        self.assertEqual(cam.w(FLUSH), SITES[FLUSH][0])

    def test_insertion_sites_end_in_the_layer_below(self):
        for site in RESUME:
            with self.subTest(site=hex(site)):
                op = 0xEA000000 if site in B_FORM else 0xEB000000
                o = self.other('OTHR', (site, 1, branch(site, LOWER, op)))
                cam = self.boot(('05OTHER.BIN', o))
                self.assertEqual(self.outcome(cam), 'LOADED')
                blob = self.blob(cam)
                self.assertEqual(cam.w(blob + self.sym[RESUME[site]]), LOWER)
                at = blob + self.sym[REPLAY[site]]
                self.assertEqual(cam.w(at) & 0xFFFFF000, 0xE59FF000)      # ldr pc, [pc, #]
                self.assertEqual(at + 8 + (cam.w(at) & 0xFFF), blob + self.sym[RESUME[site]])

    def test_stock_insertion_sites_keep_their_replay(self):
        cam = self.boot()
        blob = self.blob(cam)
        for site in RESUME:
            at = blob + self.sym[REPLAY[site]]
            self.assertEqual(cam.w(at), self.sup_word(REPLAY[site]), hex(site))

    def sup_word(self, label):
        off = B.SL_HEADER_LEN + self.sym[label]
        return struct.unpack_from('<I', self.sup, off)[0]

    def test_a_layer_below_in_the_other_form_releases(self):
        o = self.other('OTHR', (STOP, 1, branch(STOP, LOWER, 0xEB000000)))  # bl, ours is b
        cam = self.boot(('05OTHER.BIN', o))
        self.assertEqual(self.outcome(cam), 'RELEASED')
        self.assertEqual(cam.tasks, [])

    def test_stop_runs_ours_then_the_layer_below_with_the_callers_registers(self):
        o = self.other('OTHR', (STOP, 1, branch(STOP, LOWER, 0xEA000000)))
        cam = self.boot(('05OTHER.BIN', o))
        mu = cam.mu
        for reg, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3),
                          (9, 0x11, 0x22, 0x33)):
            mu.reg_write(reg, v)
        sp = T.STACK - 0x2000
        mu.reg_write(UC_ARM_REG_SP, sp)
        mu.reg_write(UC_ARM_REG_LR, T.DONE)
        mu.emu_start(STOP, LOWER, count=2_000_000)
        self.assertEqual(cam.r(UC_ARM_REG_SP), sp)
        self.assertEqual(cam.r(UC_ARM_REG_LR), T.DONE)
        self.assertEqual([cam.r(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                             UC_ARM_REG_R3)], [9, 0x11, 0x22, 0x33])

    def test_outer_layer_can_chain_on_ours(self):
        """A sup after lossless claiming CHAIN on a CHAIN site is granted; the
        site then goes to its veneer and ours is what it calls next."""
        o = self.other('OUTR', (FLUSH, 1, 0))
        files, dirs = T.card(('10LOSS.BIN', self.sup), ('20OUTER.BIN', o))
        cam = LosslessCamera(files, dirs)
        cam.boot()
        self.assertEqual(self.outcome(cam), 'LOADED')
        self.assertEqual(self.outcome(cam, '20OUTER.BIN'), 'LOADED')

    # ---- the saved ON/OFF state, in this sup's own file --------------------
    def state_word(self, cam, field):
        state = self.blob(cam) + self.facts['state_off']
        return cam.w(state + self.facts['layout'][field])

    def with_record(self, on, check=None):
        sup = bytearray(self.sup)
        at = B.SL_HEADER_LEN + self.sym['settings']
        magic = 0x54534C46
        chk = magic ^ 1 ^ on ^ 0xFFFFFFFF if check is None else check
        struct.pack_into('<4I', sup, at, magic, 1, on, chk)
        return bytes(sup)

    def test_the_record_is_built_off_and_found(self):
        cam = self.boot()
        at = B.SL_HEADER_LEN + self.sym['settings']
        self.assertEqual(struct.unpack_from('<4I', self.sup, at),
                         (0x54534C46, 1, 0, 0x54534C46 ^ 1 ^ 0xFFFFFFFF))
        self.assertEqual(self.state_word(cam, 'settings_ok'), 1)
        self.assertEqual(self.state_word(cam, 'settings_on'), 0)

    def test_a_saved_on_is_read_at_boot(self):
        cam = self.boot(sup=self.with_record(1))
        self.assertEqual(self.outcome(cam), 'LOADED')
        self.assertEqual(self.state_word(cam, 'settings_ok'), 1)
        self.assertEqual(self.state_word(cam, 'settings_on'), 1)

    def test_a_damaged_record_is_off_and_never_saved(self):
        cam = self.boot(sup=self.with_record(1, check=0x12345678))
        self.assertEqual(self.outcome(cam), 'LOADED')
        self.assertEqual(self.state_word(cam, 'settings_ok'), 0)
        self.assertEqual(self.state_word(cam, 'settings_on'), 0)

    def test_it_needs_service_version_2(self):
        self.assertEqual(struct.unpack_from('<I', self.sup, 28)[0], 2)      # min_svc

    # ---- mutation: the tests above can fail ------------------------------
    def mutate_kind(self, site, kind):
        sup = bytearray(self.sup)
        at = B.SL_HEADER_LEN + self.sym['claims'] + 4
        for i in range(L.SITE_COUNT):
            a, s, k = struct.unpack_from('<3I', sup, at + 12 * i)
            if a == site:
                struct.pack_into('<I', sup, at + 12 * i + 8, kind)
                return bytes(sup)
        raise AssertionError('site not in claims')

    def test_mutation_clip_as_chain_is_caught(self):
        o = self.other('OTHR', (CLIP, 1, 0))
        cam = self.boot(('05OTHER.BIN', o), sup=self.mutate_kind(CLIP, 1))
        self.assertEqual(self.outcome(cam), 'LOADED')              # the guard is the kind

    def test_mutation_skipping_the_engine_claim_is_caught(self):
        sup = bytearray(self.sup)
        # `LDA r1, ENGINE`: movw r1, #0 ; movt r1, #0x300D -> make the id 0x300E0000
        movt = 0xE3401000 | ((0x300D & 0xF000) << 4) | (0x300D & 0x0FFF)
        at = sup.find(struct.pack('<I', movt))
        self.assertGreater(at, 0)
        struct.pack_into('<I', sup, at, movt + 1)
        o = B.patch_sup('ENGX', [(ENGINE, None, 2, 'res')])
        cam = self.boot(('05ENGINE.BIN', o), sup=bytes(sup))
        self.assertEqual(self.outcome(cam), 'LOADED')


if __name__ == '__main__':
    unittest.main()
