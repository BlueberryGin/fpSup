#!/usr/bin/env python3
"""Build the fpLossless TEST card: direct compression + USB shell + Fast Start 2.

    python3 -B lossless/build_card.py --out /absolute/new/directory

Writes AutoRun.txt and fpSup.BIN into a NEW directory (refuses a non-empty
one), plus BUILD_NOTES.md and SHA256SUMS. It never writes a card.

WHAT IS IN IT (manifest.json requested_test_build): the current USB shell from
fp_usb_shell, Fast Start 2 (--store-boot --loader-hook --four-box-bar, one
packaging, SUP_BUILD_RULES §5), and the lossless launcher as a run-in-place
section after the shell's worker. No menu, no OpenGate, no gyro.

HOW THE C GETS ON THE CAMERA WITHOUT A LINKER. Every module is compiled as ONE
translation unit with every public function made internal, so the compiler
resolves every call itself and the object carries no relocation at all; the
repository's strict extractor (research/ui/tools/arm_text/elf_text.py) then
takes .text and refuses anything it would have had to patch. -fropi makes
the few function addresses the code stores PC-relative, -fno-jump-tables keeps
switch tables out of .rodata. The source files are not edited: the build
rewrites copies.

WHAT THE LAUNCHER DOES is card.S: allocate its own 64 KiB USER block, copy,
publish, init, then arm four hook sites all-or-nothing, with veneers taken
from the cave's bump allocator. Each site is also declared here as a 4-byte
section holding the firmware's own word, so stage2 journals it and the
loader's power-off callback writes it back (SUP_BUILD_RULES §4).
"""
import argparse
import hashlib
import json
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
FPSUP = HERE.parent
ROOT = FPSUP.parent
SHELL = FPSUP / 'fp_usb_shell'
NATIVE = HERE / "native"
UISHARE = FPSUP / "uishare"                 # the shared UI convention (ui_pool)
IMAGE = ROOT / 'out/seg0_c0000000.bin'
IMAGE_SHA256 = 'aaa5208a028d9c4aebb9cc8614add723d456e96b2a95914f433079954320e622'
sys.path.insert(0, str(SHELL))
sys.path.insert(0, str(ROOT / 'research/ui/tools/arm_text'))

BANNER = 'fpLossless TEST'          # the bar holds 19 characters
MENU_AT = 0xF000                    # native/menu_page.h FPL_MENU_AT = loader MAXLEN
BLOCK_BYTES = 0x80000               # the launcher's own USER block: code and
                                    # state (most of it the 128 flush promises),
                                    # then the menu row's page and string pool,
                                    # which the UI borrows for the whole boot
SITES = {                           # site: (stock word, what it is)
    0xC03A33C8: (0xEBFFFC1A, 'REC preparation: bl C03A2438'),
    0xC038BFF0: (0xE12FFF33, 'creator enqueue: blx r3'),
    0xC0398D88: (0xE92D49F0, 'stop check entry: push {r4-r8, fp, lr}'),
    0xC03A5490: (0xEB0BD652, 'final flush: bl C069ADE0'),
}
# The fields a host reads after a test take, by their path in struct fpl_card.
# Their offsets are taken from the ARM compile itself, never computed on the
# host, whose pointers are a different size.
FIELDS = ['magic', 'hold_live', 'rec_events', 'rec_admitted', 'rec_raw',
          'rec_firmware_refused', 'rec_hold_refused', 'hold_init_failed', 'ring_busy',
          'stops', 'finishes', 'finish_busy', 'last_finish', 'stale_promises',
          'rec.workspace.last_result', 'rec.workspace.native_result',
          'rec.facts.seen.width', 'rec.facts.seen.height', 'rec.facts.seen.bits',
          'rec.facts.seen.raster', 'rec_menu_off', 'menu.result', 'menu.registered',
          'spare_bytes', 'spare_failed', 'spares_freed',
          'task_id', 'lane_b_failed', 'lane_stop_result',
          'hold.magic', 'hold.lane', 'hold.arrivals', 'hold.held', 'hold.passed',
          'hold.compressed', 'hold.refused', 'hold.no_benefit', 'hold.not_eligible',
          'hold.faults', 'hold.swapped', 'hold.swap_declined', 'hold.swap_undone',
          'hold.dma_failed', 'hold.lanes_full', 'hold.refused_by',
          'hold_b.magic', 'hold_b.lane', 'hold_b.held', 'hold_b.compressed',
          'hold_b.refused', 'hold_b.no_benefit', 'hold_b.faults', 'hold_b.swapped',
          'hold_b.dma_failed',
          'flush.applied', 'flush.trailer_failed', 'flush.length_mismatch']
