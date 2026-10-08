#include "menu_page.h"
#include "ui_pool.h"      /* fpSup/uishare: the shared string pool convention */
#include "ui_apply.h"     /* fpSup/uishare: pages composed from each sup's FPUI block */

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

/* Where the row's data starts, from the VBIN header at `h`: the loader's read
 * cap for a VBIN that size (menu_page.h FPL_MENU_AT_*); 0 if it is no VBIN or
 * would need more than the loader can be told to read. */
static uint32_t menu_at(uintptr_t h) {
    uint32_t count = peek(h + 4), body = peek(h + 12), used;
    if (peek(h) != FPL_VBIN_MAGIC || count > FPL_MENU_AT_MAX / 8u || body > FPL_MENU_AT_MAX)
        return 0;
    used = 16u + 8u * count + body;
    if (used <= FPL_MENU_AT_MIN) return FPL_MENU_AT_MIN;
    used = (used + FPL_MENU_AT_STEP - 1u) & ~(FPL_MENU_AT_STEP - 1u);
    return used <= FPL_MENU_AT_MAX ? used : 0;
}

/* `data`/`data_len`: the FPUI block already in memory (Loader v3: it rides in
 * the sup's own block), or 0 to read it out of \fpSup.BIN as before. */
static uint32_t install(struct fpl_menu *m, uintptr_t area, uint32_t area_bytes,
                        uintptr_t data, uint32_t data_len) {
    uint32_t screen_words[5], path_words[5];
    const char *SCREEN = (const char *)screen_words, *PATH = (const char *)path_words;
    uintptr_t screens = 0, screen = 0, app, reader, fobj, file, d;
    uint32_t count, actual = 0, ok, room, at = 0;
    struct uia_outcome ui;

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
    /* Whether MainB2 is the stock page or a copy another sup composed is
     * ui_apply's to check (uishare: stock, or announced by its header). */

    /* ---- the file, into memory owned for the rest of the boot --------- */
    if (data) {
        file = data;
        actual = data_len;
        m->file_at = 0;
        goto have_block;
    }
    if ((area & 7u) || area_bytes < FILE_OBJECT + FPL_MENU_AT_STEP)
        return fail(m, FPL_MENU_ROOM);
    fobj = area;
    file = area + FILE_OBJECT;
    f_ctor(fobj, VOLUME_SD);
    room = area_bytes - FILE_OBJECT;
    ok = f_open(fobj, PATH, 1);
    if (ok) {
        /* The row's FPUI block rides in fpSup.BIN past what the loader
         * reads; where that is comes from the VBIN header (menu_at). Reads of
         * one open file: the header, then up to `room` at a time to move past
         * the rest of the loader's part, then the block. Should a read not
         * continue from the last, the block's magic or checksum does not
         * match and nothing is installed. */
        ok = f_read(fobj, file, 16u, &actual) && actual == 16u;
        if (ok) {
            at = menu_at(file);
            ok = at != 0u;
        }
        for (uint32_t done = 16u; ok && done < at; done += actual) {
            uint32_t want = at - done < room ? at - done : room;
            actual = 0;
            ok = f_read(fobj, file, want, &actual) && actual == want;
        }
        actual = 0;
        if (ok) ok = f_read(fobj, file, room, &actual);
        f_close(fobj);
    }
    f_dtor(fobj, 2);
    m->file_at = at;
    if (!ok) return fail(m, FPL_MENU_FILE);
    m->file_len = actual;
    if (actual >= room) return fail(m, FPL_MENU_ROOM);    /* may not be all of it */
have_block:
    m->file_len = actual;
    if (actual < 0x20u || peek8(file) != 'F' || peek8(file + 1) != 'P' ||
        peek8(file + 2) != 'U' || peek8(file + 3) != 'I')
        return fail(m, FPL_MENU_FORMAT);                  /* padding after: putfile cannot shorten */

    /* ---- the variables: registered once, 0, memory only ---------------- */
    for (uint32_t n = 0; n < FPL_MENU_VARIABLES; ++n) {
        const char *NAME = (const char *)m->names[n];
        d = variable(app, NAME);
        if (!d) {
            uint32_t def[3];
            def[0] = VARIABLE_INT;
            def[1] = (uint32_t)(uintptr_t)NAME;
            /* the value and its popup staging start at the saved state */
            def[2] = n < 2u && m->initial == 1u ? 1u : 0u;
            if (reg(app, 1, def) != 0) return fail(m, FPL_MENU_REGISTER);
            m->registered++;
            d = variable(app, NAME);
        }
        if (!d) return fail(m, FPL_MENU_VARIABLE);
    }
    publish();

    /* ---- the row: added to MainB2 by the shared UI convention ----------- */
    m->ui_result = uia_apply(file, actual, &ui);
    m->ui_op = ui.op;
    if (m->ui_result != UIA_OK) return fail(m, FPL_MENU_UI);
    m->app = app; m->reader = reader; m->page = ui.page;
    m->page_len = peek((ui.page & ~(uintptr_t)3) - UIA_HEADER + 12);
    m->first_id = ui.first_id;
    m->row = ui.n_slots ? ui.slots[0] : 0;
    m->pool = peek(reader + READER_POOL); m->pool_len = peek(reader + READER_POOL_LEN);
    m->result = FPL_MENU_INSTALLED;
    return FPL_MENU_INSTALLED;
}

uint32_t fpl_menu_install(struct fpl_menu *m, uintptr_t area, uint32_t area_bytes) {
    return install(m, area, area_bytes, 0, 0);
}

uint32_t fpl_menu_install_data(struct fpl_menu *m, uintptr_t data, uint32_t data_len) {
    if (!data || !data_len) return m ? fail(m, FPL_MENU_FILE) : FPL_MENU_NO_GUI;
    return install(m, 0, 0, data, data_len);
}

uint32_t fpl_menu_now(struct fpl_menu *m) {
    uintptr_t d;
    const char *value_name;
    if (!m || m->result != FPL_MENU_INSTALLED) return UINT32_MAX;
    value_name = (const char *)m->names[0];
    d = variable(m->app, value_name);
    return d ? peek(d + DESC_VALUE) : UINT32_MAX;
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
