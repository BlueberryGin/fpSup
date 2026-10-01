#ifndef FPLOSSLESS_REC_WORKSPACE_H
#define FPLOSSLESS_REC_WORKSPACE_H
#include "../frame_pipeline.h"
#include "raw_workspace.h"
#include "workspace_layout.h"

#define FPL_REC_WORKSPACE_MAGIC 0x52574c46u
#define FPL_REC_PREPARE_SITE 0xc03a33c8u
#define FPL_REC_PREPARE_STOCK_WORD 0xebfffc1au
/* The CameraFunction events that start a recording. 3 is the one traced
 * statically (C0B9E064 -> C03A18B0). 0x23 is what the movie button actually
 * sent on the camera, measured 2026-09-30 with the test card's request log
 * (sequence 0x2D, 0x23, 0x24, 0x2D around one take; 0x24 is the stop).
 * Its handler C03A1EB8 posts Movie message 0x23 through C03A9558. */
#define FPL_REC_EVENT_START 3u
#define FPL_REC_EVENT_MOVIE 0x23u
#define FPL_REC_IS_START(code) ((code) == FPL_REC_EVENT_START || \
                                (code) == FPL_REC_EVENT_MOVIE)

/* Read the actual producer AFTER native preparation. Called again with the
 * granted capacity after reservation: that call must assess native source
 * headroom with this allocation already held. Never synthesize readiness
 * from this module's allocation success. A missing provider denies REC.
 * Provider must not start recording or retain its stack output pointer. */
typedef uint32_t (*fpl_rec_workspace_facts)(void *, uint32_t reserved_bytes,
                                           struct fpl_context *);
struct fpl_rec_workspace_setup {
    fpl_rec_workspace_facts read;
    void *context;
    uint32_t copy_source, metadata_bytes;
    /* 1: reserve no output span. For an adapter whose engine writes its result
     * into a spare frame allocation of its own (frame_hold's buffer swap): a
     * frame-sized output it never uses only takes memory from the frames'
     * pool, which at 3K stopped recording (2026-10-01, rec log "mem"). */
    uint32_t no_output;
};
struct fpl_rec_workspace {
    uint32_t magic, last_result;
    /* What the original C03A2438 returned on the last event 3: a caller that
     * must never block recording can dispatch RAW when WE refuse, but only if
     * the firmware itself agreed. 0 until the original has been called. */
    uint32_t native_result;
    struct fpl_state control;
    struct fpl_pipeline pipeline;
    struct fpl_raw_workspace memory;
    struct fpl_workspace_layout layout;
};

/* Fresh, zeroed, independently owned resident storage. No loader staging,
 * no boot-scoped RAW allocation and no reset of a previous live instance. */
uint32_t fpl_rec_workspace_init(struct fpl_rec_workspace *, uint32_t session);

/* Task-context event-3 REC adapter. Calls original C03A2438(camera, request)
 * FIRST, then plans/reserves RAW, rechecks facts/headroom, and enters the
 * existing direct control/pipeline. Returns native boolean (1 dispatch,
 * 0 refuse); last_result is FPL_*. Not itself the two-argument hook ABI:
 * the resident caller must supply owned state/setup. No installer is here.
 * Only the traced event 3 is admitted; other events are NOT routed here.
 * Caller serializes REC, mode changes, worker and teardown throughout.
 * This does not prove/implement source hold, codec DMA bounds or handoff. */
uint32_t fpl_rec_workspace_prepare(struct fpl_rec_workspace *, uintptr_t camera,
                                   const uint32_t *request,
                                   const struct fpl_rec_workspace_setup *);

/* Stop is separate from release: the pending frame still needs completion.
 * finish also handles asynchronous native start failure/cancellation, but
 * only after its caller proves no producer/worker/callback/output user can
 * start or remain. A late failure for another take cannot free this one.
 * Invoke before native map teardown, never after C001CE88 rebuilt RAW. */
uint32_t fpl_rec_workspace_stop(struct fpl_rec_workspace *, uint32_t take);
uint32_t fpl_rec_workspace_finish(struct fpl_rec_workspace *, uint32_t take,
                                  uint32_t quiescent);
#endif
