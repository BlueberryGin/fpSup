#include "rec_workspace.h"

#if defined(FPL_REC_WORKSPACE_HOST_TEST)
extern uint32_t fpl_test_native_rec_prepare(uintptr_t, const uint32_t *);
#define native_prepare fpl_test_native_rec_prepare
#elif defined(__arm__) && UINTPTR_MAX == UINT32_MAX
static uint32_t native_prepare(uintptr_t camera, const uint32_t *request) {
    typedef uint32_t (*fn)(uintptr_t, const uint32_t *);
    return ((fn)0xc03a2438u)(camera, request);
}
#else
#error "ARM32 native REC ABI required; host tests must explicitly substitute it"
#endif

static void rec_zero(volatile unsigned char *p, uint32_t bytes) {
    while (bytes--) *p++ = 0;
}
static uint32_t rec_valid(const struct fpl_rec_workspace *r) {
    return r && r->magic == FPL_REC_WORKSPACE_MAGIC &&
           r->control.magic == FPL_MAGIC && r->control.abi == FPL_ABI &&
           !r->control.reserved0 && !r->control.reserved1 &&
           r->pipeline.magic == FPL_PIPELINE_MAGIC && r->pipeline.session;
}
static uint32_t same_format(const struct fpl_context *a, const struct fpl_context *b) {
    return a->firmware == b->firmware && a->cine == b->cine &&
           a->compression == b->compression && a->bits == b->bits &&
           a->width == b->width && a->height == b->height &&
           a->fps_num == b->fps_num && a->fps_den == b->fps_den &&
           a->media == b->media;
}
static uint32_t refuse(struct fpl_rec_workspace *r, uint32_t result) {
    r->last_result = result;
    return 0;
}
static uint32_t undo_reservation(struct fpl_rec_workspace *r, uint32_t take,
                                  uint32_t reason) {
    /* Only called before native dispatch and before any pipeline arrival.
     * Thus no codec/output user was started by this preparation. */
    uint32_t result = fpl_raw_workspace_release(&r->memory, take, 1);
    if (result != FPL_OK) return refuse(r, result);
    rec_zero((volatile unsigned char *)&r->layout, sizeof(r->layout));
    return refuse(r, reason);
}

uint32_t fpl_rec_workspace_init(struct fpl_rec_workspace *r, uint32_t session) {
    uint32_t result;
    if (!r || !session) return FPL_INVALID;
    /* Do not let init discard an allocation or a live nested object. */
    const unsigned char *bytes = (const unsigned char *)r;
    for (uint32_t n = 0; n < sizeof(*r); ++n)
        if (bytes[n]) return FPL_INVALID;
    fpl_boot(&r->control);
    result = fpl_pipeline_init(&r->pipeline, session);
    if (result != FPL_OK) return result;
    r->magic = FPL_REC_WORKSPACE_MAGIC;
    return FPL_OK;
}

/* The output span removed and every later span moved down over it. */
static void drop_output(struct fpl_workspace_layout *l) {
    uint32_t gap = l->output.capacity;
    struct fpl_workspace_span *later[] = { &l->codec_sizes, &l->codec_scratch, &l->metadata,
                                           &l->tile_offsets, &l->tile_counts };
    if (!gap) return;
    for (uint32_t i = 0; i < sizeof later / sizeof later[0]; ++i)
        if (later[i]->offset >= l->output.offset + gap) later[i]->offset -= gap;
    l->output.capacity = 0;
    l->total_bytes -= gap;
}

