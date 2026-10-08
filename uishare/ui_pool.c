#include "ui_pool.h"

/* Ver.5.02 runtime layout, read from the camera 2026-09-30:
 *   [C37B7048] = UI app; app+80 screen count, app+8C screen array
 *   screen+24  = the shared NBU reader (every screen has the same one)
 *   reader+10  = pool length, reader+14 = pool address, reader+24 = NBU base */
#define GUI_OBJECT    0xC37B7048u
#define SCREENS_COUNT 0x80u
#define SCREENS_ARRAY 0x8Cu
#define SCREEN_READER 0x24u
#define READER_LEN    0x10u
#define READER_POOL   0x14u
#define READER_BASE   0x24u
#define NBU_BASE      0xC18C0460u
#define MAX_SCREENS   512u

#if defined(UIS_HOST_TEST)
#define N (&uis_test_natives)
#define peek(a) N->read(a)
#define poke(a, v) N->write(a, v)
#define peek8(a) N->read_byte(a)
#define poke8(a, v) N->write_byte(a, v)
#define alloc(n) N->alloc(n)
#define publish() N->publish()
#define icache() N->icache()
#elif defined(__arm__) && UINTPTR_MAX == UINT32_MAX
#define peek(a) (*(volatile const uint32_t *)(a))
#define poke(a, v) (*(volatile uint32_t *)(a) = (v))
#define peek8(a) (*(volatile const uint8_t *)(a))
#define poke8(a, v) (*(volatile uint8_t *)(a) = (uint8_t)(v))
/* USER memory from the camera's allocator, as every sup launcher gets it:
 * C001D740(desc, class 0, bytes, 0), C001D7F0(desc) -> address. Never freed. */
static uintptr_t alloc(uint32_t n) {
    typedef void (*get_fn)(uint32_t *, uint32_t, uint32_t, uint32_t);
    typedef uintptr_t (*addr_fn)(uint32_t *);
    uint32_t desc[4] = {0, 0, 0, 0};
    ((get_fn)0xC001D740u)(desc, 0, n, 0);
    return ((addr_fn)0xC001D7F0u)(desc);
}
static void publish(void) { typedef void (*fn)(void); ((fn)0xC000E91Cu)(); }
static void icache(void) { typedef void (*fn)(void); ((fn)0xC000EABCu)(); }
#else
#error "ARM32 native UI ABI required; host tests must explicitly substitute it"
#endif

static uint32_t aligned(uintptr_t a) { return a && !(a & 3u); }

uintptr_t uis_reader(void) {
    uintptr_t app = peek(GUI_OBJECT), screens, screen, reader;
    uint32_t count;
    if (!aligned(app)) return 0;
    count = peek(app + SCREENS_COUNT);
    screens = peek(app + SCREENS_ARRAY);
    if (!count || count > MAX_SCREENS || !aligned(screens)) return 0;
    screen = peek(screens);
    if (!aligned(screen)) return 0;
    reader = peek(screen + SCREEN_READER);
    if (!aligned(reader) || peek(reader + READER_BASE) != NBU_BASE) return 0;
    return reader;
}

static uint32_t ours(uintptr_t pool, uint32_t len) {
    return pool > UIS_HEADER && !(pool & 3u) &&
           peek(pool - 16) == UIS_MAGIC && peek(pool - 12) == UIS_VERSION &&
           peek(pool - 8) >= len && len >= UIS_STOCK_LEN;
}

uint32_t uis_pool_known(uintptr_t reader) {
    uintptr_t pool;
    uint32_t len;
    if (!reader) return 0;
    pool = peek(reader + READER_POOL);
    len = peek(reader + READER_LEN);
    return (pool == UIS_STOCK_POOL && len == UIS_STOCK_LEN) || ours(pool, len);
}

