#!/bin/zsh
# usage: rec_take.sh <seconds> -- one MOV pulse to start, NOTHING on USB while recording
# (RECORD_SHELL_COEXIST_RESOLVED: a host transaction at record-start wedges the
# controller), one pulse to stop, then wait before touching USB again.
cd /Users/dido/Developer.localized/SIGMAfp_re/fpSup/fp_usb_shell
T=${1:-3}
host/fpsh key MOVON >/dev/null 2>&1; sleep 0.12; host/fpsh key MOVOFF >/dev/null 2>&1
sleep $T
host/fpsh key MOVON >/dev/null 2>&1; sleep 0.12; host/fpsh key MOVOFF >/dev/null 2>&1
sleep 5
host/fpsh ping
