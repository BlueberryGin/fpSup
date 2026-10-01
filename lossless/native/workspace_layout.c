#include "workspace_layout.h"

static uint32_t align1024(uint32_t n) {
    return (n + FPL_WORKSPACE_ALIGNMENT - 1u) & ~(FPL_WORKSPACE_ALIGNMENT - 1u);
}

static uint32_t span(struct fpl_workspace_span *s, uint32_t offset, uint32_t capacity) {
    s->offset = offset;
    s->capacity = capacity;
    return offset + capacity;
}

uint32_t fpl_workspace_plan(struct fpl_workspace_layout *out,
                            uint32_t width, uint32_t height, uint32_t bits,
                            uint32_t copy_source, uint32_t metadata_bytes) {
    uint32_t cols, rows, tiles, raster, packed_capacity, table_capacity;
    uint32_t metadata_capacity, fixed, total, at;
    if (!out || copy_source > 1u) return FPL_INVALID;
    if (width < 8u || width > FPL_WORKSPACE_MAX_WIDTH || (width & 7u) ||
        height < 2u || height > FPL_WORKSPACE_MAX_HEIGHT || (height & 1u) ||
        (bits != 10u && bits != 12u && bits != 14u)) return FPL_UNSUPPORTED;

    cols = (width + FPL_TILE_WIDTH - 1u) / FPL_TILE_WIDTH;
    rows = (height + FPL_TILE_HEIGHT - 1u) / FPL_TILE_HEIGHT;
    tiles = cols * rows;
    if (!tiles || tiles > FPL_TILE_MAX) return FPL_UNSUPPORTED;

    /* Geometry caps above keep these products, round-ups and fixed sum in
     * uint32_t. Only caller-sized metadata can approach the address limit. */
    raster = ((width * bits) >> 3) * height;
    packed_capacity = align1024(raster);
    table_capacity = align1024(tiles * 4u);
    fixed = packed_capacity * (1u + copy_source) + table_capacity * 4u;
    if (metadata_bytes > UINT32_MAX - (FPL_WORKSPACE_ALIGNMENT - 1u))
        return FPL_INVALID;
    metadata_capacity = align1024(metadata_bytes);
    if (metadata_capacity > UINT32_MAX - fixed) return FPL_INVALID;
    total = fixed + metadata_capacity;

    /* Publish only after all validation. No failure may expose a half-plan. */
    out->width = width;
    out->height = height;
    out->bits = bits;
    out->tile_cols = cols;
    out->tile_rows = rows;
    out->tile_count = tiles;
    out->raster_bytes = raster;
    out->total_bytes = total;
    at = span(&out->source, 0u, copy_source ? packed_capacity : 0u);
    at = span(&out->output, at, packed_capacity);
    at = span(&out->codec_sizes, at, table_capacity);
    at = span(&out->codec_scratch, at, table_capacity);
    at = span(&out->metadata, at, metadata_capacity);
    at = span(&out->tile_offsets, at, table_capacity);
    (void)span(&out->tile_counts, at, table_capacity);
    return FPL_OK;
}
