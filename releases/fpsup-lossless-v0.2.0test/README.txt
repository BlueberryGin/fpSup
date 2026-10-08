================================================================
 Lossless CinemaDNG  fpsup-lossless-v0.2.0test
 SIGMA fp Ver.5.02 only -- Loader v3 card
================================================================

WHAT IT IS

  The fp has a hardware lossless codec it never uses for video.  This
  card puts it to work: with Lossless RAW on, each CinemaDNG frame is
  compressed losslessly (DNG Compression 7, tiled) while you record.
  A 3K 12-bit frame of 9.1 MB typically becomes about 3.2 MB.

  Every frame either is compressed or is written exactly as the camera
  writes it today.  When the codec cannot finish a frame in time the
  frame goes out uncompressed, so a clip can mix compressed and
  uncompressed frames.  Nothing is dropped to make room.  The first
  frame of every take is always uncompressed (DaVinci Resolve plays a
  mixed clip only when its first frame is).

  Compressed clips play back in the camera: the player decodes them
  with the same hardware.

HOW TO USE

  Copy AutoRun.txt and the fpSup folder to the root of the SD card and
  start the camera.  SHOOT menu, page 2 (CINE), below Audio Recording:
  Lossless RAW  OFF / ON.

  Your choice is kept across power-off.  It is saved in this card's own
  file (fpSup/10LOSS.BIN), a second or so after you change it, while
  the camera is not recording; nothing is written to the camera's own
  settings.  Copying a fresh 10LOSS.BIN onto the card sets it back to
  OFF.

  To combine with other fpSup products released for Loader v3, copy
  their fpSup folders over this one: every product carries the same
  LOADER.BIN and UI frames, and its own 10LOSS.BIN.

  Upgrading from an older (single fpSup.BIN) card: delete the old
  fpSup.BIN (and the FPSUPUI folder, if there is one) from the card
  root.  A card from before Loader v3 cannot be mixed with this one.

WHAT CHANGED SINCE fpsup-lossless-v0.1.3test

  - New card layout (Loader v3): Lossless is one file, fpSup/10LOSS.BIN.
    Products are combined by copying folders, not on the merge page.
  - The Lossless RAW setting is now remembered after power-off
    (before: OFF at every power-on).
  - Fixed: in FHD (and OG2K) with Lossless on, recording could stop by
    itself after about 94 frames.
  - 3K (OG3K) frames are cut into fewer, larger tiles: compression is
    a little faster, and fewer frames go out uncompressed (about 8%
    instead of 14% at 29.97p).  UHD also gets a better tile size.
  - If something else on the card already uses the same parts of the
    camera, Lossless now steps aside cleanly at start-up instead of
    loading half-way, and everything it changed is put back at
    power-off.
  - When the codec stalls, the frame is given up on (and written
    uncompressed) after about 0.12 s instead of 0.5-1 s.
  - Fixes for rare cases where a frame waiting for the codec could
    leave the pipeline stuck; more internal diagnostics for the
    auto-stop gap reported on SD.

  v0.1.3test and older (single fpSup.BIN cards): Lossless RAW starts
  OFF at every power-on, and FHD / OG2K takes with Lossless on can stop
  after about 94 frames.

WHAT HAS BEEN VERIFIED

  On a SIGMA fp, Ver.5.02, 2026-10-07, with exactly this release's
  AutoRun.txt, LOADER.BIN, UI frames and 10LOSS.BIN (same SHA-256 as
  MANIFEST.txt); the test card also carried the USB shell, a gyro
  test file, OG2K/OG3K and Screen Flip:

    - 3K (OG3K) 12-bit 29.97p to SD, several 10-15 s takes: 512x512
      tiles, about 92% of frames compressed, no stall.
    - FHD 29.97p to SD, several takes with different tile sizes: every
      frame after the first compressed, no stall.

  With earlier builds of this same Loader v3 version (2026-10-06..07):
  loading, the menu row, ON/OFF remembered across restart, the 94-frame
  fix (FHD 24p and 59.94p, 771 and 1954 frames, all compressed),
  Lossless together with OG3K on one card, and a 512x512-tile 3K frame
  decoding on a computer to the full 3024x2010 picture.  Everything
  listed for v0.1.2test (SSD 48p 15 minutes, in-camera playback) was
  on the older card layout.

  NOT verified: in-camera playback and DaVinci Resolve with the new
  512x512 tiles; UHD; 10-bit and frame rates not listed above; the
  100p + 10-bit + Lossless freeze report; sustained SD takes past the
  camera's own auto-stop; saving the setting right before switching
  the camera off; this release folder copied onto a fresh card as-is.
  Test build: make and inspect a disposable recording before using it
  for important footage.

================================================================
