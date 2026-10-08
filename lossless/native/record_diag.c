#include "record_diag.h"

#if defined(FPL_RECORD_DIAG_HOST_TEST)
#define read32(a) fpl_record_diag_test_read32(a)
#define read8(a) fpl_record_diag_test_read8(a)
#elif defined(__arm__) && UINTPTR_MAX == UINT32_MAX
static uint32_t read32(uintptr_t a) { return *(volatile const uint32_t *)a; }
static unsigned char read8(uintptr_t a) { return *(volatile const unsigned char *)a; }
#else
#error "ARM32 native record diagnostics required; host tests must substitute reads"
#endif

static void saturate(uint32_t *v) { if (*v != UINT32_MAX) ++*v; }

void fpl_record_diag_reset(struct fpl_record_diag *d) {
    unsigned char *p = (unsigned char *)d;
    if (!d) return;
    for (uint32_t n = 0; n < sizeof(*d); ++n) p[n] = 0;
}

void fpl_record_diag_event(struct fpl_record_diag *d, uintptr_t msg, uintptr_t frame) {
    uint32_t event, result;
    if (!d || !msg) return;
    event = read32(msg + 0x0cu);
    if (event != 5 && event != 8 && event != 9) return;
    result = read32(msg + 0xe4u);
    saturate(&d->event_calls);
    if (!frame) saturate(&d->lookup_missing);
    if (event == 5) {
        if (result == 4) saturate(&d->capture_limits);
        else if (result) saturate(&d->raw_errors);
    } else if (result) {
        saturate(&d->completion_errors);
    }
    if (!result && frame) return;
    d->last_event = event;
    d->last_result = result;
    d->last_slot = read32(msg + 0xe8u);
    d->last_generation = frame ? read32(frame + 0x1100u) : UINT32_MAX;
    d->last_state = frame ? read32(frame + 0x1104u) : UINT32_MAX;
    d->last_handle = frame ? read32(frame + 0x68u) : 0;
    d->last_missing = frame == 0;
}

void fpl_record_diag_discard(struct fpl_record_diag *d, uintptr_t frame,
                             uint32_t clear_caller) {
    struct fpl_record_drop *drop;
    if (!d) return;
    saturate(&d->discard_calls);
    if (!frame) { saturate(&d->discard_missing); return; }
    saturate(&d->discard_count);
    if (clear_caller == 0xc038bf0cu) saturate(&d->discard_raw);
    else if (clear_caller == 0xc038c0a8u) saturate(&d->discard_completion);
    else if (clear_caller == 0xc0398d48u) saturate(&d->discard_teardown);
    else saturate(&d->discard_other);
    /* A fixed recent-history window, not a second frame queue. */
    drop = &d->drops[d->drop_next % FPL_RECORD_DROP_SLOTS];
    drop->slot = read32(frame + 0x10fcu);
    drop->generation = read32(frame + 0x1100u);
    drop->caller = clear_caller;
    for (uint32_t n = 0; n < FPL_RECORD_NAME_BYTES; ++n)
        drop->name[n] = read8(frame + 0x1030u + n);
    d->drop_next = (d->drop_next % FPL_RECORD_DROP_SLOTS + 1u) % FPL_RECORD_DROP_SLOTS;
    if (d->drop_used < FPL_RECORD_DROP_SLOTS) ++d->drop_used;
    else saturate(&d->drop_overwritten);
}

void fpl_record_diag_writer(struct fpl_record_diag *d, uint32_t low, uint32_t high) {
    if (!d) return;
    saturate(&d->writer_calls);
    if (low || high) saturate(&d->writer_nonzero);
    else saturate(&d->writer_zero);
    d->writer_low = low;
    d->writer_high = high;
}