/* Where `s` already resolves in [pool, pool+len), or UINT32_MAX. */
static uint32_t find(uintptr_t pool, uint32_t from, uint32_t len, const char *s, uint32_t n) {
    for (uint32_t i = from; i + n < len; ++i) {
        uint32_t k = 0;
        while (k < n && peek8(pool + i + k) == (uint8_t)s[k]) ++k;
        if (k == n && peek8(pool + i + n) == 0) return i;
    }
    return UINT32_MAX;
}

static void copy(uintptr_t to, uintptr_t from, uint32_t n) {
    uint32_t i = 0;
    if (!((to | from) & 3u))
        for (; i + 4 <= n; i += 4) poke(to + i, peek(from + i));
    for (; i < n; ++i) poke8(to + i, peek8(from + i));
}

/* A new copy of the current pool with `capacity` bytes, switched in. */
static uintptr_t grow(uintptr_t reader, uintptr_t pool, uint32_t len, uint32_t capacity) {
    uintptr_t block = alloc(UIS_HEADER + capacity), fresh;
    if (!block || (block & 7u)) return 0;
    fresh = block + UIS_HEADER;
    copy(fresh, pool, len);                     /* the room past it is written
                                                   string by string */
    poke(block + 0, UIS_MAGIC);
    poke(block + 4, UIS_VERSION);
    poke(block + 8, capacity);
    poke(block + 12, 0);
    publish();
    /* pointer first: every offset below the old length means the same in
     * both, so a resolve between the two stores is still right */
    poke(reader + READER_POOL, fresh);
    publish();
    return fresh;
}

/* stock_at as uis_intern_hinted takes it; FULL = no hint, scan everything. */
#define FULL 0xFFFFFFFEu

static uint32_t intern(const char *s, uint32_t n, uint32_t stock_at, uint32_t *offset) {
    uintptr_t reader = uis_reader(), pool;
    uint32_t len, at, end, capacity, from = 0;
    if (!s || !offset || !n || s[n] != 0) return UIS_INVALID;
    for (uint32_t i = 0; i < n; ++i) if (!s[i]) return UIS_INVALID;
    if (!reader) return UIS_NO_READER;
    if (!uis_pool_known(reader)) return UIS_UNKNOWN_POOL;
    pool = peek(reader + READER_POOL);
    len = peek(reader + READER_LEN);
    if (stock_at == UIS_NOT_STOCK) {
        from = UIS_STOCK_LEN;               /* the stock bytes never change */
    } else if (stock_at != FULL && stock_at < UIS_STOCK_LEN - n) {
        uint32_t k = 0;                     /* a builder's hint: check it */
        while (k < n && peek8(pool + stock_at + k) == (uint8_t)s[k]) ++k;
        if (k == n && peek8(pool + stock_at + n) == 0) { *offset = stock_at; return UIS_OK; }
    }
    if ((at = find(pool, from, len, s, n)) != UINT32_MAX) { *offset = at; return UIS_OK; }

    at = (len + 3u) & ~3u;                      /* new strings start aligned */
    end = at + n + 1u;
    capacity = pool == UIS_STOCK_POOL ? 0 : peek(pool - 8);
    if (end > capacity) {
        uint32_t want = end + UIS_HEADROOM;
        if (capacity && want < 2u * capacity) want = 2u * capacity;
        if (!(pool = grow(reader, pool, len, want))) return UIS_NO_MEMORY;
    }
    for (uint32_t i = len; i < at; ++i) poke8(pool + i, 0);
    for (uint32_t i = 0; i < n; ++i) poke8(pool + at + i, (uint8_t)s[i]);
    poke8(pool + at + n, 0);
    publish();
    poke(reader + READER_LEN, end);             /* the new string exists now */
    publish();
    *offset = at;
    return UIS_OK;
}

/* Many at once: each string found where uis_intern would find it, the missing
 * ones appended together and published with ONE pair of D-cache cleans instead
 * of a pair per string (Sensor Lab: ~40 strings; each clean pair is boot time).
 * s[i] are resident C strings, offsets[i] receives each.  Strings repeated in s
 * get one copy.  stock_at: 0, or UIS_NOT_STOCK when none of them is a stock
 * string (uis_intern_hinted's meaning).  On any failure nothing new is visible
 * (the length is not moved). */
