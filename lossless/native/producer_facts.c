#include "producer_facts.h"

/* Where each fact comes from, all Ver.5.02 [C]:
 *
 *   settings  = C0021C40(C0021C00())      current capture settings; C0021C00
 *                                         returns the boot-initialised
 *                                         singleton 0xC30751D4, C0021C40 reads
 *                                         its +0x5C. Both pure reads.
 *   C0437140(&obj, 2, settings, 3)        the LiveViewState descriptor the
 *                                         kind-1 creator C0374268 copies into
 *                                         every frame at +0x10 (C02E5B78, 18
 *                                         words). Refcounted: released by
 *                                         C022EDB0(&obj, 2), which calls the
 *                                         object's vtable and frees nothing.
 *   obj[0], obj[1], obj[8]                width, height and Sigpro format:
 *                                         the three fields C03D9630 -- the
 *                                         registered raster-size function --
 *                                         reads from that same descriptor.
 *   C0135D40(w, h, format)                the raster size in bytes. Formats
 *                                         0..4 are packed 12, 14, 16, 10, 8
 *                                         bit (C0135DB8), which is also the
 *                                         codec's depth-code order.
 *   settings[0x14] -> C00C9BD0            the frame rate the CinemaDNG
 *                                         FrameRate tag (0xC764) is written
 *                                         from.
 *
 * Not found in the producer, so taken from the caller and refused when
 * unknown: CINE mode, CinemaDNG (rather than MOV) and SD media.
 *
 * UNVERIFIED: that C0437140 is safe to call from the task that runs REC
 * preparation. The creator calls it from the Movie task. Serialisation with a
 * concurrent live-view reconfiguration is the caller's contract. */

#if defined(FPL_PRODUCER_FACTS_HOST_TEST)
extern uintptr_t fpl_test_settings(void);
extern void fpl_test_query(uintptr_t *, uint32_t, uintptr_t, uint32_t);
extern void fpl_test_release(uintptr_t *, uint32_t);
extern uint32_t fpl_test_raster(uint32_t, uint32_t, uint32_t);
extern void fpl_test_rate(uint32_t *);
#define native_settings fpl_test_settings
#define native_query fpl_test_query
#define native_release fpl_test_release
#define native_raster fpl_test_raster
#define native_rate fpl_test_rate
#elif defined(__arm__) && UINTPTR_MAX == UINT32_MAX
static uintptr_t native_settings(void) {
    typedef uintptr_t (*get)(void);
    typedef uintptr_t (*current)(uintptr_t);
    return ((current)0xc0021c40u)(((get)0xc0021c00u)());
}
static void native_query(uintptr_t *obj, uint32_t kind, uintptr_t settings,
                         uint32_t which) {
    typedef void (*fn)(uintptr_t *, uint32_t, uintptr_t, uint32_t);
    ((fn)0xc0437140u)(obj, kind, settings, which);
}
static void native_release(uintptr_t *obj, uint32_t flags) {
    typedef void (*fn)(uintptr_t *, uint32_t);
    ((fn)0xc022edb0u)(obj, flags);
}
static uint32_t native_raster(uint32_t width, uint32_t height, uint32_t format) {
    typedef uint32_t (*fn)(uint32_t, uint32_t, uint32_t);
    return ((fn)0xc0135d41u)(width, height, format);   /* Thumb */
}
static void native_rate(uint32_t *out) {
    typedef void (*fn)(uint32_t *);
    ((fn)0xc00c9bd0u)(out);
}
#else
#error "ARM32 native producer ABI required; host tests must explicitly substitute it"
#endif

#define KIND_CINEMADNG_IMAGE 2u     /* C0374268, kind-1 branch */
#define LIVEVIEW_TABLE 3u
#define RELEASE_REF_ONLY 2u         /* bit 0 clear: do not free the holder */
#define RATE_CODE_OFFSET 0x14u

static void facts_zero(volatile unsigned char *p, uint32_t bytes) {
    while (bytes--) *p++ = 0;
}
/* Word by word through volatile: a plain struct assignment or copy loop is
 * turned into __aeabi_memcpy4 by the compiler, and there is no runtime
 * library on the camera side to provide it. */
static void facts_copy(volatile uint32_t *to, const volatile uint32_t *from,
                       uint32_t words) {
    while (words--) *to++ = *from++;
}

/* C0135DB8's plain packed formats. Anything with 0x10000 or 0x20000 set is a
 * Sigpro-converted layout, not a raster this codec path takes. */
static uint32_t format_bits(uint32_t format) {
    switch (format) {
    case 0: return 12;
    case 1: return 14;
    case 2: return 16;
    case 3: return 10;
    case 4: return 8;
    default: return 0;
    }
}

/* C00C9BD0's explicit cases. Every other code falls to its default and is
 * labelled 30000/1001 -- a guess, so a code outside this set is refused
 * rather than trusted. Code 4 is explicit and also 30000/1001. */
static uint32_t rate_code_known(uint32_t code) {
    return (code >= 1 && code <= 4) || (code >= 6 && code <= 10);
}

