#ifndef FPLOSSLESS_MENU_PAGE_H
#define FPLOSSLESS_MENU_PAGE_H
#include <stdint.h>

/* The Lossless RAW row on SHOOT page 2 (CINE), under Audio Recording.
 *
 * No hook. MainB2 is parsed lazily, every time the Settings pages open (the
 * UI resource cache keeps one menu page and drops it when the menu opens,
 * measured 2026-09-30), by C05E82F1: it finds MainB2's 44-byte runtime entry
 * in reader+A8/+AC, sets the reader cursor to entry+08 (an offset from the
 * NBU base at reader+24), and parses records from there, resolving string
 * offsets against the pool at reader+14 (length reader+10). So:
 *
 *   - the private page (menu/build_menu_candidate.py, a complete MainB2 with
 *     the fourth row) rides in fpSup.BIN after the loader's 61440 bytes and
 *     is read from there into memory that is never freed;
 *   - the page's few private strings get their offsets from the shared pool
 *     (fpSup/uishare/ui_pool.h, uis_intern) and are written into the page;
 *     the pool is nobody's: another sup may already have extended it;
 *   - MV_fpLossless (and the row's two helpers) are registered once, integers
 *     defaulting to 0 (OFF). They live in the UI variable registry, i.e. in
 *     memory only: nothing is ever written to the settings store, and every
 *     boot starts OFF;
 *   - then MainB2's page offset is switched.
 *
 * All or nothing: every runtime structure is checked against what the stock
 * firmware builds before anything is written; any mismatch installs nothing.
 * The switched words are heap objects rebuilt at every boot. */

#define FPL_MENU_MAGIC       0x4D4C5046u   /* "FPLM", the card file */
#define FPL_MENU_FILE_HEADER 0x20u
#define FPL_MENU_VERSION     2u
/* where the row's data starts in fpSup.BIN: right past the loader's MAXLEN
 * (fp_usb_shell/templates/loader.S), so the loader never sees it */
#define FPL_MENU_AT          0xF000u
#define FPL_MENU_VARIABLES   3u

enum fpl_menu_result {
    FPL_MENU_INSTALLED = 1,
    FPL_MENU_NO_GUI = 2, FPL_MENU_NO_SCREEN = 3, FPL_MENU_READER = 4,
    FPL_MENU_NO_ENTRY = 5, FPL_MENU_ROOM = 6, FPL_MENU_FILE = 7,
    FPL_MENU_FORMAT = 8, FPL_MENU_REGISTER = 9, FPL_MENU_VARIABLE = 10,
    FPL_MENU_POOL = 11              /* the shared string pool refused */
};

struct fpl_menu {
    uint32_t result;                /* enum fpl_menu_result, 0 = never tried */
    uintptr_t app, reader, entry;   /* the native objects switched */
    uintptr_t page, pool;
    uint32_t page_len, pool_len, file_len;
    uint32_t registered;            /* how many this boot registered */
    uint32_t reads, on_reads;       /* REC-time lookups; how many said ON */
    /* The row's private integers, all registered 0: its value, the popup's
     * staging value and its lock (menu/build_menu_candidate.py audio-toggle).
     * The registry BORROWS each name, so they live here, in state that is
     * never freed, not on a stack. names[0] is the value. */
    uint32_t names[FPL_MENU_VARIABLES][5];
};

/* `area` is memory owned for the rest of the boot (never freed), 8-aligned;
 * it receives the file object, the page and the pool. */
uint32_t fpl_menu_install(struct fpl_menu *, uintptr_t area, uint32_t area_bytes);

/* 1 if the row reads ON right now; 0 for OFF, not installed, or unknown. */
uint32_t fpl_menu_on(struct fpl_menu *);

#if defined(FPL_MENU_HOST_TEST)
struct fpl_menu_natives {
    uint32_t (*read)(uintptr_t);
    void (*write)(uintptr_t, uint32_t);
    uint32_t (*read_byte)(uintptr_t);
    void (*write_byte)(uintptr_t, uint32_t);
    /* the file object: ctor(obj, volume), open(obj, path, mode) -> 0 fails,
     * read(obj, buf, len, &actual) -> 0 fails, close(obj), dtor(obj, 2) */
    void (*f_ctor)(uintptr_t, uint32_t);
    uint32_t (*f_open)(uintptr_t, const char *, uint32_t);
    uint32_t (*f_read)(uintptr_t, uintptr_t, uint32_t, uint32_t *);
    void (*f_close)(uintptr_t);
    void (*f_dtor)(uintptr_t, uint32_t);
    /* C05DB419(registry, name, &descriptor): 0 for hit and miss */
    uint32_t (*lookup)(uintptr_t, const char *, uintptr_t *);
    /* C05DB309(app, count, definitions) */
    uint32_t (*reg)(uintptr_t, uint32_t, const uint32_t *);
    void (*publish)(void);
};
extern const struct fpl_menu_natives fpl_menu_test_natives;
#endif
#endif
