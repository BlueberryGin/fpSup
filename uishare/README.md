# uishare — sharing the native UI between sups

A convention and the small library that implements it. There is no central
component and nothing for the card composer to do. Every sup that changes a
shared UI resource does it from its own entry, through this code, and the
result is the same whichever order the sups load in, and whether a sup is
alone on the card or merged with others.

## Rules

1. **Nobody claims coordinates.** Offsets, IDs and indices are handed out at
   load time; a sup writes what it was given into its own data before that
   data is used.
2. **Shared state describes itself.** Whatever the convention replaces
   carries a header (magic, version, capacity), so the next sup can find it
   and add to it.
3. **Append only; existing meanings never change.** Stock offsets stay valid;
   nothing added by another sup moves.
4. **Unknown state is left alone.** If a resource is neither stock nor made by
   this convention (another tool, a newer incompatible layout), the call fails.
   Only that sup's own UI addition is missing; nothing else changes.
5. **Call from an entry.** Entries run one after another on the loader's task.
   Nothing here is safe from a hook or from another task.

## Strings — nested layers (2026-10-04)

**Private strings no longer go into the pool.** Each sup's private strings are
one layer of the nested string hooks: a small resident block hung on the
firmware's three string functions (resolve `C05E5B58`, owns `C05E61C8`,
remain `C05E61E0`) in front of whatever was there. A layer answers for its own
offsets (from `0x40000000` up, each after the layer inside it) and its own
bytes, and passes everything else, arguments untouched, inward; the innermost
does what the firmware did. The stock pool, `reader+0x10/+0x14` and every stock
offset stay as they are: nothing is copied, nothing is scanned.

`ui_apply.c` does this by itself for every FPUI block (a string whose
`stock_at` holds gets its stock offset; every other one goes in the block's
layer, which goes up at commit). `STRBASE string#, address` hands an offset to
the sup's own data (OpenGate's `ui_aliases`). A block with any string makes
`build_section.sites()` declare the three sites with their stock words.

Rules, the firmware evidence and the open question for pages:
[NESTED_HOOKS.md](NESTED_HOOKS.md). Code: `ui_strings.S` (one layer, ARM;
`gen_strings_code.py` makes `ui_strings_code.h`), `ui_pool.c`
(`uis_layer_base`, `uis_layer_add`, `uis_layer_fixed`, `uis_stock_has`). Tests:
`test_ui_strings.py` (the layers as ARM on the real image, against the
firmware's own functions, with mutations). `ui/chain.py` reads layers back.

**Whose layer (header v2, 2026-10-06).** A layer's first 8 bytes are the name
of the sup that hung it: the base name of the file Loader v3 loaded it from
(`10LOSS`, `31FMT`). uishare finds the loader's service table itself
(`SL_SVC_AT`, only while entries run) -- callers pass nothing -- and takes the
name from `svc->self()`; a layer already on a site counts only if its name is
one `svc->holder()` lists for that site. Without a running Loader v3 the name
is `FSDL`. v1 and v2 layers do not recognise each other: build every sup on a
card with the same uishare. `uis_layer_fixed` hangs a layer for ids someone
already hard-coded (>= `0xF0000000`, used in place, skipped by later numbering).

## ui_pool — the old string pool (kept for older sups)

`uis_intern(s, n, &offset)` returns the offset at which `s` resolves in the NBU
string pool. If the pool already contains `s` (stock, or added by any sup), it
returns that offset. Otherwise it appends `s`. The first append copies the
stock pool once, with 16 KiB of room, into memory that is never freed, and
switches the reader to the copy.

The header is 16 bytes, immediately before the pool:

| offset | contents |
|---|---|
| −16 | `"FSPL"` |
| −12 | version, 1 |
| −8 | capacity |
| −4 | 0 |

`reader+0x10` is always the length in use.

A sup whose own code checks the pool, as OG's string alias hook does, must
accept either the stock pool or `uis_pool_known()`.

### Boot cost: pass the stock hint

`uis_intern` scans the whole 176,152-byte stock pool once per string, about
0.25 s each on the camera (Sensor Lab, 80 strings: ~20 s of boot). A builder
knows the stock pool -- it is in the pinned image -- so it passes, per string,
where that scan would find it, or `UIS_NOT_STOCK`:

    uis_intern_hinted(s, n, stock_at, &offset)

A hint is checked (n + 1 bytes) before it is used, a wrong one falls back to
the full scan, and `UIS_NOT_STOCK` searches only what sups appended after the
stock bytes. Hinted and unhinted callers get the same offsets.
`fpSup/lossless/menu/pack_menu_file.py` `stock_at()` computes the hint.

Tests: `python3 -B -m unittest test_ui_pool` (host model plus mutations; the
ARM build must have no `.rodata` and no `.text` relocations).

Users: none through `ui_apply` any more (2026-10-04); `fpSup/lcdflip` (retired)
still calls `uis_intern`. Lossless's `menu_page.c` only asks `uis_pool_known`.

## Build

Compile `ui_pool.c` into the sup, with `ui_pool.h` on the include path. It
needs nothing from the rest of fpSup.

`fpSup/lossless/build_card.py` shows a unity build that keeps the code free of
relocations: it makes the public `uis_` functions static and adds the `-I`
path for `uishare`.

## ui_apply — pages, lists and files by addition (2026-10-03)

A sup describes what it ADDS in Python (`ui/`: `rows.add_row`-style blocks,
`options.color_raw`, `options.resolution`); the result is an FPUI block
(`ui/fpui.py` is the format and the reference applier). On the camera
`ui_apply.c` carries it out at boot:

- a page (a resource set with a runtime entry: MainB2, B2_5, ColorButtonMenu,
  ...) is served from a COPY with an `FSPG` header; each sup copies the page
  as it is now, inserts its records and adds to counters, and switches the
  entry when every op succeeded;
- a list's CSV is served from a copy through the shared file-redirect
  handler (`fv_handler.S` at C05E5BEC, table `FSFV`); each sup appends rows;
- object IDs, row indices and string offsets are handed out on the camera.

A sup carries the applier either linked in (Lossless) or as a ready section
(`build_section.py`: section.S + ui_pool + ui_apply + its block) passed as a
`--boot-bin`; declare the sites in `info['sites']` with their stock words.

Tests: `test_ui_apply.py`, `test_options.py` (both C against the reference,
on the pinned image, with mutations). Plan and status: `MENU_MERGE_PLAN.md`.

## Not yet here

The same pattern for adding rows, hiding rows, adding options (Settings,
summary, Quick Set, LV) and greying options out.

See `research/ui/UI_SERVICE_INVENTORY.md` for what each existing sup changes
today.
