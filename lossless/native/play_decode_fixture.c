/* Host substitutes for play_decode.c. One flat memory holds the player's
 * frame buffer -- NOT 1 KiB aligned, as measured on the camera -- the slot
 * describing it and the scratch the decoder takes at clip open. Every write
 * is checked against where it may land.
 *
 * The fake engine: a tile in the "stream" is {length, tile id} then filler.
 * Decoding a tile row writes, for every tile, `th` WHOLE rows (as the real
 * engine does: it overran a short buffer on the camera, 2026-10-01) but only
 * the columns inside the frame, each byte (id * 7 + row in tile + column). */
#include <string.h>
#include "play_decode.h"

#define MEM        0x50000000u
#define MEM_SIZE   (4u << 20)
#define SLOT       (MEM + 0x100u)
#define BUF        (MEM + 0x10780u)            /* not 1 KiB aligned */
#define SCRATCH    (MEM + 0x200000u)
#define ENGINE_BLK 0xc302cdd4u
#define TILE_VAR   0xc302cdecu

static uint8_t mem[MEM_SIZE];
static uint8_t first[FPL_PLAY_HEADER];
static uint32_t first_len, first_fails;
static uint32_t engine_blk[4], tile_var, oob, bad_writes, cap_bytes;
static uint32_t opens, closes, inits, submits, starts, waits, dmas, allocs, releases, syncs;
static uint32_t dma_fail_at, engine_fail, alloc_fail, misaligned_engine;
static uint32_t out_addr, stream_addr, rows_told, width_told, started;
static uint32_t tw_, th_, bits_, scratch_bytes;

static uint32_t *slot32(uintptr_t a) {
    if (a >= ENGINE_BLK && a < ENGINE_BLK + 16) return &engine_blk[(a - ENGINE_BLK) / 4];
    if (a == TILE_VAR) return &tile_var;
    if (a >= MEM && a + 4 <= MEM + MEM_SIZE && !(a & 3u)) return (uint32_t *)(void *)&mem[a - MEM];
    oob++;
    return 0;
}
static uint32_t rd(uintptr_t a) { uint32_t *s = slot32(a); return s ? *s : 0; }
/* The decoder may write: the buffer up to its capacity, the scratch it was
 * given, the engine variables. Nothing else. */
static uint32_t allowed(uintptr_t a, uint32_t n) {
    if (a >= BUF && a + n <= BUF + cap_bytes) return 1;
    if (scratch_bytes && a >= SCRATCH && a + n <= SCRATCH + scratch_bytes) return 1;
    return 0;
}
static void wr(uintptr_t a, uint32_t v) {
    uint32_t *s;
    if (a != TILE_VAR && !(a >= ENGINE_BLK && a < ENGINE_BLK + 16) && !allowed(a, 4))
        bad_writes++;
    if ((s = slot32(a))) *s = v;
}
static uint8_t *at(uintptr_t a) { return &mem[a - MEM]; }

static uintptr_t f_alloc(uint32_t *obj, uint32_t bytes) {
    if (alloc_fail || bytes > MEM_SIZE - (SCRATCH - MEM)) return 0;
    allocs++;
    obj[0] = SCRATCH;
    scratch_bytes = bytes;
    memset(at(SCRATCH), 0xEE, bytes);
    return SCRATCH;
}
static void f_release(uint32_t *obj) { releases++; obj[0] = 0; scratch_bytes = 0; }
static uint32_t f_dma(uintptr_t to, uintptr_t from, uint32_t n) {
    dmas++;
    if (dma_fail_at && dmas == dma_fail_at) return 5;
    if (!allowed(to, n) || from < MEM || from + n > MEM + MEM_SIZE) { bad_writes++; return 5; }
    memmove(at(to), at(from), n);
    return 0;
}
static uint32_t f_init(volatile uint32_t *p) {
    inits++;
    if (p[8] != 1) return 0;                        /* only the decode flag */
    engine_blk[0] = p[5]; engine_blk[1] = 0x1111; engine_blk[2] = p[6]; engine_blk[3] = 0x2222;
    width_told = p[0]; rows_told = p[1]; tw_ = p[3]; th_ = p[4];
    bits_ = p[2] == 0 ? 12 : p[2] == 1 ? 14 : p[2] == 2 ? 16 : 10;
    return 1;
}
static uint32_t f_flag(void) { return 77; }
static uint32_t f_clr(uint32_t f, uint32_t p) { (void)f; (void)p; return 0; }
static uint32_t f_open(void) { opens++; return 0; }
static uint32_t f_submit(volatile uint32_t *r) {
    submits++;
    if (r[11] != 0xc062fce1u || r[5] != engine_blk[0] || r[7] != engine_blk[2] ||
        r[0] != width_told || r[1] != rows_told) return 1;
    if ((r[5] | r[7]) & 0x3ffu) { misaligned_engine++; return 1; }
    out_addr = r[5]; stream_addr = r[7];
    return 0;
}
static uint32_t f_start(void) { starts++; started = 1; return 0; }
static uint32_t f_wait(uint32_t flag, uint32_t *pattern) {
    waits++;
    if (flag != 77 || !started) return 0xffffffceu;
    started = 0;
    if (engine_fail) return 0xffffffceu;
    uint32_t across = (width_told + tw_ - 1) / tw_, pitch = ((width_told * bits_ / 8) + 3) & ~3u;
    uintptr_t s = stream_addr;
    for (uint32_t t = 0; t < tile_var; ++t) {
        uint32_t len = rd(s), id = rd(s + 4);
        uint32_t tx = t % across, ty = t / across;
        uint32_t x0 = tx * tw_, x1 = x0 + tw_ > width_told ? width_told : x0 + tw_;
        uint32_t b0 = x0 * bits_ / 8, b1 = x1 * bits_ / 8;
        for (uint32_t y = 0; y < th_; ++y) {
            uintptr_t row = out_addr + (ty * th_ + y) * pitch;
            if (!allowed(row + b0, b1 - b0)) { bad_writes++; continue; }
            for (uint32_t b = b0; b < b1; ++b) *at(row + b) = (uint8_t)(id * 7 + y + b);
        }
        s += len;
    }
    *pattern = 2;
    return 0;
}
static uint32_t f_close(void) { closes++; return 0; }
static uint32_t f_now(void) { return 1000; }
static void f_sync(void) { syncs++; }
static uint32_t f_first(uintptr_t desc, uint32_t *file, uint8_t *buf, uint32_t bytes,
                        uint32_t *got) {
    (void)file;
    if (desc != 0xDE5C || first_fails) return 0;
    *got = first_len < bytes ? first_len : bytes;
    memcpy(buf, first, *got);
    return 1;
}

