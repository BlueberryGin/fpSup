================================================================
 Open Gate formats (res-custom)  fpsup-res-custom-v0.1.2test
 SIGMA fp Ver.5.02 only -- Loader v3 card
================================================================

WHAT IT IS

  Extra movie resolutions for CinemaDNG, chosen like UHD and FHD in
  Settings (MENU > recording > resolution) and in Quick Set:

    OG2K    2000x1334  3:2  23.98-100     sensor 3x3 binning
    OG3.5K  3456x2304  3:2  23.98-29.97   full 1:1 readout, scaled in the ISP
    OG3K    3008x2000  3:2  23.98-100     sensor 2x2 binning
    OG4K    3840x2560  3:2  23.98-29.97   full 1:1 readout, scaled in the ISP
    S16     2096x1238  16:9 23.98-100     1:1 window (about 2.9x crop)

  One program file (fpSup\32RESCUS.BIN) and one small data file per
  format (fpSup\RESCUS\*.RCD).  The camera's own tables and settings
  are never changed: the camera only ever stores UHD or FHD; the format
  you chose is remembered in 32RESCUS.BIN itself.

  S16, OG3.5K and OG4K are Jose Hurtado's formats (fpSup-Formats
  v0.6.1), carried over as data.  Open gate on the fp was first done by
  Vitaly Li (FP3K).

HOW TO USE

  Copy AutoRun.txt and the fpSup folder (with fpSup\RESCUS inside) to the
  root of the SD card and start the camera.  To leave a format out, delete
  its .RCD from fpSup\RESCUS (at most six are used; the menu order is the
  file-name order).  To combine with other Loader v3 products, copy their
  fpSup folders over this one.

  Set the recording format to CinemaDNG.  Frame rates a format does not
  have are greyed out while it is chosen (your saved frame rate is kept
  and comes back when you change the resolution again).

  Crop Mode: OG3.5K and OG4K follow it -- with Crop Mode on they record
  the centre of the sensor 1:1, at the same DNG size.  For OG2K, OG3K and
  S16 Crop Mode is greyed out and off while they are chosen (your saved
  setting is kept).

  Cannot share a card with the older OG3K / OG2K cards (30OG3K, 30OG2K),
  Jose's 31FMT, or res-lab.

  Removing: choose UHD or FHD first, then delete the files.

WHAT CHANGED

  - First release.  Replaces the separate OG3K and OG2K cards.

WHAT HAS BEEN VERIFIED

  Offline (emulated against the camera's firmware image, with the real
  loader): loading, every hook and the power-off write-back, the data
  files' checks, the menu blocks, the Quick Set records for 3 to 8 rows,
  and that each format programs the sensor exactly as the card it comes
  from (OG3K/OG2K) or as Jose's patched modes (S16/OG3.5K/OG4K).

  On the camera (earlier builds of this core, not these exact files):
  the menu and Quick Set with all five formats; recording OG3K 12-bit,
  S16 12-bit (with its standby framing), OG4K 8-bit and OG3.5K 8-bit
  (correct DNG sizes, clean pictures); MOV with a format chosen records
  normal MOV.
  NOT yet tested on the camera: Crop Mode with OG3.5K/OG4K, the greyed
  frame rates and Crop Mode, OG3K/OG2K/S16 100p, OG4K standby framing,
  a format coming back after a restart.