uint32_t uis_intern_many(const char *const *s, uint32_t count, uint32_t stock_at, uint32_t *offsets) {
    uintptr_t reader = uis_reader(), pool;
    uint32_t len, end, capacity, pending = 0;
    /* UIS_NOT_STOCK: the builder knows none is a stock string -- search only
     * what other sups appended (r13: the whole-pool scans were the boot time) */
    uint32_t from = stock_at == UIS_NOT_STOCK ? UIS_STOCK_LEN : 0;
    if (!s || !offsets) return UIS_INVALID;
    if (!reader) return UIS_NO_READER;
    if (uis_pool_known(reader) == 0) return UIS_UNKNOWN_POOL;
    pool = peek(reader + READER_POOL);
    len = peek(reader + READER_LEN);
    end = len;
    /* where each lands: found, or after what is pending (aligned, as uis_intern) */
    for (uint32_t i = 0; i < count; ++i) {
        uint32_t n = 0, at;
        if (!s[i]) return UIS_INVALID;
        while (s[i][n]) ++n;
        if (!n) return UIS_INVALID;
        if ((at = find(pool, from, len, s[i], n)) != UINT32_MAX) { offsets[i] = at; continue; }
        for (uint32_t j = 0; j < i; ++j) {            /* the same text earlier in this batch */
            uint32_t k = 0;
            while (s[j][k] && s[j][k] == s[i][k]) ++k;
            if (!s[j][k] && !s[i][k]) { at = offsets[j]; break; }
        }
        if (at != UINT32_MAX) { offsets[i] = at; continue; }
        at = (end + 3u) & ~3u;
        offsets[i] = at;
        end = at + n + 1u;
        pending++;
    }
    if (!pending) return UIS_OK;
    capacity = pool == UIS_STOCK_POOL ? 0 : peek(pool - 8);
    if (capacity < end) {                      /* a bigger copy, as uis_intern grows */
        uint32_t want = end + UIS_HEADROOM;
        if (capacity && want < 2u * capacity) want = 2u * capacity;
        if (!(pool = grow(reader, pool, len, want))) return UIS_NO_MEMORY;
    }
    for (uint32_t i = len; i < end; ++i) poke8(pool + i, 0);
    for (uint32_t i = 0; i < count; ++i) {
        if (offsets[i] < len) continue;
        for (uint32_t k = 0; s[i][k]; ++k) poke8(pool + offsets[i] + k, (uint8_t)s[i][k]);
    }
    publish();
    poke(reader + READER_LEN, end);             /* every new string exists now */
    publish();
    return UIS_OK;
}

uint32_t uis_intern(const char *s, uint32_t n, uint32_t *offset) {
    return intern(s, n, FULL, offset);
}

uint32_t uis_intern_hinted(const char *s, uint32_t n, uint32_t stock_at, uint32_t *offset) {
    return intern(s, n, stock_at, offset);
}

/* ---- nested string layers (NESTED_HOOKS.md) ------------------------------- */
#include "ui_strings_code.h"

/* the three sites with their stock words, and each one's entry in a layer */
#define SITE_RES       0xC05E5B58u
#define STOCK_RES      0x3FFFF1B1u
#define SITE_OWN       0xC05E61C8u
#define STOCK_OWN      0x428A6942u
#define SITE_REM       0xC05E61E0u
#define STOCK_REM      0x69406902u
/* the shared cave allocator (stage2 resets the bump word every boot) */
#define CAVE_BUMP      0xC072E060u
#define CAVE_END       0xC072EFB4u
#define THUMB_VENEER   0xF000F8DFu      /* ldr.w pc, [pc, #0]; then the entry */
#define VENEERS        24u              /* three veneers of 8 bytes */
#define LAYER_TABLE    UIS_LAYER_BYTES  /* string addresses follow the code */

