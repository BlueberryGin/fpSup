/* Skip exactly ONE frame's write, and see what the camera makes of the gap.
 *
 * THE QUESTION
 *
 * The target architecture hijacks a frame, compresses it while later frames pass
 * through, and submits it late. That needs a legal way to not write a frame now.
 * Three objections were on record against simply not writing one:
 *
 *   - it would leave an empty DNG. Eliminated: C069ADE0 opens, writes and closes
 *     the file itself, so a call that never happens creates no file.
 *   - it would unbalance the completion accounting. Eliminated statically on
 *     2026-09-29: the two observer slots at manager+4 and +8 are zeroed by the
 *     constructor and never written, so C03A52D0 notifies nobody.
 *   - it would leave a GAP in the _%06d frame numbering. This is the one left,
 *     and it is a question about the camera, not about the code.
 *
 * So this skips one frame and stops. What the card shows afterwards - a gap, a
 * renumbering, a clip that plays, a clip that does not - is the answer.
 *
 * HOW THE SKIP WORKS
 *
 * The hook replaces the `bl C069ADE0` at 0xC03A5490, so returning with bx lr
 * lands on the instruction AFTER the call, which is where the caller reads the
 * 64-bit result. Zero means success there: the aggregated path at 0xC03A54F4
 * takes `orrs r3, r6, r7; bne` to its failure branch. So r0 = r1 = 0 and no
 * call at all is a write that the caller believes succeeded and that never
 * touched the card.
 *
 * WHAT IT DOES NOT DO
 *
 * No encode, no allocation, no buffer of ours anywhere, nothing written to the
 * frame or the card. One frame of one clip is missing; every other frame takes
 * the camera's own path untouched. The hook restores itself on every entry and
 * is deliberately not re-armed after the skip.
 */

#ifndef STATE
#define STATE 0xC072F700
#endif
/* No usable default. 0xEB000000 used to be the default here and that is
 * exactly how this probe failed on 2026-09-29: the host reused a builder that
 * did not pass HOOK_ARMED, the default was baked in, and since a BL with a zero
 * offset targets site+8, every frame after the first had its write skipped with
 * a corrupted lr. The camera sat with the card light on waiting for completions
 * that could never arrive.
 *
 * So the default is 0 and the re-arm below refuses to write it. A probe that
 * cannot re-arm does nothing; a probe that re-arms with a wrong word breaks
 * every frame in the clip. */
#ifndef HOOK_ARMED
#define HOOK_ARMED 0
#endif
/* Which hook entry to skip. The third, so that two frames are known to have
 * gone through normally first and the gap has neighbours on both sides. */
#ifndef SKIP_AT
#define SKIP_AT 3
#endif
#ifndef ENTRY_LIMIT
#define ENTRY_LIMIT 12
#endif

.equ HOOK_SITE,       0xC03A5490
.equ HOOK_ORIG,       0xEB0BD652
.equ REAL_FLUSH,      0xC069ADE0

.equ FRAME_ID,        0x10FC            /* frame[0x10FC], the frame id */
.equ FRAME_BUF,       0x68              /* frame[0x68], its RAW buffer */
.equ FRAME_STATE,     0x1104
.equ STATE_RELEASED,  0x12              /* what the card layer leaves behind */
.equ PROOF_MAGIC,     0x31504B53        /* "SKP1" */

.equ S_COUNT,         0x00
.equ S_WRITER,        0x04
.equ S_FRAME,         0x08
.equ S_LR,            0x0C
.equ S_RESTORED,      0x10
.equ S_PROOF,         0x14              /* seeded by the host */
.equ S_SKIPPED,       0x18              /* "SKP1" once the skip happened */
.equ S_SKIP_ID,       0x1C              /* the skipped frame's id */
.equ S_SKIP_BUF,      0x20              /* and its buffer */
.equ S_SKIP_STATE,    0x24              /* its state word on the way in */
.equ S_PASSED,        0x28              /* frames passed through before it */
.equ S_STAGE,         0x2C
.equ S_RELEASED_SET,  0x30              /* 1 = we wrote 0x12 ourselves */

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

