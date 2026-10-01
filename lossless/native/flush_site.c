#include "flush_site.h"

#define WRITER_NODE   0x0cu
#define WRITER_HEAD   0x8cu
#define WRITER_COUNT  0x90u
#define NODE_BUFFER   0x04u
#define NODE_LENGTH   0x08u

#if defined(FPL_FLUSH_SITE_HOST_TEST)
#define F (&fpl_flush_test_natives)
#define peek(a) F->read(a)
#define poke(a, v) F->write(a, v)
#define uncached(a) F->uncached(a)
#define barrier() F->barrier()
static uint32_t rd(uintptr_t a) { return F->read(a); }
static void wr(uintptr_t a, uint32_t v) { F->write(a, v); }
#elif defined(__arm__) && UINTPTR_MAX == UINT32_MAX
#define peek(a) (*(volatile const uint32_t *)(a))
#define poke(a, v) (*(volatile uint32_t *)(a) = (v))
#define uncached(a) ((uintptr_t)(a) + 0x40000000u)
#define barrier() __asm__ volatile("dsb sy" ::: "memory")
static uint32_t rd(uintptr_t a) { return *(volatile const uint32_t *)a; }
static void wr(uintptr_t a, uint32_t v) { *(volatile uint32_t *)a = v; }
#else
#error "ARM32 native writer ABI required; host tests must explicitly substitute it"
#endif

static void saturate(uint32_t *v) { if (*v != UINT32_MAX) ++*v; }

uint32_t fpl_flush_before(uintptr_t writer, struct fpl_frame_hold *h,
                          struct fpl_frame_hold *h2, struct fpl_flush_stats *s) {
    const struct fpl_hold_commit *c;
    struct fpl_trailer t;
    uintptr_t node, buffer;
    uint32_t length, rounded;

    if (!s) return FPL_INVALID;
    saturate(&s->calls);
    /* The shape G3 checked and the 9/29 probe relied on. */
    if (!writer || (writer & 3u) || peek(writer + WRITER_COUNT) != 1 ||
        peek(writer + WRITER_HEAD) != writer + WRITER_NODE) {
        saturate(&s->shape);
        return FPL_OK;
    }
    node = writer + WRITER_NODE;
    buffer = peek(node + NODE_BUFFER);
    length = peek(node + NODE_LENGTH);
    if (buffer && !(buffer & 3u) && !fpl_hold_peek(h, buffer) && fpl_hold_peek(h2, buffer))
        h = h2;                          /* the second lane's promise */
    if (!buffer || (buffer & 3u) || !(c = fpl_hold_peek(h, buffer))) {
        saturate(&s->no_promise);
        return FPL_OK;
    }
    if (length != c->stock_bytes) {
        /* Not the whole stock file: some other write of this buffer. The
         * promise stays for the real one. */
        saturate(&s->length_mismatch);
        return FPL_OK;
    }
    if (fpl_trailer_write_all(uncached(buffer), c->payload, c->tile_bytes, c->tiles,
                              FPL_TILE_WIDTH, FPL_TILE_HEIGHT, c->capacity,
                              rd, wr, &t) != FPL_OK) {
        /* The payload is already in the raster and the header was refused:
         * this frame is lost either way. Recorded, consumed, not retried. */
        saturate(&s->trailer_failed);
        fpl_hold_consumed(h, c);
        return FPL_FAULT;
    }
    barrier();
    /* Rounded up to 1 KiB, as every length given to this writer has been:
     * the extra bytes are the old raster's, after the file's last table. */
    rounded = (t.file_bytes + 0x3ffu) & ~0x3ffu;
    if (rounded > c->capacity || rounded >= c->stock_bytes) rounded = t.file_bytes;
    poke(node + NODE_LENGTH, rounded);
    barrier();
    s->last_length = peek(node + NODE_LENGTH);
    fpl_hold_consumed(h, c);
    saturate(&s->applied);
    return FPL_OK;
}
