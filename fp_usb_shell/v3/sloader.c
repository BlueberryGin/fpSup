/* Loader v3, the 加載器 (LOADER.BIN).  Design: LOADER_V3.md.
 *
 * Entered once from the loader (loader.S, unchanged but for its path), from
 * the staging buffer the loader frees on its way out:
 *
 *     sl_boot(vbin, unhook, base, strings)
 *
 * It copies all of itself into a block of its own first -- the power-off
 * write-back and the services a sup is handed must outlive the staging buffer
 * -- and carries on there.  Then it lists \fpSup, sorts the names, and loads
 * each *.BIN but itself: one block per file, read whole, entered once.
 *
 * Built like uishare: clang -fropi, one .text, no relocations, no data
 * sections, no globals.  Everything mutable is in `struct state`, at the end
 * of the resident block, reached through the service table a sup is handed
 * (the table is the first member).  No string literals either: the strings
 * this needs come from the ARM stub.
 *
 * Firmware addresses are Ver.5.02.  The directory API was read out of the
 * shell's `dir` and called on the camera (research/firmware/notes/
 * DIR_ENUM_API.md); the file API and allocator are the ones loader.S and
 * stage2.S already use. */
#include "sloader.h"

typedef uint32_t u32;
typedef uint16_t u16;
typedef uint8_t u8;

#ifndef CAVE_BUMP
#error "CAVE_BUMP, CAVE_ARENA, CAVE_ARENA_END and LOAD_DONE_US come from build_v3.py"
#endif

/* --- firmware ------------------------------------------------------------ */
#define FW(addr, type) ((type)(uintptr_t)(addr))
#define tick_us()        FW(0xC002B6E0, u32 (*)(void))()
#define dcache()         FW(0xC000E91C, void (*)(void))()
#define icache()         FW(0xC000EABC, void (*)(void))()
#define mem_heap(c)      FW(0xC001CF78, void *(*)(u32))(c)
#define mem_get(h, n)    FW(0xC001D038, void *(*)(void *, u32, u32, u32, u32))(h, n, 0, 0, 0)
#define mem_free(h, p)   FW(0xC001D2B8, void (*)(void *, void *))(h, p)
#define poff_mgr()       FW(0xC0023A98, void *(*)(void))()
#define poff_add(m, o, f) FW(0xC0024118, u32 (*)(void *, void *, u32))(m, o, f)
#define f_ctor(o, v)     FW(0xC0365E90, void (*)(void *, u32))(o, v)
#define f_open(o, p)     FW(0xC0365FB0, u32 (*)(void *, const char *, u32))(o, p, 1)
#define f_create(o, p)   FW(0xC0365FB0, u32 (*)(void *, const char *, u32))(o, p, 7)
#define f_write(o, b, n) FW(0xC03660E8, u32 (*)(void *, const void *, u32))(o, b, n)
#define f_read(o, b, n, a) FW(0xC0366060, u32 (*)(void *, void *, u32, u32 *))(o, b, n, a)
#define f_close(o)       FW(0xC0366020, void (*)(void *))(o)
#define f_dtor(o)        FW(0xC0365ED0, void (*)(void *, u32))(o, 2)
#define dir_ctor(h, d)   FW(0xC0366918, void *(*)(void *, u32))(h, d)
#define dir_open(h, p)   FW(0xC0366990, u32 (*)(void *, const char *))(h, p)
#define dir_next(h, e)   FW(0xC03669F8, u32 (*)(void *, void *))(h, e)
#define dir_dtor(h)      FW(0xC0366940, void (*)(void *, u32))(h, 2)

#define DRIVE_SD     1u        /* the card; 5 would be an SSD                  */
#define DIR_ENTRY    0x238u    /* name UTF-16 +0, size u64 +0x210, attr +0x218 */
#define ATTR_HIDDEN  0x02u
#define ATTR_DIR     0x10u

/* --- limits -------------------------------------------------------------- */
#define MAX_SUPS     32u
#define MAX_CLAIMS   768u
#define MAX_LOG      96u
#define NAME_MAX     40u       /* the longest name a sup file may have         */
#define JOURNAL_BYTES 0x8000u  /* stock bytes + 8 per range; res-lab is ~20 KB  */
#define MAX_BLOCK    0x01000000u

/* Scratch, freed before the first sup runs.  The file object is "about 3 KiB"
 * (loader.S); 4 KiB is what loader.S gives it. */
