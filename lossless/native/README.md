# fpLossless v5.02 native variable call layer and binding-ops port

Actual ARM32 C wrappers around the verified native Thumb entrypoints. This is
**not an installed hook, complete binding port, renderer or recording product**.
No camera or USB operation is provided. Test compilation emits only a temporary
object; no VSHL, AutoRun, installer, code-cave reservation or merged card.

## REC workspace implementation

The recording workspace is separate from the deferred UI code below:

- `workspace_layout.c` computes one 1 KiB-aligned arena from actual geometry,
  packed bit depth, metadata and four independent codec/TIFF table spans. An
  optional source copy is budgeted separately. The output span is a
  benefit-only budget, **not a worst-case JPEG or enforced DMA bound**.
- `raw_workspace.c` uses mode 3, RAW/class 10 and the native descriptor get/free
  entries `C001CFD8` / `C001D3D0`, rather than the old class-0 probe blocks.
  It retains allocator-granted capacity and take ownership. Stale take,
  uncertain drain, malformed descriptors and mode changes cannot free memory.
- `rec_workspace.c` calls original `C03A2438`, plans and reserves, rechecks
  format/readiness/source headroom, then enters direct control and the existing
  one-frame pipeline. Stop does not free; finish requires the same take, an
  empty slot and caller-proven quiescence. Start failure/cancellation uses the
  same finish path before native memory-map teardown.

- `producer_facts.c` is the `fpl_rec_workspace_facts` provider. It reads the
  producer the way the kind-1 creator does (`C0021C40(C0021C00())`, then
  `C0437140(&obj, 2, settings, 3)`, width/height/format at `obj[0]/[1]/[8]`),
  sizes the raster with `C0135D40` and takes the rate from `C00C9BD0`, refusing
  codes that fall to its default. It never adds readiness. CINE, CinemaDNG and
  SD come from caller probes and refuse when unknown.
- `rec_hook.c` is the body behind `C03A33C8`: event 3 goes to the adapter, every
  other request -- and any request when our state is broken -- reaches the
  original `C03A2438` with the same arguments. No anchor, launcher or journal
  declaration is supplied yet.

The four-argument REC adapter is not the two-argument installed hook. It needs
owned resident state, a real producer-facts provider and serialized native
REC/failure/stop integration. No hook installer, source lease, compressor or
DMA-bound proof is supplied. Only traced event 3 belongs at this adapter;
unrelated native events must retain their original route. 14-bit layout
calculation does not bypass the existing recording rejection.

Offline build, following `SUP_BUILD_RULES.md` and without creating a card:

```sh
python3 -B lossless/native/build_workspace_offline.py --out /absolute/empty/output-directory
```

Run from `fpSup` with clang, Python and Unicorn available. This writes five
unlinked ARM objects, logs, `ledger.json` and `SHA256SUMS`; it refuses a nonempty
output directory. It runs layout 14 cases, RAW 40 inner cases, REC integration
14 cases, control 33 cases and frame pipeline 26 cases, with zero skips in the
2026-09-30 run. The three workspace suites also pass under Python `-O`.
RAW tests execute original firmware descriptor wrappers with physical
allocation and mode/pool selection substituted. Full REC ARM execution remains
unverified: its linked cross-module calls cannot be tested by discarding ELF
relocations. REC integration executes real compiled C on the host instead.
Evidence: [workspace ledger](../../../projects/lossless-sup/build/workspace-offline-20260930-TBQxXV/ledger.json).

## Reproduce

With clang and an existing Unicorn 2.1.4 environment:

```sh
python3 -B /absolute/path/fpSup/lossless/native/test_native_variable.py \
  --seg0 /absolute/path/out/seg0_c0000000.bin \
  --shared-probe /absolute/path/research/ui/tools/native_variable_probe/binding_probe.py \
  --elf-helper /absolute/path/research/ui/tools/arm_text/elf_text.py
```

The test compiles this exact C as freestanding ARMv7 Thumb soft-float, rejects
unresolved symbols or text relocations, maps its code RX and executes it into
the pinned original firmware instructions. The current 25 cases pass with zero skips;
r4-r11 and SP are checked after calls, with 20000-instruction / 250 ms bounds.
Original firmware is RX; writes stay in synthetic RAM. The resulting code is
3268 bytes, SHA-256
`7bf9a88e4581cd521cc217874e089e5c3ba0ddb642838227bafaccfa05d0a06a`.

Original Lua registration, heap, locks, strcmp and callback services remain
substituted, and native widget vectors are empty. Thus register/set/subscribe
call shapes execute, but complete UI rendering and real scheduling do not.
Current evidence: `projects/lossless-sup/build/native-menu-offline-20260930-59qpxF/native-variable.log`.
The older `native-variable-port-20260916.json` records the previous 23-case source.

## Implemented

- `fp_nv_init`: accepts only fresh storage and the three exact private names
  `MV_fpLossless`, `MV_fpLosslessCursor`, `MV_fpLosslessConfirm`; rejects suffixes,
  prefixes, stock names and five changed native function prologues.
- `fp_nv_inspect`: treats successful lookup plus NULL as absent, not found.
- `fp_nv_register_off`: default OFF, one-time ownership claim, no adoption of
  another descriptor and no blind retry after partial native failure.
- `fp_nv_read`: checks descriptor/name/type and subscription identity.
- `fp_nv_subscribe`: verifies the copied pair and retains callback context on
  uncertain failure; duplicate/foreign owners are not overwritten.
- `fp_nv_set_canonical`: accepts 0/1 only, checks ownership before/after calling
  the original named-array setter, then checks readback. The quiet variant
  suppresses app callbacks, **not** native component dispatch.
