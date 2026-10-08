/* Loader v3's built-in page: SYSTEM page 6, "All Sups" (projects/usb-shell-sup/
 * notes/ALL_SUPS_MENU.md).  Compiled into LOADER.BIN in one unit after sloader.c
 * (build_v3.py, allsups=True); sloader.c enters it as a sup of its own, last,
 * under the name LOADER.BIN, so its claims, its uishare string layer and its
 * log lines are in the books like any sup's (sigmafp-re-70, 2026-10-07).
 *
 * The data -- this table, the FPUI block, the texts, the NBR pack with FT_Y6
 * and FT_LIST -- is \fpSup\UI\ALLSUPS.BIN (fpSup/uishare/tab/build_ft6.py
 * --data), not LOADER.BIN.  The mechanism was proven on the camera as the
 * stand-alone sup 90FTAB6.BIN (r1, r2c) and inside the loader (r3a):
 *
 *   1 claims: 0xC056461C CHAIN, the three string sites SHARED_UI; an 8-byte
 *     cave veneer                                       (refused: RELEASE)
 *   2 uia_apply: the string layer goes up; every STRBASE (relocated here to
 *     the table's offset array, the block's hash redone) hands one private
 *     string's offset to it
 *   3 the private strings' fields in the pages (big-endian); the list: every
 *     *.BIN and *.OFF in \fpSup but this loader, what the books say became
 *     of it; the private variables (FT_Key, FT_N1..FT_N17, FT_Focus) registered with
 *     the list as their first values; FT_Key subscribed
 *   4 the pack through the stock loader C05E84D8 after register_pages' checks;
 *     the new reader takes the stock reader's pool; the new machine goes to
 *     app+0xF4 slot 0
 *   5 the page-id layer at 0xC056461C: "FT_..." -> MainY5's GUI id
 * From 2 on the data block stays: KEEP.
 *
 * A row's Right / OK writes FT_Key = its number.  allsups_key (UI thread)
 * renames that file NAME.BIN <-> NAME.OFF (the shell's `fl rename`: file
 * object, open mode 7, C0366508) and posts the cell's new text. */

#define FT6_MAGIC   0x54365446u        /* "FT6T" (= sloader.c ALLSUPS_MAGIC) */
#define GUI_SITE    0xC056461Cu
#define GUI_NEXT0   0xC0564638u
#define GUI_OBJECT_AT 0xC37B7048u
#define NBR_LOAD    0xC05E84D9u        /* Thumb */
#define NBU_BASE_AT 0xC18C0460u
#define VAR_REGISTER 0xC05DB309u       /* Thumb: (app, n, {type, name, first value}) */
#define VAR_SUBSCRIBE 0xC0560FB0u      /* ARM: (GUI object, name, fn) -- fn(descriptor) on the UI thread */
#define POST_INT    0xC0593F30u        /* ARM: (0, name, value) queued to the GUI */
#define POST_STR    0xC0593FE0u        /* ARM: (0, name, text) -- the GUI keeps the pointer */
#define F_RENAME    0xC0366508u        /* ARM: (file object, new path), 0 = failed */
#define FILE_OBJECT_BYTES 0x1000u
#define DESC_VALUE  0x08u

#define APP_SCREENS_N   0x80u
#define APP_SCREENS_CAP 0x84u
#define APP_SCREENS     0x8Cu
#define APP_MACH_N      0xF8u
#define APP_MACH_CAP    0xFCu
#define APP_MACH        0x104u
#define SCREEN_READER   0x24u
#define READER_APP      0x08u
#define READER_POOL_LEN 0x10u
#define READER_POOL     0x14u
#define READER_BASE     0x24u
#define MACH_NAME       0x08u

#define FT6_ROWS    17u                /* build_ft6.ROWS: the language grid's cells */
enum { T_UNLOAD, T_LOAD, T_FAILED, T_ERROR, T_DIR, T_BIN, T_OFF_EXT, T_BLANK,
       T_I_OK, T_I_AEL, T_I_MENU, T_SP, T_W_UNINSTALL, T_W_INSTALL, T_W_CANCEL, T_COUNT };
