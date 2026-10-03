#ifndef FPLOSSLESS_CODEC_JOB_H
#define FPLOSSLESS_CODEC_JOB_H
#include <stdint.h>
#include "../control.h"

#define FPL_CODEC_JOB_MAGIC 0x424f4a43u
#define FPL_CODEC_TILE_MAX 160u

/* The firmware's own encoder, C062F6F8 (reached through F_ENC C05A6920),
 * cut in two at its one wait.
 *
 *   submit  F_INIT, flag, tile count, request, clr_flg, OPEN, memset the
 *           band's table, SUBMIT, soft-reset pulse, START      -> returns
 *   poll    twai_flg with no wait; not set -> FPL_BUSY
 *   finish  clr_flg, error bit -> CLOSE and fail; else EOI fix (C062F6C0),
 *           tile fix (C062FCF8), total (C062F4E0), zero-pad to 1 KiB and
 *           add the pad to the last tile, copy the band table, CLOSE
 *
 * Every step is the firmware's, called with the arguments it passes, in the
 * order it calls them. The one addition is the split itself.
 *
 * Only single-band (one "stripe") frames are taken: C062F478 bands a frame
 * whose tile row exceeds 64 MiB of source, and nothing up to 6064x4042 at 16
 * bit comes near that. A multi-band frame is refused, not approximated. */
enum fpl_codec_phase {
    FPL_CODEC_IDLE = 0, FPL_CODEC_RUNNING = 1, FPL_CODEC_DONE = 2,
    FPL_CODEC_FAILED = 3
};

struct fpl_codec_job {
    uint32_t magic, phase, last_native, polls;
    uint32_t width, height, format, depth, tiles, band_height;
    uintptr_t source, destination, table, band_table;
    uint32_t source_bytes, destination_bytes, table_bytes;
    uint32_t source_capacity;           /* what the caller vouched readable */
    uint32_t flag, pattern, end_position, total, padded;
    uint32_t request[12];
    /* the engine's registers when a job was given up on (fpl_codec_job_abort):
     * 300D0000, 04, 08, 0C, 64, 74, F8 (end position), 3FC */
    uint32_t stall_regs[8];
};

/* Everything the caller vouches for. `source_capacity` is how many bytes are
 * readable from `source` -- the frame buffer's own allocation, not the
 * raster: the engine is told a 1 KiB-rounded length and may read that far.
 * `destination_capacity` is what the engine may write: one raw frame, per the
 * design, so work that will not fit is refused by the engine rather than
 * overrunning. `table`/`table_capacity` hold the size table AND the band's
 * temporary copy that follows it, as C062F6F8 lays them out. */
struct fpl_codec_input {
    uint32_t width, height, format;          /* Sigpro format 0..3 */
    uintptr_t source, destination, table;
    uint32_t source_capacity, destination_capacity, table_capacity;
};

#if defined(FPL_CODEC_JOB_HOST_TEST)
/* Host tests substitute every firmware call through this table. */
struct fpl_codec_natives {
    uint32_t (*init)(const uint32_t *);
    uint32_t (*flag)(void);
    uint32_t (*clr_flg)(uint32_t, uint32_t);
    uint32_t (*twai_flg)(uint32_t, uint32_t, uint32_t, uint32_t *, uint32_t);
    uint32_t (*open)(void);
    uint32_t (*submit)(const uint32_t *);
    void (*reset)(void);
    uint32_t (*start)(void);
    uint32_t (*close)(void);
    void (*eoi)(uintptr_t, uint32_t);
    void (*tiles)(uintptr_t, uintptr_t, uint32_t);
    uint32_t (*total)(uintptr_t, uint32_t);
    uint32_t (*read)(uintptr_t);
    void (*write)(uintptr_t, uint32_t);
};
extern const struct fpl_codec_natives fpl_codec_test_natives;
#endif

uint32_t fpl_codec_job_init(struct fpl_codec_job *);
/* The bytes the engine will be told it may read, for a caller that wants to
 * check its buffer before submitting. 0 means the shape is refused. */
uint32_t fpl_codec_source_bytes(uint32_t width, uint32_t height, uint32_t format);
uint32_t fpl_codec_job_submit(struct fpl_codec_job *, const struct fpl_codec_input *);
/* FPL_BUSY while the engine runs; FPL_OK once finished and fixed up (total and
 * padded are then the payload); FPL_UNSUPPORTED when the engine refused the
 * frame (its error bit: output would not fit) -- closed, reusable, send RAW;
 * FPL_FAULT when the wait failed any other way or a close failed -- state
 * unknown, so the job and its buffers stay held. */
uint32_t fpl_codec_job_poll(struct fpl_codec_job *);
/* A job the engine never finished (2026-10-02: on the camera one job in a long
 * take never signalled, and everything after it went out uncompressed). Its
 * registers are kept in stall_regs, then CLOSE -- power off, the block reset,
 * the interrupt off -- stops it, and the job is IDLE again: its source was only
 * read, its output is not used. FPL_FAULT if CLOSE failed (state unknown). */
uint32_t fpl_codec_job_abort(struct fpl_codec_job *);
#endif
