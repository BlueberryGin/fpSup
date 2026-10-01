#include "play_decode.h"

#define ROOT_STOCK      8u            /* IFD0, where the recorder put it */
#define TIFF_LE         0x002a4949u   /* "II*\0" */
#define MAX_TAGS        128u
#define MAX_TILES       160u          /* codec_job's FPL_CODEC_TILE_MAX */
#define MAX_ROWS        64u
#define MAX_TILE_H      0x200u        /* the engine's own bound */
#define WAIT_TICKS      200u          /* a 3K tile row takes ~4 ms */
#define ENGINE_BLOCK    0xc302cdd4u   /* what init latched: buffers, lengths */
#define TILE_COUNT_VAR  0xc302cdecu
#define DECODE_CALLBACK 0xc062fce1u   /* C062FAD0's own: sets flag bit 2 */
#define SLOT_BUFFER     0x14u
#define SLOT_CAPACITY   0x18u
#define SLOT_READ       0x1cu

#if defined(FPL_PLAY_DECODE_HOST_TEST)
#define N (&fpl_play_test_natives)
#define peek(a) N->read(a)
#define poke(a, v) N->write(a, v)
#define scratch_get(o, n) N->alloc(o, n)
#define scratch_put(o) N->release(o)
#define dma(t, f, n) N->dma(t, f, n)
#define engine_init(p) N->init(p)
#define engine_flag() N->flag()
#define engine_clr(f) N->clr(f, 0)
#define engine_open() N->open()
#define engine_submit(r) N->submit(r)
#define engine_start() N->start()
#define engine_wait(f, p) N->wait(f, p)
#define engine_close() N->close()
#define now_us() N->now()
#define cache_sync() N->sync()
#define first_frame(d, f, b, n, g) N->first_frame(d, f, b, n, g)
#elif defined(__arm__) && UINTPTR_MAX == UINT32_MAX
#define peek(a) (*(volatile const uint32_t *)(a))
#define poke(a, v) (*(volatile uint32_t *)(a) = (v))
typedef uint32_t (*fn0)(void);
typedef uint32_t (*fnp)(volatile uint32_t *);
static uintptr_t scratch_get(uint32_t *obj, uint32_t bytes) {
    typedef uint32_t (*get_fn)(uint32_t *, uint32_t, uint32_t, uint32_t, uint32_t);
    typedef uintptr_t (*addr_fn)(uint32_t *);
    ((get_fn)0xc001d740u)(obj, 10, bytes, 0x400u, 0);           /* the RAW pool */
    return ((addr_fn)0xc001d7f0u)(obj);
}
static void scratch_put(uint32_t *obj) {
    typedef void (*fn)(uint32_t *, uint32_t);
    ((fn)0xc001d7a0u)(obj, 2);
}
/* C00FBB90 {source, destination, bytes, flags 0}: synchronous, its own cache
 * maintenance and semaphore (frame_hold.c header_dma). */
static uint32_t dma(uintptr_t to, uintptr_t from, uint32_t bytes) {
    volatile uint32_t r[4];
    r[0] = (uint32_t)from;
    r[1] = (uint32_t)to;
    r[2] = bytes;
    r[3] = 0;
    return ((fnp)0xc00fbb91u)(r);
}
#define engine_init(p) ((fnp)0xc05a6890u)(p)                     /* ARM */
#define engine_flag() ((fn0)0xc062f441u)()
#define engine_clr(f) ((uint32_t (*)(uint32_t, uint32_t))0xc00169c8u)(f, 0)
#define engine_open() ((fn0)0xc062fe71u)()
#define engine_submit(r) ((fnp)0xc062fec1u)(r)                    /* C0630240 */
#define engine_start() ((fn0)0xc062fed9u)()                       /* C06303F8 */
static uint32_t engine_wait(uint32_t flag, uint32_t *pattern) {
    typedef uint32_t (*fn)(uint32_t, uint32_t, uint32_t, uint32_t *, uint32_t);
    return ((fn)0xc0016c08u)(flag, 2, 1, pattern, WAIT_TICKS);   /* twai_flg */
}
#define engine_close() ((fn0)0xc062fe81u)()
#define now_us() ((fn0)0xc002b6e0u)()
#define cache_sync() ((void)((fn0)0xc000e91cu)())   /* what the player calls
                                                       around its own read */
