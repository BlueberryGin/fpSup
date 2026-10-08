#!/usr/bin/env python3
"""Write and verify a card over the USB shell.

    ./deploy.py path/to/card-directory
    ./deploy.py --bin-only path/to/card-directory

The camera's file-open mode 7 overwrites but does not truncate. In particular,
replacing a large fpSup.BIN with a smaller one leaves the previous payload's
tail on the card. Prepare a temporary copy at least as long as the existing
file, then compare the complete remote file before saying the update landed.
Transfers have no automatic host timeout: killing one mid-command leaves its
camera-side state uncertain. If the candidate contains FPSUPUI/*.BIN, every
sidecar must already match the card; this tool does not write them. No operation
here reboots the camera.
"""
import argparse
import filecmp
import hashlib
import json
import pathlib
import signal
import struct
import subprocess
import sys
import tempfile
import time

from putfile import FOBJ_ROOM, POOL_OFF, POOL_SIZE, sh

HERE = pathlib.Path(__file__).resolve().parent
FILES = ('fpSup.BIN', 'AutoRun.txt')
TRIES = 5
# putfile's default staging region starts POOL_OFF into the worker's pool and
# also needs a file object. Its own check_fits remains the final authority.
MAX_UPLOAD = POOL_SIZE - POOL_OFF - FOBJ_ROOM
MAX_SIDECAR_READ = 32 * 1024 * 1024 - 0x1000
BACK = HERE / '.deploy-readback'
STATE = BACK / 'oncard.json'


def run(script, *args):
    # Keep putfile's progress visible: its staging/repair phase can otherwise
    # appear to hang for minutes while subprocess.run captures every update.
    return subprocess.run([sys.executable, str(HERE / script), *args],
                          capture_output=script != 'putfile.py', text=True,
                          cwd=HERE)


def note(card, files, sources, sidecars, written):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({'card': str(card), 'files': files,
                                 'source_files': sources,
                                 'sidecars_preflight': sidecars,
                                 'written_files': list(written)},
                                indent=1, sort_keys=True))


def check_pair():
    if STATE.exists():
        return
    print('  NOTE: the last deploy did not finish, or predates this check.\n'
          '        The camera may hold fpSup.BIN from one build and\n'
          '        AutoRun.txt from another, which boots without complaint.')


def remote_sizes():
    """Use the same `dir` shape getfile uses, but fail closed on no response."""
    listing = sh('dir')
    if not listing.strip() or listing.lstrip().startswith('ERR'):
        raise RuntimeError(f'cannot read card directory: {listing.strip()!r}')
    found = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[-1].lower()
        if name not in (n.lower() for n in FILES):
            continue
        try:
            size = int(parts[-2])
        except ValueError:
            raise RuntimeError(f'cannot parse size in directory entry: {line!r}')
        if size < 0:
            raise RuntimeError(f'negative size in directory entry: {line!r}')
        if name in found and found[name] != size:
            raise RuntimeError(f'conflicting directory entries for {name}')
        found[name] = size
    return {name: found.get(name.lower()) for name in FILES}


def prepare_bin(data, minimum):
    """Zero-extend a VBIN, retaining its header and any appended payload data."""
    if max(minimum, len(data)) > MAX_UPLOAD:
        raise ValueError(f'fpSup.BIN would exceed putfile staging limit '
                         f'{MAX_UPLOAD} B')
    if minimum <= len(data):
        return data
    if len(data) < 16:
        raise ValueError('fpSup.BIN is too short for a VBIN header')
    magic, count, _entry, body_len = struct.unpack_from('<4sIII', data)
    if magic != b'VBIN' or count > (len(data) - 16) // 8:
        raise ValueError('fpSup.BIN has no valid VBIN header; cannot pad it')
    if 16 + count * 8 + body_len > len(data):
        raise ValueError('fpSup.BIN section body exceeds the file; cannot pad it')
    return data + b'\0' * (minimum - len(data))


