#!/usr/bin/env python3
"""The Quick Set record layer (qs_layer.S + qs_apply.c + qs_layer.c, the bytes
qs_build.layer_blob() gives a sup) run in unicorn over the firmware image.

    python3 -B -m unittest test_qs_layer

A model Loader v3 (svc: self, holder, cave_alloc, publish) hangs layers on
C05E6400 one after another, as sup entries would. Then, for every record of
the recipe, the firmware's call is replayed: C05E6400 with a reader at the
stock NBU base and the record's position. At C05E6404 (where every layer's
replay of the displaced push.w lands) the test takes the bytes the stock
parser would read, moves the position past them as the parser does, and
returns. Checked:

  - what the stock parser gets == ui/qs.py chain() for those layers (outer
    first), every record, N = 3, 4, 8, layers in several orders;
  - after the call the reader is back at the stock base, the position past
    the STOCK record (what the firmware expects);
  - a record not in the recipe goes through untouched, no copy;
  - a copy whose name the books do not list is not trusted (passes through);
  - qs_check refuses an unsupported N and a site holding foreign code;
  - two sups, either load order: same bytes for every record (QS_SHARE §2).
"""
import pathlib
import struct
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE   # noqa: E402
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,  # noqa: E402
                               UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R11, UC_ARM_REG_SP,
                               UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_CPSR)
from ui import qs                                  # noqa: E402
import qs_build                                    # noqa: E402

IMG, IMG_SIZE = 0xC0000000, 0x03000000            # up to C3000000: NBU and code
HEAP, HEAP_SIZE = 0x40000000, 0x00800000
STACK, STACK_SIZE = 0x50000000, 0x00100000
RET = 0x60000000
SP_SKEW = 0                                         # test_stack_alignment sets 4
RAM, RAM_SIZE = 0xC3000000, 0x01000000           # the firmware's RAM objects (model)
SVC = HEAP + 0x700000
READER = HEAP + 0x710000
FV = HEAP + 0x720000
CAVE_BUMP = 0xC072E060
SITE, SITE_STOCK = 0xC05E6400, 0x4FF0E92D
PARSE = 0xC05E6404                                 # where the replay lands (Thumb)
LAY_SITE, LAY_STOCK, LAY_PARSE = 0xC05D90F0, 0x4CF0E92D, 0xC05D90F4
LOAD_SITE, LOAD_STOCK, LOAD_BODY = 0xC05E84D8, 0xB086B500, 0xC05E84DC
LAYOUT_FIND, RCACHE_V3 = 0xC05E04E8, 0xC0566498
RCACHE, RCACHE_NAMES = 0xC37B7254, 0xC37B7288
RES_VALUE = 0xC31B3A4C                    # the setting in the working bank (bank select byte 0)
RES_CACHE = 0xC31ACC30                    # the property object's lagging cache
STOCK_NBR = 0xC0D22400
CONV, CONV_MOVW, CONV_MOVT, CONV_COUNT = 0xC06BDA30, 0xC06BDA3C, 0xC06BDA40, 0xC06BDA48
FV_SITE, RES_CSV = 0xC05E5BEC, 0xC0F8E7EC
THUMB_VENEER = 0xF000F8DF

class Books:
    """Loader v3's services as fp_usb_shell/v3/sloader.c has them (not an ideal
    model: d9, camera, 2026-10-07): claim (conflicts, SHARED_UI sharing, the
    REPAIR of a range's first claim -- stock bytes back if memory differs),
    claim_res, holder (load order; a memory claim overlapping [addr, addr+4) or
    a resource claim whose id is addr; only kept sups and the running one),
    self, cave_alloc, publish. `skip_res`: the camera's svc_holder before the
    fix, which ignored resource claims (the mutation)."""
    CLAIM, CLAIM_RES, CAVE, PUBLISH, HOLDER, SELF, LOG = range(7)
    OFFS = {CLAIM: 8, CLAIM_RES: 12, CAVE: 20, PUBLISH: 24, HOLDER: 32, SELF: 36, LOG: 28}

    def __init__(self, m):
        self.m, self.sups, self.claims, self.cur, self.running = m, [], [], None, False
        self.repaired, self.skip_res, self.bump = [], False, 0xC072E080
        self.paths = {}
        self.log = []

    # -- the loader's side
    def start(self, path):
        self.sups.append({'path': path, 'state': 'running'})
        self.cur, self.running = len(self.sups) - 1, True

    def finish(self, keep):
        sup = self.sups[self.cur]
        sup['state'] = 'keep' if keep else 'release'
        if not keep:
            self.claims = [c for c in self.claims if c['sup'] != self.cur]
        self.running = False

    def release_later(self, idx):
        """A sup the books no longer list (its claims gone, memory as it is)."""
        self.sups[idx]['state'] = 'release'
        self.claims = [c for c in self.claims if c['sup'] != idx]

    # -- the services
    def claim(self, addr, ln, stock, kind):
        if not ln or (addr | ln) & 3 or not 1 <= kind <= 3 or not stock:
            return -3
        shared = False
        for c in self.claims:
            if c['res'] or not (addr < c['addr'] + c['len'] and c['addr'] < addr + ln):
                continue
            if c['sup'] == self.cur and c['addr'] == addr and c['len'] == ln and c['kind'] == kind:
                return 0
            if kind != 2 and c['kind'] == kind and c['addr'] == addr and c['len'] == ln:
                shared = True
                continue
            return -1
        if not shared:
            want = bytes(self.m.mu.mem_read(stock, ln))
            if bytes(self.m.mu.mem_read(addr, ln)) != want:
                self.m.mu.mem_write(addr, want)
                self.repaired.append(addr)
        self.claims.append({'sup': self.cur, 'addr': addr, 'len': ln, 'kind': kind, 'res': False})
        return 0

    def claim_res(self, rid, kind):
        for c in self.claims:
            if not c['res'] or c['addr'] != rid:
                continue
            if c['sup'] == self.cur:
                return 0
            if kind != 2 and c['kind'] == kind:
                continue
            return -1
        self.claims.append({'sup': self.cur, 'addr': rid, 'len': 0, 'kind': kind, 'res': True})
        return 0

    def holder(self, addr, i):
        for k, sup in enumerate(self.sups):
            if sup['state'] != 'keep' and not (k == self.cur and self.running):
                continue
            for c in self.claims:
                if c['sup'] != k:
                    continue
                if c['res']:
                    if self.skip_res or c['addr'] != addr:
                        continue
                elif not (addr < c['addr'] + c['len'] and c['addr'] < addr + 4):
                    continue
                if i == 0:
                    return sup['path']
                i -= 1
                break
        return 0

    def self_(self):
        return self.sups[self.cur]['path'] if self.running else 0

    def cave(self, n):
        p, n = (self.bump + 7) & ~7, (n + 7) & ~7
        if not n or p + n > 0xC072EFB4:
            return 0
        self.bump = p + n
        return p

    def kept_names(self):
        out = []
        for sup in self.sups:
            if sup['state'] == 'keep':
                raw = bytes(self.m.mu.mem_read(sup['path'], 64)).split(b'\0')[0]
                out.append(raw.split(b'\\')[-1].split(b'.')[0])
        return out

    def hook(self, mu, addr, size, user):
        from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4
        which = (addr - STUBS) // 4
        r = [mu.reg_read(x) for x in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        if which == self.CLAIM:
            sp = mu.reg_read(UC_ARM_REG_SP)
            kind = struct.unpack('<I', bytes(mu.mem_read(sp, 4)))[0]      # the fifth argument
            v = self.claim(r[1], r[2], r[3], kind)
        elif which == self.CLAIM_RES:
            v = self.claim_res(r[1], r[2])
        elif which == self.CAVE:
            v = self.cave(r[1])
        elif which == self.PUBLISH:
            v = 0
        elif which == self.HOLDER:
            v = self.holder(r[1], r[2])
        elif which == self.LOG:
            self.log.append(bytes(mu.mem_read(r[1], 32)).split(b'\0')[0].decode())
            v = 0
        else:
            v = self.self_()
        mu.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF)


