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
                  UIS_NO_MEMORY = 3, UIS_INVALID = 4,
                  UIS_HOOK = 5 };                /* a string site holds code that is not a layer */

/* The shared NBU reader, or 0 if the UI is not what Ver.5.02 builds. */
uintptr_t uis_reader(void);

/* 1 if `reader`'s pool is the stock one or a pool made by this convention. */
uint32_t uis_pool_known(uintptr_t reader);

/* The offset at which the NUL-terminated `s` (length `n`, without the NUL)
 * resolves, adding it if no string in the pool already ends that way. */
uint32_t uis_intern(const char *s, uint32_t n, uint32_t *offset);

/* The same, without scanning the stock pool (2026-10-03). Each scan of the
 * 176,152 stock bytes cost about 0.25 s on the camera, once per string, at
 * boot; a builder knows the stock pool (it is in the pinned firmware image)
 * and passes, for each string, where uis_intern would find it there:
 *
 *   stock_at = the first offset i with pool[i..i+n) == s and pool[i+n] == 0,
 *              or UIS_NOT_STOCK if the stock pool has no such place.
 *
 * A stock_at is checked (n + 1 bytes) before it is returned; if it does not
 * hold, this falls back to uis_intern. UIS_NOT_STOCK searches only what was
 * appended after the stock bytes. The offset returned is one uis_intern could
 * have returned, so hinted and unhinted callers stay consistent. */
#define UIS_NOT_STOCK    0xFFFFFFFFu
uint32_t uis_intern_hinted(const char *s, uint32_t n, uint32_t stock_at, uint32_t *offset);
/* Many resident C strings at once, published with one pair of cache cleans
 * (not one pair per string): offsets[i] as uis_intern would give s[i].
 * stock_at: 0 (search everything) or UIS_NOT_STOCK (none is a stock string:
 * search only what was appended after the stock bytes). */
uint32_t uis_intern_many(const char *const *s, uint32_t count, uint32_t stock_at, uint32_t *offsets);

/* ---- nested string layers (NESTED_HOOKS.md, 2026-10-04) -------------------
 *
 * The way to add strings now: nothing is copied and the pool is never touched.
 * Each sup's private strings live in a layer of its own, hung on the firmware's
 * string functions (resolve C05E5B58, owns C05E61C8, remain C05E61E0) in front
 * of whatever was there; a layer answers for its offsets [base, base+count)
 * and its bytes, and passes everything else inward. The innermost layer does
 * what the firmware did. Offsets are handed out at load time, from
 * UIS_LAYER_FIRST up, each layer after the one inside it.
 *
 * uis_layer_base: where the next layer's offsets start, or UIS_HOOK if a site
 *   holds anything other than its stock word or a layer (then nothing may be
 *   added; nothing has been changed).
 * uis_layer_add: install a layer holding s[0..count) (s[i] has n[i] bytes and a
 *   NUL; 0 < n[i] < 64); string i resolves at *base + i. The strings are copied.
 * uis_stock_has: 1 if the reader's pool holds s (n bytes + NUL) at stock_at, a
 *   stock offset a builder computed (the stock bytes never move).
 *
 * Whose layer (header v2, 2026-10-06): the first 8 bytes of a layer are the
 * name of the sup that put it there -- the base name of the file Loader v3
 * loaded it from ("10LOSS", "31FMT"), NUL-padded. While a Loader v3 runs
 * entries it publishes its service table (sloader.h SL_SVC_AT); then the name
 * comes from svc->self(), and a layer found on a site counts only if its name
 * is a sup the loader's books list as holding that site (svc->holder()).
 * Without a running Loader v3 (an AutoRun/stage2 card) the name is
 * UIS_LAYER_LEGACY ("FSDL") and only that name is recognised.
 *
 * uis_layer_fixed: a layer for offsets a sup already hands the firmware
 *   (fixed ids from code there is no source for): [base, base+count) must lie
 *   at or above UIS_LAYER_FIXED and clear of every layer already in the chain;
 *   string i is the word at table + 4*i, used in place (not copied: table and
 *   strings must live as long as the boot); owns/remain answer for [lo, hi).
 *   Offsets handed out later skip it. */
#define UIS_LAYER_FIRST  0x40000000u
#define UIS_LAYER_FIXED  0xF0000000u    /* boot-issued offsets stay below this */
#define UIS_LAYER_LEGACY 0x4C445346u    /* "FSDL": the name without a Loader v3 */
#define UIS_LAYER_VERSION 5u            /* v5 (2026-10-07): self-describing -- the header holds
                                           its length and its entries' offsets, each entry is
                                           preceded by its offset; code changes no longer need a
                                           new version. Fields are only ever added at the end
                                           (read past +72 only if H_LEN says so); the version
                                           changes only if a field's meaning changes. */
#define UIS_LAYER_NAME_BYTES 8u
uint32_t uis_layer_base(uint32_t *base);
uint32_t uis_layer_add(const uintptr_t *s, const uint32_t *n, uint32_t count, uint32_t *base);
uint32_t uis_layer_fixed(uintptr_t table, uint32_t count, uint32_t base, uintptr_t lo, uintptr_t hi);
uint32_t uis_stock_has(uintptr_t s, uint32_t n, uint32_t stock_at);

/* Quick Set "current format" images (QS_SHARE.md §4.3). Three offsets, handed
 * out once per boot by the first QS sup (the first holder of UIS_RES_QSCUR,
 * a resource every QS sup claims SHARED_UI), name the big tile's third-state
 * images; each QS sup's layer answers them with its own three names while the
 * resolution value is its enum.
 *   uis_qs_shared: *qsid = the three offsets' first, found in the layer of the
 *     sup the books list first for UIS_RES_QSCUR; UIS_HOOK if that sup has no
 *     such layer yet (then *qsid = 0 and *issuer = 1 if that sup is this one:
 *     it hands them out itself, from its own strings).
 *   uis_layer_add_qs: uis_layer_add, the layer answering [qsid, qsid+3) with
 *     strings own..own+2 of this layer while the value is `enum_value`. */
#define UIS_RES_QSCUR 0x55435351u      /* "QSCU": a claim_res id */
uint32_t uis_qs_shared(uint32_t *qsid, uint32_t *issuer);
uint32_t uis_layer_add_qs(const uintptr_t *s, const uint32_t *n, uint32_t count, uint32_t *base,
                          uint32_t qsid, uint32_t enum_value, uint32_t own);

#if defined(UIS_HOST_TEST)
struct uis_natives {
    uint32_t (*read)(uintptr_t);
    void (*write)(uintptr_t, uint32_t);
    uint32_t (*read_byte)(uintptr_t);
    void (*write_byte)(uintptr_t, uint32_t);
    uintptr_t (*alloc)(uint32_t bytes);        /* 8-aligned, never freed; 0 fails */
    void (*publish)(void);
    void (*icache)(void);                      /* I-cache invalidate (C000EABC) */
};
extern const struct uis_natives uis_test_natives;
/* A running Loader v3, as a host model has it: model addresses of
 * NUL-terminated paths, like svc->self / svc->holder.  Weak and 0 in
 * ui_pool.c (no loader, the legacy name), so a fixture that does not model
 * one needs nothing; ui_apply_fixture.c defines them. */
extern uintptr_t (*uis_test_loader_self)(void);
extern uintptr_t (*uis_test_loader_holder)(uintptr_t addr, uint32_t i);
#endif
#endif
