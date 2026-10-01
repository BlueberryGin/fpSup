/* Host substitutes for menu_page.c. Never linked into a camera build.
 *
 * Three memory regions at their real addresses: the NBU's string pool as the
 * firmware holds it (C18C0460..), the GUI object word (C37B7048), and a heap
 * holding the app, screens, reader, runtime entries, the variable registry
 * and the area the module is given. Any access outside them is counted. The
 * native objects are laid out exactly as read from the camera 2026-09-30. */
#include <string.h>
#include "menu_page.h"
#include "ui_pool.h"

#define NBU        0xC18C0460u
#define NBU_SIZE   0x30000u
#define GUIW       0xC37B7048u
#define HEAP       0x10000000u
#define HEAP_SIZE  (2u << 20)
#define APP        (HEAP + 0x0000u)
#define SCREENS    (HEAP + 0x1000u)          /* pointer array */
#define SCREEN_AT  (HEAP + 0x2000u)          /* screen i at +i*0x40 */
#define READER     (HEAP + 0x4000u)
#define ENTRIES    (HEAP + 0x5000u)
#define NAMES      (HEAP + 0x7000u)
#define REGISTRY_H (HEAP + 0x8000u)          /* app+888 points here */
#define DESCS      (HEAP + 0x9000u)
#define AREA       (HEAP + 0x100000u)
#define UIS_HEAP   (HEAP + 0x180000u)        /* the shared pool's copies */
#define POOL_LEN   176152u
#define NSCREENS   4u

static uint8_t nbu[NBU_SIZE], heap[HEAP_SIZE];
static uint32_t guiw, oob;
static uint8_t file[0x80000];
static uint32_t file_len, file_present, open_mode, file_pos;
static uint32_t ctors, dtors, opens, reads, closes, ctor_volume;
static uint32_t reg_calls, reg_result, reg_publishes, lookups, publishes;
static uint32_t writes_log[64], writes_n;     /* order of switched words */
static uint32_t desc_n;
static char path_seen[64];
static uint32_t borrowed_bad;
static struct fpl_menu menu;


static uint8_t *at(uintptr_t a) {
    if (a >= NBU && a < NBU + NBU_SIZE) return &nbu[a - NBU];
    if (a >= GUIW && a < GUIW + 4) return (uint8_t *)&guiw + (a - GUIW);
    if (a >= HEAP && a < HEAP + HEAP_SIZE) return &heap[a - HEAP];
    oob++;
    return 0;
}
static uint32_t rd8(uintptr_t a) { uint8_t *p = at(a); return p ? *p : 0; }
static void wr8(uintptr_t a, uint32_t v) { uint8_t *p = at(a); if (p) *p = (uint8_t)v; }
static uint32_t rd(uintptr_t a) {
    uint8_t *p = at(a); uint32_t v = 0;
    if (!p || !at(a + 3)) return 0;
    memcpy(&v, p, 4); return v;
}
static void wr_raw(uintptr_t a, uint32_t v) { uint8_t *p = at(a); if (p && at(a + 3)) memcpy(p, &v, 4); }
static void wr(uintptr_t a, uint32_t v) {
    /* the three words that switch the page: logged in order */
    if (a == READER + 0x10 || a == READER + 0x14 || a == ENTRIES + 44 * 2 + 8)
        if (writes_n < 64) writes_log[writes_n++] = (uint32_t)(a - HEAP);
    wr_raw(a, v);
}
static void put_str(uintptr_t a, const char *s) { for (; ; ++a, ++s) { wr8(a, (uint8_t)*s); if (!*s) break; } }

