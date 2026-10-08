/* Offline native sanitizer/oracle test. Never link into firmware.
 * Includes the production C to exercise private arithmetic boundaries too. */
#include <assert.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include "geometry.c"

static uint64_t random_state = UINT64_C(0xa95031786e42d7fb);
static uint64_t events, oracle_fits, direct_fits, helper_cases;
static uint64_t reasons[FG_EXPLICIT_RESET + 1];

static uint64_t rnd64(void)
{
    uint64_t x = random_state;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    random_state = x;
    return x;
}

static uint32_t rnd(uint32_t n)
{
    return (uint32_t)(rnd64() % n);
}

static uint32_t edge(uint32_t lo, uint32_t hi)
{
    uint32_t choice = rnd(4);
    if (choice == 0) return lo;
    if (choice == 1) return hi;
    return lo + rnd(hi - lo + 1);
}

static void oracle(const FgState *state, const FgResult *result)
{
    uint32_t n = state->count;
    long double sx = 0, sy = 0, sxx = 0, sxy = 0;
    long double numerator, denominator, largest = 0;
    if (result->reason != FG_OK && result->reason != FG_NOISY) return;
    assert(n >= 3 && n <= FG_CAPACITY);
    ++oracle_fits;
    for (uint32_t i = 0; i < n; ++i) {
        long double x = (uint32_t)(state->times[i] - state->times[0]);
        long double y = state->reciprocal_q16[i];
        sx += x; sy += y; sxx += x*x; sxy += x*y;
    }
    denominator = n*sxx - sx*sx;
    numerator = n*sxy - sx*sy;
    assert(denominator > 0 && sy > 0);
    for (uint32_t i = 0; i < n; ++i) {
        long double x = (uint32_t)(state->times[i] - state->times[0]);
        long double prediction = sy/n + (numerator/denominator)*(x-sx/n);
        long double error = fabsl(state->reciprocal_q16[i] - prediction)/(sy/n);
        if (error > largest) largest = error;
    }
    long double residual = floorl(largest*FG_Q16);
    if (residual > UINT32_MAX) residual = UINT32_MAX;
    assert(fabsl(result->residual_q16 - residual) <= 1.0L);
    if (result->reason == FG_OK) {
        long double rate = floorl(fabsl(numerator)*n*1000.0L*FG_Q16/(denominator*sy));
        if (rate > INT32_MAX) rate = INT32_MAX;
        if (numerator < 0) rate = -rate;
        assert(fabsl(result->relative_rate_q16 - rate) <= 1.0L);
        assert(result->confidence_permille <= 1000);
        if ((uint32_t)llabs(result->relative_rate_q16) < state->config.min_rate_q16)
            assert(result->direction == FG_STATIONARY);
        else assert(result->direction == (numerator < 0 ? FG_APPROACH : FG_RECEDE));
    } else assert(result->direction == FG_UNKNOWN);
}

static void check_event(const FgState *state, const FgResult *result)
{
    ++events;
    assert(result->motion_authorized == 0);
    assert(result->reason <= FG_EXPLICIT_RESET);
    ++reasons[result->reason];
    assert(state->count <= FG_CAPACITY);
    if (!state->anchored) assert(state->count == 0);
    if (state->count) {
        assert(state->times[state->count-1] - state->times[0] <= state->config.window_ms);
        for (uint32_t i = 0; i < state->count; ++i) {
            assert(state->reciprocal_q16[i] >= 32 && state->reciprocal_q16[i] <= (1u<<27));
            if (i) assert(forward(state->times[i], state->times[i-1]));
        }
    }
    oracle(state, result);
}

static FgConfig random_config(void)
{
    FgConfig c;
    c.window_ms = edge(20, 1000);
    c.min_span_ms = edge(1, c.window_ms);
    c.min_samples = edge(3, FG_CAPACITY);
    c.max_gap_ms = edge(1, c.window_ms);
    c.max_age_ms = edge(0, 1000);
    c.max_anchor_age_ms = edge(c.window_ms+1, 600000);
    c.min_rate_q16 = edge(1, FG_Q16);
    c.max_residual_q16 = edge(1, 16384);
    c.max_scale_step_q16 = edge(1, 32768);
    return c;
}

