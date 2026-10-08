"""The firmware words the AutoRun changes, on their own.

They used to live in build_autorun.py, which runs when imported -- so a
script that wanted only this list rebuilt AutoRun.txt as a side effect, in
whichever mode was the default. That happened twice in one evening, once
replacing a loader build with a classic one nobody asked for.
"""

# Two different jobs, and they used to be one list.  The first patch is what
# makes the channel reachable at all: with the interface still declaring itself
# PTP, the host's own PTP stack claims interface 0 before the daemon can, and
# every command comes back LIBUSB_ERROR_ACCESS.  The other six shape EP 0x83
# for hook-push, which only a USB-attached development session does.
#
# A card that carries the shell needs the first and not the other six.  They
# were bundled, and --no-ep-patches dropped all seven, so the first debug card
# anyone tried to talk to could not be talked to (macOS: ptpcamerad had it).
IFACE = [
    (0xC0CF3740, 0xFFFFFF03,
     "PTP interface template: {bNumEndpoints, class, subclass, protocol}",
     "03/06/01/01 -> 03/ff/ff/ff.  Lengths and the endpoint set are untouched;",
     "this only stops the host's PTP stack from claiming interface 0 before the",
     "shell daemon can."),
]

# SuperSpeed endpoint companions of the shell's two bulk pipes: bMaxBurst 3 ->
# 15 (byte 2 of each word).  At burst 3 a single EP82 transfer measured
# ~240 MB/s whatever its length (2026-10-06); 16-packet bursts are what a
# 5 Gb/s bulk pipe needs to go further.  Loader v3 shell sup only; untested.
BURST15 = [
    (0xC0CF3748, 0x000F3006, "EP 0x01 OUT SuperSpeed companion: bMaxBurst 3 -> 15"),
    (0xC0CF3750, 0x000F3006, "EP 0x82 IN SuperSpeed companion: bMaxBurst 3 -> 15"),
]

# The firmware's own PTP receiver, off.  usbTask (FUN_c0033b80) answers every
# USB event in mode 5 by calling FUN_c04db778(5) from FUN_c0033ad0 -- one PTP
# container in, 4 bytes then the rest -- and that arms EP 0x01 OUT on its own
# buffer.  When it wins the race with the worker, a shell command lands in PTP's
# parser (answer: 12-byte 0x2003 Session Not Open, txid = our sequence) and the
# worker waits out its 50 s.  Seen 1 in 20-60 UP01 chunks; with MEM1 on the third
# transfer (camera, 2026-10-06).  The interface is vendor class, so no host ever
# speaks PTP to it: nothing is lost.  This is the only call site; the other mode
# branches beside it are untouched.  Shell sup only (v3), restored at power-off.
NOPTP = [
    (0xC0033B3C, 0xE3A00000,
     "FUN_c0033ad0 mode 5: BL FUN_c04db778 (PTP receive) -> mov r0, #0"),
    # The driver half of the same receiver.  On an XferNotReady for a bulk OUT
    # (the host sending before anything is armed), whoever waits on "any event"
    # -- usbTask, which keeps running with PTP off -- calls FUN_c01e76b8, and
    # unless OUT_BUSY (0xC31E3974) is 1 it arms OUT on the firmware's buffer
    # [0xC3025744].  The worker sets OUT_BUSY after every transfer, but usbTask
    # handling the same completion can clear it again behind it: 1 lost command
    # in ~300 MEM1 round trips, found whole in that buffer (camera, 2026-10-06).
    # Thumb `beq` (skip when busy) -> `b` (always skip); the next halfword,
    # `mov r0, r5`, is unchanged.  EP0 and IN branches are elsewhere.
    (0xC01E7704, 0x4628E01E,
     "FUN_c01e76b8: beq (OUT_BUSY) -> b, never auto-arm bulk OUT on [0xC3025744]"),
]

PUSH = [
    (0xC0CF3780, 0x02830507, "EP 0x83, SuperSpeed: interrupt -> bulk"),
    (0xC0CF3784, 0x00000400, "EP 0x83, SuperSpeed: wMaxPacketSize 64 -> 1024, bInterval 11 -> 0"),
    (0xC0CF3758, 0x00033006, "its SuperSpeed companion: bMaxBurst 0 -> 3"),
    (0xC0CF375C, 0x00000000, "its SuperSpeed companion: wBytesPerInterval 64 -> 0"),
    (0xC0CF3798, 0x02830507, "EP 0x83, full speed: interrupt -> bulk"),
    (0xC0CF379C, 0x00000040, "EP 0x83, full speed: bInterval 100 -> 0"),
]


# DRAM self-refresh is what keeps the injection cave alive across a soft power
# off, and the firmware's built-in window is 900 seconds: 0xC00239C8,
# `mov r0, #0x384`, stored to [obj+0x6C] by the SelfRefreshSetting constructor
# at 0xC0023948.
#
# `sys selfStopTime` cannot raise it.  The getter at 0xC0024310 reads the
# configured value into r4 and then throws it away, because 0xC0024358 is a
# hardcoded `mov r0, #0` that makes the "use the configured value" test always
# false.  That reads as a compile-time-disabled feature rather than a bug; the
# dead branch's own ceiling is 0xA8C0 = 43200 s = 12 h.
#
# Patching the constructor's default beats enabling the configured path.  The
# configured value lives in BSS, which the boot zeroes (0xC3000000..0xC38D6FB0),
# so a warm boot that skips the AutoRun would silently fall back to 900 seconds
# -- the exact case this exists to serve.  This is code: it sits in the firmware
# image in DRAM and survives the warm boot it is for.
#
# 0xA800 = 43008 s = 11.95 h, the largest ARM-encodable immediate under the
# firmware's own 43200-second ceiling.
#
# NOT free: self-refresh draws current the whole time the camera is off.  Off
# by default; --retain-ram turns it on.
#
# UNVERIFIED: that 900 s is the power-off retention window at all.  It was read
# statically out of a config the `sys selfConfig` command prints, next to poff /
# eco / sleep.  The cheap check is to leave the camera off past the window and
# see whether the cave survives.  See
# research/firmware/notes/DRAM_SELF_REFRESH_AND_WARM_BOOT.md.
RETAIN = [
    (0xC00239C8, 0xE3A00B2A,
     "SelfRefreshSetting default stop time: mov r0,#0x384 (900 s, 15 min) ->",
     "mov r0,#0xA800 (43008 s, 11.95 h).  Holds DRAM in self-refresh across a",
     "soft power-off for twelve hours instead of fifteen minutes, which is what",
     "lets a warm boot find the cave already loaded."),
]

PATCHES = IFACE + PUSH        # both, for a USB development build

SCREEN = [
    (0xC0BB1208, 0xFFFFF8B2, "text colour"),
    (0xC03E46A0, 0xE3A05078, "mov r5,#120 — x, clear of the battery indicator"),
    (0xC03E4698, 0xE3A08010, "mov r8,#16  — y"),
]

BAR_WIDTH = 8
