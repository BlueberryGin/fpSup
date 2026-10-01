/* The TEST card's resident logic: four hook bodies and the take lifecycle.
 *
 * Built only into the direct-compression test card (§12, §16). It is the one
 * place that turns the proven modules into something a camera runs:
 *
 *   rec    C03A33C8  REC preparation: reserve the take's RAW workspace and
 *                    start a lossless take, then set the frame adapter up.
 *   arrive C038BFF0  every CinemaDNG frame reaching the creator.
 *   stop   C0398D88  (entry) every native stop check: finish the held frame,
 *                    then end the take and give the workspace back.
 *   flush  C03A5490  the writer's final flush: trailer and length.
 *
 * TEST-CARD POLICY, deliberately different from the product contract:
 *
 *   * Lossless RAW is chosen on the camera: a row under Audio Recording on
 *     SHOOT page 2 (CINE), menu_page.c. It lives in memory only and is OFF
 *     at every boot; while it is OFF every take is exactly stock, and so is
 *     every take if the row could not be installed.
 *   * It NEVER refuses a recording. If lossless cannot be prepared -- another
 *     format, no memory, anything -- the take records RAW exactly as stock,
 *     provided the firmware itself agreed to record. The product rule "no
 *     silent RAW when the user chose ON" is about a menu promise; this card
 *     makes no promise and says TEST on its banner.
 *   * Readiness is ASSERTED here, not proven: codec, writer, header,
 *     playback, storage and REC-gate proofs do not all exist yet (in-camera
 *     playback of tiled Compression=7 cannot exist at all). This card is how
 *     the remaining ones get measured. The assertion lives in test_readiness()
 *     and nowhere else.
 *   * CINE, CinemaDNG and SD are asserted the same way. A MOV take never
 *     reaches the kind-1 creator, and its REC preparation fails the mode-3
 *     reservation, so it simply records as stock.
 */
#include "rec_hook.h"
#include "frame_hold.h"
#include "flush_site.h"
#include "menu_page.h"

#define FPL_CARD_MAGIC 0x44524143u               /* "CARD" */
#define USED __attribute__((used))

#if defined(__arm__) && UINTPTR_MAX == UINT32_MAX
static uint32_t card_original_prepare(uintptr_t camera, const uint32_t *request) {
    typedef uint32_t (*fn)(uintptr_t, const uint32_t *);
    return ((fn)0xc03a2438u)(camera, request);
}
static uint32_t card_original_enqueue(uintptr_t creator, uint32_t id, uint32_t argument) {
    typedef uint32_t (*fn)(uintptr_t, uint32_t, uint32_t);
    return ((fn)0xc037dd50u)(creator, id, argument);
}
/* The frames' own allocator: C037A990 gets every kind-1 frame buffer with
 * C001CFD8(C001CF78(10), &frame[0x68], bytes, 0x400, 0) and C0374180 frees
 * it with C001D3D0. The spare comes from the same place, or the firmware
 * could not free it once it is a frame's. */
static void card_get_spare(void *record, uint32_t bytes) {
    typedef uintptr_t (*pool_fn)(uint32_t);
    typedef uintptr_t (*get_fn)(uintptr_t, void *, uint32_t, uint32_t, uint32_t);
    ((get_fn)0xc001cfd8u)(((pool_fn)0xc001cf78u)(10), record, bytes, 0x400, 0);
}
static void card_free_spare(void *record) {
    typedef void (*fn)(void *);
    ((fn)0xc001d3d0u)(record);
}
/* tk_cre_tsk C0016A58 + tk_sta_tsk C0016BC0, as fp_usb_shell/templates/
 * taskcreate.S: {exinf, tskatr TA_HLNG|TA_DSNAME, entry, itskpri, stksz,
 * dsname[8], 0}. Word stores: an initialised struct would be .rodata. */