STUBS = SVC + 0x400                                    # seven `bx lr`, one per service


def bw_word(site, to):
    off = (to - (site + 4)) & 0xFFFFFFFF
    s = off >> 31
    i1, i2 = (off >> 23) & 1, (off >> 22) & 1
    j1, j2 = (1 ^ i1) ^ s, (1 ^ i2) ^ s
    hw1 = 0xF000 | s << 10 | ((off >> 12) & 0x3FF)
    hw2 = 0x9000 | j1 << 13 | j2 << 11 | ((off >> 1) & 0x7FF)
    return hw1 | hw2 << 16


class Machine:
    blob = sym = stubs = None

    def __init__(self, n_rows=None):
        if Machine.blob is None:
            Machine.blob, Machine.sym = qs_build.layer_blob()
        mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        mu.mem_map(IMG, IMG_SIZE)
        mu.mem_write(IMG, qs.image()[:IMG_SIZE])
        mu.mem_map(HEAP, HEAP_SIZE)
        mu.mem_map(STACK, STACK_SIZE)
        mu.mem_map(RET, 0x1000)
        mu.mem_map(RAM, RAM_SIZE)
        self.mu = mu
        w = lambda a, v: mu.mem_write(a, struct.pack('<I', v))   # noqa: E731
        self.w = w
        self.books = Books(self)
        mu.mem_write(STUBS, bytes.fromhex('1eff2fe1') * 7)           # bx lr
        w(SVC + 0, 3)
        for k, off in Books.OFFS.items():
            w(SVC + off, STUBS + 4 * k)
        mu.hook_add(UC_HOOK_CODE, self.books.hook, begin=STUBS, end=STUBS + 4 * 7 - 1)
        self.npaths = 0
        self.next_block = HEAP
        if n_rows is not None:
            self.fake_csv(n_rows)
        self.parsed = None
        self.copy_heads = []
        mu.hook_add(UC_HOOK_CODE, self._parse, begin=PARSE, end=PARSE + 1)
        self.loads, self.load_fail, self.layouts, self.released, self.finds = [], set(), [], [], {}
        self.unsafe = []
        mu.hook_add(UC_HOOK_CODE, self._load, begin=LOAD_BODY, end=LOAD_BODY + 1)
        mu.hook_add(UC_HOOK_CODE, self._layout, begin=LAY_PARSE, end=LAY_PARSE + 1)
        mu.hook_add(UC_HOOK_CODE, self._find, begin=LAYOUT_FIND, end=LAYOUT_FIND + 1)
        mu.hook_add(UC_HOOK_CODE, self._v3, begin=RCACHE_V3, end=RCACHE_V3 + 1)

    def word(self, a):
        return struct.unpack('<I', self.mu.mem_read(a, 4))[0]

    def fake_csv(self, rows):
        """The shared CSV copy fv_handler would serve, with `rows` data rows."""
        text = b'\xef\xbb\xbfNO,TEXT,Popup,IMAGE,Enabled,Enabled2\r\n' + b''.join(
            b'%d,x,Popup,blank,1,2\r\n' % (i + 1) for i in range(rows))
        head, table, copy, veneer = FV, FV + 0x100, FV + 0x200, 0xC072E070
        self.mu.mem_write(copy, text)
        for a, v in ((head, 0x4B485346), (head + 4, 1), (head + 8, table),
                     (table, 0x56465346), (table + 4, 1), (table + 8, 1), (table + 12, 4),
                     (table + 16, RES_CSV), (table + 20, copy), (table + 24, len(text)),
                     (veneer, THUMB_VENEER), (veneer + 4, head + 16)):
            self.w(a, v)
        self.w(FV_SITE, bw_word(FV_SITE, veneer))

    def call(self, fn, *args, thumb=True, count=2_000_000):
        mu = self.mu
        for r, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            mu.reg_write(r, v & 0xFFFFFFFF)
        mu.reg_write(UC_ARM_REG_SP, STACK + STACK_SIZE - 0x100 - SP_SKEW)
        mu.reg_write(UC_ARM_REG_LR, RET)
        mu.emu_start(fn | (1 if thumb else 0), RET, count=count)
        if mu.reg_read(UC_ARM_REG_PC) != RET:
            raise AssertionError('did not return: pc 0x%08X' % mu.reg_read(UC_ARM_REG_PC))
        return mu.reg_read(UC_ARM_REG_R0)

    def path(self, name):
        a = SVC + 0x800 + 0x40 * self.npaths
        self.npaths += 1
        self.mu.mem_write(a, b'\\fpSup\\' + name.encode() + b'.BIN\0')
        return a

    def add_layer(self, name, k, ids, enum=None, flags=0, pack=b'', build=None):
        """A sup entry: check, claim (the books), commit. Returns qs_check's result.
        `build`: (blob, sym) of another uishare build (default: today's)."""
        blob, sym = build or (Machine.blob, Machine.sym)
        block = self.next_block
        self.next_block += 0x10000
        self.mu.mem_write(block, blob)
        p = self.path(name)
        self.books.start(p)
        pack_at = 0
        if pack:
            pack_at = (block + len(blob) + 63) & ~63
            self.mu.mem_write(pack_at, pack)
        opt = SVC + 0x600
        vals = [block + sym['table'], k] + [ids[i] for i in range(4)] + \
            [100 + k if enum is None else enum, flags, pack_at, len(pack), 0]
        for i, v in enumerate(vals):
            self.w(opt + 4 * i, v)
        r = self.call(block + sym['qs_check'], block, SVC, opt)    # claims, then checks
        if r:
            self.books.finish(keep=False)
            return r
        self.call(block + sym['qs_hang'], block, SVC, opt)
        self.books.finish(keep=True)
        self.last_block = block
        return 0

    def _ret(self, mu, pops, r0):
        """The stock function's epilogue for what the replay pushed: pop
        {pops..., pc} (after add sp for the load's sub sp, #24)."""
        sp = mu.reg_read(UC_ARM_REG_SP)
        regs = struct.unpack('<%dI' % (len(pops) + 1), bytes(mu.mem_read(sp, 4 * (len(pops) + 1))))
        for r, v in zip(pops, regs):
            mu.reg_write(r, v)
        mu.reg_write(UC_ARM_REG_SP, sp + 4 * (len(pops) + 1))
        mu.reg_write(UC_ARM_REG_R0, r0)
        mu.reg_write(UC_ARM_REG_PC, regs[-1])

    def _load(self, mu, address, size, user):
        args = tuple(mu.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        self.loads.append(args)
        mu.reg_write(UC_ARM_REG_SP, mu.reg_read(UC_ARM_REG_SP) + 24)
        self._ret(mu, [], 1 if args[1] in self.load_fail else 0)

    def _layout(self, mu, address, size, user):
        from unicorn.arm_const import UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R10
        lay = mu.reg_read(UC_ARM_REG_R0)
        self.layouts.append(lay)
        if self.word(lay + 0xC) == 0:                            # the stock load re-parses
            self.w(lay + 0xC, self.new_tree(lay))
        self._ret(mu, [UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                       UC_ARM_REG_R10, UC_ARM_REG_R11], 0x7777)

    def _find(self, mu, address, size, user):
        mu.reg_write(UC_ARM_REG_R0, self.finds.get(mu.reg_read(UC_ARM_REG_R1), 0))
        mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))

    v3_frees = True
    loading = 0                                                  # the layout C05D90F0 is loading

    def _v3(self, mu, address, size, user):
        """UicResourceCache::v3, as the firmware has it (C0566498): r0 is the
        cache. If it holds a layout: unload it (C05D93A0 -> C05D91A0 -> loader
        +0x10 C05E83F0: tree refcount -1, at 1 the tree is freed and
        [layout+0xC] = 0), clear the cache's name and +4. Anything else in r0
        is the bug v5c had (a tree taken for the cache)."""
        this = mu.reg_read(UC_ARM_REG_R0)
        if this != RCACHE:
            self.unsafe.append(this)
        lay = self.word(RCACHE + 4)
        if this == RCACHE and lay and self.v3_frees:
            tree = self.word(lay + 0xC)
            if tree:
                self.released.append(tree)
                if lay == self.loading:
                    self.unsafe.append(tree)                     # its own tree, inside its own load
                n = self.word(tree + 0xC)
                self.w(tree + 0xC, n - 1)
                if n == 1:
                    self.w(lay + 0xC, 0)
            self.mu.mem_write(RCACHE + 8, bytes(0x20))
            self.w(RCACHE + 4, 0)
        elif this == RCACHE and lay:
            self.released.append(self.word(lay + 0xC))           # deferred: the cache lets go later
        mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))

    tree_next = RAM + 0x800000

    def new_tree(self, lay):
        t = Machine.tree_next
        Machine.tree_next += 0x40
        self.w(t + 0xC, 1)                                       # refcount: the cache's
        return t

    def layout_model(self):
        """Three QS layouts the cache knows by name; the cache holds the first."""
        ctx = RAM + 0x700000
        self.ctx = ctx
        self.lay_objs = []
        for i in range(3):
            lay = RAM + 0x710000 + 0x100 * i
            name = 0x9000 + i
            self.w(lay + 4, ctx)
            self.w(lay + 0xC, self.new_tree(lay))
            self.w(RCACHE_NAMES + 4 * i, name)
            self.finds[name] = lay
            self.lay_objs.append(lay)
        self.w(RCACHE + 4, self.lay_objs[0])
        self.w(ctx + 0x84C, RAM + 0x7F0000)                     # current screen: something else
        self.w(ctx + 0x848, RAM + 0x7F0100)
        self.other = RAM + 0x720000                              # a layout that is not QS
        self.w(self.other + 4, ctx)
        self.w(self.other + 0xC, self.new_tree(self.other))
        return self.lay_objs

    def load_layout(self, lay):
        """C05D90F0 loads `lay`. Releasing the tree of the layout being loaded,
        inside its own load, is what froze the camera (v5c, 2026-10-07):
        the stock load goes on with a freed tree."""
        self.loading = lay
        try:
            self.call(LAY_SITE, lay)
        finally:
            self.loading = 0
        if lay in getattr(self, 'lay_objs', ()) and self.word(RCACHE + 4) != lay:
            # shown: the cache (re)adopts it (C05650D8 on event 0x2601: unload
            # the old, load this one, +4 = it). Refcounts: the cache's load and
            # the screen's close cancel out, so not modelled.
            self.w(RCACHE + 4, lay)
        if self.unsafe:
            raise AssertionError('v3 misused (not the cache in r0, or the layout being loaded): %s'
                                 % ['0x%08X' % t for t in self.unsafe])

    def nbr_load(self, ctx, res, size=0x1000):
        return self.call(LOAD_SITE, ctx, res, size, 0)

    def _parse(self, mu, address, size, user):
        base, pos = self.word(READER + 36), self.word(READER + 4)
        rec = base + pos
        n = struct.unpack('>I', bytes(mu.mem_read(rec + 4, 4)))[0]
        self.parsed = (base, bytes(mu.mem_read(rec, n)))
        if base != 0xC18C0460:                    # a copy: its header names a listed maker
            head = bytes(mu.mem_read(rec - 16, 16))
            names = self.books.kept_names()
            self.copy_heads.append((head[:8].rstrip(b'\0') in names,
                                    struct.unpack('<II', head[8:]) == (2, 0xC18C0460)))
        self.w(READER + 4, pos + n)
        from unicorn.arm_const import (UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8,
                                       UC_ARM_REG_R9, UC_ARM_REG_R10)
        self._ret(mu, [UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8,
                       UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11], 0x5A5A)

    def parse(self, address, ctx=0x1C7C7C7C):
        self.w(READER + 8, ctx)
        self.w(READER + 36, 0xC18C0460)
        self.w(READER + 4, address - 0xC18C0460)
        self.parsed = None
        r = self.call(SITE, READER)
        return r, self.parsed, self.word(READER + 36), self.word(READER + 4)


