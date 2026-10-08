#ifndef FP_FOCUS_GEOMETRY_H
#define FP_FOCUS_GEOMETRY_H

#include <stdint.h>

/* Shadow-only core. No camera addresses, I/O, allocation, or lens commands.
 * All clocks share one uint32 millisecond epoch, with modular forward deltas
 * less than 2^31. Generations are nonzero adapter-owned identity tokens, not
 * raw native tracking IDs or publication counters presented as exposure IDs.
 * scale_q16 is a linear size / effective image width, not an area or AF ROI.
 * Defaults are development thresholds, NOT camera-calibrated parameters. */
#define FG_VERSION 1u
#define FG_CAPACITY 32u
#define FG_Q16 65536u

enum fg_source { FG_FACE = 1, FG_AAT = 2 };
enum fg_direction { FG_UNKNOWN = 0, FG_APPROACH = 1, FG_RECEDE = 2,
                    FG_STATIONARY = 3 };
enum fg_phase { FG_NO_ANCHOR = 0, FG_WARMUP = 1, FG_TRACKING = 2 };
enum fg_flags {
    FG_SCALE_VALID = 1u << 0,
    FG_IDENTITY_VALID = 1u << 1,
    FG_TIME_VALID = 1u << 2,
    FG_PROJECTION_STABLE = 1u << 3,
    FG_LENS_STATIONARY = 1u << 4,
    FG_AAT_SCALE_VALIDATED = 1u << 5
};
#define FG_REQUIRED_FLAGS 31u
enum fg_confirmation { FG_CDAF_CONFIRMED = 1u, FG_POSITION_VALID = 2u };
enum fg_reason {
    FG_OK = 0, FG_NEEDS_FOCUS = 1, FG_BAD_CONFIG = 2,
    FG_BAD_SAMPLE = 3, FG_STALE = 4, FG_OUT_OF_ORDER = 5,
    FG_CONTEXT_CHANGED = 6, FG_UNTRUSTED_SCALE = 7,
    FG_LENS_MOVING = 8, FG_ANCHOR_EXPIRED = 9,
    FG_GAP = 10, FG_SCALE_JUMP = 11, FG_TOO_FEW = 12,
    FG_TOO_SHORT = 13, FG_NOISY = 14, FG_CONFIRMATION_REJECTED = 15,
    FG_EXPLICIT_RESET = 16
};

typedef struct {
    uint32_t window_ms;              /* 20..1000 */
    uint32_t min_span_ms;            /* >0, <=window */
    uint32_t min_samples;            /* 3..FG_CAPACITY */
    uint32_t max_gap_ms;             /* >0, <=window */
    uint32_t max_age_ms;             /* <=1000 */
    uint32_t max_anchor_age_ms;      /* >window, <=600000 */
    uint32_t min_rate_q16;           /* relative depth rate / second, 1..65536 */
    uint32_t max_residual_q16;       /* max relative fit residual, 1..16384 */
    uint32_t max_scale_step_q16;     /* relative one-sample step, 1..32768 */
} FgConfig;

typedef struct {
    uint32_t now_ms;
    uint32_t sample_ms;
    uint32_t source_seq;             /* must advance within this context */
    uint32_t target_generation;
    uint32_t context_generation;     /* lens/readout/crop/scale convention */
    uint32_t scale_generation;       /* reset on face/AAT handover/reseed */
    uint32_t source_kind;
    uint32_t flags;
    uint32_t scale_q16;              /* 64..131072; normalized LINEAR size */
    int32_t lens_position;           /* diagnostic native domain, no inference */
} FgSample;

typedef struct {
    uint32_t phase;
    uint32_t direction;
    uint32_t reason;
    uint32_t anchor_valid;
    uint32_t sample_count;
    int32_t relative_rate_q16;       /* slope / mean reciprocal per second;
                                      positive=recede, negative=approach */
    uint32_t residual_q16;
    uint32_t confidence_permille;    /* heuristic, NOT probability */
    uint32_t anchor_age_ms;
    int32_t anchor_position;
    uint32_t anchor_scale_q16;
    uint32_t motion_authorized;     /* always zero in this development core */
} FgResult;

typedef struct {
    FgConfig config;
    uint32_t config_valid;
    uint32_t anchored;
    FgSample anchor;
    uint32_t focus_event_seq;
    uint32_t has_focus_event;
    uint32_t have_last;
    FgSample last;
    uint32_t count;
    uint32_t times[FG_CAPACITY];
    uint32_t reciprocal_q16[FG_CAPACITY]; /* anchor.scale/current.scale */
} FgState;

void fg_default_config(FgConfig *config);
/* Returns 1 on success, 0 for null/invalid config. Once per state lifetime;
 * this clears replay protection. Use fg_invalidate for operational resets. */
int fg_init(FgState *state, const FgConfig *config);
/* Clear anchor/history; retain confirmation and observation high-water marks. */
void fg_invalidate(FgState *state, uint32_t reason, FgResult *result);
/* Explicit optical confirmation only; never inferred from scale/lens settled.
 * Require both confirmation bits, fresh valid sample, newer focus_event_seq,
 * and no event older than the last accepted observation. On success reset
 * history and seed with this sample. A rejected confirmation must not create
 * or refresh an anchor; result remains UNKNOWN. */
void fg_confirm(FgState *state, const FgSample *sample,
                uint32_t focus_event_seq, uint32_t confirmation_flags,
                FgResult *result);
/* Requires an anchor. Any source/context change or invalid/untrusted sample
 * invalidates anchor except lens motion / short gaps, which clear trend only.
 * Old/duplicate timestamps OR sequences reject without mutating valid history.
 * A gap clears history; expiration or a discontinuous scale jump drops anchor.
 * Never infer current correction direction from the movement trend. */
void fg_update(FgState *state, const FgSample *sample, FgResult *result);

#endif