static int32_t card_task_create(uintptr_t entry, uint32_t priority) {
    typedef int32_t (*cre_fn)(volatile uint32_t *);
    typedef int32_t (*sta_fn)(int32_t, uint32_t);
    volatile uint32_t d[8];
    int32_t id;
    d[0] = 0;
    d[1] = 0x41u;
    d[2] = (uint32_t)entry;
    d[3] = priority;
    d[4] = 0x2000u;
    d[5] = 0x434c5046u;                          /* "FPLC" */
    d[6] = 0x0045444fu;                          /* "ODE\0" */
    d[7] = 0;
    id = ((cre_fn)0xc0016a58u)(d);
    if (id < 1) return id;
    return ((sta_fn)0xc0016bc0u)(id, 0) == 0 ? id : -1;
}
static void card_sleep_ms(uint32_t n) {          /* C03705D8 -> tk_dly_tsk */
    typedef void (*fn)(uint32_t);
    ((fn)0xc03705d8u)(n);
}
#elif defined(FPL_CARD_HOST_TEST)
extern uint32_t fpl_test_card_prepare(uintptr_t, const uint32_t *);
extern uint32_t fpl_test_card_enqueue(uintptr_t, uint32_t, uint32_t);
extern void fpl_test_card_get_spare(void *, uint32_t);
extern void fpl_test_card_free_spare(void *);
extern int32_t fpl_test_card_task_create(uintptr_t, uint32_t);
extern void fpl_test_card_sleep(uint32_t);
#define card_task_create fpl_test_card_task_create
#define card_sleep_ms fpl_test_card_sleep
#define card_original_prepare fpl_test_card_prepare
#define card_original_enqueue fpl_test_card_enqueue
#define card_get_spare fpl_test_card_get_spare
#define card_free_spare fpl_test_card_free_spare
#else
#error "ARM32 native card ABI required; host tests must explicitly substitute it"
#endif

struct fpl_card {
    uint32_t magic, block_bytes;
    uintptr_t base;
    struct fpl_rec_hook rec;
    struct fpl_frame_hold hold;
    /* The second lane (frame_hold.c, "lanes"): its own pipeline, spare and
     * table. lane[1] stays invalid when any of them cannot be had, and the
     * card runs on one lane. */
    struct fpl_pipeline pipe_b;
    struct fpl_frame_hold hold_b;
    struct { uintptr_t handle; uint32_t capacity, allocator_class; } table_b;
    struct fpl_frame_hold *lane[2];
    struct fpl_flush_stats flush;
    uint32_t hold_live;
    /* read over USB after a test take; all saturating */
    uint32_t rec_events, rec_admitted, rec_raw, rec_firmware_refused;
    uint32_t rec_hold_refused, hold_init_failed, ring_busy;
    uint32_t stops, finishes, finish_busy, last_finish;
    /* the take's spare for the buffer swap: asked, granted, given back */
    uint32_t spare_bytes, spare_failed, spares_freed;
    uint32_t stale_promises;            /* forgotten at a new take, see start_hold */
    uintptr_t task_entry;               /* card.S task_shim */
    int32_t task_id;                    /* the codec task; < 1: none, and the
                                           card holds frames the old way */
    uint32_t lane_b_failed, lane_stop_result;
    /* SHOOT 2 (CINE) Lossless RAW row: memory only, OFF at every boot */
    struct fpl_menu menu;
    uintptr_t menu_area;
    uint32_t menu_area_bytes, rec_menu_off;
};

static void card_saturate(uint32_t *v) { if (*v != UINT32_MAX) ++*v; }

/* ---- the test card's assertions: the only place they are made -------- */
static uint32_t test_yes(void *c) { (void)c; return 1; }
static uint32_t test_readiness(void *c, uint32_t reserved) {
    (void)c; (void)reserved;
    return FPL_READY_CAPTURE;       /* ASSERTED for measurement, not proven */
}

static uint32_t card_valid(const struct fpl_card *c) {
    return c && c->magic == FPL_CARD_MAGIC;
}
static uint32_t ring_empty(const struct fpl_card *c) {
    for (uint32_t n = 0; n < FPL_HOLD_COMMITS; ++n)
        if (c->hold.ring[n].file || c->hold_b.ring[n].file) return 0;
    return 1;
}

