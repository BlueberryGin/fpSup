#ifndef FPLOSSLESS_REC_HOOK_H
#define FPLOSSLESS_REC_HOOK_H
#include "rec_workspace.h"
#include "producer_facts.h"

#define FPL_REC_HOOK_MAGIC 0x4b4f4f48u

/* The site: C03A3398 calls C03A2438(camera, request) with BL C03A2438 at
 * C03A33C8 and dispatches the event only if that returns non-zero. Every
 * request type C03A2438 handles passes this one BL -- 1, 3, 0xF, 0x14, 0x26
 * among them -- so everything but event 3 must reach the original exactly as
 * it would have without us: same arguments, same result, nothing touched.
 *
 * The firmware passes two arguments and the adapter needs a third, so the
 * anchor at the site (a short stub in the cave, per SUP_BUILD_RULES: cave
 * holds anchors, bodies live in memory the launcher allocated) loads the
 * resident state into r2 and branches here. That stub, the launcher that
 * allocates this state, and the journal declaration of the site word are the
 * installer's; none of them is in this file. */
struct fpl_rec_hook {
    uint32_t magic;
    uint32_t calls, passed_through, admitted, refused, invalid;
    uint32_t last_request, last_result;
    struct fpl_rec_workspace workspace;
    struct fpl_producer_facts facts;
    struct fpl_rec_workspace_setup setup;
};

/* Fresh, zeroed, resident storage. `facts` probes and readiness are filled by
 * the caller after this and before the hook is armed. */
uint32_t fpl_rec_hook_init(struct fpl_rec_hook *, uint32_t session,
                           uint32_t metadata_bytes);

/* The hook body. Returns what C03A3398 expects from C03A2438: non-zero to
 * dispatch the event, zero to refuse it. */
uint32_t fpl_rec_hook_call(uintptr_t camera, const uint32_t *request,
                           struct fpl_rec_hook *hook);
#endif
