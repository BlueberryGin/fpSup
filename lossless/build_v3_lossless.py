#!/usr/bin/env python3
"""Build fpLossless as a Loader v3 sup: \\fpSup\\10LOSS.BIN.

    python3 -B lossless/build_v3_lossless.py --out /absolute/new/directory [--card]

Design: projects/usb-shell-sup/notes/LOADER_V3.md §8 step 2. The same C and
the same shims as build_card.py; only the entry differs (native/card.S under
V3=1):

  - the file IS the block: [FSB1 header][card.S][compiled C][FPUI], the state
    after it in the zeroed tail. No allocation, no copy, no second read of a
    file: the row's FPUI block rides in the sup (card.c FPL_MENU_OFF).
  - entry(block, svc) claims all thirteen sites and the engine first, checks
    the layers below, takes its cave block from svc->cave_alloc, and only then
    starts the codec task and installs the row, then arms. A refusal anywhere
    before that returns SL_RELEASE with nothing written.

--out DIR writes DIR/10LOSS.BIN, layout.json and build.json. --card makes DIR a
whole v3 card instead (AutoRun.txt, fpSup/LOADER.BIN, fpSup/UI/, fpSup/00SHELL.BIN
and fpSup/10LOSS.BIN, MANIFEST.sha256). It never writes a camera card.
"""
import argparse
import hashlib
import json
import pathlib
import struct
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
FPSUP = HERE.parent
ROOT = FPSUP.parent
V3 = FPSUP / 'fp_usb_shell' / 'v3'
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(V3))
sys.path.insert(0, str(HERE / 'menu'))

import build_card as C                                   # noqa: E402
import build_v3 as B                                     # noqa: E402

NAME = '10LOSS.BIN'
SUP_ID = 'LOSS'
VERSION = 1
SETTINGS_OFF = 0
SITE_COUNT = 14                    # card.S `claims`; the same sites as build_card.SITES
KINDS = {1: 'CHAIN', 2: 'EXCL', 3: 'SHARED_UI'}
WANT_KIND = {0xC038BFF0: 2, 0xC05BDDAC: 2,                     # arrive, clip size
             0xC05E5B58: 3, 0xC05E61C8: 3, 0xC05E61E0: 3,     # uishare string layers
             0xC0B9E5D8: 2}                                   # bulk size (data)


def menu_block():
    import build_fpui
    menu, _, _ = build_fpui.build()
    return menu


def compile_text(tmp, unit, menu_off, menu_len):
    """The C with the FPUI block's place compiled in."""
    saved = list(C.CFLAGS)
    C.CFLAGS += [f'-DFPL_MENU_OFF=0x{menu_off:X}u', f'-DFPL_MENU_LEN=0x{menu_len:X}u',
                 f'-DFPL_SETTINGS_OFF=0x{SETTINGS_OFF:X}u']
    try:
        obj = C.compile_c(tmp, unit)
        import elf_text
        return obj, elf_text.load_text(obj)
    finally:
        C.CFLAGS[:] = saved


def build_blob(tmp, menu):
    from armasm import assemble, symbols
    tree = tmp / 'tree'
    tree.mkdir()
    unit = C.unity(tree)
    card = HERE / 'native' / 'card.S'
    v3 = ['V3=1']
    probe = v3 + ['BLOB_LEN=0x4', 'BLOCK_BYTES=0x4', 'STATE_OFF=0x4'] + \
            [f'{k}=0x1' for k in C.ENTRIES]
    probed_syms = symbols(card, probe)
    c_base = probed_syms['c_base']
    settings_off = probed_syms['settings']      # the launcher's layout does not depend on C
    global SETTINGS_OFF
    SETTINGS_OFF = settings_off
    # FPL_MENU_OFF is an immediate in the C, so the text's length can depend on
    # it: compile, put the block right after the text, until that holds.
    # Only ever move the block later (2026-10-07: a uishare change made the
    # text's length flip between two encodings of the immediate, so "equal"
    # never came): the block may sit past the text with padding, never inside it.
    menu_off = 0
    for _ in range(8):
        _, text = compile_text(tree, unit, menu_off, len(menu))
        need = (c_base + len(text.code) + 63) & ~63
        if need <= menu_off:
            break
        menu_off = need
    else:
        raise C.BuildError('FPL_MENU_OFF did not settle')
    missing = [f for f in C.ENTRIES.values() if f not in text.functions]
    if missing:
        raise C.BuildError(f'entries missing from the text: {missing}')
    for name in C.ENTRIES.values():
        if not text.functions[name] & 1:
            raise C.BuildError(f'{name} is not Thumb')
    fields = ''.join(f'char fpl_off_{i}[__builtin_offsetof(struct fpl_card, {f}) + 1];\n'
                     for i, f in enumerate(C.FIELDS))
    saved = list(C.CFLAGS)
    C.CFLAGS += [f'-DFPL_MENU_OFF=0x{menu_off:X}u', f'-DFPL_MENU_LEN=0x{len(menu):X}u',
                 f'-DFPL_SETTINGS_OFF=0x{settings_off:X}u']
    try:
        probed = C.compile_c(tree, unit, '\nchar fpl_card_size_probe[sizeof(struct fpl_card)];\n'
                             + fields)
    finally:
        C.CFLAGS[:] = saved
    state = C.symbol_size(probed, 'fpl_card_size_probe')
    layout = {f: C.symbol_size(probed, f'fpl_off_{i}') - 1 for i, f in enumerate(C.FIELDS)}
    blob_len = menu_off + len(menu)
    blob_len += -blob_len % 8
    state_off = (blob_len + 63) & ~63
    block = (state_off + state + 0xFFF) & ~0xFFF
    defines = v3 + [f'BLOB_LEN=0x{blob_len:X}', f'BLOCK_BYTES=0x{block:X}',
               f'STATE_OFF=0x{state_off:X}'] + \
              [f'{k}=0x{text.functions[v]:X}' for k, v in C.ENTRIES.items()]
    launcher = assemble(card, defines)
    syms = symbols(card, defines)
    if syms['c_base'] != c_base or len(launcher) != c_base or syms['settings'] != settings_off:
        raise C.BuildError('card.S moved between passes')
    blob = launcher + text.code
    blob += b'\0' * (menu_off - len(blob))
    blob += menu
    blob += b'\0' * (blob_len - len(blob))
    check_claims(launcher, syms['claims'])
    return blob, {'c_base': c_base, 'c_text': len(text.code), 'menu_off': menu_off,
                  'menu_len': len(menu), 'blob_len': blob_len, 'state_off': state_off,
                  'state_bytes': state, 'block_bytes': block,
                  'entries': {v: text.functions[v] for v in C.ENTRIES.values()},
                  'symbols': {k: syms[k] for k in ('entry', 'claims', 'settings', 'c_base', 'task_shim',
                                                    'g_card', 'flush_real', 'discard_real',
                                                    'stop_resume', 'play_next')},
                  'defines': defines, 'layout': layout}


