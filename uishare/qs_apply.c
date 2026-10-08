#include "qs_apply.h"

/* See qs_apply.h. The reference is ui/qs.py; test_qs_c.py compares them. */

#if defined(QS_HOST_TEST)
#define peek8(a)      (*(const uint8_t *)(a))
#define poke8(a, v)   (*(uint8_t *)(a) = (uint8_t)(v))
#elif defined(__arm__) && UINTPTR_MAX == UINT32_MAX
#define peek8(a)      (*(volatile const uint8_t *)(a))
#define poke8(a, v)   (*(volatile uint8_t *)(a) = (uint8_t)(v))
#else
#error "ARM32 or QS_HOST_TEST"
#endif

static uint32_t le16(uintptr_t a) { return peek8(a) | peek8(a + 1) << 8; }
static uint32_t le32(uintptr_t a) { return le16(a) | le16(a + 2) << 16; }
static uint32_t be32(uintptr_t a) {
    return (uint32_t)peek8(a) << 24 | peek8(a + 1) << 16 | peek8(a + 2) << 8 | peek8(a + 3);
}
static void put_be32(uintptr_t a, uint32_t v) {
    poke8(a, v >> 24); poke8(a + 1, v >> 16); poke8(a + 2, v >> 8); poke8(a + 3, v);
}
static void copy(uintptr_t to, uintptr_t from, uint32_t n) {
    for (uint32_t i = 0; i < n; ++i) poke8(to + i, peek8(from + i));
}
static uint32_t same(uintptr_t a, uintptr_t b, uint32_t n) {
    for (uint32_t i = 0; i < n; ++i) if (peek8(a + i) != peek8(b + i)) return 0;
    return 1;
}

#define ROW_BYTES 16u
#define FN_BYTES  12u
#define T_COUNT   4u
#define T_ROWS    8u
#define T_EDITS   12u
#define T_FN      16u
#define T_NMAX    20u

uint32_t qs_fnv(uintptr_t p, uint32_t n) {
    uint32_t h = 0x811C9DC5u;
    for (uint32_t i = 0; i < n; ++i) h = (h ^ peek8(p + i)) * 0x01000193u;
    return h;
}

uint32_t qs_find(uintptr_t tab, uint32_t src) {
    uintptr_t rows = tab + le32(tab + T_ROWS);
    uint32_t lo = 0, hi = le32(tab + T_COUNT);
    while (lo < hi) {
        uint32_t mid = (lo + hi) >> 1, a = le32(rows + ROW_BYTES * mid);
        if (a == src) return mid + 1u;
        if (src > a) lo = mid + 1u; else hi = mid;
    }
    return 0;
}

static uintptr_t fn_row(uintptr_t tab, uint32_t n) {
    if (n > le32(tab + T_NMAX)) return 0;
    uintptr_t f = tab + le32(tab + T_FN) + FN_BYTES * n;
    return le32(f) ? f : 0;
}

uint32_t qs_n_supported(uintptr_t tab, uint32_t n, uint32_t *verified) {
    uintptr_t f = fn_row(tab, n);
    if (verified) *verified = f ? peek8(f + 9) : 0;
    return f != 0;
}

/* old -> out by an edit list. 0 if it would not fit. */
static uint32_t edit(uintptr_t edits, uintptr_t old, uint32_t len, uintptr_t out, uint32_t cap,
                     const uint32_t ids[4]) {
    uint32_t cur = 0, o = 0;
    for (;;) {
        uint32_t at = le16(edits), del = le16(edits + 2), n = le16(edits + 4);
        if (at == 0xFFFFu) break;
        if (at < cur || at > len) return 0;
        if (o + (at - cur) > cap) return 0;
        copy(out + o, old + cur, at - cur);
        o += at - cur;
        edits += 6;
        if (n & 0x8000u) {
            if (o + 4u > cap) return 0;
            put_be32(out + o, ids[n & 3u]);
            o += 4;
        } else {
            if (o + n > cap) return 0;
            copy(out + o, edits, n);
            o += n;
            edits += (n + 1u) & ~1u;
        }
        cur = at + del;
    }
    if (cur > len || o + (len - cur) > cap) return 0;
    copy(out + o, old + cur, len - cur);
    return o + (len - cur);
}

