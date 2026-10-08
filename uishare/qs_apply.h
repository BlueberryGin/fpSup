#ifndef FPSUP_QS_APPLY_H
#define FPSUP_QS_APPLY_H
#include <stdint.h>

/* The shared Quick Set recipe, applied to one NBU record (QS_SHARE.md §2).
 *
 * The C twin of ui/qs.py: test_qs_c.py runs both on every record and every
 * scenario and compares bytes. Everything here is a pure function of the
 * recipe table (ui/qs.py encode()), the stock record and the layer's (n, k,
 * ids): no globals, no firmware calls, so it compiles -fropi into any sup.
 *
 * Table (little-endian, 4-aligned):
 *   +0 version (1)  +4 row count  +8 rows offset  +12 edits offset
 *   +16 f(N) offset +20 N max     +24 k max       +28 0
 *   row (16 B, sorted by address): address, stock FNV-1a, length (u16),
 *     rule (u8), a (u8), b (u16), edits offset (u16, from the edits area)
 *       clone        a = property + 1 (0: none), b = marker (0xFF: none) | time << 8
 *       end          b = time
 *       qs_max       -
 *       cursor       a, b = the two offsets of the clip name
 *       footer_n8    edits
 *       footer_label a = k, edits
 *   edits: {u16 at, u16 delete, u16 n} n bytes, padded to 2;
 *          n = 0x8000 | slot: the marker's id, 4 bytes big-endian; at = 0xFFFF ends
 *   f(N) (12 B for N = 0..N max): qs_max (float bits, 0 = unsupported),
 *          cursor clip (pool offset), footer n8 (u8), verified (u8), 0 (u16)
 */
#define QS_TABLE_VERSION 1u
#define QS_BUFFER        512u           /* largest record after every rule: 488 (test_qs) */

enum qs_rule { QS_CLONE = 0, QS_END = 1, QS_MAX = 2, QS_CURSOR = 3, QS_FOOTER_N8 = 4,
               QS_FOOTER_LABEL = 5 };
enum qs_outcome { QS_SKIP = 0, QS_DONE = 1, QS_WROTE = 2, QS_CONFLICT = 3, QS_ERROR = 4 };

/* Row index + 1 of the record at stock address `src`, 0 if not in the table. */
uint32_t qs_find(uintptr_t tab, uint32_t src);

/* FNV-1a of n bytes. */
uint32_t qs_fnv(uintptr_t p, uint32_t n);

/* One layer on one record. `stock` = the record's stock bytes (row length),
 * `cur`/`*cur_len` = what the parser would get now (may be the stock bytes),
 * `scratch` = QS_BUFFER bytes. ids[0..2] the shared images, ids[3] this
 * layer's own SET image. On QS_WROTE the new bytes are in scratch, *cur_len
 * their length; the caller puts them where the parser reads. */
uint32_t qs_layer(uintptr_t tab, uint32_t row, uintptr_t stock, uintptr_t cur, uint32_t *cur_len,
                  uintptr_t scratch, uint32_t n, uint32_t k, const uint32_t ids[4]);

/* 1 if N is in the table (supported), 0 if not. *verified: measured on a camera. */
uint32_t qs_n_supported(uintptr_t tab, uint32_t n, uint32_t *verified);
#endif
