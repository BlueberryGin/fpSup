#include "flip.h"
#include "ui_apply.h"     /* fpSup/uishare: pages composed from each sup's FPUI block */

/* Ver.5.02 facts (fpSup/lossless/native/menu_page.c, sl_menu/sl_modes.c):
 *   C37B7048        the GUI object; its word +0 is the UI app
 *   app+888 -> +60  the variable registry
 *   C05DB419        lookup(registry, name, &descriptor), Thumb: 0 for hit and miss
 *   C05DB309        register(app, count, {type, name, value}...), Thumb
 *   C0560FB0        subscribe(GUI object, name, fn), ARM; fn(descriptor, x) on
 *                   the UI thread, new value at descriptor +8, returns 0
 *   C02E4420        relayout (display message 4), ARM
 * Each called routine's first word is checked before the first call. */
#define GUI_OBJECT      0xC37B7048u
#define APP_REGISTRY    0x888u
#define REGISTRY_OFF    0x60u
#define VARIABLE_INT    0u
#define DESC_TYPE       0x00u
#define DESC_NAME       0x04u
#define DESC_VALUE      0x08u
#define CARD_MAGIC      0x50494C46u      /* "FLIP" */
#define LOOKUP_AT       0xC05DB418u
#define LOOKUP_WORD     0x3014B510u
#define REGISTER_AT     0xC05DB308u
#define REGISTER_WORD   0x4FF0E92Du
#define SUBSCRIBE_AT    0xC0560FB0u
#define SUBSCRIBE_WORD  0xE92D49F0u
#define RELAYOUT_AT     0xC02E4420u
#define RELAYOUT_WORD   0xE92D4010u
#define USED __attribute__((used))

#if defined(FPF_HOST_TEST)
#define N (&fpf_test_natives)
#define peek(a) N->read(a)
#define poke(a, v) N->write(a, v)
#define peek8(a) N->read_byte(a)
#define relayout() N->relayout()
#define lookup(r, n, d) N->lookup(r, n, d)
#define reg(a, c, d) N->reg(a, c, d)
#define subscribe(g, n, f) N->subscribe(g, n, f)
#define publish() N->publish()
#elif defined(__arm__) && UINTPTR_MAX == UINT32_MAX
#define peek(a) (*(volatile const uint32_t *)(a))
#define poke(a, v) (*(volatile uint32_t *)(a) = (v))
#define peek8(a) (*(volatile const uint8_t *)(a))
static void relayout(void) {
    typedef void (*fn)(uint32_t); ((fn)RELAYOUT_AT)(0);
}
static uint32_t lookup(uintptr_t r, const char *n, uintptr_t *d) {
    typedef uint32_t (*fn)(uintptr_t, const char *, uintptr_t *); return ((fn)(LOOKUP_AT | 1u))(r, n, d);
}
static uint32_t reg(uintptr_t a, uint32_t c, const uint32_t *d) {
    typedef uint32_t (*fn)(uintptr_t, uint32_t, const uint32_t *); return ((fn)(REGISTER_AT | 1u))(a, c, d);
}
static uint32_t subscribe(uintptr_t g, const char *n, uintptr_t f) {
    typedef uint32_t (*fn)(uintptr_t, const char *, uintptr_t); return ((fn)SUBSCRIBE_AT)(g, n, f);
}
static void publish(void) {         /* D-cache clean+invalidate, barriers */
    typedef void (*fn)(void); ((fn)0xC000E91Cu)();
}
#else
#error "ARM32 native ABI required; host tests must explicitly substitute it"
#endif

uint32_t fpf_want(uint32_t value) {
    return value == 1u ? 0x333000u : value == 2u ? 0x001000u : value == 3u ? 0x332000u : 0u;
}

uint32_t fpf_touch(uint32_t value) { return value == 1u || value == 3u; }