def prepare_autorun(data, minimum):
    """Extend a shorter script using short, inert comment lines."""
    if minimum <= len(data):
        if len(data) > MAX_UPLOAD:
            raise ValueError(f'AutoRun.txt exceeds putfile staging limit '
                             f'{MAX_UPLOAD} B')
        return data
    if not data.endswith(b'\n') or b'\0' in data:
        raise ValueError('AutoRun.txt must end in a newline and contain no NUL to pad it')
    data.decode('utf-8')
    remaining = max(minimum - len(data), 2)
    if len(data) + remaining > MAX_UPLOAD:
        raise ValueError(f'AutoRun.txt would exceed putfile staging limit '
                         f'{MAX_UPLOAD} B')
    filler = bytearray()
    while remaining > 64:
        filler.extend(b'#' + b' ' * 62 + b'\n')
        remaining -= 64
    if remaining == 1:
        # A complete comment needs two bytes. One extra byte is harmless:
        # mode 7 can grow a file even though it cannot shrink one.
        remaining = 2
    filler.extend(b'#' + b' ' * (remaining - 2) + b'\n')
    return data + filler


def compatible_autorun(source, on_card):
    """Accept exact bytes, or only an inert comment tail on the card.

    Historical 32772-byte AutoRun files can carry `###\n` after a 32768-byte
    script. The whole script prefix must match; no executable extra line may
    be treated as equivalent for a BIN-only update.
    """
    if on_card == source:
        return True, 0
    if not source.endswith(b'\n') or not on_card.startswith(source):
        return False, 0
    tail = on_card[len(source):]
    if not tail.endswith(b'\n'):
        return False, 0
    try:
        tail.decode('utf-8')
    except UnicodeDecodeError:
        return False, 0
    if not all(line.startswith(b'#') or not line.strip()
               for line in tail.splitlines()):
        return False, 0
    return True, len(tail)


def read_remote(name, size, destination):
    destination.unlink(missing_ok=True)
    result = run('getfile.py', '\\' + name, str(destination), '--size', str(size))
    if result.returncode or not destination.is_file() or destination.stat().st_size != size:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, detail[-1] if detail else 'readback missing or wrong length'
    return True, ''


def candidate_sidecars(card):
    """Return candidate splash assets; an empty FPSUPUI folder is incomplete."""
    folder = card / 'FPSUPUI'
    if not folder.exists():
        return []
    if not folder.is_dir():
        raise ValueError('FPSUPUI exists but is not a directory')
    files = sorted(path for path in folder.iterdir()
                   if path.is_file() and path.suffix.upper() == '.BIN')
    if not files:
        raise ValueError('FPSUPUI has no .BIN sidecars; sidecar must be '
                         'deployed/verified separately')
    return files


def verify_sidecars(card, destination):
    """Check every candidate sidecar before any card write; never upload one."""
    verified = {}
    for index, source in enumerate(candidate_sidecars(card)):
        relative = source.relative_to(card)
        remote = '\\'.join(relative.parts)
        size = source.stat().st_size
        if size <= 0 or size > MAX_SIDECAR_READ:
            raise ValueError(f'{relative} is {size} B, outside sidecar readback '
                             f'limit 1..{MAX_SIDECAR_READ} B; sidecar must be '
                             'deployed/verified separately')
        copy = destination / f'sidecar-{index}.BIN'
        started = time.monotonic()
        print(f'  checking {relative} ({size} B)...', flush=True)
        ok, detail = read_remote(remote, size, copy)
        if not ok or not filecmp.cmp(source, copy, shallow=False):
            reason = detail or 'bytes differ'
            raise ValueError(f'{relative}: {reason}; sidecar must be '
                             'deployed/verified separately')
        verified[str(relative)] = hashlib.sha256(source.read_bytes()).hexdigest()
        print(f'  {relative}: matched in {time.monotonic() - started:.1f}s',
              flush=True)
    if verified:
        print(f'  FPSUPUI: {len(verified)} candidate sidecars matched before '
              'transfer; none will be written')
    return verified


