#include "geometry.h"

/* No floating point, allocator, C library calls, or compiler division runtime.
 * q <= 2^27, n <= 32, and x <= 1000 ms. Therefore sum(q) <= 2^32,
 * sum(x*q) <= 2^32*1000, |OLS numerator| <= 2^37*1000,
 * and denominator <= n*n*1000*1000/4 <= 256000000.
 * The largest rate numerator is |num|*n*1000 <= 4398046511104000000.
 * Fit/residual signed intermediates are bounded below 7*2^60 < INT64_MAX.
 * Fractional Q16 division generates bits instead of multiplying by 65536. */

#define FG_HALF_EPOCH 0x80000000u
#define FG_MAX_SCALE 131072u
#define FG_MIN_SCALE 64u
#define FG_MAX_SIGNED 2147483647u

static uint64_t divide_u64(uint64_t numerator, uint64_t denominator)
{
    uint64_t quotient = 0;
    uint32_t bit = 64;
    if (denominator == 0)
        return 0;
    while (bit != 0) {
        --bit;
        /* Only shift denominator after proving that the shift fits. */
        if (denominator <= (numerator >> bit)) {
            numerator -= denominator << bit;
            quotient |= (uint64_t)1u << bit;
        }
    }
    return quotient;
}

static uint64_t magnitude_i64(int64_t value)
{
    /* Also defined for INT64_MIN, although our numerical bounds exclude it. */
    return value < 0 ? (uint64_t)(-(value + 1)) + 1u : (uint64_t)value;
}

static uint32_t ratio_q16(uint64_t numerator, uint64_t denominator,
                          uint32_t limit)
{
    uint64_t whole;
    uint64_t remainder;
    uint64_t value;
    uint32_t fraction = 0;
    uint32_t bit;
    if (denominator == 0)
        return limit;
    whole = divide_u64(numerator, denominator);
    if (whole > (limit >> 16))
        return limit;
    remainder = numerator - whole * denominator;
    for (bit = 0; bit != 16; ++bit) {
        fraction <<= 1;
        /* 2*remainder without overflowing even for a large denominator. */
        if (remainder >= denominator - remainder) {
            remainder -= denominator - remainder;
            fraction |= 1u;
        } else {
            remainder <<= 1;
        }
    }
    value = (whole << 16) | fraction;
    return value > limit ? limit : (uint32_t)value;
}

static int forward(uint32_t newer, uint32_t older)
{
    uint32_t delta = newer - older;
    return delta != 0 && delta < FG_HALF_EPOCH;
}

static void copy_sample(FgSample *to, const FgSample *from)
{
    to->now_ms = from->now_ms;
    to->sample_ms = from->sample_ms;
    to->source_seq = from->source_seq;
    to->target_generation = from->target_generation;
    to->context_generation = from->context_generation;
    to->scale_generation = from->scale_generation;
    to->source_kind = from->source_kind;
    to->flags = from->flags;
    to->scale_q16 = from->scale_q16;
    to->lens_position = from->lens_position;
}

static int same_context(const FgSample *a, const FgSample *b)
{
    return a->target_generation == b->target_generation &&
           a->context_generation == b->context_generation &&
           a->scale_generation == b->scale_generation &&
           a->source_kind == b->source_kind;
}

static void clear_anchor(FgState *state)
{
    state->anchored = 0;
    state->count = 0;
    /* last is a timestamp/sequence high-water mark, not trend history.
     * Keep it and focus_event_seq across invalidation to reject replay. */
}

static void result_base(const FgState *state, uint32_t now_ms,
                        uint32_t reason, FgResult *result)
{
    if (result == 0)
        return;
    result->phase = FG_NO_ANCHOR;
    result->direction = FG_UNKNOWN;
    result->reason = reason;
    result->anchor_valid = 0;
    result->sample_count = 0;
    result->relative_rate_q16 = 0;
    result->residual_q16 = 0;
    result->confidence_permille = 0;
    result->anchor_age_ms = 0;
    result->anchor_position = 0;
    result->anchor_scale_q16 = 0;
    result->motion_authorized = 0;
    if (state != 0 && state->config_valid && state->anchored) {
        result->phase = FG_WARMUP;
        result->anchor_valid = 1;
        result->sample_count = state->count;
        result->anchor_age_ms = now_ms - state->anchor.sample_ms;
        result->anchor_position = state->anchor.lens_position;
        result->anchor_scale_q16 = state->anchor.scale_q16;
    }
}

