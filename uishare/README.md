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

## ui_pool — the string pool

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

Tests: `python3 -B -m unittest test_ui_pool` (host model plus mutations; the
ARM build must have no `.rodata` and no `.text` relocations).

Users: `fpSup/lossless/native/menu_page.c`.

## Build

Compile `ui_pool.c` into the sup, with `ui_pool.h` on the include path. It
needs nothing from the rest of fpSup.

`fpSup/lossless/build_card.py` shows a unity build that keeps the code free of
relocations: it makes the public `uis_` functions static and adds the `-I`
path for `uishare`.

## Not yet here

The same pattern for adding rows, hiding rows, adding options (Settings,
summary, Quick Set, LV) and greying options out.

See `research/ui/UI_SERVICE_INVENTORY.md` for what each existing sup changes
today.
