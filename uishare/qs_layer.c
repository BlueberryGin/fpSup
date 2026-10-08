#include "qs_layer.h"

/* See qs_layer.h and QS_SHARE.md. ARM32 only: test_qs_layer.py runs it in
 * unicorn over the firmware image. */

#if !(defined(__arm__) && UINTPTR_MAX == UINT32_MAX)
#error "ARM32 only"
#endif
#define peek(a)      (*(volatile const uint32_t *)(a))
#define poke(a, v)   (*(volatile uint32_t *)(a) = (v))
#define peek8(a)     (*(volatile const uint8_t *)(a))
#define poke8(a, v)  (*(volatile uint8_t *)(a) = (uint8_t)(v))

static uint32_t rec_be32(uintptr_t a) {
    return (uint32_t)peek8(a) << 24 | peek8(a + 1) << 16 | peek8(a + 2) << 8 | peek8(a + 3);
}

/* the reader (og3k_ui.S ui_record): +4 position, +8 resource context, +36 base */
#define R_POS  4u
#define R_CTX  8u
#define R_BASE 36u

/* sloader.h struct sl_svc */
#define SVC_CLAIM   8u
#define SVC_CLAIM_RES 12u
#define SVC_LOG     28u
#define SVC_CAVE    20u
#define SVC_PUBLISH 24u
#define SVC_HOLDER  32u
#define SVC_SELF    36u
#define MAX_HOLDERS 32u
#define THUMB_VENEER 0xF000F8DFu

/* the shared file-redirect table (fv_handler.S): the site, its block, the CSV */
#define FV_SITE      0xC05E5BECu
#define FV_STOCK     0x60681840u
#define FV_MAGIC     0x4B485346u        /* "FSHK" */
#define FV_ENTRY_OFF 16u
#define RES_CSV      0xC0F8E7ECu        /* B2_5_4: the movie resolution list */

/* The current resolution value: the setting in the menu-setting storage's
 * working bank (ui_strings.S has the same three addresses and why), not the
 * property object's +0x14 cache, which lags one change (camera 2026-10-07). */
#define RES_BANK_SEL 0xC31B954Cu
#define RES_RAW_A    0xC31B3A4Cu
#define RES_RAW_B    0xC31B9314u
static uint32_t res_value(void) { return peek(peek8(RES_BANK_SEL) ? RES_RAW_B : RES_RAW_A); }

/* og3k_ui.S ui_layout: UicResourceCache and friends */
#define RCACHE       0xC37B7254u        /* singleton: +4 held layout, +0x28 busy */
/* UicResourceCache::v3 (ARM, vtable 0xC0D0EF88 +0x14), r0 = the CACHE: unloads
 * [cache+4] (C05D93A0 -> C05D91A0 -> ctx loader +0x10 = C05E83F0: tree
 * refcount -1; at 1 the tree is freed and [layout+0xC] = 0), then clears the
 * cache's name and +4. The stock calls it the same way (C0565090, event
 * 0x2602). A tree in r0 is taken for a cache: it writes into the tree. */
#define RCACHE_V3    0xC0566498u
#define RCACHE_NAMES 0xC37B7288u        /* its three QS layout names */
#define LAYOUT_FIND  0xC05E04E9u        /* (ctx, name) -> layout or 0 (Thumb) */

typedef const char *(*holder_fn)(uintptr_t, uint32_t, uint32_t);
typedef const char *(*self_fn)(uintptr_t);
typedef uint32_t (*fn1)(uintptr_t);
typedef uint32_t (*fn2)(uintptr_t, uintptr_t);
typedef uint32_t (*fn4)(uint32_t, uint32_t, uint32_t, uint32_t);
typedef void (*publish_fn)(uintptr_t);
typedef uint32_t (*cave_fn)(uintptr_t, uint32_t);
typedef int (*claim_fn)(uintptr_t, uint32_t, uint32_t, const void *, uint32_t);
#define SL_SHARED_UI 3u
#define SL_EXCL      2u
typedef int (*claim_res_fn)(uintptr_t, uint32_t, uint32_t);
typedef void (*log_fn)(uintptr_t, const char *);

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
    return !(off & 1u) && (off + 0x01000000u) < 0x02000000u;
}
/* ARM movw/movt r2 and mov r2,#imm (the converter's three words). */
static uint32_t movw_r2(uint32_t v) { return 0xE3002000u | (v & 0xF000u) << 4 | (v & 0xFFFu); }
static uint32_t movt_r2(uint32_t v) { return 0xE3402000u | (v & 0xF0000000u) >> 12 | (v & 0x0FFF0000u) >> 16; }
static uint32_t movw_value(uint32_t w) { return (w >> 4 & 0xF000u) | (w & 0xFFFu); }
static uint32_t mov_r2(uint32_t n) { return 0xE3A02000u | (n & 0xFFu); }

