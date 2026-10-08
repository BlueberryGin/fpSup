#ifndef FP_FOCUS_CONFIRM_JOIN_H
#define FP_FOCUS_CONFIRM_JOIN_H

#include "geometry.h"

/* Offline/shadow pairing, not a hardware adapter or optical validator.
 * One serialized consumer owns the state. All events share a uint32 ms clock
 * and contiguous stream_seq assigned by an ordering/loss-detecting transport.
 * Source hooks have not yet established that contract on the camera.
 * Never derive the supplied validity assertions from a cached value alone. */
#define FJ_VERSION 1u
enum fj_kind {
    FJ_BEGIN = 1, FJ_DRIVE_BEGIN = 2, FJ_AF_RESULT = 3,
    FJ_DRIVE_END = 4, FJ_SUBJECT = 5, FJ_CANCEL = 6,
    FJ_FAULT = 7, FJ_TICK = 8
};
enum fj_flag {
    FJ_SUPPORTED_PATH = 1u, FJ_CONNECTED = 2u,
    FJ_FINAL_END = 4u, FJ_AF_ACCEPTED = 8u,
    FJ_POSITION_VALID = 16u, FJ_SETTLE_VALIDATED = 32u,
    FJ_CANCELLED = 64u, FJ_FAULTED = 128u, FJ_NOOP = 256u
};
enum fj_phase {
    FJ_IDLE = 0, FJ_WAIT_DRIVE = 1, FJ_WAIT_PAIR = 2,
    FJ_WAIT_SUBJECT = 3, FJ_CONSUMED = 4, FJ_BLOCKED = 5
};
enum fj_reason {
    FJ_OK = 0, FJ_NEEDS_BEGIN = 1, FJ_BAD_CONFIG = 2,
    FJ_BAD_EVENT = 3, FJ_OLD_EVENT = 4, FJ_LOST_EVENT = 5,
    FJ_STALE_EVENT = 6, FJ_WRONG_EPISODE = 7, FJ_CONTEXT_CHANGED = 8,
    FJ_WRONG_DRIVE = 9, FJ_WAITING = 10, FJ_INTERMEDIATE_END = 11,
    FJ_RESULT_FAILED = 12, FJ_CANCEL_REASON = 13, FJ_FAULT_REASON = 14,
    FJ_TIMEOUT = 15, FJ_NOOP_UNSUPPORTED = 16, FJ_UNTRUSTED_POSITION = 17,
    FJ_UNTRUSTED_SUBJECT = 18, FJ_ALREADY_CONSUMED = 19,
    FJ_RESET_REASON = 20
};

typedef struct {
    uint32_t max_episode_ms;       /* 20..600000 */
    uint32_t max_event_age_ms;     /* 0..1000 */
    uint32_t max_pair_gap_ms;      /* 1..max_episode_ms */
    uint32_t max_sample_age_ms;    /* 0..1000 */
    uint32_t max_position_age_ms;  /* 1..max_episode_ms */
} FjConfig;

typedef struct {
    uint32_t kind;
    uint32_t stream_seq;
    uint32_t event_ms;
    uint32_t now_ms;
    uint32_t episode_generation;
    uint32_t drive_generation;
    uint32_t target_generation;
    uint32_t context_generation;
    uint32_t scale_generation;
    uint32_t source_kind;
    uint32_t flags;
    uint32_t error_code;           /* AF result accepts exactly zero */
    uint32_t position_ms;          /* origin of end_position, not read time */
    int32_t end_position;
    FgSample sample;               /* used only by FJ_SUBJECT */
} FjEvent;

typedef struct {
    uint32_t candidate_ready;      /* one-shot, only a fresh FJ_SUBJECT can set */
    uint32_t phase;
    uint32_t reason;
    uint32_t episode_generation;
    uint32_t drive_generation;
    uint32_t focus_event_seq;      /* incremented only for a new candidate */
    uint32_t confirmation_authorized; /* ALWAYS ZERO: shadow candidate */
    uint32_t motion_authorized;       /* ALWAYS ZERO */
    FgSample candidate;
} FjResult;