USED uint32_t fpl_card_init(struct fpl_card *c, uintptr_t base, uint32_t block_bytes,
                           uintptr_t task_entry) {
    uint32_t result;
    /* the whole state inside the block the launcher was given */
    if (!c || !base || (uintptr_t)c < base || (uintptr_t)c - base > block_bytes ||
        block_bytes - ((uintptr_t)c - base) < sizeof(*c) || ((uintptr_t)c & 7u))
        return FPL_INVALID;
    const unsigned char *bytes = (const unsigned char *)c;
    for (uint32_t n = 0; n < sizeof(*c); ++n)
        if (bytes[n]) return FPL_INVALID;      /* the launcher zeroes it */
    if ((result = fpl_rec_hook_init(&c->rec, 1, 0x15400u)) != FPL_OK) return result;
    c->rec.facts.cine = test_yes;
    c->rec.facts.cinemadng = test_yes;
    c->rec.facts.sd_media = test_yes;
    c->rec.facts.readiness = test_readiness;
    /* No source copy: the engine reads each frame in place (operator
     * decision 2026-09-30; see frame_hold.c readable()). */
    c->rec.setup.copy_source = 0;
    /* Nor an output span: the engine writes into the spare frame allocation
     * (buffer swap). A frame-sized output nobody uses took the frames' memory
     * and stopped a 3K take (2026-10-01, rec log "mem"). A frame the swap
     * cannot take goes out as it is. */
    c->rec.setup.no_output = 1;
    c->base = base;
    c->block_bytes = block_bytes;
    c->task_entry = task_entry;      /* card.S task_shim: the codec task */
    c->lane[0] = &c->hold;
    c->lane[1] = &c->hold_b;
    if (fpl_pipeline_init(&c->pipe_b, 2) != FPL_OK) card_saturate(&c->lane_b_failed);
    c->magic = FPL_CARD_MAGIC;
    /* Priority 12: above SRecRaw (15), where frames arrive, below SRecFile
     * (6); it sleeps a millisecond between passes. Without it the card holds
     * frames the old way, one at a time from the arrival. */
    c->task_id = task_entry ? card_task_create(task_entry, 12) : -1;
    /* The row. The rest of the block, never freed: the UI borrows the page
     * and pool for the whole boot. Whatever the outcome, recording is safe:
     * without an installed row fpl_menu_on() is 0 and every take is stock. */
    c->menu_area = ((uintptr_t)c + sizeof(*c) + 7u) & ~(uintptr_t)7u;
    c->menu_area_bytes = c->menu_area < base + block_bytes ?
                         (uint32_t)(base + block_bytes - c->menu_area) : 0;
    fpl_menu_install(&c->menu, c->menu_area, c->menu_area_bytes);
    return FPL_OK;
}

/* The second lane: a spare like the first, and a table of its own (the
 * engine writes its size table there while the other lane's is read). Any
 * failure leaves it invalid; lane 0 then runs alone. */
static void start_lane_b(struct fpl_card *c, const struct fpl_hold_workspace *a) {
    struct fpl_hold_workspace ws;
    ws.output = 0;
    ws.output_capacity = 0;
    ws.table_capacity = a->table_capacity;
    c->table_b.handle = c->table_b.capacity = c->table_b.allocator_class = 0;
    card_get_spare(&c->table_b, ws.table_capacity);
    ws.spare.handle = ws.spare.capacity = ws.spare.allocator_class = 0;
    card_get_spare(&ws.spare, c->spare_bytes);
    ws.table = c->table_b.handle;
    if (!c->table_b.handle || (c->table_b.handle & 0x3ffu) ||
        c->table_b.capacity < ws.table_capacity ||
        !ws.spare.handle || ws.spare.allocator_class != 10u ||
        ws.spare.capacity < c->spare_bytes ||
        fpl_pipeline_begin(&c->pipe_b, &c->rec.workspace.control) != FPL_OK) {
        if (ws.spare.handle) card_free_spare(&ws.spare);
        if (c->table_b.handle) card_free_spare(&c->table_b);
        c->table_b.handle = 0;
        card_saturate(&c->lane_b_failed);
        return;
    }
    if (fpl_hold_init(&c->hold_b, &c->pipe_b, &c->rec.facts, &ws) != FPL_OK) {
        /* the pipeline began: end it, it never held anything */
        fpl_pipeline_stop(&c->pipe_b);
        fpl_pipeline_finish(&c->pipe_b, 1);
        card_free_spare(&ws.spare);
        card_free_spare(&c->table_b);
        c->table_b.handle = 0;
        card_saturate(&c->lane_b_failed);
    }
}

