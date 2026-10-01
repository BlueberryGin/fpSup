/* Host harness for trailer.c: the file lives in a caller-owned byte buffer,
 * mapped at a fake word-aligned address. Every access trailer.c makes is
 * checked to be word aligned and inside the buffer -- the property that
 * keeps the 2026-09-29 Data Abort from coming back. */
#include <stdint.h>
#include <string.h>
#include "trailer.h"

#define BASE 0x50000000u
static uint8_t *buffer;
static uint32_t size, unaligned, outside, reads, writes;
static uintptr_t last_write;
static uint32_t first_root_write;

static uint32_t rd(uintptr_t a) {
    reads++;
    if (a & 3u) unaligned++;
    if (a < BASE || a + 4 > BASE + size) { outside++; return 0; }
    uint32_t v;
    memcpy(&v, buffer + (a - BASE), 4);
    return v;
}
static void wr(uintptr_t a, uint32_t v) {
    writes++;
    last_write = a;
    if (a == BASE + 4 && !first_root_write) first_root_write = writes;
    if (a & 3u) unaligned++;
    if (a < BASE || a + 4 > BASE + size) { outside++; return; }
    memcpy(buffer + (a - BASE), &v, 4);
}

uint32_t fpl_fixture_build(uint8_t *buf, uint32_t buf_size, uint32_t payload,
                           const uint32_t *tiles, uint32_t count, uint32_t tw,
                           uint32_t th, uint32_t capacity, uint32_t *result) {
    struct fpl_trailer t;
    buffer = buf; size = buf_size;
    unaligned = outside = reads = writes = first_root_write = 0;
    uint32_t r = fpl_trailer_write_all(BASE, payload, tiles, count, tw, th,
                                       capacity, rd, wr, &t);
    result[0] = r;
    result[1] = r == FPL_OK ? t.file_bytes : 0;
    result[2] = r == FPL_OK ? t.ifd_at : 0;
    result[3] = r == FPL_OK ? t.entries : 0;
    result[4] = unaligned;
    result[5] = outside;
    result[6] = writes;
    result[7] = (uint32_t)(last_write - BASE);
    result[8] = first_root_write;
    return r;
}
uint32_t fpl_fixture_plan(uint8_t *buf, uint32_t buf_size, uint32_t payload, uint32_t count) {
    struct fpl_trailer t;
    buffer = buf; size = buf_size;
    return fpl_trailer_plan(BASE, payload, count, rd, &t);
}