#define S_FOBJ       0x0000u
#define S_HOLDER     0x1000u
#define S_ENTRY      0x1010u
#define S_PATH       0x1300u
#define S_HDR        0x1380u
#define S_CANDS      0x1400u
#define SCRATCH_BYTES (S_CANDS + MAX_SUPS * sizeof(struct cand))

/* Log record codes.  The host decodes them; there are no strings to spare. */
enum {
    L_START = 1, L_NO_DIR, L_SKIP_NAME, L_TOO_MANY, L_OPEN_FAIL, L_BAD_HEADER,
    L_DUP_ID, L_NO_MEMORY, L_SHORT_READ, L_LOADED, L_RELEASED, L_REPAIRED,
    L_SUP, L_DONE, L_NO_POFF, L_CONFLICT, L_LISTED, L_CLAIM,
    L_DISABLED,           /* SL_ALLSUPS: its sup_id is on LOADER.BIN's list */
};

/* \fpSup\LOAD.LOG: one line per record, "cc aaaaaaaa bbbbbbbb text", padded to
 * a fixed size -- open mode 7 overwrites without truncating, so the file is
 * always exactly this long and an old tail can never survive. */
#define LOG_FILE_BYTES 4096u
#define ALLSUPS_MAGIC 0x54365446u      /* "FT6T": allsups.c's table, its data file's first word */
/* LOADER.BIN's disabled list (SL_ALLSUPS; the user 2026-10-08: no renaming,
 * "紀錄在 loader_v3 的 bin 裡面"): DS_BYTES after the code, {DS_MAGIC, count,
 * sup_id x DS_MAX}.  The code length (VBIN +12) does not count it, so loader.S
 * and everything before this do not see it. */
#define DS_MAGIC      0x53445346u      /* "FSDS" */
#define DS_MAX        30u
#define DS_BYTES      (8u + 4u * DS_MAX)

struct cand { char name[NAME_MAX]; u32 size; };
struct claim { u32 addr, len; u8 kind, sup, res, pad; };
struct sup { char name[NAME_MAX]; u32 id; void *block; u32 state;
             char path[NAME_MAX + 8]; };  /* resident: entry's r2 */
struct logrec { u32 code, a, b; char text[20]; };

struct strings {          /* from sloader_entry.S                              */
    const char *dir;      /* "\fpSup"                                          */
    const char *self;     /* "LOADER.BIN"                                      */
    const char *logname;  /* "LOAD.LOG"                                        */
    u32 splash;           /* the four-box completion, ARM; 0 when not built    */
#ifdef SL_ALLSUPS
    const char *allsups;  /* "\fpSup\UI\ALLSUPS.BIN"                           */
#endif
};

struct state {
    struct sl_svc svc;                 /* FIRST: the table is the state        */
    u32 poff[3];                       /* X-8, routine, journal: see stage2.S  */
    void *heap;
    u32 *jbase, *jcur, *jend;
    u32 nclaims, cur, jmark, cmark;
    struct claim claims[MAX_CLAIMS];
    u32 nsups;
    struct sup sups[MAX_SUPS];
    u32 nlog;
    struct logrec log[MAX_LOG];
    u32 running;                       /* 1 while a sup's entry runs           */
#ifdef SL_ALLSUPS
    /* Appended: the fields above keep their places (test_v3 mirrors them).
     * LOADER.BIN as read (its own bytes and the disabled list after the code,
     * DS_*): the page writes it back whole with a changed list. */
    u8 *file;
    u32 file_len;
    u32 *dis;                          /* into `file`: DS_MAGIC, count, ids    */
    /* svc->button, per sup (the index into sups[]) */
    int (*btn_fn[MAX_SUPS])(void *);
    void *btn_arg[MAX_SUPS];
    const char *btn_label[MAX_SUPS];
#endif
};

/* --- small helpers (no libc: -fno-builtin, and nothing to link) ---------- */
static void zero(void *d, u32 n) { volatile u8 *p = d; while (n--) *p++ = 0; }
static void copy(void *d, const void *s, u32 n)
{
    volatile u8 *a = d; const volatile u8 *b = s;
    while (n--) *a++ = *b++;
}
static int differs(const void *a, const void *b, u32 n)
{
    const volatile u8 *x = a, *y = b;
    while (n--) if (*x++ != *y++) return 1;
    return 0;
}
static u32 slen(const char *s) { u32 n = 0; while (s[n]) n++; return n; }
static char upper(char c) { return (c >= 'a' && c <= 'z') ? (char)(c - 32) : c; }
static int ncmp(const char *a, const char *b)   /* case-insensitive strcmp     */
{
    for (;; a++, b++) {
        char x = upper(*a), y = upper(*b);
        if (x != y) return x < y ? -1 : 1;
        if (!x) return 0;
    }
}
static void publish(void) { dcache(); icache(); }