.global probe_entry
probe_entry:
    push    {r0, r1, r2, r3, r4, r5, r6, r7, r12, lr}
    LDA     r4, STATE
    ldr     r3, [r4, #S_COUNT]
    add     r3, r3, #1
    str     r3, [r4, #S_COUNT]
    str     r0, [r4, #S_WRITER]
    str     r1, [r4, #S_FRAME]
    str     lr, [r4, #S_LR]
    mov     r0, #1
    str     r0, [r4, #S_STAGE]

    /* Restore before anything else, every entry. */
    LDA     r5, HOOK_SITE
    LDA     r6, HOOK_ORIG
    str     r6, [r5]
    dsb     sy
    isb
    ldr     r7, [r5]
    str     r7, [r4, #S_RESTORED]
    cmp     r7, r6
    bne     pass_through

    ldr     r0, [r4, #S_PROOF]
    LDA     r1, PROOF_MAGIC
    cmp     r0, r1
    bne     pass_through

    /* Already skipped once? Then this probe is finished: never twice. */
    ldr     r0, [r4, #S_SKIPPED]
    cmp     r0, r1
    beq     pass_through

    ldr     r3, [r4, #S_COUNT]
    cmp     r3, #SKIP_AT
    blo     pass_through               /* not yet: let it through, re-armed */
    bhi     pass_through               /* past it and never skipped: give up */

    /* ---- the skip ---------------------------------------------------- */
    ldr     r1, [r4, #S_FRAME]
    cmp     r1, #0
    beq     pass_through
    LDA     r0, FRAME_ID
    ldr     r0, [r1, r0]
    str     r0, [r4, #S_SKIP_ID]
    ldr     r0, [r1, #FRAME_BUF]
    str     r0, [r4, #S_SKIP_BUF]
    LDA     r0, FRAME_STATE
    ldr     r0, [r1, r0]
    str     r0, [r4, #S_SKIP_STATE]
    ldr     r0, [r4, #S_COUNT]
    sub     r0, r0, #1
    str     r0, [r4, #S_PASSED]
    LDA     r0, PROOF_MAGIC
    str     r0, [r4, #S_SKIPPED]

    /* Hand the frame's buffer back, because nothing else will.
     *
     * The first run of this probe skipped one write and the recording stopped
     * immediately. The frame it skipped was measured stuck at state 0x0C - it
     * never reached 0xD, let alone the 0x12 the card layer writes after its
     * teardown, and the CinemaDNG manager at 0xC03A11F0 reclaims a frame's
     * buffer ONLY from frames in 0x12, reading frame[0x68]. So the skip leaked
     * that buffer.
     *
     * This writes the terminal state the card layer would have written. If the
     * recording then survives, the stop was the consequence of the leak rather
     * than a decision by some check, and this is the legal skip the design
     * needs. If it still stops, there is an explicit check elsewhere and this
     * rules the leak out as its cause.
     *
     * Only the state word: none of the five teardown calls, no notification.
     * The smallest change that can distinguish the two explanations. */
    LDA     r0, FRAME_STATE
    mov     r2, #STATE_RELEASED
    str     r2, [r1, r0]
    mov     r0, #1
    str     r0, [r4, #S_RELEASED_SET]

    mov     r0, #9
    str     r0, [r4, #S_STAGE]
    dsb     sy

    /* Return a successful write without performing one. The hook is NOT
     * re-armed: exactly one frame is missing and the rest of the clip is the
     * camera's own. */
    pop     {r0, r1, r2, r3, r4, r5, r6, r7, r12, lr}
    mov     r0, #0
    mov     r1, #0
    bx      lr

pass_through:
    /* Re-arm so the next frame is seen, until the skip has happened or the
     * entry ceiling is reached. Two ceilings, because one counter that can be
     * scribbled is not a bound. */
    ldr     r0, [r4, #S_SKIPPED]
    LDA     r1, PROOF_MAGIC
    cmp     r0, r1
    beq     no_rearm
    ldr     r0, [r4, #S_COUNT]
    cmp     r0, #ENTRY_LIMIT
    bhs     no_rearm
    LDA     r1, HOOK_ARMED
    cmp     r1, #0
    beq     no_rearm                   /* never write a word we were not given */
    LDA     r0, HOOK_SITE
    str     r1, [r0]
    dsb     sy
    isb
    ldr     r2, [r0]                   /* and prove it landed */
    cmp     r2, r1
    beq     no_rearm
    LDA     r1, HOOK_ORIG              /* it did not: leave the site stock */
    str     r1, [r0]
    dsb     sy
    isb
no_rearm:
    pop     {r0, r1, r2, r3, r4, r5, r6, r7, r12, lr}
    ldr     pc, [pc, #-4]
    .word   REAL_FLUSH
