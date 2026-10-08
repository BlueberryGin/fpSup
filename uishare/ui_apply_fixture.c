/* Host substitutes for ui_apply.c and ui_pool.c together: the firmware's NBU
 * region (loaded from the pinned image by the test) at its address, the GUI
 * word, one app with a screen list and the shared reader with its runtime
 * page entries, and an allocator handing out fresh heap blocks. */
#include <string.h>
#include <stdlib.h>
#include "ui_apply.h"
#include "ui_pool.h"

#define IMG       0xC0000000u          /* the firmware image: NBU, NBR, code, cave */
#define IMG_SIZE  0x03000000u
#define NBU       0xC18C0460u
#define FV_SITE   0xC05E5BECu          /* writable: the redirect site */
#define STR_RES   0xC05E5B58u          /* writable: the three string sites */
#define STR_OWN   0xC05E61C8u
#define STR_REM   0xC05E61E0u
#define CAVE_LO   0xC072E060u          /* writable: the bump word and the arena */
#define CAVE_HI   0xC072EFB4u
#define GUIW      0xC37B7048u
#define HEAP      0x10000000u
#define HEAP_SIZE (16u << 20)
#define APP       (HEAP + 0x0000u)
#define SCREENS   (HEAP + 0x1000u)
#define SCREEN    (HEAP + 0x2000u)
#define READER    (HEAP + 0x3000u)
#define ENTRIES   (HEAP + 0x4000u)
#define NAMES     (HEAP + 0x6000u)
#define BLOCK     (HEAP + 0x10000u)    /* the FPUI block under test */
#define ALLOC     (HEAP + 0x200000u)

static uint8_t *img, *heap;
static uint32_t guiw, oob, allocs, alloc_fail, alloc_next, publishes, nbu_writes;
static uint32_t entry_writes, last_entry_write, dirty, dirty_at_switch, byte_reads;
static uint32_t site_log[8], site_dirty[8], site_n;

static uint8_t *at(uintptr_t a) {
    if (a >= IMG && a < IMG + IMG_SIZE) return &img[a - IMG];
    if (a >= GUIW && a < GUIW + 4) return (uint8_t *)&guiw + (a - GUIW);
    if (a >= HEAP && a < HEAP + HEAP_SIZE) return &heap[a - HEAP];
    oob++;
    return 0;
}
/* A write into the image other than the site words and the cave. */
static uint32_t site(uintptr_t a, uintptr_t s) { return a >= s && a < s + 4; }
static uint32_t fw(uintptr_t a) {
    return a >= IMG && a < IMG + IMG_SIZE && !site(a, FV_SITE) && !site(a, STR_RES) &&
           !site(a, STR_OWN) && !site(a, STR_REM) && !(a >= CAVE_LO && a < CAVE_HI);
}
static uint32_t rd8(uintptr_t a) { uint8_t *p = at(a); byte_reads++; return p ? *p : 0; }
static void wr8(uintptr_t a, uint32_t v) {
    uint8_t *p = at(a);
    dirty++;
    if (fw(a)) nbu_writes++;             /* the firmware image is read-only */
    if (p) *p = (uint8_t)v;
}
static uint32_t rd(uintptr_t a) { uint32_t v = 0; if (at(a) && at(a + 3)) memcpy(&v, at(a), 4); return v; }
static void wr(uintptr_t a, uint32_t v) {
    if (fw(a)) nbu_writes++;
    if ((site(a, STR_RES) || site(a, STR_OWN) || site(a, STR_REM)) && site_n < 8) {
        site_dirty[site_n] = dirty;          /* unpublished writes when the site goes live */
        site_log[site_n++] = (uint32_t)a;
        if (at(a) && at(a + 3)) memcpy(at(a), &v, 4);
        return;                              /* a site word is not part of the layer */
    }
    if (a >= ENTRIES && a < ENTRIES + 0x2000u && (a - ENTRIES) % 44u == 8u) {
        entry_writes++; last_entry_write = (uint32_t)a;
        dirty_at_switch += dirty;            /* unpublished writes when the UI can see it */
    } else {
        dirty++;
    }
    if (at(a) && at(a + 3)) memcpy(at(a), &v, 4);
}
static uintptr_t al(uint32_t n) {
    allocs++;
    if (alloc_fail && allocs >= alloc_fail) return 0;
    uintptr_t a = ALLOC + alloc_next;
    if (alloc_next + n > HEAP_SIZE - 0x200000u) return 0;
    memset(&heap[a - HEAP], 0xA5, n);          /* the allocator does not zero */
    alloc_next += (n + 0xFFFu) & ~0xFFFu;
    return a;
}
static void pub(void) { publishes++; dirty = 0; }
static uint32_t icaches;
static void ic(void) { icaches++; }
const struct uia_natives uia_test_natives = { rd, wr, rd8, wr8, al, pub, ic };
/* A running Loader v3, modelled on fp_usb_shell/v3/sloader.c (not an ideal
 * one -- 2026-10-07, d9 found the camera's svc_holder skipped resource claims
 * while the old model answered every address the same):
 *   - fx_loader(self, sups, n): the sups in load order (all kept), `self` the
 *     one whose entry runs. Each holds MEMORY claims on the uishare sites its
 *     claim list names (the three string sites and C05E5BEC); nothing else.
 *   - claim_res(id): a RESOURCE claim by the running sup (SHARED_UI; asked
 *     twice, the same answer), as svc_claim_res. fx_claim_res(sup, id) makes
 *     one for another sup (a sup that claimed but has not hung its layer).
 *   - holder(addr, i): the i-th sup in load order with a claim covering addr --
 *     a memory claim overlapping [addr, addr+4), or a resource claim whose id
 *     is addr (svc_holder). fx_holder_skips_res(1) is the camera's loader
 *     before the fix (resource claims ignored): the mutation. */