/* "\fpSup\31S16.BIN" -> "31S16" in two words, NUL-padded (ui_pool.c name_of). */
static void name_of(uintptr_t path, uint32_t w[2]) {
    uintptr_t base = path;
    w[0] = w[1] = 0;
    for (uint32_t k = 0; k < 64u && peek8(path + k); ++k)
        if (peek8(path + k) == '\\') base = path + k + 1u;
    for (uint32_t k = 0; k < 8u; ++k) {
        uint32_t c = peek8(base + k);
        if (!c || c == '.') break;
        w[k >> 2] |= c << (8u * (k & 3u));
    }
}
/* 1 if (n0, n1) is a sup the books list as holding `site`. */
static uint32_t holds(uintptr_t svc, uint32_t site, uint32_t n0, uint32_t n1) {
    uintptr_t path;
    uint32_t n[2];
    if (!svc || !n0) return 0;
    for (uint32_t i = 0; i < MAX_HOLDERS; ++i) {
        path = (uintptr_t)((holder_fn)peek(svc + SVC_HOLDER))(svc, site, i);
        if (!path) return 0;
        name_of(path, n);
        if (n[0] == n0 && n[1] == n1) return 1;
    }
    return 0;
}

uint32_t qs_rows_now(void) {
    uint32_t w = peek(FV_SITE), rows = 0, start = 1, n;
    uintptr_t veneer, head, table, copy = 0, size = 0;
    if (w == FV_STOCK) return 2;                        /* nobody added a row */
    veneer = bw_target(FV_SITE, w);
    if (!veneer || (veneer & 3u) || peek(veneer) != THUMB_VENEER) return 0;
    head = peek(veneer + 4) - FV_ENTRY_OFF;
    if (peek(head) != FV_MAGIC) return 0;
    table = peek(head + 8);
    n = peek(table + 8);
    for (uint32_t i = 0; i < n; ++i)
        if (peek(table + 16 + 12 * i) == RES_CSV) {
            copy = peek(table + 20 + 12 * i);
            size = peek(table + 24 + 12 * i);
        }
    if (!copy) return 2;
    for (uint32_t i = 0; i < size; ++i) {               /* ui_apply.c csv_rows */
        uint32_t c = peek8(copy + i);
        if (c == '\n') { start = 1; continue; }
        if (c == '\r') continue;
        if (start) { rows++; start = 0; }
    }
    return rows ? rows - 1u : 0;                        /* minus the header line */
}

/* The header of the QS layer whose entry `field` names is at `entry`, or 0:
 * the word before the entry is its offset, the header repeats it (v2). */
static uintptr_t qs_header_of(uintptr_t entry, uint32_t field) {
    uintptr_t h;
    uint32_t off;
    if (!entry || (entry & 3u) || entry < 4u) return 0;
    off = peek(entry - 4u);
    if ((off & 3u) || off < QS_H_MIN_LEN || off > 0x10000u || entry < off) return 0;
    h = entry - off;
    if (peek(h + QS_H_VERSION) != QS_LAYER_VERSION || peek(h + QS_H_LEN) < QS_H_MIN_LEN ||
        peek(h + field) != off)
        return 0;
    return h;
}

/* The layer entry the site's B.W reaches now: 1 and *entry (0 = the stock
 * word); 0 if the site holds anything else. */
static uint32_t site_now(uintptr_t svc, uint32_t site, uint32_t stock, uint32_t field,
                         uintptr_t *entry) {
    uint32_t w = peek(site);
    uintptr_t veneer, hdr;
    if (w == stock) { *entry = 0; return 1; }
    veneer = bw_target(site, w);
    if (!veneer || (veneer & 3u) || peek(veneer) != THUMB_VENEER) return 0;
    *entry = peek(veneer + 4);
    hdr = qs_header_of(*entry, field);
    return hdr && holds(svc, site, peek(hdr + QS_H_NAME), peek(hdr + QS_H_NAME + 4));
}

