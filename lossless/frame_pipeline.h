#ifndef FPLOSSLESS_FRAME_PIPELINE_H
#define FPLOSSLESS_FRAME_PIPELINE_H

#include <stdint.h>
#include "control.h"

#define FPL_PIPELINE_MAGIC 0x4c535046u
enum fpl_slot_phase { FPL_SLOT_FREE, FPL_SLOT_HELD, FPL_SLOT_ENCODING,
                      FPL_SLOT_READY, FPL_SLOT_COMMITTING, FPL_SLOT_RETAINED,
                      FPL_SLOT_READY_RAW };
enum fpl_frame_action { FPL_FRAME_NONE, FPL_FRAME_RAW, FPL_FRAME_SELECTED };
#define FPL_HANDOFF_SOURCE 1u
#define FPL_HANDOFF_OUTPUT 2u
#define FPL_HANDOFF_ALL (FPL_HANDOFF_SOURCE | FPL_HANDOFF_OUTPUT)

struct fpl_frame_token { uint32_t session, take, frame; };
struct fpl_frame_lease {
    struct fpl_frame_token token;
    uintptr_t buffer, metadata, writer;
    /* Both refer to the complete native file buffer, not only its raster. */
    uint32_t capacity, stock_file_bytes;
};
struct fpl_frame_output {
    uintptr_t bytes;
    uint32_t length; /* encoded tile bytes, not the full DNG */
    uint32_t file_bytes, validated; /* final DNG bytes including metadata */
};
struct fpl_pipeline {
    uint32_t magic, session, take, active, enabled, stopping, fault, phase, last_frame;
    /* handed_off counts selected frames transferred as compressed OR RAW. */
    uint32_t seen, passed, selected, handed_off;
    struct fpl_frame_lease lease;
    struct fpl_frame_output output;
};

/* Task-context policy ONLY: callers serialize every call with REC/menu/worker
 * transitions. An IRQ queues a completion notification; it never calls this
 * API, copies an image, performs I/O, or frees memory.
 *
 * This core neither creates native holds nor proves deferred writer support.
 * Before idle admission the adapter must hold this exact native source and
 * its metadata, and defer its writer context BEFORE native file creation. All
 * three remain valid until handoff or externally proven cleanup. Busy frames
 * are not admitted, modified, held, or queued here: stock writes them as RAW.
 * A deferred selected frame must retain its original file identity even while
 * later RAW files pass; a filename counter reconstructed at completion fails
 * this contract. No current native adapter is supplied by these files. */

/* Fresh zeroed storage; session is nonzero and not reused while any old
 * notification can survive. Never reset live/retained storage to recover. */
uint32_t fpl_pipeline_init(struct fpl_pipeline *, uint32_t session);
/* Called only after the existing recorder/control readiness gate succeeded
 * (fpl_binding_begin for UI, fpl_begin_direct for a separate no-menu build).
 * Snapshot RAW/OFF or LOSSLESS/ON;
 * do not construct a synthetic fpl_state to bypass that gate. The take number
 * is generated here; no per-take frame-count limit exists. RAW takes never
 * acquire a codec lease. Existing fpl_end still requires all recorder cleanup. */
uint32_t fpl_pipeline_begin(struct fpl_pipeline *, const struct fpl_state *);
/* frame tokens use the current session/take and increasing nonzero frame IDs.
 * hold_proven/deferred_proven are adapter facts, NOT preferences. On refusal,
 * ownership is not adopted: the caller still owns any hold it acquired.
 * RAW is returned before looking at incoming pointers or proof flags when a
 * job is outstanding. Caller must not pre-acquire a hold for that busy case. */
uint32_t fpl_pipeline_arrive(struct fpl_pipeline *, const struct fpl_frame_lease *,
                             uint32_t hold_proven, uint32_t deferred_proven,
                             uint32_t *action);
/* A non-OK start may have armed DMA. It retains the whole lease, stops new
 * admission, and requires cleanup proof; it is never treated as busy RAW. */
uint32_t fpl_pipeline_submitted(struct fpl_pipeline *, const struct fpl_frame_token *,
                                uint32_t start_result);
/* A callback/status alone does not prove DMA stopped. Without codec_quiescent
 * the slot stays ENCODING. validated means the adapter checked codec success,
 * every tile, source-tail coverage, separate codec output bounds, same-source
 * DNG metadata, and that encoding left the original source unmodified. Those
 * checks are not provided by this policy core. A valid but non-smaller result
 * becomes READY_RAW: transfer the unchanged original instead of copy-back. */
uint32_t fpl_pipeline_complete(struct fpl_pipeline *, const struct fpl_frame_token *,
                               uint32_t codec_result, uint32_t codec_quiescent,
                               const struct fpl_frame_output *);

/* Called with the SAVED lease/output, never with a later arriving frame.
 * The descriptor pointers are borrowed only for this call: the adapter must
 * not retain &lease/&output storage, which is cleared and reused on success.
 * Adapter must copy the validated result into that exact native buffer and
 * transfer that exact deferred writer context, retaining its metadata/file ID.
 * NULL output means a valid encode had no size benefit: transfer this same
 * unchanged original frame, with NO compressed copy or metadata rewrite.
 * Only non-NULL output counts toward fpl_frame_done (compressed frames).
 * Set SOURCE only once native writer owns the source; OUTPUT only once no
 * native writer/DMA/callback borrows the codec output. SD need not be finished:
 * it owns the native frame after transfer, NOT our reusable codec output.
 * A partial/error outcome is not atomic: retain everything until cleanup. */
typedef uint32_t (*fpl_same_frame_handoff)(void *, const struct fpl_frame_lease *,
                                          const struct fpl_frame_output *,
                                          uint32_t *proofs);
uint32_t fpl_pipeline_handoff(struct fpl_pipeline *, const struct fpl_frame_token *,
                              fpl_same_frame_handoff, void *);
/* Stop is idempotent while active: no new selected frames, but an existing
 * job may still complete and hand off. Incoming drain-time frames stay RAW. */
uint32_t fpl_pipeline_stop(struct fpl_pipeline *);
/* Cancel only a never-submitted HELD lease. The adapter must serialize against
 * submit and establish native ownership transfer/cleanup before resolving it.
 * This cannot recover ENCODING or RETAINED; existing faults stay unchanged. */
uint32_t fpl_pipeline_cancel_held(struct fpl_pipeline *, const struct fpl_frame_token *,
                                  uint32_t ownership_resolved);
/* On failure the adapter must establish complete ownership resolution and
 * codec/IRQ/output quiescence, including a possibly partial handoff. This only
 * records that external cleanup; it does not free/return/write a buffer. */
uint32_t fpl_pipeline_reap(struct fpl_pipeline *, const struct fpl_frame_token *,
                           uint32_t quiescent, uint32_t ownership_resolved);
/* Not merely REC key-up. Only a free slot plus proven callback/codec drain
 * permits a new take. Faults remain sticky for lossless until an explicit
 * quiescent reboot; after proven cleanup OFF/RAW may resume stock recording. */
uint32_t fpl_pipeline_finish(struct fpl_pipeline *, uint32_t quiescent);

#endif