/* The clip's first frame, opened the way C05BDD68 names it: the descriptor's
 * volume (C069B930) and path (C069B9B8); the file API menu_page.c uses. */
static uint32_t first_frame(uintptr_t desc, uint32_t *file, uint8_t *buf, uint32_t bytes,
                            uint32_t *got) {
    typedef uint32_t (*d_fn)(uintptr_t);
    typedef void (*ctor_fn)(uint32_t *, uint32_t);
    typedef uint32_t (*open_fn)(uint32_t *, uint32_t, uint32_t);
    typedef uint32_t (*read_fn)(uint32_t *, uint8_t *, uint32_t, uint32_t *);
    typedef void (*close_fn)(uint32_t *);
    uint32_t ok;
    ((ctor_fn)0xc0365e90u)(file, ((d_fn)0xc069b930u)(desc));
    ok = ((open_fn)0xc0365fb0u)(file, ((d_fn)0xc069b9b8u)(desc), 1);
    if (ok) {
        ok = ((read_fn)0xc0366060u)(file, buf, bytes, got);
        ((close_fn)0xc0366020u)(file);
    }
    ((ctor_fn)0xc0365ed0u)(file, 2);                            /* destructor */
    return ok;
}
#else
#error "ARM32 native playback ABI required; host tests must explicitly substitute it"
#endif

static void saturate(uint32_t *v) { if (*v != UINT32_MAX) ++*v; }
static uint32_t kib(uint32_t v) { return (v + 0x3ffu) & ~0x3ffu; }
/* No division: there is no runtime library on the camera. Bounded operands. */
static uint32_t ceil_div(uint32_t a, uint32_t b) {
    uint32_t q = 0;
    while (a > 0) { ++q; a = a > b ? a - b : 0; }
    return q;
}

/* The player's buffer is read through its own mapping (what it will see);
 * our own copy of a first frame's header, `local`, directly. */
static uint32_t rd8(uintptr_t a, uint32_t local) {
    if (local) return *(const volatile uint8_t *)a;
    return (peek(a & ~(uintptr_t)3u) >> ((a & 3u) * 8u)) & 0xffu;
}
static uint32_t rd16(uintptr_t a, uint32_t l) { return rd8(a, l) | rd8(a + 1, l) << 8; }
static uint32_t rd32(uintptr_t a, uint32_t l) { return rd16(a, l) | rd16(a + 2, l) << 16; }

struct ifd {
    uint32_t w, h, bits, comp, tw, th;
    uint32_t toff_type, toff_n, toff, tcnt_type, tcnt_n, tcnt;
    uint32_t soff, scnt, soff_n, scnt_n;
};

/* One IFD of [base, base + got). 0: not readable. */
static uint32_t read_ifd(uintptr_t base, uint32_t got, uint32_t at, struct ifd *f,
                         uint32_t local) {
    uint32_t n;
    for (uint32_t k = 0; k < sizeof(*f) / 4u; ++k) ((uint32_t *)f)[k] = 0;
    if (got < 16u || (at & 1u) || at < 8u || at > got - 2u) return 0;
    n = rd16(base + at, local);
    if (!n || n > MAX_TAGS || 2u + 12u * n + 4u > got - at) return 0;
    for (uint32_t k = 0; k < n; ++k) {
        uintptr_t e = base + at + 2u + 12u * k;
        uint32_t tag = rd16(e, local), type = rd16(e + 2, local), count = rd32(e + 4, local);
        uint32_t v = type == 3 ? rd16(e + 8, local) : rd32(e + 8, local);
        switch (tag) {
        case 256: f->w = v; break;
        case 257: f->h = v; break;
        case 258: f->bits = v; break;
        case 259: f->comp = v; break;
        case 322: f->tw = v; break;
        case 323: f->th = v; break;
        case 324: f->toff_type = type; f->toff_n = count; f->toff = rd32(e + 8, local); break;
        case 325: f->tcnt_type = type; f->tcnt_n = count; f->tcnt = rd32(e + 8, local); break;
        case 273: f->soff = v; f->soff_n = count; break;
        case 279: f->scnt = v; f->scnt_n = count; break;
        default: break;
        }
    }
    return 1;
}