static void logrec(struct state *st, u32 code, u32 a, u32 b, const char *text)
{
    if (st->nlog >= MAX_LOG) return;
    struct logrec *r = &st->log[st->nlog];
    r->code = code; r->a = a; r->b = b;
    u32 i = 0;
    if (text) for (; i < sizeof r->text - 1 && text[i]; i++) r->text[i] = text[i];
    r->text[i] = 0;
    st->nlog++;
}

/* --- journal ------------------------------------------------------------- *
 * { address, bytes, the stock words }..., terminated by address 0 -- the
 * layout stage2's lh_unhook walks, and sloader_entry.S carries the same
 * routine.  The terminator goes first and the address last, so a power-off in
 * the middle of an append sees the old journal or the new one. */
static int journal(struct state *st, u32 addr, u32 len, const void *stock)
{
    u32 words = len / 4;
    if (st->jcur + 2 + words + 1 > st->jend) return SL_FULL;
    volatile u32 *e = st->jcur;
    copy((void *)(e + 2), stock, len);
    e[2 + words] = 0;
    e[1] = len;
    e[0] = addr;
    st->jcur = (u32 *)(e + 2 + words);
    return SL_OK;
}

/* --- services ------------------------------------------------------------ */
static int overlaps(const struct claim *c, u32 addr, u32 len)
{
    return addr < c->addr + c->len && c->addr < addr + len;
}

static int svc_claim(const struct sl_svc *svc, u32 addr, u32 len, const void *stock, u32 kind)
{
    struct state *st = (struct state *)svc;
    if (!len || ((addr | len) & 3) || kind < SL_CHAIN || kind > SL_SHARED_UI || !stock)
        return SL_BADARG;
    int shared = 0;
    for (u32 i = 0; i < st->nclaims; i++) {
        struct claim *c = &st->claims[i];
        if (c->res || !overlaps(c, addr, len)) continue;
        if (c->sup == st->cur && c->addr == addr && c->len == len && c->kind == kind)
            return SL_OK;                         /* asked twice: same answer */
        if (kind != SL_EXCL && c->kind == kind && c->addr == addr && c->len == len) {
            shared = 1;                           /* another layer, same site */
            continue;
        }
        logrec(st, L_CONFLICT, addr, st->sups[c->sup].id, st->sups[st->cur].name);
        return SL_CONFLICT;
    }
    if (st->nclaims >= MAX_CLAIMS) return SL_FULL;
    if (!shared) {
        /* First claim on this range this boot.  If the memory is not the
         * firmware's own, nobody this boot put it there -- it is a patch a
         * missed power-off left behind.  Put the stock bytes back before
         * anything can branch through it, and journal the stock bytes, not
         * what was in memory (LOADER_V2.md, the ⏸ problem). */
        if (st->jcur + 2 + len / 4 + 1 > st->jend) return SL_FULL;
        if (differs((const void *)(uintptr_t)addr, stock, len)) {
            copy((void *)(uintptr_t)addr, stock, len);
            publish();
            logrec(st, L_REPAIRED, addr, len, st->sups[st->cur].name);
        }
        journal(st, addr, len, stock);
    }
    struct claim *c = &st->claims[st->nclaims++];
    c->addr = addr; c->len = len; c->kind = (u8)kind; c->sup = (u8)st->cur;
    c->res = 0; c->pad = 0;
    return SL_OK;
}

static int svc_claim_res(const struct sl_svc *svc, u32 id, u32 kind)
{
    struct state *st = (struct state *)svc;
    if (kind < SL_CHAIN || kind > SL_SHARED_UI) return SL_BADARG;
    for (u32 i = 0; i < st->nclaims; i++) {
        struct claim *c = &st->claims[i];
        if (!c->res || c->addr != id) continue;
        if (c->sup == st->cur) return SL_OK;
        if (kind != SL_EXCL && c->kind == kind) continue;
        logrec(st, L_CONFLICT, id, st->sups[c->sup].id, st->sups[st->cur].name);
        return SL_CONFLICT;
    }
    if (st->nclaims >= MAX_CLAIMS) return SL_FULL;
    struct claim *c = &st->claims[st->nclaims++];
    c->addr = id; c->len = 0; c->kind = (u8)kind; c->sup = (u8)st->cur;
    c->res = 1; c->pad = 0;
    return SL_OK;
}