#define AEL_BASE    100u               /* ft6_page.AEL_BASE: AEL on cell k writes FT_Key = 100 + k */
enum { P_NAME, P_GREY, P_VER, P_RED, P_GREEN, P_COUNT };   /* ft6_page.CELL_PARTS */
enum { BOOT_ON, BOOT_OFF, BOOT_FAILED };

/* status: 0 ok; 1 claim refused; 2 no cave; 10+r uia_apply refused with r;
 * 3 no app; 4 no stock reader; 5 no room; 6 loader refused; 7 bad reader;
 * 8 machine count did not grow by one; 9 last machine is not ours;
 * 90+ a variable would not register */
struct ft6_row {
    char base[24];                     /* "10LOSS": the file's name without .BIN / .OFF */
    char disp[24];                     /* "Lossless": the product's name -- the GUI keeps the pointer */
    uint32_t ver;                      /* "v0.2.0test" (data offset), 0 = none known  */
    uint32_t boot, bin, err;           /* this boot; on at the next (not on the list); the write failed */
    uint32_t id, legacy;               /* its sup_id (0: none read); a NAME.OFF file (not switchable) */
    uint32_t sup;                      /* 1 + its index in the books when it loaded, else 0 */
    char foot[196];                    /* FT_OK for this cell: the GUI keeps the pointer */
};
struct ft6_tab {                       /* build_ft6.py data_file(): the same words  */
    uint32_t magic, pack_off, pack_len;
    uint32_t ui_off, n_ui;             /* FPUI blocks: {offset, bytes, first string#} */
    uint32_t n_str, soff_off;          /* STRBASE fills soff[string#]               */
    uint32_t n_refs, refs_off;         /* {pack offset, string#}: BE fields         */
    uint32_t n_vars, vars_off;         /* {type, name}: FT_Key, FT_<part><k> by part, FT_Focus */
    uint32_t txt[T_COUNT];             /* data offsets of the fixed texts           */
    uint32_t rows_off;                 /* FT6_ROWS x struct ft6_row, zero in the file */
    uint32_t n_prod, prod_off;         /* {sup_id, name, version}: data offsets      */
    uint32_t stock[4];                 /* GUI site, then the three string sites     */
    char machine[12];                  /* "TransFtTab": no .rodata here             */
    uint32_t status, ven, rd, app, mach, ui_result, ui_op;
    uint32_t n_rows, fobj, keys;       /* run time                                  */
    uint32_t st, path;                 /* the loader's state; "\fpSup\LOADER.BIN"     */
};

typedef uint32_t (*fn3)(uint32_t, uint32_t, uint32_t);
typedef uint32_t (*fn4)(uint32_t, uint32_t, uint32_t, uint32_t);

static inline uint32_t rd32(uintptr_t a) { return *(volatile uint32_t *)a; }
static inline void wr32(uintptr_t a, uint32_t v) { *(volatile uint32_t *)a = v; }
static inline int bad(uintptr_t p) { return !p || (p & 3u); }

/* The page-id layer, ARM, called by `bl` through the veneer: r0 = the
 * CustomScreenLoader, r1 = the page name.  Only ip is touched on the way in.
 * ft6_next / ft6_ctx: words in .text (RAM), written at load. */
__asm__(
    ".text\n"
    ".balign 4\n"
    ".arm\n"
    "ft6_layer:\n"
    "    cmp     r1, #0\n"
    "    beq     1f\n"
    "    ldrb    ip, [r1]\n"
    "    cmp     ip, #0x46\n"              /* 'F' */
    "    ldrbeq  ip, [r1, #1]\n"
    "    cmpeq   ip, #0x54\n"              /* 'T' */
    "    ldrbeq  ip, [r1, #2]\n"
    "    cmpeq   ip, #0x5F\n"              /* '_' */
    "    moveq   r0, #21\n"                /* MainY5's GUI id (build_ft6.GUI_ID) */
    "    bxeq    lr\n"
    "1:  ldr     ip, ft6_next\n"
    "    bx      ip\n"
    "ft6_next:\n"
    "    .word   0\n"
    "ft6_ctx:\n"
    "    .word   0\n"
    ".thumb\n");
extern const uint8_t ft6_layer[];
extern const uint32_t ft6_next;      /* const: -fropi addresses it PC-relative */
extern const uint32_t ft6_ctx;       /* the data block, for allsups_key */

