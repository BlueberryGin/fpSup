#ifndef FPSCREENFLIP_FLIP_H
#define FPSCREENFLIP_FLIP_H
#include <stdint.h>

/* fpScreenFlip as a Loader v3 sup (\fpSup\40FLIP.BIN; projects/usb-shell-sup/
 * notes/LOADER_V3.md). THIS MODULE IS NOT CAMERA-TESTED.
 *
 * One flip value per display mode: System 2 > Mode Settings (Y2_5) > Custom
 * N > More options (Y2_5_1), TOOLS tab, fifth row "Screen Flip". The camera
 * keeps a STILL set and a CINE set of customs (research/firmware/notes/
 * DISPLAY_MODE_SWITCH.md §1b), so the table is 2 sets x Custom 1..4; the
 * fifth slot (mode 4) is "LCD off" and has no flip.
 *
 * Setting a value does NOT touch the screen. The value is applied when the
 * camera SWITCHES INTO that display mode (DISP, remote, a custom disabled,
 * STILL <-> CINE, shooting style) -- and once at boot, for the mode it starts
 * in. The table lives in this sup's own file (entry.S `settings`), read from
 * the block at boot and written back in place by the sup's task; never the
 * camera's settings store.
 *
 * At load (entry.S, after the panel register and the touch flag are claimed):
 *   - registers MV_fpScreenFlip, an integer: the row's working copy only;
 *   - adds the row (its FPUI block, through uishare/ui_apply.c);
 *   - subscribes to the row's writes (C0560FB0, UI thread): the value goes
 *     into table[set][N], N = the custom Y2_5_1 is editing (0xC37628A4);
 *   - starts the task (every FPF_POLL_MS):
 *       live mode (set, m) changed -> apply table[set][m] to the panel;
 *       edited custom (set, N) changed -> queue MV_fpScreenFlip = table[set][N]
 *         (C0593F30), so the row shows that custom's value;
 *       table differs from the file for FPF_SAVE_POLLS polls, not recording
 *         -> rewrite the record in this file and read it back.
 *     Polling, not a settings observer: boot and STILL/CINE send no display-
 *     mode event, observer slots are few (10) and the observer runs on the
 *     setter's task anyway (DISPLAY_MODE_SWITCH.md §2).
 *
 * What a value does (measured 2026-09-30, memory lcd-flip-layer-bits):
 *   0x30190044  one nibble per layer (bit 0 horizontal, bit 1 vertical);
 *               layer 3 = LCD picture, 4/5 = LCD OSD:
 *     0 normal 0x000000, 1 180 0x333000, 2 mirror 0x001000 (picture only),
 *     3 180 + mirror 0x332000. Other bits kept.
 *   0xC3760E44  touch 180 flag: 1 for 1 and 3; written only when the touch
 *               object's vtable [0xC3760E48] is 0xC0CBE37C, else 1 and 3 are
 *               refused (0 and 2 still apply).
 *   C02E4420    relayout (display message 4) after a register write. */

#define FPF_LAYER_REG    0x30190044u
#define FPF_LAYER_MASK   0x00FFF000u
#define FPF_TOUCH_FLAG   0xC3760E44u
#define FPF_TOUCH_VT_AT  0xC3760E48u
#define FPF_TOUCH_VT     0xC0CBE37Cu
#define FPF_VALUES       4u
#define FPF_CUSTOMS      4u             /* Custom 1..4; mode 4 is LCD off */
#define FPF_SETS         2u             /* 0 STILL, 1 CINE */
#define FPF_SLOTS        (FPF_SETS * FPF_CUSTOMS)
#define FPF_EDITING      0xC37628A4u    /* MenuDisplayCustomHandler +0x28: N, 0xFFFF before */
#define FPF_NO_KEY       0xFFFFFFFFu
#define FPF_POLL_MS      50u
#define FPF_SAVE_POLLS   20u            /* x FPF_POLL_MS = 1 s unchanged */
#define FPF_HEADER_LEN   32u            /* the FSB1 header before the blob */
#define FPF_SETTINGS_MAGIC 0x54534646u  /* "FFST" */
#define FPF_SETTINGS_WORDS 5u           /* magic, version 1, slots 0..3, 4..7, check */

enum fpf_result {                   /* fpf_card.result */
    FPF_READY = 1,                  /* row in, subscribed, task running */
    FPF_NO_GUI = 2,                 /* no UI app / registry */
    FPF_REGISTER = 3,               /* registration refused */
    FPF_VARIABLE = 4,               /* registered, but not found as an integer */
    FPF_FIRMWARE = 5,               /* a called routine is not the Ver.5.02 one */
    FPF_SUBSCRIBE = 6,              /* subscription refused (the row is in) */
    FPF_UI = 7,                     /* ui_apply refused the block: ui_result says why */
    FPF_TASK = 8,                   /* the task was not started (row in, no flip) */
    FPF_INVALID = 9                 /* the state is not where/what entry.S promised */
};

enum fpf_apply {
    FPF_APPLY_NONE = 0,             /* register already right */
    FPF_APPLY_WRITTEN = 1,          /* written + relayout */
    FPF_APPLY_REFUSED = 2           /* turns the UI, but the touch object is unknown */
};

struct fpf_card {
    /* entry.S fills these four before fpf_card_init (resident addresses) */
    uintptr_t ui_at;                /* the row's FPUI block */
    uint32_t ui_len;
    uintptr_t task_entry;           /* task_shim: fpf_task(state) */
    uintptr_t changed_entry;        /* changed_shim: fpf_changed(descriptor, state) */
    uint32_t magic;                 /* "FLIP" */
    uint32_t result;                /* enum fpf_result, 0 = never tried */
    uintptr_t base;                 /* the blob (entry.S `entry`) */
    const char *self_path;          /* this file (v3 r2), 0 = never save */
    uint32_t name[5];               /* "MV_fpScreenFlip": the registry and the
                                       GUI queue BORROW it */
    uint32_t registered, subscribe_result, ui_result, ui_op, first_id, row_y;
    uintptr_t page;                 /* the Y2_5_1 copy switched in */
    int32_t task_id;
    uint8_t table[FPF_SLOTS];       /* set * 4 + custom -> value 0..3 */
    uint8_t saved[FPF_SLOTS];       /* what the file holds */
    uint32_t settings_ok;           /* the record in the file was valid */
    uint32_t live_key, edit_key;    /* last (set, mode) applied / (set, N) shown */
    uint32_t last_apply;            /* enum fpf_apply */
    uint32_t polls, switches, applies, refused, pushes, edits, saves, save_failed;
    uint32_t save_pending;
    uint32_t fobj[1024];            /* a file object (loader.S gives it 4 KiB) */
};

uint32_t fpf_want(uint32_t value);          /* layer bits 12..23 for a value */
uint32_t fpf_touch(uint32_t value);         /* 1 if the value turns the UI */
uint32_t fpf_apply(uint32_t value);         /* enum fpf_apply */
uint32_t fpf_changed(uintptr_t descriptor, struct fpf_card *c);  /* the row's callback; 0 */
void fpf_poll(struct fpf_card *c);          /* one pass of the task */
void fpf_task(struct fpf_card *c);          /* the task (entry.S task_shim): never returns */
/* entry.S, after the claims: the state (in the zeroed tail of the block, its
 * first four words filled), the blob, this sup's path. Always 0 = SL_KEEP:
 * from the registration on, the registry borrows the block. */
uint32_t fpf_card_init(struct fpf_card *c, uintptr_t base, const char *path);
#endif
