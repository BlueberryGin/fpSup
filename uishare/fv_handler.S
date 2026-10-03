/* The shared file-redirect handler (uishare, 2026-10-03).
 *
 * The NBR resource reader's "where is this file" (FUN_c05e5bb8) ends with
 *
 *     C05E5BE8  ldr.w r1, [r4, #0xb4]      base of the resource pack
 *     C05E5BEC  adds  r0, r0, r1           r0 = file address
 *     C05E5BEE  str   r0, [r5, #4]         view->data
 *     C05E5BF0  ldr   r0, [sp]             view->size = the size on the stack
 *
 * C05E5BEC is replaced by a Thumb B.W to a veneer in the cave (ldr.w pc,[pc])
 * that enters this handler in ARM state. It adds as the firmware does, then
 * looks the address up in the shared table: a hit hands the reader a copy a
 * sup composed (CSV rows appended by several sups, an icon under a borrowed
 * name) and its size; anything else passes untouched. Then it does the
 * displaced store and returns to Thumb C05E5BF0 with the stack as it was.
 *
 * raw-view's stub_fv (camera-tested 2026-09-25..27) did this for its own four
 * files; this is the same instruction sequence with the table made shared.
 * Free: r1-r3, ip; flags are not used after the site.
 *
 * Layout of the block ui_apply.c allocates (never freed):
 *
 *     +0   "FSHK"  +4 version 1  +8 the table's address  +12 0
 *     +16  this code (fv_entry)
 *     table: "FSFV" | version 1 | count | capacity | {stock, copy, size} * capacity
 */
.syntax unified
.arm
.text

.global fv_head
fv_head:
    .word   0x4B485346              @ "FSHK"
    .word   1
fv_table:
    .word   0                       @ filled in by ui_apply
    .word   0

.global fv_entry
fv_entry:
    add     r0, r0, r1              @ the displaced adds
    ldr     r2, fv_table
    ldr     ip, [r2, #8]            @ entries in use
    add     r3, r2, #16
1:  subs    ip, ip, #1
    bmi     3f
    ldr     r1, [r3], #12
    cmp     r0, r1
    bne     1b
    ldr     r0, [r3, #-8]           @ the composed copy
    ldr     r1, [r3, #-4]
    str     r1, [sp]                @ and its size, where the reader takes it
3:  str     r0, [r5, #4]            @ the displaced str
    ldr     pc, fv_return
fv_return:
    .word   0xC05E5BF1              @ Thumb C05E5BF0
.global fv_end
fv_end:
