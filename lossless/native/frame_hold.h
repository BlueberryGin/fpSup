#ifndef FPLOSSLESS_FRAME_HOLD_H
#define FPLOSSLESS_FRAME_HOLD_H
#include "../frame_pipeline.h"
#include "codec_job.h"
#include "producer_facts.h"

#define FPL_HOLD_MAGIC 0x444c4f48u
/* A compressed frame's file MUST get its trailer and length at the flush, or
 * it is written as garbage. A frame is therefore only ever held when a slot
 * is free to carry that promise. The flush comes when the SD aggregation
 * batch (64 MiB) is written, so the slots must outnumber the compressed frames
 * one batch can hold: on the camera 16 filled before the first flush and 13
 * frames were turned away (2026-09-30). At ~0.9 MB a compressed FHD frame, a
 * batch holds about 70; 128 also matches the aggregation's own cookie cap.
 * Host tests build with fewer so that filling them needs few buffers. */
#ifndef FPL_HOLD_COMMITS
#define FPL_HOLD_COMMITS 128u
#endif

/* The same-frame adapter at the CinemaDNG creator's enqueue.
 *
 * C038BD98 hands every arriving frame to the creator with
 * creator->vtable[0x10](creator, id, 1) at C038BFF0 -- C037DD50. The first
 * thing C037DD50 does is look the frame up and, for kind 1, write 0xB ("pending")
 * to frame+0x1104; only after that does it touch the creator's active slot or
 * FIFO. HOLDING a frame is exactly that first part and nothing more. Native
 * stop waits for 0xB (C0376300 classifies it "not finished"), and the stop
 * cleanup C03752A0 leaves it alone: executed from the image in
 * test_native_defer_contract.py. RELEASING it is the whole original call,
 * which re-marks it and queues it; its file is its own, because the file is
 * created from the frame at dispatch, after that.
 *
 * Arrival:  finish the previous job if the engine is done (copy back into
 *           ITS frame, queue ITS frame), then hold this frame and start the
 *           engine on it -- or, if the engine is still busy, pass this frame
 *           to the original untouched.
 * Stop:     wait for the job (the firmware's own encoder waits too), finish
 *           it, queue the frame; native stop then sees no 0xB and completes.
 *
 * The file length and the trailing IFD are applied later, where they were
 * proven on 2026-09-29: the writer's flush (C03A5490). This adapter only
 * publishes, per committed frame, what that site needs -- a single-producer
 * single-consumer ring, because the flush runs on the writer's task.
 */

/* One slot per committed frame. `file` is written LAST by the producer
 * (non-zero = published) and cleared LAST by the consumer (zero = free), each
 * behind a barrier: a per-slot handoff, so one frame that never reaches the
 * flush cannot hold up any other frame's. */
struct fpl_hold_commit {
    volatile uintptr_t file;            /* the frame's file buffer (seg[0]) */
    uint32_t payload, tiles;            /* padded payload bytes after 0x13400 */
    uint32_t capacity, stock_bytes;     /* the file buffer's; the stock length */
    uint32_t tile_bytes[FPL_CODEC_TILE_MAX];   /* host order */
};

struct fpl_hold_workspace {
    uintptr_t output, table;            /* 1 KiB aligned, ours */
    uint32_t output_capacity, table_capacity;
    /* ZERO COPY by buffer swap (2026-09-30). The in-place experiment
     * corrupted the top tile row: the engine writes ahead of what it has
     * read. Instead the engine reads the frame's raster A and writes into a
     * spare allocation S at the same offset; at release the frame's native
     * allocation record is swapped to S, so the firmware writes S as the
     * file and frees S, and A becomes the next frame's spare. The record is
     * the one C037A990 fills and C0374180 frees -- class 10 from C001CFD8,
     * the same allocator and class as S -- so the firmware cannot tell them
     * apart. Only the 0x15400 prefix (the header the firmware DMA'd into A
     * before the enqueue, C038C5F8 -> C00FBB90) is copied. {0,0,0}: no
     * spare, every frame takes the copy-back into its own raster. */
    struct { uintptr_t handle; uint32_t capacity, allocator_class; } spare;
};

/* A lane: one held frame at a time, run by the codec task (see frame_hold.c,
 * "lanes"). Two lanes let the next frame wait, already held, while the engine
 * works, so the task can start it the moment the engine is done. */
enum fpl_hold_lane {
    FPL_LANE_IDLE = 0, FPL_LANE_HELD = 1, FPL_LANE_RUNNING = 2,
    FPL_LANE_FINISHED = 3, FPL_LANE_FAULT = 4, FPL_LANE_COLLECTING = 5
};

