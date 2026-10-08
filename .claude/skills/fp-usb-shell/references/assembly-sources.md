# USB shell assembly source guide

Paths in this guide are relative to the `fpSup/` checkout. The maintained ARM
source files live in `fp_usb_shell/asm/`. They are executable
inputs to the host tools and card builder, not a directory of files to copy
unchanged into a new product. Keep the build inputs in that source directory;
put reusable instructions in this skill. Check the actual caller, defines,
ownership and selected build before editing or loading one.

| Job | Sources | Caller or use |
|---|---|---|
| Shared boot chain | `loader.S`, `stage2.S`, `store_boot.S`, `abort.S`, `entries.S`, `splash_finish.S`, `sleeper.S` | `build_autorun.py` and card builders; `stage2.S` includes `splash_finish.S` from the same directory. Follow `SUP_BUILD_RULES.md` and its English counterpart. |
| File transfer | `bulkload.S`, `putfile.S`, `getfile.S`, `dumpraw.S`, `dumpdirect.S` | `putfile.py`, `getfile.py`; use `deploy.py` for a verified card update. `dump.S` is an older, superseded measurement source. |
| Borrowed shell handler | `shellcmd.S`, `callfn.S`, `heapalloc.S`, `lensblock.S`, `taskcreate.S` | Task-context patterns and host probes. `callfn.py` and the existing host tool should normally drive a one-shot call. |
| Callback and bounded probes | `oneshot.S`, `hookprobe.S`, `pathprobe.S`, `pushprobe.S`, `recprobe.S` | Callback-context investigations; inspect the source's assumptions and authorize any live use separately. |
| Resident swap and boot experiment | `park.S`, `gate.S` | `load.py`/`gyro/load.sh` and `warm_gate_card.py` respectively. `gate.S` is a test, not a product entry. |

The shell command handler runs in task context and can block; the gyro callback
is interrupt-like and cannot block or take mutexes. A resident pool thread is
a third route. Check each source's actual borrowed site and ARM ABI. Preserve
LR across calls and 8-byte stack alignment, restore an exact original hook in
`finally`, and publish D/I caches before executing newly written code.

Host tools resolve scratch via `cave.claim()` where implemented. Old fixed
addresses near `0xC072F700` and `0xC072FA00` are historical examples, not
free-memory declarations. Fixed worker rendezvous words cannot be claimed.
Never infer ownership from a zero-filled region. Inspect `cave.ABI`, the tool's
`_LAYOUT`, and the running card before placing data or arming a hook.

Mode 7 in `putfile.S` overwrites an existing file's prefix but does not
truncate an old longer file. Full length and byte-for-byte readback are required
for a card update. See [hot-update.md](hot-update.md).

When changing the shared boot chain, preserve the loader/stage2/entry contract
and run the boot-chain tests named by `SUP_BUILD_RULES.md`. For a new probe,
document measured facts separately from hypotheses, check every firmware
return value, and verify both code upload and restoration paths offline before
any authorized camera trial.