const struct fpl_play_natives fpl_play_test_natives = {
    rd, wr, f_alloc, f_release, f_dma, f_init, f_flag, f_clr, f_open, f_submit, f_start,
    f_wait, f_close, f_now, f_sync, f_first
};

static struct fpl_play play;

void fpl_fixture_reset(void) {
    memset(&play, 0, sizeof play);
    scratch_bytes = 0;
    alloc_fail = first_fails = 0;
}
/* The clip's first frame file, as C05BDD68 names it. */
uint32_t fpl_fixture_clip(const uint8_t *file, uint32_t len, uint32_t size, uint32_t busy) {
    first_len = len < sizeof first ? len : sizeof first;
    memcpy(first, file, first_len);
    allocs = releases = 0;
    return fpl_play_clip(&play, 0xDE5C, size, busy);
}
/* clip open then the player's pool, as the camera does: the old pool
 * freed first when the size changed, then made */
uint32_t fpl_fixture_open(const uint8_t *file, uint32_t len, uint32_t size, uint32_t busy) {
    uint32_t r = fpl_fixture_clip(file, len, size, busy);
    fpl_play_end(&play);
    fpl_play_pool(&play);
    return r;
}
void fpl_fixture_end(void) { fpl_play_end(&play); }
void fpl_fixture_pool(void) { fpl_play_pool(&play); }
/* A frame as the player leaves it: `file` read into BUF, capacity `cap`. */
void fpl_fixture_load(const uint8_t *file, uint32_t len, uint32_t cap) {
    memset(mem, 0xA5, SCRATCH - MEM);
    oob = bad_writes = opens = closes = inits = submits = starts = waits = 0;
    dmas = dma_fail_at = engine_fail = misaligned_engine = syncs = 0;
    started = 0;
    cap_bytes = cap;
    memcpy(at(BUF), file, len);
    *(uint32_t *)(void *)at(SLOT + 0x14) = BUF;
    *(uint32_t *)(void *)at(SLOT + 0x18) = cap;
    *(uint32_t *)(void *)at(SLOT + 0x1c) = len;
}
void fpl_fixture_knob(uint32_t k, uint32_t v) {
    switch (k) {
    case 0: dma_fail_at = v; break;
    case 1: engine_fail = v; break;
    case 2: alloc_fail = v; break;
    case 3: *(uint32_t *)(void *)at(SLOT + 0x14) = v; break;     /* buffer */
    case 4: first_fails = v; break;
    }
}
uint32_t fpl_fixture_run(uint32_t busy) { return fpl_play_frame(&play, SLOT, busy); }
uint32_t fpl_fixture_byte(uint32_t offset) { return mem[BUF - MEM + offset]; }
uint32_t fpl_fixture_get(uint32_t f) {
    if (f >= 100 && f < 100 + FPL_PLAY_REASONS) return play.refused_by[f - 100];
    switch (f) {
    case 0: return play.seen;
    case 1: return play.stock;
    case 2: return play.decoded;
    case 3: return oob;
    case 4: return bad_writes;
    case 5: return opens;
    case 6: return closes;
    case 7: return allocs;
    case 8: return releases;
    case 9: return dmas;
    case 10: return submits;
    case 11: return misaligned_engine;
    case 12: return play.scratch_bytes;
    case 13: return play.clips_ours;
    case 14: return syncs;
    case 15: return play.scratch_failed;
    case 16: return play.clip_failed;
    default: return 0xffffffffu;
    }
}