void fg_default_config(FgConfig *config)
{
    if (config == 0)
        return;
    config->window_ms = 300;
    config->min_span_ms = 120;
    config->min_samples = 5;
    config->max_gap_ms = 150;
    config->max_age_ms = 100;
    config->max_anchor_age_ms = 5000;
    config->min_rate_q16 = 1311;
    config->max_residual_q16 = 1311;
    config->max_scale_step_q16 = 16384;
}

static int valid_config(const FgConfig *c)
{
    return c != 0 && c->window_ms >= 20 && c->window_ms <= 1000 &&
           c->min_span_ms > 0 && c->min_span_ms <= c->window_ms &&
           c->min_samples >= 3 && c->min_samples <= FG_CAPACITY &&
           c->max_gap_ms > 0 && c->max_gap_ms <= c->window_ms &&
           c->max_age_ms <= 1000 &&
           c->max_anchor_age_ms > c->window_ms &&
           c->max_anchor_age_ms <= 600000 &&
           c->min_rate_q16 > 0 && c->min_rate_q16 <= FG_Q16 &&
           c->max_residual_q16 > 0 && c->max_residual_q16 <= 16384 &&
           c->max_scale_step_q16 > 0 && c->max_scale_step_q16 <= 32768;
}

int fg_init(FgState *state, const FgConfig *config)
{
    if (state == 0)
        return 0;
    state->config_valid = 0;
    state->anchored = 0;
    state->has_focus_event = 0;
    state->focus_event_seq = 0;
    state->have_last = 0;
    state->count = 0;
    if (!valid_config(config))
        return 0;
    state->config.window_ms = config->window_ms;
    state->config.min_span_ms = config->min_span_ms;
    state->config.min_samples = config->min_samples;
    state->config.max_gap_ms = config->max_gap_ms;
    state->config.max_age_ms = config->max_age_ms;
    state->config.max_anchor_age_ms = config->max_anchor_age_ms;
    state->config.min_rate_q16 = config->min_rate_q16;
    state->config.max_residual_q16 = config->max_residual_q16;
    state->config.max_scale_step_q16 = config->max_scale_step_q16;
    state->config_valid = 1;
    return 1;
}

void fg_invalidate(FgState *state, uint32_t reason, FgResult *result)
{
    if (state == 0) {
        result_base(0, 0, FG_BAD_CONFIG, result);
        return;
    }
    clear_anchor(state);
    result_base(state, 0, state->config_valid ? reason : FG_BAD_CONFIG, result);
}

static uint32_t sample_reason(const FgState *state, const FgSample *sample)
{
    uint32_t age;
    uint32_t basic_flags = FG_SCALE_VALID | FG_IDENTITY_VALID | FG_TIME_VALID;
    if (sample == 0 || sample->target_generation == 0 ||
        sample->context_generation == 0 || sample->scale_generation == 0 ||
        (sample->source_kind != FG_FACE && sample->source_kind != FG_AAT) ||
        sample->scale_q16 < FG_MIN_SCALE || sample->scale_q16 > FG_MAX_SCALE ||
        (sample->flags & basic_flags) != basic_flags)
        return FG_BAD_SAMPLE;
    age = sample->now_ms - sample->sample_ms;
    if (age >= FG_HALF_EPOCH || age > state->config.max_age_ms)
        return FG_STALE;
    if ((sample->flags & FG_PROJECTION_STABLE) == 0 ||
        (sample->source_kind == FG_AAT &&
         (sample->flags & FG_AAT_SCALE_VALIDATED) == 0))
        return FG_UNTRUSTED_SCALE;
    if ((sample->flags & FG_LENS_STATIONARY) == 0)
        return FG_LENS_MOVING;
    return FG_OK;
}