static u32 svc_chain_next(const struct sl_svc *svc, u32 site, u32 thumb)
{
    (void)svc;
    if (!thumb) {
        u32 w = *(volatile u32 *)(uintptr_t)site;
        if ((w & 0x0E000000u) != 0x0A000000u) return 0;          /* B / BL / BLX imm */
        int32_t off = (int32_t)(w << 8) >> 6;
        u32 t = site + 8 + (u32)off;
        if ((w >> 28) == 0xF) t |= ((w >> 23) & 2u) | 1u;        /* BLX: to Thumb    */
        return t;
    }
    u32 h1 = *(volatile u16 *)(uintptr_t)site, h2 = *(volatile u16 *)(uintptr_t)(site + 2);
    if ((h1 & 0xF800u) != 0xF000u) return 0;
    u32 op = h2 & 0xD000u;
    if (op != 0x9000u && op != 0xD000u && op != 0xC000u) return 0; /* B.W / BL / BLX */
    u32 s = (h1 >> 10) & 1, j1 = (h2 >> 13) & 1, j2 = (h2 >> 11) & 1;
    u32 i1 = !(j1 ^ s), i2 = !(j2 ^ s);
    u32 imm = (s << 24) | (i1 << 23) | (i2 << 22) | ((h1 & 0x3FFu) << 12) | ((h2 & 0x7FFu) << 1);
    int32_t off = (int32_t)(imm << 7) >> 7;
    u32 t = site + 4 + (u32)off;
    return op == 0xC000u ? (t & ~3u) : (t | 1u);
}

static u32 svc_cave_alloc(const struct sl_svc *svc, u32 bytes)
{
    (void)svc;
    volatile u32 *bump = (volatile u32 *)(uintptr_t)CAVE_BUMP;
    u32 p = (*bump + 7) & ~7u, n = (bytes + 7) & ~7u;
    if (!n || p < CAVE_ARENA || p + n > CAVE_ARENA_END) return 0;
    *bump = p + n;
    return p;
}

static void svc_publish(const struct sl_svc *svc) { (void)svc; publish(); }

/* Who holds `addr`: the i-th sup, in load order, with a claim covering it. */
static const char *svc_holder(const struct sl_svc *svc, u32 addr, u32 i)
{
    struct state *st = (struct state *)svc;
    for (u32 k = 0; k < st->nsups; k++) {
        if (st->sups[k].state != SL_KEEP && !(k == st->cur && st->running)) continue;
        for (u32 j = 0; j < st->nclaims; j++) {
            struct claim *c = &st->claims[j];
            /* a memory claim covering addr, or a resource claim whose id is addr
             * (uishare's UIS_RES_QSCUR: the first holder issues the shared QS
             * image ids -- ui_pool.c uis_qs_shared; camera 2026-10-07) */
            if (c->sup != k || (c->res ? c->addr != addr : !overlaps(c, addr, 4))) continue;
            if (!i--) return st->sups[k].path;
            break;
        }
    }
    return 0;
}

static const char *svc_self(const struct sl_svc *svc)
{
    struct state *st = (struct state *)svc;
    return st->running ? st->sups[st->cur].path : 0;
}

#ifdef SL_ALLSUPS
static int svc_button(const struct sl_svc *svc, int (*fn)(void *), void *arg, const char *label)
{
    struct state *st = (struct state *)svc;
    if (!st->running || !fn) return SL_BADARG;
    st->btn_fn[st->cur] = fn;
    st->btn_arg[st->cur] = arg;
    st->btn_label[st->cur] = label;
    return SL_OK;
}
#endif

static void svc_log(const struct sl_svc *svc, const char *line)
{
    struct state *st = (struct state *)svc;
    logrec(st, L_SUP, st->sups[st->cur].id, 0, line);
}

/* A sup said "not me": every range it was the first to claim goes back to the
 * stock bytes it gave (it should not have written them, but if it did this
 * undoes it), the journal is cut back to where it was, its claims are gone. */