def main(argv=None):
    started = time.monotonic()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bin-only', action='store_true',
                    help='verify the card AutoRun matches this script (allowing '
                         'only comment-only tail), then write only fpSup.BIN')
    ap.add_argument('card', type=pathlib.Path, help='directory with fpSup.BIN and AutoRun.txt')
    args = ap.parse_args(argv)
    card = args.card.resolve()
    sources = {name: card / name for name in FILES}
    for name, src in sources.items():
        if not src.is_file():
            raise SystemExit(f'{src} is not there')

    # Resolve lengths and prepare both complete expected images before the first
    # card write. A failed preflight must leave the previous pair record intact.
    try:
        old = remote_sizes()
        if args.bin_only and old['AutoRun.txt'] is not None and \
                old['AutoRun.txt'] > MAX_UPLOAD:
            raise ValueError(f'card AutoRun.txt exceeds readback limit '
                             f'{MAX_UPLOAD} B')
        original = {name: src.read_bytes() for name, src in sources.items()}
        expected = {
            'fpSup.BIN': prepare_bin(original['fpSup.BIN'], old['fpSup.BIN'] or 0),
            'AutoRun.txt': (original['AutoRun.txt'] if args.bin_only else
                            prepare_autorun(original['AutoRun.txt'],
                                            old['AutoRun.txt'] or 0)),
        }
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f'preflight failed: {exc}')

    back = BACK
    back.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='deploy-', dir=back) as temp:
        tmp = pathlib.Path(temp)
        prepared = {}
        for name, data in expected.items():
            prepared[name] = tmp / name
            prepared[name].write_bytes(data)
            if len(data) != len(original[name]):
                print(f'  {name}: padded {len(original[name])} -> {len(data)} B '
                      f'(mode 7 does not truncate)')

        if args.bin_only:
            if old['AutoRun.txt'] is None:
                raise SystemExit('preflight failed: card AutoRun length is unknown; '
                                 'use a full deploy with the matching card build')
            copy = tmp / 'check-AutoRun.txt'
            phase = time.monotonic()
            print(f'  checking card AutoRun.txt ({old["AutoRun.txt"]} B)...',
                  flush=True)
            ok, detail = read_remote('AutoRun.txt', old['AutoRun.txt'], copy)
            if not ok:
                raise SystemExit('preflight failed: cannot verify card AutoRun '
                                 f'({detail}); no write was attempted')
            actual_autorun = copy.read_bytes()
            compatible, extra = compatible_autorun(original['AutoRun.txt'],
                                                    actual_autorun)
            if not compatible:
                raise SystemExit('preflight failed: card AutoRun is not byte-for-byte '
                                 'identical to this build, or has executable extra '
                                 'content; use a full deploy')
            expected['AutoRun.txt'] = actual_autorun
            if extra:
                print(f'  AutoRun.txt: script prefix matches; {extra} B of '
                      'comment-only tail verified')
            else:
                print('  AutoRun.txt: exact bytes verified')
            print(f'  AutoRun check took {time.monotonic() - phase:.1f}s', flush=True)

        try:
            sidecars = verify_sidecars(card, tmp)
        except (OSError, RuntimeError, ValueError) as exc:
            raise SystemExit(f'preflight failed: {exc}; no card write was attempted')

        upload = dict(prepared)
        old_bin_size = old['fpSup.BIN']
        if old_bin_size is not None and old_bin_size > len(original['fpSup.BIN']):
            # Previous verified updates leave a zero tail. Mode 7 preserves it,
            # so only the changed prefix has to cross the slow command channel.
            # Read the *current* full file first; a stale host record is not
            # evidence that the tail still contains zeros.
            previous = tmp / 'check-old-fpSup.BIN'
            print(f'  checking old BIN tail ({old_bin_size} B) for a shorter '
                  'transfer...', flush=True)
            ok, detail = read_remote('fpSup.BIN', old_bin_size, previous)
            if ok and not any(previous.read_bytes()[len(original['fpSup.BIN']):]):
                short = tmp / 'upload-fpSup.BIN'
                short.write_bytes(original['fpSup.BIN'])
                upload['fpSup.BIN'] = short
                print(f'  old BIN tail is zero: send {len(original["fpSup.BIN"])} B '
                      f'and verify all {old_bin_size} B afterward', flush=True)
            else:
                print('  old BIN tail is not proven zero; sending the full '
                      f'{old_bin_size} B image' + (f' ({detail})' if detail else ''),
                      flush=True)

        check_pair()
        # From here until readback succeeds, the on-card pair is unconfirmed.
        STATE.unlink(missing_ok=True)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: sys.exit(
                '\n  INTERRUPTED MID-DEPLOY -- card contents are unconfirmed. '
                'Inspect the camera and transfer state before retrying or rebooting.'))

        names = FILES[:1] if args.bin_only else FILES
        for name in names:
            target = prepared[name]
            size = target.stat().st_size
            copy = tmp / f'readback-{name}'
            for attempt in range(1, TRIES + 1):
                src = upload[name]
                phase = time.monotonic()
                print(f'  writing {name} ({src.stat().st_size} B sent, '
                      f'{size} B expected; attempt {attempt})...', flush=True)
                write = run('putfile.py', str(src), '\\' + name)
                if write.returncode:
                    # putfile streams its progress directly to the terminal,
                    # so CompletedProcess.stdout/stderr are both None here.
                    lines = (write.stderr or write.stdout or '').strip().splitlines()
                    detail = lines[-1] if lines else 'see transfer output above'
                    raise SystemExit(f'{name}: write exited {write.returncode} '
                                     f'({detail}); outcome is unconfirmed. DO NOT REBOOT')
                print(f'  {name}: write took {time.monotonic() - phase:.1f}s; '
                      f'reading back {size} B...', flush=True)
                phase = time.monotonic()
                ok, detail = read_remote(name, size, copy)
                if not ok:
                    try:
                        now = remote_sizes()[name]
                    except (OSError, RuntimeError) as exc:
                        raise SystemExit(f'{name}: readback failed ({detail}); '
                                         f'cannot inspect card length ({exc}). '
                                         'DO NOT REBOOT')
                    if now is not None and now > size:
                        raise SystemExit(f'{name}: card file remains {now} B after a '
                                         f'{size} B write; mode 7 cannot truncate it. '
                                         'DO NOT REBOOT')
                    # A failed read does not show whether the last write ran.
                    # Never send another write based on a timeout/error alone.
                    raise SystemExit(f'{name}: readback failed ({detail}); outcome '
                                     'is unconfirmed. DO NOT REBOOT')
                if filecmp.cmp(target, copy, shallow=False):
                    print(f'  {name}  {size} B verified in '
                          f'{time.monotonic() - phase:.1f}s (attempt {attempt})')
                    break
                # A complete, fresh readback proves that the previous handler
                # returned. A byte mismatch can now be repaired by overwriting.
                print(f'  {name}: attempt {attempt} read back different bytes')
                if src != target:
                    upload[name] = target
                    print(f'  {name}: retrying with the full {size} B image',
                          flush=True)
            else:
                raise SystemExit(f'{name} did not land in {TRIES} attempts -- DO NOT REBOOT')

        note(card, {name: hashlib.sha256(expected[name]).hexdigest() for name in FILES},
             {name: hashlib.sha256(original[name]).hexdigest() for name in FILES},
             sidecars, names)
        print(f'  root pair verified; wrote {", ".join(names)} only '
              f'in {time.monotonic() - started:.1f}s (no reboot performed)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
