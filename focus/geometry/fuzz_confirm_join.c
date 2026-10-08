/* Native-only, deterministic sanitizer test. Never link this harness into
 * firmware. From this directory:
 * clang -std=c11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined \
 *   -fno-sanitize-recover=all fuzz_confirm_join.c confirm_join.c \
 *   -o /private/tmp/fp-confirm-join-fuzz
 * /private/tmp/fp-confirm-join-fuzz
 *
 * Public-API test: independent raw-event witnesses verify every candidate.
 * No private production functions, native firmware addresses, or camera I/O.
 */
#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include "confirm_join.h"

#define SEED UINT64_C(0x5f2ba84dfe1c9037)
#define TRIALS 8192u
#define PATH (FJ_SUPPORTED_PATH | FJ_CONNECTED)
#define END (PATH | FJ_FINAL_END | FJ_POSITION_VALID | FJ_SETTLE_VALIDATED)
#define MODES 36u

static uint64_t rng = SEED;
static uint64_t calls, resets, candidates, mutations, clean_episodes, long_tails;
static uint64_t reasons[FJ_RESET_REASON + 1];
static uint64_t mode_counts[MODES];

typedef struct {
    FjState state;
    FjConfig config;
    uint32_t clock, stream, episode, drive, step_ms, source;
    uint32_t drive_begin_ms;
    int32_t position;
    FjEvent result_witness, end_witness;
    int have_result_witness, have_end_witness;
    uint32_t emitted[32];
    uint32_t emitted_count;
} Run;

static uint64_t random64(void)
{
    uint64_t x = rng;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    rng = x;
    return x;
}

static uint32_t random_below(uint32_t bound)
{
    assert(bound != 0);
    return (uint32_t)(random64() % bound);
}

static uint32_t boundary(uint32_t low, uint32_t high)
{
    uint32_t choice = random_below(4);
    if (choice == 0) return low;
    if (choice == 1) return high;
    return low + random_below(high - low + 1);
}

static int follows_or_equals(uint32_t later, uint32_t earlier)
{
    return later - earlier < UINT32_C(0x80000000);
}

static int age_fits(uint32_t now, uint32_t then, uint32_t limit)
{
    return now - then <= limit;
}

static uint32_t next_generation(uint32_t value)
{
    ++value;
    return value ? value : 1;
}

static void require_same_identity(const FjEvent *a, const FjEvent *b)
{
    assert(a->episode_generation == b->episode_generation);
    assert(a->drive_generation == b->drive_generation);
    assert(a->target_generation == b->target_generation);
    assert(a->context_generation == b->context_generation);
    assert(a->scale_generation == b->scale_generation);
    assert(a->source_kind == b->source_kind);
}

static void require_result(const FjEvent *event)
{
    assert(event->kind == FJ_AF_RESULT);
    assert((event->flags & (PATH | FJ_AF_ACCEPTED)) == (PATH | FJ_AF_ACCEPTED));
    assert((event->flags & (FJ_CANCELLED | FJ_FAULTED | FJ_NOOP)) == 0);
    assert(event->error_code == 0);
}

static void require_end(const FjEvent *event, const FjState *before, const FjConfig *config)
{
    assert(event->kind == FJ_DRIVE_END);
    assert((event->flags & END) == END);
    assert((event->flags & (FJ_CANCELLED | FJ_FAULTED | FJ_NOOP)) == 0);
    assert(follows_or_equals(event->position_ms, before->drive_begin_ms));
    assert(follows_or_equals(event->event_ms, event->position_ms));
    assert(age_fits(event->now_ms, event->position_ms, config->max_position_age_ms));
}