#define MAX_SUPS 8u
#define MAX_RES  16u
static uintptr_t ld_self, ld_sup[MAX_SUPS];
static uint32_t ld_n, ld_skip_res;
static uintptr_t res_sup[MAX_RES];          /* the claimer's path: claims outlive fx_loader's list */
static uint32_t res_id[MAX_RES], res_n;
static uint32_t same_path(uintptr_t a, uintptr_t b) {
    if (a == b) return 1;
    for (uint32_t i = 0; i < 64; ++i) {
        uint32_t x = rd8(a + i), y = rd8(b + i);
        if (x != y) return 0;
        if (!x) return 1;
    }
    return 1;
}
static const uintptr_t SITES_HELD[] = { STR_RES, STR_OWN, STR_REM, FV_SITE };
static uintptr_t lself(void) { return ld_self; }
static uint32_t cur(void) {
    for (uint32_t k = 0; k < ld_n; ++k) if (ld_sup[k] == ld_self) return k;
    return MAX_SUPS;
}
static uintptr_t lholder(uintptr_t a, uint32_t i) {
    for (uint32_t k = 0; k < ld_n; ++k) {
        uint32_t holds = 0;
        for (uint32_t j = 0; j < sizeof SITES_HELD / sizeof SITES_HELD[0]; ++j)
            if (a + 4u > SITES_HELD[j] && a < SITES_HELD[j] + 4u) holds = 1;
        for (uint32_t r = 0; r < res_n && !ld_skip_res; ++r)
            if (same_path(res_sup[r], ld_sup[k]) && res_id[r] == a) holds = 1;
        if (holds && !i--) return ld_sup[k];
    }
    return 0;
}
static uint32_t lclaim_res(uint32_t id) {
    if (cur() == MAX_SUPS) return 1;
    for (uint32_t r = 0; r < res_n; ++r) if (same_path(res_sup[r], ld_self) && res_id[r] == id) return 0;
    if (res_n == MAX_RES) return 1;
    res_sup[res_n] = ld_self; res_id[res_n++] = id;
    return 0;
}
const struct uis_natives uis_test_natives = { rd, wr, rd8, wr8, al, pub, ic };
uintptr_t (*uis_test_loader_self)(void) = lself;
uintptr_t (*uis_test_loader_holder)(uintptr_t, uint32_t) = lholder;
uint32_t (*uis_test_loader_claim_res)(uint32_t) = lclaim_res;
void fx_loader(uint32_t self, const uint32_t *sups, uint32_t n) {
    ld_self = self; ld_n = n < MAX_SUPS ? n : MAX_SUPS;
    for (uint32_t i = 0; i < ld_n; ++i) ld_sup[i] = sups[i];
}
void fx_claim_res(uint32_t sup_path, uint32_t id) {
    if (res_n < MAX_RES) { res_sup[res_n] = sup_path; res_id[res_n++] = id; }
}
void fx_holder_skips_res(uint32_t v) { ld_skip_res = v; }