/* ---- the row <-> enum table ------------------------------------------------ */
/* The shared table the converter points at now: its pairs, or 0 = stock; 1 on
 * success, 0 if the converter holds something else. */
static uint32_t pairs_now(uintptr_t svc, uintptr_t *pairs) {
    uint32_t w = peek(QS_CONV_MOVW), t = peek(QS_CONV_MOVT);
    uintptr_t a;
    if (w == movw_r2(QS_CONV_STOCK_TABLE) && t == movt_r2(QS_CONV_STOCK_TABLE) &&
        peek(QS_CONV_COUNT) == mov_r2(3)) {
        *pairs = 0;
        return 1;
    }
    a = movw_value(w) | movw_value(t) << 16;
    if (movw_r2(a) != w || movt_r2(a) != t || (a & 3u) || a < QS_PAIRS_HEAD) return 0;
    a -= QS_PAIRS_HEAD;
    if (peek(a + 8) != QS_LAYER_VERSION || peek(a + 12) != QS_PAIRS_MAX ||
        !holds(svc, QS_CONV_MOVW, peek(a), peek(a + 4)))
        return 0;
    *pairs = a + QS_PAIRS_HEAD;
    return 1;
}
static uint32_t pairs_count(void) { return peek(QS_CONV_COUNT) & 0xFFu; }

/* ---- the pack ------------------------------------------------------------- */
uintptr_t qs_pack_place(uintptr_t pack, uint32_t len, uintptr_t area, uint32_t room) {
    uintptr_t to = (area + 63u) & ~(uintptr_t)63u;
    if (!pack || len < 16 || peek(pack) != 0x0052424Eu || to + len > area + room) return 0;
    if (to != pack) {                                   /* overlapping moves: the right way round */
        if (to < pack) for (uint32_t i = 0; i < len; ++i) poke8(to + i, peek8(pack + i));
        else for (uint32_t i = len; i-- > 0;) poke8(to + i, peek8(pack + i));
    }
    return to;
}

/* Register this sup's pack in `ctx` once (og3k_ui.S ensure_ui_pack): 1 ready. */
static uint32_t ensure_pack(uintptr_t header, uintptr_t ctx) {
    uint32_t st;
    if (!peek(header + QS_H_PACK)) return 1;
    if (peek(header + QS_H_PACK_CTX) == ctx && (st = peek(header + QS_H_PACK_STATE)) != 0)
        return st == 1;
    poke(header + QS_H_PACK_CTX, ctx);
    poke(header + QS_H_PACK_STATE, 2);
    st = ((fn4)peek(header + QS_H_LOAD_REPLAY))(ctx, peek(header + QS_H_PACK),
                                                 peek(header + QS_H_PACK_LEN), 0);
    poke(header + QS_H_PACK_STATE, st == 0 ? 1u : 3u);
    if (st) poke(header + QS_H_STATUS, QS_ST_PACK);
    return st == 0;
}

/* ---- entry: check, then hang ------------------------------------------------ */
/* The five firmware ranges the layers change, claimed SHARED_UI with their
 * stock words: the claim comes before any look at the sites, because the
 * loader's first claim of a range repairs it (a hook a missed power-off left
 * there, after a warm restart) -- before it, a stale layer would read as a
 * foreign hook (camera, d9, 2026-10-07). A second claim by the same sup is
 * answered the same, so a sup that claims these itself changes nothing. */
static uint32_t claim_sites(uintptr_t svc) {
    uint32_t w[2];
    claim_fn claim = (claim_fn)peek(svc + SVC_CLAIM);
    w[0] = QS_SITE_STOCK;
    if (claim(svc, QS_SITE, 4, w, SL_SHARED_UI)) return 0;
    w[0] = QS_LAY_STOCK;
    if (claim(svc, QS_LAY_SITE, 4, w, SL_SHARED_UI)) return 0;
    w[0] = QS_LOAD_STOCK;
    if (claim(svc, QS_LOAD_SITE, 4, w, SL_SHARED_UI)) return 0;
    w[0] = movw_r2(QS_CONV_STOCK_TABLE); w[1] = movt_r2(QS_CONV_STOCK_TABLE);
    if (claim(svc, QS_CONV_MOVW, 8, w, SL_SHARED_UI)) return 0;
    w[0] = mov_r2(3);
    if (claim(svc, QS_CONV_COUNT, 4, w, SL_SHARED_UI)) return 0;
    return 1;
}

