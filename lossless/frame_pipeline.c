#include "frame_pipeline.h"

static uint32_t valid(const struct fpl_pipeline *p) {
    return p && p->magic == FPL_PIPELINE_MAGIC && p->session &&
           p->active <= 1 && p->enabled <= 1 && p->stopping <= 1 &&
           p->phase <= FPL_SLOT_READY_RAW;
}
static void zero_bytes(volatile unsigned char *p, uint32_t n) {
    while (n--) *p++ = 0;
}
static void increment(uint32_t *value) {
    if (*value != UINT32_MAX) ++*value; /* statistics never stop a long take */
}
static uint32_t token_equal(const struct fpl_frame_token *a,
                            const struct fpl_frame_token *b) {
    return a && b && a->session == b->session && a->take == b->take &&
           a->frame == b->frame;
}
static uint32_t ours(const struct fpl_pipeline *p, const struct fpl_frame_token *t) {
    return valid(p) && p->active && p->phase != FPL_SLOT_FREE &&
           token_equal(t, &p->lease.token);
}
static uint32_t retain(struct fpl_pipeline *p, uint32_t fault) {
    if (!p->fault) p->fault = fault ? fault : FPL_FAULT;
    p->stopping = 1;
    p->phase = FPL_SLOT_RETAINED;
    return FPL_FAULT;
}
static void clear_slot(struct fpl_pipeline *p) {
    zero_bytes((volatile unsigned char *)&p->lease, sizeof(p->lease));
    zero_bytes((volatile unsigned char *)&p->output, sizeof(p->output));
    p->phase = FPL_SLOT_FREE;
}