static void verify_candidate(Run *run, const FjState *before,
                             const FjEvent *event, const FjResult *result)
{
    const FgSample *sample = &event->sample;
    assert(event->kind == FJ_SUBJECT);
    assert(before->phase == FJ_WAIT_SUBJECT);
    assert(run->have_result_witness && run->have_end_witness);
    require_result(&run->result_witness);
    require_end(&run->end_witness, before, &run->config);
    require_same_identity(event, &run->result_witness);
    require_same_identity(event, &run->end_witness);
    assert(sample->target_generation == event->target_generation);
    assert(sample->context_generation == event->context_generation);
    assert(sample->scale_generation == event->scale_generation);
    assert(sample->source_kind == event->source_kind);
    assert(sample->sample_ms == event->event_ms && sample->now_ms == event->now_ms);
    assert((sample->flags & FG_REQUIRED_FLAGS) == FG_REQUIRED_FLAGS);
    if (sample->source_kind == FG_AAT) assert(sample->flags & FG_AAT_SCALE_VALIDATED);
    assert(sample->scale_q16 >= 64 && sample->scale_q16 <= 131072);
    assert(sample->lens_position == run->end_witness.end_position);
    assert(follows_or_equals(sample->sample_ms, run->result_witness.event_ms));
    assert(follows_or_equals(sample->sample_ms, run->end_witness.event_ms));
    assert(age_fits(sample->now_ms, sample->sample_ms, run->config.max_sample_age_ms));
    assert(age_fits(event->now_ms, event->event_ms, run->config.max_event_age_ms));
    assert(age_fits(event->now_ms, before->begin_ms, run->config.max_episode_ms));
    assert(age_fits(event->now_ms, run->result_witness.event_ms, run->config.max_pair_gap_ms));
    assert(age_fits(event->now_ms, run->end_witness.event_ms, run->config.max_pair_gap_ms));
    assert(age_fits(event->now_ms, run->end_witness.position_ms, run->config.max_position_age_ms));
    assert(memcmp(&result->candidate, sample, sizeof(*sample)) == 0);
    assert(result->phase == FJ_CONSUMED && result->reason == FJ_OK);
    assert(result->focus_event_seq == before->focus_event_seq + 1u);
    assert(result->episode_generation == event->episode_generation);
    assert(result->drive_generation == event->drive_generation);
    for (uint32_t i = 0; i < run->emitted_count; ++i)
        assert(run->emitted[i] != event->episode_generation);
    assert(run->emitted_count < 32);
    run->emitted[run->emitted_count++] = event->episode_generation;
    ++candidates;
}

static FjResult apply(Run *run, const FjEvent *event)
{
    FjState before = run->state;
    FjResult result;
    FgSample zero = {0};
    fj_step(&run->state, event, &result);
    ++calls;
    assert(result.reason <= FJ_RESET_REASON);
    ++reasons[result.reason];
    assert(result.confirmation_authorized == 0 && result.motion_authorized == 0);
    if (event && before.have_stream) {
        uint32_t delta = event->stream_seq - before.last_stream_seq;
        if (delta == 0 || delta >= UINT32_C(0x80000000)) {
            assert(result.reason == FJ_OLD_EVENT);
            assert(memcmp(&before, &run->state, sizeof(before)) == 0);
        } else assert(run->state.last_stream_seq == event->stream_seq);
    }
    if (event && event->kind == FJ_BEGIN && result.phase == FJ_WAIT_DRIVE &&
        result.reason == FJ_WAITING) {
        run->have_result_witness = run->have_end_witness = 0;
    }
    if (event && event->kind == FJ_DRIVE_BEGIN && result.phase == FJ_WAIT_PAIR &&
        result.reason == FJ_WAITING) {
        run->have_result_witness = run->have_end_witness = 0;
        assert(!run->state.have_result && !run->state.have_end);
    }
    if (!before.have_result && run->state.have_result) {
        assert(event);
        require_result(event);
        run->result_witness = *event;
        run->have_result_witness = 1;
    }
    if (!before.have_end && run->state.have_end) {
        assert(event);
        require_end(event, &before, &run->config);
        run->end_witness = *event;
        run->have_end_witness = 1;
    }
    if (result.candidate_ready) {
        assert(event);
        verify_candidate(run, &before, event, &result);
    } else {
        assert(memcmp(&result.candidate, &zero, sizeof(zero)) == 0);
        assert(run->state.focus_event_seq == before.focus_event_seq);
    }
    if (run->state.phase == FJ_BLOCKED) {
        assert(!run->state.have_result && !run->state.have_end);
        run->have_result_witness = run->have_end_witness = 0;
    }
    if (event) {
        /* Keep generator clocks/transport after delivered forward mutations.
         * These cursors are not production state and never alter it. */
        if (follows_or_equals(event->now_ms, run->clock)) run->clock = event->now_ms;
        if (follows_or_equals(event->event_ms, run->clock)) run->clock = event->event_ms;
        if (follows_or_equals(event->stream_seq, run->stream)) run->stream = event->stream_seq + 1u;
    }
    return result;
}