- `fp_nv_unsubscribe_locked`: verifies exact pair/callback/context before
  calling native removal. It can clean up a failed subscription with no pair
  but never frees descriptor, name or callback context.

Results use `FP_NV_*`, not `FPL_*`; `last_native` retains an encountered native
error. A future binding port must explicitly translate these results.
A failed native mutation may already have published state. Faults are sticky;
do not clear state to retry. Cleanup does not clear the fault.

## `native_port.c`: the `fpl_binding_ops` adapter

`native_port.c` wires these calls into the coordinator's port contract, so a
native variable event now reaches `fpl_binding_notify` and policy decides the
outcome. It is still **not** a row, a renderer, a hook or a recording product.

- `fpl_port_init` takes the app, the retained `MV_fpLossless` storage, an
  optional producer-facts provider and an optional exclusion/quiescence
  provider. `fpl_port_bind` attaches the coordinator before the first attach;
  a port with a live subscription never changes coordinator.
- `fpl_port_translate` is the explicit FP_NV_* -> FPL_* map. It never returns
  FPL_OK for a native error and never returns FPL_BUSY, which would invite a
  retry after a possibly-published partial mutation. Faults are sticky, and
  only `unsubscribe`/`quiesce` stay reachable while faulted.
- `context` always strips `FPL_READY_UI`. An integer write plus readback is not
  a rendered view, so ON stays unselectable through this port no matter what
  the facts provider claims. RAW recording is unaffected.
- `publish` applies the canonical value and, when prepared, confirmation
  permission. Visibility, per-choice grey rendering and reason text remain
  unapplied; `presentation_unapplied` is not proof of a displayed view.
- The callback trampoline forwards only events for the claimed descriptor, and
  forwards the claimed handle rather than an unchecked pointer. Its own write
  echo is suppressed; a nested notification is refused and faults the port,
  because serialization is the caller's contract and clearly did not hold.
- Without providers, `notification_enter` and `quiesce` report FPL_BUSY and
  acquire nothing, so close/reap fail closed and the ticket, callback code and
  context stay alive. `fpl_port_release` drops the retained ticket only after
  quiescence was proven **and** the coordinator finished its own retirement.

Reproduce (no camera, no emulator needed):

```sh
python3 -B /absolute/path/fpSup/lossless/native/test_native_port.py
```

22 scenarios run twice each, in-process at -O2 and again as an ASan/UBSan
executable, plus an ARMv7 Thumb soft-float compile. The real coordinator, UI
policy and control sources are used; only the ARM32-only `fp_nv_*` layer is
substituted, keeping its checked semantics. Four injected defects (a kept
`FPL_READY_UI`, a dropped exclusion requirement, an invented drain proof and an
admitted nested notification) each fail the suite. Evidence:
`projects/lossless-sup/build/native-binding-ops-port-20260920.json`.

Unlike `native_variable.c`, this adapter has a function-pointer table and so
needs relocated read-only data: installing it requires a real link/loader step,
not a relocation-free text copy. Its only external symbols are the seven
`fp_nv_*` entries, `fpl_binding_notify` and `fpl_binding_refresh`; it pulls in no libc, no floating
point and no compiler runtime.

## Private cursor and confirmation permission

`fpl_port_prepare_permissions` registers exact Cursor and Confirm variables at
zero before attach/private-page parsing. Names and state remain alive for the
native registry lifetime; a partial registration is retained and never retried.
The optional resource profile binds Cursor to List controlValue.value in both
directions, but Confirm to Right/Enter keyEvent.type in the app-to-property
direction only. Saved preference remains `MV_fpLossless`.

An owned, current Cursor callback invokes refresh with fresh producer facts,
not selection. Confirmation closes before reading those facts, before canonical
publication and before attempting close exclusion. Only a visible, permitted
choice reopens it. Recording, unreadable facts, reentry and incomplete close
leave it revoked, or explicitly uncertain if the native revoke itself fails.
Final cursor readback catches a synchronous move during permission publication.
Both subscriptions must be removed and drained before releasing the ticket.

`test_native_permissions.py` runs 23 compiled-C scenarios at -O2 and with
ASan/UBSan, plus one test killing four isolated mutations. Native registry and
exclusion providers are declared substitutes in that host suite.
`research/ui/tools/native_control_probe/test_native_permission_binding.py`
separately executes six original-instruction cases: A2P attach, integer property
storage, Enter/Right dispatch, read-only binding, cursor P2A and a real-instruction
mutation. Framework boundaries remain stubbed; this is not full-page rendering.

Crucially, native `phase=3` repeats can dispatch even with keyEvent.type=0.
Permission publication is therefore not event drain or the final authorization
gate. Saved-value callbacks still recheck policy and restore the canonical value.
This implementation **never grants `FPL_READY_UI`** and ON remains unavailable.

## Mandatory external contracts / remaining work

Every call requires a live initialized v5.02 app, valid separate caller-owned
buffers and serialized registry/GUI ownership. Name storage is borrowed for the
whole registry lifetime. Prologue guards are conflict checks, not a replacement
for full firmware identity verification.

Before `unsubscribe_locked`, the caller must already exclude ALL native
notifications and subscriber changes. The native internal mutation lock does
not provide that guarantee. Afterward it must drain queued source events and
callback users before releasing code/context. This module supplies neither
lock acquisition nor quiescence; its tests use serial emulation only.

Callback-to-policy glue and the private confirmation variables now exist in
`native_port.c`. Full rendered-view publication, actual exclusion/quiescence
providers and ownership/queue integration remain pending. Value
readback alone must never set `FPL_READY_UI`. The safe recorder entry remains `fpl_binding_begin`, with all
codec/writer/header/playback/storage proofs still required.