static void start_hold(struct fpl_card *c) {
    struct fpl_rec_workspace *w = &c->rec.workspace;
    struct fpl_hold_workspace ws;
    uintptr_t handle = w->memory.allocation.handle;
    unsigned char *h = (unsigned char *)&c->hold;

    /* Promises the last take left: frames the firmware dropped (a take that
     * stopped itself drops what is still queued, 2026-10-01: 8 left at 3K).
     * Their files will never reach the flush: REC cannot start before the
     * last take's files are written. Forget them, count them. */
    if (!ring_empty(c)) {
        for (uint32_t n = 0; n < FPL_HOLD_COMMITS; ++n) {
            if (c->hold.ring[n].file) card_saturate(&c->stale_promises);
            if (c->hold_b.ring[n].file) card_saturate(&c->stale_promises);
        }
        card_saturate(&c->ring_busy);
    }
    for (uint32_t n = 0; n < sizeof(c->hold); ++n) h[n] = 0;
    h = (unsigned char *)&c->hold_b;
    for (uint32_t n = 0; n < sizeof(c->hold_b); ++n) h[n] = 0;
    ws.output = w->layout.output.capacity ? handle + w->layout.output.offset : 0;
    ws.output_capacity = w->layout.output.capacity;
    ws.table = handle + w->layout.codec_sizes.offset;
    ws.table_capacity = w->layout.codec_sizes.capacity + w->layout.codec_scratch.capacity;
    /* One frame's worth, exactly what C037A990 asks for: the header reserve
     * plus the raster in 512 B units. Without it, every result is copied
     * back into its own frame. */
    ws.spare.handle = ws.spare.capacity = ws.spare.allocator_class = 0;
    c->spare_bytes = 0x15400u + ((c->rec.facts.seen.raster + 0x1ffu) & ~0x1ffu);
    card_get_spare(&ws.spare, c->spare_bytes);
    if (!ws.spare.handle || ws.spare.allocator_class != 10u ||
        ws.spare.capacity < c->spare_bytes) {
        if (ws.spare.handle) card_free_spare(&ws.spare);
        ws.spare.handle = ws.spare.capacity = ws.spare.allocator_class = 0;
        card_saturate(&c->spare_failed);
    }
    if (fpl_hold_init(&c->hold, &w->pipeline, &c->rec.facts, &ws) != FPL_OK) {
        if (ws.spare.handle) card_free_spare(&ws.spare);
        card_saturate(&c->hold_init_failed);
        return;
    }
    if (c->task_id > 0 && ws.spare.handle) start_lane_b(c, &ws);
    c->hold_live = 1;
}

/* The engine is idle (the take finished): the spare -- by now some earlier
 * frame's buffer -- goes back to the pool. */
static void release_spare(struct fpl_card *c) {
    struct fpl_frame_hold *h[2] = {&c->hold, &c->hold_b};
    for (uint32_t n = 0; n < 2; ++n) {
        if (!h[n]->workspace.spare.handle) continue;
        card_free_spare(&h[n]->workspace.spare);
        h[n]->workspace.spare.handle = 0;
        h[n]->workspace.spare.capacity = 0;
        h[n]->workspace.spare.allocator_class = 0;
        card_saturate(&c->spares_freed);
    }
    if (c->table_b.handle) {
        card_free_spare(&c->table_b);
        c->table_b.handle = 0;
    }
}

