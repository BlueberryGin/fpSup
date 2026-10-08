#!/usr/bin/env python3
"""Drive the fp's menus from the host and look at the result.

    ./ui_drive.py MENU DOWN RIGHT shot        # keys in order, then a screenshot
    ./ui_drive.py shot                        # just a screenshot
    ./ui_drive.py reboot                      # reboot over the shell, wait for pong

Every step prints what it did; `shot` prints the local JPG path (read it with
the Read tool to see it).  Keys are the shell's `key <NAME>ON` events, one per
step, `KEY_GAP` seconds apart -- back-to-back key commands once wedged the
USB worker (fp-camera-control 8b).  A key word may carry a count: `DOWN:3` (not `*`: zsh globs it).

Rules learned on the camera (skill fp-ui-selftest):
  - look before every decision: one screenshot per screen change, never chain
    a long blind key sequence;
  - MENU from live view opens the menu where it was last left, not at a fixed
    page -- read the screenshot to find where you are;
  - the screenshot key does not move the UI, but it dismisses the QS overlay;
  - S1 (half-press) leaves any menu, even a frozen one, back to live view
    without recording (its USB reply may time out; the key still lands).
"""
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
FPSH = str(HERE / 'host' / 'fpsh')
FPSHD = str(HERE / 'fpshd')
OUT = pathlib.Path('/tmp/fp_ui')
KEY_GAP = 0.8
SHOT_WAIT = 2.5
REBOOT_LIMIT = 120                              # a normal reboot answers in ~24 s
KEYS = {'MENU', 'OK', 'UP', 'DOWN', 'LEFT', 'RIGHT', 'QS', 'S1', 'DISP', 'Fn', 'AEL', 'PLAY'}
# never from here: S2 / MOV (they record in CINE), CAPTURE (use shot)


def sh(*args, timeout=20):
    try:
        return subprocess.run([FPSH, *args], capture_output=True, text=True,
                              timeout=timeout, cwd=HERE).stdout.strip()
    except Exception as e:
        return f'<{type(e).__name__}>'


def key(name):
    if name not in KEYS:
        raise SystemExit(f'unknown or refused key {name!r}; allowed: {sorted(KEYS)}')
    r = sh('key', f'{name}ON')
    if name == 'S1':                            # half-press: a full ON/OFF pulse
        time.sleep(0.3)
        r += sh('key', 'S1OFF')
        time.sleep(0.5)
        r += sh('key', 'S1OFF')                 # once seen lost: MENU then does nothing (2026-10-03)
    time.sleep(KEY_GAP)
    return r


def shots():
    listing = sh('dir', '\\DCIM\\100SIGMA', timeout=30)
    return {int(m.group(2)): int(m.group(1))
            for m in re.finditer(r'(\d+)\s+SS__(\d{4})\.JPG', listing)}


def shot(tag='shot'):
    before = shots()
    sh('key', 'CAPTUREON')
    sh('key', 'CAPTUREOFF')
    for _ in range(10):
        time.sleep(SHOT_WAIT)
        new = {n: s for n, s in shots().items() if n not in before}
        if new:
            break
    else:
        raise SystemExit('no new screenshot appeared')
    n = max(new)
    time.sleep(0.5)
    size = shots()[n]                          # the size can still grow right after it appears
    OUT.mkdir(exist_ok=True)
    local = OUT / f'{time.strftime("%H%M%S")}-{tag}-SS{n:04d}.jpg'
    r = subprocess.run([sys.executable, '-B', 'getfile.py', f'\\DCIM\\100SIGMA\\SS__{n:04d}.JPG', str(local),
                        '--size', str(size)], cwd=HERE, capture_output=True, text=True)
    if not local.exists() or local.stat().st_size != size:
        raise SystemExit(f'getfile failed: {r.stdout[-300:]}{r.stderr[-300:]}')
    return local