/* the FPUI block's hash (ui_apply.c fnv, FNV-1a) */
static uint32_t ft6_fnv(uintptr_t p, uint32_t n)
{
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < n; ++i) h = (h ^ *(const volatile uint8_t *)(p + i)) * 16777619u;
    return h;
}

static uint32_t ft6_be(uint32_t v)
{
    return (v >> 24) | ((v >> 8) & 0xFF00u) | ((v << 8) & 0xFF0000u) | (v << 24);
}

static int ft6_same(uintptr_t a, const char *b)
{
    for (;; ++a, ++b) {
        if (*(const volatile uint8_t *)a != (uint8_t)*b) return 0;
        if (!*b) return 1;
    }
}

static char *ft6_cat(char *p, const char *s)
{
    while (*s) *p++ = *s++;
    *p = 0;
    return p;
}

static const char *ft6_text(uint8_t *block, uint32_t i)
{
    return (const char *)(block + ((struct ft6_tab *)block)->txt[i]);
}

static struct ft6_row *ft6_rows(uint8_t *block)
{
    return (struct ft6_row *)(block + ((struct ft6_tab *)block)->rows_off);
}

static const char *ft6_var(uint8_t *block, uint32_t i)
{
    struct ft6_tab *t = (struct ft6_tab *)block;
    return (const char *)(block + rd32((uintptr_t)(block + t->vars_off + 8u * i + 4u)));
}

/* variable index of cell k's part p (build_ft6.variables) */
/* the variables are registered red, green, name, grey, version (build_ft6
 * .VAR_PARTS): the posted ones first */
static uint32_t ft6_slot(uint32_t p)
{
    return p == P_RED ? 0u : p == P_GREEN ? 1u : p == P_NAME ? 2u : p == P_GREY ? 3u : 4u;
}
static uint32_t ft6_part_of(uint32_t slot)
{
    return slot == 0u ? P_RED : slot == 1u ? P_GREEN : slot == 2u ? P_NAME : slot == 3u ? P_GREY : P_VER;
}
/* 0 FT_Key, 1 FT_OK, then the cells' parts, FT_Focus last (build_ft6.variables) */
#define VAR_OK 1u
static uint32_t ft6_vi(uint32_t p, uint32_t k) { return 2u + ft6_slot(p) * FT6_ROWS + (k - 1u); }

/* What cell part p shows (the user 2026-10-07): the product's name white when
 * it loaded this boot, grey when not; small, its version; red "Unload on
 * restart" / "Load failed" / "Write failed", green "Load on restart" (the
 * texts: build_ft6.TEXTS).  A part not shown is " ". */
static const char *ft6_part(uint8_t *block, const struct ft6_row *r, uint32_t p)
{
    uint32_t t = T_BLANK;
    if (!r->base[0]) return ft6_text(block, T_BLANK);
    if (p == P_NAME) return r->boot == BOOT_ON ? r->disp : ft6_text(block, T_BLANK);
    if (p == P_GREY) return r->boot != BOOT_ON ? r->disp : ft6_text(block, T_BLANK);
    if (p == P_VER) return r->ver ? (const char *)(block + r->ver) : ft6_text(block, T_BLANK);
    if (p == P_RED)
        t = r->err ? T_ERROR : r->boot == BOOT_ON && !r->bin ? T_UNLOAD
          : r->boot == BOOT_FAILED && r->bin ? T_FAILED : T_BLANK;
    if (p == P_GREEN)
        t = !r->err && r->boot == BOOT_OFF && r->bin ? T_LOAD : T_BLANK;
    return ft6_text(block, t);
}

/* The product's name and version by the sup_id in the file's header; a sup
 * the table does not know: its file name without the load-order digits. */
static void ft6_name(uint8_t *block, struct ft6_row *r, uint32_t id)
{
    struct ft6_tab *t = (struct ft6_tab *)block;
    const char *n = r->base;
    for (uint32_t i = 0; id && i < t->n_prod; i++) {
        uintptr_t e = (uintptr_t)(block + t->prod_off) + 12u * i;
        if (rd32(e) != id) continue;
        n = (const char *)(block + rd32(e + 4));
        r->ver = rd32(e + 8);
        copy(r->disp, n, slen(n) + 1);
        return;
    }
    while (*n >= '0' && *n <= '9' && n[1]) n++;
    copy(r->disp, n, slen(n) + 1);
}

