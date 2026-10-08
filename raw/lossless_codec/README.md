# FHD hardware lossless-JPEG probe suite

**Current entry, 2026-09-30:**
[`PROGRESS_20260930.md`](../../../projects/lossless-sup/notes/PROGRESS_20260930.md).
The status/commands below are historical. A synchronous probe wrote decodable
DNGs on 9/29; a separate async experiment completed against owned synthetic
input. Neither certifies the current integrated async/native-source/callback
version. Source lifetime across batch flush, earlier-image/later-file identity,
source-tail coverage and release still need closure. Do not bypass
ownership gates or treat manually supplied handles as owned buffers.

Builds follow [`SUP_BUILD_RULES.md`](../../SUP_BUILD_RULES.md). Notify the user
before any camera test and obtain authorization for that run.

**9/30 implementation update:** `inline_compress_driver.py arm` and direct
`arm()` now refuse before any camera operation; there is no unsafe override.
The old probe is retained as historical source, not a deployment route. Status
now separates processed steps (`S_DONE`) from actual writebacks (`S_LATE`), and
does not treat end-position, SOI or file length alone as decode/lossless proof.
`test_native_writer_contract.py` executes the stock writer branches: a zero
flush result is failure, observers do exist, and frame+0x1030 is a pathname,
not a persistent writer. The original late-submit ledger is preserved with
corrections in PROGRESS §10. New adaptive policy lives in `../../lossless/`;
it still has no native adapter. No camera operation or installable build was
performed in this increment.

This directory keeps the staged SIGMA fp 5.02 experiments for connecting the
live FHD CinemaDNG writer to the camera's fixed-function lossless-JPEG codec.
It is a research probe suite, not a recording patch and not a flashable
firmware image.

Current product verdict, evidence corrections and research gates live in
[`PROGRESS_20260930.md`](../../../projects/lossless-sup/notes/PROGRESS_20260930.md).
Some detailed runbook text below is a historical description of the staged
fixed-address probes; do not treat it as current memory ownership guidance.

| Phase | Camera status | What it establishes |
|---|---|---|
| Exact writer arguments | **Passed, 2026-08-31** | The live writer seam and its FHD segment are identified |
| Power/clock preflight | **Passed, 2026-08-31** | The exact writer task can balance the required domains while the codec is idle |
| Scratch allocation | **Passed live, 2026-09-21** | A guarded, aligned 4 MiB DMA allocation completed on camera; current output-capacity bound remains unsafe for unknown expansion |
| Encode and measure | **Passed live one-shot and 8-frame burst, 2026-09-21** | Movie-path encode-to-RAM completed; output was discarded, actual tile was 512×512, and this is not sustained writer/card proof |
| Exact final flush | **Offline-verified; not yet run on camera** | One-shot read-only capture of the completed writer list and synchronous flush result |

The hardware codec has now completed one live movie-path encode and a short
eight-frame burst. The measured `F_ENC` latency was 18.146 ms for the one-shot
and 18.138–18.229 ms for the burst. Those values exclude init, size query,
header construction, writer work and SD flush. The live probe used 512×512
tiles; the 512×368 product target is not yet live-validated. The next blockers
are safe output capacity, corrected tile-height wiring, exact flush timing,
expanded-header writer integration and sustained end-to-end recording.

中文摘要：movie writer context 的單幀 encode-to-RAM 與 8-frame short burst 已實機成功，
但輸出仍被丟棄，並未寫出 compressed DNG。18.138–18.229 ms 只量 `F_ENC`；正式
512×368、最壞輸出容量、header/writer、SD flush、長跑與回放仍未成立。這組工具只改 RAM，
並不是未簽章 `.bin` 或可刷寫韌體。

## Contents

| File | Role |
|---|---|
| `exact_dng_writer_probe.py` / `.S` | Build, arm, inspect, and restore the verified one-shot FHD writer probe |
| `exact_writer_power_preflight.S` | Balance power and clock from the exact live writer context without calling the codec |
| `exact_writer_scratch_preflight.S` | Allocate and guard the proposed 4 MiB DMA layout without calling the codec |
| `single_frame_codec_probe.py` | Enforce the staged preconditions and keep live encode disabled |
| `single_frame_encode_discard_probe.S` | PHASE=0/1 historical staged designs; PHASE=2 live one-shot consume-and-free; PHASE=3 bounded multi-frame encode burst |
| `exact_flush_writer_probe.py` / `.S` | Guarded one-shot probe at the final SD flush; observes the exact writer/list shape without modifying it |
| `test_*.py` | Verify assembly bounds, hook transaction order, proof gates, cleanup policy, and dry-run refusal |

## Playback boundary

