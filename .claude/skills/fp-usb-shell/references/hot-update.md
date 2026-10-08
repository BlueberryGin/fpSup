# Verified card hot update

Paths here are relative to the `fpSup/` checkout. `fp_usb_shell/deploy.py`
queries and writes the live camera. Use it only when
the card update is authorized. For an offline-only task, build a candidate in a
fresh local directory, inspect its AutoRun and BIN, and run the offline tests;
the card's actual AutoRun, file lengths and readback remain unverified.

For an authorized card update over the existing USB shell:

```sh
cd fpSup/fp_usb_shell
python3 -B deploy.py --bin-only /path/to/built-card
```

`--bin-only` is for a compatible payload whose loader and AutoRun behavior
remain unchanged. The tool reads the card's complete AutoRun before writing;
it accepts an exact copy or the candidate followed only by complete inert
comment/blank lines left by an earlier non-truncating write. It records the
actual remote hash. If the candidate has `FPSUPUI/*.BIN`, the tool reads every
candidate sidecar in full and refuses the root-file update unless each already
matches the card; it does not upload sidecars or discover extra remote ones.
Check Sensor Lab ABI, Fast configuration and settings values against a trusted
build and its manifest. For a loader or AutoRun change, use the pair-update form:

```sh
python3 -B deploy.py /path/to/built-card
```

The tool writes the BIN before AutoRun, checks each process result, and reads
back the whole remote file. Mode 7 does not truncate an existing longer file.
When a candidate is shorter than the file already on the card, the deploy tool
prepares a temporary padded expected image so the complete remote length can be
compared. If a fresh full read proves the old tail is already zero, it sends
only the new prefix and still compares the complete remote file afterward.
The VBIN header still defines the payload sections; the padding is transfer
storage, not a new runtime pairing rule. Keep the candidate build files
unchanged and record both candidate and transferred lengths.

With a new UP01 worker and fpshd 3.1.0, `putfile.py` stages the BIN in binary
blocks of at most 16,320 bytes on EP 0x01. It probes the current worker before
each block, checks CRC and absolute address, and resolves a lost acknowledgment
against the worker's last accepted tuple. A non-UP01 worker falls back to the
checked `echo` path, so the first update from an older card still uses that
path. Both paths verify staging before card file I/O; deployment still reads
the complete on-card file. The UP01 path has offline tests but no live camera
throughput or DMA-completion validation yet.

Wait for the deploy tool's verified completion before a reboot or RAM worker
swap. A successful `putfile.py` status, a prefix hash, or a matching `--size`
read without checking the actual remote length is insufficient. A failed or
interrupted transfer leaves the card uncertain; inspect its exact state before
resuming. Do not blindly retry a command that may still be executing or kill
an in-flight transfer merely because it is slow. Check the active daemon and
USB owner before recovery; after a wedge, identify the interrupted operation
and use a scoped recovery rather than loading an unrelated payload.

`swapworker.py` replaces the shell worker in RAM after a verified compatible
BIN update. `load.py` with `park.S` replaces a resident loop with the same
state ABI. Neither is a substitute for card readback. Changes to the loader,
Fast Start configuration, entry ABI, or AutoRun require the pair-update path
described by `SUP_BUILD_RULES.md`.