UNITS = ['control.c', 'frame_pipeline.c', 'native/workspace_layout.c', 'uishare/ui_pool.c',
         'native/menu_page.c',
         'native/raw_workspace.c', 'native/rec_workspace.c', 'native/producer_facts.c',
         'native/rec_hook.c', 'native/codec_job.c', 'native/trailer.c',
         'native/frame_hold.c', 'native/flush_site.c', 'native/card.c']
ENTRIES = {'OFF_INIT': 'fpl_card_init', 'OFF_REC': 'fpl_card_rec',
           'OFF_ARRIVE': 'fpl_card_arrive', 'OFF_STOP': 'fpl_card_stop',
           'OFF_FLUSH': 'fpl_card_flush', 'OFF_TASK': 'fpl_card_task'}
CFLAGS = ['--target=armv7a-none-eabi', '-mcpu=cortex-a9', '-mthumb', '-mfloat-abi=soft',
          '-mfpu=none', '-ffreestanding', '-fno-builtin', '-nostdlib', '-fno-jump-tables',
          '-fropi', '-fno-addrsig', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
          '-Wno-unused-function']
PUBLIC = re.compile(r'^(?!typedef\b|static\b|extern\b|#|enum\b|union\b|struct\s+\w+\s*\{)'
                    r'((?:USED\s+)?(?:const\s+)?(?:struct\s+\w+|u?int\w*_t|void|uintptr_t)'
                    r'\s*\**\s*(?:fpl|uis)_\w+\s*\()', re.M)


class BuildError(RuntimeError):
    pass


def unity(tree: pathlib.Path) -> pathlib.Path:
    """Copies with every public fpl_ function made internal, and one unit
    that includes them all, each module's private names prefixed."""
    for src in list(HERE.glob('*.[ch]')) + list(NATIVE.glob('*.[ch]')) + list(UISHARE.glob('*.[ch]')):
        rel = src.relative_to(HERE.parent) if src.parent == UISHARE else src.relative_to(HERE)
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(PUBLIC.sub(r'static \1', src.read_text()))
    lines = ['/* generated by build_card.py */']
    for unit in UNITS:
        path = tree / unit
        text = path.read_text()
        private = sorted({s for s in re.findall(
            r'^(?:static|INLINE)\s[^(=;]*?\b(\w+)\s*\(', text, re.M)
            if not s.startswith(('fpl_', 'uis_'))})
        macros = sorted(set(re.findall(r'^\s*#\s*define\s+(\w+)', text, re.M)))
        lines += [f'#define {s} {path.stem}__{s}' for s in private]
        lines.append(f'#include "{path}"')
        lines += [f'#undef {s}' for s in private] + [f'#undef {m}' for m in macros]
    out = tree / 'unity.c'
    out.write_text('\n'.join(lines) + '\n')
    return out


def compile_c(tree, unit, extra=''):
    source = unit
    if extra:
        source = tree / 'probe.c'
        source.write_text(unit.read_text() + extra)
    obj = tree / (source.stem + '.o')
    clang = shutil.which('clang') or 'clang'
    r = subprocess.run([clang] + CFLAGS + ['-c', '-I', str(tree), '-I', str(tree / 'native'),
                                           '-I', str(tree / 'uishare'),
                        str(source), '-o', str(obj)], capture_output=True, text=True)
    if r.returncode:
        raise BuildError('compile failed:\n' + r.stderr)
    return obj