Compression is not currently a transparent recording feature.  The still-DNG
develop path has a hardware decoder branch for `Compression=7`, but the V5.02
CinemaDngPlay parser reads strip layout and does not parse the tiled lossless-DNG
tags.  A valid compressed frame therefore does not imply in-camera movie
playback.  Variable compressed sizes have a separate first-frame-derived buffer
capacity constraint, and dynamically dropping source frames would additionally
require explicit timeline semantics.  Until those are solved, this directory
remains a probe suite, not a release path.

## Exact CinemaDNG writer probe

This is a one-shot diagnostic for the live FHD call at `0xC0722AFC` to
`0xC069AC88`. It records the first call, restores the firmware instruction from
inside the probe, and then passes the untouched call to the real writer.

The earlier candidate at `0xC0722A58` belongs to a v18 path that did not run in
the FHD recording test. The actual path was identified at `0xC0722AFC`, whose
original word is `0xEBFDE061` and whose expected return address is
`0xC0722B00`.

The installer also requires the surrounding six-word sequence before it writes
anything: `mov r1,sp; mov r0,r10; mov r2,#2; bl; add sp,#8; pop`. This guards
against applying the probe to a different firmware build or nearby call site.

It deliberately uses the shell template region at `0xC072F800` and state at
`0xC072F700`; it does not overwrite the gyro logger resident at
`0xC072E064..0xC072EFAC`.

Build and inspect without touching the camera:

```sh
./exact_dng_writer_probe.py arm --dry-run
```

The following fixed-address live sequence is retained as a historical transcript,
not current deployment guidance. It must not be run without a reviewed dynamic
cave lease, current cache-publication path and explicit camera-operation approval:

```sh
./exact_dng_writer_probe.py arm
# Record 1-2 seconds of FHD CinemaDNG, then stop recording.
./exact_dng_writer_probe.py status
./exact_dng_writer_probe.py restore
```

The first hit should leave `count` at 1, `first_lr` at `0xC0722B00`, `done` at
`0x454E4F44`, and both the live hook and `restored_word` at `0xEBFDE061`.
`count` above 1 means the already-fetched hook instruction ran again; those
extra entries still tail-call the original function and do not overwrite the
first record.

## Live validation — 2026-08-31

A short 1920x1080, 12-bit CinemaDNG recording produced the expected one-shot
result:

```text
hook            0xEBFDE061  original/restored
count           0x00000001
first_r0        0xC3A6F3BC
first_r1        0xC3A6F37C
first_r2        0x00000002
first_lr        0xC0722B00
segment_buffer  0x53B02C00
segment_length  0x00318200
done            0x454E4F44
restored_word   0xEBFDE061
```

This confirms that `0xC0722AFC` is the live FHD CinemaDNG call which registers
one complete 3,244,544-byte DNG segment with `0xC069AC88`. The probe restored
the original instruction itself; no host-side restore write was needed.

Do not run `putfile.py`, `getfile.py`, or another template helper while the
probe is armed: those tools share `0xC072F700/0xC072F800`. If the camera becomes
unresponsive, pull the battery; all changes are RAM-only.

## Exact-writer codec preflight and encode design

`single_frame_codec_probe.py` shares the same verified live call site and the
same six-word context guard as the exact argument probe. It has five actions:

```sh
./single_frame_codec_probe.py preflight --dry-run
./single_frame_codec_probe.py scratch --dry-run
./single_frame_codec_probe.py encode --dry-run
./single_frame_codec_probe.py status --dry-run
./single_frame_codec_probe.py restore --dry-run
```

The `preflight` image is a power/clock-only one-shot. On its first entry it
restores `0xC0722AFC`, then performs this balanced sequence from the exact
writer task:

```text
power domain 3 on -> clock domain 5 on -> clock domain 5 off -> power domain 3 off
```

Every return value and any cleanup error are recorded. Cleanup follows the reviewed firmware policy: a
failed power-on releases nothing; a failed clock-on releases power; after a
successful clock-on, clock-off is attempted, and a failed clock-off does not cut
power underneath a clock that may still be enabled. Before those calls it reads
the segment and DNG only to enforce the observed FHD shape, verifies the codec
mode is idle, and records the source address. It never modifies the DNG or
segment, allocates memory, calls a codec wrapper, or writes codec globals. A
complete success leaves magic `0x52575050` (`PPWR`) at state offset `+0xD4`.

Live on 2026-08-31, this exact writer context passed: one hit, the original hook
restored, codec mode zero, aligned source `0x53B16000`, and all four power/clock
returns plus cleanup error equal to zero. The resulting `PPWR` magic was read
back successfully.