/* "\fpSup" + '\\' + base + ".BIN" / ".OFF" */
static void ft6_path(uint8_t *block, char *p, const struct ft6_row *r, uint32_t bin)
{
    p = ft6_cat(p, ft6_text(block, T_DIR));
    *p++ = 0x5C;
    p = ft6_cat(p, r->base);
    ft6_cat(p, ft6_text(block, bin ? T_BIN : T_OFF_EXT));
}

/* LOADER.BIN's disabled list (sloader.c DS_*): put `id` on it or take it off,
 * then write the file back whole -- the same length, so mode 7's no-truncate
 * cannot leave a tail.  1 = written. */
static uint32_t ft6_disable(struct ft6_tab *t, uint32_t id, uint32_t off)
{
    struct state *st = (struct state *)(uintptr_t)t->st;
    u32 *d = st ? st->dis : 0, n, i, ok, was[DS_MAX + 2];
    if (!d) return 2;
    if (!id) return 3;
    copy(was, d, DS_BYTES);
    n = d[1];
    for (i = 0; i < n && d[2 + i] != id; i++) {}
    if (off && i == n) {
        if (n == DS_MAX) return 4;
        d[2 + n] = id;
        d[1] = n + 1u;
    } else if (!off && i < n) {
        d[2 + i] = d[1 + n];
        d[1 + n] = 0;
        d[1] = n - 1u;
    }
    f_ctor((void *)(uintptr_t)t->fobj, DRIVE_SD);
    ok = f_create((void *)(uintptr_t)t->fobj, (const char *)(uintptr_t)t->path) ? 1u : 5u;
    if (ok == 1u) {
        ok = f_write((void *)(uintptr_t)t->fobj, st->file, st->file_len) != 0 ? 1u : 6u;
        f_close((void *)(uintptr_t)t->fobj);
    }
    f_dtor((void *)(uintptr_t)t->fobj);
    if (ok != 1u) copy(d, was, DS_BYTES);       /* the list stays what the card has */
    return ok;                                  /* 1 written; 2 no list, 3 no id, 4 full, 5 open, 6 write */
}

/* Cell r's footer (the user 2026-10-08): "[OK] Uninstall" for a sup on at the
 * next boot, "[OK] Install" for one off, nothing for a cell with nothing to
 * switch; "[AEL] <its label>" when the sup took the key (svc->button);
 * "[MENU] Cancel". */
static void ft6_foot(uint8_t *block, struct state *st, struct ft6_row *r)
{
    char *p = r->foot, *end = r->foot + sizeof r->foot - 1;
    const char *parts[8];
    uint32_t n = 0;
    if (r->id && !r->legacy) {
        parts[n++] = ft6_text(block, T_I_OK);
        parts[n++] = ft6_text(block, r->bin ? T_W_UNINSTALL : T_W_INSTALL);
        parts[n++] = ft6_text(block, T_SP);
    }
    if (r->sup && st->btn_fn[r->sup - 1u]) {
        parts[n++] = ft6_text(block, T_I_AEL);
        if (st->btn_label[r->sup - 1u]) parts[n++] = st->btn_label[r->sup - 1u];
        parts[n++] = ft6_text(block, T_SP);
    }
    parts[n++] = ft6_text(block, T_I_MENU);
    parts[n++] = ft6_text(block, T_W_CANCEL);
    for (uint32_t i = 0; i < n; i++)
        for (const char *q = parts[i]; *q && p < end; q++) *p++ = *q;
    *p = 0;
}

static const char *ft6_footer(uint8_t *block, uint32_t k)
{
    if (!k || k > FT6_ROWS) return ft6_text(block, T_BLANK);
    return ft6_rows(block)[k - 1u].foot;       /* a cell past the list: "[MENU] Cancel" */
}