static uint32_t format_code(uint32_t bits) {
    switch (bits) {
    case 12: return 0;
    case 14: return 1;
    case 16: return 2;
    case 10: return 3;
    default: return 0xffu;
    }
}
static uint32_t pitch_of(uint32_t w, uint32_t bits) { return ((w * bits >> 3) + 3u) & ~3u; }

/* The recorder's raster (C0135D40) rounds its LAST row up to 12 pixels: at
 * FHD 1936 wide that row is 2916 B, not 2904, and the strip 12 B longer than
 * rows x pitch (2026-10-01: an FHD clip was taken for not ours). The extra is
 * padding past the frame; nothing decoded lands in it. */
#define LAST_ROW_SLACK 64u
static uint32_t strip_bytes(const struct ifd *f0) {
    uint32_t rows = pitch_of(f0->w, f0->bits) * f0->h;
    return f0->scnt > rows ? f0->scnt : rows;
}

/* IFD0 of a frame: the stock strip of a frame we know how to decode. */
static uint32_t stock_strip(const struct ifd *f0) {
    return f0->comp == 1 && format_code(f0->bits) != 0xffu && f0->w >= 8 &&
           f0->w <= 0x4000 && !(f0->w & 7u) && f0->h >= 2 && f0->h <= 0x4000 &&
           !(f0->h & 1u) && f0->soff_n == 1 && f0->scnt_n == 1 && f0->soff >= 8u &&
           f0->scnt && f0->scnt <= pitch_of(f0->w, f0->bits) * f0->h + LAST_ROW_SLACK;
}

/* ---- clip open ---------------------------------------------------------- */
void fpl_play_end(struct fpl_play *p) {
    if (!p || !p->scratch) return;
    scratch_put(p->obj);
    p->scratch = 0;
    p->scratch_bytes = 0;
    saturate(&p->scratch_freed);
}

uint32_t fpl_play_clip(struct fpl_play *p, uintptr_t desc, uint32_t size, uint32_t busy) {
    struct ifd f0;
    uint32_t got = 0, want, stock, need;
    uintptr_t h;
    if (!p) return size;
    saturate(&p->clips);
    p->clip_size_was = size;
    if (busy || !desc) return size;
    for (uint32_t n = 0; n < FPL_PLAY_HEADER / 4u; ++n) p->header[n] = 0;
    if (!first_frame(desc, p->file, (uint8_t *)p->header, FPL_PLAY_HEADER, &got) ||
        got < 16u || got > FPL_PLAY_HEADER) {
        saturate(&p->clip_failed);
        return size;
    }
    h = (uintptr_t)p->header;
    if (rd32(h, 1) != TIFF_LE || rd32(h + 4, 1) == ROOT_STOCK) return size;   /* stock */
    if (!read_ifd(h, got, ROOT_STOCK, &f0, 1) || !stock_strip(&f0)) {
        saturate(&p->clip_failed);
        return size;
    }
    saturate(&p->clips_ours);
    /* the stock frame file: header, then the strip padded to 512 B */
    stock = f0.soff + ((f0.scnt + 0x1ffu) & ~0x1ffu);
    want = stock > size ? stock : size;
    p->clip_size_set = want;
    /* one tile row's stream and output, for the tallest tile the engine takes */
    need = 2u * kib(pitch_of(f0.w, f0.bits) * MAX_TILE_H);
    if (p->scratch && p->scratch_bytes < need) fpl_play_end(p);
    if (!p->scratch) {
        p->scratch = scratch_get(p->obj, need);
        if (!p->scratch || (p->scratch & 0x3ffu)) {
            if (p->scratch) scratch_put(p->obj);
            p->scratch = 0;
            saturate(&p->scratch_failed);
        } else {
            p->scratch_bytes = need;
        }
    }
    return want;
}

