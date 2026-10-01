/* Host-only synthetic producer/allocator. Never linked into a camera build. */
#include "rec_workspace.h"
static struct fpl_rec_workspace rec;
static struct fpl_rec_workspace_setup setup;
static uint32_t flags, mode, width, height, bits, gets, frees, calls, reads;
static uint32_t wanted, granted, order[32], order_count, bad_call;
static void event(uint32_t value) { if (order_count < 32) order[order_count++] = value; }

uint32_t fpl_test_native_rec_prepare(uintptr_t camera, const uint32_t *request) {
    event(1); ++calls;
    if (camera != 0x12340000 || (request[0] != 3 && request[0] != 0x23)) bad_call = 1;
    if (flags & 1) return 0;
    if (!(flags & 32)) mode = 3;
    return 1;
}
uint32_t fpl_raw_workspace_native_mode(void) { event(2); return mode; }
uint32_t fpl_raw_workspace_native_pool(uint32_t allocator_class) {
    event(3); if (allocator_class != 10) bad_call = 1;
    return 0x43000000;
}
uint32_t fpl_raw_workspace_native_get(uint32_t allocator, struct fpl_raw_descriptor *d,
                                     uint32_t bytes, uint32_t alignment, uint32_t caller) {
    event(4); ++gets; wanted = bytes;
    if (allocator != 0x43000000 || alignment != 1024 || caller) bad_call = 1;
    d->handle = (flags & 2) ? 0 : 0x55000000;
    d->capacity = d->handle ? bytes + 1024 : 0;
    d->allocator_class = 10;
    granted = d->capacity;
    return d->handle;
}
void fpl_raw_workspace_native_free(struct fpl_raw_descriptor *d) {
    event(7); ++frees;
    if (d->handle != 0x55000000 || d->allocator_class != 10 ||
        d->capacity != granted) bad_call = 1;
}
static uint32_t facts(void *context, uint32_t reserved, struct fpl_context *out) {
    if (context != &flags) bad_call = 1;
    ++reads; event(reserved ? 6 : 5);
    if ((flags & 16) && reserved) return FPL_NOT_READY;
    out->firmware = 502; out->cine = 1; out->compression = 1;
    out->width = width; out->height = height; out->bits = bits;
    out->fps_num = 24000; out->fps_den = 1001; out->media = 1;
    out->ready = FPL_READY_CAPTURE; /* EXPLICIT FIXTURE, not real native proof. */
    if (reserved) {
        if (reserved != granted || gets != frees + 1) bad_call = 1;
        if (flags & 4) out->ready &= ~FPL_READY_REC_GATE;
        if (flags & 8) out->width += 8;
    }
    return FPL_OK;
}
uint32_t fpl_fixture_reset(uint32_t w, uint32_t h, uint32_t b, uint32_t copy) {
    /* Only the test harness starts a fresh synthetic boot. */
    unsigned char *p = (unsigned char *)&rec;
    for (uint32_t n = 0; n < sizeof(rec); ++n) p[n] = 0;
    flags = mode = gets = frees = calls = reads = wanted = granted = order_count = bad_call = 0;
    width = w; height = h; bits = b;
    setup.read = facts; setup.context = &flags;
    setup.copy_source = copy & 1u; setup.metadata_bytes = 0x15400;
    setup.no_output = (copy >> 1) & 1u;         /* bit 1: no output span */
    return fpl_rec_workspace_init(&rec, 73);
}
void fpl_fixture_flags(uint32_t value) { flags = value; }
uint32_t fpl_fixture_prepare(uint32_t event_id) {
    uint32_t request[3] = {event_id, 0, 0};
    return fpl_rec_workspace_prepare(&rec, 0x12340000, request, &setup);
}
uint32_t fpl_fixture_missing_provider(void) {
    uint32_t request[3] = {3, 0, 0};
    return fpl_rec_workspace_prepare(&rec, 0x12340000, request, 0);
}
uint32_t fpl_fixture_finish(uint32_t take, uint32_t quiet) {
    return fpl_rec_workspace_finish(&rec, take, quiet);
}
uint32_t fpl_fixture_stop(uint32_t take) { return fpl_rec_workspace_stop(&rec, take); }
uint32_t fpl_fixture_reinit(void) { return fpl_rec_workspace_init(&rec, 74); }
uint32_t fpl_fixture_pending(void) {
    struct fpl_frame_lease lease = {{73, rec.pipeline.take, 1},
        0x57000000, 0x57002000, 0x57003000, 4096, 2048};
    uint32_t action;
    return fpl_pipeline_arrive(&rec.pipeline, &lease, 1, 1, &action);
}
uint32_t fpl_fixture_abort_pending(void) {
    struct fpl_frame_token token = {73, rec.pipeline.take, 1};
    if (fpl_pipeline_submitted(&rec.pipeline, &token, FPL_FAULT) != FPL_FAULT)
        return FPL_INVALID;
    return fpl_pipeline_reap(&rec.pipeline, &token, 1, 1);
}
uint32_t fpl_fixture_read(uint32_t field) {
    switch (field) {
    case 0: return rec.last_result; case 1: return gets; case 2: return frees;
    case 3: return calls; case 4: return wanted; case 5: return rec.memory.allocation.handle;
    case 6: return rec.pipeline.take; case 7: return rec.pipeline.active;
    case 8: return rec.control.clip; case 9: return rec.layout.output.capacity;
    case 10: return reads; case 11: return bad_call; case 12: return order_count;
    case 13: return rec.pipeline.fault;
    case 14: return rec.layout.total_bytes;
    case 15: return rec.layout.output.offset;
    case 16: return rec.layout.codec_sizes.offset; case 17: return rec.layout.codec_scratch.offset;
    case 18: return rec.layout.metadata.offset; case 19: return rec.layout.tile_offsets.offset;
    case 20: return rec.layout.tile_counts.offset; case 21: return rec.layout.tile_counts.capacity;
    case 22: return rec.layout.codec_sizes.capacity; case 23: return rec.layout.metadata.capacity;
    default: return field >= 32 && field < 64 ? order[field - 32] : UINT32_MAX;
    }
}