/* Where the Thumb B.W `w` at `site` goes, or 0 if `w` is not one. */
static uintptr_t bw_target(uintptr_t site, uint32_t w) {
    uint32_t hw1 = w & 0xFFFFu, hw2 = w >> 16, s, j1, j2, i1, i2, off;
    if ((hw1 & 0xF800u) != 0xF000u || (hw2 & 0xD000u) != 0x9000u) return 0;
    s = (hw1 >> 10) & 1u; j1 = (hw2 >> 13) & 1u; j2 = (hw2 >> 11) & 1u;
    i1 = 1u ^ (j1 ^ s); i2 = 1u ^ (j2 ^ s);
    off = s << 24 | i1 << 23 | i2 << 22 | (hw1 & 0x3FFu) << 12 | (hw2 & 0x7FFu) << 1;
    if (s) off |= 0xFE000000u;
    return site + 4u + off;
}
static uint32_t bw_word(uintptr_t site, uintptr_t to) {
    uint32_t off = to - (site + 4u), s = off >> 31, i1 = (off >> 23) & 1u, i2 = (off >> 22) & 1u;
    uint32_t j1 = (1u ^ i1) ^ s, j2 = (1u ^ i2) ^ s;
    uint32_t hw1 = 0xF000u | s << 10 | ((off >> 12) & 0x3FFu);
    uint32_t hw2 = 0x9000u | j1 << 13 | j2 << 11 | ((off >> 1) & 0x7FFu);
    return hw1 | hw2 << 16;
}
static uint32_t bw_reaches(uintptr_t site, uintptr_t to) {
    uint32_t off = to - (site + 4u);
    return !(off & 1u) && (off + 0x01000000u) < 0x02000000u;     /* +-16 MiB */
}

/* ---- whose layer: Loader v3's books (header v2) ---------------------------
 * The layer header: +0 name (8 bytes), +8 version, +12 base, +16 count,
 * +20 lo, +24 hi, +28/+32/+36 next resolve/owns/remain, +40 table
 * (ui_strings.S). */
#define H_NAME     0u
#define H_VERSION  8u
#define H_BASE     12u
#define H_COUNT    16u
#define H_LO       20u
#define H_HI       24u
#define H_NEXT_RES 28u
#define H_NEXT_OWN 32u
#define H_NEXT_REM 36u
#define H_TABLE    40u
#define H_QSID     44u                  /* v3, QS_SHARE.md §4.3 */
#define H_QSENUM   48u
#define H_QSNAMES  52u
#define H_LEN      56u                  /* v5: the layer describes itself */
#define H_OFF_RES  60u
#define H_OFF_OWN  64u
#define H_OFF_REM  68u
#define H_MIN_LEN  72u                  /* the fields this code reads; longer headers are fine */
#define MAX_DEPTH  64u                  /* layers walked before giving up */
#define MAX_HOLDERS 32u                 /* sloader.c MAX_SUPS */

/* sloader.h: SL_SVC_AT holds &svc while a Loader v3 runs entries, else 0;
 * the table: +0 version, +32 holder, +36 self, +40 done_at_start. */
#define SL_SVC_AT     0xC072F6FCu
#define LOAD_DONE_US  0xC072F6F8u
#define SVC_VERSION   0u
#define SVC_HOLDER    32u
#define SVC_CLAIM_RES 12u
#define SVC_SELF      36u
#define SVC_DONE      40u

