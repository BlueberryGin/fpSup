/* Host-only substitutes for the REC hook chain. Never linked into a camera
 * build. The original C03A2438 is modelled by what the hook must preserve:
 * which arguments it saw and what it returned. */
#include <string.h>
#include "rec_hook.h"

static struct fpl_rec_hook hook;
static uint32_t request_words[4];
static uintptr_t seen_camera;
static const uint32_t *seen_request;
static uint32_t original_calls, original_result, mode;
static uint32_t gets, frees, queries, releases, reads_ready;
static uint32_t readiness_value, bad_call;

/* ---- the original C03A2438 ------------------------------------------- */
uint32_t fpl_test_native_rec_prepare(uintptr_t camera, const uint32_t *request) {
    original_calls++;
    seen_camera = camera;
    seen_request = request;
    if (request && (request[0] == 3 || request[0] == 0x23) && original_result) mode = 3;
    return original_result;
}

/* ---- RAW/class 10 allocator ------------------------------------------ */
uint32_t fpl_raw_workspace_native_mode(void) { return mode; }
uint32_t fpl_raw_workspace_native_pool(uint32_t allocator_class) {
    if (allocator_class != 10) bad_call = 1;
    return 0x43000000;
}
uint32_t fpl_raw_workspace_native_get(uint32_t allocator, struct fpl_raw_descriptor *d,
                                     uint32_t bytes, uint32_t alignment, uint32_t caller) {
    gets++;
    if (allocator != 0x43000000 || alignment != 1024 || caller) bad_call = 1;
    d->handle = 0x55000000;
    d->capacity = bytes;
    d->allocator_class = 10;
    return d->handle;
}
void fpl_raw_workspace_native_free(struct fpl_raw_descriptor *d) {
    frees++;
    if (d->handle != 0x55000000 || d->allocator_class != 10) bad_call = 1;
}

/* ---- producer services: an FHD 12-bit 23.976 producer ----------------- */
static uint32_t settings_object[16], descriptor_object[18];
uintptr_t fpl_test_settings(void) { return (uintptr_t)settings_object; }
void fpl_test_query(uintptr_t *obj, uint32_t kind, uintptr_t settings, uint32_t which) {
    queries++;
    if (kind != 2 || which != 3 || settings != (uintptr_t)settings_object) bad_call = 1;
    *obj = (uintptr_t)descriptor_object;
}
void fpl_test_release(uintptr_t *obj, uint32_t flags) {
    releases++;
    if (flags != 2 || !*obj) bad_call = 1;
}
uint32_t fpl_test_raster(uint32_t width, uint32_t height, uint32_t format) {
    return format == 0 ? (height - 1) * (width * 3 / 2) + (width + 11) / 12 * 12 * 3 / 2 : 0;
}
void fpl_test_rate(uint32_t *out) { out[0] = 24000; out[1] = 1001; }

static uint32_t yes(void *c) { (void)c; return 1; }
static uint32_t readiness(void *c, uint32_t reserved) {
    (void)c;
    if (reserved) reads_ready++;
    return readiness_value;
}

/* ---- the fixture's own entry points ----------------------------------- */
uint32_t fpl_fixture_reset(uint32_t ready) {
    memset(&hook, 0, sizeof hook);
    memset(request_words, 0, sizeof request_words);
    seen_camera = 0;
    seen_request = 0;
    original_calls = gets = frees = queries = releases = reads_ready = bad_call = 0;
    original_result = 1;
    mode = 1;                       /* STILL until the original switches it */
    readiness_value = ready;
    memset(settings_object, 0, sizeof settings_object);
    memset(descriptor_object, 0, sizeof descriptor_object);
    settings_object[0x14 / 4] = 1;
    descriptor_object[0] = 1936;
    descriptor_object[1] = 1090;
    descriptor_object[8] = 0;
    uint32_t result = fpl_rec_hook_init(&hook, 7, 0x15400);
    if (result != FPL_OK) return result;
    hook.facts.cine = yes;
    hook.facts.cinemadng = yes;
    hook.facts.sd_media = yes;
    hook.facts.readiness = readiness;
    return FPL_OK;
}
void fpl_fixture_original_returns(uint32_t value) { original_result = value; }
void fpl_fixture_break_hook(void) { hook.magic = 0; }
void fpl_fixture_rewire_setup(void) { hook.setup.context = 0; }

/* which_hook: 0 the fixture's, 1 NULL. request_code 0xFFFFFFFF passes NULL. */
uint32_t fpl_fixture_call(uint32_t request_code, uint32_t which_hook) {
    const uint32_t *request = 0;
    if (request_code != 0xFFFFFFFFu) {
        request_words[0] = request_code;
        request = request_words;
    }
    return fpl_rec_hook_call(0x12340000, request, which_hook ? 0 : &hook);
}
uint32_t fpl_fixture_reinit(void) { return fpl_rec_hook_init(&hook, 7, 0x15400); }

uint32_t fpl_fixture_get(uint32_t field) {
    switch (field) {
    case 0: return original_calls;
    case 1: return seen_camera == 0x12340000;
    case 2: return seen_request == request_words;   /* the SAME object, not a copy */
    case 3: return gets;
    case 4: return frees;
    case 5: return queries;
    case 6: return releases;
    case 7: return bad_call;
    case 8: return hook.calls;
    case 9: return hook.passed_through;
    case 10: return hook.admitted;
    case 11: return hook.refused;
    case 12: return hook.last_result;
    case 13: return hook.workspace.memory.allocation.handle != 0;
    case 14: return hook.workspace.pipeline.active;
    case 15: return reads_ready;
    case 16: return (uint32_t)(uintptr_t)seen_request;
    default: return 0xFFFFFFFFu;
    }
}
