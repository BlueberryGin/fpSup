/* Two output slots, released by the frame's own state word. Mirror only.
 *
 * WHAT IS BEING TESTED, AND WHAT IS DELIBERATELY NOT
 *
 * Compression is not being tested. G4b wrote a valid compressed CinemaDNG from
 * our own buffer on 2026-09-23 and LibRaw decoded it. What has never worked is
 * REUSE: G5 ran eight encodes from one buffer and frames 1, 2, 4 and 8 came out
 * with identical content, with one length disagreeing with the file. That is a
 * slot-cycling failure, not a codec failure, so this probe reproduces G5's
 * shape with the codec removed and a real release signal added.
 *
 * So each frame is copied byte for byte into a slot and the writer's node is
 * pointed at the copy, with the same length - G4a's trick, which proves the
 * redirect took by watermarking the copy. Swapping the copy for the proven
 * encode afterwards is a small change; doing both at once would leave two
 * explanations for one failure.
 *
 * THE RELEASE SIGNAL
 *
 * A slot goes back to FREE when the frame it was handed to reaches state 0x12
 * in frame[0x1104]. That state was located statically on 2026-09-28: the card
 * layer writes it after five teardown calls, and the CinemaDNG manager at
 * 0xC03A11F0 reclaims a frame's buffer ONLY from frames in that state, reading
 * frame[0x68]. The submit-time state 0xD is NOT used: it is written the moment
 * the flush returns, and G3 measured that return at 372 us for 3.2 MB, so it
 * means queued, not written.
 *
 * That reading is an inference. This probe is how it gets tested: if 0x12 comes
 * too early the frames on the card are corrupt, and if it never comes the run
 * stops after two frames with both slots HELD. Both outcomes are legible.
 *
 * THE SITE
 *
 * 0xC03A5490, the final flush, where G3 established the shape live: r0 is the
 * writer object the caller built on its own stack, r1 is the frame, the single
 * node sits at writer+0x0C and writer[0x90] counts them. The frame pointer is
 * an argument here, which is why this site and not the CinemaDNG writer hook:
 * a slot has to know which frame to watch, and at the other site the frame is
 * not available.
 *
 * Nothing is allocated. Both slots are blocks the host acquired and recorded
 * beforehand, and their bases are seeded into the state block.
 */

#ifndef STATE
#define STATE 0xC072F700
#endif
#ifndef HOOK_ARMED
#define HOOK_ARMED 0xEB000000
#endif
#ifndef FRAMES
#define FRAMES 8
#endif

.equ HOOK_SITE,       0xC03A5490
.equ HOOK_ORIG,       0xEB0BD652
.equ REAL_FLUSH,      0xC069ADE0

.equ DNG_LEN,         0x318200         /* the only length this probe accepts */
.equ UNCACHED,        0x40000000
.equ FRAME_STATE,     0x1104
.equ STATE_RELEASED,  0x12
.equ TICK_VAR,        0xC30781A4       /* raw, what F_CLOCK is built on */
.equ MARK_MAGIC,      0x324C534D       /* "MSL2" */
.equ MARK_OFF,        DNG_LEN - 16     /* trailing padding; no decoder reads it */

/* State block. Its own layout: this probe hooks a different site from the
 * others and shares nothing with them but the cave. */
.equ S_COUNT,         0x00
.equ S_WRITER,        0x04
.equ S_FRAME,         0x08
.equ S_LR,            0x0C
.equ S_RESTORED,      0x10
.equ S_ERROR,         0x14
.equ S_STAGE,         0x18
.equ S_FRAMES,        0x1C             /* frames redirected into a slot */
.equ S_REFUSED,       0x20             /* frames left native: no slot free */
.equ S_RELEASED,      0x24             /* releases observed */
.equ S_SLOT_A,        0x28             /* seeded: slot 0 base */
.equ S_SLOT_B,        0x2C             /* seeded: slot 1 base */
.equ S_NODE_BUF,      0x30             /* the last frame's node, before redirect */
.equ S_NODE_LEN,      0x34
.equ S_LAST_SEEN,     0x38             /* frame[0x1104] of the last HELD slot */
.equ S_SHAPE_BAD,     0x3C             /* frames the shape check refused */
.equ S_PROOF,         0x40             /* "MSL2", seeded; refuses to run without */

.equ ERR_NO_SLOT_BASE, 1
.equ ERR_SHAPE,        2

/* Slot records, in slot 0's block. Two of 8 words. */
.equ SLOT_OFF,        0x3F6000
.equ SLOT_STRIDE,     0x20
.equ L_STATE,         0x00             /* 0 FREE, 1 HELD */
.equ L_FRAME,         0x04
.equ L_SEQ,           0x08
.equ L_BASE,          0x0C             /* seeded by the host */
.equ L_HELD_TICK,     0x10
.equ L_FREE_TICK,     0x14
.equ L_LAST_STATE,    0x18
.equ L_LEN,           0x1C

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