#if defined(UIS_HOST_TEST)
__attribute__((weak)) uintptr_t (*uis_test_loader_self)(void) = 0;
__attribute__((weak)) uintptr_t (*uis_test_loader_holder)(uintptr_t, uint32_t) = 0;
__attribute__((weak)) uint32_t (*uis_test_loader_claim_res)(uint32_t) = 0;
static uint32_t loader_claim_res(uintptr_t svc, uint32_t id) {
    (void)svc; return uis_test_loader_claim_res ? uis_test_loader_claim_res(id) : 1u;
}
static uintptr_t loader(void) { return uis_test_loader_self && uis_test_loader_self() ? 1u : 0u; }
static uintptr_t loader_self(uintptr_t svc) { (void)svc; return uis_test_loader_self(); }
static uintptr_t loader_holder(uintptr_t svc, uintptr_t a, uint32_t i) {
    (void)svc; return uis_test_loader_holder ? uis_test_loader_holder(a, i) : 0;
}
#else
/* The running Loader v3's table, or 0. A table a past boot left behind fails
 * the last test: the loader writes LOAD_DONE_US after its last entry. */
static uintptr_t loader(void) {
    uintptr_t svc = peek(SL_SVC_AT);
    if (!svc || (svc & 3u) || peek(svc + SVC_VERSION) < 3u ||
        peek(svc + SVC_DONE) != peek(LOAD_DONE_US))
        return 0;
    return svc;
}
typedef uintptr_t (*self_fn)(uintptr_t);
typedef uintptr_t (*holder_fn)(uintptr_t, uintptr_t, uint32_t);
static uintptr_t loader_self(uintptr_t svc) { return ((self_fn)peek(svc + SVC_SELF))(svc); }
static uintptr_t loader_holder(uintptr_t svc, uintptr_t a, uint32_t i) {
    return ((holder_fn)peek(svc + SVC_HOLDER))(svc, a, i);
}
/* svc->claim_res(id, SL_SHARED_UI): 0 = held (by this sup, maybe with others). */
static uint32_t loader_claim_res(uintptr_t svc, uint32_t id) {
    typedef int (*claim_res_fn)(uintptr_t, uint32_t, uint32_t);
    return ((claim_res_fn)peek(svc + SVC_CLAIM_RES))(svc, id, 3u) != 0;
}
#endif

/* "\fpSup\10LOSS.BIN" -> "10LOSS" in two words, NUL-padded (at most 8 chars). */
static void name_of(uintptr_t path, uint32_t w[2]) {
    uintptr_t base = path;
    w[0] = w[1] = 0;
    for (uint32_t k = 0; k < 64u && peek8(path + k); ++k)
        if (peek8(path + k) == '\\') base = path + k + 1u;
    for (uint32_t k = 0; k < UIS_LAYER_NAME_BYTES; ++k) {
        uint32_t c = peek8(base + k);
        if (!c || c == '.') break;
        w[k >> 2] |= c << (8u * (k & 3u));
    }
}
/* The name a layer of this sup carries; 0 if a running loader gives none. */
static uint32_t my_name(uint32_t w[2]) {
    uintptr_t svc = loader(), path;
    if (!svc) { w[0] = UIS_LAYER_LEGACY; w[1] = 0; return 1; }
    path = loader_self(svc);
    if (!path) return 0;
    name_of(path, w);
    return w[0] != 0;
}
/* 1 if the header at `at` is a layer's and its name is one the books list as
 * holding `site` (no Loader v3 running: the legacy name). */
static uint32_t layer_ok(uintptr_t at, uintptr_t site) {
    uintptr_t svc, path;
    uint32_t n[2];
    if (peek(at + H_VERSION) != UIS_LAYER_VERSION) return 0;
    svc = loader();
    if (!svc) return peek(at + H_NAME) == UIS_LAYER_LEGACY && peek(at + H_NAME + 4) == 0;
    if (!peek(at + H_NAME)) return 0;
    for (uint32_t i = 0; i < MAX_HOLDERS && (path = loader_holder(svc, site, i)) != 0; ++i) {
        name_of(path, n);
        if (n[0] == peek(at + H_NAME) && n[1] == peek(at + H_NAME + 4)) return 1;
    }
    return 0;
}