static void undo_current(struct state *st)
{
    for (u32 *e = (u32 *)(uintptr_t)st->jmark; e < st->jcur && e[0]; e += 2 + e[1] / 4)
        copy((void *)(uintptr_t)e[0], e + 2, e[1]);
    st->jcur = (u32 *)(uintptr_t)st->jmark;
    st->jcur[0] = 0;
    st->nclaims = st->cmark;
    publish();
}

/* --- directory ----------------------------------------------------------- */
static int wanted(const char *name, const char *self)
{
    u32 n = slen(name);
    if (n < 5 || name[0] == '.') return 0;          /* ".", "._x" (macOS), dotfiles */
    const char *ext = name + n - 4;
    if (ext[0] != '.' || upper(ext[1]) != 'B' || upper(ext[2]) != 'I' || upper(ext[3]) != 'N')
        return 0;
    return ncmp(name, self) != 0;
}

/* The firmware gives the names in directory order -- the order they were
 * written, which a drag in Finder changes.  The order is the load order and
 * the nesting order of hooks, so it is the names'. */
static u32 list_dir(struct state *st, u8 *s, const struct strings *str)
{
    struct cand *cand = (struct cand *)(s + S_CANDS);
    u32 n = 0;
    void *holder = s + S_HOLDER;
    u8 *ent = s + S_ENTRY;
    dir_ctor(holder, DRIVE_SD);
    if (!dir_open(holder, str->dir)) {
        logrec(st, L_NO_DIR, 0, 0, str->dir);
        dir_dtor(holder);
        return 0;
    }
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
        if (!ok || !wanted(name, str->self)) {
            if (ok && name[0] != '.') logrec(st, L_SKIP_NAME, 0, 0, name);
            continue;
        }
        u32 size_hi = *(volatile u32 *)(ent + 0x214);
        if (n == MAX_SUPS) { logrec(st, L_TOO_MANY, n, 0, name); continue; }
        copy(cand[n].name, name, i + 1);
        cand[n].size = size_hi ? 0xFFFFFFFFu : *(volatile u32 *)(ent + 0x210);
        n++;
    }
    dir_dtor(holder);
    for (u32 i = 1; i < n; i++)                     /* insertion sort, n <= 32 */
        for (u32 j = i; j && ncmp(cand[j - 1].name, cand[j].name) > 0; j--) {
            struct cand t;
            copy(&t, &cand[j], sizeof t);
            copy(&cand[j], &cand[j - 1], sizeof t);
            copy(&cand[j - 1], &t, sizeof t);
        }
    return n;
}

static char *make_path(u8 *s, const struct strings *str, const char *name)
{
    char *p = (char *)(s + S_PATH);
    u32 d = slen(str->dir);
    copy(p, str->dir, d);
    p[d] = '\\';
    copy(p + d + 1, name, slen(name) + 1);
    return p;
}

/* Read up to `want` bytes of the file into `buf`.  Returns what was read, or
 * 0xFFFFFFFF when the file would not open. */
static u32 read_file(u8 *s, const char *path, void *buf, u32 want)
{
    void *fo = s + S_FOBJ;
    u32 got = 0;
    f_ctor(fo, DRIVE_SD);
    if (!f_open(fo, path)) { f_dtor(fo); return 0xFFFFFFFFu; }
    f_read(fo, buf, want, &got);
    f_close(fo);
    f_dtor(fo);
    return got;
}

typedef int (*sup_entry)(void *block, const struct sl_svc *svc, const char *path);

