#include "ui_apply.h"
#include "ui_pool.h"

/* Ver.5.02 runtime layout (fp-native-ui §8a, read from the camera):
 *   reader+A8 = number of runtime page entries, reader+AC = the entries,
 *   44 bytes each: +04 name (C string), +08 page offset from the NBU base. */
#define READER_ENTRIES 0xA8u
#define READER_TABLE   0xACu
#define ENTRY_BYTES    44u
#define ENTRY_NAME     0x04u
#define ENTRY_OFFSET   0x08u
#define NBU_BASE       0xC18C0460u
#define MAX_ENTRIES    512u

#define OP_PAGE        1u
#define OP_GUARD       2u
#define OP_INSERT      3u
#define OP_ADD32       4u
#define OP_ADDF        5u
#define OP_ALLOC       6u
#define OP_DONE        7u
#define OP_HOOK_FV     8u
#define OP_FILE        9u
#define OP_CSV_ADD     10u
#define OP_CSV_CELL    11u
#define OP_FILE_SET    12u
#define OP_FILE_DONE   13u
#define OP_EXPECT      14u
#define OP_SETSTR_SLOT 15u
#define R_LOCAL_ID 1u
#define R_STRING   2u
#define R_SLOT_F32 3u
#define R_SLOT_U32 4u
#define NAME_STRING 0xFFFFFFFEu
#define MAX_STRINGS 64u
#define MAX_ARGS    24u
#define MAX_PAGES   4u
#define MAX_FILES   8u
#define FSHK        0x4B485346u          /* "FSHK": the shared file-redirect handler */
#define FSFV        0x56465346u          /* "FSFV": its table */

#if defined(UIA_HOST_TEST)
#define N (&uia_test_natives)
#define peek(a) N->read(a)
#define poke(a, v) N->write(a, v)
#define peek8(a) N->read_byte(a)
#define poke8(a, v) N->write_byte(a, v)
#define alloc(n) N->alloc(n)
#define publish() N->publish()
#define icache() N->icache()
#elif defined(__arm__) && UINTPTR_MAX == UINT32_MAX
/* Firmware structures through volatile accesses; our own copies are ordinary
 * cached memory and are published (D-cache clean) before the switch. */
#define peek(a) (*(volatile const uint32_t *)(a))
#define poke(a, v) (*(volatile uint32_t *)(a) = (v))
#define peek8(a) (*(const uint8_t *)(a))
#define poke8(a, v) (*(uint8_t *)(a) = (uint8_t)(v))
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

/* ---- small helpers ------------------------------------------------------ */
static uint32_t le(uintptr_t a) {
    return peek8(a) | peek8(a + 1) << 8 | peek8(a + 2) << 16 | (uint32_t)peek8(a + 3) << 24;
}
static uint32_t be(uintptr_t a) {
    return (uint32_t)peek8(a) << 24 | peek8(a + 1) << 16 | peek8(a + 2) << 8 | peek8(a + 3);
}
static void put_be(uintptr_t a, uint32_t v) {
    poke8(a, v >> 24); poke8(a + 1, v >> 16); poke8(a + 2, v >> 8); poke8(a + 3, v);
}
/* memmove, a word at a time wherever source and destination share their
 * alignment (pages are copied to the same alignment as their source, and the
 * firmware's records insert in whole words): a byte loop over 160 KB of page
 * costs a noticeable part of a second at boot. */
static void move(uintptr_t to, uintptr_t from, uint32_t n) {
    if (to == from || !n) return;
    if (to < from || to >= from + n) {                  /* forward */
        if (!((to ^ from) & 3u)) {
            while (n && (to & 3u)) { poke8(to, peek8(from)); ++to; ++from; --n; }
            for (; n >= 4u; n -= 4u, to += 4u, from += 4u) poke(to, peek(from));
        }
        for (uint32_t i = 0; i < n; ++i) poke8(to + i, peek8(from + i));
    } else {                                            /* backward, overlapping */
        if (!((to ^ from) & 3u)) {
            while (n && ((to + n) & 3u)) { --n; poke8(to + n, peek8(from + n)); }
            for (; n >= 4u; n -= 4u) poke(to + n - 4u, peek(from + n - 4u));
        }
        while (n--) poke8(to + n, peek8(from + n));
    }
}
static uint32_t fnv(uintptr_t p, uint32_t n) {
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < n; ++i) h = (h ^ peek8(p + i)) * 16777619u;
    return h;
}
static uint32_t cstr_eq(uintptr_t a, uintptr_t b, uint32_t n) {
    for (uint32_t i = 0; i < n; ++i) if (peek8(a + i) != peek8(b + i)) return 0;
    return peek8(a + n) == 0;
}