/* The header of the layer whose entry `field` names is at `entry`, or 0.
 * v5: the word before every entry is the entry's offset from its header, and
 * the header repeats it -- no reader assumes another build's code layout, so a
 * layer whose code moved is still recognised (2026-10-07). */
static uintptr_t header_of(uintptr_t entry, uint32_t field) {
    uintptr_t h;
    uint32_t off;
    if (!entry || (entry & 3u) || entry < 4u) return 0;                /* layers are ARM code */
    off = peek(entry - 4u);
    if ((off & 3u) || off < H_MIN_LEN || off > 0x10000u || entry < off) return 0;
    h = entry - off;
    if (peek(h + H_VERSION) != UIS_LAYER_VERSION || peek(h + H_LEN) < H_MIN_LEN ||
        peek(h + field) != off)
        return 0;
    return h;
}
static uintptr_t entry_of(uintptr_t h, uint32_t field) { return h + peek(h + field); }

/* 1 and the layer whose `field` entry hangs on `site` (0 = the stock word is
 * there); 0 if the site holds anything else. */
static uint32_t site_layer(uintptr_t site, uint32_t stock, uint32_t field, uintptr_t *layer) {
    uint32_t w = peek(site);
    uintptr_t veneer, at;
    if (w == stock) { *layer = 0; return 1; }
    veneer = bw_target(site, w);
    if (!veneer || (veneer & 3u) || peek(veneer) != THUMB_VENEER) return 0;
    at = header_of(peek(veneer + 4), field);
    if (!at || !layer_ok(at, site)) return 0;
    *layer = at;
    return 1;
}
/* The outermost layer (0: none yet), if all three sites agree on it. */
static uint32_t outer_layer(uintptr_t *layer) {
    uintptr_t a, b, c;
    if (!site_layer(SITE_RES, STOCK_RES, H_OFF_RES, &a) ||
        !site_layer(SITE_OWN, STOCK_OWN, H_OFF_OWN, &b) ||
        !site_layer(SITE_REM, STOCK_REM, H_OFF_REM, &c) || a != b || b != c)
        return 0;
    *layer = a;
    return 1;
}
/* Walk the chain from `layer` inward. *base: where the next boot-issued
 * offsets start (past every issued range); 0 if [lo, hi) overlaps a layer's
 * range or a layer is not one. */
static uint32_t walk(uintptr_t layer, uint32_t lo, uint32_t hi, uint32_t *base) {
    uint32_t next = UIS_LAYER_FIRST, depth = 0;
    for (; layer; ++depth) {
        uint32_t b = peek(layer + H_BASE), e = b + peek(layer + H_COUNT);
        uintptr_t in = peek(layer + H_NEXT_RES);
        if (depth == MAX_DEPTH || peek(layer + H_VERSION) != UIS_LAYER_VERSION || e < b ||
            b < UIS_LAYER_FIRST || (b < UIS_LAYER_FIXED && e > UIS_LAYER_FIXED))
            return 0;                                            /* not offsets we make */
        if (b < hi && lo < e) return 0;
        if (b < UIS_LAYER_FIXED && e > next) next = e;
        layer = in ? header_of(in, H_OFF_RES) : 0;
        if (in && !layer) return 0;
    }
    *base = next;
    return 1;
}

uint32_t uis_layer_base(uint32_t *base) {
    uintptr_t layer;
    if (!base) return UIS_INVALID;
    if (!outer_layer(&layer) || !walk(layer, 0, 0, base)) return UIS_HOOK;
    return UIS_OK;
}

/* Header, veneers, sites: the layer at `block` (its code already written)
 * goes up on top of `inner`. Nothing here can fail. */