/* FT_Focus's subscriber (UI thread): the grid's cursor moved to cell value+1 */
static uint32_t allsups_focus(uintptr_t desc)
{
    uint8_t *block = (uint8_t *)(uintptr_t)ft6_ctx;
    uint32_t v = rd32(desc + DESC_VALUE);
    if (!block || v >= FT6_ROWS) return 0;
    FW(POST_STR, fn3)(0, (uint32_t)(uintptr_t)ft6_var(block, VAR_OK), (uint32_t)(uintptr_t)ft6_footer(block, v + 1u));
    return 0;
}

/* FT_Key's subscriber (UI thread): put row k's sup on the list or take it
 * off (no file is renamed: the user 2026-10-08 -- a running sup writes back to
 * its own file at power-off, and a renamed one left a stub), post its texts */
static uint32_t allsups_key(uintptr_t desc)
{
    uint8_t *block = (uint8_t *)(uintptr_t)ft6_ctx;
    struct ft6_tab *t = (struct ft6_tab *)block;
    uint32_t k = rd32(desc + DESC_VALUE);
    if (!block) return 0;
    if (k > AEL_BASE && k <= AEL_BASE + t->n_rows) {  /* AEL: the cell's sup decides */
        struct ft6_row *a = &ft6_rows(block)[k - AEL_BASE - 1u];
        struct state *st = (struct state *)(uintptr_t)t->st;
        int (*fn)(void *) = a->sup ? st->btn_fn[a->sup - 1u] : 0;
        int ret = fn ? fn(st->btn_arg[a->sup - 1u]) : -1;
        /* FT_Key back past the cells: 2000 + the cell + its handler's answer x 100
         * (gui geti FT_Key); no handler: 2000 + the cell - 100 */
        FW(POST_INT, fn3)(0, (uint32_t)(uintptr_t)ft6_var(block, 0),
                          2000u + (k - AEL_BASE) + (uint32_t)(ret * 100));
        return 0;
    }
    if (!k || k > t->n_rows) return 0;
    struct ft6_row *r = &ft6_rows(block)[k - 1];
    if (r->legacy || !r->id) return 0;           /* nothing to put on the list */
    t->keys++;
    uint32_t rc = ft6_disable(t, r->id, r->bin), ok = rc == 1u;
    r->err = !ok;
    if (ok) r->bin = !r->bin;
    ft6_foot(block, (struct state *)(uintptr_t)t->st, r);
    for (uint32_t p = P_RED; p <= P_GREEN; p++)
        FW(POST_STR, fn3)(0, (uint32_t)(uintptr_t)ft6_var(block, ft6_vi(p, k)),
                          (uint32_t)(uintptr_t)ft6_part(block, r, p));
    /* FT_Key back to "no row" -- a number past the cells, so the same row
     * writes a change; it says what happened (gui geti FT_Key):
     * 1000 + ft6_disable's code x 10 + the list was found at boot */
    FW(POST_STR, fn3)(0, (uint32_t)(uintptr_t)ft6_var(block, VAR_OK), (uint32_t)(uintptr_t)ft6_footer(block, k));
    FW(POST_INT, fn3)(0, (uint32_t)(uintptr_t)ft6_var(block, 0),
                      1000u + rc * 10u + (((struct state *)(uintptr_t)t->st)->dis ? 1u : 0u));
    return 0;
}

/* Every *.BIN and *.OFF in \fpSup but this loader, sorted, at most FT6_ROWS:
 * what the books say became of each. */
