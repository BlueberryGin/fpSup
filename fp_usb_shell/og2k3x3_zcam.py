#!/usr/bin/env python3
"""OG2K 3x3 + Z CAM 2020x336 多寫的字,逐一加一個 —— §7.52 的錯位修正候選。

    ./og2k3x3_zcam.py            依序錄完整批(29.97p,每支 1 秒)
    ./og2k3x3_zcam.py --dry      只印要送的指令
    ./og2k3x3_zcam.py --all      七個一起 / 去掉 0728 / 對照(§7.55 之後)
    ./og2k3x3_zcam.py --p3       0734=3 搭配 / 不搭配 072A=168(§7.59 之後)

前提:3x3 卡在跑(GTAB 3、M139 5/4/4)、OG2K、CINE、格率已由使用者設成 29.97p。
順序:STOCK(mode_reg 蓋回 3/2/2)→ BASE1 → 七個候選 → BASE2。
每支都只用 0 號槽(候選)或 0–2 號槽(STOCK),錄完 mode_reg_test 0 139 清掉全部 10 槽。
數值一律十進位 —— mode_reg_add 用 strtoul(,,0),前導 0 會變八進位(§7.54)。
每支錄之前先把標籤寫進 /tmp/clipmap.txt 的 PENDING 行:凍結時 USB 一起死,
最後一行 PENDING 就是凍結的那支。
"""
import sys, time
import og2k3x3_test as t
from putfile import sh

# (標籤, [(addr, value), ...])   M139 值 → Z CAM 2020x336 值,§7.52 表
RUNS = [
    ('Z_STOCK',  [(0x734, 3), (0x737, 2), (0x738, 2)]),
    ('Z_BASE1',  []),
    ('Z_079E_4',   [(0x79E, 4)]),     # M139 8
    ('Z_0736_16',  [(0x736, 16)]),    # M139 0;bit4 沒測過(奇數 = 跨色開關)
    ('Z_078D_16',  [(0x78D, 16)]),    # M139 0
    ('Z_079C_63',  [(0x79C, 63)]),    # M139 61
    ('Z_0794_13',  [(0x794, 13)]),    # M139 1
    ('Z_0728_71',  [(0x728, 71)]),    # M139 63
    ('Z_072A_168', [(0x72A, 168)]),   # M139 160
    ('Z_BASE2',  []),
]
ALL7 = [(0x79E, 4), (0x736, 16), (0x78D, 16), (0x79C, 63), (0x794, 13), (0x728, 71), (0x72A, 168)]
# --all:七個一起(Z CAM 336 原廠是一起寫的);去掉 0728 那支用來分開整幀平移
RUNS_ALL = [
    ('Z_STOCK',  [(0x734, 3), (0x737, 2), (0x738, 2)]),
    ('Z_BASE1',  []),
    ('Z_ALL7',   ALL7),
    ('Z_ALL7_no0728', [r for r in ALL7 if r[0] != 0x728]),
    ('Z_BASE2',  []),
]
# --p3:0734=3(線性外推的相位正確值)是否因 072A 延伸讀出而不再混色(§7.44 P1:+0.31 / b 0.42)
RUNS_P3 = [
    ('Z_STOCK_FULL', [(0x734, 3), (0x737, 2), (0x738, 2), (0x72A, 160)]),
    ('Z_BASE1',  []),
    ('P3_072A',  [(0x734, 3), (0x737, 4), (0x738, 4), (0x72A, 168)]),
    ('P3_no072A', [(0x734, 3), (0x737, 4), (0x738, 4)]),
    ('Z_BASE2',  []),
]
if '--all' in sys.argv:
    RUNS = RUNS_ALL
if '--p3' in sys.argv:
    RUNS = RUNS_P3
DRY = '--dry' in sys.argv


def cmd(c):
    print('    ' + c)
    if not DRY:
        sh(c)


def log(line):
    with open('/tmp/clipmap.txt', 'a') as f:
        f.write(line + '\n')


def main():
    if not DRY:
        fr = sh('menu SetMovFramerate').strip()
        g = t.rd(0xC0732100)
        row = t.row()
        print('  framerate enum %s  GTAB %d  M139 %s' % (fr, g, {t.ROW[a]: v for a, v in row.items()}))
        assert g == 3 and row == t.V3, '不是 3x3 卡 / M139 不是 5/4/4'
        assert fr == '3', '格率不是使用者設的 29.97p(enum 3)'
        cmd('imager mode_reg_test 0 139')           # 清 10 槽
    for label, regs in RUNS:
        print('  %s' % label)
        for i, (a, v) in enumerate(regs):
            cmd('imager mode_reg_add %d 0x%X %d' % (i, a, v))
        if regs:
            cmd('imager mode_reg_test 1 139')
            time.sleep(0.4)
        if DRY:
            continue
        desc = ' '.join('%04X=%d' % (a, v) for a, v in regs) or 'none'
        log('PENDING %s fr=29.97 override=%s' % (label, desc))
        t.clip(label, 'fr=29.97 3x3 card override=%s' % desc)
        cmd('imager mode_reg_test 0 139')
        time.sleep(2)
        assert t.row() == t.V3, 'M139 列被改動'


main()
