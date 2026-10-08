#!/usr/bin/env python3
"""Vertical merge tap count -- is it settable from 2 to 3?

WHY
---
OG2K (the (3,2,3,3) merge-tuple family) reduces by 3 vertically, but the pixels
say it only ever *merges two rows and discards the third*.  Reconstructing all
70 modes' 161 sensor registers and grouping them by merge tuple turns up two
pairs of words inside the merge block that behave like per-axis tap counts:

    reg 0x0722 / 0x0723   (copy at 0x0737 / 0x0738)   1x:0   /2:2   /3:2
    reg 0x0724 / 0x0725   (copy at 0x0739 / 0x073A)   1x:0   /2:2   /3:4

One pair does not move from /2 to /3; the other widens.  That is exactly the
asymmetry measured from frames -- vertical still bins two rows at /3 while
horizontal goes from a 2-tap to a 3-tap [1,2,1]/4.  So 0x0722/0x0723 is the
candidate "vertical tap count", and it is sitting at 2 in every merging mode.

This writes 3 to it through the firmware's own override hook and looks at what
comes out.  If the vertical kernel becomes an equal-weight three-row average,
OG2K gains about 0.29 EV and roughly 3.4x less aliasing, for free.

WHAT IT TOUCHES
---------------
`imager mode_reg_add <slot> <addr> <dat>` queues a register override and
`imager mode_reg_test <flag> <mode>` arms it for one sensor mode.  Both are
firmware commands meant for exactly this.  The image sensor is NOT on the
`i2c` command's device list (charge_ic, battery_ic, accel, power_ic, lcd_power,
touchpanel, usbcc), so nothing here can reach the PMIC.

Nothing is persisted: no `menu save`, no `setting write`.  A power cycle puts
the camera back to stock whatever happens, and `--revert` undoes it live.

USAGE
-----
    python3 vtap_test.py --show            # state only, writes nothing
    python3 vtap_test.py --apply           # arm the override on the live mode
    python3 vtap_test.py --revert          # put 2 back and disarm
"""
import argparse
import re
import sys
import time

sys.path.insert(0, '.')
from putfile import sh  # noqa: E402

VERTICAL = ('0722', '0723', '0737', '0738')   # the pair and its copy
HORIZONTAL = ('0724', '0725', '0739', '073A')  # left alone -- shown for context


def say(cmd, retries=1):
    """Run one shell command and print what came back, surviving timeouts."""
    try:
        out = sh(cmd)
    except Exception as exc:                     # the shell recovers; we carry on
        print('    %-38s -- %s' % (cmd, type(exc).__name__))
        return None
    flat = ' | '.join(l.strip() for l in out.strip().splitlines() if l.strip())
    print('    %-38s -- %s' % (cmd, flat[:150]))
    return out


def mode_enum():
    """The sensor mode the camera is on right now, as an int."""
    out = sh('imager mode_now')
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(',')]
        if len(parts) > 3 and parts[0].isdigit() and len(parts[2]) == 4:
            return int(parts[2], 10), line.strip()
    return None, out.strip()


def show():
    enum, line = mode_enum()
    print('  sensor mode : %s' % line)
    print('  enum        : %s' % enum)
    for k, v in snapshot().items():
        if k != 'mode':
            print('    %-14s %s' % (k, v))
    for c in ('status get app_current', 'status get is_recording',
              'menu SetMovRecSize'):
        say(c)
    return enum


def apply_override(value):
    enum, _ = mode_enum()
    if enum is None:
        print('  ! could not read the current sensor mode; stopping')
        return
    print('  arming %s = %d on sensor mode %d' % (','.join(VERTICAL), value, enum))
    for slot, addr in enumerate(VERTICAL):
        say('imager mode_reg_add %d %s %02X' % (slot, addr, value))
    say('imager mode_reg_test 1 %d' % enum)
    time.sleep(0.5)
    print('  -- after --')
    say('imager mode_now')


FIELDS = ('shutter', 'gain', 'base_size', 'bef_size', 'hvbin',
          'v_down_sample', 'raw_zoom', 'offset')


def snapshot():
    """The imager's own view of the current mode, as a dict."""
    out = sh('imager mode_now')
    d = {}
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(',')]
        if len(parts) > 4 and parts[0].isdigit() and len(parts[2]) == 4:
            d['mode'] = line.strip()
        for f in FIELDS:
            m = re.match(r'\s*' + f + r'\s*[: ]\s*(.+)$', line)
            if m:
                d[f] = m.group(1).strip()
    return d