/* "QS FAIL ENUM n" to LOAD.LOG (no string constants: -fropi, no .rodata). */
static void log_enum(uintptr_t svc, uint32_t v) {
    char line[20];
    uint32_t i = 0;
    const uint32_t head[3] = {0x46205351u, 0x204C4941u, 0x4D554E45u};   /* "QS FAIL ENUM" */
    for (uint32_t k = 0; k < 12; ++k) line[i++] = (char)(head[k >> 2] >> (8u * (k & 3u)));
    line[i++] = ' ';
    if (v >= 100) { line[i++] = (char)('0' + v / 100); v %= 100; line[i++] = (char)('0' + v / 10); v %= 10; }
    else if (v >= 10) { line[i++] = (char)('0' + v / 10); v %= 10; }
    line[i++] = (char)('0' + v);
    line[i] = 0;
    ((log_fn)peek(svc + SVC_LOG))(svc, line);
}

uint32_t qs_check(uintptr_t header, uintptr_t svc, const struct qs_option *opt) {
    uintptr_t e, pairs, bump;
    uint32_t n = opt->n ? opt->n : qs_rows_now(), cnt;
    if (!svc || !((self_fn)peek(svc + SVC_SELF))(svc)) return QS_NAME;
    /* this sup's resolution value is its own on this card (ui/enums.py) */
    if (opt->enum_value > 0xFFu ||
        ((claim_res_fn)peek(svc + SVC_CLAIM_RES))(svc, QS_RES_ENUM_BASE + opt->enum_value, SL_EXCL)) {
        log_enum(svc, opt->enum_value);
        return QS_ENUM_TAKEN;
    }
    if (!claim_sites(svc)) return QS_HOOK;
    if (!site_now(svc, QS_SITE, QS_SITE_STOCK, QS_H_OFF_REC, &e) ||
        !site_now(svc, QS_LAY_SITE, QS_LAY_STOCK, QS_H_OFF_LAY, &e) ||
        !site_now(svc, QS_LOAD_SITE, QS_LOAD_STOCK, QS_H_OFF_LOAD, &e))
        return QS_HOOK;
    if (!qs_n_supported(opt->table, n, 0) || opt->k >= n || opt->k < 2u || opt->k > 0xFFu) return QS_NMAX;
    if (!pairs_now(svc, &pairs)) return QS_TABLE;
    cnt = pairs ? pairs_count() : 2u;
    if (cnt >= QS_PAIRS_MAX) return QS_TABLE;
    if (pairs)
        for (uint32_t i = 0; i < cnt; ++i)
            if (peek(pairs + 8 * i) == opt->enum_value || peek(pairs + 8 * i + 4) == opt->k)
                return QS_ENUM_TAKEN;
    if (opt->enum_value == 2 || opt->enum_value == 3) return QS_ENUM_TAKEN;   /* FHD, UHD */
    if (opt->pack && (opt->pack & 63u || opt->pack_len < 16 || peek(opt->pack) != 0x0052424Eu))
        return QS_PACK_BAD;
    /* the cave hang will use, reserved now so hang cannot fail */
    bump = ((cave_fn)peek(svc + SVC_CAVE))(svc, 24);
    if (!bump || (bump & 7u) || !bw_reaches(QS_SITE, bump) || !bw_reaches(QS_LAY_SITE, bump + 8) ||
        !bw_reaches(QS_LOAD_SITE, bump + 16))
        return QS_ROOM;
    poke(header + QS_H_CAVE, bump);
    poke(header + QS_H_PAIRS_NEW, 0);
    if (!pairs) {
        uintptr_t t = ((cave_fn)peek(svc + SVC_CAVE))(svc, QS_PAIRS_BYTES);
        if (!t || (t & 7u)) return QS_ROOM;
        poke(header + QS_H_PAIRS_NEW, t);
    }
    return QS_OK;
}

static uintptr_t veneer_to(uintptr_t v, uintptr_t entry) {
    poke(v, THUMB_VENEER);
    poke(v + 4, entry);
    return v;
}