.global probe_entry
probe_entry:
    push    {r0, r1, r2, r3, r4, r5, r6, r7, r8, r10, r12, lr}
    LDA     r4, STATE
    ldr     r3, [r4, #S_COUNT]
    add     r3, r3, #1
    str     r3, [r4, #S_COUNT]
    str     r0, [r4, #S_WRITER]
    str     r1, [r4, #S_FRAME]
    str     lr, [r4, #S_LR]

    /* Restore first, always. A fault anywhere below then costs this frame and
     * not every frame after it. Re-arming happens at the very end. */
    LDA     r5, HOOK_SITE
    LDA     r6, HOOK_ORIG
    str     r6, [r5]
    dsb     sy
    isb
    ldr     r7, [r5]
    str     r7, [r4, #S_RESTORED]
    cmp     r7, r6
    bne     call_real

    /* The host's proof that it seeded this state block for this probe. */
    ldr     r0, [r4, #S_PROOF]
    LDA     r1, MARK_MAGIC
    cmp     r0, r1
    bne     call_real

    ldr     r0, [r4, #S_FRAMES]
    cmp     r0, #FRAMES
    bhs     call_real                  /* bounded, and the bound is the point */

    ldr     r10, [r4, #S_SLOT_A]
    cmp     r10, #0
    moveq   r0, #ERR_NO_SLOT_BASE
    streq   r0, [r4, #S_ERROR]
    beq     call_real
    add     r10, r10, #UNCACHED
    LDA     r0, SLOT_OFF
    add     r10, r10, r0               /* r10 = slot 0's record */

#ifdef ROUND_ROBIN
    /* ROUND ROBIN. No release pass, no free-slot search: frame N goes into
     * slot N mod 2, and overwrites whatever the frame two before it left
     * there. That is the whole experiment. If the card has not finished
     * reading the slot by then, the earlier frame's FILE ends up holding this
     * frame's pixels -- and the watermark's sequence number is what makes it
     * visible, because G5 established the corruption is otherwise silent:
     * every file still decodes, still carries twelve tiles, still has offsets
     * matching its SOI markers.
     *
     * Deliberately no release signal of any kind. 0x12 was measured on
     * 2026-09-29 never to arrive at this site, and the point here is to
     * measure how deep a pool has to be, not to look for a signal again. */
    ldr     r5, [r4, #S_FRAMES]
    and     r5, r5, #1
    add     r6, r10, r5, lsl #5
    b       have_slot
#else
    /* ---- release pass ------------------------------------------------
     * Every HELD slot is asked about its own frame. A slot whose frame has
     * reached 0x12 goes back to FREE, and when it did so is recorded: the
     * difference from L_HELD_TICK is the hold time, which is the number the
     * pool size and the compression duty cycle both come from. */
    mov     r5, #0                     /* slot index */
release_pass:
    add     r6, r10, r5, lsl #5        /* r6 = record (stride 0x20) */
    ldr     r0, [r6, #L_STATE]
    cmp     r0, #1
    bne     release_next
    ldr     r1, [r6, #L_FRAME]
    cmp     r1, #0
    beq     release_next
    LDA     r0, FRAME_STATE
    ldr     r0, [r1, r0]
    str     r0, [r4, #S_LAST_SEEN]
    cmp     r0, #STATE_RELEASED
    bne     release_next
    mov     r0, #0
    str     r0, [r6, #L_STATE]
    LDA     r0, TICK_VAR
    ldr     r0, [r0]
    str     r0, [r6, #L_FREE_TICK]
    ldr     r0, [r4, #S_LAST_SEEN]
    str     r0, [r6, #L_LAST_STATE]
    ldr     r0, [r4, #S_RELEASED]
    add     r0, r0, #1
    str     r0, [r4, #S_RELEASED]
release_next:
    add     r5, r5, #1
    cmp     r5, #2
    blo     release_pass
    dsb     sy

    /* ---- pick a free slot -------------------------------------------- */
    mov     r5, #0
pick_slot:
    add     r6, r10, r5, lsl #5
    ldr     r0, [r6, #L_STATE]
    cmp     r0, #0
    beq     have_slot
    add     r5, r5, #1
    cmp     r5, #2
    blo     pick_slot
    /* Both busy. The frame is left completely alone - native buffer, native
     * length - which is the RAW_PASS the admission contract calls for, and is
     * also the only honest thing to do with a frame we cannot hold. */
    ldr     r0, [r4, #S_REFUSED]
    add     r0, r0, #1
    str     r0, [r4, #S_REFUSED]
    b       call_real

#endif

have_slot:
    ldr     r7, [r6, #L_BASE]          /* the slot's own block base */
    cmp     r7, #0
    moveq   r0, #ERR_NO_SLOT_BASE
    streq   r0, [r4, #S_ERROR]
    beq     call_real

    /* ---- the writer's single node, checked exactly as G3 checked it ---- */
    ldr     r0, [r4, #S_WRITER]
    cmp     r0, #0
    beq     shape_bad
    tst     r0, #3
    bne     shape_bad
    ldr     r1, [r0, #0x90]            /* node count */
    cmp     r1, #1
    bne     shape_bad
    ldr     r2, [r0, #0x8C]            /* head */
    add     r3, r0, #0x0C
    cmp     r2, r3
    bne     shape_bad
    ldr     r1, [r2, #0x04]            /* node buffer */
    ldr     r3, [r2, #0x08]            /* node length */
    str     r1, [r4, #S_NODE_BUF]
    str     r3, [r4, #S_NODE_LEN]
    cmp     r1, #0
    beq     shape_bad
    tst     r1, #3
    bne     shape_bad
    LDA     r0, DNG_LEN
    cmp     r3, r0
    bne     shape_bad                  /* only the exact stock length */

    mov     r0, #3
    str     r0, [r4, #S_STAGE]

    /* ---- copy the frame into the slot, both sides uncached ------------
     * G4a established that CPU writes through the +0x40000000 view reach the
     * card's DMA with a barrier and no cache maintenance. The length is the
     * node's own, checked above, so this cannot run past either end. */
    add     r5, r1, #UNCACHED          /* source: the camera's buffer */
    add     r6, r7, #UNCACHED          /* destination: the slot */
    LDA     r8, DNG_LEN
mirror_copy:
    ldm     r5!, {r0, r1, r2, r3}
    stm     r6!, {r0, r1, r2, r3}
    subs    r8, r8, #16
    bne     mirror_copy

    /* A watermark, because a byte-identical copy cannot prove itself: if the
     * redirect never took, the card would receive the original and look
     * exactly the same. Two words in the trailing padding after the raster,
     * which no decoder reads - the magic, and the frame's own sequence, so the
     * files on the card say which frame each one is and a slot that was reused
     * for two frames cannot hide it. */
    add     r6, r7, #UNCACHED
    LDA     r0, MARK_OFF
    add     r6, r6, r0
    LDA     r0, MARK_MAGIC
    ldr     r1, [r4, #S_FRAMES]
    str     r0, [r6]
    str     r1, [r6, #4]
    dsb     sy

    /* ---- redirect, then record who holds what ------------------------- */
    ldr     r0, [r4, #S_WRITER]
    ldr     r2, [r0, #0x8C]            /* head, re-read after the copy */
    str     r7, [r2, #0x04]            /* node buffer -> our slot */
    dsb     sy

    LDA     r0, SLOT_OFF
    ldr     r10, [r4, #S_SLOT_A]
    add     r10, r10, #UNCACHED
    add     r10, r10, r0
    mov     r5, #0
find_again:
    add     r6, r10, r5, lsl #5
    ldr     r0, [r6, #L_BASE]
    cmp     r0, r7
    beq     record_slot
    add     r5, r5, #1
    cmp     r5, #2
    blo     find_again
    b       call_real                  /* cannot happen: r7 came from a record */

record_slot:
    mov     r0, #1
    str     r0, [r6, #L_STATE]
    ldr     r0, [r4, #S_FRAME]
    str     r0, [r6, #L_FRAME]
    ldr     r0, [r4, #S_FRAMES]
    str     r0, [r6, #L_SEQ]
    LDA     r0, TICK_VAR
    ldr     r0, [r0]
    str     r0, [r6, #L_HELD_TICK]
    ldr     r0, [r4, #S_NODE_LEN]
    str     r0, [r6, #L_LEN]
    mov     r0, #0
    str     r0, [r6, #L_FREE_TICK]
    dsb     sy

    ldr     r0, [r4, #S_FRAMES]
    add     r0, r0, #1
    str     r0, [r4, #S_FRAMES]
    mov     r0, #7
    str     r0, [r4, #S_STAGE]
    b       call_real

shape_bad:
    ldr     r0, [r4, #S_SHAPE_BAD]
    add     r0, r0, #1
    str     r0, [r4, #S_SHAPE_BAD]
    mov     r0, #ERR_SHAPE
    str     r0, [r4, #S_ERROR]

call_real:
    /* Re-arm last, so every risky instruction above ran with the site
     * unhooked. Stop re-arming once the bound is reached: the clip then
     * finishes on the camera's own path with nothing of ours in it. */
    ldr     r0, [r4, #S_FRAMES]
    cmp     r0, #FRAMES
    bhs     no_rearm
    ldr     r0, [r4, #S_COUNT]
    cmp     r0, #FRAMES * 3
    bhs     no_rearm                   /* a second ceiling, on hook entries */
    LDA     r0, HOOK_SITE
    LDA     r1, HOOK_ARMED
    str     r1, [r0]
    dsb     sy
    isb
no_rearm:
    pop     {r0, r1, r2, r3, r4, r5, r6, r7, r8, r10, r12, lr}
    ldr     pc, [pc, #-4]
    .word   REAL_FLUSH
