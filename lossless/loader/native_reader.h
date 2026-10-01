#ifndef FPLOSSLESS_NATIVE_READER_H
#define FPLOSSLESS_NATIVE_READER_H
#include <stdint.h>

/* v5.02 ARM32 reader, not a normalized host view. No installer/hook here. */
typedef uint32_t (*fp_nr_original_parser)(void *reader);
enum fp_nr_result {
    FP_NR_OK = 0, FP_NR_COMPLETE = 1,
    FP_NR_INVALID = 0x100, FP_NR_NOT_TARGET, FP_NR_REFUSED, FP_NR_POISONED
};
enum fp_nr_phase { FP_NR_EMPTY, FP_NR_PREPARED, FP_NR_ACTIVE, FP_NR_PINNED,
                   FP_NR_DEAD };
struct fp_nr_config {
    const uint8_t *page, *pool;
    uint32_t page_length, pool_length, page_fnv, pool_fnv;
    uint32_t records, header_length, objects;
    fp_nr_original_parser original_parser;
};
struct fp_nr_state {
    uint32_t magic, phase;
    struct fp_nr_config config;
    void *reader;
    uint32_t saved_cursor, saved_app, saved_owner, saved_pool_length, saved_pool;
    uint32_t saved_owns_source, saved_source_length, saved_source, saved_stream;
    uint32_t cursor, records, last_result;
    uint32_t partial_count, partial_array, partial_root;
};

/* Fresh zeroed, separately owned state. Config contains build-pinned constants,
 * not fingerprints computed from arbitrary runtime input. All spans must be
 * accessible and immutable for the entire GUI lifetime; config is copied.
 * The original NBU page and string pool must also remain immutable: a new
 * overlay/resource replacement requires separate owner/drain proof, not reuse.
 * original_parser is the retained ORIGINAL C05E6401 implementation/trampoline,
 * never an installed hook entry that recurses. Unpatched offline tests may use
 * C05E6401 directly. This pointer contract is the future installer's obligation.
 * Success only initializes this bridge; it does NOT mean installation ready. */
uint32_t fp_nr_init(struct fp_nr_state *, const struct fp_nr_config *);

/* Call ONLY at the first MainB2 parser boundary, with serialized GUI ownership,
 * after native C05E82F1 selected owner context (+0c), before its first C05E6401.
 * No synthetic page-id/readiness claim replaces the native offset checks.
 * COMPLETE consumes the whole private page and is native terminal return 1.
 * NOT_TARGET/REFUSED are pre-call decisions for a future outer adapter, not
 * native parser returns to blindly forward. Any post-start failure preserves
 * diagnostics, clears reader +50/+b4 to stop partial-root publication, and is
 * permanently poisoned. Page, pool, partial objects and scratch stay retained.
 *
 * BEFORE AN INSTALLER IS ALLOWED: an outer-entry gate must stop native reader
 * initialization/cleanup/retry while ACTIVE/DEAD and establish owner exclusion
 * and drain. A record hook alone cannot do that: C05E61F0 runs before this seam.
 * Clearing +50/+b4 stops the observed immediate publication, not all later reuse.
 * There is deliberately no reset, release, rollback, or installer-ready API. */
uint32_t fp_nr_parse(struct fp_nr_state *, void *reader);

/* Narrow post-restore resolver: only private appended offsets and this exact
 * retained reader in its original source/pool context. NULL means not handled.
 * Ordinary stock offsets use the original resolver. No ownership is released. */
const char *fp_nr_string(struct fp_nr_state *, void *reader, uint32_t offset);
#endif