/* ---- a frame -------------------------------------------------------------- */
/* C062FAD0's steps for ONE tile row, the height given as the tile's: init
 * with the decode flag, the request, OPEN, SUBMIT, START, wait for bit 2,
 * CLOSE. 0 = decoded. */
static uint32_t decode_row(const struct ifd *f, uint32_t across, uintptr_t out,
                           uintptr_t stream) {
    volatile uint32_t init[9], req[12];
    uint32_t flag, pattern = 0, ok;
    init[0] = f->w; init[1] = f->th; init[2] = format_code(f->bits);
    init[3] = f->tw; init[4] = f->th;
    init[5] = (uint32_t)out;
    init[6] = (uint32_t)stream;
    init[7] = 0;
    init[8] = 1;                                     /* decode */
    if (engine_init(init) != 1) return 1;
    for (uint32_t n = 0; n < 12; ++n) req[n] = 0;
    req[0] = f->w; req[1] = f->th; req[2] = format_code(f->bits);
    req[3] = f->tw; req[4] = f->th;
    for (uint32_t n = 0; n < 4; ++n) req[5 + n] = peek(ENGINE_BLOCK + 4u * n);
    req[11] = DECODE_CALLBACK;
    flag = engine_flag();
    if ((int32_t)flag < 0) return 1;
    poke(TILE_COUNT_VAR, across);
    engine_clr(flag);
    if (engine_open() != 0) return 1;
    ok = engine_submit(req) == 0 && engine_start() == 0 &&
         engine_wait(flag, &pattern) == 0 && (pattern & 2u);
    engine_clr(flag);
    if (engine_close() != 0) ok = 0;
    return ok ? 0 : 1;
}

static uint32_t refuse(struct fpl_play *p, uint32_t why) { saturate(&p->refused_by[why]); return why; }