uint32_t fpl_rec_workspace_prepare(struct fpl_rec_workspace *r, uintptr_t camera,
                                   const uint32_t *request,
                                   const struct fpl_rec_workspace_setup *setup) {
    struct fpl_context before, after;
    struct fpl_state trial, saved;
    uint32_t result, take;
    if (!rec_valid(r)) return 0;
    r->native_result = 0;
    if (!camera || !request || !FPL_REC_IS_START(request[0]))
        return refuse(r, FPL_UNSUPPORTED);
    if (!setup || !setup->read || setup->copy_source > 1 || !setup->metadata_bytes)
        return refuse(r, FPL_NOT_READY);
    if (r->memory.fault) return refuse(r, FPL_FAULT);
    if (r->pipeline.active || r->pipeline.phase != FPL_SLOT_FREE ||
        r->control.clip != FPL_IDLE || r->memory.allocation.handle || r->memory.take)
        return refuse(r, FPL_BUSY);
    if (r->pipeline.fault || r->control.fault) return refuse(r, FPL_FAULT);
    if (r->pipeline.take == UINT32_MAX) return refuse(r, FPL_INVALID);

    /* Both cold (map changes) and same-map second REC pass through here.
     * C03A3398 must not dispatch event 3 until this returns true. */
    r->native_result = native_prepare(camera, request);
    if (!r->native_result) return refuse(r, FPL_NOT_READY);
    rec_zero((volatile unsigned char *)&before, sizeof(before));
    result = setup->read(setup->context, 0, &before);
    if (result != FPL_OK) return refuse(r, result);
    trial = r->control;
    result = fpl_begin_direct(&trial, &before);
    if (result != FPL_OK) return refuse(r, result);
    result = fpl_workspace_plan(&r->layout, before.width, before.height, before.bits,
                                setup->copy_source, setup->metadata_bytes);
    if (result != FPL_OK) return refuse(r, result);
    if (setup->no_output) drop_output(&r->layout);
    take = r->pipeline.take + 1;
    result = fpl_raw_workspace_reserve(&r->memory, take, r->layout.total_bytes);
    if (result != FPL_OK) return refuse(r, result);

    /* Re-read after reserving, not a cached admission made with an empty pool.
     * The real provider must withdraw REC readiness if source headroom is now
     * inadequate. Output allocation is NOT proof of a hardware DMA limit. */
    rec_zero((volatile unsigned char *)&after, sizeof(after));
    result = setup->read(setup->context, r->memory.allocation.capacity, &after);
    if (result != FPL_OK) return undo_reservation(r, take, result);
    if (!same_format(&before, &after)) return undo_reservation(r, take, FPL_NOT_READY);
    saved = r->control;
    result = fpl_begin_direct(&r->control, &after);
    if (result != FPL_OK) return undo_reservation(r, take, result);
    result = fpl_pipeline_begin(&r->pipeline, &r->control);
    if (result != FPL_OK) {
        r->control = saved;
        return undo_reservation(r, take, result);
    }
    r->last_result = FPL_OK;
    return 1;
}

uint32_t fpl_rec_workspace_stop(struct fpl_rec_workspace *r, uint32_t take) {
    if (!rec_valid(r) || !take || r->pipeline.take != take ||
        r->memory.take != take) return FPL_INVALID;
    r->last_result = fpl_pipeline_stop(&r->pipeline);
    return r->last_result;
}

uint32_t fpl_rec_workspace_finish(struct fpl_rec_workspace *r, uint32_t take,
                                  uint32_t quiescent) {
    uint32_t result;
    if (!rec_valid(r) || !take || r->memory.take != take) return FPL_INVALID;
    /* Failed prepare can leave a held reservation. A quarantined native
     * result still refuses release below; finish never clears that fault. */
    if (r->pipeline.active) {
        if (r->pipeline.take != take) return FPL_INVALID;
        result = fpl_pipeline_stop(&r->pipeline);
        if (result != FPL_OK) return result;
    }
    if (quiescent != 1 || r->pipeline.phase != FPL_SLOT_FREE) return FPL_BUSY;
    result = fpl_raw_workspace_release(&r->memory, take, quiescent);
    if (result != FPL_OK) { r->last_result = result; return result; }
    if (r->pipeline.active) {
        result = fpl_pipeline_finish(&r->pipeline, quiescent);
        if (result != FPL_OK) { r->last_result = result; return result; }
    }
    result = fpl_end(&r->control, quiescent);
    rec_zero((volatile unsigned char *)&r->layout, sizeof(r->layout));
    r->last_result = result;
    return result;
}