static void fuzz_events(void)
{
    for (uint32_t trial = 0; trial < 2048; ++trial) {
        FgState state;
        FgResult result;
        FgConfig c = random_config();
        FgSample sample = {0};
        uint32_t clock = UINT32_MAX - rnd(2000), sequence = UINT32_MAX-rnd(40);
        uint32_t focus_sequence = UINT32_MAX-rnd(20);
        assert(fg_init(&state, &c));
        sample.sample_ms = clock;
        sample.now_ms = clock;
        sample.source_seq = sequence;
        sample.target_generation = sample.context_generation = sample.scale_generation = 1;
        sample.source_kind = (trial & 1) ? FG_FACE : FG_AAT;
        sample.flags = FG_REQUIRED_FLAGS | FG_AAT_SCALE_VALIDATED;
        sample.scale_q16 = edge(64,131072);
        sample.lens_position = (int32_t)rnd64();
        fg_confirm(&state, &sample, focus_sequence, 3, &result);
        check_event(&state, &result);
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t mode = rnd(100);
            clock += edge(1, c.window_ms+1);
            ++sequence;
            sample.sample_ms = clock;
            sample.now_ms = clock + rnd(c.max_age_ms+1);
            sample.source_seq = sequence;
            sample.flags = FG_REQUIRED_FLAGS | FG_AAT_SCALE_VALIDATED;
            sample.target_generation = (sample.target_generation ? sample.target_generation : 1);
            sample.scale_q16 = edge(64,131072);
            if (mode < 35) {
                if (state.have_last) sample.scale_q16 = state.last.scale_q16;
                fg_update(&state, &sample, &result);
            } else if (mode < 45) {
                fg_confirm(&state, &sample, ++focus_sequence, 3, &result);
            } else if (mode < 50) {
                fg_confirm(&state, &sample, state.focus_event_seq, 3, &result);
            } else if (mode < 55) {
                fg_confirm(&state, &sample, ++focus_sequence, rnd(3), &result);
            } else if (mode < 60) {
                sample.now_ms = sample.sample_ms+c.max_age_ms+1;
                fg_update(&state, &sample, &result);
            } else if (mode < 65) {
                if (state.have_last) sample.source_seq = state.last.source_seq;
                fg_update(&state, &sample, &result);
            } else if (mode < 70) {
                if (state.have_last) sample.sample_ms = state.last.sample_ms;
                fg_update(&state, &sample, &result);
            } else if (mode < 75) {
                sample.flags &= ~FG_LENS_STATIONARY;
                fg_update(&state, &sample, &result);
            } else if (mode < 80) {
                sample.flags &= ~(rnd(2) ? FG_PROJECTION_STABLE : FG_AAT_SCALE_VALIDATED);
                fg_update(&state, &sample, &result);
            } else if (mode < 85) {
                ++sample.context_generation;
                sequence = sample.source_seq = 0;
                fg_update(&state, &sample, &result);
            } else if (mode < 90) {
                sample.scale_q16 = rnd(2) ? 64 : 131072;
                fg_update(&state, &sample, &result);
            } else if (mode < 94) {
                if (rnd(2)) sample.target_generation = 0;
                else sample.scale_q16 = rnd(2) ? 63 : 131073;
                fg_update(&state, &sample, &result);
            } else if (mode < 98) {
                fg_invalidate(&state, FG_EXPLICIT_RESET, &result);
            } else if (mode == 98) fg_update(&state, 0, &result);
            else fg_confirm(&state, 0, ++focus_sequence, 3, &result);
            check_event(&state, &result);
        }
    }
}