static uint32_t ft6_list(struct state *st, uint8_t *block, u8 *s)
{
    struct ft6_row *rows = ft6_rows(block);
    const char *self = st->sups[st->cur].name;
    uint32_t n = 0;
    void *holder = s + S_HOLDER;
    u8 *ent = s + S_ENTRY;
    dir_ctor(holder, DRIVE_SD);
    if (!dir_open(holder, ft6_text(block, T_DIR))) { dir_dtor(holder); return 0; }
    for (;;) {
        zero(ent, DIR_ENTRY);
        if (!dir_next(holder, ent)) break;
        if (ent[0x218] & (ATTR_DIR | ATTR_HIDDEN)) continue;
        char name[NAME_MAX];
        u32 i = 0, ok = 1;
        for (;; i++) {
            u16 c = (u16)(ent[2 * i] | (ent[2 * i + 1] << 8));
            if (!c) break;
            if (i == NAME_MAX - 1 || c > 0x7E || c < 0x20 || c == '\\') { ok = 0; break; }
            name[i] = (char)c;
        }
        name[i < NAME_MAX ? i : NAME_MAX - 1] = 0;
        if (!ok || i < 5 || i - 4 >= sizeof rows[0].base || name[0] == '.' || !ncmp(name, self)) continue;
        uint32_t bin = !ncmp(name + i - 4, ft6_text(block, T_BIN));
        if (!bin && ncmp(name + i - 4, ft6_text(block, T_OFF_EXT))) continue;
        if (n == FT6_ROWS) continue;
        struct ft6_row *r = &rows[n++];
        zero(r, sizeof *r);
        copy(r->base, name, i - 4);
        r->bin = bin;
        r->legacy = !bin;
        r->boot = bin ? BOOT_FAILED : BOOT_OFF;
        if (bin)
            for (u32 j = 0; j < st->nsups; j++)
                if (j != st->cur && st->sups[j].state == SL_KEEP && !ncmp(st->sups[j].name, name)) {
                    r->boot = BOOT_ON;
                    r->sup = j + 1u;
                }
    }
    dir_dtor(holder);
    for (u32 i = 1; i < n; i++)                     /* insertion sort, as list_dir */
        for (u32 j = i; j && ncmp(rows[j - 1].base, rows[j].base) > 0; j--) {
            struct ft6_row tmp;
            copy(&tmp, &rows[j], sizeof tmp);
            copy(&rows[j], &rows[j - 1], sizeof tmp);
            copy(&rows[j - 1], &tmp, sizeof tmp);
        }
    /* the names: each file's sup_id, from its header (none open while the
     * directory was listed) */
    for (u32 i = 0; i < n; i++) {
        char path[64];
        struct sl_header *h = (struct sl_header *)(s + S_HDR);
        ft6_path(block, path, &rows[i], rows[i].bin);
        zero(h, sizeof *h);
        u32 got = read_file(s, path, h, sizeof *h);
        rows[i].id = got == sizeof *h && h->magic == SL_MAGIC ? h->sup_id : 0u;
        ft6_name(block, &rows[i], rows[i].id);
        if (rows[i].bin && rows[i].id && st->dis)       /* on the list: off this boot and the next */
            for (u32 j = 0; j < st->dis[1]; j++)
                if (st->dis[2 + j] == rows[i].id) { rows[i].boot = BOOT_OFF; rows[i].bin = 0; }
    }
    for (u32 i = n; i < FT6_ROWS; i++) zero(&rows[i], sizeof rows[i]);    /* past the list: blank */
    for (u32 i = 0; i < FT6_ROWS; i++) ft6_foot(block, st, &rows[i]);
    return n;
}

/* FT_Key (int 0), FT_N<k> (the cells' texts), FT_Focus (int 0, the grid's
 * cursor): memory only */
static uint32_t ft6_variables(uint8_t *block, uintptr_t app)
{
    struct ft6_tab *t = (struct ft6_tab *)block;
    struct ft6_row *rows = ft6_rows(block);
    for (uint32_t i = 0; i < t->n_vars; i++) {
        uint32_t def[3];
        def[0] = rd32((uintptr_t)(block + t->vars_off + 8u * i));
        def[1] = (uint32_t)(uintptr_t)ft6_var(block, i);
        def[2] = !def[0] ? 0u                                      /* an int: FT_Key, FT_Focus */
               : i == VAR_OK ? (uint32_t)(uintptr_t)ft6_footer(block, 1u)
               : (uint32_t)(uintptr_t)ft6_part(block, &rows[(i - 2u) % FT6_ROWS], ft6_part_of((i - 2u) / FT6_ROWS));
        if (FW(VAR_REGISTER, fn3)((uint32_t)app, 1, (uint32_t)(uintptr_t)def)) return 90u + i;
    }
    uint32_t sub = FW(VAR_SUBSCRIBE, fn3)(GUI_OBJECT_AT, (uint32_t)(uintptr_t)ft6_var(block, 0),
                                          (uint32_t)(uintptr_t)&allsups_key);
    FW(VAR_SUBSCRIBE, fn3)(GUI_OBJECT_AT, (uint32_t)(uintptr_t)ft6_var(block, t->n_vars - 1u),
                           (uint32_t)(uintptr_t)&allsups_focus);
    /* DEBUG (camera r3e3): what boot did, readable with `gui geti FT_Key`:
     * 3000 + the list found x 100 + the subscription's answer (low byte) */
    struct state *st = (struct state *)(uintptr_t)t->st;
    FW(POST_INT, fn3)(0, (uint32_t)(uintptr_t)ft6_var(block, 0),
                      3000u + (st && st->dis ? 100u : 0u) + (sub & 0xFFu));
    return 0;
}