def ids(k):
    return {0: 0x40000010, 1: 0x40000011, 2: 0x40000012, 3: 0x40000100 + k}


class TestLayer(unittest.TestCase):
    def run_scenario(self, n, ks):
        """ks in load order (first = innermost)."""
        m = Machine(n_rows=n)
        for k in ks:
            self.assertEqual(m.add_layer(f'3{k}T', k, ids(k)), 0)
        layers = [(n, k, ids(k)) for k in reversed(ks)]          # outermost first
        outs = {}
        for row in qs.recipe()['records']:
            a = int(row['address'], 16)
            r, (base, got), rb, rp = m.parse(a)
            want, _ = qs.chain(row, layers)
            self.assertEqual(r, 0x5A5A)
            self.assertEqual(got, want, f'{row["address"]} N={n} order={ks}')
            self.assertEqual((rb, rp), (0xC18C0460, a - 0xC18C0460 + row['length']), row['address'])
            outs[a] = got
        self.assertTrue(m.copy_heads and all(x and y for x, y in m.copy_heads), m.copy_heads[:3])
        return m, outs

    def test_one_layer_n3(self):
        self.run_scenario(3, [2])

    def test_two_layers_either_order(self):
        _, a = self.run_scenario(4, [2, 3])
        _, b = self.run_scenario(4, [3, 2])
        self.assertEqual(a, b)

    def test_six_layers_n8(self):
        self.run_scenario(8, [2, 3, 4, 5, 6, 7])
        self.run_scenario(8, [7, 5, 3, 2, 4, 6])

    def test_untouched_record_passes(self):
        m = Machine(n_rows=3)
        m.add_layer('32T', 2, ids(2))
        a = 0xC2278C02                                           # QS_CINE header: not ours
        r, (base, got), rb, rp = m.parse(a)
        self.assertEqual(base, 0xC18C0460)                       # no copy
        self.assertEqual(rp, a - 0xC18C0460 + len(got))

    def copy_parse(self, m, row, name):
        """The reader on a copy (the stock bytes) whose header names `name`."""
        a = int(row['address'], 16)
        pos = a - 0xC18C0460
        buf = HEAP + 0x780000
        nm = name.encode().ljust(8, b'\0')
        m.mu.mem_write(buf, nm + struct.pack('<II', 2, 0xC18C0460) + qs.stock(row) + bytes(1024))
        m.w(READER + 36, buf + 16 - pos)
        m.w(READER + 4, pos)
        m.parsed = None
        m.call(SITE, READER)
        return m.parsed[1]

    def test_unlisted_copy_not_trusted(self):
        """A copy whose maker the books do not list is passed through untouched;
        the same copy under a listed name is edited (the control)."""
        m = Machine(n_rows=3)
        m.add_layer('32T', 2, ids(2))
        row = next(r for r in qs.recipe()['records'] if r['rule'] == 'clone')
        self.assertEqual(self.copy_parse(m, row, 'ZZ'), qs.stock(row))
        self.assertEqual(self.copy_parse(m, row, '32T'), qs.chain(row, [(3, 2, ids(2))])[0])

    def test_other_firmware_record_left_alone(self):
        """A recipe record whose stock bytes differ (FNV): nobody touches it."""
        m = Machine(n_rows=3)
        m.add_layer('32T', 2, ids(2))
        row = next(r for r in qs.recipe()['records'] if r['rule'] == 'clone')
        a = int(row['address'], 16)
        m.mu.mem_write(a + 20, bytes([m.mu.mem_read(a + 20, 1)[0] ^ 1]))
        changed = bytes(m.mu.mem_read(a, row['length']))
        r, (base, got), rb, rp = m.parse(a)
        self.assertEqual((base, got), (0xC18C0460, changed))

    def test_check_refuses_unsupported_n(self):
        m = Machine(n_rows=9)
        self.assertEqual(m.add_layer('39T', 2, ids(2)), 2)       # QS_NMAX
        self.assertEqual(m.word(SITE), SITE_STOCK)
        m = Machine(n_rows=3)
        self.assertEqual(m.add_layer('31T', 1, ids(1)), 2)       # row 1 is FHD's

    def test_stale_hook_is_repaired_by_the_claim(self):
        """A warm restart leaves last boot's B.W on the site: qs_check claims
        first, the loader puts the stock word back, then the check passes
        (camera, d9, 2026-10-07: the check used to come first)."""
        m = Machine(n_rows=3)
        m.w(SITE, bw_word(SITE, 0xC072EF00))                    # last boot's layer, gone now
        m.w(LAY_SITE, bw_word(LAY_SITE, 0xC072EF08))
        self.assertEqual(m.add_layer('32T', 2, ids(2)), 0)
        self.assertIn(SITE, m.books.repaired)
        self.assertIn(LAY_SITE, m.books.repaired)
        row = next(r for r in qs.recipe()['records'] if r['rule'] == 'clone')
        self.assertEqual(m.parse(int(row['address'], 16))[1][1], qs.chain(row, [(3, 2, ids(2))])[0])

    def test_check_refuses_a_site_held_by_someone_else(self):
        """Another sup holds the site this boot (EXCL): the claim is refused."""
        m = Machine(n_rows=3)
        m.books.start(m.path('20OTHER'))
        self.assertEqual(m.books.claim(SITE, 4, SVC + 0xF00, 2), 0)
        m.books.finish(keep=True)
        self.assertEqual(m.add_layer('32T', 2, ids(2)), 1)       # QS_HOOK
        self.assertEqual(m.word(LOAD_SITE), LOAD_STOCK)          # nothing hung


