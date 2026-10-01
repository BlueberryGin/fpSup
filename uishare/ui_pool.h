#ifndef FPSUP_UI_POOL_H
#define FPSUP_UI_POOL_H
#include <stdint.h>

/* Shared UI string pool — a convention every sup follows by itself.
 *
 * The native UI resolves every string in an NBU record as an offset into ONE
 * pool: reader+0x14 (address), reader+0x10 (length), checked by C05E5B58
 * (offset < length) and C05E61C8/C05E9008 (a pointer inside [pool, pool+len)
 * is borrowed, anything else is copied). A sup whose UI records need a string
 * the firmware does not have gets an offset from uis_intern() at load time and
 * writes it into its own records before they are used. Nobody claims offsets
 * in advance; the order the sups load in does not matter; a sup alone works.
 *
 * The pool as the first sup finds it is the stock one (C18C0474, 176152
 * bytes). The first intern that needs a new string copies it once into memory
 * that is never freed, with room to spare, and switches the reader to the
 * copy. Every copy carries a 16-byte header immediately BEFORE its first byte:
 *
 *     pool-16  "FSPL"      magic
 *     pool-12  version     1; a newer layout only ever adds after this header
 *     pool-8   capacity    bytes available from pool[0]
 *     pool-4   0
 *
 * and reader+0x10 is always the number of bytes in use. Existing offsets never
 * change meaning: the stock bytes are the prefix, strings are only appended.
 * When the room runs out a larger copy replaces it; the old one stays, since
 * the UI may still borrow strings from it. Any other pool is left alone and
 * the call fails: that sup's own strings are missing, nothing else changes.
 *
 * All sups run their entries one after another on the loader's task, so there
 * is no concurrent writer. Call this from an entry, not from a hook. */

#define UIS_MAGIC        0x4C505346u    /* "FSPL" */
#define UIS_VERSION      1u
#define UIS_HEADER       16u
#define UIS_STOCK_POOL   0xC18C0474u
#define UIS_STOCK_LEN    176152u
#define UIS_HEADROOM     0x4000u        /* 16 KiB beyond the first need */

enum uis_result { UIS_OK = 0, UIS_NO_READER = 1, UIS_UNKNOWN_POOL = 2,
                  UIS_NO_MEMORY = 3, UIS_INVALID = 4 };

/* The shared NBU reader, or 0 if the UI is not what Ver.5.02 builds. */
uintptr_t uis_reader(void);

/* 1 if `reader`'s pool is the stock one or a pool made by this convention. */
uint32_t uis_pool_known(uintptr_t reader);

/* The offset at which the NUL-terminated `s` (length `n`, without the NUL)
 * resolves, adding it if no string in the pool already ends that way. */
uint32_t uis_intern(const char *s, uint32_t n, uint32_t *offset);

#if defined(UIS_HOST_TEST)
struct uis_natives {
    uint32_t (*read)(uintptr_t);
    void (*write)(uintptr_t, uint32_t);
    uint32_t (*read_byte)(uintptr_t);
    void (*write_byte)(uintptr_t, uint32_t);
    uintptr_t (*alloc)(uint32_t bytes);        /* 8-aligned, never freed; 0 fails */
    void (*publish)(void);
};
extern const struct uis_natives uis_test_natives;
#endif
#endif