static void reset_run(Run *run)
{
    FjState before = run->state;
    FjResult result;
    FgSample zero = {0};
    fj_reset(&run->state, &result);
    ++calls; ++resets; ++reasons[result.reason];
    assert(result.reason == FJ_RESET_REASON && result.phase == FJ_IDLE);
    assert(!result.candidate_ready && !result.confirmation_authorized && !result.motion_authorized);
    assert(memcmp(&result.candidate, &zero, sizeof(zero)) == 0);
    assert(run->state.last_stream_seq == before.last_stream_seq);
    assert(run->state.episode_generation == before.episode_generation);
    assert(run->state.drive_highwater == before.drive_highwater);
    assert(run->state.focus_event_seq == before.focus_event_seq);
    run->have_result_witness = run->have_end_witness = 0;
}

static FjEvent event_for(Run *run, uint32_t kind)
{
    FjEvent event = {0};
    run->clock += run->step_ms;
    event.kind = kind;
    event.stream_seq = run->stream++;
    event.event_ms = event.now_ms = run->clock;
    event.episode_generation = run->episode;
    event.drive_generation = run->drive;
    event.target_generation = 11;
    event.context_generation = 22;
    event.scale_generation = 33;
    event.source_kind = run->source;
    event.flags = kind == FJ_DRIVE_END ? END :
                  (kind == FJ_AF_RESULT ? PATH | FJ_AF_ACCEPTED : PATH);
    event.position_ms = event.event_ms;
    event.end_position = run->position;
    event.sample.now_ms = event.now_ms;
    event.sample.sample_ms = event.event_ms;
    event.sample.source_seq = event.stream_seq;
    event.sample.target_generation = event.target_generation;
    event.sample.context_generation = event.context_generation;
    event.sample.scale_generation = event.scale_generation;
    event.sample.source_kind = event.source_kind;
    event.sample.flags = FG_REQUIRED_FLAGS | FG_AAT_SCALE_VALIDATED;
    event.sample.scale_q16 = boundary(64,131072);
    event.sample.lens_position = run->position;
    return event;
}

static FjResult emit(Run *run, uint32_t kind)
{
    FjEvent event = event_for(run,kind);
    return apply(run,&event);
}

static void begin_episode(Run *run)
{
    run->episode = next_generation(run->episode);
    run->drive = next_generation(run->drive);
    FjResult result = emit(run,FJ_BEGIN);
    assert(result.phase == FJ_WAIT_DRIVE && !result.candidate_ready);
    result = emit(run,FJ_DRIVE_BEGIN);
    run->drive_begin_ms = run->clock;
    assert(result.phase == FJ_WAIT_PAIR && !result.candidate_ready);
}

static void pair(Run *run, int end_first)
{
    FjResult result = emit(run,end_first ? FJ_DRIVE_END : FJ_AF_RESULT);
    assert(result.phase == FJ_WAIT_PAIR && !result.candidate_ready);
    result = emit(run,end_first ? FJ_AF_RESULT : FJ_DRIVE_END);
    assert(result.phase == FJ_WAIT_SUBJECT && !result.candidate_ready);
}

static void clean_episode(Run *run, int end_first)
{
    begin_episode(run);
    pair(run,end_first);
    FjResult result = emit(run,FJ_SUBJECT);
    assert(result.candidate_ready == 1);
    ++clean_episodes;
    /* Consumed confirmation must not time out the separately owned FG anchor. */
    run->clock += run->config.max_episode_ms + run->config.max_pair_gap_ms + 1;
    result = emit(run,FJ_SUBJECT);
    assert(result.phase == FJ_CONSUMED && result.reason == FJ_ALREADY_CONSUMED);
    FjEvent tick = event_for(run,FJ_TICK);
    tick.episode_generation = tick.drive_generation = tick.target_generation = 0;
    tick.context_generation = tick.scale_generation = tick.source_kind = tick.flags = 0;
    result = apply(run,&tick);
    assert(result.phase == FJ_CONSUMED && result.reason == FJ_ALREADY_CONSUMED);
    ++long_tails;
}

