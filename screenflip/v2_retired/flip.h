#ifndef FPSCREENFLIP_FLIP_H
#define FPSCREENFLIP_FLIP_H
#include <stdint.h>

/* fpScreenFlip, the camera side: the row's variable and what its value does.
 *
 * At boot, from the loader's entry chain, in this order, each step only if
 * the one before succeeded (any failure: no row, camera stock):
 *
 *   - registers MV_fpScreenFlip, an integer, 0: the UI variable registry
 *     keeps it in memory; nothing is ever written to the settings store, and
 *     every boot starts at 0 (normal);
 *   - adds the row (System 2 > Mode Settings > Custom 1..4 > More options,
 *     TOOLS tab): its FPUI block (menu/build_flip_fpui.py) through the shared
 *     applier fpSup/uishare/ui_apply.c, compiled into this blob;
 *   - subscribes to the variable's writes (C0560FB0, the call Sensor Lab r31
 *     proved on the camera); the callback runs on the UI thread with the new
 *     value at descriptor +8 and needs no state of its own, and applies the
 *     value to the panel.
 *
 * What a value does (the layer register and touch flag measured on the camera
 * 2026-09-30, research notes lcd-flip-layer-bits; THIS MODULE IS NOT
 * CAMERA-TESTED):
 *
 *   0x30190044  display layer register, one nibble per layer (bit 0 horizontal,
 *               bit 1 vertical); layers 3 = LCD picture, 4/5 = LCD OSD (the UI):
 *     0 normal        0x000000
 *     1 180           0x333000   picture and UI turned
 *     2 mirror        0x001000   picture mirrored left-right, UI untouched
 *     3 180 + mirror  0x332000   picture flipped top-bottom (180 then mirror),
 *                                UI turned
 *   Other bits are kept (read, clear 12..23, OR).
 *   0xC3760E44  the touch panel's built-in 180 flag: 1 when the UI is turned
 *               (values 1, 3), else 0; written only when the touch object's
 *               vtable word [0xC3760E48] is 0xC0CBE37C. A value that turns the
 *               UI is refused when it is not (the touch screen would be upside
 *               down); 0 and 2 are always allowed.
 *   C02E4420    the firmware's relayout (posts display message 4, re-sends the
 *               cached layer setups); without it the screen stays garbled until
 *               S1. ARM, no meaningful argument.
 *
 * HDMI hot-plug clears the register; it is put back at the next write of the
 * row (there is no poll: no task, no hook). */

#define FPF_LAYER_REG    0x30190044u
#define FPF_LAYER_MASK   0x00FFF000u
#define FPF_TOUCH_FLAG   0xC3760E44u
#define FPF_TOUCH_VT_AT  0xC3760E48u
#define FPF_TOUCH_VT     0xC0CBE37Cu
#define FPF_VALUES       4u

enum fpf_result {                   /* fpf_card.result */
    FPF_READY = 1,                  /* registered (or already) and subscribed */
    FPF_NO_GUI = 2,                 /* no UI app / registry */
    FPF_REGISTER = 3,               /* registration refused */
    FPF_VARIABLE = 4,               /* registered, but not found as an integer */
    FPF_FIRMWARE = 5,               /* a called routine is not the Ver.5.02 one */
    FPF_SUBSCRIBE = 6,              /* subscription refused (the row is in) */
    FPF_UI = 7                      /* ui_apply refused the block: ui_result says why */
};

enum fpf_apply {
    FPF_APPLY_NONE = 0,             /* register already right */
    FPF_APPLY_WRITTEN = 1,          /* written + relayout */
    FPF_APPLY_REFUSED = 2           /* turns the UI, but the touch object is unknown */
};

struct fpf_card {
    uint32_t ui_at, ui_len;         /* the row's FPUI block (launch.S fills these) */
    uint32_t magic;                 /* "FLIP" */
    uint32_t result;                /* enum fpf_result, 0 = never tried */
    uint32_t registered;            /* 1 if this boot registered the variable */
    uint32_t subscribe_result;      /* what C0560FB0 returned */
    uint32_t name[5];               /* "MV_fpScreenFlip": the registry BORROWS it */
    uint32_t ui_result, ui_op;      /* uia_apply's result and the op it stopped at */
    uint32_t first_id, row_y;       /* the row's first object id and its y */
    uintptr_t page;                 /* the Y2_5_1 copy switched in */
};

uint32_t fpf_want(uint32_t value);          /* layer bits 12..23 for a value */
uint32_t fpf_touch(uint32_t value);         /* 1 if the value turns the UI */
uint32_t fpf_apply(uint32_t value);         /* enum fpf_apply */
uint32_t fpf_changed(uintptr_t descriptor, uintptr_t unused);   /* the callback; 0 */
uint32_t fpf_card_init(struct fpf_card *c, uintptr_t base, uint32_t block_bytes);

#if defined(FPF_HOST_TEST)
struct fpf_natives {
    uint32_t (*read)(uintptr_t);
    void (*write)(uintptr_t, uint32_t);
    uint32_t (*read_byte)(uintptr_t);
    void (*relayout)(void);
    uint32_t (*lookup)(uintptr_t registry, const char *name, uintptr_t *descriptor);
    uint32_t (*reg)(uintptr_t app, uint32_t count, const uint32_t *definitions);
    uint32_t (*subscribe)(uintptr_t gui, const char *name, uintptr_t fn);
    void (*publish)(void);
};
extern const struct fpf_natives fpf_test_natives;
#endif
#endif
