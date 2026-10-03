================================================================
 Lossless  fpsup-lossless-v0.1.2test
 SIGMA fp — lossless-compressed CinemaDNG
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

  Put AutoRun.txt and fpSup.BIN in the root of the SD card and start
  the camera.  SHOOT menu, page 2 (CINE), below Audio Recording:
  Lossless RAW  OFF / ON.  It starts OFF at every power-on; nothing is
  saved in the camera's settings.

WHAT HAS BEEN VERIFIED

  On a SIGMA fp, Ver.5.02, 2026-10-01..02, with the Lossless recording
  code of this card (the test cards also carried OG3K and the USB
  shell).  Changed since, tested only on a computer so far: the menu row
  is now added to the camera's menu page at start-up instead of
  replacing the page (so other cards' rows can join it), it starts up
  seconds faster, and Lossless can share a card with RAW-View:

    - 3K (OG3K) 12-bit 29.97p to SD: 84-87% of frames compressed,
      all written frames decode.
    - 3K 12-bit 48p to an SSD, 15 minutes: about half of the frames
      compressed to the end, no stall.
    - FHD and 3K compressed clips play back in the camera.

  NOT verified: this release card itself has not been booted; UHD,
  10-bit and every frame rate not listed above; sustained SD takes
  past the camera's own auto-stop.  Test build.

MERGING

  Lossless can be combined with every other fpSup card on the merge
  page, RAW-View included.  With OpenGate, use OG3K v0.2.8a / OG2K v0.1.5a or later:
  earlier OpenGate cards do not show their menu names beside the
  Lossless row.

================================================================
