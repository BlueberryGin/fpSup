#include "ui_pool.h"

/* Ver.5.02 runtime layout, read from the camera 2026-09-30:
 *   [C37B7048] = UI app; app+80 screen count, app+8C screen array
 *   screen+24  = the shared NBU reader (every screen has the same one)
 *   reader+10  = pool length, reader+14 = pool address, reader+24 = NBU base */
#define GUI_OBJECT    0xC37B7048u
#define SCREENS_COUNT 0x80u
#define SCREENS_ARRAY 0x8Cu
#define SCREEN_READER 0x24u
#define READER_LEN    0x10u
#define READER_POOL   0x14u
#define READER_BASE   0x24u
#define NBU_BASE      0xC18C0460u
#define MAX_SCREENS   512u

#if defined(UIS_HOST_TEST)
#define N (&uis_test_natives)
#define peek(a) N->read(a)
#define poke(a, v) N->write(a, v)
#define peek8(a) N->read_byte(a)
#define poke8(a, v) N->write_byte(a, v)
#define alloc(n) N->alloc(n)
#define publish() N->publish()
#elif defined(__arm__) && UINTPTR_MAX == UINT32_MAX
#define peek(a) (*(volatile const uint32_t *)(a))
#define poke(a, v) (*(volatile uint32_t *)(a) = (v))
#define peek8(a) (*(volatile const uint8_t *)(a))
#define poke8(a, v) (*(volatile uint8_t *)(a) = (uint8_t)(v))
/* USER memory from the camera's allocator, as every sup launcher gets it:
 * C001D740(desc, class 0, bytes, 0), C001D7F0(desc) -> address. Never freed. */
static uintptr_t alloc(uint32_t n) {
    typedef void (*get_fn)(uint32_t *, uint32_t, uint32_t, uint32_t);
    typedef uintptr_t (*addr_fn)(uint32_t *);
    uint32_t desc[4] = {0, 0, 0, 0};
    ((get_fn)0xC001D740u)(desc, 0, n, 0);
    return ((addr_fn)0xC001D7F0u)(desc);
}
static void publish(void) { typedef void (*fn)(void); ((fn)0xC000E91Cu)(); }
#else
#error "ARM32 native UI ABI required; host tests must explicitly substitute it"
#endif

static uint32_t aligned(uintptr_t a) { return a && !(a & 3u); }

uintptr_t uis_reader(void) {
    uintptr_t app = peek(GUI_OBJECT), screens, screen, reader;
    uint32_t count;
    if (!aligned(app)) return 0;
    count = peek(app + SCREENS_COUNT);
    screens = peek(app + SCREENS_ARRAY);
    if (!count || count > MAX_SCREENS || !aligned(screens)) return 0;
    screen = peek(screens);
    if (!aligned(screen)) return 0;
    reader = peek(screen + SCREEN_READER);
    if (!aligned(reader) || peek(reader + READER_BASE) != NBU_BASE) return 0;
    return reader;
}

static uint32_t ours(uintptr_t pool, uint32_t len) {
    return pool > UIS_HEADER && !(pool & 3u) &&
           peek(pool - 16) == UIS_MAGIC && peek(pool - 12) == UIS_VERSION &&
           peek(pool - 8) >= len && len >= UIS_STOCK_LEN;
}

uint32_t uis_pool_known(uintptr_t reader) {
    uintptr_t pool;
    uint32_t len;
    if (!reader) return 0;
    pool = peek(reader + READER_POOL);
    len = peek(reader + READER_LEN);
    return (pool == UIS_STOCK_POOL && len == UIS_STOCK_LEN) || ours(pool, len);
}

/* Where `s` already resolves in [pool, pool+len), or UINT32_MAX. */
static uint32_t find(uintptr_t pool, uint32_t len, const char *s, uint32_t n) {
    for (uint32_t i = 0; i + n < len; ++i) {
        uint32_t k = 0;
        while (k < n && peek8(pool + i + k) == (uint8_t)s[k]) ++k;
        if (k == n && peek8(pool + i + n) == 0) return i;
    }
    return UINT32_MAX;
}

static void copy(uintptr_t to, uintptr_t from, uint32_t n) {
    uint32_t i = 0;
    if (!((to | from) & 3u))
        for (; i + 4 <= n; i += 4) poke(to + i, peek(from + i));
    for (; i < n; ++i) poke8(to + i, peek8(from + i));
}

/* A new copy of the current pool with `capacity` bytes, switched in. */
static uintptr_t grow(uintptr_t reader, uintptr_t pool, uint32_t len, uint32_t capacity) {
    uintptr_t block = alloc(UIS_HEADER + capacity), fresh;
    if (!block || (block & 7u)) return 0;
    fresh = block + UIS_HEADER;
    copy(fresh, pool, len);                     /* the room past it is written
                                                   string by string */
    poke(block + 0, UIS_MAGIC);
    poke(block + 4, UIS_VERSION);
    poke(block + 8, capacity);
    poke(block + 12, 0);
    publish();
    /* pointer first: every offset below the old length means the same in
     * both, so a resolve between the two stores is still right */
    poke(reader + READER_POOL, fresh);
    publish();
    return fresh;
}

uint32_t uis_intern(const char *s, uint32_t n, uint32_t *offset) {
    uintptr_t reader = uis_reader(), pool;
    uint32_t len, at, end, capacity;
    if (!s || !offset || !n || s[n] != 0) return UIS_INVALID;
    for (uint32_t i = 0; i < n; ++i) if (!s[i]) return UIS_INVALID;
    if (!reader) return UIS_NO_READER;
    if (!uis_pool_known(reader)) return UIS_UNKNOWN_POOL;
    pool = peek(reader + READER_POOL);
    len = peek(reader + READER_LEN);
    if ((at = find(pool, len, s, n)) != UINT32_MAX) { *offset = at; return UIS_OK; }

    at = (len + 3u) & ~3u;                      /* new strings start aligned */
    end = at + n + 1u;
    capacity = pool == UIS_STOCK_POOL ? 0 : peek(pool - 8);
    if (end > capacity) {
        uint32_t want = end + UIS_HEADROOM;
        if (capacity && want < 2u * capacity) want = 2u * capacity;
        if (!(pool = grow(reader, pool, len, want))) return UIS_NO_MEMORY;
    }
    for (uint32_t i = len; i < at; ++i) poke8(pool + i, 0);
    for (uint32_t i = 0; i < n; ++i) poke8(pool + at + i, (uint8_t)s[i]);
    poke8(pool + at + n, 0);
    publish();
    poke(reader + READER_LEN, end);             /* the new string exists now */
    publish();
    *offset = at;
    return UIS_OK;
}