static uint32_t probe(fpl_facts_probe fn, void *context, uint32_t *out) {
    uint32_t value;
    if (!fn) return FPL_NOT_READY;
    value = fn(context);
    if (value > 1) return FPL_NOT_READY;
    *out = value;
    return FPL_OK;
}

uint32_t fpl_producer_facts_init(struct fpl_producer_facts *f) {
    if (!f) return FPL_INVALID;
    const unsigned char *bytes = (const unsigned char *)f;
    for (uint32_t n = 0; n < sizeof(*f); ++n)
        if (bytes[n]) return FPL_INVALID;
    f->magic = FPL_FACTS_MAGIC;
    return FPL_OK;
}

static uint32_t facts_fail(struct fpl_producer_facts *f, struct fpl_context *out,
                           uint32_t result) {
    out->ready = 0;
    f->last_result = result;
    return result;
}

uint32_t fpl_producer_facts_read(void *facts, uint32_t reserved_bytes,
                                 struct fpl_context *out) {
    struct fpl_producer_facts *f = (struct fpl_producer_facts *)facts;
    struct fpl_facts_seen seen;
    uintptr_t settings, obj = 0;
    uint32_t rate[2] = {0, 0}, result, bits;
    const uint32_t *descriptor;

    if (!out) return FPL_INVALID;
    facts_zero((volatile unsigned char *)out, sizeof(*out));
    if (!f || f->magic != FPL_FACTS_MAGIC) return FPL_INVALID;
    f->reads++;
    facts_zero((volatile unsigned char *)&seen, sizeof(seen));

    settings = native_settings();
    if (!settings || (settings & 3u)) return facts_fail(f, out, FPL_NOT_READY);

    native_query(&obj, KIND_CINEMADNG_IMAGE, settings, LIVEVIEW_TABLE);
    if (!obj || (obj & 3u)) {
        native_release(&obj, RELEASE_REF_ONLY);
        return facts_fail(f, out, FPL_NOT_READY);
    }
    descriptor = (const uint32_t *)obj;
    facts_copy(seen.descriptor, descriptor, FPL_FACTS_DESCRIPTOR_WORDS);
    /* Nothing below reads the object: its reference is returned first. */
    native_release(&obj, RELEASE_REF_ONLY);

    seen.width = seen.descriptor[0];
    seen.height = seen.descriptor[1];
    seen.format = seen.descriptor[8];
    bits = format_bits(seen.format);
    if (!seen.width || !seen.height || !bits)
        return facts_fail(f, out, FPL_UNSUPPORTED);
    seen.bits = bits;
    seen.raster = native_raster(seen.width, seen.height, seen.format);
    if (!seen.raster) return facts_fail(f, out, FPL_UNSUPPORTED);

    seen.rate_code = *(const volatile uint32_t *)(settings + RATE_CODE_OFFSET);
    if (!rate_code_known(seen.rate_code)) return facts_fail(f, out, FPL_UNSUPPORTED);
    native_rate(rate);
    if (!rate[0] || !rate[1]) return facts_fail(f, out, FPL_UNSUPPORTED);
    seen.fps_num = rate[0];
    seen.fps_den = rate[1];

    if ((result = probe(f->cine, f->probe_context, &out->cine)) != FPL_OK ||
        (result = probe(f->cinemadng, f->probe_context, &out->compression)) != FPL_OK ||
        (result = probe(f->sd_media, f->probe_context, &out->media)) != FPL_OK)
        return facts_fail(f, out, result);

    out->firmware = 502;            /* this file is built for 5.02 addresses */
    out->bits = seen.bits;
    out->width = seen.width;
    out->height = seen.height;
    out->fps_num = seen.fps_num;
    out->fps_den = seen.fps_den;
    /* Readiness is someone else's proof. None supplied means none held. */
    out->ready = f->readiness ? f->readiness(f->readiness_context, reserved_bytes) : 0;
    facts_copy((volatile uint32_t *)&f->seen, (const uint32_t *)&seen,
               sizeof(seen) / sizeof(uint32_t));
    f->last_result = FPL_OK;
    return FPL_OK;
}

uint32_t fpl_producer_facts_match(const struct fpl_producer_facts *f,
                                  const uint32_t *frame_descriptor) {
    /* Only the format: width, height and Sigpro format -- the three words the
     * registered raster-size function C03D9630 reads. The rest of the 18 are
     * not all format: on the camera (2026-09-30) word 15 of every frame's copy
     * held that frame's own buffer address (0x54136800) where the LiveViewState
     * object read at REC preparation holds 0, so requiring all eighteen turned
     * every one of 133 frames away. */
    if (!f || f->magic != FPL_FACTS_MAGIC || !frame_descriptor || !f->seen.width)
        return 0;
    return frame_descriptor[0] == f->seen.descriptor[0] &&
           frame_descriptor[1] == f->seen.descriptor[1] &&
           frame_descriptor[8] == f->seen.descriptor[8];
}