/* Integer-valued IEEE single floats, without float arithmetic. */
static uint32_t f32_to_int(uint32_t w, int32_t *v) {
    uint32_t e = (w >> 23) & 0xFFu, m = (w & 0x7FFFFFu) | 0x800000u;
    int32_t r;
    if (!(w & 0x7FFFFFFFu)) { *v = 0; return 1; }
    if (e < 127u || e > 127u + 23u) return 0;                /* fraction or too big */
    if (m & ((1u << (23u - (e - 127u))) - 1u)) return 0;     /* not an integer */
    r = (int32_t)(m >> (23u - (e - 127u)));
    *v = (w & 0x80000000u) ? -r : r;
    return 1;
}
static uint32_t int_to_f32(int32_t v, uint32_t *w) {
    uint32_t s = 0, m, e = 150u;
    if (v <= -(1 << 24) || v >= (1 << 24)) return 0;
    if (!v) { *w = 0; return 1; }
    if (v < 0) { s = 0x80000000u; m = (uint32_t)-v; } else m = (uint32_t)v;
    while (m < 0x800000u) { m <<= 1; --e; }
    *w = s | e << 23 | (m & 0x7FFFFFu);
    return 1;
}
static uint32_t name_hash(uintptr_t s, uint32_t n) { uint32_t h = fnv(s, n); return h ? h : 1u; }

/* Struct copies word by word: an assignment becomes a memcpy call, and the
 * camera build links nothing. */
static void copy_words(void *to, const void *from, uint32_t bytes) {
    uint32_t *t = (uint32_t *)to;
    const uint32_t *f = (const uint32_t *)from;
    for (uint32_t i = 0; i < bytes / 4u; ++i) { volatile uint32_t v = f[i]; t[i] = v; }
}

/* ---- a page being built ---------------------------------------------------- */
struct uia_work {
    uintptr_t entry, page, deltas;        /* entry word owner; new page; its table */
    uint32_t len, cap, stock_len, next_id, n_deltas, max_deltas;
    uint32_t counters[UIA_COUNTERS][2];
    uint32_t first_id, active;
};

struct uia_arena { uintptr_t next, end; };

static uintptr_t ui_alloc(struct uia_arena *arena, uint32_t n) {
    uintptr_t p;
    if (!arena) return alloc(n);
    p = (arena->next + 7u) & ~(uintptr_t)7u;
    if (p < arena->next || p > arena->end || n > arena->end - p) return alloc(n);
    arena->next = p + n;
    return p;
}

static uint32_t current(const struct uia_work *w, uint32_t pos, uint32_t *at) {
    uint32_t c = pos;
    if (pos > w->stock_len) return 0;
    for (uint32_t i = 0; i < w->n_deltas; ++i)
        if (peek(w->deltas + 8u * i) <= pos) c += peek(w->deltas + 8u * i + 4u);
    if (c + 4u > w->len && c != w->len) return 0;
    *at = c;
    return 1;
}

/* The page's runtime entry, by name. */
static uintptr_t find_entry(uintptr_t reader, uintptr_t name, uint32_t n) {
    uint32_t count = peek(reader + READER_ENTRIES);
    uintptr_t table = peek(reader + READER_TABLE);
    if (!count || count > MAX_ENTRIES || !table || (table & 3u)) return 0;
    for (uint32_t i = 0; i < count; ++i) {
        uintptr_t e = table + ENTRY_BYTES * i, s = peek(e + ENTRY_NAME);
        if (s && cstr_eq(s, name, n)) return e;
    }
    return 0;
}

