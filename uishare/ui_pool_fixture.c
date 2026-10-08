/* Host substitutes for ui_pool.c: the stock pool at its firmware address, the
 * GUI word, one app with a screen list and the shared reader, and an
 * allocator handing out fresh heap blocks. Never linked into a camera build. */
#include <string.h>
#include "ui_pool.h"

#define NBU       0xC18C0460u
#define NBU_SIZE  0x30000u
#define GUIW      0xC37B7048u
#define HEAP      0x10000000u
#define HEAP_SIZE (4u << 20)
#define APP       (HEAP + 0x0000u)
#define SCREENS   (HEAP + 0x1000u)
#define SCREEN    (HEAP + 0x2000u)
#define READER    (HEAP + 0x3000u)
#define ALLOC     (HEAP + 0x100000u)

static uint8_t nbu[NBU_SIZE], heap[HEAP_SIZE];
static uint32_t guiw, oob, allocs, alloc_fail, alloc_next, publishes, byte_reads;
static uint32_t log_addr[64], log_n;

static uint8_t *at(uintptr_t a) {
    if (a >= NBU && a < NBU + NBU_SIZE) return &nbu[a - NBU];
    if (a >= GUIW && a < GUIW + 4) return (uint8_t *)&guiw + (a - GUIW);
    if (a >= HEAP && a < HEAP + HEAP_SIZE) return &heap[a - HEAP];
    oob++;
    return 0;
}
static uint32_t rd8(uintptr_t a) { uint8_t *p = at(a); byte_reads++; return p ? *p : 0; }
static void wr8(uintptr_t a, uint32_t v) {
    uint8_t *p = at(a);
    if (a >= NBU && a < NBU + NBU_SIZE) oob++;          /* the firmware pool is read-only */
    if (p) *p = (uint8_t)v;
}
static uint32_t rd(uintptr_t a) { uint32_t v = 0; if (at(a) && at(a + 3)) memcpy(&v, at(a), 4); return v; }
static void wr(uintptr_t a, uint32_t v) {
    if (a == READER + 0x10 || a == READER + 0x14)
        if (log_n < 64) log_addr[log_n++] = (uint32_t)(a - READER);
    if (a >= NBU && a < NBU + NBU_SIZE) oob++;
    if (at(a) && at(a + 3)) memcpy(at(a), &v, 4);
}
static uintptr_t al(uint32_t n) {
    allocs++;
    if (alloc_fail) return 0;
    uintptr_t a = ALLOC + alloc_next;
    if (alloc_next + n > HEAP_SIZE - 0x100000u) return 0;
    memset(&heap[a - HEAP], 0xA5, n);          /* the allocator does not zero */
    alloc_next += (n + 0xFFFu) & ~0xFFFu;
    return a;
}
static void pub(void) { publishes++; }
static void ic(void) {}
const struct uis_natives uis_test_natives = { rd, wr, rd8, wr8, al, pub, ic };

static void raw32(uintptr_t a, uint32_t v) { if (at(a)) memcpy(at(a), &v, 4); }

void fx_reset(void) {
    memset(nbu, 0, sizeof nbu); memset(heap, 0, sizeof heap);
    oob = allocs = alloc_fail = alloc_next = publishes = log_n = byte_reads = 0;
    /* a stock pool of NUL-separated words: "w0\0w1\0..." then a known string */
    uint32_t o = 0x14, k = 0;
    while (o + 16 < 0x14 + UIS_STOCK_LEN) {
        char buf[16]; int n = 0; uint32_t v = k++;
        buf[n++] = 'w';
        do { buf[n++] = (char)('0' + v % 10); v /= 10; } while (v);
        for (int i = 0; i < n; ++i) nbu[o + i] = (uint8_t)buf[i];
        o += (uint32_t)n + 1;
    }
    memcpy(&nbu[0x14 + 1000], "Footer05", 9);
    guiw = APP;
    raw32(APP + 0x80, 3); raw32(APP + 0x8C, SCREENS);
    raw32(SCREENS, SCREEN); raw32(SCREEN + 0x24, READER);
    raw32(READER + 0x10, UIS_STOCK_LEN); raw32(READER + 0x14, UIS_STOCK_POOL);
    raw32(READER + 0x24, NBU);
}
void fx_alloc_fail(uint32_t v) { alloc_fail = v; }
void fx_word(uint32_t a, uint32_t v) { raw32(a, v); }
uint32_t fx_peek(uint32_t a) { return rd(a); }
uint32_t fx_byte(uint32_t a) { return rd8(a); }
uint32_t fx_intern(const char *s, uint32_t n, uint32_t *off) { return uis_intern(s, n, off); }
uint32_t fx_intern_hinted(const char *s, uint32_t n, uint32_t at, uint32_t *off) {
    return uis_intern_hinted(s, n, at, off);
}
uint32_t fx_intern_many(const char *const *s, uint32_t count, uint32_t at, uint32_t *off) {
    return uis_intern_many(s, count, at, off);
}
uint32_t fx_known(void) { return uis_pool_known(uis_reader()); }
/* the string the UI would read at `off`, as the resolver C05E5B58 does it */
uint32_t fx_resolve(uint32_t off, char *out, uint32_t cap) {
    uint32_t len = rd(READER + 0x10), pool = rd(READER + 0x14);
    if (off >= len) return 0;
    uint32_t i = 0;
    for (; i + 1 < cap; ++i) { out[i] = (char)rd8(pool + off + i); if (!out[i]) break; }
    out[i] = 0;
    return 1;
}
uint32_t fx_get(uint32_t f) {
    if (f >= 100 && f < 164) return log_addr[f - 100];
    switch (f) {
    case 0: return oob; case 1: return allocs; case 2: return publishes; case 3: return log_n;
    case 4: return rd(READER + 0x10); case 5: return rd(READER + 0x14); case 6: return READER;
    case 7: return ALLOC; case 8: return byte_reads;
    default: return 0xFFFFFFFFu;
    }
}