def m_place(m):
    """qs_pack_place in a blob loaded at a scratch block."""
    at = HEAP + 0x300000
    m.mu.mem_write(at, Machine.blob)
    return at + Machine.sym['qs_pack_place']


PACK = b'NBR\0\x01\x05' + bytes(10) + bytes(range(256)) * 2


class TestLayout(unittest.TestCase):
    """C05D90F0: a QS layout the cache holds is released -- once -- only while
    ANOTHER, non-QS layout loads (QS closed): the tree parsed before the layers
    were hung, and (switch rule) after the value became this sup's."""

    def machine(self, order):
        m = Machine(n_rows=4)
        lays = m.layout_model()
        for k in order:
            self.assertEqual(m.add_layer(f'3{k}T', k, ids(k)), 0)
        return m, lays

    def test_one_release_any_order(self):
        for order in ([2, 3], [3, 2]):
            m, lays = self.machine(order)
            old = m.word(lays[0] + 0xC)
            m.load_layout(lays[0])                             # QS opens: its own load, nothing
            self.assertEqual(m.released, [], order)
            m.load_layout(m.other)                             # QS closed, something else loads
            self.assertEqual(m.released, [old], order)         # once, the pre-hook tree
            self.assertEqual(m.word(lays[0] + 0xC), 0, order)  # (model: v3 clears it -- unproven)
            m.load_layout(lays[0])                             # the stock load re-parses it
            self.assertNotEqual(m.word(lays[0] + 0xC), 0, order)
            m.load_layout(m.other)
            self.assertEqual(len(m.released), 1, order)        # never again
            self.assertEqual(m.layouts, [lays[0], m.other, lays[0], m.other])

    def test_never_while_it_loads(self):
        """The v5c freeze: every condition holds, but the layout being loaded
        is the cache's -- nothing, however many times."""
        m, lays = self.machine([2, 3])
        for _ in range(3):
            m.load_layout(lays[0])
        self.assertEqual(m.released, [])

    def test_not_while_another_qs_layout_loads(self):
        m, lays = self.machine([2])
        m.load_layout(lays[1])
        m.load_layout(lays[2])
        self.assertEqual(m.released, [])

    def test_once_even_if_release_is_deferred(self):
        m, lays = self.machine([2])
        m.v3_frees = False                                     # the cache lets go later
        m.load_layout(m.other)
        m.load_layout(m.other)
        self.assertEqual(len(m.released), 1)

    def test_waits_while_on_screen(self):
        m, lays = self.machine([2])
        for screen in (0x84C, 0x848):
            m.w(m.ctx + screen, lays[0])                       # QS is the current / held screen
            m.load_layout(m.other)
            self.assertEqual(m.released, [], hex(screen))
            m.w(m.ctx + screen, RAM + 0x7F0000 + screen)
        m.load_layout(m.other)                                 # closed: now
        self.assertEqual(len(m.released), 1)

    def test_not_while_busy(self):
        m, lays = self.machine([2])
        m.mu.mem_write(RCACHE + 0x28, b'\1')
        m.load_layout(m.other)
        self.assertEqual(m.released, [])

    def test_not_if_not_only_the_caches(self):
        m, lays = self.machine([2])
        m.w(m.word(lays[0] + 0xC) + 0xC, 2)                    # someone else holds the tree
        m.load_layout(m.other)
        self.assertEqual(m.released, [])

    def test_rebuilt_layout_is_forgotten(self):
        m, lays = self.machine([2])
        m.load_layout(lays[0])                                 # the snapshot (QS open)
        self.assertEqual(m.released, [])
        m.w(lays[0] + 0xC, m.new_tree(lays[0]))                # then rebuilt: through our layers
        m.load_layout(m.other)
        self.assertEqual(m.released, [])

    def test_a_layout_built_after_the_snapshot_counts(self):
        """Switch rule on a QS layout the cache had not built at the snapshot."""
        m = Machine(n_rows=4)
        lays = m.layout_model()
        name = m.word(RCACHE_NAMES)
        del m.finds[name]                                      # not built yet
        m.w(RES_VALUE, 3)
        self.assertEqual(m.add_layer('32T', 2, ids(2), enum=9, flags=1), 0)
        m.load_layout(m.other)                                 # snapshot: slot 0 empty
        self.assertEqual(m.released, [])
        m.finds[name] = lays[0]                                # built later, through us
        m.w(RES_VALUE, 9)
        m.load_layout(lays[0])
        self.assertEqual(m.released, [])
        m.load_layout(m.other)
        self.assertEqual(len(m.released), 1)

    def test_switch_rule(self):
        """Off: switching to this sup's enum does nothing. On: one release, at
        the first non-QS load after the switch."""
        for flags, want in ((0, 0), (1, 1)):
            m = Machine(n_rows=4)
            lays = m.layout_model()
            m.w(RES_VALUE, 3)
            self.assertEqual(m.add_layer('32T', 2, ids(2), enum=9, flags=flags), 0)
            m.load_layout(lays[1])                             # first load: snapshot
            m.w(lays[0] + 0xC, m.new_tree(lays[0]))            # the cache's layout: built after us
            m.w(RES_VALUE, 9)                                  # the user picks this format
            m.load_layout(lays[0])                             # QS re-parse inside the setter
            self.assertEqual(len(m.released), 0, flags)
            m.load_layout(m.other)                             # QS closed
            self.assertEqual(len(m.released), want, flags)
            m.load_layout(lays[0])
            m.load_layout(m.other)
            self.assertEqual(len(m.released), want, flags)     # once per switch

    def test_the_cameras_timing(self):
        """Camera 2026-10-07: at power-on the cache's QS layout was parsed
        before the layers; the firmware re-parses QS while the setter still
        runs (the setting holds the OLD value). Releases happen only at a
        non-QS load: the next QS open re-parses with the right names."""
        m = Machine(n_rows=4)
        lays = m.layout_model()
        m.w(RES_VALUE, 4)                                      # power-on: OG3K selected
        self.assertEqual(m.add_layer('30OG2K', 2, ids(2), enum=7, flags=1), 0)
        self.assertEqual(m.add_layer('30OG3K', 3, ids(3), enum=4, flags=1), 0)
        m.load_layout(lays[0])                                 # first QSON: the stale tree stays
        self.assertEqual(len(m.released), 0, 'never inside its own load (the v5c freeze)')
        m.load_layout(m.other)                                 # QS closed
        self.assertEqual(len(m.released), 1, 'released once, waiting for a non-QS load')
        m.load_layout(lays[0])                                 # re-parsed, value 4
        m.load_layout(m.other)
        self.assertEqual(len(m.released), 1)
        # the user picks OG2K in QS: the in-setter re-parse sees the OLD value (4)
        m.load_layout(lays[0])
        self.assertEqual(len(m.released), 1)
        m.w(RES_VALUE, 7)                                      # the setter finishes
        m.load_layout(lays[0])                                 # still QS
        self.assertEqual(len(m.released), 1)
        m.load_layout(m.other)                                 # closed: released, now with 7
        self.assertEqual(len(m.released), 2)
        m.load_layout(lays[0])
        m.load_layout(m.other)
        self.assertEqual(len(m.released), 2)