def cycle():
    """Leave the mode and come back, so a queued override gets applied."""
    before = snapshot()
    print('  -- before the mode re-entry --')
    for k, v in before.items():
        print('    %-14s %s' % (k, v))

    say('menu SetMovRecSize 2')          # away to FHD
    time.sleep(2.0)
    say('menu SetMovRecSize 4')          # and back
    time.sleep(2.5)

    after = snapshot()
    print('  -- after --')
    changed = []
    for k in sorted(set(before) | set(after)):
        b, a = before.get(k), after.get(k)
        mark = '' if b == a else '   <-- CHANGED'
        if b != a:
            changed.append(k)
        print('    %-14s %s%s' % (k, a, mark))
    print('  changed: %s' % (', '.join(changed) if changed else 'nothing'))


def recording():
    out = sh('status get is_recording')
    return '1' in out.split('->')[-1]


def pulse():
    """One complete movie-button press.  Never retried: a timed-out reply may
    still have toggled recording, and a second pulse would undo it."""
    try:
        sh('key MOVON', retries=0)
    except Exception:
        pass
    time.sleep(0.12)
    try:
        sh('key MOVOFF', retries=0)
    except Exception:
        pass


def record(seconds):
    if recording():
        print('  ! already recording -- stopping instead of starting another')
        pulse()
        time.sleep(3)
        print('  is_recording now: %s' % recording())
        return
    print('  starting a %.1fs clip' % seconds)
    pulse()
    time.sleep(seconds)
    pulse()
    time.sleep(3.0)
    still = recording()
    print('  is_recording after the stop pulse: %s' % still)
    if still:
        print('  ! still recording -- sending one more complete pulse')
        pulse()
        time.sleep(3.0)
        print('  is_recording now: %s' % recording())
    say(r'dir \CINEMA')


MERGE_SELECT = '001C'        # decoded: 5 = 1x, 3 = 2x, 2 = 3x.  OG2K sits at 2.


def control(value):
    """Positive control for the override path itself.

    0x001C is the one merge register whose meaning is already decoded, and
    changing it cannot be subtle: the sensor would stop reducing by three while
    everything downstream still expects it to.  A broken or differently scaled
    frame means the override mechanism works, and the vertical-tap registers
    were simply the wrong guess.  A frame that is pixel-for-pixel unchanged
    means nothing was ever applied, and the guess is still open.

    Recoverable by a power cycle; nothing here is persisted.
    """
    enum, _ = mode_enum()
    if enum is None:
        print('  ! could not read the current sensor mode; stopping')
        return
    print('  arming merge select 0x%s = %d on sensor mode %d  '
          '(5=1x, 3=2x, 2=3x)' % (MERGE_SELECT, value, enum))
    say('imager mode_reg_add 0 %s %02X' % (MERGE_SELECT, value))
    say('imager mode_reg_test 1 %d' % enum)
    time.sleep(0.5)
    say('imager mode_now')


def control_off():
    enum, _ = mode_enum()
    print('  putting merge select back to 2 (3x) and disarming')
    say('imager mode_reg_add 0 %s 02' % MERGE_SELECT)
    if enum is not None:
        say('imager mode_reg_test 0 %d' % enum)
    time.sleep(0.5)
    say('imager mode_now')


def revert():
    enum, _ = mode_enum()
    print('  putting 2 back and disarming')
    for slot, addr in enumerate(VERTICAL):
        say('imager mode_reg_add %d %s 02' % (slot, addr))
    if enum is not None:
        say('imager mode_reg_test 0 %d' % enum)
    time.sleep(0.5)
    say('imager mode_now')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--show', action='store_true', help='print state, write nothing')
    g.add_argument('--apply', action='store_true', help='set the vertical taps to 3')
    g.add_argument('--cycle', action='store_true',
                   help='re-enter the mode and diff the imager state')
    g.add_argument('--revert', action='store_true', help='set them back to 2 and disarm')
    a = ap.parse_args()

    print('fp vertical-tap test  --  horizontal %s is left alone'
          % ','.join(HORIZONTAL))
    say('version')
    if a.show:
        show()
    elif a.apply:
        show()
        apply_override(3)
    elif a.cycle:
        cycle()
    else:
        revert()


if __name__ == '__main__':
    main()