static void hang(uintptr_t block, uintptr_t inner, const uint32_t name[2], uint32_t base,
                 uint32_t count, uintptr_t lo, uintptr_t hi, uintptr_t table,
                 uint32_t qsid, uint32_t qsenum, uintptr_t qsnames) {
    uintptr_t veneer = peek(CAVE_BUMP);
    poke(block + H_QSID, qsid);
    poke(block + H_QSENUM, qsenum);
    poke(block + H_QSNAMES, qsnames);
    poke(block + H_NAME, name[0]);
    poke(block + H_NAME + 4, name[1]);
    poke(block + H_VERSION, UIS_LAYER_VERSION);
    poke(block + H_BASE, base);
    poke(block + H_COUNT, count);
    poke(block + H_LO, lo);
    poke(block + H_HI, hi);
    poke(block + H_NEXT_RES, inner ? entry_of(inner, H_OFF_RES) : 0u);   /* its offsets, not ours */
    poke(block + H_NEXT_OWN, inner ? entry_of(inner, H_OFF_OWN) : 0u);
    poke(block + H_NEXT_REM, inner ? entry_of(inner, H_OFF_REM) : 0u);
    poke(block + H_LEN, UIS_LAYER_HEADER);
    poke(block + H_OFF_RES, UIS_LAYER_RESOLVE);
    poke(block + H_OFF_OWN, UIS_LAYER_OWNS);
    poke(block + H_OFF_REM, UIS_LAYER_REMAIN);
    poke(block + H_TABLE, table);
    /* three veneers from the cave: resolve, owns, remain */
    poke(CAVE_BUMP, veneer + VENEERS);
    poke(veneer + 0, THUMB_VENEER);  poke(veneer + 4, block + UIS_LAYER_RESOLVE);
    poke(veneer + 8, THUMB_VENEER);  poke(veneer + 12, block + UIS_LAYER_OWNS);
    poke(veneer + 16, THUMB_VENEER); poke(veneer + 20, block + UIS_LAYER_REMAIN);
    publish(); icache();
    /* the checks first, so a pointer is recognised before it can be produced */
    poke(SITE_OWN, bw_word(SITE_OWN, veneer + 8));
    poke(SITE_REM, bw_word(SITE_REM, veneer + 16));
    publish(); icache();
    poke(SITE_RES, bw_word(SITE_RES, veneer));
    publish(); icache();
}
static uint32_t cave_room(void) {
    uint32_t bump = peek(CAVE_BUMP);
    return !(bump & 3u) && bump >= CAVE_BUMP + 4u && bump + VENEERS <= CAVE_END &&
           bw_reaches(SITE_RES, bump) && bw_reaches(SITE_REM, bump + 16u);
}

static uint32_t layer_add(const uintptr_t *s, const uint32_t *n, uint32_t count, uint32_t *base,
                          uint32_t qsid, uint32_t qsenum, uint32_t own) {
    uintptr_t inner, block, data;
    uint32_t bytes = 0, b, at, name[2];
    if (!s || !n || !base || !count || count > 256u || (qsid && own + 3u > count)) return UIS_INVALID;
    for (uint32_t i = 0; i < count; ++i) {
        if (!s[i] || !n[i] || n[i] >= 64u || peek8(s[i] + n[i])) return UIS_INVALID;
        for (uint32_t k = 0; k < n[i]; ++k) if (!peek8(s[i] + k)) return UIS_INVALID;
        bytes += n[i] + 1u;
    }
    if (!my_name(name)) return UIS_INVALID;
    if (!outer_layer(&inner) || !walk(inner, 0, 0, &b) || b + count < b ||
        b + count > UIS_LAYER_FIXED)
        return UIS_HOOK;
    if (!cave_room()) return UIS_NO_MEMORY;
    block = alloc(LAYER_TABLE + 4u * count + bytes);
    if (!block || (block & 7u)) return UIS_NO_MEMORY;

    /* the layer, complete, before anything points at it */
    uis_layer_code(block);
    data = block + LAYER_TABLE + 4u * count;
    at = 0;
    for (uint32_t i = 0; i < count; ++i) {
        poke(block + LAYER_TABLE + 4u * i, data + at);
        for (uint32_t k = 0; k <= n[i]; ++k) poke8(data + at + k, peek8(s[i] + k));
        at += n[i] + 1u;
    }
    hang(block, inner, name, b, count, data, data + bytes, block + LAYER_TABLE,
         qsid, qsenum, qsid ? block + LAYER_TABLE + 4u * own : 0u);
    *base = b;
    return UIS_OK;
}