class TestLayoutMutations(unittest.TestCase):
    """Each rule of qs_layout is needed: a build without it fails TestLayout."""
    MUTATIONS = {
        'v5c: releases inside its own load': (
            'qs_layer.c', 'if (qs_slot(snap, ctx, layout) >= 0 || (at = qs_slot(snap, ctx, held)) < 0)',
            'if ((at = qs_slot(snap, ctx, held)) < 0)'),
        'releases while another QS layout loads': (
            'qs_layer.c', 'if (qs_slot(snap, ctx, layout) >= 0 || (at = qs_slot(snap, ctx, held)) < 0)',
            'if (layout == held || (at = qs_slot(snap, ctx, held)) < 0)'),
        'ignores the current screen': ('qs_layer.c', 'peek(ctx + 0x84C) != l && ', ''),
        'ignores the held screen': ('qs_layer.c', ' && peek(ctx + 0x848) != l;', ';'),
        'ignores busy': ('qs_layer.c', 'peek8(RCACHE + 0x28) == 0 && ', ''),
        'ignores other holders': ('qs_layer.c', 'peek(tree + 0xC) == 1 &&', '1 &&'),
        'not one shot': ('qs_layer.c', '        poke(mark, 0);                                  /* one shot per layout */\n', ''),
        'pending not cleared': ('qs_layer.c', '        poke(header + QS_H_PENDING, 0);\n', ''),
        'rebuilt not forgotten': ('qs_layer.c', 'if (peek(mark) && peek(mark) != tree) poke(mark, 0);',
                                  'if (0 && peek(mark) != tree) poke(mark, 0);'),
        'v3 given the tree (v5c)': ('qs_layer.c', '((fn1)RCACHE_V3)(RCACHE);', '((fn1)RCACHE_V3)(tree);'),
        'late layouts unknown': ('qs_layer.c', 'if (!at && name && (at = ((fn2)LAYOUT_FIND)(ctx, name))) poke(snap + 8 * i, at);',
                                 '(void)ctx; (void)name;'),
    }

    def test_every_mutation_is_caught(self):
        real = Machine.blob, Machine.sym
        try:
            for name, edit in self.MUTATIONS.items():
                with self.subTest(mutation=name):
                    Machine.blob, Machine.sym = qs_build.layer_blob(edits=[edit])
                    result = unittest.TestResult()
                    unittest.defaultTestLoader.loadTestsFromTestCase(TestLayout).run(result)
                    self.assertFalse(result.wasSuccessful(), f'mutation survived: {name}')
        finally:
            Machine.blob, Machine.sym = real


