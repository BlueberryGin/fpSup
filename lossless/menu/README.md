# Lossless RAW — native fourth-row offline candidate

This is a real binary page transform for the fpLossless product, not a mockup.
It adds a CINE-only `Lossless RAW` row with literal `OFF` / `ON` choices to a
private copy of MainB2. Its status remains **BLOCKED_NOT_DEPLOYABLE**.
No camera access, transport, firmware image, VSHL, AutoRun or installer is used.

## Build and test

Python 3.9+ and the canonical `research/ui/tools/native_ui_audit.py` are required.
Both CLI and Python API default to `pure-fixed-gated`. `pure-select-gated` and
`audio-gated` remain explicit historical comparison profiles.

```sh
python3 -B /absolute/path/build_menu_candidate.py \
  --seg0 /absolute/path/seg0_c0000000.bin \
  --audit-module /absolute/path/research/ui/tools/native_ui_audit.py \
  --output /absolute/path/new-candidate-directory

python3 -B /absolute/path/test_menu_candidate.py \
  --seg0 /absolute/path/seg0_c0000000.bin \
  --audit-module /absolute/path/research/ui/tools/native_ui_audit.py
```

The output directory must be new; existing directories are never overwritten.
The audit dependency is resolved relative to the tool, not the working directory:
canonical `fpSup/lossless/menu/` and the two-level staging layout are supported.
Use the explicit `--audit-module` option for other layouts. The shared module
is loaded without writing a cache alongside it. Tests use system temporary
directories; without `--seg0`, firmware tests explicitly skip.

Source pin: 49,245,696 bytes, SHA-256
`aaa5208a028d9c4aebb9cc8614add723d456e96b2a95914f433079954320e622`.
The hash is checked before any transformation.

## Outputs

- `MainB2.fpLossless.gated.page`: complete private record stream. This is not
  a firmware image or a standalone NBU; no loader/index adapter is included.
- `MainB2.fpLossless.strings`: byte-identical original string-pool prefix plus
  private strings. Old offsets keep their meaning in this independent pool.
- `manifest.json`: source/output hashes, all record locations/provenance, typed
  object and string fields, scalar edits, typed byte insertions, budgets,
  shared-dependency fingerprints and unresolved runtime gates.

## Default fixed-popup transform

The donor is MainY4's two-choice `MenuItem_Select`, not `MenuItem_SelectJump`:
`C216C8B8..C2172BC6`, SHA-256
`33eca8104f7ab0630c7544e9769f6daaa7d0bc6c62856805379ea9ef0d70b48b`.
Its original labels are not OFF/ON; this profile explicitly replaces them.

- Start with a donor of 21 objects / 148 records; remove two shared-width
  records for a 146-record private row. Allocate new IDs after checking the complete
  MainB2-plus-donor namespace, bounded to 16 bits. Current IDs are 34494..34514.
- Explicitly reparent wrapper 196 → MainB2 B2 object 116; set B2 child count 7.
  Other foreign-page object references are rejected except four exact shared
  Footer / HeaderActiveTab callsites, guarded by object identities and six
  byte-identical animation-group/clip fingerprints.
- Invert wrapper visibility to STILL-hidden / CINE-visible, move y=162 → 243,
  append its clip to MainB2 ModeChange. Existing ModeChange budget allows 7.
- Remap every typed object reference, including unaligned property fields.
  Event tag `10006` owner is +24; most other donor components use +20.
  Component-local IDs and coincidentally equal numeric values are not replaced.
- Redirect five typed `ST_CableRelease` fields to private `MV_fpLossless`.
  Replace popup labels, row title, summary and corresponding text-animation
  keys with literal `OFF`, `ON`, `Lossless RAW`; resolver flags are set to 0.
- Explicitly set List owner 19463 `controlValue` min=0/max=1/value=0/loop=1.
  This adds 12 bytes, separately recorded in `typed_insertions`. Root max=7
  is an animation/state index and remains untouched.
- Preserve all original allocation reservations and add donor requirements.

| Result | Pure-fixed candidate |
| --- | ---: |
| Page bytes / records | 179725 / 1538 |
| New row bytes / objects | 25269 / 21 |
| Total objects | 214 |
| Header bytes | 3288 |
| Group / clip / property budget entries | 58 / 197 / 396 |

