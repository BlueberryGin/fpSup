fpGyroSup Base v1.14.0 -- SIGMA fp firmware Ver.5.02 only

Put AutoRun.txt and fpSup.BIN in the root of the SD card the camera boots
from.  Nothing has to be prepared on the disks you record to: the log goes
to the ROOT of the disk the take went to, not into a GYRO folder.  There is
nothing to create and nothing to put back after a format -- people forgot
the folder and reported the log missing, and the camera cannot make one for
them: the only two moments it could are while a take is starting (which
froze it) or at boot, when it can only guess which disk you will use.  An
old GYRO folder still on a card is now just an empty folder.

Then record.  Each take writes

    \A001_037.GYR    in the root of the disk \CINEMA\A001_037 went to

64 bytes of header and then nothing but 8-byte records, gyro and
accelerometer interleaved in the order they happened.  Convert with

    ./gyro/gyr7.py A001_037.GYR --gcsv A001_037.gcsv

If a take produces no .GYR, look in the root of the disk it went to and
then in the root of the SD card, which is where the log goes when it cannot
be opened on the other disk.  If it is in neither, the logger did not start:
check that all four boxes filled at boot.

This card carries the stream and nothing else: no gcsv on the camera, no
lens profile, no USB shell.  If you would rather the camera wrote the .gcsv and
the .json for you and left no .GYR at all, that is the main fpGyroSup release,
in the same folder.

    https://ijigen.github.io/fpSup/gyro/web/     convert in a browser
    ./gyro/gyr7.py                               convert on the command line
