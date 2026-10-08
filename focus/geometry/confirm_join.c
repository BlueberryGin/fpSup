#include "confirm_join.h"

/* A single serialized consumer owns this bounded, allocation-free state.
 * Unsigned subtraction implements modulo-2^32 time/sequence comparisons;
 * intervals >= 2^31 are rejected, never treated as elapsed forward time.
 * This file does not call geometry.c, native firmware, or a runtime library. */
#define FJ_HALF_EPOCH 0x80000000u
#define FJ_PATH_FLAGS (FJ_SUPPORTED_PATH | FJ_CONNECTED)
#define FJ_END_FLAGS (FJ_PATH_FLAGS | FJ_POSITION_VALID | FJ_SETTLE_VALIDATED)

static int newer(uint32_t value, uint32_t previous)
{
    uint32_t delta = value - previous;
    return delta != 0 && delta < FJ_HALF_EPOCH;
}

static int not_before(uint32_t value, uint32_t previous)
{
    return value - previous < FJ_HALF_EPOCH;
}

static int within(uint32_t value, uint32_t previous, uint32_t limit)
{
    uint32_t elapsed = value - previous;
    return elapsed < FJ_HALF_EPOCH && elapsed <= limit;
}

static void clear_sample(FgSample *sample)
{
    sample->now_ms = 0;
    sample->sample_ms = 0;
    sample->source_seq = 0;
    sample->target_generation = 0;
    sample->context_generation = 0;
    sample->scale_generation = 0;
    sample->source_kind = 0;
    sample->flags = 0;
    sample->scale_q16 = 0;
    sample->lens_position = 0;
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

static void output(const FjState *state, uint32_t reason, FjResult *result)
{
    if (result == 0)
        return;
    result->candidate_ready = 0;
    result->phase = state != 0 ? state->phase : FJ_BLOCKED;
    result->reason = reason;
    result->episode_generation = state != 0 && state->have_episode ?
                                 state->episode_generation : 0;
    result->drive_generation = state != 0 ? state->drive_generation : 0;
    result->focus_event_seq = state != 0 ? state->focus_event_seq : 0;
    result->confirmation_authorized = 0;
    result->motion_authorized = 0;
    clear_sample(&result->candidate);
}

static void clear_pair(FjState *state)
{
    state->have_result = 0;
    state->result_ms = 0;
    state->have_end = 0;
    state->end_ms = 0;
    state->position_ms = 0;
    state->end_position = 0;
}

static void block(FjState *state, uint32_t reason, FjResult *result)
{
    state->phase = FJ_BLOCKED;
    clear_pair(state);
    output(state, reason, result);
}

void fj_default_config(FjConfig *config)
{
    if (config == 0)
        return;
    config->max_episode_ms = 5000;
    config->max_event_age_ms = 100;
    config->max_pair_gap_ms = 500;
    config->max_sample_age_ms = 100;
    config->max_position_age_ms = 500;
}

static int valid_config(const FjConfig *config)
{
    return config != 0 && config->max_episode_ms >= 20 &&
           config->max_episode_ms <= 600000 &&
           config->max_event_age_ms <= 1000 &&
           config->max_pair_gap_ms > 0 &&
           config->max_pair_gap_ms <= config->max_episode_ms &&
           config->max_sample_age_ms <= 1000 &&
           config->max_position_age_ms > 0 &&
           config->max_position_age_ms <= config->max_episode_ms;
}

int fj_init(FjState *state, const FjConfig *config)
{
    if (state == 0)
        return 0;
    state->config_valid = 0;
    state->phase = FJ_IDLE;
    state->have_stream = 0;
    state->last_stream_seq = 0;
    state->last_event_ms = 0;
    state->last_now_ms = 0;
    state->have_episode = 0;
    state->episode_generation = 0;
    state->begin_ms = 0;
    state->target_generation = 0;
    state->context_generation = 0;
    state->scale_generation = 0;
    state->source_kind = 0;
    state->have_drive_highwater = 0;
    state->drive_highwater = 0;
    state->drive_generation = 0;
    state->drive_begin_ms = 0;
    state->focus_event_seq = 0;
    clear_pair(state);
    if (!valid_config(config))
        return 0;
    state->config.max_episode_ms = config->max_episode_ms;
    state->config.max_event_age_ms = config->max_event_age_ms;
    state->config.max_pair_gap_ms = config->max_pair_gap_ms;
    state->config.max_sample_age_ms = config->max_sample_age_ms;
    state->config.max_position_age_ms = config->max_position_age_ms;
    state->config_valid = 1;
    return 1;
}

void fj_reset(FjState *state, FjResult *result)
{
    if (state == 0 || !state->config_valid) {
        output(state, FJ_BAD_CONFIG, result);
        return;
    }
    state->phase = FJ_IDLE;
    state->drive_generation = 0;
    state->drive_begin_ms = 0;
    clear_pair(state);
    /* Keep stream/clock, episode, drive, and candidate-sequence high waters. */
    output(state, FJ_RESET_REASON, result);
}

static int identity_valid(const FjEvent *event)
{
    return event->episode_generation != 0 && event->target_generation != 0 &&
           event->context_generation != 0 && event->scale_generation != 0 &&
           (event->source_kind == FG_FACE || event->source_kind == FG_AAT);
}

static int identity_matches(const FjState *state, const FjEvent *event)
{
    return event->target_generation == state->target_generation &&
           event->context_generation == state->context_generation &&
           event->scale_generation == state->scale_generation &&
           event->source_kind == state->source_kind;
}

static int expired(const FjState *state, uint32_t now_ms)
{
    /* These are pending-confirmation deadlines. After one-shot emission,
     * geometry owns anchor expiry; retain the stream/identity/drive guards
     * without invalidating ordinary tracking at the old pair's deadline. */
    if (state->phase == FJ_CONSUMED)
        return 0;
    if (!within(now_ms, state->begin_ms, state->config.max_episode_ms))
        return 1;
    /* Keep the first event's deadline even after the counterpart arrives.
     * Duplicates and subject samples never refresh either stored time. */
    if (state->have_result &&
        !within(now_ms, state->result_ms, state->config.max_pair_gap_ms))
        return 1;
    if (state->have_end &&
        !within(now_ms, state->end_ms, state->config.max_pair_gap_ms))
        return 1;
    return 0;
}

static int subject_valid(const FjState *state, const FjEvent *event)
{
    const FgSample *sample = &event->sample;
    return sample->sample_ms == event->event_ms &&
           sample->now_ms == event->now_ms &&
           sample->target_generation == state->target_generation &&
           sample->context_generation == state->context_generation &&
           sample->scale_generation == state->scale_generation &&
           sample->source_kind == state->source_kind &&
           (sample->flags & FG_REQUIRED_FLAGS) == FG_REQUIRED_FLAGS &&
           (sample->source_kind != FG_AAT ||
            (sample->flags & FG_AAT_SCALE_VALIDATED) != 0) &&
           sample->scale_q16 >= 64 && sample->scale_q16 <= 131072 &&
           within(sample->now_ms, sample->sample_ms, state->config.max_sample_age_ms) &&
           not_before(sample->sample_ms, state->result_ms) &&
           not_before(sample->sample_ms, state->end_ms) &&
           sample->lens_position == state->end_position;
}

void fj_step(FjState *state, const FjEvent *event, FjResult *result)
{
    uint32_t sequence_delta = 1;
    int clocks_valid = 1;
    if (state == 0 || !state->config_valid) {
        output(state, FJ_BAD_CONFIG, result);
        return;
    }
    if (event == 0) {
        block(state, FJ_BAD_EVENT, result);
        return;
    }
    if (state->have_stream) {
        sequence_delta = event->stream_seq - state->last_stream_seq;
        if (sequence_delta == 0 || sequence_delta >= FJ_HALF_EPOCH) {
            output(state, FJ_OLD_EVENT, result);
            return;
        }
        clocks_valid = not_before(event->event_ms, state->last_event_ms) &&
                       not_before(event->now_ms, state->last_now_ms);
        /* Never lower clock high waters on a rejected backward event. */
        if (not_before(event->event_ms, state->last_event_ms))
            state->last_event_ms = event->event_ms;
        if (not_before(event->now_ms, state->last_now_ms))
            state->last_now_ms = event->now_ms;
    } else {
        state->last_event_ms = event->event_ms;
        state->last_now_ms = event->now_ms;
    }
    state->have_stream = 1;
    state->last_stream_seq = event->stream_seq;
    if (sequence_delta != 1) {
        block(state, FJ_LOST_EVENT, result);
        return;
    }
    if (!clocks_valid ||
        !within(event->now_ms, event->event_ms, state->config.max_event_age_ms)) {
        block(state, FJ_STALE_EVENT, result);
        return;
    }
    if (event->kind < FJ_BEGIN || event->kind > FJ_TICK) {
        block(state, FJ_BAD_EVENT, result);
        return;
    }
    if (event->kind != FJ_TICK) {
        if (event->kind == FJ_FAULT || (event->flags & FJ_FAULTED) != 0) {
            block(state, FJ_FAULT_REASON, result);
            return;
        }
        if (event->kind == FJ_CANCEL || (event->flags & FJ_CANCELLED) != 0) {
            block(state, FJ_CANCEL_REASON, result);
            return;
        }
        if ((event->flags & FJ_NOOP) != 0) {
            block(state, FJ_NOOP_UNSUPPORTED, result);
            return;
        }
        if (!identity_valid(event)) {
            block(state, FJ_BAD_EVENT, result);
            return;
        }
    }
    if (event->kind == FJ_BEGIN) {
        if ((event->flags & FJ_PATH_FLAGS) != FJ_PATH_FLAGS) {
            block(state, FJ_BAD_EVENT, result);
            return;
        }
        if (state->have_episode && !newer(event->episode_generation, state->episode_generation)) {
            block(state, FJ_WRONG_EPISODE, result);
            return;
        }
        state->have_episode = 1;
        state->episode_generation = event->episode_generation;
        state->begin_ms = event->event_ms;
        state->target_generation = event->target_generation;
        state->context_generation = event->context_generation;
        state->scale_generation = event->scale_generation;
        state->source_kind = event->source_kind;
        state->drive_generation = 0;
        state->drive_begin_ms = 0;
        clear_pair(state);
        state->phase = FJ_WAIT_DRIVE;
        output(state, FJ_WAITING, result);
        return;
    }
    if (state->phase == FJ_IDLE || state->phase == FJ_BLOCKED || !state->have_episode) {
        output(state, FJ_NEEDS_BEGIN, result);
        return;
    }
    if (expired(state, event->now_ms)) {
        block(state, FJ_TIMEOUT, result);
        return;
    }
    if (event->kind == FJ_TICK) {
        output(state, state->phase == FJ_CONSUMED ? FJ_ALREADY_CONSUMED : FJ_WAITING, result);
        return;
    }
    if (event->episode_generation != state->episode_generation) {
        block(state, FJ_WRONG_EPISODE, result);
        return;
    }
    if (!identity_matches(state, event)) {
        block(state, FJ_CONTEXT_CHANGED, result);
        return;
    }
    if (event->kind == FJ_DRIVE_BEGIN) {
        if (state->phase == FJ_CONSUMED) {
            block(state, FJ_ALREADY_CONSUMED, result);
            return;
        }
        if ((event->flags & FJ_PATH_FLAGS) != FJ_PATH_FLAGS) {
            block(state, FJ_BAD_EVENT, result);
            return;
        }
        if (event->drive_generation == 0 ||
            (state->have_drive_highwater && !newer(event->drive_generation, state->drive_highwater))) {
            block(state, FJ_WRONG_DRIVE, result);
            return;
        }
        state->have_drive_highwater = 1;
        state->drive_highwater = event->drive_generation;
        state->drive_generation = event->drive_generation;
        state->drive_begin_ms = event->event_ms;
        clear_pair(state);
        state->phase = FJ_WAIT_PAIR;
        output(state, FJ_WAITING, result);
        return;
    }
    if (event->drive_generation == 0 || event->drive_generation != state->drive_generation) {
        block(state, FJ_WRONG_DRIVE, result);
        return;
    }
    if (event->kind == FJ_AF_RESULT) {
        if ((event->flags & (FJ_PATH_FLAGS | FJ_AF_ACCEPTED)) !=
            (FJ_PATH_FLAGS | FJ_AF_ACCEPTED) || event->error_code != 0) {
            block(state, FJ_RESULT_FAILED, result);
            return;
        }
        if (state->have_result) {
            output(state, FJ_WAITING, result);
            return;
        }
        state->have_result = 1;
        state->result_ms = event->event_ms;
        state->phase = state->have_end ? FJ_WAIT_SUBJECT : FJ_WAIT_PAIR;
        output(state, FJ_WAITING, result);
        return;
    }
    if (event->kind == FJ_DRIVE_END) {
        if ((event->flags & FJ_FINAL_END) == 0) {
            output(state, FJ_INTERMEDIATE_END, result);
            return;
        }
        if ((event->flags & FJ_END_FLAGS) != FJ_END_FLAGS) {
            block(state, FJ_UNTRUSTED_POSITION, result);
            return;
        }
        if (state->have_end) {
            output(state, FJ_WAITING, result);
            return;
        }
        if (!not_before(event->position_ms, state->drive_begin_ms) ||
            !not_before(event->event_ms, event->position_ms) ||
            !within(event->now_ms, event->position_ms, state->config.max_position_age_ms)) {
            block(state, FJ_UNTRUSTED_POSITION, result);
            return;
        }
        state->have_end = 1;
        state->end_ms = event->event_ms;
        state->position_ms = event->position_ms;
        state->end_position = event->end_position;
        state->phase = state->have_result ? FJ_WAIT_SUBJECT : FJ_WAIT_PAIR;
        output(state, FJ_WAITING, result);
        return;
    }
    /* FJ_SUBJECT is the only remaining kind and the only emission point. */
    if (state->phase == FJ_CONSUMED) {
        output(state, FJ_ALREADY_CONSUMED, result);
        return;
    }
    if (!state->have_result || !state->have_end) {
        output(state, FJ_WAITING, result);
        return;
    }
    if (!within(event->now_ms, state->position_ms, state->config.max_position_age_ms)) {
        block(state, FJ_UNTRUSTED_POSITION, result);
        return;
    }
    if (!subject_valid(state, event)) {
        block(state, FJ_UNTRUSTED_SUBJECT, result);
        return;
    }
    state->phase = FJ_CONSUMED;
    state->focus_event_seq += 1u;
    output(state, FJ_OK, result);
    if (result != 0) {
        result->candidate_ready = 1;
        copy_sample(&result->candidate, &event->sample);
    }
}
