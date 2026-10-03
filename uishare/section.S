/* A sup's UI additions as one destination-zero section (uishare, 2026-10-03).
 *
 * build_section.py lays it out as
 *
 *     [this entry] [c_base: ui_pool.c + ui_apply.c, one unit, no relocations]
 *     [the sup's FPUI block]
 *
 * and passes OFF_APPLY (uia_apply's offset from the start, Thumb bit set),
 * BLOCK_OFF and BLOCK_LEN. The entry runs where the file landed, through the
 * loader's entry chain (stage2 / entries.S): it applies the block and returns.
 * Nothing it leaves behind points into the staging buffer: ui_apply copies the
 * pages, the CSVs and the file-redirect handler into memory of its own. */
.syntax unified
.arm
.text

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

.equ OUTCOME, 64                @ struct uia_outcome (52 bytes), 8-aligned

entry:
    push    {r4, r5, r6, lr}            @ 16 bytes: the stack stays 8-aligned
    sub     sp, sp, #OUTCOME
    adr     r4, entry                   @ where the section landed
    LDA     r0, BLOCK_OFF
    add     r0, r4, r0
    LDA     r1, BLOCK_LEN
    mov     r2, sp
    LDA     ip, OFF_APPLY
    add     ip, r4, ip
    blx     ip                          @ uia_apply(block, bytes, &outcome)
    add     sp, sp, #OUTCOME
    mov     r0, #0
    pop     {r4, r5, r6, pc}

.align 2
.global c_base
c_base:
