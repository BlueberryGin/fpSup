#include "native_reader.h"

_Static_assert(sizeof(void *) == 4, "native reader requires ARM32 pointers");
#define INLINE static __attribute__((always_inline)) inline
#define MAGIC UINT32_C(0x46504e52)
#define NBU UINT32_C(0xc18c0460)
#define POOL (NBU + 20u)
#define POOL_LEN 176152u
#define START UINT32_C(0x76ff04)
#define END UINT32_C(0x795950)
#define SOURCE_MAX (UINT32_C(0xc2ef6e00) - NBU)
#define READER_SIZE 0xb8u
#define WORD(r, offset) (*(volatile uint32_t *)((uint8_t *)(r) + (offset)))

INLINE uint32_t address(const void *p) { return (uint32_t)(uintptr_t)p; }
INLINE uint32_t be32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | p[3];
}
INLINE int span(uint32_t p, uint32_t n)
{ return p && n && n <= UINT32_MAX - p; }
INLINE int overlap(uint32_t a, uint32_t an, uint32_t b, uint32_t bn)
{ return a < b + bn && b < a + an; }
INLINE uint32_t fnv(const uint8_t *p, uint32_t n)
{
    uint32_t h = UINT32_C(0x811c9dc5), i;
    for (i = 0; i < n; ++i) h = (h ^ p[i]) * UINT32_C(0x01000193);
    return h;
}
INLINE int valid_config(const struct fp_nr_config *c)
{
    return span(address(c->page), c->page_length) &&
           span(address(c->pool), c->pool_length) &&
           c->page_length >= 28u && c->page_length <= 0x100000u &&
           c->pool_length <= 0x100000u &&
           c->pool_length > POOL_LEN && c->header_length >= 20 &&
           c->header_length <= c->page_length - 8u &&
           c->records >= 2 && c->records <= c->page_length / 8 &&
           c->objects == 214u && (address((const void *)c->original_parser) & 1u) &&
           !overlap(address(c->page), c->page_length, address(c->pool), c->pool_length) &&
           !overlap(address(c->page), c->page_length, NBU, SOURCE_MAX) &&
           !overlap(address(c->pool), c->pool_length, NBU, SOURCE_MAX);
}
INLINE int page_valid(const struct fp_nr_config *c)
{
    uint32_t pos = 0, count = 0, terminal = 0, i;
    while (pos < c->page_length) {
        uint32_t tag, len;
        if (c->page_length - pos < 8) return 0;
        tag = be32(c->page + pos); len = be32(c->page + pos + 4);
        if (len < 8 || len > c->page_length - pos) return 0;
        if (!count && (tag != 0x10002u || len != c->header_length ||
                       be32(c->page + 8) != c->objects)) return 0;
        if (count && tag == 0x10002u) return 0;
        if (tag == UINT32_MAX) {
            if (len != 8 || pos != c->page_length - 8) return 0;
            terminal = 1;
        }
        if (++count > c->records) return 0;
        pos += len;
    }
    if (!terminal || count != c->records ||
        fnv(c->page, c->page_length) != c->page_fnv ||
        fnv(c->pool, c->pool_length) != c->pool_fnv ||
        fnv((const uint8_t *)(NBU + START), END - START) != 0x617110b3u)
        return 0;
    for (i = 0; i < POOL_LEN; ++i)
        if (c->pool[i] != ((const uint8_t *)POOL)[i]) return 0;
    return 1;
}
INLINE int private_view(struct fp_nr_state *s)
{
    void *r = s->reader;
    return WORD(r, 8) == s->saved_app && WORD(r, 12) == s->saved_owner &&
           WORD(r, 0x10) == s->config.pool_length &&
           WORD(r, 0x14) == address(s->config.pool) && !WORD(r, 0x18) &&
           WORD(r, 0x20) == s->config.page_length &&
           WORD(r, 0x24) == address(s->config.page) && !WORD(r, 0x28);
}
INLINE uint32_t poison(struct fp_nr_state *s)
{
    void *r = s->reader;
    /* Native selector publishes on ANY nonzero parser return if both survive. */
    if (s->phase != FP_NR_DEAD) {
        s->partial_count = WORD(r, 0x50);
        s->partial_array = WORD(r, 0x54);
        s->partial_root = WORD(r, 0xb4);
    }
    WORD(r, 0x50) = 0;
    WORD(r, 0xb4) = 0;
    s->phase = FP_NR_DEAD;
    return FP_NR_POISONED;
}

uint32_t fp_nr_init(struct fp_nr_state *s, const struct fp_nr_config *c)
{
    if (!s || !c || (address(s) & 3u) || (address(c) & 3u) ||
        !span(address(s), sizeof(*s)) || !span(address(c), sizeof(*c)) ||
        overlap(address(s), sizeof(*s), address(c), sizeof(*c)) ||
        s->magic || s->phase || !valid_config(c) ||
        overlap(address(s), sizeof(*s), address(c->page), c->page_length) ||
        overlap(address(s), sizeof(*s), address(c->pool), c->pool_length))
        return FP_NR_INVALID;
    s->config.page = c->page; s->config.pool = c->pool;
    s->config.page_length = c->page_length; s->config.pool_length = c->pool_length;
    s->config.page_fnv = c->page_fnv; s->config.pool_fnv = c->pool_fnv;
    s->config.records = c->records; s->config.header_length = c->header_length;
    s->config.objects = c->objects; s->config.original_parser = c->original_parser;
    s->magic = MAGIC; s->phase = FP_NR_PREPARED;
    return FP_NR_OK;
}