class TestPack(unittest.TestCase):
    """C05E84D8: each sup's private pack goes into the context the stock NBR went into."""

    def test_registered_once_per_context_each_layer(self):
        for order in ([2, 3], [3, 2]):
            m = Machine(n_rows=4)
            packs = {}
            for k in order:
                self.assertEqual(m.add_layer(f'3{k}T', k, ids(k), pack=PACK), 0)
                packs[k] = m.word(m.last_block + 96)
            self.assertTrue(all(p % 64 == 0 for p in packs.values()))
            self.assertEqual(m.nbr_load(0x1111, STOCK_NBR), 0)
            got = sorted(a for a in m.loads)
            want = sorted([(0x1111, STOCK_NBR, 0x1000, 0)] +
                          [(0x1111, packs[k], len(PACK), 0) for k in order])
            self.assertEqual(got, want, order)
            m.nbr_load(0x1111, STOCK_NBR)                      # the same context again
            self.assertEqual(len(m.loads), len(want) + 1)
            m.nbr_load(0x2222, STOCK_NBR)                      # a new context
            self.assertEqual(len(m.loads), len(want) + 1 + 1 + len(order))

    def test_other_packs_do_not_trigger(self):
        m = Machine(n_rows=3)
        m.add_layer('32T', 2, ids(2), pack=PACK)
        m.nbr_load(0x1111, 0xC1000000)
        self.assertEqual(len(m.loads), 1)

    def test_record_path_registers(self):
        """The stock NBR loaded before the layers: the first record registers."""
        m = Machine(n_rows=3)
        m.add_layer('32T', 2, ids(2), pack=PACK)
        row = next(r for r in qs.recipe()['records'] if r['rule'] == 'clone')
        r, (base, got), rb, rp = m.parse(int(row['address'], 16), ctx=0x3333)
        self.assertEqual([a[0] for a in m.loads], [0x3333])
        self.assertEqual(got, qs.chain(row, [(3, 2, ids(2))])[0])

    def test_failed_pack_leaves_qs_stock(self):
        m = Machine(n_rows=3)
        m.add_layer('32T', 2, ids(2), pack=PACK)
        hdr = m.last_block
        m.load_fail.add(m.word(hdr + 96))
        row = next(r for r in qs.recipe()['records'] if r['rule'] == 'clone')
        r, (base, got), rb, rp = m.parse(int(row['address'], 16), ctx=0x3333)
        self.assertEqual((base, got), (0xC18C0460, qs.stock(row)))
        self.assertEqual(m.word(hdr + 148), 0x80000001)        # QS_ST_PACK

    def test_pack_place(self):
        m = Machine()
        blk = HEAP + 0x200000
        src, area = blk + 0x1003, blk + 0x2001
        m.mu.mem_write(src, PACK)
        to = m.call(m_place(m), src, len(PACK), area, 0x400)
        self.assertEqual((to % 64, to >= area, bytes(m.mu.mem_read(to, len(PACK)))), (0, True, PACK))
        self.assertEqual(m.call(m_place(m), src, len(PACK), area, len(PACK)), 0)   # no room to align
        m.mu.mem_write(src, b'XBR\0')
        self.assertEqual(m.call(m_place(m), src, len(PACK), area, 0x400), 0)

    def test_bad_pack_refused_at_entry(self):
        m = Machine(n_rows=3)
        self.assertEqual(m.add_layer('32T', 2, ids(2), pack=b'XYZ\0' + bytes(60)), 7)
        self.assertEqual(m.word(LOAD_SITE), LOAD_STOCK)


