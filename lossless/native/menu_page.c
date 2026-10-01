#include "menu_page.h"
#include "ui_pool.h"      /* fpSup/uishare: the shared string pool convention */

/* Ver.5.02 runtime layout, read from the camera 2026-09-30:
 *   C37B7048              the GUI object (C055F0F0); its word +0 is the UI app
 *   app+7C                screen vector: +4 count, +10 array (C05E04D8/E0)
 *   screen+04/+08/+24     app, name, the shared NBU reader
 *   reader+10/+14         string pool length / address
 *   reader+24             NBU base C18C0460
 *   reader+A8/+AC         44-byte runtime entries: +04 name, +08 page offset
 *   app+888 -> +60        the variable registry (C05D9EB0)
 * Firmware facts: MainB2 page C2030364. The string pool belongs to no sup:
 * private strings go through uis_intern() (fpSup/uishare/ui_pool.h). */
#define GUI_OBJECT      0xC37B7048u
#define SCREENS_COUNT   0x80u
#define SCREENS_ARRAY   0x8Cu
#define SCREEN_APP      0x04u
#define SCREEN_NAME     0x08u
#define SCREEN_READER   0x24u
#define READER_POOL_LEN 0x10u
#define READER_POOL     0x14u
#define READER_SOURCE   0x24u
#define READER_ENTRIES  0xA8u
#define READER_TABLE    0xACu
#define ENTRY_BYTES     44u
#define ENTRY_NAME      0x04u
#define ENTRY_OFFSET    0x08u
#define APP_REGISTRY    0x888u
#define REGISTRY_OFF    0x60u
#define NBU_BASE        0xC18C0460u
#define MAINB2_OFFSET   0x76FF04u
#define MAX_SCREENS     512u
#define FILE_OBJECT     0x1000u       /* the native file object, ~3 KiB */
#define VOLUME_SD       1u
#define VARIABLE_INT    0u
#define DESC_TYPE       0x00u
#define DESC_NAME       0x04u
#define DESC_VALUE      0x08u

/* Strings are spelled in words: the camera build links .text alone. */
static void spell(uint32_t *to, uint32_t a, uint32_t b, uint32_t c, uint32_t d, uint32_t e) {
    volatile uint32_t *v = to;
    v[0] = a; v[1] = b; v[2] = c; v[3] = d; v[4] = e;
}
#define PATH_WORDS(w) spell(w, 0x5370665Cu, 0x422E7075u, 0x00004E49u, 0u, 0u)    /* \fpSup.BIN */
#define SCREEN_WORDS(w) spell(w, 0x6E69614Du, 0x00003242u, 0u, 0u, 0u)           /* MainB2 */
static void name_words(struct fpl_menu *m) {
    spell(m->names[0], 0x665F564Du, 0x736F4C70u, 0x73656C73u, 0x00000073u, 0u);          /* MV_fpLossless */
    spell(m->names[1], 0x5F425553u, 0x665F564Du, 0x736F4C70u, 0x73656C73u, 0x00000073u); /* SUB_MV_fpLossless */
    spell(m->names[2], 0x4C435845u, 0x4C70665Fu, 0x6C73736Fu, 0x00737365u, 0u);          /* EXCL_fpLossless */
}

#if defined(FPL_MENU_HOST_TEST)
#define N (&fpl_menu_test_natives)
#define peek(a) N->read(a)
#define poke(a, v) N->write(a, v)
#define peek8(a) N->read_byte(a)
#define poke8(a, v) N->write_byte(a, v)
#define f_ctor(o, v) N->f_ctor(o, v)
#define f_open(o, p, m) N->f_open(o, p, m)
#define f_read(o, b, l, a) N->f_read(o, b, l, a)
#define f_close(o) N->f_close(o)
#define f_dtor(o, m) N->f_dtor(o, m)
#define lookup(r, n, d) N->lookup(r, n, d)
#define reg(a, c, d) N->reg(a, c, d)
#define publish() N->publish()
#elif defined(__arm__) && UINTPTR_MAX == UINT32_MAX
#define peek(a) (*(volatile const uint32_t *)(a))
#define poke(a, v) (*(volatile uint32_t *)(a) = (v))
#define peek8(a) (*(volatile const uint8_t *)(a))
#define poke8(a, v) (*(volatile uint8_t *)(a) = (uint8_t)(v))
static void f_ctor(uintptr_t o, uint32_t v) {
    typedef void (*fn)(uintptr_t, uint32_t); ((fn)0xC0365E90u)(o, v);
}
static uint32_t f_open(uintptr_t o, const char *p, uint32_t m) {
    typedef uint32_t (*fn)(uintptr_t, const char *, uint32_t); return ((fn)0xC0365FB0u)(o, p, m);
}
static uint32_t f_read(uintptr_t o, uintptr_t b, uint32_t l, uint32_t *a) {
    typedef uint32_t (*fn)(uintptr_t, uintptr_t, uint32_t, uint32_t *);
    return ((fn)0xC0366060u)(o, b, l, a);
}
static void f_close(uintptr_t o) { typedef void (*fn)(uintptr_t); ((fn)0xC0366020u)(o); }
static void f_dtor(uintptr_t o, uint32_t m) {
    typedef void (*fn)(uintptr_t, uint32_t); ((fn)0xC0365ED0u)(o, m);
}
static uint32_t lookup(uintptr_t r, const char *n, uintptr_t *d) {   /* Thumb */
    typedef uint32_t (*fn)(uintptr_t, const char *, uintptr_t *); return ((fn)0xC05DB419u)(r, n, d);
}
static uint32_t reg(uintptr_t a, uint32_t c, const uint32_t *d) {    /* Thumb */
    typedef uint32_t (*fn)(uintptr_t, uint32_t, const uint32_t *); return ((fn)0xC05DB309u)(a, c, d);
}
static void publish(void) {       /* D-cache clean+invalidate, barriers */
    typedef void (*fn)(void); ((fn)0xC000E91Cu)();
}
#else
#error "ARM32 native menu ABI required; host tests must explicitly substitute it"
#endif