void qs_hang(uintptr_t header, uintptr_t svc, const struct qs_option *opt) {
    uintptr_t rec = 0, lay = 0, load = 0, pairs = 0, path, v1, v2, v3;
    uint32_t n[2], cnt;
    path = (uintptr_t)((self_fn)peek(svc + SVC_SELF))(svc);
    site_now(svc, QS_SITE, QS_SITE_STOCK, QS_H_OFF_REC, &rec);
    site_now(svc, QS_LAY_SITE, QS_LAY_STOCK, QS_H_OFF_LAY, &lay);
    site_now(svc, QS_LOAD_SITE, QS_LOAD_STOCK, QS_H_OFF_LOAD, &load);
    pairs_now(svc, &pairs);
    name_of(path, n);
    poke(header + QS_H_NAME, n[0]);
    poke(header + QS_H_NAME + 4, n[1]);
    poke(header + QS_H_VERSION, QS_LAYER_VERSION);
    poke(header + QS_H_NEXT, rec);
    poke(header + QS_H_LAY_NEXT, lay);
    poke(header + QS_H_LOAD_NEXT, load);
    poke(header + QS_H_TABLE, opt->table);
    poke(header + QS_H_K, opt->k);
    for (uint32_t i = 0; i < 4; ++i) poke(header + QS_H_IDS + 4 * i, opt->ids[i]);
    poke(header + QS_H_N, 0);
    poke(header + QS_H_RECORD, (uint32_t)(uintptr_t)&qs_record);
    poke(header + QS_H_LAYOUT, (uint32_t)(uintptr_t)&qs_layout);
    poke(header + QS_H_LOAD, (uint32_t)(uintptr_t)&qs_load);
    poke(header + QS_H_REPLAY, header + QS_REPLAY_RECORD + 1u);
    poke(header + QS_H_LAY_REPLAY, header + QS_REPLAY_LAYOUT + 1u);
    poke(header + QS_H_LOAD_REPLAY, header + QS_REPLAY_LOAD + 1u);
    poke(header + QS_H_SVC, svc);
    poke(header + QS_H_ENUM, opt->enum_value);
    poke(header + QS_H_FLAGS, opt->flags);
    /* "not seen yet": if the value is already ours when the first layout loads
     * (power-on with this format selected), that counts as becoming ours and
     * the cache's QS layout -- parsed before the layers -- is re-parsed once
     * (camera 2026-10-07: no custom image at power-on) */
    poke(header + QS_H_LAST_ENUM, 0xFFFFFFFFu);
    poke(header + QS_H_PACK, opt->pack);
    poke(header + QS_H_PACK_LEN, opt->pack_len);
    poke(header + QS_H_PACK_CTX, 0);
    poke(header + QS_H_PACK_STATE, 0);
    poke(header + QS_H_LAY_TAKEN, 0);
    for (uint32_t i = 0; i < 6; ++i) poke(header + QS_H_SNAP + 4 * i, 0);
    poke(header + QS_H_RELEASES, 0);
    poke(header + QS_H_PENDING, 0);
    poke(header + QS_H_STATUS, 0);
    poke(header + QS_H_STATE, QS_READY);

    /* the pair table: the stock UHD/FHD pairs (not the spare (4,2)) the first
     * time, then (enum, k) */
    if (!pairs) {
        uintptr_t t = peek(header + QS_H_PAIRS_NEW);
        poke(t, n[0]); poke(t + 4, n[1]);
        poke(t + 8, QS_LAYER_VERSION); poke(t + 12, QS_PAIRS_MAX);
        pairs = t + QS_PAIRS_HEAD;
        for (uint32_t i = 0; i < 4; ++i) poke(pairs + 4 * i, peek(QS_CONV_STOCK_TABLE + 4 * i));
        cnt = 2;
    } else {
        cnt = pairs_count();
    }
    poke(pairs + 8 * cnt, opt->enum_value);
    poke(pairs + 8 * cnt + 4, opt->k);
    poke(QS_CONV_COUNT, mov_r2(cnt + 1u));
    poke(QS_CONV_MOVW, movw_r2(pairs));
    poke(QS_CONV_MOVT, movt_r2(pairs));

    v1 = veneer_to(peek(header + QS_H_CAVE), header + QS_ENTRY_RECORD);
    v2 = veneer_to(peek(header + QS_H_CAVE) + 8, header + QS_ENTRY_LAYOUT);
    v3 = veneer_to(peek(header + QS_H_CAVE) + 16, header + QS_ENTRY_LOAD);
    ((publish_fn)peek(svc + SVC_PUBLISH))(svc);
    poke(QS_LOAD_SITE, bw_word(QS_LOAD_SITE, v3));      /* the pack first: records may need it */
    poke(QS_SITE, bw_word(QS_SITE, v1));
    poke(QS_LAY_SITE, bw_word(QS_LAY_SITE, v2));
    ((publish_fn)peek(svc + SVC_PUBLISH))(svc);
}

