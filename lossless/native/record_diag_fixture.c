/* Host-only bounded native-memory model. No camera transport is linked. */
#include <string.h>
#include "record_diag.h"

#define MSG_BASE ((uintptr_t)0x100000u)
#define FRAME_BASE ((uintptr_t)0x200000u)
static uint32_t message[0x100 / 4], frame[0x1120 / 4];
static struct fpl_record_diag diag;
static uint32_t reads32, reads8, bad_reads;

uint32_t fpl_record_diag_test_read32(uintptr_t a) {
    ++reads32;
    if (!(a & 3u) && a >= MSG_BASE && a - MSG_BASE <= sizeof(message) - 4)
        return message[(a - MSG_BASE) / 4];
    if (!(a & 3u) && a >= FRAME_BASE && a - FRAME_BASE <= sizeof(frame) - 4)
        return frame[(a - FRAME_BASE) / 4];
    ++bad_reads;
    return 0;
}
unsigned char fpl_record_diag_test_read8(uintptr_t a) {
    ++reads8;
    if (a >= FRAME_BASE && a - FRAME_BASE < sizeof(frame))
        return ((unsigned char *)frame)[a - FRAME_BASE];
    ++bad_reads;
    return 0;
}
void fpl_diag_fixture_reset(void) {
    memset(message, 0, sizeof(message));
    memset(frame, 0, sizeof(frame));
    reads32 = reads8 = bad_reads = 0;
    fpl_record_diag_reset(&diag);
}
struct fpl_record_diag *fpl_diag_fixture_state(void) { return &diag; }
uint32_t fpl_diag_fixture_size(void) { return sizeof(diag); }
void fpl_diag_fixture_message(uint32_t event, uint32_t result, uint32_t slot) {
    message[0x0c / 4] = event;
    message[0xe4 / 4] = result;
    message[0xe8 / 4] = slot;
}
void fpl_diag_fixture_frame(uint32_t slot, uint32_t generation,
                            uint32_t state, uint32_t handle) {
    frame[0x10fc / 4] = slot;
    frame[0x1100 / 4] = generation;
    frame[0x1104 / 4] = state;
    frame[0x68 / 4] = handle;
}
void fpl_diag_fixture_name(uint32_t at, uint32_t value) {
    if (at < FPL_RECORD_NAME_BYTES)
        ((unsigned char *)frame)[0x1030 + at] = (unsigned char)value;
}
/* mask: NULL diagnostics=1, NULL message=2, NULL frame=4. */
void fpl_diag_fixture_event(uint32_t mask) {
    fpl_record_diag_event(mask & 1u ? 0 : &diag,
                          mask & 2u ? 0 : MSG_BASE,
                          mask & 4u ? 0 : FRAME_BASE);
}
void fpl_diag_fixture_discard(uint32_t caller, uint32_t mask) {
    fpl_record_diag_discard(mask & 1u ? 0 : &diag,
                            mask & 4u ? 0 : FRAME_BASE, caller);
}
void fpl_diag_fixture_writer(uint32_t low, uint32_t high, uint32_t mask) {
    fpl_record_diag_writer(mask & 1u ? 0 : &diag, low, high);
}
void fpl_diag_fixture_null_reset(void) { fpl_record_diag_reset(0); }
uint32_t fpl_diag_fixture_reads(uint32_t which) {
    return which == 0 ? reads32 : which == 1 ? reads8 : bad_reads;
}