uint32_t fpl_pipeline_init(struct fpl_pipeline *p, uint32_t session) {
    if (!p || p->magic || !session) return FPL_INVALID;
    zero_bytes((volatile unsigned char *)p, sizeof(*p));
    p->session = session;
    p->magic = FPL_PIPELINE_MAGIC;
    return FPL_OK;
}
uint32_t fpl_pipeline_begin(struct fpl_pipeline *p, const struct fpl_state *state) {
    if (!valid(p)) return FPL_INVALID;
    if (p->active || p->phase != FPL_SLOT_FREE) return FPL_BUSY;
    if (!state || state->magic != FPL_MAGIC || state->abi != FPL_ABI ||
        state->reserved0 || state->reserved1 || state->requested > 1 ||
        (state->clip != FPL_RAW && state->clip != FPL_LOSSLESS) ||
        state->requested != (state->clip == FPL_LOSSLESS) ||
        (state->clip == FPL_LOSSLESS && state->fault)) return FPL_INVALID;
    if (p->fault && state->clip == FPL_LOSSLESS) return FPL_FAULT;
    if (p->take == UINT32_MAX) return FPL_INVALID; /* never reuse a take token */
    ++p->take;
    p->active = 1;
    p->enabled = state->clip == FPL_LOSSLESS;
    p->stopping = 0;
    p->last_frame = 0;
    p->seen = p->passed = p->selected = p->handed_off = 0;
    return FPL_OK;
}
uint32_t fpl_pipeline_arrive(struct fpl_pipeline *p, const struct fpl_frame_lease *f,
                             uint32_t held, uint32_t deferred, uint32_t *action) {
    if (action) *action = FPL_FRAME_NONE;
    if (!valid(p) || !p->active || !f || !action ||
        f->token.session != p->session || f->token.take != p->take ||
        !f->token.frame || f->token.frame <= p->last_frame) return FPL_INVALID;
    /* These fields are the only part inspected in the busy/stop fast path. */
    if (!p->enabled || p->stopping || p->phase != FPL_SLOT_FREE) {
        p->last_frame = f->token.frame;
        increment(&p->seen); increment(&p->passed);
        *action = FPL_FRAME_RAW;
        return FPL_OK;
    }
    if (held != 1 || deferred != 1) return FPL_NOT_READY;
    if (!f->buffer || !f->metadata || !f->writer || !f->stock_file_bytes ||
        f->capacity < f->stock_file_bytes || f->capacity > UINTPTR_MAX - f->buffer)
        return FPL_INVALID;
    p->lease = *f;
    p->last_frame = f->token.frame;
    increment(&p->seen); increment(&p->selected);
    p->phase = FPL_SLOT_HELD;
    *action = FPL_FRAME_SELECTED;
    return FPL_OK;
}
uint32_t fpl_pipeline_submitted(struct fpl_pipeline *p, const struct fpl_frame_token *t,
                                uint32_t result) {
    if (!ours(p, t) || p->phase != FPL_SLOT_HELD) return FPL_INVALID;
    if (result != FPL_OK) return retain(p, result);
    p->phase = FPL_SLOT_ENCODING;
    return FPL_OK;
}
uint32_t fpl_pipeline_complete(struct fpl_pipeline *p, const struct fpl_frame_token *t,
                               uint32_t result, uint32_t quiescent,
                               const struct fpl_frame_output *out) {
    if (!ours(p, t) || p->phase != FPL_SLOT_ENCODING) return FPL_INVALID;
    if (quiescent != 1) return FPL_BUSY;
    if (result != FPL_OK) return retain(p, result);
    if (!out || !out->bytes || out->validated != 1 || !out->length ||
        out->length > UINTPTR_MAX - out->bytes || out->file_bytes < out->length)
        return retain(p, FPL_INVALID);
    p->output = *out;
    if (out->file_bytes >= p->lease.stock_file_bytes) {
        /* Ordinary incompressibility is not a codec fault. Separate output
         * may exceed native capacity: none of it will be copied into source. */
        p->phase = FPL_SLOT_READY_RAW;
        return FPL_OK;
    }
    if (out->file_bytes > p->lease.capacity) return retain(p, FPL_INVALID);
    p->phase = FPL_SLOT_READY;
    return FPL_OK;
}
uint32_t fpl_pipeline_handoff(struct fpl_pipeline *p, const struct fpl_frame_token *t,
                              fpl_same_frame_handoff handoff, void *context) {
    uint32_t result, proofs = 0;
    const struct fpl_frame_output *output;
    if (!ours(p, t) || (p->phase != FPL_SLOT_READY && p->phase != FPL_SLOT_READY_RAW) ||
        !handoff) return FPL_INVALID;
    output = p->phase == FPL_SLOT_READY_RAW ? 0 : &p->output;
    /* Native transfer may synchronously notify its callers. A nested handoff
     * must not copy/publish this file twice, even with one task executor. */
    p->phase = FPL_SLOT_COMMITTING;
    result = handoff(context, &p->lease, output, &proofs);
    if (result != FPL_OK || proofs != FPL_HANDOFF_ALL)
        return retain(p, result ? result : FPL_FAULT);
    increment(&p->handed_off);
    clear_slot(p);
    return FPL_OK;
}
uint32_t fpl_pipeline_stop(struct fpl_pipeline *p) {
    if (!valid(p) || !p->active) return FPL_INVALID;
    p->stopping = 1;
    return FPL_OK;
}
uint32_t fpl_pipeline_cancel_held(struct fpl_pipeline *p, const struct fpl_frame_token *t,
                                  uint32_t resolved) {
    if (!ours(p, t) || p->phase != FPL_SLOT_HELD) return FPL_INVALID;
    if (resolved != 1) return FPL_BUSY;
    clear_slot(p);
    return FPL_OK;
}
uint32_t fpl_pipeline_reap(struct fpl_pipeline *p, const struct fpl_frame_token *t,
                           uint32_t quiescent, uint32_t resolved) {
    if (!ours(p, t) || p->phase != FPL_SLOT_RETAINED) return FPL_INVALID;
    if (quiescent != 1 || resolved != 1) return FPL_BUSY;
    clear_slot(p);
    return FPL_OK;
}
uint32_t fpl_pipeline_finish(struct fpl_pipeline *p, uint32_t quiescent) {
    if (!valid(p) || !p->active || !p->stopping) return FPL_INVALID;
    if (p->phase != FPL_SLOT_FREE || quiescent != 1) return FPL_BUSY;
    p->active = 0;
    p->enabled = 0;
    p->stopping = 0;
    return FPL_OK;
}