class TestPairs(unittest.TestCase):
    """The converter C06BDA30: (enum, row) pairs, shared."""

    def converter(self, m):
        out = HEAP + 0x790000
        n = m.call(CONV, 0, out, thumb=False)
        t = m.word(out)
        return [(m.word(t + 8 * i), m.word(t + 8 * i + 4)) for i in range(n)]

    def test_stock(self):
        self.assertEqual(self.converter(Machine()), [(3, 0), (2, 1), (4, 2)])

    def test_appended_in_load_order(self):
        m = Machine(n_rows=4)
        self.assertEqual(m.add_layer('32T', 2, ids(2), enum=9), 0)
        self.assertEqual(self.converter(m), [(3, 0), (2, 1), (9, 2)])
        self.assertEqual(m.add_layer('33T', 3, ids(3), enum=5), 0)
        self.assertEqual(self.converter(m), [(3, 0), (2, 1), (9, 2), (5, 3)])

    def test_og_keeps_its_enum(self):
        m = Machine(n_rows=3)
        self.assertEqual(m.add_layer('30OG3K', 2, ids(2), enum=4), 0)
        self.assertEqual(self.converter(m), [(3, 0), (2, 1), (4, 2)])

    def test_refusals(self):
        m = Machine(n_rows=4)
        self.assertEqual(m.add_layer('32T', 2, ids(2), enum=9), 0)
        self.assertEqual(m.add_layer('33T', 3, ids(3), enum=9), 5)     # enum taken
        self.assertEqual(m.add_layer('34T', 2, ids(2), enum=8), 5)     # row taken
        self.assertEqual(m.add_layer('35T', 3, ids(3), enum=3), 5)     # UHD's
        m2 = Machine(n_rows=3)
        m2.w(CONV_MOVW, 0xE3002123)                                    # last boot's value: repaired
        self.assertEqual(m2.add_layer('32T', 2, ids(2), enum=9), 0)
        self.assertIn(CONV_MOVW, m2.books.repaired)
        m4 = Machine(n_rows=3)
        m4.books.start(m4.path('20OTHER'))
        m4.books.claim(CONV_COUNT, 4, SVC + 0xF00, 2)                  # someone's EXCL this boot
        m4.books.finish(keep=True)
        self.assertEqual(m4.add_layer('32T', 2, ids(2), enum=9), 1)    # QS_HOOK
        m3 = Machine(n_rows=3)
        self.assertEqual(m3.add_layer('32T', 2, ids(2), enum=3), 5)    # UHD's, first sup

    def test_a_released_makers_table_is_repaired_away(self):
        """The loader's books are the truth: a table whose maker left them is
        a stale value, and the next claim puts the stock words back."""
        m = Machine(n_rows=4)
        self.assertEqual(m.add_layer('32T', 2, ids(2), enum=9), 0)
        m.books.release_later(0)
        self.assertEqual(m.add_layer('33T', 3, ids(3), enum=5), 0)
        self.assertIn(CONV_MOVW, m.books.repaired)
        self.assertEqual(self.converter(m), [(3, 0), (2, 1), (5, 3)])


