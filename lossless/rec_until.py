#!/usr/bin/env python3
"""One take that may stop itself: start, leave USB alone, then stop only if it
is still recording.

    python3 -B lossless/rec_until.py SECONDS
    python3 -B lossless/rec_until.py --autostop SECONDS   # a take that stops itself

Nothing touches USB while the take runs except the stop pulse itself
(RECORD_SHELL_COEXIST_RESOLVED; 2026-10-06 a status read during an OG3K take
froze the shell). If the camera had already stopped the take itself, that
pulse starts a new take: 10 s later one status read finds it and a second
pulse stops it -- a short extra clip, reported. Prints one JSON line.
"""
import json
import subprocess
import sys
import time
import pathlib

SHELL = pathlib.Path(__file__).resolve().parent.parent / 'fp_usb_shell'
sys.path.insert(0, str(SHELL))
import putfile as pf                                    # noqa: E402

FPSH = str(SHELL / 'host' / 'fpsh')


def pulse():
    subprocess.run([FPSH, 'key', 'MOVON'], capture_output=True)
    time.sleep(0.12)
    subprocess.run([FPSH, 'key', 'MOVOFF'], capture_output=True)


def recording():
    r = pf.sh('status get is_recording', retries=2, timeout_ms=3000).strip()
    # 2026-10-07: a timed-out read came back empty and was taken for "not
    # recording" -- the take ran on and the next two "takes" never started.
    if r.endswith('1'):
        return True
    if r.endswith('0'):
        return False
    raise SystemExit(f'is_recording unreadable ({r!r}): stopping here, sending nothing more')


def clips():
    """Clip folders on the card -- a dir of \\CINEMA, safe once nothing records."""
    r = pf.sh(r'dir \CINEMA', retries=2, timeout_ms=5000)
    if 'No such file or directory' in r:
        return []                          # a freshly formatted card: no clips yet
    if '<DIR>' not in r:
        raise SystemExit(f'dir \\CINEMA unreadable ({r[-80:]!r}): stopping here')
    return [l.split()[-1] for l in r.splitlines() if '<DIR>' in l and l.split()[-1].startswith('A')]


def counted(wait):
    """Start, wait, stop -- no USB read while anything records.  A stop pulse
    that found the take already ended starts a new one; that shows up as two
    new folders, and that take (which surely records) gets one blind pulse."""
    before = clips()
    if recording():
        raise SystemExit('already recording: not starting')
    pulse()
    time.sleep(wait)
    pulse()
    time.sleep(12)
    new = [c for c in clips() if c not in before]
    extra = len(new) >= 2
    if extra:
        pulse()
        time.sleep(12)
    print(json.dumps({'waited_s': wait, 'clips': new,
                      'stopped_by': 'camera, before the host' if extra else 'host'}))


def main():
    if sys.argv[1] == '--counted':
        return counted(float(sys.argv[2]))
    if sys.argv[1] == '--autostop':
        return autostop(float(sys.argv[2]))
    wait = float(sys.argv[1])
    if recording():
        raise SystemExit('already recording: not starting')
    t0 = time.time()
    pulse()
    # Nothing on USB while it records -- not even a status read (2026-10-06:
    # two is_recording reads in an OG3K + lossless take, then the shell and the
    # screen froze after the stop). The stop pulse is the one transaction.
    time.sleep(wait)
    pulse()
    stop_at = round(time.time() - t0, 1)
    time.sleep(10)                     # let the writer finish before any read
    restarted = recording()
    if restarted:
        # the camera had stopped by itself and our pulse started a new take:
        # stop that one too, and say so
        pulse()
        time.sleep(10)
        if recording():
            raise SystemExit('still recording after a second stop pulse')
    print(json.dumps({'waited_s': wait, 'stop_pulse_at_s': stop_at,
                      'stopped_by': 'camera (a new take was started and stopped)' if restarted
                      else 'host, or the camera before it'}))


def autostop(wait):
    """A take expected to stop itself (card too slow): start, send nothing,
    look once after `wait` s; stop it only if it is somehow still recording."""
    if recording():
        raise SystemExit('already recording: not starting')
    pulse()
    time.sleep(wait)
    still = recording()
    if still:
        pulse()
        time.sleep(10)
    print(json.dumps({'waited_s': wait, 'stopped_by': 'host (it had not stopped)' if still
                      else 'camera'}))


if __name__ == '__main__':
    main()