static uint32_t open_page(struct uia_work *w, uintptr_t name, uint32_t name_n,
                          const uint32_t *a, struct uia_arena *arena) {
    uintptr_t reader = uis_reader(), e, old, block, hdr;
    uint32_t old_len, old_next, old_n = 0, deltas_cap;
    if (!reader || !(e = find_entry(reader, name, name_n))) return UIA_NO_UI;
    old = NBU_BASE + peek(e + ENTRY_OFFSET);
    hdr = (old & ~3u) - UIA_HEADER;                      /* a copy's header, word-aligned */
    if (peek(e + ENTRY_OFFSET) == a[1]) {                       /* the stock page */
        old_len = a[2]; old_next = a[3];
        for (uint32_t i = 0; i < UIA_COUNTERS; ++i) w->counters[i][0] = w->counters[i][1] = 0;
    } else if (peek(hdr) == UIA_MAGIC && peek(hdr + 4) == UIA_VERSION &&
               peek(hdr + 16) == a[2]) {                       /* a convention copy */
        old_len = peek(hdr + 12);
        old_next = peek(hdr + 20);
        old_n = peek(hdr + 24);
        if (old_next < a[3]) old_next = a[3];
        for (uint32_t i = 0; i < UIA_COUNTERS; ++i) {
            w->counters[i][0] = peek(hdr + 36 + 8 * i);
            w->counters[i][1] = peek(hdr + 40 + 8 * i);
        }
    } else {
        return UIA_PAGE;
    }
    if (old_next + a[4] > 0x10000u || old_n + a[6] < old_n) return UIA_FULL;
    deltas_cap = old_n + a[6];
    w->cap = old_len + a[5];
    block = ui_alloc(arena, 8u * deltas_cap + UIA_HEADER + 4u + w->cap);
    if (!block || (block & 7u)) return UIA_NO_MEMORY;
    w->deltas = block;
    w->page = block + 8u * deltas_cap + UIA_HEADER + (old & 3u);   /* the source's alignment */
    if (old_n) move(w->deltas, peek(hdr + 32), 8u * old_n);
    move(w->page, old, old_len);
    w->entry = e;
    w->len = old_len;
    w->stock_len = a[2];
    w->first_id = old_next;
    w->next_id = old_next + a[4];
    w->n_deltas = old_n;
    w->max_deltas = deltas_cap;
    w->active = 1;
    return UIA_OK;
}

/* The copy's header; the entry is switched at commit. */
static void close_page(struct uia_work *w) {
    uintptr_t h = (w->page & ~3u) - UIA_HEADER;
    poke(h + 0, UIA_MAGIC); poke(h + 4, UIA_VERSION); poke(h + 8, w->cap);
    poke(h + 12, w->len); poke(h + 16, w->stock_len); poke(h + 20, w->next_id);
    poke(h + 24, w->n_deltas); poke(h + 28, w->max_deltas); poke(h + 32, w->deltas);
    for (uint32_t i = 0; i < UIA_COUNTERS; ++i) {
        poke(h + 36 + 8 * i, w->counters[i][0]); poke(h + 40 + 8 * i, w->counters[i][1]);
    }
    for (uint32_t o = 36 + 8 * UIA_COUNTERS; o < UIA_HEADER; o += 4) poke(h + o, 0);
    w->active = 0;
}

/* ---- files: the shared redirect table --------------------------------------- */
struct uia_file {
    uintptr_t buf;
    uint32_t stock, len, cap, rows_before, appended, active;
};

/* The table the handler at `site` reads: found if another sup installed it,
 * else installed now from the handler code in the block. a: site, stock word,
 * capacity, fragment offset, length, entry offset, cave bump word, arena end,
 * veneer word, offset of the handler's table word. */