static uint32_t ft6_register(uint8_t *block, struct ft6_tab *t)
{
    uintptr_t app = rd32(GUI_OBJECT_AT), screens, screen, reader, rdr = 0, data, last;
    uint32_t n0, n;
    t->app = app;
    if (bad(app)) return 3;
    if (!rd32(app + APP_SCREENS_N)) return 4;
    screens = rd32(app + APP_SCREENS);
    if (bad(screens)) return 4;
    screen = rd32(screens);
    if (bad(screen)) return 4;
    reader = rd32(screen + SCREEN_READER);
    if (bad(reader) || rd32(reader + READER_BASE) != NBU_BASE_AT || rd32(reader + READER_APP) != app)
        return 4;
    if (rd32(app + APP_SCREENS_N) + 1u >= rd32(app + APP_SCREENS_CAP)) return 5;   /* two pages */
    n0 = rd32(app + APP_MACH_N);
    if (n0 >= rd32(app + APP_MACH_CAP)) return 5;
    if (FW(NBR_LOAD, fn4)((uint32_t)app, (uint32_t)(uintptr_t)(block + t->pack_off), t->pack_len,
                          (uint32_t)(uintptr_t)&rdr))
        return 6;
    t->rd = rdr;
    if (bad(rdr)) return 7;
    wr32(rdr + READER_POOL_LEN, rd32(reader + READER_POOL_LEN));
    wr32(rdr + READER_POOL, rd32(reader + READER_POOL));
    n = rd32(app + APP_MACH_N);
    if (n != n0 + 1u) return 8;
    data = rd32(app + APP_MACH);
    last = rd32(data + 4u * (n - 1u));
    t->mach = last;
    if (bad(last) || !ft6_same(rd32(last + MACH_NAME), t->machine)) return 9;
    for (uint32_t i = n - 1u; i; --i) wr32(data + 4u * i, rd32(data + 4u * (i - 1u)));
    wr32(data, last);
    return 0;
}

/* no table: a const array would be .rodata, which LOADER.BIN does not have */
static uint32_t ft6_site(uint32_t i)
{
    return i == 0 ? GUI_SITE : i == 1 ? 0xC05E5B58u : i == 2 ? 0xC05E61C8u : 0xC05E61E0u;
}

static void ft6_report(const struct sl_svc *svc, struct ft6_tab *t, uint32_t s)
{
    char line[12];
    t->status = s;
    line[0] = 'F'; line[1] = 'T'; line[2] = '6'; line[3] = ' '; line[4] = 's'; line[5] = '=';
    line[8] = 0;
    line[6] = (char)('0' + (s / 10u) % 10u);
    line[7] = (char)('0' + s % 10u);
    svc->log(svc, line);
}

/* Every STRBASE's address -> &soff[first + its string#]; then the block's hash. */
static void ft6_relocate(uint8_t *block, struct ft6_tab *t, uintptr_t b, uint32_t first)
{
    uintptr_t p = b + 0x20u;
    uint32_t ns = rd32(b + 16), nw = rd32(b + 20);
    for (uint32_t i = 0; i < ns; i++) {
        uint32_t len = rd32(p + 4);
        p += 8u + ((len + 1u + 3u) & ~3u);
    }
    for (uint32_t i = 0; i < nw;) {
        uint32_t w = rd32(p + 4u * i), n = w & 0xFFFFFFu;
        if ((w >> 24) == 16u && n == 2u)
            wr32(p + 4u * (i + 2u),
                 (uint32_t)(uintptr_t)(block + t->soff_off) + 4u * (first + rd32(p + 4u * (i + 1u))));
        i += 1u + n;
    }
    wr32(b + 12, ft6_fnv(b + 0x20, rd32(b + 8) - 0x20));
}