The `scratch` image is the next isolated phase. Its live installer first reads
and validates the complete `PPWR` state, then carries only that proof magic into
a new state block. On the first FHD frame it requests one 4 MiB non-cache block
with 1 KiB alignment, validates the low DMA handle and all arithmetic/ranges,
checks that it cannot overlap the DNG segment, and writes/reads four guards. It
does not call power, clock, or codec functions and does not modify the DNG or
segment. A complete result leaves `0x434F4C41` (`ALOC`) at `+0xD8`.

If the scratch phase succeeds, the allocation is intentionally retained. The
installer refuses another power or scratch preflight while it is retained,
preventing a second 4 MiB allocation or loss of the only handle. At the time
this staged runbook was written, a live encode/free consumer did not exist;
PHASE=2 and PHASE=3 now do, and their 2026-09-21 evidence supersedes that old
status. The fixed-address sequence below remains historical and is not the
approved next-run procedure.

The common installer retains the exact probe's safety properties:

- it verifies the six firmware words at `0xC0722AF0..0xC0722B04`;
- code and the 256-byte state block are written and read back before the hook;
- the hook is the final write;
- an immediate first hit is recognized and never re-armed;
- restore accepts only this probe's armed word with a valid surrounding context.

The reviewed preflight images extend into known helper slots (`power` ends at
`0xC072FAAC`; `scratch` ends at `0xC072FC3C`, below its enforced
`0xC072FD00` limit). The shell reserves `0xC072F800..0xC0730000` for one
template routine, but the named helpers reuse sub-slots inside it. Do not run
`putfile`, `getfile`, bulk-loader, dump, or another template helper while either
probe is loaded or armed.

The `encode` action remains deliberately **dry-run only**: it is the reviewed
self-allocating design, kept byte-identical at 1268 bytes ending `0xC072FCF4`
for comparison, and it refuses before opening the camera transport.

`encode-live` (PHASE=2) is the armable one. It allocates **nothing**: the host
reads a complete, error-free scratch result and seeds exactly four words — the
`PPWR` and `ALOC` proofs, the allocator and the retained handle — into a fresh
state block. The probe re-derives the entire layout from that handle, repeats
every alias/alignment/range/overlap check, and additionally requires the three
guard words the scratch phase left behind to still be intact. A trampled guard
means the block is not what we think it is: it is then neither encoded into nor
freed. The image is 1420 bytes, ending `0xC072FD8C`, under its `0xC072FE00`
limit and 512 bytes below the shell's reserved end.

Its release policy is deliberately asymmetric, because the engine is a single
global with no ownership arbitration and a wrong free is how the still path was
killed on 2026-08-30:

| Situation | Action |
|---|---|
| adopted, encode never entered | free |
| adopted, encode returned success | free |
| encode entered and did not succeed | **retain** — the codec may still own the buffer; power-cycle to reclaim |

`S_ENC_STARTED` is written *before* the call, so a hang that never returns is
still visible in the state block afterwards.

Historical fixed-address sequence, preserved only to explain the evidence
provenance; do not execute it as a current runbook:

```sh
./single_frame_codec_probe.py preflight     # PPWR
# record 1-2 s FHD CinemaDNG, stop
./single_frame_codec_probe.py status
./single_frame_codec_probe.py scratch       # ALOC, retained
# record again, stop
./single_frame_codec_probe.py status
./single_frame_codec_probe.py encode-live   # consume + free
# record again, stop
./single_frame_codec_probe.py status        # prints the encode report
```

`status` decodes a finished PHASE=2 result into elapsed microseconds (the tick
is a free-running 1 MHz counter, so a wrap is a plain 32-bit subtraction),
Mpix/s against the 41,708 us 24p frame budget, the summed compressed size and
ratio, the twelve tile sizes, whether the sampled source markers were unchanged,
and whether the block was released. **One frame is not sustained throughput**,
and a rate derived from a single tile set is not a worst case.

The reviewed 4 MiB encode layout is:

```text
base + 0x000000 .. 0x304FFF   codec compressed output/work (cap 0x305000)
base + 0x305000               overrun guard
base + 0x306000 .. 0x30602F   final 12-word size table (init[7])
base + 0x306030               size-table guard
base + 0x306400 .. 0x30642F   codec temporary size table
base + 0x306430               temporary-table guard
```

`F_GET` requests 1 KiB alignment directly. The codec receives the low allocator
handle, not the cached alias. In the dry-run encode source, `init[6]` is the
compressed output/work base and `init[7]` is the size-table base; the original
DNG is source-only, and neither its header nor `seg[1]` is written.

Offline verification:

```sh
python3 -m unittest -v \
  test_exact_dng_writer_probe.py \
  test_single_frame_codec_probe.py
```