static uint32_t hook_fv(const uint32_t *a, uintptr_t frag, uint32_t nf,
                         uintptr_t *table, struct uia_arena *arena) {
    uintptr_t site = a[0], block, entry, veneer, bump;
    uint32_t w = peek(site), off, hw1, hw2, s, j1, j2, i1, i2;
    if (w != a[1]) {                                     /* someone's B.W: ours? */
        hw1 = w & 0xFFFFu; hw2 = w >> 16;
        if ((hw1 & 0xF800u) != 0xF000u || (hw2 & 0xD000u) != 0x9000u) return UIA_HOOK;
        s = (hw1 >> 10) & 1u; j1 = (hw2 >> 13) & 1u; j2 = (hw2 >> 11) & 1u;
        i1 = 1u ^ (j1 ^ s); i2 = 1u ^ (j2 ^ s);
        off = s << 24 | i1 << 23 | i2 << 22 | (hw1 & 0x3FFu) << 12 | (hw2 & 0x7FFu) << 1;
        if (s) off |= 0xFE000000u;
        veneer = site + 4u + off;
        if (veneer & 3u || peek(veneer) != a[8]) return UIA_HOOK;
        entry = peek(veneer + 4);
        if ((entry & 3u) || peek(entry - a[5]) != FSHK || peek(entry - a[5] + 4) != 1u)
            return UIA_HOOK;
        *table = peek(entry - a[5] + a[9]);
        if (!*table || (*table & 3u) || peek(*table) != FSFV || peek(*table + 4) != 1u)
            return UIA_HOOK;
        return UIA_OK;
    }
    if (a[3] > nf || a[4] > nf - a[3] || a[4] < 16u || a[5] >= a[4] || a[9] + 4u > a[4] ||
        !a[2] || a[2] > 256u)
        return UIA_OP;
    bump = peek(a[6]);
    if ((bump & 3u) || bump + 8u > a[7] || bump < a[6]) return UIA_FULL;
    block = ui_alloc(arena, ((a[4] + 3u) & ~3u) + 16u + 12u * a[2]);
    if (!block || (block & 7u)) return UIA_NO_MEMORY;
    move(block, frag + a[3], a[4]);
    *table = block + ((a[4] + 3u) & ~3u);
    poke(*table, FSFV); poke(*table + 4, 1u); poke(*table + 8, 0u); poke(*table + 12, a[2]);
    poke(block + a[9], *table);
    entry = block + a[5];
    poke(a[6], bump + 8u);                               /* the veneer is ours */
    poke(bump, a[8]); poke(bump + 4, entry);             /* ldr.w pc, [pc, #0]; entry (ARM) */
    publish(); icache();
    off = bump - (site + 4u);
    s = off >> 31; i1 = (off >> 23) & 1u; i2 = (off >> 22) & 1u;
    j1 = (1u ^ i1) ^ s; j2 = (1u ^ i2) ^ s;
    hw1 = 0xF000u | s << 10 | ((off >> 12) & 0x3FFu);
    hw2 = 0x9000u | j1 << 13 | j2 << 11 | ((off >> 1) & 0x7FFu);
    poke(site, hw1 | hw2 << 16);                         /* B.W veneer */
    publish(); icache();
    return UIA_OK;
}

static uint32_t table_find(uintptr_t table, uint32_t stock, uint32_t *copy, uint32_t *size) {
    uint32_t n = peek(table + 8);
    for (uint32_t i = 0; i < n; ++i)
        if (peek(table + 16 + 12 * i) == stock) {
            *copy = peek(table + 20 + 12 * i); *size = peek(table + 24 + 12 * i);
            return 1;
        }
    return 0;
}