static void adversarial_episode(Run *run, uint32_t mode)
{
    ++mutations; ++mode_counts[mode];
    begin_episode(run);
    FjEvent event = event_for(run,FJ_AF_RESULT);
    if (mode == 0) {
        /* Baseline with final end first, plus one intermediate backlash end. */
        event.kind = FJ_DRIVE_END; event.flags = PATH;
        assert(apply(run,&event).reason == FJ_INTERMEDIATE_END);
        pair(run,1);
    } else if (mode == 1) {
        (void)apply(run,&event);
        FjState saved = run->state;
        assert(apply(run,&event).reason == FJ_OLD_EVENT);
        assert(memcmp(&saved,&run->state,sizeof(saved)) == 0);
        (void)emit(run,FJ_DRIVE_END);
    } else if (mode == 2) {
        (void)apply(run,&event);
        (void)emit(run,FJ_DRIVE_END);
        uint32_t old_result_ms = run->state.result_ms, old_end_ms = run->state.end_ms;
        (void)emit(run,FJ_AF_RESULT);
        FjEvent repeated = event_for(run,FJ_DRIVE_END);
        repeated.end_position = run->position == INT32_MAX ? INT32_MIN : run->position+1;
        (void)apply(run,&repeated);
        assert(run->state.result_ms == old_result_ms && run->state.end_ms == old_end_ms);
        assert(run->state.end_position == run->position);
    } else if (mode == 3 || mode == 4) {
        (void)apply(run,&event);
        (void)emit(run,FJ_DRIVE_END);
        uint32_t prior_drive = run->drive;
        run->drive = next_generation(run->drive);
        (void)emit(run,FJ_DRIVE_BEGIN);
        run->drive_begin_ms = run->clock;
        if (mode == 3) pair(run,random_below(2));
        else {
            (void)emit(run,FJ_AF_RESULT);
            event = event_for(run,FJ_DRIVE_END); event.drive_generation = prior_drive;
            (void)apply(run,&event);
        }
    } else if (mode >= 5 && mode <= 17) {
        switch (mode) {
        case 5: ++event.stream_seq; break;
        case 6: ++event.context_generation; break;
        case 7: event.episode_generation = next_generation(event.episode_generation); break;
        case 8: event.drive_generation = next_generation(event.drive_generation); break;
        case 9: event.flags &= ~FJ_AF_ACCEPTED; break;
        case 10: event.error_code = boundary(1,UINT32_MAX); break;
        case 11: event.kind = FJ_CANCEL; break;
        case 12: event.flags |= FJ_FAULTED; break;
        case 13: event.flags |= FJ_NOOP; break;
        case 14: event.now_ms += run->config.max_event_age_ms+1; break;
        case 15: event.event_ms = run->state.last_event_ms-1; break;
        case 16: event.kind = 0; break;
        default: event.target_generation = 0; break;
        }
        (void)apply(run,&event);
        assert(run->state.phase == FJ_BLOCKED);
        (void)emit(run,FJ_DRIVE_END);
    } else if (mode >= 18 && mode <= 23) {
        (void)apply(run,&event);
        event = event_for(run,FJ_DRIVE_END);
        switch (mode) {
        case 18: event.flags &= ~FJ_SETTLE_VALIDATED; break;
        case 19: event.flags &= ~FJ_FINAL_END; break;
        case 20: event.position_ms = run->drive_begin_ms-1; break;
        case 21: event.position_ms = event.event_ms+1; break;
        case 22: event.flags &= ~FJ_CONNECTED; break;
        default: run->clock += run->config.max_pair_gap_ms+1;
                 event.event_ms = event.now_ms = run->clock; break;
        }
        (void)apply(run,&event);
    } else {
        (void)apply(run,&event);
        (void)emit(run,FJ_DRIVE_END);
        if (mode == 24) reset_run(run);
        else if (mode == 25) (void)emit(run,FJ_BEGIN); /* Same episode cannot rearm. */
    }
    event = event_for(run,FJ_SUBJECT);
    switch (mode) {
    case 26: ++event.sample.context_generation; break;
    case 27: event.sample.sample_ms -= 1; break;
    case 28: event.sample.now_ms += 1; break;
    case 29: event.sample.lens_position = run->position == INT32_MAX ? INT32_MIN : run->position+1; break;
    case 30: event.sample.scale_q16 = random_below(2) ? 63 : 131073; break;
    case 31: event.sample.flags &= ~FG_PROJECTION_STABLE; break;
    case 32: event.sample.flags &= ~FG_AAT_SCALE_VALIDATED; break;
    case 33: event.now_ms += run->config.max_sample_age_ms+1;
             event.sample.now_ms = event.now_ms; break;
    case 34: run->clock += run->config.max_position_age_ms+1;
             event.now_ms = event.event_ms = event.sample.now_ms = event.sample.sample_ms = run->clock; break;
    case 35: event.flags |= FJ_CANCELLED; break;
    default: break;
    }
    FjResult result = apply(run,&event);
    if (mode <= 3) assert(result.candidate_ready);
    else assert(!result.candidate_ready);
    if (result.candidate_ready) {
        run->drive = next_generation(run->drive);
        result = emit(run,FJ_DRIVE_BEGIN);
        assert(result.phase == FJ_BLOCKED && !result.candidate_ready);
    }
    /* Missing final event/subject must eventually block, never auto-confirm. */
    run->clock += run->config.max_episode_ms + run->config.max_pair_gap_ms + 1;
    result = emit(run,FJ_TICK);
    assert(!result.candidate_ready);
}

