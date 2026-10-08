/* How much will the allocator actually give?
 *
 * The 2026-09-29 measurement of the SD aggregation batch puts a number on the
 * buffer pool an asynchronous design needs: 64 MiB, because a slot must
 * survive until the batch holding it is flushed and the batch threshold is
 * vol[0x4828] = 0x04000000. Two 4 MiB blocks are the most this project has
 * ever obtained, and one block is capped at 16 MiB (measured 2026-09-29).
 * So the pool would have to be a LIST, and the question this asks is how
 * many blocks of a given size can be held AT ONCE.
 *
 * WHAT IT DOES AND DOES NOT DO
 *
 * It allocates and then FREES EVERY BLOCK, in the same call, and returns how
 * many it got. It retains nothing, writes no lifecycle record and leaves
 * nothing held: a capability question does not need a block kept, and a probe
 * that keeps one owes a release it cannot be trusted to remember. The first
 * refusal stops the asking and the free runs on exactly what was granted, so
 * a failure can never free something that was never obtained.
 *
 * It is callfn-invoked, not a hook. No clip is recorded and no frame is
 * touched.
 *
 * ABI
 *   r0 in   the size in bytes
 *   r1 in   the alignment (0 means 0x400, what every other probe asks for)
 *   r2 in   how many blocks to hold AT ONCE (0 or 1 means one)
 *   r0 out  how many were granted
 *
 * SCRATCH layout, words:
 *   0  granted count      1  allocator      2  worst free return
 *   3  the size asked for 4  the count asked for
 *   8.. one handle per block, in the order granted
 *
 * callfn reports r0 only, so everything else comes back through SCRATCH.
 * SCRATCH is the caller's result area, passed in so nothing here
 * picks an address -- the rule that cost a shell hang on 2026-09-22. SCRATCH
 * is one of the three names the safety checks know, which is why it is that
 * name and not a better-reading one.
 */

#ifndef SCRATCH
.equ SCRATCH, 0xC072F700        /* standalone default; the caller passes one */
#endif

.equ F_ALLOC,         0xC001CF78
.equ F_GET,           0xC001D038
.equ F_FREE,          0xC001D2B8

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

.equ MAX_BLOCKS,      8            /* the handle table's own bound */
.equ H_COUNT,         0x00
.equ H_ALLOC,         0x04
.equ H_FREE_RET,      0x08
.equ H_SIZE,          0x0C
.equ H_WANT,          0x10
.equ H_TABLE,         0x20

.global probe_entry
probe_entry:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}
    mov     r4, r0                     /* r4 = requested size */
    movs    r5, r1
    moveq   r5, #0x400                 /* r5 = alignment */
    movs    r9, r2
    moveq   r9, #1                     /* r9 = how many to hold at once */
    mov     r10, #0                    /* worst free return; done reads it on
                                        * the allocator-failure path too */
    cmp     r9, #MAX_BLOCKS
    movhi   r9, #MAX_BLOCKS            /* the table is the bound, not the
                                        * caller: a count past its end would
                                        * write over whatever follows */

    LDA     r7, SCRATCH
    mov     r0, #0
    str     r0, [r7, #H_COUNT]
    str     r0, [r7, #H_ALLOC]
    str     r0, [r7, #H_FREE_RET]
    str     r4, [r7, #H_SIZE]          /* echo both inputs back, so a caller
                                        * that read the wrong slot can tell */
    str     r9, [r7, #H_WANT]
    mov     r8, #0                     /* r8 = how many are held */
clear_table:
    add     r0, r7, r8, lsl #2
    mov     r1, #0
    str     r1, [r0, #H_TABLE]
    add     r8, r8, #1
    cmp     r8, #MAX_BLOCKS
    blo     clear_table

    mov     r0, #0
    LDA     r12, F_ALLOC
    blx     r12
    mov     r6, r0                     /* r6 = allocator */
    str     r6, [r7, #H_ALLOC]
    cmp     r6, #0
    beq     done

    mov     r8, #0
take_one:
    cmp     r8, r9
    bhs     give_back
    sub     sp, sp, #8
    mov     r0, r6
    mov     r1, r4
    mov     r2, r5
    mov     r3, #0
    mov     r12, #0
    str     r12, [sp]                  /* the fifth argument is stacked */
    LDA     r12, F_GET
    blx     r12
    add     sp, sp, #8
    cmp     r0, #0
    beq     give_back                  /* refused: stop asking, free what we
                                        * have. A partial answer is the answer */
    add     r1, r7, r8, lsl #2
    str     r0, [r1, #H_TABLE]
    add     r8, r8, #1
    str     r8, [r7, #H_COUNT]
    b       take_one

    /* Give every one of them straight back, newest first. Nothing here is
     * allowed to outlive the call: a probe that asks for 64 MiB and keeps it
     * has taken the allocator away from everything else for the whole boot. */
give_back:
free_one:
    cmp     r8, #0
    beq     done
    sub     r8, r8, #1
    add     r1, r7, r8, lsl #2
    ldr     r1, [r1, #H_TABLE]
    cmp     r1, #0
    beq     free_one
    mov     r0, r6
    LDA     r12, F_FREE
    blx     r12
    cmp     r0, #0
    movne   r10, r0                    /* keep any non-zero return */
    b       free_one

done:
    str     r10, [r7, #H_FREE_RET]
    dsb     sy
    ldr     r0, [r7, #H_COUNT]
    pop     {r4, r5, r6, r7, r8, r9, r10, pc}