static uint32_t aligned(uintptr_t a) { return a && !(a & 3u); }
/* The NUL-terminated native string at `a` equals `s` (bounded by s). */
static uint32_t same(uintptr_t a, const char *s) {
    if (!a) return 0;
    for (uint32_t n = 0;; ++n) {
        uint32_t c = peek8(a + n);
        if (c != (uint8_t)s[n]) return 0;
        if (!c) return 1;
    }
}
static uint32_t fnv(uintptr_t a, uint32_t n, uint32_t h) {
    for (uint32_t i = 0; i < n; ++i) h = (h ^ peek8(a + i)) * 16777619u;
    return h;
}
/* A big-endian word in the page, as the NBU reader sees it. */
static void put_be(uintptr_t a, uint32_t v) {
    poke8(a, v >> 24); poke8(a + 1, v >> 16); poke8(a + 2, v >> 8); poke8(a + 3, v);
}
static uint32_t fail(struct fpl_menu *m, uint32_t why) { m->result = why; return why; }

/* The registered variable's descriptor, or 0. */
static uintptr_t variable(uintptr_t app, const char *NAME) {
    uintptr_t d = 0, registry;
    if (!aligned(app) || !aligned(peek(app + APP_REGISTRY))) return 0;
    registry = peek(app + APP_REGISTRY) + REGISTRY_OFF;
    if (lookup(registry, NAME, &d) != 0 || !aligned(d)) return 0;
    if (peek(d + DESC_TYPE) != VARIABLE_INT || !same(peek(d + DESC_NAME), NAME)) return 0;
    return d;
}