def reboot():
    """`reboot` over the shell, restart the daemon until it answers (bootmeasure.one)."""
    subprocess.Popen([FPSH, 'reboot'], cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    time.sleep(1.0)
    subprocess.run(['pkill', '-9', '-f', 'fpshd'], capture_output=True)
    time.sleep(0.3)
    deadline = time.time() + REBOOT_LIMIT
    while time.time() < deadline:
        daemon = subprocess.Popen([FPSHD], cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.3)
        if 'pong' in sh('ping', timeout=6):
            time.sleep(3)                       # let the card's loader finish and the UI settle
            return f'up after {time.time() - t0:.1f} s'
        daemon.kill()
        time.sleep(0.12)
    raise SystemExit(f'no answer in {REBOOT_LIMIT} s: look at the camera (ask the user)')


# ---- where am I, without a screenshot (mem get only) ----------------------
APP_PTR, APP_SCREEN, SCREEN_NAME = 0xC37B7048, 0x84C, 0x08      # app+0x84C = the screen shown
FOCUS_VARS = ("MENU_Level1", "MENU_Level2", "MENU_Level3", "SL_LabFocus", "SL_LabTab",
              "SL_O_W", "SL_O_H", "SL_O_BH", "SL_O_BV", "SL_O_A0", "SL_O_A1", "SL_O_A2", "SL_O_A3")
VAR_CACHE = pathlib.Path('/tmp') / 'fp_appvars_cache.json'


def mem(a, n=4):
    out = sh('mem', 'get', f'{a:#x},,{n:#x}', timeout=30)
    return {int(m.group(1), 16): int(m.group(2), 16)
            for m in re.finditer(r'A:0x([0-9a-f]+), D:0x([0-9A-F]+)', out)}


def mem_words(a, n):
    w = {}
    for o in range(0, n, 0x400):
        for _ in range(3):
            part = mem(a + o, min(0x400, n - o))
            if len(part) == min(0x400, n - o) // 4:
                break
        w.update(part)
    return w


def cstr(a, n=32):
    w = mem(a, n)
    return b''.join(w.get(a + i, 0).to_bytes(4, 'little') for i in range(0, n, 4)).split(b'\0')[0].decode('latin1')


def app_vars(names):
    """{name: value} from the UI app's variable registry (the hash table that
    projects/open-gate/build/sl_menu/appvars.py reads), cached per boot."""
    import json

    def fnv(t):
        h = 0x811c9dc5
        for b in t.encode():
            h = ((h ^ b) * 0x1000193) & 0xFFFFFFFF
        return h
    app = mem(APP_PTR)[APP_PTR]
    reg = mem(app + 0x888)[app + 0x888] + 0x60
    t = mem(reg + 0x14, 0x18)
    count, ents = t[reg + 0x14 + 8], t[reg + 0x14 + 0xC]
    boot = mem(0xC072F6F8)[0xC072F6F8]       # LOAD_DONE_US: one value per boot
    key = f'{ents:#x}:{count}:{boot:#x}'      # the table can sit at the same place
                                              # after a reboot, its descriptors do not
    cache = json.loads(VAR_CACHE.read_text()) if VAR_CACHE.exists() else {}
    if key not in cache:
        w = mem_words(ents, count * 12)
        cache = {key: {str(w[ents + 12 * i]): w[ents + 12 * i + 8] for i in range(count)}}
        VAR_CACHE.write_text(json.dumps(cache))
    out = {}
    for n in names:
        d = cache[key].get(str(fnv(n)))
        if d is not None:
            out[n] = mem(d + 8)[d + 8]
    return out


def object_names(screen):
    """{object id: name} of the screen's page: a stock set by its name, or the
    private page as build_res_lab_card builds it."""
    try:
        sys.path[:0] = [str(HERE.parents[1] / 'projects/open-gate/build')]
        import sl_b25_page as P
        if screen == 'SL_LAB':
            import imx410_plan as PL
            PL.WITH_SL_EXCL, PL.WITH_RATE_ROW, PL.WITH_BIT_ROW = True, False, False
            import sl_lab_page as LAB, sl_size_page as SZ
            img = (P.ROOT / 'out/MAIN_c0000000.bin').read_bytes()
            pool = P.Pool(img[P.U.POOL_START - P.B:P.U.POOL_START - P.B + P.U.POOL_SIZE])
            page, _ = SZ.assemble(LAB.build_lab_page(img, pool, []), [])
        else:
            import sl_pages
            page = sl_pages.set_bytes('B2_5' if screen == 'B2_5' else screen)
            img = (P.ROOT / 'out/MAIN_c0000000.bin').read_bytes()
            pool = P.Pool(img[P.U.POOL_START - P.B:P.U.POOL_START - P.B + P.U.POOL_SIZE])
        return {r.words(7)[3]: pool.text(r.words(7)[5]) for r in P.N.split(page) if r.tag == 0x10003}
    except Exception:
        return {}


def where():
    app = mem(APP_PTR)[APP_PTR]
    scr = mem(app + APP_SCREEN)[app + APP_SCREEN]
    name = cstr(mem(scr + SCREEN_NAME)[scr + SCREEN_NAME]) if scr else '?'
    vals = app_vars(FOCUS_VARS)
    names = object_names(name)
    lines = [f'screen {name}']
    for v in FOCUS_VARS:
        if v in vals:
            x = vals[v]
            label = f' ({names[x]})' if v.startswith(('MENU_', 'SL_LabFocus')) and x in names else ''
            lines.append(f'  {v:12s} {x}{label}')
    return '\n'.join(lines)


def main(argv):
    for word in argv:
        if word == 'shot':
            print('shot', shot())
        elif word.startswith('shot:'):
            print('shot', shot(word[5:]))
        elif word == 'where':
            print(where())
        elif word == 'reboot':
            print('reboot', reboot())
        elif word.startswith('wait'):
            time.sleep(float(word[4:] or 1))
        else:
            name, _, n = word.partition(':')
            for _ in range(int(n or 1)):
                print('key', name, key(name) or '')


if __name__ == '__main__':
    main(sys.argv[1:])
