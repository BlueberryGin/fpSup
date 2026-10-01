#ifndef FPLOSSLESS_PRODUCER_FACTS_H
#define FPLOSSLESS_PRODUCER_FACTS_H
#include <stdint.h>
#include "../control.h"

#define FPL_FACTS_MAGIC 0x46435446u
/* The creator copies this many words of the descriptor into frame+0x10
 * (C02E5B78). Kept whole so the first real frame can be compared against
 * exactly what REC preparation saw, not against three fields of it. */
#define FPL_FACTS_DESCRIPTOR_WORDS 18u

/* A fact this module cannot read from the producer. Returns 1 for yes, 0 for
 * no; anything else is "unknown" and refuses. NULL is unknown. */
typedef uint32_t (*fpl_facts_probe)(void *);
/* Proofs owned by OTHER verified adapters (codec, writer, header, playback,
 * storage, REC gate). This module contributes none and never widens them.
 * Called after the layout is known, with the capacity actually granted. */
typedef uint32_t (*fpl_facts_readiness)(void *, uint32_t reserved_bytes);

struct fpl_facts_seen {
    uint32_t width, height, format, bits, raster;
    uint32_t rate_code, fps_num, fps_den;
    uint32_t descriptor[FPL_FACTS_DESCRIPTOR_WORDS];
};
struct fpl_producer_facts {
    uint32_t magic, reads, last_result;
    fpl_facts_probe cine, cinemadng, sd_media;
    void *probe_context;
    fpl_facts_readiness readiness;
    void *readiness_context;
    /* What the most recent successful read saw. The frame adapter compares
     * the first real frame's descriptor against this before trusting it. */
    struct fpl_facts_seen seen;
};

uint32_t fpl_producer_facts_init(struct fpl_producer_facts *);

/* The fpl_rec_workspace_facts provider. Reads the producer the same way the
 * kind-1 RAW creator does (C0374268): the current capture settings, then the
 * LiveViewState descriptor for image kind 2, then its width, height and
 * Sigpro format code. Frame rate comes from the table the CinemaDNG FrameRate
 * tag is written from (C00C9BD0). Returns FPL_*; on any failure `out` is left
 * with ready = 0 and nothing in `seen` changes. */
uint32_t fpl_producer_facts_read(void *facts, uint32_t reserved_bytes,
                                 struct fpl_context *out);

/* Does a frame's own descriptor (frame+0x10) match what was read at REC
 * preparation? The producer can change between preparation and the first
 * frame; a mismatch means the reservation was planned for another format. */
uint32_t fpl_producer_facts_match(const struct fpl_producer_facts *,
                                  const uint32_t *frame_descriptor);
#endif
