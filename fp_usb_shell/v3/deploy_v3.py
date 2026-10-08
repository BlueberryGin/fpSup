#!/usr/bin/env python3
"""Put a Loader v3 card directory onto the camera's card over the USB shell.

    python3 v3/deploy_v3.py CARD_DIR [--only fpSup/00SHELL.BIN ...] [--dry-run]

Per file: delete the old one if it is there (mode 7 overwrites without
truncating, and the 加載器 refuses a sup whose header length is not the file's
size), write it, read the whole file back and compare.  AutoRun.txt goes
last, so a run that stops half way leaves the card booting what it booted
before.  Talks to the camera: --dry-run to see the plan only.
"""
import argparse, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
SHELL = HERE.parent
sys.path.insert(0, str(SHELL))

SKIP = ('backup-before', 'MANIFEST.sha256')


def plan(card, only):
    files = sorted(p for p in card.rglob('*') if p.is_file()
                   and not any(s in p.parts for s in SKIP) and not p.name.startswith('.')
                   and p.suffix.upper() != '.LOG')
    if only:
        want = {pathlib.PurePosixPath(o) for o in only}
        files = [p for p in files if pathlib.PurePosixPath(p.relative_to(card).as_posix()) in want]
    files.sort(key=lambda p: p.name == 'AutoRun.txt')          # AutoRun last
    return [(p, '\\' + '\\'.join(p.relative_to(card).parts)) for p in files]


def remote_sizes(sh, folder):
    out, sizes = sh(f'dir {folder}'), {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[-2].isdigit():
            sizes[parts[-1]] = int(parts[-2])
    return sizes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('card', type=pathlib.Path)
    ap.add_argument('--only', action='append', default=[])
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    steps = plan(a.card, a.only)
    if a.dry_run:
        for local, remote in steps:
            print(f'{remote:28s} {local.stat().st_size:8d} B')
        return 0
    from putfile import sh, delete_file
    made = set()
    with tempfile.TemporaryDirectory() as t:
        back = pathlib.Path(t) / 'back.bin'
        for local, remote in steps:
            folder, name = remote.rsplit('\\', 1)
            folder = folder or '\\'
            parts = [x for x in folder.split('\\') if x]
            for i in range(1, len(parts) + 1):            # mkdir is harmless if it exists
                d = '\\' + '\\'.join(parts[:i])
                if d not in made:
                    sh(f'mkdir {d}')
                    made.add(d)
            old = remote_sizes(sh, folder).get(name)
            if old is not None:
                if not delete_file(remote):
                    sys.exit(f'{remote}: is there ({old} B) but would not delete; stop')
            n = local.stat().st_size
            for tool, args in (('putfile.py', [str(local), remote]),
                               ('getfile.py', [remote, str(back), '--size', str(n)])):
                r = subprocess.run([sys.executable, '-B', str(SHELL / tool), *args],
                                   capture_output=True, text=True)
                if r.returncode:
                    sys.exit(f'{remote}: {tool} failed:\n{r.stdout[-600:]}{r.stderr[-600:]}')
            if back.read_bytes() != local.read_bytes():
                sys.exit(f'{remote}: read back differs; stop')
            got = remote_sizes(sh, folder).get(name)
            if got != n:
                sys.exit(f'{remote}: card says {got} B, wrote {n}; stop')
            print(f'OK  {remote:28s} {n:8d} B' + (f'  (replaced {old} B)' if old is not None else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