uint32_t fpl_menu_install(struct fpl_menu *m, uintptr_t area, uint32_t area_bytes) {
    uint32_t screen_words[5], path_words[5];
    const char *SCREEN = (const char *)screen_words, *PATH = (const char *)path_words;
    uintptr_t screens = 0, screen = 0, app, reader, table, entry = 0;
    uintptr_t fobj, file, page, strings, relocs, d;
    uint32_t count, actual = 0, page_len, strings_len, reloc_count, ok, room;
    uint32_t cache_from[8], cache_to[8], cached = 0;

    if (!m || m->result) return m ? m->result : FPL_MENU_NO_GUI;   /* once */
    SCREEN_WORDS(screen_words);
    PATH_WORDS(path_words);
    name_words(m);
    /* ---- the native objects, exactly as the stock firmware builds them -- */
    app = peek(GUI_OBJECT);                    /* [C37B7048]: the UI app */
    if (!aligned(app)) return fail(m, FPL_MENU_NO_GUI);
    count = peek(app + SCREENS_COUNT);
    screens = peek(app + SCREENS_ARRAY);
    if (!aligned(screens) || !count || count > MAX_SCREENS) return fail(m, FPL_MENU_NO_GUI);
    for (uint32_t n = 0; n < count && !screen; ++n) {
        uintptr_t s = peek(screens + 4u * n);
        if (aligned(s) && same(peek(s + SCREEN_NAME), SCREEN)) screen = s;
    }
    if (!screen || peek(screen + SCREEN_APP) != app) return fail(m, FPL_MENU_NO_SCREEN);
    reader = peek(screen + SCREEN_READER);
    if (!aligned(reader) || peek(reader + READER_SOURCE) != NBU_BASE ||
        !uis_pool_known(reader))              /* stock, or shared by the convention */
        return fail(m, FPL_MENU_READER);
    count = peek(reader + READER_ENTRIES);
    table = peek(reader + READER_TABLE);
    if (!aligned(table) || !count || count > MAX_SCREENS) return fail(m, FPL_MENU_READER);
    for (uint32_t n = 0; n < count && !entry; ++n) {
        uintptr_t e = table + ENTRY_BYTES * n;
        if (same(peek(e + ENTRY_NAME), SCREEN)) entry = e;
    }
    if (!entry || peek(entry + ENTRY_OFFSET) != MAINB2_OFFSET) return fail(m, FPL_MENU_NO_ENTRY);

    /* ---- the file, into memory owned for the rest of the boot --------- */
    if ((area & 7u) || area_bytes < FILE_OBJECT + FPL_MENU_AT)
        return fail(m, FPL_MENU_ROOM);
    fobj = area;
    file = area + FILE_OBJECT;
    f_ctor(fobj, VOLUME_SD);
    room = area_bytes - FILE_OBJECT;
    ok = f_open(fobj, PATH, 1);
    if (ok) {
        /* The row's data rides in fpSup.BIN past what the loader reads
         * (FPL_MENU_AT). Two reads of one open file: the first, into the same
         * buffer, only moves past the loader's part. Should the second not
         * continue from there, the magic below does not match and nothing
         * is installed. */
        ok = f_read(fobj, file, FPL_MENU_AT, &actual) && actual == FPL_MENU_AT;
        actual = 0;
        if (ok) ok = f_read(fobj, file, room, &actual);
        f_close(fobj);
    }
    f_dtor(fobj, 2);
    if (!ok) return fail(m, FPL_MENU_FILE);
    m->file_len = actual;
    if (actual >= room) return fail(m, FPL_MENU_ROOM);    /* may not be all of it */
    page_len = peek(file + 0x08);
    strings_len = peek(file + 0x0C);
    reloc_count = peek(file + 0x10);
    if (actual < FPL_MENU_FILE_HEADER || peek(file) != FPL_MENU_MAGIC ||
        peek(file + 4) != FPL_MENU_VERSION || peek(file + 0x14) != MAINB2_OFFSET ||
        !page_len || page_len > actual || strings_len > actual || reloc_count > actual / 8u ||
        actual - FPL_MENU_FILE_HEADER < page_len + strings_len + 8u * reloc_count)
        return fail(m, FPL_MENU_FORMAT);        /* padding after: putfile cannot shorten */
    page = file + FPL_MENU_FILE_HEADER;
    strings = page + page_len;
    relocs = strings + strings_len;
    if (fnv(page, page_len + strings_len + 8u * reloc_count, 2166136261u) != peek(file + 0x18))
        return fail(m, FPL_MENU_FORMAT);
    /* Every reference first, so a bad file changes nothing. */
    for (uint32_t r = 0; r < reloc_count; ++r) {
        uint32_t at = peek(relocs + 8u * r), s = peek(relocs + 8u * r + 4u), n = 0;
        if (at > page_len - 4u || s >= strings_len) return fail(m, FPL_MENU_FORMAT);
        while (s + n < strings_len && peek8(strings + s + n)) ++n;
        if (!n || s + n >= strings_len) return fail(m, FPL_MENU_FORMAT);
    }
    /* ---- the page's private strings: offsets from the shared pool ------- */
    for (uint32_t r = 0; r < reloc_count; ++r) {
        uint32_t at = peek(relocs + 8u * r), s = peek(relocs + 8u * r + 4u), n = 0, off = 0, k;
        for (k = 0; k < cached && cache_from[k] != s; ++k) {}
        if (k < cached) {
            off = cache_to[k];
        } else {
            char text[64];                 /* uis_intern wants a C string */
            while (peek8(strings + s + n)) {
                if (n + 1 >= sizeof text) return fail(m, FPL_MENU_FORMAT);
                text[n] = (char)peek8(strings + s + n); ++n;
            }
            text[n] = 0;
            if (uis_intern(text, n, &off) != UIS_OK) return fail(m, FPL_MENU_POOL);
            if (cached < 8u) { cache_from[cached] = s; cache_to[cached] = off; ++cached; }
        }
        put_be(page + at, off);
    }

    /* ---- the variables: registered once, 0, memory only ---------------- */
    for (uint32_t n = 0; n < FPL_MENU_VARIABLES; ++n) {
        const char *NAME = (const char *)m->names[n];
        d = variable(app, NAME);
        if (!d) {
            uint32_t def[3];
            def[0] = VARIABLE_INT;
            def[1] = (uint32_t)(uintptr_t)NAME;
            def[2] = 0u;                               /* OFF */
            if (reg(app, 1, def) != 0) return fail(m, FPL_MENU_REGISTER);
            m->registered++;
            d = variable(app, NAME);
        }
        if (!d) return fail(m, FPL_MENU_VARIABLE);
    }
    publish();

    /* ---- switch MainB2 to the page -------------------------------------- */
    poke(entry + ENTRY_OFFSET, page - NBU_BASE);
    publish();
    m->app = app; m->reader = reader; m->entry = entry; m->page = page; m->page_len = page_len;
    m->pool = peek(reader + READER_POOL); m->pool_len = peek(reader + READER_POOL_LEN);
    m->result = FPL_MENU_INSTALLED;
    return FPL_MENU_INSTALLED;
}

uint32_t fpl_menu_on(struct fpl_menu *m) {
    uintptr_t d;
    if (!m || m->result != FPL_MENU_INSTALLED) return 0;
    if (m->reads != UINT32_MAX) ++m->reads;
    d = variable(m->app, (const char *)m->names[0]);
    if (!d || peek(d + DESC_VALUE) != 1u) return 0;
    if (m->on_reads != UINT32_MAX) ++m->on_reads;
    return 1;
}
