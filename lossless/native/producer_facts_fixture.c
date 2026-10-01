/* Host substitutes for the five firmware services producer_facts.c calls.
 * The descriptor object carries a reference count so a test can see that
 * every query was released. Release overwrites the descriptor with poison,
 * and the next query restores it, so code that reads the object after giving
 * its reference back sees garbage -- which the tests then catch as wrong
 * facts. Nothing here imitates readiness. */
#include <string.h>
#include "producer_facts.h"

static uint32_t settings_object[16];
static uint32_t descriptor_object[0x30], descriptor_live[18];
static int refs, queries, releases;
static int query_null, settings_null;
static uint32_t probe_values[3];
static uint32_t readiness_value, readiness_calls, readiness_reserved;
static struct fpl_producer_facts facts;
static struct fpl_context last;
static uint32_t last_result;

uintptr_t fpl_test_settings(void) {
    return settings_null ? 0 : (uintptr_t)settings_object;
}
void fpl_test_query(uintptr_t *obj, uint32_t kind, uintptr_t settings, uint32_t which) {
    queries++;
    if (kind != 2 || which != 3 || settings != (uintptr_t)settings_object || query_null) {
        *obj = 0;
        return;
    }
    refs++;
    memcpy(descriptor_object, descriptor_live, sizeof descriptor_live);
    *obj = (uintptr_t)descriptor_object;
}
void fpl_test_release(uintptr_t *obj, uint32_t flags) {
    releases++;
    if (flags != 2) refs += 1000;              /* wrong release mode: loud */
    if (*obj) {
        refs--;
        for (uint32_t n = 0; n < 18; ++n) descriptor_object[n] = 0xDEADBEEFu;
    }
}
/* C0135D40 for the plain packed formats, as C0135DB8 computes a row. */
static uint32_t row(uint32_t width, uint32_t format) {
    switch (format) {
    case 0: return width * 3 >> 1;
    case 1: return width * 7 >> 2;
    case 2: return width << 1;
    case 3: return width * 5 >> 2;
    case 4: return width;
    default: return 1;
    }
}
uint32_t fpl_test_raster(uint32_t width, uint32_t height, uint32_t format) {
    if (!width || !height) return 0;
    return (height - 1) * ((row(width, format) + 3) & ~3u) +
           ((row((width + 11) / 12 * 12, format) + 3) & ~3u);
}
void fpl_test_rate(uint32_t *out) {
    static const uint32_t table[11][2] = {
        {30000, 1001}, {24000, 1001}, {24000, 1000}, {25000, 1000},
        {30000, 1001}, {30000, 1001}, {48000, 1000}, {50000, 1000},
        {60000, 1001}, {100000, 1000}, {120000, 1001}};
    uint32_t code = settings_object[0x14 / 4];
    const uint32_t *pick = code < 11 ? table[code] : table[0];
    out[0] = pick[0];
    out[1] = pick[1];
}

static uint32_t probe_cine(void *c) { (void)c; return probe_values[0]; }
static uint32_t probe_cdng(void *c) { (void)c; return probe_values[1]; }
static uint32_t probe_sd(void *c) { (void)c; return probe_values[2]; }
static uint32_t readiness(void *c, uint32_t reserved) {
    (void)c;
    readiness_calls++;
    readiness_reserved = reserved;
    return readiness_value;
}

uint32_t fpl_fixture_reset(uint32_t width, uint32_t height, uint32_t format,
                           uint32_t rate_code) {
    memset(settings_object, 0, sizeof settings_object);
    memset(descriptor_object, 0, sizeof descriptor_object);
    memset(&facts, 0, sizeof facts);
    memset(&last, 0, sizeof last);
    refs = queries = releases = 0;
    query_null = settings_null = 0;
    readiness_value = readiness_calls = readiness_reserved = 0;
    probe_values[0] = probe_values[1] = probe_values[2] = 1;
    settings_object[0x14 / 4] = rate_code;
    memset(descriptor_live, 0, sizeof descriptor_live);
    descriptor_live[0] = width;
    descriptor_live[1] = height;
    descriptor_live[8] = format;
    for (uint32_t n = 2; n < 18; ++n)
        if (n != 8) descriptor_live[n] = 0x1000 + n;     /* distinct filler */
    if (fpl_producer_facts_init(&facts) != FPL_OK) return 99;
    facts.cine = probe_cine;
    facts.cinemadng = probe_cdng;
    facts.sd_media = probe_sd;
    facts.readiness = readiness;
    return FPL_OK;
}
void fpl_fixture_probe(uint32_t which, uint32_t value) { probe_values[which] = value; }
void fpl_fixture_drop_probe(uint32_t which) {
    if (which == 0) facts.cine = 0;
    if (which == 1) facts.cinemadng = 0;
    if (which == 2) facts.sd_media = 0;
    if (which == 3) facts.readiness = 0;
}
void fpl_fixture_readiness(uint32_t value) { readiness_value = value; }
void fpl_fixture_fail(uint32_t which) {
    if (which == 0) settings_null = 1;
    if (which == 1) query_null = 1;
}
void fpl_fixture_descriptor(uint32_t index, uint32_t value) {
    descriptor_live[index] = value;
}
uint32_t fpl_fixture_read(uint32_t reserved) {
    last_result = fpl_producer_facts_read(&facts, reserved, &last);
    return last_result;
}
uint32_t fpl_fixture_read_uninitialised(void) {
    struct fpl_producer_facts blank;
    memset(&blank, 0, sizeof blank);
    return fpl_producer_facts_read(&blank, 0, &last);
}
uint32_t fpl_fixture_reinit(void) { return fpl_producer_facts_init(&facts); }

/* 0..9 the context; 10.. bookkeeping; 20.. what `seen` holds. */
uint32_t fpl_fixture_get(uint32_t field) {
    const uint32_t *c = (const uint32_t *)&last;
    if (field < 10) return c[field];
    switch (field) {
    case 10: return (uint32_t)refs;
    case 11: return (uint32_t)queries;
    case 12: return (uint32_t)releases;
    case 13: return readiness_calls;
    case 14: return readiness_reserved;
    case 15: return facts.reads;
    case 16: return facts.last_result;
    case 20: return facts.seen.width;
    case 21: return facts.seen.height;
    case 22: return facts.seen.format;
    case 23: return facts.seen.bits;
    case 24: return facts.seen.raster;
    case 25: return facts.seen.rate_code;
    case 26: return facts.seen.fps_num;
    case 27: return facts.seen.fps_den;
    default: return 0xFFFFFFFFu;
    }
}
/* The frame's own descriptor, as the creator would have copied it; `poke`
 * changes one word to model a producer that moved after preparation. */
uint32_t fpl_fixture_match(uint32_t poke_index, uint32_t poke_value) {
    uint32_t frame[18];
    memcpy(frame, descriptor_live, sizeof frame);
    if (poke_index < 18) frame[poke_index] = poke_value;
    return fpl_producer_facts_match(&facts, frame);
}
