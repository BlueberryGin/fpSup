#ifndef FPLOSSLESS_TRAILER_H
#define FPLOSSLESS_TRAILER_H
#include <stdint.h>
#include "../control.h"

/* The trailing-IFD layout, built on the camera from the frame's OWN root IFD.
 *
 *   0       .. 0x13400    the frame's header, byte for byte, except the root
 *                         pointer at offset 4
 *   0x13400 .. ifd_at     the compressed tiles (the payload, 1 KiB padded)
 *   ifd_at  .. +ifd_size  the root IFD's entries, minus StripOffsets (273),
 *                         RowsPerStrip (278) and StripByteCounts (279), with
 *                         Compression (259) = 7 and TileWidth (322),
 *                         TileLength (323), TileOffsets (324), TileByteCounts
 *                         (325) added; sorted; next-IFD 0
 *           .. +4*tiles   TileOffsets, from 0x13400, word aligned
 *           .. +4*tiles   TileByteCounts
 *
 * The same contract as projects/lossless-sup/tools/trailing_ifd.py, whose
 * output was decoded to the identical samples as the stock frame on
 * 2026-09-26 and written by the camera and decoded on 2026-09-29. The test
 * compares this code's bytes with that tool's.
 *
 * Every other entry is copied as it is: their out-of-line values sit in the
 * untouched header, so their offsets stay right. */
#define FPL_TRAILER_PIXELS 0x13400u
#define FPL_TRAILER_MAX_ENTRIES 340u    /* 4 KiB of IFD: the bound the frame
                                          adapter charged for the trailer */

struct fpl_trailer {
    uint32_t entries, ifd_at, ifd_size, offset_table, count_table, file_bytes;
};

/* Plan without writing anything: validate the header at `file` (read through
 * `read`) and compute where everything goes. FPL_OK or FPL_UNSUPPORTED. */
typedef uint32_t (*fpl_trailer_read)(uintptr_t);
typedef void (*fpl_trailer_write)(uintptr_t, uint32_t);
uint32_t fpl_trailer_plan(uintptr_t file, uint32_t payload, uint32_t tiles,
                          fpl_trailer_read read, struct fpl_trailer *out);
/* Write the IFD and tables, then -- last -- the root pointer. `capacity` is
 * the file buffer's; nothing is written unless everything fits. */
uint32_t fpl_trailer_write_all(uintptr_t file, uint32_t payload,
                               const uint32_t *tile_bytes, uint32_t tiles,
                               uint32_t tile_width, uint32_t tile_height,
                               uint32_t capacity, fpl_trailer_read read,
                               fpl_trailer_write write, struct fpl_trailer *out);
#endif