static void raw32(uintptr_t a, uint32_t v) { if (at(a)) memcpy(at(a), &v, 4); }

/* image: the firmware from 0xC0000000; entries: name\0name\0..., offsets */
void fx_reset(const uint8_t *image, uint32_t n, const char *names, const uint32_t *offsets,
              uint32_t count) {
    if (!img) img = malloc(IMG_SIZE);
    if (!heap) heap = malloc(HEAP_SIZE);
    memset(img, 0, IMG_SIZE); memset(heap, 0, HEAP_SIZE);
    memcpy(img, image, n < IMG_SIZE ? n : IMG_SIZE);
    raw32(CAVE_LO, CAVE_LO + 4);             /* stage2 resets the bump every boot */
    oob = allocs = alloc_fail = alloc_next = publishes = nbu_writes = 0;
    ld_self = 0; ld_n = 0; ld_skip_res = 0; res_n = 0;
    entry_writes = last_entry_write = dirty = dirty_at_switch = byte_reads = site_n = 0;
    guiw = APP;
    raw32(APP + 0x80, 3); raw32(APP + 0x8C, SCREENS);
    raw32(SCREENS, SCREEN); raw32(SCREEN + 0x24, READER);
    raw32(READER + 0x10, UIS_STOCK_LEN); raw32(READER + 0x14, UIS_STOCK_POOL);
    raw32(READER + 0x24, NBU);
    raw32(READER + 0xA8, count); raw32(READER + 0xAC, ENTRIES);
    uintptr_t s = NAMES;
    for (uint32_t i = 0; i < count; ++i) {
        size_t k = strlen(names);
        memcpy(at(s), names, k + 1);
        raw32(ENTRIES + 44 * i + 4, (uint32_t)s);
        raw32(ENTRIES + 44 * i + 8, offsets[i]);
        s += k + 1; names += k + 1;
    }
}
void fx_alloc_fail(uint32_t v) { alloc_fail = v; }
uint32_t fx_fixed(uint32_t table, uint32_t count, uint32_t base, uint32_t lo, uint32_t hi) {
    return uis_layer_fixed(table, count, base, lo, hi);
}
uint32_t fx_peek(uint32_t a) { return rd(a); }
void fx_poke(uint32_t a, uint32_t v) { raw32(a, v); }
void fx_read(uint32_t a, uint8_t *out, uint32_t n) { for (uint32_t i = 0; i < n; ++i) out[i] = (uint8_t)rd8(a + i); }
/* Which build of uia_apply applies the next block: test_qs_option links a
 * second build (other entry offsets) into the same model to apply one sup's
 * block with it -- two builds of uishare on one card. */
uint32_t (*fx_apply_fn)(uintptr_t, uint32_t, struct uia_outcome *) = uia_apply;
uint32_t fx_apply(const uint8_t *blob, uint32_t n, uint32_t *out_words) {
    struct uia_outcome o;
    memcpy(at(BLOCK), blob, n);
    uint32_t r = fx_apply_fn(BLOCK, n, &o);
    out_words[0] = o.result; out_words[1] = o.op; out_words[2] = o.first_id;
    out_words[3] = (uint32_t)o.page; out_words[4] = o.n_slots; icaches += 0;
    for (uint32_t i = 0; i < UIA_MAX_SLOTS; ++i) out_words[5 + i] = o.slots[i];
    return r;
}
uint32_t fx_get(uint32_t f) {
    switch (f) {
    case 0: return oob; case 1: return allocs; case 2: return publishes; case 3: return nbu_writes;
    case 4: return entry_writes; case 5: return last_entry_write; case 6: return READER;
    case 7: return ENTRIES; case 8: return rd(READER + 0x10); case 9: return rd(READER + 0x14);
    case 10: return dirty_at_switch; case 11: return byte_reads; case 12: return icaches;
    case 13: return site_n;
    case 20: case 21: case 22: case 23: case 24: case 25: case 26: case 27: return site_log[f - 20];
    case 30: case 31: case 32: case 33: case 34: case 35: case 36: case 37: return site_dirty[f - 30];
    default: return 0xFFFFFFFFu;
    }
}