/* ---- record layer ------------------------------------------------------------ */
static uint32_t call_next(uintptr_t header, uintptr_t reader) {
    uintptr_t next = peek(header + QS_H_NEXT);
    if (!next) next = peek(header + QS_H_REPLAY);
    return ((fn1)next)(reader);
}

static uint32_t layer_n(uintptr_t header) {
    uint32_t n = peek(header + QS_H_N);
    if (!n) {
        n = qs_rows_now();
        poke(header + QS_H_N, n);
    }
    return n;
}

static void copy_bytes(uintptr_t to, uintptr_t from, uint32_t n) {
    for (uint32_t i = 0; i < n; ++i) poke8(to + i, peek8(from + i));
}

/* The record is stock: this layer is the first to change it, so the copy
 * (and the scratch inner layers use) lives in this frame. */
__attribute__((noinline))
static uint32_t first_copy(uintptr_t reader, uintptr_t header, uint32_t row, uintptr_t src,
                           uint32_t len, uintptr_t base, uint32_t pos) {
    uint32_t buf[QS_COPY_BYTES / 4];
    uintptr_t b = (uintptr_t)buf, rec = b + QS_COPY_HEAD, scratch = rec + QS_BUFFER;
    uint32_t cur_len = len, ids[4], what, r;
    for (uint32_t i = 0; i < 4; ++i) ids[i] = peek(header + QS_H_IDS + 4 * i);
    what = qs_layer(peek(header + QS_H_TABLE), row, src, src, &cur_len, scratch,
                    layer_n(header), peek(header + QS_H_K), ids);
    if (what != QS_WROTE) return call_next(header, reader);
    copy_bytes(rec, scratch, cur_len);
    buf[0] = peek(header + QS_H_NAME);
    buf[1] = peek(header + QS_H_NAME + 4);
    buf[2] = QS_LAYER_VERSION;
    buf[3] = QS_NBU_BASE;
    poke(reader + R_BASE, rec - pos);
    r = call_next(header, reader);
    poke(reader + R_BASE, base);
    poke(reader + R_POS, pos + len);                    /* past the stock record */
    return r;
}

uint32_t qs_record(uintptr_t reader, uintptr_t header) {
    uintptr_t base, cur, src, table;
    uint32_t pos, row, len, cur_len, ids[4], what;
    if (peek(header + QS_H_STATE) != QS_READY) return call_next(header, reader);
    base = peek(reader + R_BASE);
    pos = peek(reader + R_POS);
    cur = base + pos;
    src = QS_NBU_BASE + pos;
    if (base != QS_NBU_BASE) {                          /* an outer layer's copy? */
        if (peek(cur - 8) != QS_LAYER_VERSION || peek(cur - 4) != QS_NBU_BASE ||
            !holds(peek(header + QS_H_SVC), QS_SITE, peek(cur - 16), peek(cur - 12)))
            return call_next(header, reader);
    }
    if (!ensure_pack(header, peek(reader + R_CTX))) return call_next(header, reader);
    table = peek(header + QS_H_TABLE);
    row = qs_find(table, src);
    if (!row) return call_next(header, reader);
    len = rec_be32(src + 4);
    if (qs_fnv(src, len) != peek(table + peek(table + 8) + 16 * (row - 1u) + 4))
        return call_next(header, reader);
    if (base == QS_NBU_BASE) return first_copy(reader, header, row, src, len, base, pos);
    /* edit the outer layer's copy in place, with its scratch */
    for (uint32_t i = 0; i < 4; ++i) ids[i] = peek(header + QS_H_IDS + 4 * i);
    cur_len = rec_be32(cur + 4);
    what = qs_layer(table, row, src, cur, &cur_len, cur + QS_BUFFER, layer_n(header),
                    peek(header + QS_H_K), ids);
    if (what == QS_WROTE) copy_bytes(cur, cur + QS_BUFFER, cur_len);
    return call_next(header, reader);
}

