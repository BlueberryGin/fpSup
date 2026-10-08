================================================================
 USB shell  fpsup-usbshell-v3.4.0test
 SIGMA fp Ver.5.02 only -- Loader v3 card
================================================================

WHAT IT IS

  A debug channel into the running camera.  With the camera attached
  over USB, the host can send any of the firmware's own 77 shell
  commands and read the answer back, and move blocks of camera memory
  in both directions.

  It is parasitic on the camera's own PTP gadget -- the firmware
  keeps owning the endpoints and re-creates them after a
  record-mode reconfiguration, which is why recording survives
  having the shell attached.

  **Firmware Ver.5.02 only.** RAM only: delete AutoRun.txt, pull
  the battery, and the camera is stock. Nothing is written to flash.

HOW TO USE

  Copy AutoRun.txt and the fpSup folder to the root of the SD card and
  start the camera.  To combine with other fpSup products released for
  Loader v3, copy their fpSup folders over this one: every product
  carries the same LOADER.BIN and UI frames, and its own 00SHELL.BIN.

  For developers: 00SHELL.BIN is the whole shell.  To get a shell on
  a card built from another Loader v3 product, drop this one file into
  that card's \fpSup\ folder -- nothing else changes.

  Upgrading from an older (single fpSup.BIN) card: delete the old
  fpSup.BIN from the card root.  A card from before Loader v3 cannot be
  mixed with this one.

  If something did not load, \fpSup\LOAD.LOG on the card says which
  file, and why.

WHAT CHANGED SINCE fpsup-usbshell-v3.3.0

  - Moved to Loader v3: one file per product in \fpSup\.  The shell is
    00SHELL.BIN; the start-up animation frames moved to \fpSup\UI\.
    The firmware words the shell changes are still put back when the
    camera is switched off.  If another product on the same card
    already changed one of them, the shell does not load (rather than
    overwrite it), and LOAD.LOG says so.

  - Fast memory transfer.  The host can now write camera memory at
    about 398 MB/s and read it at about 382 MB/s -- the USB 5 Gb/s
    limit.  Before, reading was about 73 MB/s at best and uploading
    about 0.1 MB/s.  Only areas the camera has handed out can be
    written.  Needs the host tools of this release (fpshd 3.2.0).

  - Commands no longer go missing.  About one command in 20-60 used to
    vanish and the host waited 50 s for an answer.  The camera's own
    PTP receiver was taking them; it is switched off on this card
    while the shell is loaded.  The shell's interface is not a PTP
    one, so no host was using it.

  - The USB bulk endpoints ask for longer bursts (3 -> 15 packets).

  - The AutoRun starts faster (its per-command delays are gone) and
    the animation's text and boxes have a black outline.

  - The EP 0x83 bulk endpoint that v3.3.0 opened is NOT opened by this
    card; it stays the firmware's interrupt endpoint.  Nothing on the
    camera ever sent data through it.

  `shl` and every shell command are unchanged.  Host tools written
  against v3.3.0 still work.

WHAT HAS BEEN VERIFIED

  Offline: fp_usb_shell/v3/test_v3.py (unicorn, the real loader and
  firmware image), 22 tests pass on 2026-10-07.  These tests build
  their shell without the burst-15 rows, so they do not run this
  exact 00SHELL.BIN; they do run this exact LOADER.BIN.

  On the camera, these exact bytes (MANIFEST.txt hashes) were run as
  part of development cards, not as this release folder:

  - 00SHELL.BIN (b1c2823e...) is byte for byte the shell on
    projects/usb-shell-sup/cards/20261006-mem1/, where the fast
    transfer was measured on 2026-10-06 (398 / 382 MB/s, 5,000
    random writes read back with no failure).  That card had an
    earlier LOADER.BIN.

  - AutoRun.txt, LOADER.BIN, the UI frames and 00SHELL.BIN are all
    byte for byte on projects/screenflip/cards/20261007-v3d/, which
    was booted on the camera on 2026-10-07 with other products on
    the same card; every product loaded.

  Not done: this folder alone on a card (shell only); switching the
  camera off and checking the firmware is stock again on this build.

ENDPOINTS

  EP 0x01  OUT   host -> camera   commands and uploads
  EP 0x82  IN    camera -> host   replies and memory reads

DO NOT

  - Do not send a command while the camera is recording or playing
    back.
  - Do not leave it on a card you are shooting with. It is a
    development tool, not something to record through.