uint32_t fpl_play_frame(struct fpl_play *p, uintptr_t slot, uint32_t busy) {
    struct ifd f, f0;
    uintptr_t buf, strip, s_at, o_at;
    uint32_t cap, got, root, across, down, tiles, end, pitch, raw_row, rem, t0;
    uint32_t row_at[MAX_ROWS + 1];

    if (!p || !slot) return FPL_PLAY_R_SLOT;
    saturate(&p->seen);
    buf = peek(slot + SLOT_BUFFER);
    cap = peek(slot + SLOT_CAPACITY);
    got = peek(slot + SLOT_READ);
    p->last_buf = (uint32_t)buf; p->last_cap = cap; p->last_got = got;
    if (!buf || (buf & 3u) || buf + cap < buf) return refuse(p, FPL_PLAY_R_SLOT);
    if (got < 16u || got > cap || rd32(buf, 0) != TIFF_LE) return refuse(p, FPL_PLAY_R_TIFF);
    root = rd32(buf + 4, 0);
    if (root == ROOT_STOCK) { saturate(&p->stock); return FPL_PLAY_STOCK; }
    if (busy) return refuse(p, FPL_PLAY_R_BUSY);

    /* ---- the trailer: ours, and every tile inside what was read --------- */
    if (!read_ifd(buf, got, root, &f, 0)) return refuse(p, FPL_PLAY_R_ROOT);
    if (f.comp != 7 || format_code(f.bits) == 0xffu ||
        f.w < 8 || f.w > 0x4000 || (f.w & 7u) || f.h < 2 || f.h > 0x4000 || (f.h & 1u) ||
        f.tw < 0x20 || f.tw > 0x200 || (f.tw & 0x1fu) ||
        f.th < 2 || f.th > MAX_TILE_H || (f.th & 1u))
        return refuse(p, FPL_PLAY_R_FORMAT);
    across = ceil_div(f.w, f.tw);
    down = ceil_div(f.h, f.th);
    tiles = across * down;
    if (tiles > MAX_TILES || down > MAX_ROWS || f.toff_n != tiles || f.tcnt_n != tiles ||
        tiles < 2 || f.toff_type != 4 || f.tcnt_type != 4 ||
        f.toff > got - 4u * tiles || f.tcnt > got - 4u * tiles)
        return refuse(p, FPL_PLAY_R_TILES);
    end = rd32(buf + f.toff, 0);
    for (uint32_t k = 0, col = 0, row = 0; k < tiles; ++k) {   /* no division */
        uint32_t o = rd32(buf + f.toff + 4u * k, 0), n = rd32(buf + f.tcnt + 4u * k, 0);
        if (o != end || !n || n > got || o > got - n) return refuse(p, FPL_PLAY_R_TILES);
        if (col == 0) row_at[row] = o;
        if (++col == across) { col = 0; ++row; }
        end = o + n;
    }
    row_at[down] = end;

    /* ---- IFD0: the stock strip the pixels go back into ----------------- */
    if (!read_ifd(buf, got, ROOT_STOCK, &f0, 0) || !stock_strip(&f0) || f0.w != f.w ||
        f0.h != f.h || f0.bits != f.bits || f0.soff > row_at[0])
        return refuse(p, FPL_PLAY_R_IFD0);
    pitch = pitch_of(f.w, f.bits);
    if (f0.soff > cap || cap - f0.soff < strip_bytes(&f0)) return refuse(p, FPL_PLAY_R_ROOM);
    strip = buf + f0.soff;
    raw_row = pitch * f.th;
    /* Bottom up is safe only if no tile row's output reaches a stream above
     * it: the streams of rows 0..r-1 end where row r's raw output begins. */
    for (uint32_t r = 1; r <= down; ++r)
        if (row_at[r] - f0.soff > (r < down ? r * raw_row : pitch * f.h))
            return refuse(p, FPL_PLAY_R_ORDER);
    if (!p->scratch || p->scratch_bytes < 2u * kib(raw_row))
        return refuse(p, FPL_PLAY_R_SCRATCH);
    o_at = p->scratch;
    s_at = p->scratch + kib(raw_row);
    for (uint32_t r = 0; r < down; ++r)
        if (row_at[r + 1] - row_at[r] > p->scratch_bytes - kib(raw_row))
            return refuse(p, FPL_PLAY_R_SCRATCH);

    rem = f.h - (down - 1u) * f.th;                  /* rows in the last tile row */
    t0 = now_us();
    for (uint32_t r = down; r-- > 0;) {
        if (dma(s_at, buf + row_at[r], row_at[r + 1] - row_at[r]) != 0)
            return refuse(p, FPL_PLAY_R_COPY);
        if (decode_row(&f, across, o_at, s_at) != 0) return refuse(p, FPL_PLAY_R_ENGINE);
        if (dma(strip + r * raw_row, o_at, (r == down - 1u ? rem : f.th) * pitch) != 0)
            return refuse(p, FPL_PLAY_R_COPY);
    }
    /* The file is now the stock one: its root back on IFD0. */
    poke(buf + 4, ROOT_STOCK);
    cache_sync();
    p->last_us = now_us() - t0;
    if (p->last_us > p->max_us) p->max_us = p->last_us;
    saturate(&p->decoded);
    return FPL_PLAY_DECODED;
}