/* ---- natives ------------------------------------------------------------ */
static void f_ctor(uintptr_t o, uint32_t v) { (void)o; ctors++; ctor_volume = v; }
static uint32_t f_open(uintptr_t o, const char *p, uint32_t m) {
    (void)o; opens++; open_mode = m; file_pos = 0;
    strncpy(path_seen, p, sizeof path_seen - 1);
    return file_present;
}
static uint32_t f_read(uintptr_t o, uintptr_t b, uint32_t l, uint32_t *a) {
    (void)o; reads++;
    uint32_t left = file_len - file_pos, n = left < l ? left : l;
    for (uint32_t i = 0; i < n; ++i) wr8(b + i, file[file_pos + i]);
    file_pos += n;                          /* sequential, like a file */
    *a = n;
    return 1;
}
static void f_close(uintptr_t o) { (void)o; closes++; }
static void f_dtor(uintptr_t o, uint32_t m) { (void)o; (void)m; dtors++; }
static uint32_t cstr_eq(uintptr_t a, const char *s) {
    for (;; ++a, ++s) { if (rd8(a) != (uint8_t)*s) return 0; if (!*s) return 1; }
}
static uint32_t lookup(uintptr_t registry, const char *name, uintptr_t *d) {
    lookups++;
    *d = 0;
    if (registry != REGISTRY_H + 0x60) { oob++; return 0; }
    for (uint32_t i = 0; i < desc_n; ++i) {
        uintptr_t e = DESCS + 32 * i;
        if (cstr_eq(rd(e + 4), name)) { *d = e; return 0; }
    }
    return 0;                                /* a miss is also 0 */
}
static uint32_t reg(uintptr_t app, uint32_t count, const uint32_t *defs) {
    reg_calls++;
    reg_publishes = publishes;
    if (app != APP || count != 1) { oob++; return 1; }
    if (reg_result) return reg_result;
    uintptr_t e = DESCS + 32 * desc_n++;
    /* The registry BORROWS the name pointer. It must be the module's own
     * resident string, never a copy on the caller's stack. (A host pointer
     * does not fit the 32-bit word, so it is checked here and the string is
     * mirrored into fixture memory for later lookups.) */
    const char *name = 0;
    for (uint32_t i = 0; i < FPL_MENU_VARIABLES; ++i)
        if (defs[1] == (uint32_t)(uintptr_t)menu.names[i]) name = (const char *)menu.names[i];
    if (!name) { borrowed_bad++; return 0; }
    uintptr_t copy = NAMES + 0x800 + 32 * desc_n;
    put_str(copy, name);
    wr_raw(e + 0, defs[0]); wr_raw(e + 4, (uint32_t)copy); wr_raw(e + 8, defs[2]);
    return 0;
}
static void publish(void) { publishes++; }
static uint32_t uis_next;
static uintptr_t uis_alloc(uint32_t n) {
    uintptr_t a = UIS_HEAP + uis_next;
    if (uis_next + n > HEAP_SIZE - 0x180000u) return 0;
    memset(&heap[a - HEAP], 0xA5, n);
    uis_next += (n + 0xFFFu) & ~0xFFFu;
    return a;
}
const struct uis_natives uis_test_natives = { rd, wr, rd8, wr8, uis_alloc, publish };
const struct fpl_menu_natives fpl_menu_test_natives = {
    rd, wr, rd8, wr8, f_ctor, f_open, f_read, f_close, f_dtor, lookup, reg, publish
};

/* ---- the camera as it boots ------------------------------------------- */
static const char *screen_names[NSCREENS] = {"MenuItem_SelectJump", "MainB1", "MainB2", "MainY4"};
static const uint32_t entry_offsets[NSCREENS] = {0x10, 0x74d796, 0x76ff04, 0x889c60};