USED uint32_t fpl_card_rec(uintptr_t camera, const uint32_t *request, struct fpl_card *c) {
    uint32_t dispatch;
    if (!card_valid(c) || !request || !FPL_REC_IS_START(request[0]))
        return card_valid(c) ? fpl_rec_hook_call(camera, request, &c->rec)
                             : card_original_prepare(camera, request);
    card_saturate(&c->rec_events);
    if (!fpl_menu_on(&c->menu)) {
        /* Lossless RAW is OFF (the default): the take is exactly stock */
        card_saturate(&c->rec_menu_off);
        return card_original_prepare(camera, request);
    }
    if (c->hold_live) {
        /* a previous take never finished: record RAW, touch nothing */
        card_saturate(&c->rec_hold_refused);
        return card_original_prepare(camera, request);
    }
    dispatch = fpl_rec_hook_call(camera, request, &c->rec);
    if (dispatch) {
        card_saturate(&c->rec_admitted);
        start_hold(c);
        return dispatch;
    }
    if (c->rec.workspace.native_result) {
        /* We refused, the firmware agreed: record as stock. */
        card_saturate(&c->rec_raw);
        return c->rec.workspace.native_result;
    }
    card_saturate(&c->rec_firmware_refused);
    return 0;
}

USED uint32_t fpl_card_arrive(uintptr_t creator, uint32_t id, uint32_t argument,
                              struct fpl_card *c) {
    if (!card_valid(c) || !c->hold_live)
        return card_original_enqueue(creator, id, argument);
    return c->task_id > 0 ? fpl_lanes_arrive(c->lane, creator, id, argument)
                          : fpl_hold_arrive(&c->hold, creator, id, argument);
}

USED void fpl_card_stop(struct fpl_card *c, uint32_t kind) {
    struct fpl_rec_workspace *w;
    if (!card_valid(c) || !c->hold_live || (kind != 1 && kind != 9)) return;
    card_saturate(&c->stops);
    w = &c->rec.workspace;
    if (c->task_id > 0) c->lane_stop_result = fpl_lanes_stop(c->lane);
    else fpl_hold_stop(&c->hold);
    if (w->pipeline.phase != FPL_SLOT_FREE || c->hold.job.phase == FPL_CODEC_RUNNING ||
        c->hold.lane != FPL_LANE_IDLE ||
        (c->hold_b.magic && (c->pipe_b.phase != FPL_SLOT_FREE ||
                             c->hold_b.lane != FPL_LANE_IDLE))) {
        card_saturate(&c->finish_busy);          /* the engine still owns it */
        return;
    }
    if (c->hold_b.magic && c->pipe_b.active &&
        fpl_pipeline_finish(&c->pipe_b, 1) != FPL_OK) {
        card_saturate(&c->finish_busy);
        return;
    }
    c->last_finish = fpl_rec_workspace_finish(w, w->pipeline.take, 1);
    if (c->last_finish == FPL_OK) {
        release_spare(c);
        c->hold_live = 0;                        /* the adapter stays valid
                                                    until its promises flush */
        card_saturate(&c->finishes);
    } else {
        card_saturate(&c->finish_busy);
    }
}

USED void fpl_card_flush(uintptr_t writer, struct fpl_card *c) {
    if (!card_valid(c)) return;
    fpl_flush_before(writer, &c->hold, &c->hold_b, &c->flush);
}

/* The codec task (card.S task_shim). It never returns: a task cannot be
 * ended in this firmware. Between takes it only sleeps; during one it checks
 * the engine every millisecond and starts the waiting frame at once. */
USED void fpl_card_task(struct fpl_card *c) {
    for (;;) {
        uint32_t live = card_valid(c) && c->hold_live;
        if (live) fpl_lanes_task(c->lane);
        card_sleep_ms(live ? 1u : 10u);
#if defined(FPL_CARD_HOST_TEST)
        return;                                  /* one pass per call */
#endif
    }
}

USED uint32_t fpl_card_state_bytes(void) { return sizeof(struct fpl_card); }