uint32_t fpf_apply(uint32_t value) {
    uint32_t want, reg_now, touch_ok;
    if (value >= FPF_VALUES) value = 0;
    want = fpf_want(value);
    reg_now = peek(FPF_LAYER_REG);
    touch_ok = peek(FPF_TOUCH_VT_AT) == FPF_TOUCH_VT;
    /* A turned UI with touch left upright would invert the touch screen. */
    if (fpf_touch(value) && !touch_ok) return FPF_APPLY_REFUSED;
    if (touch_ok && peek(FPF_TOUCH_FLAG) != fpf_touch(value)) poke(FPF_TOUCH_FLAG, fpf_touch(value));
    if ((reg_now & FPF_LAYER_MASK) == want) return FPF_APPLY_NONE;
    poke(FPF_LAYER_REG, (reg_now & ~FPF_LAYER_MASK) | want);
    if (peek(RELAYOUT_AT) == RELAYOUT_WORD) relayout();
    return FPF_APPLY_WRITTEN;
}

/* The UI's callback when MV_fpScreenFlip is written: the UI thread. */
USED uint32_t fpf_changed(uintptr_t descriptor, uintptr_t unused) {
    (void)unused;
    if (descriptor && !(descriptor & 3u)) fpf_apply(peek(descriptor + DESC_VALUE));
    return 0;
}

static uint32_t aligned(uintptr_t a) { return a && !(a & 3u); }

static uint32_t same(uintptr_t a, const char *s) {
    if (!a) return 0;
    for (uint32_t n = 0;; ++n) {
        uint32_t ch = peek8(a + n);
        if (ch != (uint8_t)s[n]) return 0;
        if (!ch) return 1;
    }
}

/* The registered integer's descriptor, or 0. */
static uintptr_t variable(uintptr_t app, const char *NAME) {
    uintptr_t d = 0;
    if (lookup(peek(app + APP_REGISTRY) + REGISTRY_OFF, NAME, &d) != 0 || !aligned(d)) return 0;
    if (peek(d + DESC_TYPE) != VARIABLE_INT || !same(peek(d + DESC_NAME), NAME)) return 0;
    return d;
}

static uint32_t fail(struct fpf_card *c, uint32_t why) { c->result = why; return 0; }

/* launch.S: the state (in the resident block, ui_at/ui_len filled in), the
 * block, its size. Returns 0. */
USED uint32_t fpf_card_init(struct fpf_card *c, uintptr_t base, uint32_t block_bytes) {
    volatile uint32_t *w;
    const char *NAME;
    uintptr_t app;
    (void)base; (void)block_bytes;
    if (!c || c->result) return 0;                  /* once */
    c->magic = CARD_MAGIC;
    w = c->name;                                    /* MV_fpScreenFlip; .text only */
    w[0] = 0x665F564Du; w[1] = 0x72635370u; w[2] = 0x466E6565u; w[3] = 0x0070696Cu; w[4] = 0u;
    NAME = (const char *)c->name;
    if (peek(LOOKUP_AT) != LOOKUP_WORD || peek(REGISTER_AT) != REGISTER_WORD ||
        peek(SUBSCRIBE_AT) != SUBSCRIBE_WORD)
        return fail(c, FPF_FIRMWARE);
    app = peek(GUI_OBJECT);
    if (!aligned(app) || !aligned(peek(app + APP_REGISTRY))) return fail(c, FPF_NO_GUI);
    if (!variable(app, NAME)) {
        uint32_t def[3];
        def[0] = VARIABLE_INT;
        def[1] = (uint32_t)(uintptr_t)NAME;
        def[2] = 0u;                                /* normal */
        if (reg(app, 1, def) != 0) return fail(c, FPF_REGISTER);
        c->registered = 1;
        if (!variable(app, NAME)) return fail(c, FPF_VARIABLE);
    }
    publish();
    {
        struct uia_outcome ui;
        c->ui_result = uia_apply(c->ui_at, c->ui_len, &ui);
        c->ui_op = ui.op;
        if (c->ui_result != UIA_OK) return fail(c, FPF_UI);
        c->first_id = ui.first_id;
        c->row_y = ui.n_slots ? ui.slots[0] : 0;
        c->page = ui.page;
    }
    c->subscribe_result = subscribe(GUI_OBJECT, NAME, (uintptr_t)fpf_changed);  /* -fropi */
    if (c->subscribe_result != 0) return fail(c, FPF_SUBSCRIBE);
    c->result = FPF_READY;
    return 0;
}
