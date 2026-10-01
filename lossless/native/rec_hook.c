#include "rec_hook.h"

#if defined(FPL_REC_HOOK_HOST_TEST)
extern uint32_t fpl_test_native_rec_prepare(uintptr_t, const uint32_t *);
#define original_prepare fpl_test_native_rec_prepare
#elif defined(__arm__) && UINTPTR_MAX == UINT32_MAX
static uint32_t original_prepare(uintptr_t camera, const uint32_t *request) {
    typedef uint32_t (*fn)(uintptr_t, const uint32_t *);
    return ((fn)0xc03a2438u)(camera, request);
}
#else
#error "ARM32 native REC ABI required; host tests must explicitly substitute it"
#endif

static uint32_t hook_valid(const struct fpl_rec_hook *h) {
    return h && h->magic == FPL_REC_HOOK_MAGIC &&
           h->setup.read == fpl_producer_facts_read &&
           h->setup.context == &h->facts;
}

uint32_t fpl_rec_hook_init(struct fpl_rec_hook *h, uint32_t session,
                           uint32_t metadata_bytes) {
    uint32_t result;
    if (!h || !session || !metadata_bytes) return FPL_INVALID;
    const unsigned char *bytes = (const unsigned char *)h;
    for (uint32_t n = 0; n < sizeof(*h); ++n)
        if (bytes[n]) return FPL_INVALID;
    if ((result = fpl_rec_workspace_init(&h->workspace, session)) != FPL_OK)
        return result;
    if ((result = fpl_producer_facts_init(&h->facts)) != FPL_OK) return result;
    h->setup.read = fpl_producer_facts_read;
    h->setup.context = &h->facts;
    h->setup.copy_source = 0;
    h->setup.metadata_bytes = metadata_bytes;
    h->magic = FPL_REC_HOOK_MAGIC;
    return FPL_OK;
}

uint32_t fpl_rec_hook_call(uintptr_t camera, const uint32_t *request,
                           struct fpl_rec_hook *h) {
    uint32_t dispatch;
    /* Not ours to judge: every other request, and any request at all when our
     * own state cannot be trusted, goes to the original untouched. Refusing
     * those would take down still capture, playback transitions and the rest
     * of what C03A2438 gates, over a fault in a feature they never use. */
    if (!request || !FPL_REC_IS_START(request[0])) {
        if (hook_valid(h)) { h->calls++; h->passed_through++; }
        return original_prepare(camera, request);
    }
    if (!hook_valid(h)) {
        /* Event 3 with broken state. REC is refused, as the direct build's
         * contract requires: recording RAW while claiming nothing would be
         * the silent fallback that contract forbids. The state is not
         * touched, so there is nothing to count into. */
        return 0;
    }
    h->calls++;
    h->last_request = request[0];
    /* rec_workspace_prepare calls the original C03A2438 itself, first, and
     * only then plans, reserves and admits. Do not call it here as well. */
    dispatch = fpl_rec_workspace_prepare(&h->workspace, camera, request, &h->setup);
    h->last_result = h->workspace.last_result;
    if (dispatch) h->admitted++;
    else h->refused++;
    return dispatch;
}