static void load_one(struct state *st, u8 *s, const struct strings *str, const struct cand *c)
{
    char *path = make_path(s, str, c->name);
    struct sl_header *h = (struct sl_header *)(s + S_HDR);
    zero(h, sizeof *h);
    u32 got = read_file(s, path, h, sizeof *h);
    if (got == 0xFFFFFFFFu) { logrec(st, L_OPEN_FAIL, 0, 0, c->name); return; }
    u32 ent = h->entry_off & ~1u;
    if (got != sizeof *h || h->magic != SL_MAGIC || h->header_len != SL_HEADER_LEN
        || h->file_len != c->size || h->file_len < SL_HEADER_LEN
        || h->block_size < h->file_len || h->block_size > MAX_BLOCK
        || ent < SL_HEADER_LEN || ent >= h->file_len
        || (!(h->entry_off & 1) && (ent & 3)) || h->min_svc > SL_SVC_VERSION) {
        logrec(st, L_BAD_HEADER, h->magic, got, c->name);
        return;
    }
#ifdef SL_ALLSUPS
    if (st->dis)
        for (u32 i = 0; i < st->dis[1] && i < DS_MAX; i++)
            if (st->dis[2 + i] == h->sup_id) {
                logrec(st, L_DISABLED, h->sup_id, 0, c->name);
                return;
            }
#endif
    for (u32 i = 0; i < st->nsups; i++)
        if (st->sups[i].id == h->sup_id && st->sups[i].state == SL_KEEP) {
            logrec(st, L_DUP_ID, h->sup_id, 0, c->name);
            return;
        }
    if (st->nsups >= MAX_SUPS) return;
    u32 size = h->block_size, len = h->file_len, entry_off = h->entry_off, id = h->sup_id;
    u8 *block = mem_get(st->heap, size);
    if (!block) { logrec(st, L_NO_MEMORY, size, 0, c->name); return; }
    got = read_file(s, path, block, len);
    if (got != len || ((struct sl_header *)block)->magic != SL_MAGIC) {
        logrec(st, L_SHORT_READ, len, got, c->name);
        mem_free(st->heap, block);
        return;
    }
    zero(block + len, size - len);
    publish();

    struct sup *sp = &st->sups[st->nsups];
    copy(sp->name, c->name, slen(c->name) + 1);
    copy(sp->path, path, slen(path) + 1);    /* "\\fpSup\\" + a name < NAME_MAX */
    sp->id = id; sp->block = block; sp->state = SL_RELEASE;
    st->cur = st->nsups++;
    st->jmark = (u32)(uintptr_t)st->jcur;
    st->cmark = st->nclaims;
    st->running = 1;
    int r = ((sup_entry)(uintptr_t)((u32)(uintptr_t)block + entry_off))(block, &st->svc, sp->path);
    st->running = 0;
    if (r == SL_KEEP) {
        sp->state = SL_KEEP;
        logrec(st, L_LOADED, (u32)(uintptr_t)block, size, c->name);
        /* What each range holds now that the sup has written it: the first
         * word, so a patch that did not take shows in the log. */
        for (u32 i = st->cmark; i < st->nclaims; i++)
            if (!st->claims[i].res)
                logrec(st, L_CLAIM, st->claims[i].addr,
                       *(volatile u32 *)(uintptr_t)st->claims[i].addr, 0);
    } else {
        undo_current(st);
        mem_free(st->heap, block);
        sp->block = 0;
        logrec(st, L_RELEASED, (u32)r, 0, c->name);
    }
    publish();
}

#ifdef SL_ALLSUPS
/* The built-in page (allsups.c), entered last as the sup "LOADER.BIN".  Its
 * data file starts with a table whose words 1..2 say how long the file is
 * (pack offset + pack length).  The entering below is load_one's, repeated:
 * load_one stays byte for byte what the v3 releases carry (LOADER.BIN
 * 41f3218a..., sigmafp-re-45 2026-10-07) until the page ships; then the two
 * become one function. */
static int allsups_entry(u8 *data, const struct sl_svc *svc);

static void load_builtin(struct state *st, u8 *s, const struct strings *str)
{
    u32 *t = (u32 *)(s + S_HDR), got, len;
    const char *name = str->self, *file = str->allsups + slen(str->dir) + 1;
    if (st->nsups >= MAX_SUPS) return;
    zero(t, 16);
    got = read_file(s, str->allsups, t, 16);
    if (got == 0xFFFFFFFFu) { logrec(st, L_OPEN_FAIL, 0, 0, file); return; }
    len = t[1] + t[2];
    if (got != 16 || t[0] != ALLSUPS_MAGIC || t[1] < 16 || len < t[1] || len > MAX_BLOCK) {
        logrec(st, L_BAD_HEADER, t[0], got, file);
        return;
    }
    u8 *block = mem_get(st->heap, len);
    if (!block) { logrec(st, L_NO_MEMORY, len, 0, name); return; }
    if (read_file(s, str->allsups, block, len) != len || *(u32 *)block != ALLSUPS_MAGIC) {
        logrec(st, L_SHORT_READ, len, 0, name);
        mem_free(st->heap, block);
        return;
    }
    publish();

    char *path = make_path(s, str, name);
    struct sup *sp = &st->sups[st->nsups];
    copy(sp->name, name, slen(name) + 1);
    copy(sp->path, path, slen(path) + 1);
    sp->id = ALLSUPS_MAGIC; sp->block = block; sp->state = SL_RELEASE;
    st->cur = st->nsups++;
    st->jmark = (u32)(uintptr_t)st->jcur;
    st->cmark = st->nclaims;
    st->running = 1;
    int r = allsups_entry(block, &st->svc);
    st->running = 0;
    if (r == SL_KEEP) {
        sp->state = SL_KEEP;
        logrec(st, L_LOADED, (u32)(uintptr_t)block, len, name);
        for (u32 i = st->cmark; i < st->nclaims; i++)
            if (!st->claims[i].res)
                logrec(st, L_CLAIM, st->claims[i].addr,
                       *(volatile u32 *)(uintptr_t)st->claims[i].addr, 0);
    } else {
        undo_current(st);
        mem_free(st->heap, block);
        sp->block = 0;
        logrec(st, L_RELEASED, (u32)r, 0, name);
    }
    publish();
}
#endif