static int observation_is_old(const FgState *state, const FgSample *sample,
                              int allow_equal)
{
    if (!state->have_last || sample == 0)
        return 0;
    if (!(allow_equal && sample->sample_ms == state->last.sample_ms) &&
        !forward(sample->sample_ms, state->last.sample_ms))
        return 1;
    /* A different source/context may restart its source sequence. */
    if (same_context(sample, &state->last) &&
        !(allow_equal && sample->source_seq == state->last.source_seq) &&
        !forward(sample->source_seq, state->last.source_seq))
        return 1;
    return 0;
}

static void accept_last(FgState *state, const FgSample *sample)
{
    copy_sample(&state->last, sample);
    state->have_last = 1;
}

static void seed(FgState *state, const FgSample *sample)
{
    state->times[0] = sample->sample_ms;
    state->reciprocal_q16[0] = (uint32_t)divide_u64(
        (uint64_t)state->anchor.scale_q16 << 16, sample->scale_q16);
    state->count = 1;
}

void fg_confirm(FgState *state, const FgSample *sample,
                uint32_t focus_event_seq, uint32_t confirmation_flags,
                FgResult *result)
{
    uint32_t now_ms = sample != 0 ? sample->now_ms : 0;
    if (state == 0 || !state->config_valid) {
        result_base(state, now_ms, FG_BAD_CONFIG, result);
        return;
    }
    if ((confirmation_flags & (FG_CDAF_CONFIRMED | FG_POSITION_VALID)) !=
        (FG_CDAF_CONFIRMED | FG_POSITION_VALID) ||
        (state->has_focus_event && !forward(focus_event_seq, state->focus_event_seq)) ||
        sample_reason(state, sample) != FG_OK ||
        observation_is_old(state, sample, 1)) {
        result_base(state, now_ms, FG_CONFIRMATION_REJECTED, result);
        return;
    }
    copy_sample(&state->anchor, sample);
    state->anchored = 1;
    state->focus_event_seq = focus_event_seq;
    state->has_focus_event = 1;
    accept_last(state, sample);
    seed(state, sample);
    result_base(state, now_ms, FG_TOO_FEW, result);
}

static void append(FgState *state, const FgSample *sample)
{
    uint32_t first = 0;
    uint32_t i;
    uint32_t kept;
    while (first < state->count &&
           sample->sample_ms - state->times[first] > state->config.window_ms)
        ++first;
    kept = state->count - first;
    if (kept == FG_CAPACITY) {
        ++first;
        --kept;
    }
    for (i = 0; i < kept; ++i) {
        state->times[i] = state->times[first + i];
        state->reciprocal_q16[i] = state->reciprocal_q16[first + i];
    }
    state->times[kept] = sample->sample_ms;
    state->reciprocal_q16[kept] = (uint32_t)divide_u64(
        (uint64_t)state->anchor.scale_q16 << 16, sample->scale_q16);
    state->count = kept + 1;
}