static void start_run(Run *run, uint32_t trial)
{
    memset(run,0,sizeof(*run));
    run->config.max_episode_ms = boundary(20,600000);
    run->config.max_event_age_ms = boundary(0,1000);
    run->config.max_pair_gap_ms = boundary(1,run->config.max_episode_ms);
    run->config.max_sample_age_ms = boundary(0,1000);
    run->config.max_position_age_ms = boundary(1,run->config.max_episode_ms);
    assert(fj_init(&run->state,&run->config));
    run->clock = UINT32_MAX-random_below(100);
    run->stream = UINT32_MAX-random_below(20);
    run->episode = UINT32_MAX-random_below(4);
    run->drive = UINT32_MAX-random_below(4);
    run->source = (trial % MODES == 32 || random_below(2)) ? FG_AAT : FG_FACE;
    run->position = trial % 3 == 0 ? INT32_MIN :
                    (trial % 3 == 1 ? INT32_MAX : (int32_t)(random64() & INT32_MAX));
    uint32_t budget = run->config.max_pair_gap_ms;
    if (run->config.max_episode_ms < budget) budget = run->config.max_episode_ms;
    if (run->config.max_position_age_ms < budget) budget = run->config.max_position_age_ms;
    run->step_ms = budget/32;
    if (run->step_ms > 5) run->step_ms = 5;
}

int main(void)
{
    for (uint32_t trial = 0; trial < TRIALS; ++trial) {
        Run run;
        start_run(&run,trial);
        clean_episode(&run,trial & 1);
        adversarial_episode(&run,trial % MODES);
        clean_episode(&run,(trial & 1) ^ 1);
        /* A reset cannot turn an already-consumed episode into a fresh one. */
        reset_run(&run);
        FjEvent replay = event_for(&run,FJ_BEGIN);
        assert(apply(&run,&replay).phase == FJ_BLOCKED);
        assert(!apply(&run,0).candidate_ready);
    }
    for (uint32_t i=0;i<MODES;++i) assert(mode_counts[i] > 0);
    assert(candidates >= (uint64_t)TRIALS*2);
    printf("{\n  \"schema\": \"sigma-fp-confirm-join-fuzz/v1\",\n"
           "  \"status\": \"passed\",\n  \"seed\": \"0x5f2ba84dfe1c9037\",\n"
           "  \"trials\": %u,\n  \"api_calls\": %" PRIu64 ",\n"
           "  \"reset_calls\": %" PRIu64 ",\n  \"candidates\": %" PRIu64 ",\n"
           "  \"clean_episodes\": %" PRIu64 ",\n  \"mutated_episodes\": %" PRIu64 ",\n"
           "  \"mutation_modes\": %u,\n  \"long_consumed_tails\": %" PRIu64 ",\n"
           "  \"camera_io\": false,\n  \"confirmation_authorized\": false,\n"
           "  \"motion_authorized\": false,\n  \"reason_counts\": [",
           TRIALS,calls,resets,candidates,clean_episodes,mutations,MODES,long_tails);
    for (uint32_t i=0;i<=FJ_RESET_REASON;++i)
        printf("%s%" PRIu64,i?", ":"",reasons[i]);
    printf("]\n}\n");
    return 0;
}
