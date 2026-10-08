.syntax unified
.arm
.text
/* ============================================================================
 * fpScreenFlip launcher: the first half of lossless/native/card.S, and nothing
 * else -- no hook site, no veneer, no task.
 *
 * Runs where the file landed (a destination-zero --boot-bin section, called
 * by the loader's entry trampoline). Asks the allocator for its own USER
 * block, copies the whole blob there, zeroes the rest (the card state at
 * STATE_OFF and the menu area after it), publishes D/I, and calls the
 * resident fpf_card_init(state, block, BLOCK_BYTES). The block is never
 * freed: the registry borrows the variable name in it, and the variable
 * callback lives in it. Refused allocation: stand down, camera stock.
 *
 * The blob is [this launcher][c_base: flip.c + uishare ui_pool.c/ui_apply.c]
 * [the row's FPUI block]; the launcher points state->ui_at/ui_len at the
 * block's resident copy before the call.
 *
 * Passed in by the builder: BLOB_LEN, BLOCK_BYTES, STATE_OFF, OFF_INIT (the
 * init's offset from c_base, Thumb bit set), UI_OFF / UI_LEN (the block).
 * ========================================================================== */

.equ H_GET,        0xC001D740    /* (desc, class, size, 0)                     */
.equ H_ADDR,       0xC001D7F0    /* (desc) -> address                          */
.equ USER_CLASS,   0             /* NOT class 10: that is the frames' pool     */
.equ F_DCACHE,     0xC000E91C
.equ F_ICACHE,     0xC000EABC

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

entry:
    push    {r3, r4, r5, r6, r7, r8, r9, r10, r11, lr}   @ ten: 8-aligned
    sub     sp, sp, #16                 @ the allocator descriptor, kept
    mov     r0, sp
    mov     r1, #USER_CLASS
    LDA     r2, BLOCK_BYTES
    mov     r3, #0
    LDA     ip, H_GET
    blx     ip
    mov     r0, sp
    LDA     ip, H_ADDR
    blx     ip
    movs    r5, r0                      @ r5 = the resident base
    beq     out
    tst     r5, #7
    bne     out

    adr     r6, entry
    mov     r7, r5
    LDA     r8, BLOB_LEN
1:  ldr     ip, [r6], #4
    str     ip, [r7], #4
    subs    r8, r8, #4
    bne     1b
    LDA     r8, BLOCK_BYTES
    add     r8, r5, r8
    mov     ip, #0
2:  cmp     r7, r8
    strlo   ip, [r7], #4
    blo     2b

    LDA     r0, STATE_OFF               @ state->ui_at / ui_len: the row's
    add     r0, r5, r0                  @ FPUI block, in the resident copy
    LDA     ip, UI_OFF
    add     ip, r5, ip
    str     ip, [r0]
    LDA     ip, UI_LEN
    str     ip, [r0, #4]

    LDA     ip, F_DCACHE
    blx     ip
    LDA     ip, F_ICACHE
    blx     ip

    LDA     r0, STATE_OFF
    add     r0, r5, r0                  @ the card state
    mov     r1, r5
    LDA     r2, BLOCK_BYTES
    adr     ip, c_base
    adr     r3, entry
    sub     ip, ip, r3                  @ c_base's offset in the blob
    add     ip, r5, ip                  @ the resident C text
    LDA     r3, OFF_INIT
    add     ip, ip, r3
    blx     ip

out:
    add     sp, sp, #16
    mov     r0, #0
    pop     {r3, r4, r5, r6, r7, r8, r9, r10, r11, pc}

    .align  3
c_base:
    /* the compiled C, then the FPUI block, are appended here by screenflip/build_card.py */