/* --- the log file --------------------------------------------------------- */
static char *hex(char *p, u32 v, u32 digits)
{
    while (digits--) {
        u32 d = (v >> (4 * digits)) & 15;
        *p++ = (char)(d < 10 ? '0' + d : 'A' + d - 10);
    }
    return p;
}

static void write_log(struct state *st, const struct strings *str)
{
    char *buf = mem_get(st->heap, LOG_FILE_BYTES);
    if (!buf) return;
    char *p = buf, *end = buf + LOG_FILE_BYTES - 64;
    for (u32 i = 0; i < st->nlog && p < end; i++) {
        struct logrec *r = &st->log[i];
        p = hex(p, r->code, 2); *p++ = ' ';
        p = hex(p, r->a, 8); *p++ = ' ';
        p = hex(p, r->b, 8); *p++ = ' ';
        for (u32 j = 0; j < sizeof r->text && r->text[j]; j++) *p++ = r->text[j];
        *p++ = '\n';
    }
    while (p < buf + LOG_FILE_BYTES - 1) *p++ = ' ';
    *p = '\n';
    u8 *obj = mem_get(st->heap, 0x1000);
    if (obj) {
        char *path = (char *)(obj + 0xF00);
        u32 d = slen(str->dir);
        copy(path, str->dir, d);
        path[d] = '\\';
        copy(path + d + 1, str->logname, slen(str->logname) + 1);
        f_ctor(obj, DRIVE_SD);
        if (f_create(obj, path)) {
            f_write(obj, buf, LOG_FILE_BYTES);
            f_close(obj);
        }
        f_dtor(obj);
        mem_free(st->heap, obj);
    }
    mem_free(st->heap, buf);
}

/* --- the resident half --------------------------------------------------- */
static u32 sl_load(struct state *st, const struct strings *str)
{
    *(volatile u32 *)(uintptr_t)CAVE_BUMP = CAVE_ARENA;   /* every boot: stage2's rule */
    st->svc.version = SL_SVC_VERSION;
    st->svc.boot_id = tick_us();
    st->svc.claim = svc_claim;
    st->svc.claim_res = svc_claim_res;
    st->svc.chain_next = svc_chain_next;
    st->svc.cave_alloc = svc_cave_alloc;
    st->svc.publish = svc_publish;
    st->svc.log = svc_log;
    st->svc.holder = svc_holder;
    st->svc.self = svc_self;
    st->svc.done_at_start = *(volatile u32 *)(uintptr_t)LOAD_DONE_US;
#ifdef SL_ALLSUPS
    st->svc.button = svc_button;
#endif
    logrec(st, L_START, st->svc.boot_id, 0, 0);

    /* The journal and both power-off registrations before anything can be
     * claimed: a patch must never exist without its way back. */
    st->jbase = mem_get(st->heap, JOURNAL_BYTES);
    if (!st->jbase) { logrec(st, L_NO_MEMORY, JOURNAL_BYTES, 0, 0); return 0; }
    st->jbase[0] = 0;
    st->jcur = st->jbase;
    st->jend = st->jbase + JOURNAL_BYTES / 4;
    st->poff[0] = (u32)(uintptr_t)st->poff - 8;
    st->poff[2] = (u32)(uintptr_t)st->jbase;
    publish();
    void *m = poff_mgr();
    if (!m || !poff_add(m, st->poff, 0)) { logrec(st, L_NO_POFF, 0, 0, 0); return 0; }
    m = poff_mgr();
    if (!m || !poff_add(m, st->poff, 1)) { logrec(st, L_NO_POFF, 1, 0, 0); return 0; }

    u8 *s = mem_get(st->heap, SCRATCH_BYTES);
    if (!s) { logrec(st, L_NO_MEMORY, SCRATCH_BYTES, 0, 0); return 0; }
    zero(s, SCRATCH_BYTES);
    u32 n = list_dir(st, s, str);
    logrec(st, L_LISTED, n, 0, 0);
    /* The table where code that is not handed it can find it (SL_SVC_AT),
     * for exactly as long as entries run. */
    *(volatile u32 *)(uintptr_t)SL_SVC_AT = (u32)(uintptr_t)&st->svc;
    publish();
    for (u32 i = 0; i < n; i++)
        load_one(st, s, str, &((struct cand *)(s + S_CANDS))[i]);
#ifdef SL_ALLSUPS
    load_builtin(st, s, str);           /* last: it lists what the others did */
#endif
    *(volatile u32 *)(uintptr_t)SL_SVC_AT = 0;
    mem_free(st->heap, s);

    u32 kept = 0;
    for (u32 i = 0; i < st->nsups; i++) kept += st->sups[i].state == SL_KEEP;
    publish();
    *(volatile u32 *)(uintptr_t)LOAD_DONE_US = tick_us();
    logrec(st, L_DONE, kept, st->nsups, 0);
    return kept;
}