/* CSV text: data rows (every non-empty line after the header). */
static uint32_t csv_rows(const struct uia_file *f) {
    uint32_t rows = 0, start = 1;
    for (uint32_t i = 0; i < f->len; ++i) {
        uint32_t c = peek8(f->buf + i);
        if (c == '\n') { start = 1; continue; }
        if (c == '\r') continue;
        if (start) { rows++; start = 0; }
    }
    return rows ? rows - 1u : 0u;                       /* the header is not a row */
}
static uint32_t csv_crlf(const struct uia_file *f) {
    for (uint32_t i = 0; i + 1 < f->len; ++i)
        if (peek8(f->buf + i) == '\r' && peek8(f->buf + i + 1) == '\n') return 1;
    return 0;
}
/* Template expansion: {N} = n, {P} = n - 1, {Q} = n - 2. Returns bytes or 0. */
static uint32_t expand(uintptr_t tpl, uint32_t tn, uint32_t n, char *out, uint32_t cap) {
    uint32_t o = 0;
    for (uint32_t i = 0; i < tn; ++i) {
        uint32_t c = peek8(tpl + i);
        if (c == '{' && i + 2 < tn && peek8(tpl + i + 2) == '}') {
            uint32_t k = peek8(tpl + i + 1), v, d = 1;
            if (k == 'N') v = n; else if (k == 'P') v = n - 1u; else if (k == 'Q') v = n - 2u; else return 0;
            static const uint32_t TENS[4] = {1000u, 100u, 10u, 1u};
            if (v > 9999u) return 0;
            for (uint32_t t = 0, lead = 1; t < 4u; ++t) {     /* no divide on a Cortex-A9 */
                uint32_t digit = 0;
                while (v >= TENS[t]) { v -= TENS[t]; ++digit; }
                if (lead && digit == 0u && t < 3u) continue;
                lead = 0;
                if (o >= cap) return 0;
                out[o++] = (char)('0' + digit);
            }
            (void)d;
            i += 2;
        } else {
            if (o >= cap) return 0;
            out[o++] = (char)c;
        }
    }
    return o;
}
static uint32_t file_append(struct uia_file *f, const char *s, uint32_t n) {
    if (f->len + n > f->cap) return 0;
    for (uint32_t i = 0; i < n; ++i) poke8(f->buf + f->len + i, (uint8_t)s[i]);
    f->len += n;
    return 1;
}
static uint32_t csv_add(struct uia_file *f, uintptr_t tpl, uint32_t tn, uint32_t *index) {
    char line[96];
    uint32_t rows = csv_rows(f), n, crlf = csv_crlf(f);
    if (!f->appended) { f->rows_before = rows; f->appended = 1; }
    if (f->len && peek8(f->buf + f->len - 1) != '\n')
        if (!(crlf ? file_append(f, "\r\n", 2) : file_append(f, "\n", 1))) return UIA_FULL;
    if (!(n = expand(tpl, tn, rows + 1u, line, sizeof line))) return UIA_OP;
    if (!file_append(f, line, n) || !(crlf ? file_append(f, "\r\n", 2) : file_append(f, "\n", 1)))
        return UIA_FULL;
    *index = rows;                                      /* the new row, 0-based */
    return UIA_OK;
}
/* Replace column `col` of data row `row` (1-based). */
static uint32_t csv_cell(struct uia_file *f, uint32_t row, uint32_t col, uintptr_t tpl, uint32_t tn) {
    char cell[32];
    uint32_t n, line = 0, start = 1, i = 0, from, to, c, k;
    if (!(n = expand(tpl, tn, csv_rows(f), cell, sizeof cell))) return UIA_OP;
    for (; i < f->len; ++i) {                           /* the start of data row `row` */
        c = peek8(f->buf + i);
        if (c == '\n') { start = 1; continue; }
        if (c == '\r') continue;
        if (start) { if (line == row) break; line++; start = 0; }
    }
    if (i >= f->len) return UIA_OP;
    for (k = 0; k < col; ++k) {                         /* to the column */
        while (i < f->len && peek8(f->buf + i) != ',' && peek8(f->buf + i) != '\r' &&
               peek8(f->buf + i) != '\n') ++i;
        if (i >= f->len || peek8(f->buf + i) != ',') return UIA_OP;
        ++i;
    }
    from = to = i;
    while (to < f->len && peek8(f->buf + to) != ',' && peek8(f->buf + to) != '\r' &&
           peek8(f->buf + to) != '\n') ++to;
    if (f->len - (to - from) + n > f->cap) return UIA_FULL;
    move(f->buf + from + n, f->buf + to, f->len - to);
    for (k = 0; k < n; ++k) poke8(f->buf + from + k, (uint8_t)cell[k]);
    f->len = f->len - (to - from) + n;
    return UIA_OK;
}

/* Lossless's 18 string references name only seven strings. Pool offsets remain
 * valid when the shared pool grows: its existing prefix is copied unchanged.
 * Resolve each string index only once per block. */
static uint32_t string_offset(uint32_t index, const uintptr_t *str_at,
                              const uint32_t *str_n, const uint32_t *str_hint,
                              uint32_t *cached, uint32_t *known, uint32_t *offset) {
    char text[64];
    uint32_t mask = 1u << (index & 31u);
    if (!(known[index >> 5] & mask)) {
        for (uint32_t c = 0; c <= str_n[index]; ++c)
            text[c] = (char)peek8(str_at[index] + c);
        if (uis_intern_hinted(text, str_n[index], str_hint[index], &cached[index]) != UIS_OK)
            return UIA_STRING;
        known[index >> 5] |= mask;
    }
    *offset = cached[index];
    return UIA_OK;
}