class TestEnumClaim(unittest.TestCase):
    """Each sup claims its resolution value (EXCL) before anything else."""

    def test_same_value_second_refused(self):
        m = Machine(n_rows=4)
        self.assertEqual(m.add_layer('30OG2K', 2, ids(2), enum=7), 0)
        self.assertEqual(m.add_layer('31OTHER', 3, ids(3), enum=7), 5)     # QS_ENUM_TAKEN
        self.assertIn('QS FAIL ENUM 7', m.books.log)
        self.assertEqual(m.books.sups[1]['state'], 'release')
        self.assertFalse([c for c in m.books.claims if c['sup'] == 1])      # its claims dropped

    def test_different_values_both_load(self):
        m = Machine(n_rows=4)
        self.assertEqual(m.add_layer('30OG3K', 2, ids(2), enum=4), 0)
        self.assertEqual(m.add_layer('30OG2K', 3, ids(3), enum=7), 0)
        self.assertEqual(m.books.log, [])
        res = sorted(c['addr'] for c in m.books.claims if c['res'])
        self.assertEqual(res, [0x4D4E4500 + 4, 0x4D4E4500 + 7])


def moved_build():
    """A uishare build whose QS entries moved (padding before the layout entry)."""
    import tempfile
    src = (HERE / 'qs_layer.S').read_text()
    seam = '    .word   qs_entry_layout - qs_hdr\n'
    assert src.count(seam) == 1
    d = tempfile.mkdtemp(prefix='qsmoved-')
    p = pathlib.Path(d) / 'qs_layer.S'
    p.write_text(src.replace(seam, '    .rept 5\n    .word 0xE7F000F0\n    .endr\n' + seam))   # udf
    return qs_build.layer_blob(source=p)


class TestTwoBuilds(unittest.TestCase):
    """A QS layer of another build (entries elsewhere) is still recognised."""

    def test_a_b_a(self):
        b = moved_build()
        self.assertNotEqual(b[1]['qs_entry_layout'], Machine().sym['qs_entry_layout'])
        m = Machine(n_rows=5)
        lays = m.layout_model()
        self.assertEqual(m.add_layer('32A', 2, ids(2), enum=4, pack=PACK), 0)
        self.assertEqual(m.add_layer('33B', 3, ids(3), enum=7, pack=PACK, build=b), 0)
        self.assertEqual(m.add_layer('34A', 4, ids(4), enum=8, pack=PACK), 0)
        layers = [(5, k, ids(k)) for k in (4, 3, 2)]
        for row in qs.recipe()['records'][:40] + [r for r in qs.recipe()['records'] if r['rule'] == 'footer_label']:
            r, (base, got), rb, rp = m.parse(int(row['address'], 16))
            self.assertEqual(got, qs.chain(row, layers)[0], row['address'])
        m.nbr_load(0x5555, STOCK_NBR)
        self.assertEqual(len([x for x in m.loads if x[0] == 0x5555]), 1 + 3)   # the NBR, three packs
        m.load_layout(lays[0])
        self.assertEqual(len(m.released), 0)                   # not inside its own load
        m.load_layout(m.other)
        self.assertEqual(len(m.released), 1)                   # one release across the builds


if __name__ == '__main__':
    unittest.main()