Only three original records change: header, ModeChange and B2 child count.
Every original Audio-row byte is unchanged. The comparison `audio-gated`
profile instead produces 181976 bytes / 1579 records / 216 objects.

The fixed profile uses `verify_recipe.py` before remapping. It explicitly sets
popup width 320 in base geometry and freezes all six relevant animation clips.
It removes the shared `submenu_width` event/action and decrements their native
component budgets. The focus sync action selects mask `0x22` with both boolean
fields false: omitting the fields would retain the native true default. No new
width variable or unresolved row-state request is created. Width is a design
choice; camera font fit and navigation remain untested.

Run the 35-test builder suite above; run 8 additional recipe tests with
`FPLOSSLESS_SEG0=/absolute/path/seg0_c0000000.bin python3 -B test_recipe.py`.
Product evidence: `projects/lossless-sup/notes/FIXED_POPUP.md`.

## Schema and allocation evidence

All 18 normal donor component schemas are checked against pinned native
little-endian 12-byte `{type,name_pointer,default}` descriptors; animation clips
and groups have dedicated typed decoders. The schemas match canonical
`research/ui/tools/component-schemas-v502.json`. Drawables append native common
`rect` and `layout` descriptors to their declared fields. Serialized values are
big-endian; type B includes a pool offset and one-byte flag, allowing unaligned
subsequent fields. Masks beyond the descriptor count are rejected, and every
known property body must be consumed exactly. The default profile has no opaque
component-property bodies. This proves serialization coverage, not UI behavior.

Static tracing of `FUN_c05e6400`'s allocation-header branch shows it sums all
budgets into one arena-size request without retaining the arrays or a per-record
budget cursor. Appending donor budgets is therefore order-independent in that
aggregate calculation. Native allocation success/capacity was not exercised.

## Remaining gates — do not install

The default fixed profile removes the shared-width/row-state gate without
sending that request; the older pure-select profile still isolates it as
`fpLossless_UNRESOLVED_row_state`. Five default resource gates remain.
Private variable registration and callback lifetime, native navigation, literal
font/layout rendering, private-page loader/rebuild timing and allocator capacity
also remain untested or unimplemented. No ARM/THUMB call wrapper is emitted.

The manifest distinguishes `fourth_row_in_serialized_graph: true` and
`pure_select_widget_structure: true` from `deployable: false` and
`pure_toggle_runtime_verified: false`. It must not be included in an installable
release or fp-Merge card until those integration gates are independently closed.

## Native permission candidate

Use `--profile native-permission-gated` explicitly for the current integration
candidate. The default `pure-fixed-gated` bytes and pins stay unchanged.
This profile adds three typed native `appVariableEvent` records, no new object:

| Private variable | Native destination | Direction |
| --- | --- | --- |
| `MV_fpLossless` | Existing saved-value actions | Unchanged |
| `MV_fpLosslessCursor` | List 34497, controlValue 3.value | A2P and P2A |
| `MV_fpLosslessConfirm` | ListItem 34498, Right 10.type and Enter 12.type | A2P only |

Cursor and Confirm initially equal zero. Right/Enter records explicitly start
with type=0. Popup min/max remains 0..1, root max=7 remains an animation index,
and ESC/Left/Up/Down, saved-value actions and every original Audio byte stay
unchanged. Disabling a visual child or clamping popup max to zero is not used.

Page: 179907 bytes, 1541 records, 3288-byte header, 214 objects; SHA-256
`e602a154162790287fed8a182e07fda0af5d02509b59300a25725fe923c71dea`.
Pool: 176300 bytes, original 176152-byte prefix unchanged; SHA-256
`3ff3ea5264f8a16aec7fac30160cc8065dfe1d2b896fb7b40c3212de5cf7d3ba`.
List component count grows 7→8, ListItem 10→12, header appVariableEvent budget +3.

Run `test_permission_candidate.py` with the same `--seg0`/`--audit-module`
arguments as above: 17 tests including eight isolated binary mutations. The
existing 35 candidate and eight recipe tests still pass. This proves exact
serialization/preservation, not successful native page construction or display.
The manifest keeps variable registration, runtime permission and rendering
unverified; separate component probes cannot close full-resource runtime gates.