static void valid_long_runs(void)
{
    for (uint32_t trial = 0; trial < 128; ++trial) {
        FgState state;
        FgConfig c;
        FgResult result;
        FgSample sample = {0};
        fg_default_config(&c);
        c.window_ms = 1000; c.min_span_ms = 1; c.min_samples = 3;
        c.max_gap_ms = 1000; c.max_anchor_age_ms = 600000;
        c.max_residual_q16 = 16384; c.max_scale_step_q16 = 32768;
        assert(fg_init(&state, &c));
        sample.sample_ms = sample.now_ms = UINT32_MAX-400;
        sample.source_seq = UINT32_MAX-200;
        sample.target_generation = sample.context_generation = sample.scale_generation = 1;
        sample.source_kind = FG_FACE; sample.flags = FG_REQUIRED_FLAGS;
        sample.scale_q16 = (trial & 1) ? 64 : 131072;
        fg_confirm(&state, &sample, UINT32_MAX-1, 3, &result);
        check_event(&state,&result);
        for (uint32_t i = 0; i < 2048; ++i) {
            sample.sample_ms += 1+rnd(30);
            sample.now_ms = sample.sample_ms;
            ++sample.source_seq;
            if (i < 40) {
                uint32_t step = sample.scale_q16/4;
                if (trial & 1) sample.scale_q16 += step ? step : 1;
                else sample.scale_q16 -= step ? step : 1;
                if (sample.scale_q16 < 64) sample.scale_q16 = 64;
                if (sample.scale_q16 > 131072) sample.scale_q16 = 131072;
            }
            fg_update(&state,&sample,&result);
            check_event(&state,&result);
        }
    }
}

static void fuzz_fit_bounds(void)
{
    for (uint32_t trial = 0; trial < 100000; ++trial) {
        FgState state;
        FgConfig c;
        FgResult result;
        fg_default_config(&c);
        c.window_ms = 1000; c.min_span_ms = 1; c.min_samples = 3;
        c.max_residual_q16 = 16384;
        assert(fg_init(&state,&c));
        state.count = edge(3,32);
        uint32_t base = UINT32_MAX-rnd(1000);
        uint32_t previous = 0;
        for (uint32_t i = 0; i < state.count; ++i) {
            uint32_t maximum = 1000-(state.count-1-i);
            uint32_t x = i == 0 ? 0 : edge(previous+1,maximum);
            state.times[i] = base+x;
            state.reciprocal_q16[i] = edge(32,1u<<27);
            previous = x;
        }
        result_base(&state,base,FG_TOO_FEW,&result);
        fit(&state,&result);
        assert(result.motion_authorized == 0);
        oracle(&state,&result);
        ++direct_fits;
    }
}

static void fuzz_helpers(void)
{
    for (uint32_t i = 0; i < 1000000; ++i) {
        uint64_t a = rnd64(), b = rnd64();
        uint32_t limit = (uint32_t)rnd64();
        if ((i & 31) == 0) b = 1;
        if ((i & 31) == 1) b = UINT64_MAX;
        if ((i & 31) == 2) a = UINT64_MAX;
        if (b == 0) b = 1;
        assert(divide_u64(a,b) == a/b);
        __uint128_t want = ((__uint128_t)a << 16)/b;
        if (want > limit) want = limit;
        assert(ratio_q16(a,b,limit) == (uint32_t)want);
        assert(magnitude_i64(INT64_MIN) == ((uint64_t)1<<63));
        ++helper_cases;
    }
}

int main(void)
{
    fuzz_helpers();
    fuzz_events();
    valid_long_runs();
    fuzz_fit_bounds();
    printf("PASS events=%" PRIu64 " oracle_fits=%" PRIu64
           " direct_fit_bounds=%" PRIu64 " helper_cases=%" PRIu64
           " seed=0xa95031786e42d7fb\n",events,oracle_fits,direct_fits,helper_cases);
    for (uint32_t i=0;i<=FG_EXPLICIT_RESET;++i)
        printf("reason[%u]=%" PRIu64 "%c",i,reasons[i],i==FG_EXPLICIT_RESET?'\n':' ');
    return 0;
}