/* ---- the block ------------------------------------------------------------ */
static uint32_t apply(uintptr_t b, uint32_t bytes, struct uia_outcome *out,
                      struct uia_arena *arena) {
    uintptr_t str_at[MAX_STRINGS], ops, frag, table = 0;
    uint32_t str_n[MAX_STRINGS], str_hint[MAX_STRINGS];
    uint32_t str_cached[MAX_STRINGS];
    uint32_t str_known[2];
    uint32_t total, ns, nw, nf, cur, i, n_pages = 0, n_files = 0;
    struct uia_work w, pages[MAX_PAGES];
    struct uia_file f, files[MAX_FILES];
    w.active = 0; f.active = 0;
    str_known[0] = str_known[1] = 0;
    out->result = UIA_BLOCK; out->op = 0; out->first_id = 0; out->n_slots = 0; out->page = 0;
    if (!b || bytes < 0x20u || le(b) != 0x49555046u || le(b + 4) != 1u || le(b + 28) != 0)
        return UIA_BLOCK;
    total = le(b + 8); ns = le(b + 16); nw = le(b + 20); nf = le(b + 24);
    if (total < 0x20u || total > bytes || fnv(b + 0x20, total - 0x20) != le(b + 12) ||
        ns > MAX_STRINGS)
        return UIA_BLOCK;
    cur = 0x20;
    for (i = 0; i < ns; ++i) {
        uint32_t n;
        if (cur + 8u > total) return UIA_BLOCK;
        str_hint[i] = le(b + cur); n = le(b + cur + 4);
        if (!n || n >= 64u || cur + 8u + n + 1u > total || peek8(b + cur + 8 + n)) return UIA_BLOCK;
        for (uint32_t k = 0; k < n; ++k) if (!peek8(b + cur + 8 + k)) return UIA_BLOCK;
        str_at[i] = b + cur + 8; str_n[i] = n;
        cur += (8u + n + 1u + 3u) & ~3u;
    }
    if (nw > (total - cur) / 4u) return UIA_BLOCK;
    ops = b + cur;
    frag = ops + 4u * nw;
    if (nf > total - cur - 4u * nw) return UIA_BLOCK;

    for (uint32_t k = 0, index = 0; k < nw; ++index) {
        uint32_t head = le(ops + 4u * k), code = head >> 24, n = head & 0xFFFFFFu, r = UIA_OK;
        uintptr_t a = ops + 4u * (k + 1u);
        uint32_t args[MAX_ARGS];
        out->op = index;
        if (n > nw - k - 1u) return out->result = UIA_BLOCK;
        for (i = 0; i < n && i < MAX_ARGS; ++i) args[i] = le(a + 4u * i);
        if (code == OP_PAGE) {
            if (w.active || f.active || n != 7u || args[0] >= ns || str_hint[args[0]] != NAME_STRING ||
                n_pages >= MAX_PAGES)
                return out->result = UIA_OP;
            r = open_page(&w, str_at[args[0]], str_n[args[0]], args, arena);
        } else if (code == OP_HOOK_FV) {
            if (n != 10u || table) r = UIA_OP;
            else r = hook_fv(args, frag, nf, &table, arena);
        } else if (code == OP_FILE) {
            uint32_t copy = args[0], size = args[1];
            if (w.active || f.active || !table || n != 3u || n_files >= MAX_FILES ||
                args[2] > 0x10000u) { r = UIA_OP; goto done; }
            for (i = 0; i < n_files; ++i) if (files[i].stock == args[0]) { r = UIA_OP; goto done; }
            table_find(table, args[0], &copy, &size);
            f.cap = size + args[2];
            f.buf = ui_alloc(arena, f.cap + 4u);
            if (!f.buf) { r = UIA_NO_MEMORY; goto done; }
            move(f.buf, copy, size);
            f.stock = args[0]; f.len = size; f.appended = 0; f.rows_before = 0; f.active = 1;
        } else if (code == OP_CSV_ADD || code == OP_CSV_CELL || code == OP_FILE_SET ||
                   code == OP_FILE_DONE) {
            if (!f.active) { r = UIA_OP; goto done; }
            if (code == OP_CSV_ADD) {
                uint32_t idx;
                if (n != 1u || args[0] >= ns || out->n_slots >= UIA_MAX_SLOTS) { r = UIA_OP; goto done; }
                if ((r = csv_add(&f, str_at[args[0]], str_n[args[0]], &idx)) == UIA_OK)
                    out->slots[out->n_slots++] = idx;
            } else if (code == OP_CSV_CELL) {
                uint32_t row;
                if (n != 3u || args[2] >= ns || !f.appended) { r = UIA_OP; goto done; }
                row = args[0] == 0u ? 1u : args[0] == 1u ? f.rows_before : 0u;
                if (!row) { r = UIA_OP; goto done; }
                r = csv_cell(&f, row, args[1], str_at[args[2]], str_n[args[2]]);
            } else if (code == OP_FILE_SET) {
                uint32_t copy, size;
                if (n != 2u || args[0] > nf || args[1] > nf - args[0] || args[1] > f.cap)
                    { r = UIA_OP; goto done; }
                if (table_find(table, f.stock, &copy, &size)) { r = UIA_CONFLICT; goto done; }
                move(f.buf, frag + args[0], args[1]);
                f.len = args[1];
            } else {
                if (n) { r = UIA_OP; goto done; }
                copy_words(&files[n_files++], &f, sizeof f);
                f.active = 0;
            }
        } else if (code == OP_EXPECT) {
            if (n != 2u || args[0] >= out->n_slots) r = UIA_OP;
            else if (out->slots[args[0]] != args[1]) r = UIA_GUARD;
        } else if (code == OP_ALLOC) {
            uint32_t h, slot = UIA_COUNTERS;
            if (!w.active || n != 3u || args[0] >= ns || out->n_slots >= UIA_MAX_SLOTS) { r = UIA_OP; goto done; }
            h = name_hash(str_at[args[0]], str_n[args[0]]);
            for (i = 0; i < UIA_COUNTERS && slot == UIA_COUNTERS; ++i)
                if (w.counters[i][0] == h) slot = i;
            for (i = 0; i < UIA_COUNTERS && slot == UIA_COUNTERS; ++i)
                if (!w.counters[i][0]) { slot = i; w.counters[i][0] = h; w.counters[i][1] = 0; }
            if (slot == UIA_COUNTERS) { r = UIA_FULL; goto done; }
            out->slots[out->n_slots++] = args[1] + args[2] * w.counters[slot][1]++;
        } else if (!w.active) {
            r = UIA_OP;
        } else if (code == OP_GUARD) {
            uint32_t at;
            if (n != 2u || !current(&w, args[0], &at) || at + 4u > w.len) r = UIA_OP;
            else if (be(w.page + at) != args[1]) r = UIA_GUARD;
        } else if (code == OP_ADD32 || code == OP_ADDF) {
            uint32_t at, v;
            int32_t x;
            if (n != 2u || !current(&w, args[0], &at) || at + 4u > w.len) r = UIA_OP;
            else if (code == OP_ADD32) put_be(w.page + at, be(w.page + at) + args[1]);
            else if (!f32_to_int(be(w.page + at), &x) ||
                     !int_to_f32(x + (int32_t)args[1], &v)) r = UIA_OP;
            else put_be(w.page + at, v);
        } else if (code == OP_SETSTR_SLOT) {
            uint32_t at, idx, v;
            if (n < 4u || n > MAX_ARGS || args[0] >= out->n_slots || args[3] >= ns ||
                str_hint[args[3]] == NAME_STRING) { r = UIA_OP; goto done; }
            idx = out->slots[args[0]] - args[1];
            if (idx >= args[2] || idx >= n - 4u || !current(&w, args[4 + idx], &at) ||
                at + 4u > w.len) { r = UIA_OP; goto done; }
            r = string_offset(args[3], str_at, str_n, str_hint, str_cached, str_known, &v);
            if (r != UIA_OK) goto done;
            put_be(w.page + at, v);
        } else if (code == OP_INSERT) {
            uint32_t pos = args[0], off, len, at;
            if (n < 3u || (n - 3u) % 2u) { r = UIA_OP; goto done; }
            off = args[1]; len = args[2];
            if (!len || off > nf || len > nf - off || w.len + len > w.cap ||
                w.n_deltas >= w.max_deltas || !current(&w, pos, &at)) { r = UIA_OP; goto done; }
            move(w.page + at + len, w.page + at, w.len - at);
            move(w.page + at, frag + off, len);
            for (i = 3; i < n; i += 2) {
                uint32_t rk = le(a + 4u * i), ro = le(a + 4u * i + 4u), kind = rk >> 24,
                         arg = rk & 0xFFFFFFu, v = 0;
                if (ro > len - 4u || len < 4u) { r = UIA_OP; goto done; }
                if (kind == R_LOCAL_ID) v = w.first_id + arg;
                else if (kind == R_STRING) {
                    if (arg >= ns || str_hint[arg] == NAME_STRING) { r = UIA_OP; goto done; }
                    r = string_offset(arg, str_at, str_n, str_hint, str_cached, str_known, &v);
                    if (r != UIA_OK) goto done;
                } else if (kind == R_SLOT_U32 || kind == R_SLOT_F32) {
                    if (arg >= out->n_slots) { r = UIA_OP; goto done; }
                    v = out->slots[arg];
                    if (kind == R_SLOT_F32 && !int_to_f32((int32_t)v, &v)) { r = UIA_OP; goto done; }
                } else { r = UIA_OP; goto done; }
                put_be(w.page + at + ro, v);
            }
            poke(w.deltas + 8u * w.n_deltas, pos);
            poke(w.deltas + 8u * w.n_deltas + 4u, len);
            w.n_deltas++;
            w.len += len;
        } else if (code == OP_DONE) {
            if (n) { r = UIA_OP; goto done; }
            out->first_id = w.first_id;
            out->page = w.page;
            close_page(&w);
            copy_words(&pages[n_pages++], &w, sizeof w);
        } else {
            r = UIA_OP;
        }
    done:
        if (r != UIA_OK) return out->result = r;
        k += 1u + n;
    }
    if (w.active || f.active) return out->result = UIA_OP;

    /* ---- commit: every copy exists before anyone is pointed at it -------- */
    if (n_files) {
        uint32_t cnt = peek(table + 8), fresh = 0;
        for (i = 0; i < n_files; ++i) {
            uint32_t seen = 0;
            for (uint32_t e = 0; e < cnt; ++e) if (peek(table + 16 + 12 * e) == files[i].stock) seen = 1;
            fresh += !seen;
        }
        if (cnt + fresh > peek(table + 12)) return out->result = UIA_FULL;
    }
    publish();
    for (i = 0; i < n_files; ++i) {
        uint32_t cnt = peek(table + 8), at = cnt;
        for (uint32_t e = 0; e < cnt; ++e) if (peek(table + 16 + 12 * e) == files[i].stock) at = e;
        if (at == cnt) {
            poke(table + 16 + 12 * at, files[i].stock);
            poke(table + 20 + 12 * at, files[i].buf);
            poke(table + 24 + 12 * at, files[i].len);
            publish();
            poke(table + 8, cnt + 1u);
        } else {                                        /* the copy only grows: pointer first */
            poke(table + 20 + 12 * at, files[i].buf);
            publish();
            poke(table + 24 + 12 * at, files[i].len);
        }
    }
    if (n_files) publish();                             /* the table, before any page */
    for (i = 0; i < n_pages; ++i) poke(pages[i].entry + ENTRY_OFFSET, pages[i].page - NBU_BASE);
    publish();
    return out->result = UIA_OK;
}

uint32_t uia_apply(uintptr_t block, uint32_t bytes, struct uia_outcome *out) {
    return apply(block, bytes, out, 0);
}

uint32_t uia_apply_in_arena(uintptr_t block, uint32_t bytes, struct uia_outcome *out,
                            uintptr_t arena, uint32_t arena_bytes) {
    struct uia_arena a;
    if (!arena || arena_bytes > UINTPTR_MAX - arena || bytes > UINTPTR_MAX - block ||
        (arena < block + bytes && block < arena + arena_bytes)) {
        out->result = UIA_BLOCK;
        return UIA_BLOCK;
    }
    a.next = arena;
    a.end = arena + arena_bytes;
    return apply(block, bytes, out, &a);
}