def check_claims(launcher, at):
    """The claims table as assembled: the thirteen sites build_card declares,
    each stock word the one in BOTH reference images, the kinds as decided."""
    n, = struct.unpack_from('<I', launcher, at)
    rows = [struct.unpack_from('<3I', launcher, at + 4 + 12 * i) for i in range(n)]
    if n != len(C.SITES) or {a for a, *_ in rows} != set(C.SITES):
        raise C.BuildError('card.S claims are not build_card.SITES')
    main = B.IMAGE.read_bytes()
    C.check_sites()                                       # seg0, hash-pinned
    for addr, stock, kind in rows:
        if stock != C.SITES[addr][0] or B.stock(addr, 4, main) != struct.pack('<I', stock):
            raise C.BuildError(f'0x{addr:08X}: claimed stock 0x{stock:08X} is not the image')
        if kind != WANT_KIND.get(addr, 1):
            raise C.BuildError(f'0x{addr:08X}: kind {KINDS.get(kind, kind)}')
    return rows


def sup_file(tmp, pad_to=0):
    menu = menu_block()
    blob, facts = build_blob(tmp, menu)
    # min_svc 2: entry needs its own path in r2 to save the ON/OFF state
    data = B.make_sup(blob, B.SL_HEADER_LEN, SUP_ID, VERSION,
                      block_size=B.SL_HEADER_LEN + facts['block_bytes'], min_svc=2,
                      pad_to=pad_to)
    return data, facts


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', type=pathlib.Path, required=True)
    ap.add_argument('--trace', action='store_true',
                    help='debug: also record the per-arrival trace and the native event log '
                         '(card.c FPL_DIAG_TRACE; card_status.py --trace/--elog reads them)')
    ap.add_argument('--card', action='store_true',
                    help='a whole v3 card: loader, AutoRun, frames, shell and lossless')
    a = ap.parse_args()
    if a.trace:
        C.CFLAGS.append('-DFPL_DIAG_TRACE')
    out = a.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f'{out} is not empty: this never overwrites a build or a card')
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='fpl-v3-') as t:
        data, facts = sup_file(pathlib.Path(t))
    if a.card:
        vbin, _ = B.loader_bin()
        d = out / B.SL_DIR
        (d / 'UI').mkdir(parents=True)
        (d / B.SL_SELF).write_bytes(vbin)
        (out / 'AutoRun.txt').write_bytes(B.autorun(out))
        for i, px in enumerate(B.splash_frames()):
            (d / 'UI' / f'{i}.BIN').write_bytes(px)
        (d / '00SHELL.BIN').write_bytes(B.shell_sup(burst15=True))
        (d / NAME).write_bytes(data)
        meta = out
    else:
        (out / NAME).write_bytes(data)
        meta = out
    (meta / 'layout.json').write_text(json.dumps({
        'record': 'cave block from svc->cave_alloc: +32 "FLPC", +36 card state, '
                  '+40 resident base, +44 blob length', 'state_bytes': facts['state_bytes'],
        'fields': facts['layout']}, indent=1) + '\n')
    sources = [pathlib.Path(__file__), HERE / 'build_card.py', HERE / 'native' / 'card.S'] + \
              [(FPSUP if u.startswith('uishare/') else HERE) / u for u in C.UNITS]
    (meta / 'build.json').write_text(json.dumps({
        'product': 'fpLossless, Loader v3 sup', 'file': NAME, 'sup_id': SUP_ID,
        'version': VERSION, 'card': a.card, 'trace': a.trace,  'blob': {k: v for k, v in facts.items()
                                                    if k != 'layout'},
        'sha256': hashlib.sha256(data).hexdigest(),
        'sources': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(set(sources))},
        'written_to_card': False, 'camera_tested': False}, indent=1) + '\n')
    if a.card:
        files = sorted(p for p in out.rglob('*') if p.is_file() and p.name != 'MANIFEST.sha256')
        (out / 'MANIFEST.sha256').write_text(''.join(
            f'{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(out)}\n'
            for p in files))
    print(f'built into {out}')
    print(json.dumps({k: v for k, v in facts.items() if k not in ('defines', 'layout')},
                     indent=1))


if __name__ == '__main__':
    main()
