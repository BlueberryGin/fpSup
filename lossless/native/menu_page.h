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
 *   - the row (a clone of the same page's Audio row, menu/build_menu_candidate.py)
 *     rides in fpSup.BIN past what the loader reads, as an FPUI block of
 *     ADDITIONS (menu/build_fpui.py, 2026-10-03): the cloned records, their
 *     object IDs and private strings to fill in, the header budgets, the
 *     parent's child count and the ModeChange root;
 *   - MV_fpLossless (and the row's two helpers) are registered once, integers
 *     defaulting to 0 (OFF). They live in the UI variable registry, i.e. in
 *     memory only: nothing is ever written to the settings store, and every
 *     boot starts OFF;
 *   - then fpSup/uishare/ui_apply.c copies MainB2 as it is now (stock, or a
 *     copy another sup already added a row to), adds this row -- new object
 *     IDs, string offsets from the shared pool, the next row's y -- and
 *     switches MainB2's entry to the copy.
 *
 * All or nothing: every runtime structure is checked against what the stock
 * firmware builds before anything is written; any mismatch installs nothing.
 * The switched words are heap objects rebuilt at every boot. */

/* The row's data in fpSup.BIN is an FPUI block (fpSup/uishare/ui/fpui.py,
 * built by menu/build_fpui.py): what the row ADDS to the stock MainB2, which
 * fpSup/uishare/ui_apply.c composes onto whatever MainB2 is now -- stock, or
 * a copy another sup already added to. */
/* Where the row's data starts in fpSup.BIN: past the VBIN the loader reads,
 * at the loader's read cap for this card -- MIN (loader.S MAXLEN) when the
 * VBIN fits it, else the VBIN's end rounded up to STEP, which is exactly the
 * read fpSup-Merge gives a card that needs more (template.html capFor), up to
 * MAX (build_autorun --read-cap's ceiling). Worked out from the file's own
 * VBIN header at boot (2026-10-03), so a merge that grows the VBIN moves the
 * data and this code with it; a card whose VBIN fits MIN is laid out exactly
 * as before. */
#define FPL_MENU_AT_MIN      0xF000u
#define FPL_MENU_AT_STEP     0x1000u
#define FPL_MENU_AT_MAX      0x1F000u
#define FPL_VBIN_MAGIC       0x4E494256u   /* "VBIN" */
#define FPL_MENU_VARIABLES   3u

enum fpl_menu_result {
    FPL_MENU_INSTALLED = 1,
    FPL_MENU_NO_GUI = 2, FPL_MENU_NO_SCREEN = 3, FPL_MENU_READER = 4,
    FPL_MENU_NO_ENTRY = 5, FPL_MENU_ROOM = 6, FPL_MENU_FILE = 7,
    FPL_MENU_FORMAT = 8, FPL_MENU_REGISTER = 9, FPL_MENU_VARIABLE = 10,
    FPL_MENU_POOL = 11,             /* the shared string pool refused (FPLM; unused) */
    FPL_MENU_UI = 12                /* ui_apply refused the block: ui_result says why */
};

struct fpl_menu {
    uint32_t result;                /* enum fpl_menu_result, 0 = never tried */
    uintptr_t app, reader, entry;   /* the native objects (entry: unused since FPUI) */
    uintptr_t page, pool;
    uint32_t page_len, pool_len, file_len;
    uint32_t file_at;               /* where the data was read from (0 = no VBIN) */
    uint32_t ui_result, ui_op;      /* ui_apply's result and the op it stopped at */
    uint32_t first_id, row;         /* the row's first object id and its y */
    uint32_t registered;            /* how many this boot registered */
    uint32_t reads, on_reads;       /* REC-time lookups; how many said ON */
    /* The row's private integers, all registered 0: its value, the popup's
     * staging value and its lock (menu/build_menu_candidate.py audio-toggle).
     * The registry BORROWS each name, so they live here, in state that is
     * never freed, not on a stack. names[0] is the value. */
    uint32_t names[FPL_MENU_VARIABLES][5];
    uint32_t initial;               /* the value (and its popup staging) registers
                                       with: 0 OFF, 1 ON -- the saved state */
};

/* `area` is memory owned for the rest of the boot (never freed), 8-aligned;
 * it receives the file object, the page and the pool. */
uint32_t fpl_menu_install(struct fpl_menu *, uintptr_t area, uint32_t area_bytes);

/* Loader v3: the same, with the FPUI block already in memory -- it rides in
 * the sup's own block -- instead of read out of \fpSup.BIN. */
uint32_t fpl_menu_install_data(struct fpl_menu *, uintptr_t data, uint32_t data_len);

/* The row's value right now (0 OFF, 1 ON), or UINT32_MAX when the row is not
 * installed or the variable is gone. Counts nothing: for the saved state. */
uint32_t fpl_menu_now(struct fpl_menu *);

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
