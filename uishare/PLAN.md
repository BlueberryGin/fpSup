# Plan — sharing the native UI between sups

2026-10-01. A working plan, not a contract. The convention as implemented so
far is in [README.md](README.md). What each existing sup changes today is in
`research/ui/UI_SERVICE_INVENTORY.md`.

## Why

Several sups change the same native UI resources: the string pool, the
Settings pages, the CSV lists, Quick Set and LV. Each of them replaces or
patches those resources in its own way, so when two of them meet on one card,
the second breaks the first.

The first real case (2026-09-30) was lossless and OG3K:

- lossless switched the string pool to its own copy;
- OG3K's string hook only works when the pool is the stock one.

Several developers write sups, so the fix must not be a central registry of
coordinates, and it must not depend on the card composer knowing every sup.

## Principles

1. **Each sup does it itself, at load, through the same small library**
   (`fpSup/uishare`). There is no integrator step. The composer keeps placing
   sections as it does now.
2. **No coordinates are claimed.** String offsets, object IDs, option and row
   indices, and Y positions are handed out at load time. A sup writes what it
   was given into its own data before that data is used.
3. **Shared state describes itself.** Whatever the convention replaces carries
   a header (magic, version, capacity), so the next sup finds it and adds to it.
4. **Append only.** Stock meanings and what other sups added never move.
   Versions only add.
5. **Unknown state is left alone.** That sup's UI addition is missing;
   everything else works.
6. **A sup alone on a card works the same way.** It starts from stock.
7. **Entries only.** Entries run one after another on the loader's task.
   Nothing runs from hooks or other tasks.
8. **Memory: one copy per changed resource, with room to append in place.**
   Resources nobody changes are not copied. Quick Set is not copied whole
   (see below).

## Operations

| operation | for | how |
|---|---|---|
| `uis_intern(string) → offset` | private strings | **done**: `ui_pool`; FSPL copy of the stock pool, with room |
| `add_row(page, fragment)` | a new setting row (lossless; raw-view-like rows) | the page is served from a composed copy (runtime entry redirect); the fragment is relocated at load (IDs, strings, Y); budgets, child count and ModeChange are adjusted |
| `hide_row(page, row)` | removing a stock row | visibility 0 in both modes and later rows move up; records are not deleted |
| `add_option(list, label, value) → index` | a new choice (240 fps, 14-bit, OG3K, RAW) | the CSV is copied and extended; controller max, ListItem instances and per-value events, summary child and its limit, text keys, QS states and LV text are filled in from a parameterised fragment (v = the index handed out); row↔enum and enum↔state tables are merged and the converter's movw/movt repointed (code words declared as sections, so they are journalled) |
| `set_option_enabled(list, index, on)` | greying (Sensor Lab mode gating) | `Enabled,Enabled2` edited in the CSV copy; can change at run time |
| private icons | QS tiles, LV | one shared private NBR pack, instead of each sup registering its own |

What stays inside each sup: the meaning of a value (value-list providers,
normalisers, setting-layer hooks). These are product semantics, not UI.

## Mechanisms to prove on the camera first

1. **CSV redirect.** The NBR directory in the image is dead after boot, but the
   copy on the heap (resource ctx +0xAC/+0xB0/+0xB4) may be live. Test: point
   one CSV entry at a RAM copy, then open the page.
2. **Subpage redirect.** B2_5 and the other Settings subpages through their
   runtime entries, as MainB2 already works (proven 2026-09-30).
3. **Quick Set.** QS layouts are parsed at boot and held. The plan is one
   shared, chainable record hook (`C05E6400`) that patches only the records
   asked for, plus one shared re-parse trigger (generalised from OG's
   `C05D90F0`). QS is not copied whole: it is several hundred KiB.

## Status

| step | state |
|---|---|
| inventory of what every sup changes | done: `research/ui/UI_SERVICE_INVENTORY.md` |
| `ui_pool` (strings) + tests | done: 15 tests, 8 mutations |
| lossless uses `ui_pool` | done: 18 relocated fields; camera OK (card r, 2026-10-01) |
| OG3K accepts an FSPL pool (`og3k_ui.S` ui_string) | done: unicorn test, 5 mutations; existing OG suites pass; needs a new OG release |
| OG3K + lossless merged card | built and on the camera: OG3K menu/QS and the lossless row look normal (operator); 3K lossless take stopped itself (memory, not UI) |
| `add_row`, `hide_row`, `add_option`, `set_option_enabled`, icons | not started; the three camera proofs above come first |
| Sensor Lab + lossless | no section or UI-hook overlap found; Sensor Lab rebuilds B2_5 in place, which `add_option` would replace |

## Order of work

1. Prove the three mechanisms on the camera.
2. Turn lossless's row into `add_row`; it is the only row user today.
3. `add_option` on one real case: Sensor Lab 240 fps (Settings + summary + LV,
   no QS), then OG3K resolution (with QS).
4. Move OG's string aliases to `uis_intern` and drop its `C05E5B58` hook.
5. Release cadence: every sup that adopts the convention needs a new release.
   Until then the composer must mark "a convention sup + an old OG" as
   incompatible.

## Compatibility

- Loader, stage2, AutoRun and the entry ABI do not change.
- Released OG (≤ og3k 0.2.7a, og2k 0.1.4a) accepts only the stock pool, so it
  cannot be combined with a sup that interns a string.
- A sup that does not follow the convention and replaces a shared resource
  itself will still conflict. The convention cannot fix a sup that does not
  use it.