void fpl_fixture_reset(void) {
    memset(nbu, 0, sizeof nbu); memset(heap, 0, sizeof heap); memset(&menu, 0, sizeof menu);
    memset(file, 0, sizeof file); memset(path_seen, 0, sizeof path_seen);
    oob = file_len = open_mode = ctors = dtors = opens = reads = closes = ctor_volume = 0;
    reg_calls = reg_result = reg_publishes = lookups = publishes = writes_n = desc_n = 0;
    borrowed_bad = 0; uis_next = 0;
    file_present = 1;
    for (uint32_t i = 0; i < POOL_LEN; ++i) nbu[0x14 + i] = (uint8_t)(i * 7 + 3);  /* the pool */
    guiw = APP;
    wr_raw(APP + 0x80, NSCREENS); wr_raw(APP + 0x8C, SCREENS);
    wr_raw(APP + 0x888, REGISTRY_H);
    for (uint32_t i = 0; i < NSCREENS; ++i) {
        uintptr_t s = SCREEN_AT + 0x40 * i, n = NAMES + 0x40 * i;
        put_str(n, screen_names[i]);
        wr_raw(SCREENS + 4 * i, (uint32_t)s);
        wr_raw(s + 4, APP); wr_raw(s + 8, (uint32_t)n); wr_raw(s + 0x24, READER);
        wr_raw(ENTRIES + 44 * i + 4, (uint32_t)n);
        wr_raw(ENTRIES + 44 * i + 8, entry_offsets[i]);
    }
    wr_raw(READER + 0x10, POOL_LEN); wr_raw(READER + 0x14, NBU + 0x14);
    wr_raw(READER + 0x24, NBU); wr_raw(READER + 0xA8, NSCREENS); wr_raw(READER + 0xAC, ENTRIES);
}
void fpl_fixture_file(const uint8_t *bytes, uint32_t len, uint32_t present) {
    memcpy(file, bytes, len); file_len = len; file_present = present;
}
void fpl_fixture_word(uint32_t a, uint32_t v) { wr_raw(a, v); }
uint32_t fpl_fixture_peek(uint32_t a) { return rd(a); }
uint32_t fpl_fixture_byte(uint32_t a) { return rd8(a); }
void fpl_fixture_reg_result(uint32_t v) { reg_result = v; }
/* A variable of that name already registered (e.g. by an earlier init). */
void fpl_fixture_preregister(uint32_t type) {
    uintptr_t e = DESCS + 32 * desc_n++, n = NAMES + 0x700;
    put_str(n, "MV_fpLossless");
    wr_raw(e, type); wr_raw(e + 4, (uint32_t)n); wr_raw(e + 8, 0);
}
void fpl_fixture_set_value(uint32_t v) { wr_raw(DESCS + 8, v); }
uint32_t fpl_fixture_install(uint32_t area_bytes) { return fpl_menu_install(&menu, AREA, area_bytes); }
/* another sup that already put a string in the shared pool */
uint32_t fpl_fixture_other_sup(const char *s, uint32_t n) {
    uint32_t off = 0xFFFFFFFFu;
    return uis_intern(s, n, &off) == UIS_OK ? off : 0xFFFFFFFFu;
}
/* the string the UI resolves at a page word (big-endian), C05E5B58's rule */
uint32_t fpl_fixture_resolve_page(uint32_t page_off, char *out, uint32_t cap) {
    uintptr_t w = menu.page + page_off;
    uint32_t off = (rd8(w) << 24) | (rd8(w + 1) << 16) | (rd8(w + 2) << 8) | rd8(w + 3);
    uint32_t len = rd(READER + 0x10), pool = rd(READER + 0x14), i = 0;
    if (off >= len) return 0;
    for (; i + 1 < cap; ++i) { out[i] = (char)rd8(pool + off + i); if (!out[i]) break; }
    out[i] = 0;
    return 1;
}
uint32_t fpl_fixture_on(void) { return fpl_menu_on(&menu); }
uint32_t fpl_fixture_get(uint32_t f) {
    if (f >= 100 && f < 164) return writes_log[f - 100];
    switch (f) {
    case 0: return menu.result;
    case 1: return (uint32_t)menu.page;
    case 2: return (uint32_t)menu.pool;
    case 3: return menu.pool_len;
    case 4: return menu.registered;
    case 5: return oob;
    case 6: return ctors; case 7: return dtors; case 8: return opens;
    case 9: return reads; case 10: return closes; case 11: return ctor_volume;
    case 12: return reg_calls; case 13: return writes_n; case 14: return publishes;
    case 15: return reg_publishes; case 16: return desc_n; case 17: return open_mode;
    case 18: return menu.page_len; case 19: return menu.file_len;
    case 20: return (uint32_t)!strcmp(path_seen, "\\fpSup.BIN");
    case 21: return READER; case 22: return ENTRIES; case 23: return AREA;
    case 24: return APP; case 25: return SCREEN_AT; case 26: return NBU;
    case 27: return borrowed_bad;
    default: return 0xFFFFFFFFu;
    }
}