static int allsups_entry(uint8_t *block, const struct sl_svc *svc)
{
    struct state *st = (struct state *)svc;
    struct ft6_tab *t = (struct ft6_tab *)block;            /* the data file starts with it */
    struct uia_outcome ui;
    uint32_t s, r;
    /* 1 */
    for (uint32_t i = 0; i < 4; ++i)
        if (svc->claim(svc, ft6_site(i), 4, &t->stock[i], i ? SL_SHARED_UI : SL_CHAIN) != SL_OK) {
            ft6_report(svc, t, 1);
            return SL_RELEASE;
        }
    if (!(t->ven = svc->cave_alloc(svc, 8))) {
        ft6_report(svc, t, 2);
        return SL_RELEASE;
    }
    u8 *scratch = mem_get(st->heap, S_CANDS);           /* file object, dir entry, path, header */
    t->fobj = (uint32_t)(uintptr_t)mem_get(st->heap, FILE_OBJECT_BYTES);
    if (!scratch || !t->fobj) {
        if (scratch) mem_free(st->heap, scratch);
        ft6_report(svc, t, 2);
        return SL_RELEASE;
    }
    /* 2: the blocks, one layer each; a block's offsets are in its order and
     * its STRBASE gave the first */
    uintptr_t soff = (uintptr_t)(block + t->soff_off);
    for (uint32_t i = 0; i < t->n_str; i++) wr32(soff + 4u * i, 0);
    for (uint32_t j = 0; j < t->n_ui; j++) {
        uintptr_t e = (uintptr_t)(block + t->ui_off) + 12u * j;
        uint32_t first = rd32(e + 8), end = j + 1u < t->n_ui ? rd32(e + 20) : t->n_str;
        ft6_relocate(block, t, (uintptr_t)(block + rd32(e)), first);
        r = uia_apply((uintptr_t)(block + rd32(e)), rd32(e + 4), &ui);
        t->ui_result = r;
        t->ui_op = ui.op;
        if (r != UIA_OK || !rd32(soff + 4u * first)) {
            mem_free(st->heap, scratch);
            ft6_report(svc, t, r != UIA_OK ? 10u + (r < 79u ? r : 79u) : 89u);
            return j || r == UIA_OK ? SL_KEEP : SL_RELEASE;   /* a layer up: keep it */
        }
        for (uint32_t i = first + 1u; i < end; i++) wr32(soff + 4u * i, rd32(soff + 4u * first) + (i - first));
    }
    /* 3 */
    for (uint32_t i = 0; i < t->n_refs; i++) {
        uintptr_t ref = (uintptr_t)(block + t->refs_off) + 8u * i;
        wr32((uintptr_t)(block + t->pack_off + rd32(ref)), ft6_be(rd32(soff + 4u * rd32(ref + 4))));
    }
    t->st = (uint32_t)(uintptr_t)st;
    t->path = (uint32_t)(uintptr_t)st->sups[st->cur].path;       /* "\fpSup\LOADER.BIN" */
    t->n_rows = ft6_list(st, block, scratch);
    mem_free(st->heap, scratch);
    wr32((uintptr_t)&ft6_ctx, (uint32_t)(uintptr_t)block);
    publish();
    s = ft6_variables(block, rd32(GUI_OBJECT_AT));
    if (!s) s = ft6_register(block, t);
    if (s && s != 8u && s != 9u) {
        ft6_report(svc, t, s);           /* no page: the string layer stays, unused */
        return SL_KEEP;
    }
    /* 5 */
    {
        uint32_t next = svc->chain_next(svc, GUI_SITE, 0);
        wr32((uintptr_t)&ft6_next, next ? next : GUI_NEXT0);
        wr32(t->ven, 0xE51FF004u);                       /* ldr pc, [pc, #-4] */
        wr32(t->ven + 4u, (uint32_t)(uintptr_t)ft6_layer);
        svc->publish(svc);
        wr32(GUI_SITE, 0xEB000000u | (((t->ven - GUI_SITE - 8u) >> 2) & 0x00FFFFFFu));
        svc->publish(svc);
    }
    ft6_report(svc, t, s);
    return SL_KEEP;
}