static void fit(const FgState *state, FgResult *result)
{
    uint32_t n = state->count;
    uint32_t span;
    uint32_t i;
    uint32_t rate;
    uint32_t residual;
    uint32_t quality;
    uint64_t sx = 0, sy = 0, sxx = 0, sxy = 0;
    uint64_t denominator;
    uint64_t relative_denominator;
    uint64_t max_error = 0;
    int64_t numerator;
    if (result == 0)
        return;
    if (n < state->config.min_samples) {
        result->reason = FG_TOO_FEW;
        return;
    }
    span = state->times[n - 1] - state->times[0];
    if (span < state->config.min_span_ms) {
        result->reason = FG_TOO_SHORT;
        return;
    }
    for (i = 0; i < n; ++i) {
        uint32_t x = state->times[i] - state->times[0];
        uint32_t y = state->reciprocal_q16[i];
        sx += x;
        sy += y;
        sxx += (uint64_t)x * x;
        sxy += (uint64_t)x * y;
    }
    denominator = (uint64_t)n * sxx - sx * sx;
    if (denominator == 0 || sy == 0) {
        result->reason = FG_TOO_SHORT;
        return;
    }
    numerator = (int64_t)((uint64_t)n * sxy) - (int64_t)(sx * sy);
    relative_denominator = denominator * sy;
    for (i = 0; i < n; ++i) {
        uint32_t x = state->times[i] - state->times[0];
        int64_t centered_x = (int64_t)((uint64_t)n * x) - (int64_t)sx;
        int64_t prediction = (int64_t)(sy * denominator) + numerator * centered_x;
        int64_t observed = (int64_t)((uint64_t)state->reciprocal_q16[i] * n * denominator);
        uint64_t error = magnitude_i64(observed - prediction);
        if (error > max_error)
            max_error = error;
    }
    residual = ratio_q16(max_error, relative_denominator, UINT32_MAX);
    result->residual_q16 = residual;
    if (residual > state->config.max_residual_q16) {
        result->reason = FG_NOISY;
        return;
    }
    /* slope / mean(q), per second. This is relative motion, not defocus,
     * metres/second, or a request to move the lens. */
    rate = ratio_q16(magnitude_i64(numerator) * n * 1000u,
                     relative_denominator, FG_MAX_SIGNED);
    result->relative_rate_q16 = numerator < 0 ? -(int32_t)rate : (int32_t)rate;
    result->direction = rate < state->config.min_rate_q16 ? FG_STATIONARY :
                        (numerator < 0 ? FG_APPROACH : FG_RECEDE);
    result->phase = FG_TRACKING;
    result->reason = FG_OK;
    /* Heuristic support score only: residual headroom times window coverage.
     * It has no probability interpretation or camera-calibrated threshold. */
    quality = 1000u - (uint32_t)divide_u64((uint64_t)residual * 1000u,
                                         state->config.max_residual_q16);
    result->confidence_permille = (uint32_t)divide_u64((uint64_t)quality * span,
                                                      state->config.window_ms);
}

void fg_update(FgState *state, const FgSample *sample, FgResult *result)
{
    uint32_t now_ms = sample != 0 ? sample->now_ms : 0;
    uint32_t reason;
    uint32_t gap = 0;
    if (state == 0 || !state->config_valid) {
        result_base(state, now_ms, FG_BAD_CONFIG, result);
        return;
    }
    if (observation_is_old(state, sample, 0)) {
        result_base(state, now_ms, FG_OUT_OF_ORDER, result);
        return;
    }
    reason = sample_reason(state, sample);
    if (reason != FG_OK && reason != FG_LENS_MOVING) {
        clear_anchor(state);
        result_base(state, now_ms, reason, result);
        return;
    }
    if (state->anchored && !same_context(sample, &state->anchor)) {
        clear_anchor(state);
        accept_last(state, sample);
        result_base(state, now_ms, FG_CONTEXT_CHANGED, result);
        return;
    }
    if (state->anchored &&
        (now_ms - state->anchor.sample_ms >= FG_HALF_EPOCH ||
         now_ms - state->anchor.sample_ms > state->config.max_anchor_age_ms)) {
        clear_anchor(state);
        accept_last(state, sample);
        result_base(state, now_ms, FG_ANCHOR_EXPIRED, result);
        return;
    }
    if (reason == FG_LENS_MOVING) {
        state->count = 0;
        accept_last(state, sample);
        result_base(state, now_ms, FG_LENS_MOVING, result);
        return;
    }
    if (!state->anchored) {
        accept_last(state, sample);
        result_base(state, now_ms, FG_NEEDS_FOCUS, result);
        return;
    }
    if (state->have_last) {
        uint32_t prior = state->last.scale_q16;
        uint32_t difference = sample->scale_q16 > prior ?
                              sample->scale_q16 - prior : prior - sample->scale_q16;
        if ((uint64_t)difference * FG_Q16 >
            (uint64_t)prior * state->config.max_scale_step_q16) {
            clear_anchor(state);
            accept_last(state, sample);
            result_base(state, now_ms, FG_SCALE_JUMP, result);
            return;
        }
        gap = sample->sample_ms - state->last.sample_ms > state->config.max_gap_ms;
    }
    accept_last(state, sample);
    if (gap || state->count == 0) {
        seed(state, sample);
        result_base(state, now_ms, gap ? FG_GAP : FG_TOO_FEW, result);
        return;
    }
    append(state, sample);
    result_base(state, now_ms, FG_TOO_FEW, result);
    fit(state, result);
}