uint32_t fp_nr_parse(struct fp_nr_state *s, void *r)
{
    uint32_t p, len, tag, result;
    if (!s || !r || (address(s) & 3u) || (address(r) & 3u) ||
        !span(address(s), sizeof(*s)) || !span(address(r), READER_SIZE) ||
        s->magic != MAGIC) return FP_NR_INVALID;
    if (s->phase == FP_NR_DEAD || s->phase == FP_NR_ACTIVE) return poison(s);
    if (s->phase != FP_NR_PREPARED) return FP_NR_REFUSED;
    if (WORD(r, 0x24) != NBU || WORD(r, 4) != START) return FP_NR_NOT_TARGET;
    if (!valid_config(&s->config) ||
        overlap(address(r), READER_SIZE, address(s), sizeof(*s)) ||
        overlap(address(r), READER_SIZE, address(s->config.page), s->config.page_length) ||
        overlap(address(r), READER_SIZE, address(s->config.pool), s->config.pool_length) ||
        !WORD(r, 8) || !WORD(r, 12) || (WORD(r, 12) & 3u) ||
        WORD(r, 0x14) != POOL || WORD(r, 0x10) != POOL_LEN ||
        WORD(r, 0x18) || WORD(r, 0x28) || WORD(r, 0x50) || WORD(r, 0xb4) ||
        WORD(r, 0x20) < END || WORD(r, 0x20) > SOURCE_MAX ||
        !page_valid(&s->config)) return FP_NR_REFUSED;
    s->reader = r;
    s->saved_cursor = WORD(r, 4); s->saved_app = WORD(r, 8);
    s->saved_owner = WORD(r, 12); s->saved_pool_length = WORD(r, 0x10);
    s->saved_pool = WORD(r, 0x14); s->saved_owns_source = WORD(r, 0x18);
    s->saved_source_length = WORD(r, 0x20); s->saved_source = WORD(r, 0x24);
    s->saved_stream = WORD(r, 0x28);
    s->cursor = 0; s->records = 0; s->last_result = 0;
    s->phase = FP_NR_ACTIVE;
    WORD(r, 4) = 0; WORD(r, 0x10) = s->config.pool_length;
    WORD(r, 0x14) = address(s->config.pool);
    WORD(r, 0x20) = s->config.page_length;
    WORD(r, 0x24) = address(s->config.page);
    while (s->records < s->config.records) {
        p = s->cursor;
        if (s->phase != FP_NR_ACTIVE || !private_view(s) || WORD(r, 4) != p ||
            p > s->config.page_length || s->config.page_length - p < 8)
            return poison(s);
        tag = be32(s->config.page + p); len = be32(s->config.page + p + 4);
        if (len < 8 || len > s->config.page_length - p) return poison(s);
        result = s->config.original_parser(r);
        s->last_result = result;
        if (s->phase != FP_NR_ACTIVE || !private_view(s) || WORD(r, 4) != p + len ||
            result != (tag == UINT32_MAX ? 1u : 0u)) return poison(s);
        s->cursor = p + len; ++s->records;
    }
    if (s->cursor != s->config.page_length || s->last_result != 1 ||
        WORD(r, 0x48) != s->config.objects || WORD(r, 0x50) != s->config.objects ||
        !WORD(r, 0x54) || !WORD(r, 0xb4)) return poison(s);
    /* Only stream/source fields are restored; native allocations must survive. */
    WORD(r, 4) = END; WORD(r, 8) = s->saved_app; WORD(r, 12) = s->saved_owner;
    WORD(r, 0x10) = s->saved_pool_length; WORD(r, 0x14) = s->saved_pool;
    WORD(r, 0x18) = s->saved_owns_source; WORD(r, 0x20) = s->saved_source_length;
    WORD(r, 0x24) = s->saved_source; WORD(r, 0x28) = s->saved_stream;
    s->phase = FP_NR_PINNED;
    return FP_NR_COMPLETE;
}

const char *fp_nr_string(struct fp_nr_state *s, void *r, uint32_t offset)
{
    uint32_t end;
    if (!s || !r || (address(s) & 3u) || (address(r) & 3u) ||
        s->magic != MAGIC || s->phase != FP_NR_PINNED || s->reader != r ||
        WORD(r, 8) != s->saved_app || WORD(r, 12) != s->saved_owner ||
        WORD(r, 0x24) != s->saved_source || WORD(r, 0x20) != s->saved_source_length ||
        WORD(r, 0x14) != s->saved_pool || WORD(r, 0x10) != s->saved_pool_length ||
        WORD(r, 0x18) || WORD(r, 0x28) || offset < POOL_LEN ||
        offset >= s->config.pool_length) return (const char *)0;
    end = offset;
    while (end < s->config.pool_length && s->config.pool[end]) ++end;
    if (end == s->config.pool_length) return (const char *)0;
    return (const char *)s->config.pool + offset;
}
