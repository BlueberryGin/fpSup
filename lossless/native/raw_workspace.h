#ifndef FPLOSSLESS_RAW_WORKSPACE_H
#define FPLOSSLESS_RAW_WORKSPACE_H

#include "../control.h"

/* Ver.5.02 memmgr descriptor: preserve the allocator's returned handle;
 * it is not permission to derive a CPU/DMA alias or to address arbitrary RAM. */
struct fpl_raw_descriptor {
    uint32_t handle, capacity, allocator_class;
};
struct fpl_raw_workspace {
    struct fpl_raw_descriptor allocation;
    uint32_t take, requested, fault;
};

/* Fresh zeroed, persistent caller-owned state. Caller must serialize these
 * calls with REC preparation, all workspace users and native memory-mode
 * changes. Reserve after mode 3 is established, before native RAW allocations.
 * Neither this API nor checking current mode establishes that exclusion.
 *
 * take is a nonzero, non-reused take token; bytes is a nonzero 1-KiB multiple.
 * A normal allocation failure returns NOT_READY and leaves zero state.
 * Success holds one class-10 descriptor, including its native linked-pool
 * fallback. FAULT retains any returned descriptor; do not reset or blindly
 * free quarantined state. This API supplies no DMA/cache/codec readiness. */
uint32_t fpl_raw_workspace_reserve(struct fpl_raw_workspace *,
                                 uint32_t take, uint32_t bytes);

/* Call with the same take and quiescent == 1 only after every CPU/DMA consumer
 * is drained, and before changing memory mode. A mode mismatch quarantines
 * ownership, even if mode 3 is restored later. Success clears the state;
 * another release is INVALID, never a second native free. */
uint32_t fpl_raw_workspace_release(struct fpl_raw_workspace *,
                                 uint32_t take, uint32_t quiescent);

#ifdef FPL_RAW_WORKSPACE_HOST_TEST
/* Compile-time fixtures only; ARM production uses fixed native addresses. */
uint32_t fpl_raw_workspace_native_mode(void);
uint32_t fpl_raw_workspace_native_pool(uint32_t allocator_class);
uint32_t fpl_raw_workspace_native_get(uint32_t allocator,
                                    struct fpl_raw_descriptor *descriptor,
                                    uint32_t bytes, uint32_t alignment,
                                    uint32_t caller);
void fpl_raw_workspace_native_free(struct fpl_raw_descriptor *descriptor);
#endif

#endif
