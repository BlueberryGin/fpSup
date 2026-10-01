#!/usr/bin/env python3
"""Compile RAW/REC workspace objects and preserve focused offline test evidence.

No linker, BIN, AutoRun, transport, installer, card writes or camera access.
REC object relocations remain intact: compiling it is NOT ARM execution proof.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PRODUCT = HERE.parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(out):
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('output must be new or empty; no artifact is overwritten')
    out.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which('clang')
    if not compiler:
        raise RuntimeError('clang is required')
    ledger = {'camera_ready': False, 'artifact_type': 'unlinked ARM objects and offline tests',
              'runtime_hooks_installed': False, 'bin_or_autorun_produced': False,
              'rec_arm_execution_verified': False, 'commands': [], 'sources': {}}
    flags = ['--target=armv7-none-eabi', '-mcpu=cortex-a9', '-marm',
             '-mfloat-abi=soft', '-mfpu=none', '-std=c11', '-O2', '-ffreestanding',
             '-fno-builtin', '-fno-addrsig', '-fno-unwind-tables',
             '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra', '-Werror']
    sources = [PRODUCT / 'control.c', PRODUCT / 'frame_pipeline.c',
               HERE / 'workspace_layout.c', HERE / 'raw_workspace.c', HERE / 'rec_workspace.c']
    tests = [PRODUCT / 'tests/test_control.py', PRODUCT / 'tests/test_frame_pipeline.py',
             HERE / 'test_workspace_layout.py', HERE / 'test_raw_workspace.py',
             HERE / 'test_rec_workspace.py']
    fingerprint = sources + [p.with_suffix('.h') for p in sources] + tests + [
        HERE / 'rec_workspace_fixture.c', Path(__file__).resolve()]
    for path in fingerprint:
        ledger['sources'][str(path.relative_to(PRODUCT))] = sha(path)

    def run(label, command, timeout=120):
        result = subprocess.run(command, cwd=PRODUCT.parent, capture_output=True,
                                text=True, timeout=timeout)
        log = out / (label + '.log')
        log.write_text(result.stdout + result.stderr)
        ledger['commands'].append({'label': label, 'argv': command,
                                   'returncode': result.returncode, 'log': log.name})
        (out / 'ledger.json').write_text(json.dumps(ledger, indent=2) + '\n')
        if result.returncode:
            raise RuntimeError(f'{label} failed; see {log}')
        return result.stdout + result.stderr

    ledger['compiler'] = run('compiler-version', [compiler, '--version'])
    ledger['python'] = sys.version
    ledger['unicorn'] = importlib.metadata.version('unicorn')
    for source in sources:
        run('compile-' + source.stem, [compiler, *flags, '-c', str(source),
                                       '-o', str(out / (source.stem + '.o'))])
    for test in tests:
        run(test.stem, [sys.executable, '-B', str(test), '-v'], timeout=180)
    # Focused assertion-independent regressions, not extra unique coverage.
    for test in tests[2:]:
        run(test.stem + '-optimized', [sys.executable, '-B', '-O', str(test), '-v'], timeout=180)
    # Refuse source drift while tests/builds ran concurrently.
    for path in fingerprint:
        if sha(path) != ledger['sources'][str(path.relative_to(PRODUCT))]:
            raise RuntimeError('source changed during verification: ' + str(path))
    ledger['verification_complete'] = True
    ledger['limitations'] = [
        'Physical memory availability, allocator exclusion and DMA/producer drain use test fixtures.',
        'RAW tests execute native descriptor wrappers; REC integration is host execution plus ARM compilation.',
        'Complete REC ARM execution requires linking; text relocations were not discarded.',
        'Output spans are benefit-only budgets, not worst-case JPEG or enforced DMA bounds.',
        'No native producer-facts provider, installed REC/failure/stop hooks or compression worker is supplied.',
        '14-bit layout arithmetic does not bypass the existing control rejection.']
    (out / 'ledger.json').write_text(json.dumps(ledger, indent=2) + '\n')
    paths = sorted(p for p in out.iterdir() if p.is_file() and p.name != 'SHA256SUMS')
    (out / 'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.name}\n' for p in paths))
    print(out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    build(args.out.resolve())
