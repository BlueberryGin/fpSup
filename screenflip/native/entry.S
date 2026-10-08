.syntax unified
.arm
.text
@ entry is at offset 0; not global, so every adr to it resolves here
/* ============================================================================
 * fpScreenFlip, Loader v3 entry (projects/usb-shell-sup/notes/LOADER_V3.md §4).
 *
 * The 加載器 read this file into a block of its own and zeroed the rest, so
 * the code already runs where it stays: no allocation, no copy. The only
 * firmware words written are the string-layer sites ui_apply hangs.
 * r0 = the block, r1 = the service table, r2 = this
 * sup's path (service v2). Returns SL_KEEP (0) / SL_RELEASE (1).
 *
 *   1. claim_res the panel layer register and the touch 180 flag, EXCL:
 *      another sup that drives them (lcdflip, a second screenflip) means
 *      "do not load me"; then claim the three uishare string-layer sites
 *      SHARED_UI (`claims`). Any refusal: return RELEASE having written nothing.
 *   2. fill the state's first four words (the FPUI block, the two shims) and
 *      g_state, publish, call fpf_card_init(state, entry, path): it returns
 *      1 (RELEASE) only before it registered anything.
 *
 * Laid out by build_v3_flip.py:
 *   [this][c_base: flip.c + uishare ui_pool.c/ui_apply.c, one unit]
 *   [UI_OFF: the row's FPUI block] ... [STATE_OFF: struct fpf_card, zeroed]
 * Passed in: STATE_OFF, UI_OFF, UI_LEN and the C entries' offsets from c_base
 * (OFF_INIT, OFF_TASK, OFF_CHANGED; Thumb, bit 0 set).
 * ========================================================================== */

.equ F_DCACHE,     0xC000E91C
.equ SVC_CLAIM,    8             /* struct sl_svc (fp_usb_shell/v3/sloader.h) */
.equ SVC_RES,      12
.equ SL_EXCL,      2
.equ SL_SHARED_UI, 3
.equ SL_RELEASE,   1
.equ LAYER_REG,    0x30190044    /* claim_res ids: the resources themselves   */
.equ TOUCH_FLAG,   0xC3760E44

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

entry:
    push    {r4, r5, r6, r7, r8, lr}    @ six: 8-aligned
    mov     r6, r1                      @ r6 = the service table
    mov     r7, r2                      @ r7 = this sup's path
    adr     r5, entry                   @ r5 = the blob
    tst     r5, #7
    bne     refuse

    /* 1. the two resources, before anything is written */
    mov     r0, r6
    LDA     r1, LAYER_REG
    mov     r2, #SL_EXCL
    ldr     ip, [r6, #SVC_RES]
    blx     ip
    cmp     r0, #0
    bne     refuse
    mov     r0, r6
    LDA     r1, TOUCH_FLAG
    mov     r2, #SL_EXCL
    ldr     ip, [r6, #SVC_RES]
    blx     ip
    cmp     r0, #0
    bne     refuse

    /* ... and the firmware words ui_apply hangs the row's strings on (the
     * uishare string layers, SHARED_UI): journaled with their stock words,
     * put back at power-off, and how the next sup's uishare finds our layer */
    adr     r4, claims
    ldr     r8, [r4], #4
1:  ldr     r1, [r4]                    @ address
    mov     r2, #4
    add     r3, r4, #4                  @ its stock word, from this table
    ldr     r0, [r4, #8]                @ kind: the fifth argument
    sub     sp, sp, #8
    str     r0, [sp]
    mov     r0, r6
    ldr     ip, [r6, #SVC_CLAIM]
    blx     ip
    add     sp, sp, #8
    cmp     r0, #0
    bne     refuse
    add     r4, r4, #12
    subs    r8, r8, #1
    bne     1b

    /* 2. the state's first four words, the shims' words */
    LDA     r4, STATE_OFF
    add     r4, r5, r4                  @ r4 = the state
    LDA     r0, UI_OFF
    add     r0, r5, r0
    str     r0, [r4]                    @ ui_at
    LDA     r0, UI_LEN
    str     r0, [r4, #4]                @ ui_len
    adr     r0, task_shim
    str     r0, [r4, #8]                @ task_entry
    adr     r0, changed_shim
    str     r0, [r4, #12]               @ changed_entry
    str     r4, g_state
    adr     r8, c_base
    LDA     r0, OFF_TASK
    add     r0, r8, r0
    str     r0, w_task
    LDA     r0, OFF_CHANGED
    add     r0, r8, r0
    str     r0, w_changed
    LDA     ip, F_DCACHE
    blx     ip

    mov     r0, r4
    mov     r1, r5
    mov     r2, r7
    LDA     ip, OFF_INIT
    add     ip, r8, ip
    blx     ip                          @ 0 keep, 1 release
    pop     {r4, r5, r6, r7, r8, pc}

refuse:
    mov     r0, #SL_RELEASE
    pop     {r4, r5, r6, r7, r8, pc}

/* The task (tk_cre_tsk): fpf_task(state). */
task_shim:
    ldr     r0, g_state
    ldr     ip, w_task
    bx      ip

/* MV_fpScreenFlip written (C0560FB0, UI thread): fpf_changed(descriptor, state). */
changed_shim:
    ldr     r1, g_state
    ldr     ip, w_changed
    bx      ip

    .align  2
g_state:    .word 0
w_task:     .word 0
w_changed:  .word 0
/* The flip value of each custom, as saved in this file (flip.c
 * FPF_SETTINGS_OFF): {"FFST", version 1, STILL Custom 1..4, CINE Custom
 * 1..4 (a byte each), check}. The task rewrites these 20 bytes in place on
 * the card. Built all 0 (normal). */
settings:
    .word 0x54534646, 1, 0, 0, 0x54534646 ^ 1 ^ 0 ^ 0 ^ 0xFFFFFFFF
/* What this sup changes in the firmware image: { address, stock word, kind }.
 * build_v3_flip.py checks it against uishare build_section.sites(the row's
 * block) and the reference image. */
claims:
    .word 3
    .word 0xC05E5B58,   0x3FFFF1B1,    SL_SHARED_UI   @ uishare string layers
    .word 0xC05E61C8,   0x428A6942,    SL_SHARED_UI
    .word 0xC05E61E0,   0x69406902,    SL_SHARED_UI

    .align  3
c_base:
    /* the compiled C, then the FPUI block: build_v3_flip.py */