typedef struct {
    FjConfig config;
    uint32_t config_valid;
    uint32_t phase;
    uint32_t have_stream;
    uint32_t last_stream_seq;
    uint32_t last_event_ms;
    uint32_t last_now_ms;
    uint32_t have_episode;
    uint32_t episode_generation;
    uint32_t begin_ms;
    uint32_t target_generation;
    uint32_t context_generation;
    uint32_t scale_generation;
    uint32_t source_kind;
    uint32_t have_drive_highwater;
    uint32_t drive_highwater;
    uint32_t drive_generation;
    uint32_t drive_begin_ms;
    uint32_t have_result;
    uint32_t result_ms;
    uint32_t have_end;
    uint32_t end_ms;
    uint32_t position_ms;
    int32_t end_position;
    uint32_t focus_event_seq;
} FjState;

void fj_default_config(FjConfig *config);
/* Returns 1/0. Initialize once; operational resets retain replay protection. */
int fj_init(FjState *state, const FjConfig *config);
void fj_reset(FjState *state, FjResult *result);
void fj_step(FjState *state, const FjEvent *event, FjResult *result);

/* Contract:
 * - Nonzero generations, supported FACE/AAT source; BEGIN requires supported
 *   path + connected. Episode and drive generations advance modulo uint32,
 *   delta in (0,2^31), independently of transport stream_seq.
 * - Old/duplicate stream_seq is ignored without mutating state. Forward gaps,
 *   stale/backward clocks, mismatched episode/context/drive, faults, cancel,
 *   invalid data or expiry BLOCK and require a NEW episode BEGIN. Consume the
 *   new sequence on a forward gap/invalid event so it cannot revive state.
 *   BEGIN may recover BLOCKED only with a newer episode and a contiguous event.
 * - TICK checks deadlines; it has no identity/drive/flags requirements.
 * - DRIVE_BEGIN requires supported+connected and a newer drive generation.
 *   It drops prior result/end within the active episode (retry/new final move).
 * - AF_RESULT requires matching drive, supported+connected, error==0 and
 *   AF_ACCEPTED, with no cancelled/fault/noop bit. No-op is unsupported and
 *   never borrows a prior drive end. Repeated AF_RESULT/FINAL_END for the same
 *   drive are ignored (WAITING) without refreshing stored time/position.
 * - DRIVE_END without FINAL_END is intermediate (e.g. backlash): do not store
 *   it. Final end requires supported+connected+POSITION_VALID+SETTLE_VALIDATED.
 *   position_ms must be between drive begin and end, with bounded age.
 *   SETTLE_VALIDATED is an explicit input assertion, never a timed sleep here.
 * - Result and final end may arrive in either order for exactly one drive;
 *   their time separation must fit max_pair_gap_ms, as must waiting after the
 *   first of them. SUBJECT before both is ignored (WAITING), never buffered.
 * - SUBJECT after both must match all generations and source, event_ms equals
 *   sample.sample_ms, now_ms equals sample.now_ms, sample time >= both result
 *   and end, FgSample flags fully valid (AAT additionally validated), age bounded,
 *   and lens_position equals the fresh verified end_position. No correction or
 *   size normalization is performed. Emit only ONE candidate per episode.
 * - A consumed episode cannot emit again, even after a new drive. New drive
 *   blocks it; only newer BEGIN can rearm. No function calls fg_confirm or
 *   issues a lens command. Hardware confirmation requires a separate verified
 *   adapter; only synthetic replay may explicitly simulate its acceptance.
 * - Confirmation deadlines apply only while a candidate is pending. CONSUMED
 *   retains stream/time freshness, identity and drive guards; geometry.c owns
 *   the already established anchor's lifetime independently.
 * - All modular time intervals are <2^31; equal millisecond times are allowed.
 */
#endif