/* ---- layout layer (og3k_ui.S ui_layout) ---------------------------------------- */
/* The cache's layout `l` may be let go: it has a tree, held only by the
 * cache, the cache is not busy, and it is neither the current nor the held
 * screen (QS closed). */
static uint32_t releasable(uintptr_t l) {
    uintptr_t tree = peek(l + 0xC), ctx = peek(l + 4);
    return tree && peek8(RCACHE + 0x28) == 0 && peek(tree + 0xC) == 1 &&
           peek(ctx + 0x84C) != l && peek(ctx + 0x848) != l;
}

/* Which of the cache's three QS layouts `l` is (-1: none). A layout the
 * cache had not built at the snapshot is looked up again (no tree recorded:
 * it was built through the record layers). */
static int32_t qs_slot(uintptr_t snap, uintptr_t ctx, uintptr_t l) {
    for (uint32_t i = 0; l && i < 3; ++i) {
        uintptr_t at = peek(snap + 8 * i);
        uint32_t name = peek(RCACHE_NAMES + 4 * i);
        if (!at && name && (at = ((fn2)LAYOUT_FIND)(ctx, name))) poke(snap + 8 * i, at);
        if (at == l) return (int32_t)i;
    }
    return -1;
}

uint32_t qs_layout(uintptr_t layout, uintptr_t header) {
    uintptr_t next = peek(header + QS_H_LAY_NEXT), snap = header + QS_H_SNAP;
    uintptr_t ctx = peek(layout + 4), held, mark, tree;
    int32_t at;
    uint32_t want;
    if (!next) next = peek(header + QS_H_LAY_REPLAY);
    if (peek(header + QS_H_STATE) != QS_READY) return ((fn1)next)(layout);
    if (!peek(header + QS_H_LAY_TAKEN)) {               /* the trees built before us */
        poke(header + QS_H_LAY_TAKEN, 1);
        for (uint32_t i = 0; i < 3; ++i) {
            uint32_t name = peek(RCACHE_NAMES + 4 * i);
            uintptr_t l = name ? ((fn2)LAYOUT_FIND)(ctx, name) : 0;
            poke(snap + 8 * i, l);
            poke(snap + 8 * i + 4, l ? peek(l + 0xC) : 0);
        }
    }
    if (peek(header + QS_H_FLAGS) & QS_FLAG_SWITCH) {   /* became mine: re-parse once */
        uint32_t now = res_value(), mine = peek(header + QS_H_ENUM);
        if (now == mine && peek(header + QS_H_LAST_ENUM) != mine) poke(header + QS_H_PENDING, 1);
        poke(header + QS_H_LAST_ENUM, now);
    }
    /* Only while ANOTHER layout loads: never the one being loaded. v5c did
     * that, inside its own load, and passed the tree to v3 as if it were the
     * cache (v3 then wrote into the tree) -- the camera froze. */
    held = peek(RCACHE + 4);
    if (qs_slot(snap, ctx, layout) >= 0 || (at = qs_slot(snap, ctx, held)) < 0)
        return ((fn1)next)(layout);
    mark = snap + 8 * (uint32_t)at + 4;
    tree = peek(held + 0xC);
    if (peek(mark) && peek(mark) != tree) poke(mark, 0); /* rebuilt since: through us */
    want = peek(mark) || peek(header + QS_H_PENDING);
    if (want && releasable(held)) {
        poke(mark, 0);                                  /* one shot per layout */
        poke(header + QS_H_PENDING, 0);
        poke(header + QS_H_RELEASES, peek(header + QS_H_RELEASES) + 1u);
        ((fn1)RCACHE_V3)(RCACHE);
    }
    return ((fn1)next)(layout);
}

/* ---- pack layer (og3k_ui.S ui_load) --------------------------------------------- */
uint32_t qs_load(uint32_t *args, uintptr_t header) {
    uintptr_t next = peek(header + QS_H_LOAD_NEXT);
    uint32_t r;
    if (!next) next = peek(header + QS_H_LOAD_REPLAY);
    r = ((fn4)next)(args[0], args[1], args[2], args[3]);
    if (r == 0 && args[1] == QS_STOCK_NBR && peek(header + QS_H_STATE) == QS_READY)
        ensure_pack(header, args[0]);
    return r;
}
