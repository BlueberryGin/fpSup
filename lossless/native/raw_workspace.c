#include "raw_workspace.h"
#include <stddef.h>

#if !defined(FPL_RAW_WORKSPACE_HOST_TEST) && \
    (!defined(__arm__) || UINTPTR_MAX != UINT32_MAX)
#error "Native RAW workspace is ARM32-only; use explicit host test fixtures."
#endif

#define INLINE static __attribute__((always_inline)) inline
#define RAW_MODE 3u
#define RAW_CLASS 10u
#define RAW_ALIGNMENT 0x400u

_Static_assert(sizeof(struct fpl_raw_descriptor) == 12, "native descriptor ABI");
_Static_assert(offsetof(struct fpl_raw_descriptor, capacity) == 4,
               "native capacity offset");
_Static_assert(offsetof(struct fpl_raw_descriptor, allocator_class) == 8,
               "native allocator class offset");
_Static_assert(sizeof(struct fpl_raw_workspace) == 24, "workspace ABI");

INLINE uint32_t native_mode(void) {
#ifdef FPL_RAW_WORKSPACE_HOST_TEST
    return fpl_raw_workspace_native_mode();
#else
    typedef uint32_t (*fn)(void);
    return ((fn)0xc001cf18u)();
#endif
}
INLINE uint32_t native_pool(void) {
#ifdef FPL_RAW_WORKSPACE_HOST_TEST
    return fpl_raw_workspace_native_pool(RAW_CLASS);
#else
    typedef uint32_t (*fn)(uint32_t);
    return ((fn)0xc001cf78u)(RAW_CLASS);
#endif
}
INLINE uint32_t native_get(uint32_t pool, struct fpl_raw_descriptor *d,
                           uint32_t bytes) {
#ifdef FPL_RAW_WORKSPACE_HOST_TEST
    return fpl_raw_workspace_native_get(pool, d, bytes, RAW_ALIGNMENT, 0);
#else
    typedef uint32_t (*fn)(uint32_t, struct fpl_raw_descriptor *,
                          uint32_t, uint32_t, uint32_t);
    return ((fn)0xc001cfd8u)(pool, d, bytes, RAW_ALIGNMENT, 0);
#endif
}
INLINE void native_free(struct fpl_raw_descriptor *d) {
#ifdef FPL_RAW_WORKSPACE_HOST_TEST
    fpl_raw_workspace_native_free(d);
#else
    typedef void (*fn)(struct fpl_raw_descriptor *);
    ((fn)0xc001d3d0u)(d);
#endif
}
INLINE uint32_t aligned_state(const struct fpl_raw_workspace *s) {
    return s && !((uintptr_t)s & 3u);
}
INLINE uint32_t empty(const struct fpl_raw_workspace *s) {
    return !(s->allocation.handle | s->allocation.capacity |
             s->allocation.allocator_class | s->take | s->requested | s->fault);
}
INLINE void clear(struct fpl_raw_workspace *s) {
    s->allocation.handle = 0;
    s->allocation.capacity = 0;
    s->allocation.allocator_class = 0;
    s->take = 0;
    s->requested = 0;
    s->fault = 0;
}
INLINE uint32_t quarantine(struct fpl_raw_workspace *s) {
    s->fault = FPL_FAULT;
    return FPL_FAULT;
}
INLINE uint32_t held_shape(const struct fpl_raw_workspace *s) {
    const struct fpl_raw_descriptor *d = &s->allocation;
    return s->take && s->requested &&
           !(s->requested & (RAW_ALIGNMENT - 1u)) && d->handle &&
           !(d->handle & (RAW_ALIGNMENT - 1u)) &&
           d->allocator_class == RAW_CLASS && d->capacity >= s->requested &&
           d->capacity <= UINT32_MAX - d->handle;
}

uint32_t fpl_raw_workspace_reserve(struct fpl_raw_workspace *s,
                                 uint32_t take, uint32_t bytes) {
    uint32_t pool, result;
    if (!aligned_state(s) || !take || !bytes ||
        (bytes & (RAW_ALIGNMENT - 1u))) return FPL_INVALID;
    if (s->fault) return FPL_FAULT;
    if (!empty(s)) return FPL_BUSY;
    if (native_mode() != RAW_MODE) return FPL_NOT_READY;
    s->take = take;
    s->requested = bytes;
    pool = native_pool();
    if (!pool || (pool & 3u)) return quarantine(s);
    result = native_get(pool, &s->allocation, bytes);
    /* C001CFD8 writes class even on ordinary exhaustion; capacity remains 0.
     * Anything else is a broken native contract, not retryable lack of RAM. */
    if (!result && !s->allocation.handle && !s->allocation.capacity &&
        s->allocation.allocator_class == RAW_CLASS) {
        clear(s);
        return FPL_NOT_READY;
    }
    if (result != s->allocation.handle || !held_shape(s)) return quarantine(s);
    return FPL_OK;
}

uint32_t fpl_raw_workspace_release(struct fpl_raw_workspace *s,
                                 uint32_t take, uint32_t quiescent) {
    if (!aligned_state(s) || !take) return FPL_INVALID;
    if (s->fault) return FPL_FAULT;
    if (s->take != take || empty(s)) return FPL_INVALID;
    if (!held_shape(s)) return quarantine(s);
    if (quiescent != 1u) return FPL_BUSY;
    if (native_mode() != RAW_MODE) return quarantine(s);
    native_free(&s->allocation);
    /* C001D3D0 returns void and does not clear its input descriptor. */
    clear(s);
    return FPL_OK;
}
