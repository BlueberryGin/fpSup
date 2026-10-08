#ifndef FPSUP_UI_APPLY_H
#define FPSUP_UI_APPLY_H
#include <stdint.h>

/* Carry out one sup's FPUI block (uishare/ui/fpui.py is the format and the
 * reference; this does exactly what its apply() does).
 *
 * A page a block touches is served from a COPY of whatever the page is now
 * (the stock page, or a copy another sup made), with the block's additions
 * applied; only when every op succeeded is the page's runtime entry switched
 * to the new copy. A failure leaves the UI exactly as it was. Copies are
 * never freed: the UI may still hold strings or records of an older one.
 *
 * Every copy starts at the same alignment as its source (so it is copied a
 * word at a time) and carries a 128-byte header at H = (page & ~3) - 128,
 * and before that its insertion table, so the next sup finds it and adds to it:
 *
 *   H+0   "FSPG"   H+4 version 1   H+8 capacity
 *   H+12  length   H+16 stock length   H+20 next object id
 *   H+24  insertions   H+28 insertion capacity
 *   H+32  address of the insertion table ({stock pos, bytes} each)
 *   H+36.. 8 counters {name hash, count}, then zeros
 *
 * Call from an entry (the loader's task), never from a hook. */

#define UIA_MAGIC     0x47505346u      /* "FSPG" */
#define UIA_VERSION   1u
#define UIA_HEADER    128u
#define UIA_COUNTERS  8u
#define UIA_MAX_SLOTS 8u

enum uia_result {
    UIA_OK = 0, UIA_BLOCK = 1,        /* not an FPUI v1 block, or damaged */
    UIA_NO_UI = 2,                    /* no reader / no such page entry */
    UIA_PAGE = 3,                     /* the page is neither stock nor a convention copy */
    UIA_GUARD = 4,                    /* the page does not hold what the block expects */
    UIA_NO_MEMORY = 5, UIA_STRING = 6, UIA_OP = 7, UIA_FULL = 8,
    UIA_HOOK = 9,                     /* the file-redirect site holds someone else's code */
    UIA_CONFLICT = 10                 /* two sups replace the same whole file */
};

struct uia_outcome {
    uint32_t result, op;              /* which op failed (index) */
    uint32_t first_id;                /* the block's first object id (last page) */
    uint32_t slots[UIA_MAX_SLOTS];    /* what ALLOC handed out, in order */
    uint32_t n_slots;
    uintptr_t page;                   /* the copy switched in (last page) */
};

uint32_t uia_apply(uintptr_t block, uint32_t bytes, struct uia_outcome *out);

/* Prefer already-owned, persistent USER memory for this block's copies. The
 * caller must keep [arena, arena + arena_bytes) alive for the whole boot and
 * separate from the FPUI input. If it is too small, the usual allocator is
 * used so a valid menu does not disappear on a smaller launcher block. */
uint32_t uia_apply_in_arena(uintptr_t block, uint32_t bytes, struct uia_outcome *out,
                            uintptr_t arena, uint32_t arena_bytes);

#if defined(UIA_HOST_TEST)
struct uia_natives {
    uint32_t (*read)(uintptr_t);
    void (*write)(uintptr_t, uint32_t);
    uint32_t (*read_byte)(uintptr_t);
    void (*write_byte)(uintptr_t, uint32_t);
    uintptr_t (*alloc)(uint32_t bytes);        /* 8-aligned, never freed; 0 fails */
    void (*publish)(void);
    void (*icache)(void);                      /* I-cache invalidate (C000EABC) */
};
extern const struct uia_natives uia_test_natives;
#endif
#endif
