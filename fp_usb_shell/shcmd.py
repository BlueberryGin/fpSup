#!/usr/bin/env python3
"""Run camera memory commands, one per argument:
    shcmd.py 'mem get 0x30190044,,4' 'mem set 0x30190044 0x3000'
Only `mem get` and `mem set` are accepted; anything else is refused before it
reaches the camera, so a permission rule on this script covers nothing more."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from putfile import sh

ALLOWED = ('mem get ', 'mem set ')

cmds = sys.argv[1:]
bad = [c for c in cmds if not c.startswith(ALLOWED) or any(ch in c for ch in ';\n\r')]
if bad:
    sys.exit(f'refused (only mem get / mem set): {bad}')
for cmd in cmds:
    print(sh(cmd))