/* og3k_ui.S ui_layout_copy, as ui/qs.py clone_key. */
static uint32_t clone(uintptr_t old, uint32_t len, uintptr_t out, uint32_t cap, uint32_t time,
                      uint32_t prop, uint32_t has_value, uint32_t value) {
    uint32_t count, src = 28, o = 28;
    if (len < 28 || cap < 28) return 0;
    copy(out, old, 28);
    count = be32(old + 12);
    for (uint32_t p = 0; p < count; ++p) {
        uint32_t keys;
        if (src + 20 > len) return 0;
        keys = be32(old + src);
        if (src + 20 + 10 * keys > len || keys == 0 || o + 20 + 10 * keys + 10 > cap) return 0;
        copy(out + o, old + src, 20 + 10 * keys);
        poke8(out + o + 3, keys + 1u);
        o += 20 + 10 * keys;
        copy(out + o, old + src + 20, 10);
        put_be32(out + o + 2, time);
        if (has_value && p == prop) put_be32(out + o + 6, value);
        o += 10;
        src += 20 + 10 * keys;
    }
    if (src != len) return 0;
    put_be32(out + 4, o);
    return o;
}

/* The bytes this layer wants: length, 0 = not its row, 0xFFFFFFFF = error. */
static uint32_t target(uintptr_t tab, uintptr_t row, uintptr_t old, uint32_t len, uintptr_t out,
                       uint32_t n, uint32_t k, const uint32_t ids[4]) {
    uint32_t rule = peek8(row + 10), a = peek8(row + 11), b = le16(row + 12);
    uintptr_t edits = tab + le32(tab + T_EDITS) + le16(row + 14);
    uintptr_t f = fn_row(tab, n);
    uint32_t r;
    if (!f) return 0xFFFFFFFFu;
    switch (rule) {
    case QS_CLONE:
        r = clone(old, len, out, QS_BUFFER, b >> 8, a ? a - 1u : 0u, a && (b & 0xFFu) != 0xFFu,
                  (b & 0xFFu) != 0xFFu ? ids[b & 3u] : 0u);
        return r ? r : 0xFFFFFFFFu;
    case QS_END:
        if (len < 40 || len > QS_BUFFER) return 0xFFFFFFFFu;
        copy(out, old, len);
        put_be32(out + 36, b);
        return len;
    case QS_MAX:
        if (len < 32 || len + 4 > QS_BUFFER || (peek8(old + 31) & 2u)) return 0xFFFFFFFFu;
        copy(out, old, 32);
        put_be32(out + 32, le32(f));
        copy(out + 36, old + 32, len - 32);
        put_be32(out + 4, len + 4);
        poke8(out + 31, peek8(old + 31) | 2u);
        return len + 4;
    case QS_CURSOR:
        if (a + 4 > len || b + 4 > len || len > QS_BUFFER) return 0xFFFFFFFFu;
        copy(out, old, len);
        put_be32(out + a, le32(f + 4));
        put_be32(out + b, le32(f + 4));
        return len;
    case QS_FOOTER_N8:
        if (!peek8(f + 8)) {
            if (len > QS_BUFFER) return 0xFFFFFFFFu;
            copy(out, old, len);
            return len;
        }
        r = edit(edits, old, len, out, QS_BUFFER, ids);
        return r ? r : 0xFFFFFFFFu;
    case QS_FOOTER_LABEL:
        if (a != k) return 0;
        r = edit(edits, old, len, out, QS_BUFFER, ids);
        return r ? r : 0xFFFFFFFFu;
    }
    return 0xFFFFFFFFu;
}

uint32_t qs_layer(uintptr_t tab, uint32_t row, uintptr_t stock, uintptr_t cur, uint32_t *cur_len,
                  uintptr_t scratch, uint32_t n, uint32_t k, const uint32_t ids[4]) {
    uintptr_t r;
    uint32_t len, want;
    if (!row || row > le32(tab + T_COUNT)) return QS_ERROR;
    r = tab + le32(tab + T_ROWS) + ROW_BYTES * (row - 1u);
    len = le16(r + 8);
    want = target(tab, r, stock, len, scratch, n, k, ids);
    if (want == 0) return QS_SKIP;
    if (want == 0xFFFFFFFFu) return QS_ERROR;
    if (*cur_len == want && same(cur, scratch, want)) return QS_DONE;
    if (*cur_len == len && same(cur, stock, len)) {
        *cur_len = want;
        return QS_WROTE;
    }
    return QS_CONFLICT;
}