static u32 sl_run(struct state *st, const struct strings *str)
{
    u32 kept = sl_load(st, str);
    if (!st->svc.boot_id) return kept;              /* never got started */
    write_log(st, str);
    /* The AutoRun paused the redraw and drew frames 0-3; the last frame, the
     * hold and the restore are this side's, as they were stage2's. */
    if (str->splash) FW(str->splash, void (*)(void))();
    return kept;
}

typedef u32 (*run_fn)(struct state *, const struct strings *);

/* In the staging buffer.  `base` is the first byte of the code (the stub),
 * `vbin` the file the loader read, whose +0x0C says how long the code is. */
u32 sl_boot(const u8 *vbin, u32 unhook, u32 base, const struct strings *str)
{
    u32 code = *(const u32 *)(vbin + 12);
    u32 state_at = (code + 7) & ~7u;
    void *heap = mem_heap(0);                       /* class 0, USER */
    if (!heap) return 0;
    u8 *res = mem_get(heap, state_at + sizeof(struct state));
    if (!res) return 0;
    copy(res, (const void *)(uintptr_t)base, code);
    struct state *st = (struct state *)(res + state_at);
    zero(st, sizeof *st);
    st->heap = heap;
    st->poff[1] = (u32)(uintptr_t)res + (unhook - base);
    /* The strings are in the stub; point at the resident copy of them. */
    struct strings *rs = (struct strings *)(res + ((u32)(uintptr_t)str - base));
    rs->dir = (const char *)(res + ((u32)(uintptr_t)str->dir - base));
    rs->self = (const char *)(res + ((u32)(uintptr_t)str->self - base));
    rs->logname = (const char *)(res + ((u32)(uintptr_t)str->logname - base));
    if (str->splash) rs->splash = (u32)(uintptr_t)res + (str->splash - base);
#ifdef SL_ALLSUPS
    rs->allsups = (const char *)(res + ((u32)(uintptr_t)str->allsups - base));
    /* LOADER.BIN as read, before anything ran from it, and its disabled list */
    st->file_len = 16u + code + DS_BYTES;
    st->file = mem_get(heap, st->file_len);
    if (st->file) {
        copy(st->file, vbin, st->file_len);
        /* the stub wrote its string pointers into its own `strs` before calling
         * here; the file has zeros there (sloader_entry.S: five words) */
        zero(st->file + ((u32)(uintptr_t)str - (u32)(uintptr_t)vbin), 5u * 4u);
        u32 *d = (u32 *)(st->file + 16u + code);
        if (d[0] == DS_MAGIC && d[1] <= DS_MAX) st->dis = d;
    }
#endif
    publish();
    run_fn run = (run_fn)(uintptr_t)((u32)(uintptr_t)res + ((u32)(uintptr_t)sl_run - base));
    return run(st, rs);
}