def symbol_size(obj: pathlib.Path, name: str) -> int:
    d = obj.read_bytes()
    shoff, = struct.unpack_from('<I', d, 0x20)
    shentsize, shnum, _ = struct.unpack_from('<HHH', d, 0x2E)
    secs = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
    symtab = next(s for s in secs if s[1] == 2)
    strtab = secs[symtab[6]]
    for i in range(symtab[5] // 16):
        n, _v, size, _info, _o, _s = struct.unpack_from('<IIIBBH', d, symtab[4] + 16 * i)
        if d[strtab[4] + n:d.index(b'\0', strtab[4] + n)].decode() == name:
            return size
    raise BuildError(f'{name} not in {obj.name}')


def check_sites():
    image = IMAGE.read_bytes()
    if hashlib.sha256(image).hexdigest() != IMAGE_SHA256:
        raise BuildError('firmware image hash differs; hook sites unverified')
    for site, (stock, what) in SITES.items():
        word, = struct.unpack_from('<I', image, site - 0xC0000000)
        if word != stock:
            raise BuildError(f'0x{site:08X} ({what}) reads 0x{word:08X} in the image, '
                             f'not 0x{stock:08X}')


def build_blob(tmp: pathlib.Path):
    from armasm import assemble, symbols
    import elf_text
    tree = tmp / 'tree'
    tree.mkdir()
    unit = unity(tree)
    text = elf_text.load_text(compile_c(tree, unit))
    missing = [f for f in ENTRIES.values() if f not in text.functions]
    if missing:
        raise BuildError(f'entries missing from the text: {missing}')
    for name in ENTRIES.values():
        if not text.functions[name] & 1:
            raise BuildError(f'{name} is not Thumb')
    probe = '\nchar fpl_card_size_probe[sizeof(struct fpl_card)];\n' + ''.join(
        f'char fpl_off_{i}[__builtin_offsetof(struct fpl_card, {f}) + 1];\n'
        for i, f in enumerate(FIELDS))
    probed = compile_c(tree, unit, probe)
    state = symbol_size(probed, 'fpl_card_size_probe')
    layout = {f: symbol_size(probed, f'fpl_off_{i}') - 1 for i, f in enumerate(FIELDS)}
    card = NATIVE / 'card.S'
    probe = [f'BLOB_LEN=0x4', f'BLOCK_BYTES=0x{BLOCK_BYTES:X}', 'STATE_OFF=0x4'] + \
            [f'{k}=0x1' for k in ENTRIES]
    c_base = symbols(card, probe)['c_base']
    blob_len = c_base + len(text.code)
    blob_len += -blob_len % 8
    state_off = (blob_len + 63) & ~63
    if state_off + state > BLOCK_BYTES:
        raise BuildError(f'blob {blob_len} + state {state} exceed the {BLOCK_BYTES}-byte block')
    defines = [f'BLOB_LEN=0x{blob_len:X}', f'BLOCK_BYTES=0x{BLOCK_BYTES:X}',
               f'STATE_OFF=0x{state_off:X}'] + \
              [f'{k}=0x{text.functions[v]:X}' for k, v in ENTRIES.items()]
    launcher = assemble(card, defines)
    if symbols(card, defines)['c_base'] != c_base or len(launcher) < c_base:
        raise BuildError('card.S moved between passes')
    blob = launcher[:c_base] + text.code
    blob += b'\0' * (blob_len - len(blob))
    return blob, {'c_base': c_base, 'c_text': len(text.code), 'blob_len': blob_len,
                  'state_off': state_off, 'state_bytes': state, 'block_bytes': BLOCK_BYTES,
                  'entries': {v: text.functions[v] for v in ENTRIES.values()},
                  'defines': defines, 'layout': layout}


def og3k_sections(tmp):
    """OpenGate 3K as its own release builder makes it: every section with a
    destination (stage2, destination 0, is the shared one this build already
    has) and its entry. Nothing here knows what the sections do."""
    og = ROOT / 'projects/open-gate/build'
    sys.path.insert(0, str(og))
    import build_og3k_ui_candidate as og3k
    og3k.build(out=tmp / 'og3k', release=True)
    blob = (tmp / 'og3k' / 'fpSup.BIN').read_bytes()
    magic, count, entry, _ = struct.unpack_from('<4sIII', blob)
    if magic != b'VBIN' or entry < 0x40000000:
        raise BuildError('OpenGate BIN is not a VBIN with an absolute entry')
    args, off = [], 16 + 8 * count
    for i in range(count):
        dst, ln = struct.unpack_from('<II', blob, 16 + 8 * i)
        if dst:
            if dst < 0x40000000:
                raise BuildError('OpenGate carries a pool-offset section; not supported here')
            f = tmp / f'og3k_{i:03d}_{dst:08x}.bin'
            f.write_bytes(blob[off:off + ln])
            args += ['--also-bin', f'0x{dst:08X}:{f}']
        off += ln + (-ln % 4)
    return args + ['--vshl-entry', f'0x{entry:08X}']


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', type=pathlib.Path, required=True)
    ap.add_argument('--with-og3k', action='store_true',
                    help='also carry OpenGate 3K (its release build from '
                         'projects/open-gate/build, current og3k_ui.S): every fixed '
                         'section and its entry, after the lossless launcher')
    a = ap.parse_args()
    out = a.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f'{out} is not empty: this never overwrites a build or a card')
    out.mkdir(parents=True, exist_ok=True)
    check_sites()
    with tempfile.TemporaryDirectory(prefix='fpl-card-') as t:
        tmp = pathlib.Path(t)
        blob, facts = build_blob(tmp)
        (tmp / 'lossless.bin').write_bytes(blob)
        cmd = [sys.executable, '-B', str(SHELL / 'build_autorun.py'), '--loader',
               '--store-boot', '--loader-hook', '--four-box-bar', '--no-ep-patches',
               '--banner', BANNER, '--boot-bin', f'{tmp / "lossless.bin"}:0',
               '--out', str(out / 'AutoRun.txt')]
        for site, (stock, _) in sorted(SITES.items()):
            f = tmp / f'site_{site:08x}.bin'
            f.write_bytes(struct.pack('<I', stock))
            cmd += ['--also-bin', f'0x{site:08X}:{f}']
        if a.with_og3k:
            cmd += og3k_sections(tmp)
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=SHELL)
        (out / 'build_autorun.log').write_text(r.stdout + r.stderr)
        if r.returncode:
            raise SystemExit('build_autorun failed; see build_autorun.log')
        (out / 'lossless.bin').write_bytes(blob)
    # One file: the loader reads fpSup.BIN only up to its MAXLEN (0xF000), so
    # the menu row's page rides after that, where native/menu_page.c reads it
    # (FPL_MENU_AT). The loader's part is padded to exactly 0xF000.
    sys.path.insert(0, str(HERE / 'menu'))
    import pack_menu_file
    with tempfile.TemporaryDirectory(prefix='fpl-menu-') as t:
        menu = pack_menu_file.build(pathlib.Path(t) / 'menu.bin')
    binary = (out / 'fpSup.BIN').read_bytes()
    if len(binary) > MENU_AT:
        raise BuildError(f'fpSup.BIN is {len(binary)} bytes: past the loader MAXLEN {MENU_AT}')
    (out / 'fpSup.BIN').write_bytes(binary + b'\0' * (MENU_AT - len(binary)) + menu)
    (out / 'layout.json').write_text(json.dumps({
        'record': 'cave block from the bump: +32 "FLPC", +36 card state, +40 resident '
                  'base, +44 blob length', 'state_bytes': facts['state_bytes'],
        'fields': facts['layout']}, indent=1) + '\n')
    produced = sorted(p for p in out.iterdir() if p.is_file() and p.name != 'SHA256SUMS')
    sources = sorted([(HERE.parent if u.startswith('uishare/') else HERE) / u for u in UNITS] +
                     [NATIVE / 'card.S', pathlib.Path(__file__)])
    sums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in produced}
    (out / 'SHA256SUMS').write_text(''.join(f'{h}  {n}\n' for n, h in sums.items()))
    (out / 'build.json').write_text(json.dumps({
        'product': 'fpLossless TEST card', 'banner': BANNER,
        'command': ' '.join(sys.argv), 'blob': facts,
        'sites': {f'0x{s:08X}': {'stock': f'0x{w:08X}', 'what': d}
                  for s, (w, d) in SITES.items()},
        'outputs': sums,
        'sources': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sources},
        'firmware_image_sha256': IMAGE_SHA256,
        'written_to_card': False, 'camera_tested': False}, indent=1) + '\n')
    print(f'built into {out}')
    print(json.dumps({k: v for k, v in facts.items() if k not in ('defines', 'layout')},
                     indent=1))


if __name__ == '__main__':
    main()