struct fpl_frame_hold {
    uint32_t magic;
    struct fpl_pipeline *pipeline;
    const struct fpl_producer_facts *facts;
    struct fpl_hold_workspace workspace;
    struct fpl_codec_job job;
    uint32_t next_frame;
    /* the held frame's native identity */
    uintptr_t creator, frame;
    uint32_t native_id;
    struct fpl_frame_token token;
    /* flush-site slots: published here, consumed on the writer's task */
    struct fpl_hold_commit ring[FPL_HOLD_COMMITS];
    /* diagnostics, all saturating */
    uint32_t arrivals, held, passed, compressed, refused, no_benefit;
    uint32_t not_eligible, drained, faults, last_result;
    uint32_t direct;                    /* frames the engine read in place */
    uint32_t swapping;                  /* the held frame's output is in the spare */
    uint32_t swapped, swap_declined, swap_undone;  /* released by swap; held
                                           without a spare that fits; spare
                                           result copied back after all */
    /* Which check turned a frame away, by the reasons below. The first live
     * compressed take (2026-09-30) found all 133 frames ineligible and no
     * way to say why; these say why. */
    uint32_t refused_by[8];
    /* lane mode only */
    volatile uint32_t lane;
    uint32_t lane_result, lane_failed, abandoned;
    uint32_t order;                     /* arrival order among the lanes */
    uint32_t dma_failed;                /* header DMA refused: CPU copy used */
    uint32_t lanes_full;                /* lane 0 only: a frame passed because
                                           every lane was busy */
    struct fpl_codec_input lane_in;
};
enum fpl_hold_refusal {
    FPL_HOLD_R_DESCRIPTOR = 0, FPL_HOLD_R_RESERVE = 1, FPL_HOLD_R_HANDLE = 2,
    FPL_HOLD_R_RASTER = 3, FPL_HOLD_R_SHORT = 4, FPL_HOLD_R_OUTPUT = 5,
    FPL_HOLD_R_SLOT = 6
};

/* Fresh storage; pipeline/facts must outlive it; workspace is the output and
 * table spans of the take's reserved RAW workspace. */
#if defined(FPL_FRAME_HOLD_HOST_TEST)
/* Host tests substitute every firmware call and memory access through this. */
struct fpl_hold_natives {
    uintptr_t (*frame)(uint32_t id);                       /* C0374220 + C03753C0 */
    uint32_t (*enqueue)(uintptr_t creator, uint32_t id, uint32_t argument);
    uint32_t (*read)(uintptr_t);
    void (*write)(uintptr_t, uint32_t);
    uintptr_t (*uncached)(uintptr_t);
    void (*barrier)(void);
    uint32_t (*irq_off)(void);
    void (*irq_restore)(uint32_t);
    void (*sleep_ms)(uint32_t);
    uint32_t (*dma)(uintptr_t to, uintptr_t from, uint32_t bytes);
};
extern const struct fpl_hold_natives fpl_hold_test_natives;
#endif

uint32_t fpl_hold_init(struct fpl_frame_hold *, struct fpl_pipeline *,
                       const struct fpl_producer_facts *,
                       const struct fpl_hold_workspace *);

/* The hook body at C038BFF0. Returns what the original returned when it ran,
 * 1 when this call held the frame instead; C038BD98 ignores the value. */
uint32_t fpl_hold_arrive(struct fpl_frame_hold *, uintptr_t creator,
                         uint32_t native_id, uint32_t argument);

/* Before native stop's check (cDevt_stop -> C0398D88). Idempotent. */
uint32_t fpl_hold_stop(struct fpl_frame_hold *);

/* Lane mode: the same hold, driven from two sides. A caller uses either
 * fpl_hold_arrive/fpl_hold_stop, or these, never both on one hold.
 *   take     arrival: hold the frame if the lane is IDLE and the frame may be
 *            held; 1 = held. 0 = not held and NOT queued: the caller queues it.
 *   kick     codec task: start the HELD frame on the engine. 1 = started.
 *            The caller makes sure no other lane is RUNNING.
 *   check    codec task: RUNNING and the engine done -> FINISHED. 1 = moved.
 *   collect  arrival or stop: FINISHED -> the frame is released as
 *            fpl_hold_arrive would release it -> IDLE (FAULT if the engine's
 *            state is unknown). FPL_BUSY if nothing was finished.
 *   abandon  stop, after waiting: a HELD frame goes back whole; a RUNNING
 *            one goes back as it was and the lane ends in FAULT.
 * fpl_pipeline_stop() is the caller's, as is waiting. */
uint32_t fpl_hold_take(struct fpl_frame_hold *, uintptr_t creator,
                       uint32_t native_id, uint32_t argument);
uint32_t fpl_hold_kick(struct fpl_frame_hold *);
uint32_t fpl_hold_check(struct fpl_frame_hold *);
uint32_t fpl_hold_collect(struct fpl_frame_hold *);
uint32_t fpl_hold_abandon(struct fpl_frame_hold *);

/* Two lanes and one engine, as the card runs them. lane[1] may be invalid
 * (never initialised): then lane[0] runs alone, through the task.
 *   arrive  the creator's enqueue: collect finished lanes oldest first, then
 *           hold this frame in an IDLE lane, or give it to the original.
 *   task    one pass of the codec task: check RUNNING lanes; if none runs,
 *           kick the oldest HELD one. Bit 0: a job finished; bit 1: started.
 *   stop    stop both pipelines, wait (1 ms sleeps, bounded) for the task to
 *           run everything held, collect it, then abandon what is left. */
uint32_t fpl_lanes_arrive(struct fpl_frame_hold *const lane[2], uintptr_t creator,
                          uint32_t native_id, uint32_t argument);
uint32_t fpl_lanes_task(struct fpl_frame_hold *const lane[2]);
uint32_t fpl_lanes_stop(struct fpl_frame_hold *const lane[2]);

/* Writer's task, at the flush: the commit for this file buffer, or NULL.
 * The entry stays valid until fpl_hold_consumed() is called with it. */
const struct fpl_hold_commit *fpl_hold_peek(struct fpl_frame_hold *, uintptr_t file);
void fpl_hold_consumed(struct fpl_frame_hold *, const struct fpl_hold_commit *);
#endif