uint32_t uis_layer_add(const uintptr_t *s, const uint32_t *n, uint32_t count, uint32_t *base) {
    return layer_add(s, n, count, base, 0, 0, 0);
}
uint32_t uis_layer_add_qs(const uintptr_t *s, const uint32_t *n, uint32_t count, uint32_t *base,
                          uint32_t qsid, uint32_t enum_value, uint32_t own) {
    if (!qsid) return UIS_INVALID;
    return layer_add(s, n, count, base, qsid, enum_value, own);
}

uint32_t uis_qs_shared(uint32_t *qsid, uint32_t *issuer) {
    uintptr_t svc = loader(), first, me, layer;
    uint32_t want[2], mine[2], depth = 0;
    if (!qsid || !issuer) return UIS_INVALID;
    *qsid = 0; *issuer = 0;
    /* this sup holds the resource first (claim before asking who holds it:
     * the first holder is the one that hands out the ids) */
    if (!svc || loader_claim_res(svc, UIS_RES_QSCUR) ||
        !(first = loader_holder(svc, UIS_RES_QSCUR, 0)) || !(me = loader_self(svc)))
        return UIS_INVALID;
    name_of(first, want);
    name_of(me, mine);
    if (want[0] == mine[0] && want[1] == mine[1]) { *issuer = 1; return UIS_HOOK; }
    if (!outer_layer(&layer)) return UIS_HOOK;
    for (; layer && depth < MAX_DEPTH; ++depth) {
        uintptr_t in = peek(layer + H_NEXT_RES);
        if (peek(layer + H_VERSION) != UIS_LAYER_VERSION) return UIS_HOOK;
        if (peek(layer + H_NAME) == want[0] && peek(layer + H_NAME + 4) == want[1] &&
            peek(layer + H_QSID)) {
            *qsid = peek(layer + H_QSID);
            return UIS_OK;
        }
        layer = in ? header_of(in, H_OFF_RES) : 0;
        if (in && !layer) return UIS_HOOK;
    }
    return UIS_HOOK;
}

uint32_t uis_layer_fixed(uintptr_t table, uint32_t count, uint32_t base, uintptr_t lo, uintptr_t hi) {
    uintptr_t inner, block;
    uint32_t b, name[2];
    if (!table || (table & 3u) || !count || count > 256u || base < UIS_LAYER_FIXED ||
        base + count < base || base + count == 0u || lo >= hi)
        return UIS_INVALID;                    /* 0xFFFFFFFF (none) is never an offset */
    if (!my_name(name)) return UIS_INVALID;
    if (!outer_layer(&inner) || !walk(inner, base, base + count, &b)) return UIS_HOOK;
    if (!cave_room()) return UIS_NO_MEMORY;
    block = alloc(LAYER_TABLE);
    if (!block || (block & 7u)) return UIS_NO_MEMORY;
    uis_layer_code(block);
    hang(block, inner, name, base, count, lo, hi, table, 0, 0, 0);
    return UIS_OK;
}

uint32_t uis_stock_has(uintptr_t s, uint32_t n, uint32_t stock_at) {
    uintptr_t reader = uis_reader(), pool;
    if (!s || !n || !reader || !uis_pool_known(reader) || stock_at >= UIS_STOCK_LEN ||
        n >= UIS_STOCK_LEN - stock_at)
        return 0;
    pool = peek(reader + READER_POOL);
    for (uint32_t k = 0; k < n; ++k) if (peek8(pool + stock_at + k) != peek8(s + k)) return 0;
    return peek8(pool + stock_at + n) == 0;
}
