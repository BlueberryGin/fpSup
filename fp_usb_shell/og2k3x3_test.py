#!/usr/bin/env python3
"""OG2K 3x3 上機驗收 —— 對照組先行,每一步都讀回。

    ./og2k3x3_test.py ctrl            目前這張卡(無 3x3):29.97p 錄兩支 CTRL
    ./og2k3x3_test.py deploy          寫入 og2k-3x3-debug 並逐位元組讀回(之後要 reboot)
    ./og2k3x3_test.py verify          讀回 GTAB 與 M139 三格,OG2K↔FHD 各切一次
    ./og2k3x3_test.py v3              3x3 卡:V3 x2、同開機蓋回原廠一支、V3 再一支

每支錄 1 秒,以 \\CINEMA 出現新資料夾為準;結果追加到 /tmp/clipmap.txt。
不做 menu save / setting write;mode_reg 覆寫收尾一律 mode_reg_test 0 139。
"""
import pathlib, subprocess, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from putfile import sh

CARD = (pathlib.Path(__file__).resolve().parents[2]
        / 'projects/open-gate/build/og2k_v3tap_20260924/og2k-3x3-debug')
ROW = {0xC0B65D84: '0734', 0xC0B65D90: '0737', 0xC0B65D94: '0738'}
STOCK = {0xC0B65D84: 3, 0xC0B65D90: 2, 0xC0B65D94: 2}
V3 = {0xC0B65D84: 5, 0xC0B65D90: 4, 0xC0B65D94: 4}


def rd(a):
    r = sh('mem get 0x%08X,,4' % a)
    return int(r.split('D:')[1].split()[0], 16)


def row():
    return {a: rd(a) for a in ROW}


def folders():
    for _ in range(3):
        try:
            s = {x.split()[-1] for x in sh(r'dir \CINEMA').splitlines() if 'A001_' in x}
        except Exception:
            s = set()
        if s:
            return s
        time.sleep(1.5)
    raise RuntimeError(r'dir \CINEMA 連續三次空白,拒絕以它為閘')


def pulse():
    for c in ('key MOVON', 'key MOVOFF'):
        try:
            sh(c, retries=0)
        except Exception:
            pass
        time.sleep(0.12)


def recording():
    return '1' in sh('status get is_recording').split('->')[-1]


def reselect():
    """FHD → OG2K,讓 og3ksel 重新跑交易。"""
    sh('menu SetMovRecSize 2'); time.sleep(1.8)
    sh('menu SetMovRecSize 4'); time.sleep(2.2)


def clip(label, extra=''):
    assert sh('status get app_current').split('->')[-1].strip() == '2', '不在 CINE'
    assert not recording(), '已在錄影'
    before = folders()
    pulse(); time.sleep(1.0); pulse()
    time.sleep(3.5)
    if recording():
        time.sleep(3.0)
        if recording():
            pulse(); time.sleep(3.5)
    new = sorted(folders() - before)
    got = new[0] if new else None
    print('  %-10s -> %s %s' % (label, got or 'NONE', extra))
    if got:
        with open('/tmp/clipmap.txt', 'a') as f:
            f.write('%s %s %s\n' % (label, got, extra))
    return got


def ctrl():
    sh('menu SetMovFramerate 7'); time.sleep(1.5)
    print('  framerate', sh('menu SetMovFramerate').strip(),
          ' recsize', sh('menu SetMovRecSize').strip())
    reselect()
    r = row(); print('  M139 row', {ROW[a]: v for a, v in r.items()})
    assert r == STOCK, '對照卡的 M139 不是原廠值'
    for i in (1, 2):
        clip('CTRL%d' % i, 'card=pre-3x3')
        time.sleep(2)


def deploy():
    r = subprocess.run([sys.executable, 'deploy.py', str(CARD)],
                       cwd=pathlib.Path(__file__).resolve().parent)
    sys.exit(r.returncode)


def verify():
    g = [rd(0xC0732100 + 4 * i) for i in range(19)]
    print('  GTAB', ' '.join('%X' % x for x in g))
    assert g[0] == 3 and g[10:13] == [2, 4, 2] and g[15:18] == [1, 5, 3], 'GTAB 不是 3x3 版'
    reselect(); a = row()
    sh('menu SetMovRecSize 2'); time.sleep(2.0); b = row()
    sh('menu SetMovRecSize 4'); time.sleep(2.2); c = row()
    for name, r, want in (('OG2K', a, V3), ('FHD', b, STOCK), ('OG2K again', c, V3)):
        print('  %-10s %s %s' % (name, {ROW[k]: v for k, v in r.items()},
                                  'OK' if r == want else '*** MISMATCH ***'))
    print('  FLAG', rd(0xC0731C08))


def v3():
    sh('menu SetMovFramerate 7'); time.sleep(1.5)
    sh('imager mode_reg_test 0 139')
    reselect()
    assert row() == V3, 'M139 不是 5/4/4'
    clip('V3_1'); time.sleep(2)
    clip('V3_2'); time.sleep(2)
    # 同開機對照:覆寫蓋在 161 字之上,把三格寫回原廠
    for i, (a, v) in enumerate((('0x734', 3), ('0x737', 2), ('0x738', 2))):
        sh('imager mode_reg_add %d %s %d' % (i, a, v))
    sh('imager mode_reg_test 1 139'); time.sleep(0.4)
    clip('OVR_STOCK', 'mode_reg 734=3 737=2 738=2 over v3 row')
    sh('imager mode_reg_test 0 139'); time.sleep(2)
    clip('V3_3')


if __name__ == '__main__':
    {'ctrl': ctrl, 'deploy': deploy, 'verify': verify, 'v3': v3}[sys.argv[1]]()
