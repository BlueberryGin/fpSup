#ifndef FPLOSSLESS_RECORD_DIAG_H
#define FPLOSSLESS_RECORD_DIAG_H
#include <stdint.h>

#define FPL_RECORD_DROP_SLOTS 16u
#define FPL_RECORD_NAME_BYTES 48u

/* These are reused registry slots, not consecutive DNG file numbers. The
 * native frame owns an inline 48-byte filename at +0x1030: C0376B98 copies
 * it there; C03A5348 passes its address to the writer. Keep the fixed bytes,
 * not a borrowed pointer. Host display must bound decoding to 48 bytes. */
struct fpl_record_drop {
    uint32_t slot, generation, caller;
    unsigned char name[FPL_RECORD_NAME_BYTES];
};

struct fpl_record_diag {
    uint32_t event_calls, raw_errors, capture_limits, completion_errors;
    uint32_t lookup_missing;
    uint32_t last_event, last_result, last_slot, last_generation;
    uint32_t last_state, last_handle, last_missing;
    uint32_t discard_calls, discard_count, discard_missing;
    uint32_t discard_raw, discard_completion, discard_teardown, discard_other;
    uint32_t drop_next, drop_used, drop_overwritten;
    struct fpl_record_drop drops[FPL_RECORD_DROP_SLOTS];
    uint32_t writer_calls, writer_zero, writer_nonzero, writer_low, writer_high;
};

/* Callers serialize updates/reset; these are bounded, allocation-free copies,
 * not an ownership or scheduling mechanism. Native pointers must be live at
 * the observed site. NULL is accepted; no pointer is retained. Error counts
 * classify nonzero results; lookup_missing separately counts absent frames.
 * Result 4 at event 5 is a capture limit, not a generic RAW error. */
void fpl_record_diag_reset(struct fpl_record_diag *);
void fpl_record_diag_event(struct fpl_record_diag *, uintptr_t msg, uintptr_t frame);
void fpl_record_diag_discard(struct fpl_record_diag *, uintptr_t frame,
                             uint32_t clear_caller);
/* The complete native return, NOT proof of durable media I/O. */
void fpl_record_diag_writer(struct fpl_record_diag *, uint32_t low, uint32_t high);

#if defined(FPL_RECORD_DIAG_HOST_TEST)
uint32_t fpl_record_diag_test_read32(uintptr_t);
unsigned char fpl_record_diag_test_read8(uintptr_t);
#endif
#endif
