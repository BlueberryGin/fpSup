/* Can we write into the camera's OWN frame buffer, and shorten the node,
 * at the FLUSH site?
 *
 * WHY THIS AND WHY HERE
 *
 * The allocator gives 16 MiB in total (2026-09-29) and one SD aggregation
 * batch is 64 MiB (2026-09-29), so a pool of our own buffers cannot be one
 * batch deep. The way out is to put the compressed frame back into the
 * camera's own buffer, which the write path already owns and which therefore
 * already survives to the batch flush. That costs no pool at all.
 *
 * Two things have to be true for it, and neither has been tested HERE:
 *
 *   1. writing into the camera's own frame buffer must not freeze the camera
 *   2. the writer must honour a SHORTER node length
 *
 * Both have history, and all of it is at the OTHER site. copy-back wrote the
 * camera's seg structure at the CinemaDNG writer 0xC0722AFC and froze, one
 * success in five; copy-back-length-only froze with the copy removed, which
 * isolated the seg[1] write as the cause. G4b changed a node length at that
 * same site, into OUR buffer, and it was accepted.
 *
 * This site is different. 0xC03A5490 is the final flush, where the writer
 * object is the caller's own stack object and the single node sits at
 * writer+0x0C -- not the camera's seg. On 2026-09-29 the two-slot mirror
 * rewrote that node's BUFFER POINTER forty times with no trouble at all. What
 * it never did was write into the camera's buffer or touch the length.
 *
 * THE THREE MODES, so that a freeze has ONE candidate cause
 *
 *   MODE 1  shorten the node length; do not touch the buffer
 *   MODE 2  watermark the camera's buffer; leave the length alone
 *   MODE 3  both, which is the real operation
 *
 * MODE 2 is the safe one to run first: the mark lands in the trailing padding
 * after the raster, which no decoder reads, so the clip stays valid and only
 * the risky half is under test. MODE 1 and MODE 3 truncate the file and the
 * clip will NOT be playable; that is expected and is not a failure.
 *
 * Nothing is allocated. No codec runs. No block is needed.
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
#ifndef MODE
#define MODE 2
#endif

.equ HOOK_SITE,       0xC03A5490
.equ HOOK_ORIG,       0xEB0BD652
.equ REAL_FLUSH,      0xC069ADE0

.equ DNG_LEN,         0x318200         /* the only length this probe accepts */
/* 1,396,736 bytes: the size of the compressed CinemaDNG G4b actually wrote on
 * 2026-09-23, so the shortened length under test is a real one and not a
 * round number. 1 KiB aligned, as every buffer in this project is. */
.equ SHORT_LEN,       0x155000
.equ UNCACHED,        0x40000000
.equ MARK_MAGIC,      0x334C534D       /* "MSL3" */
#if MODE == 2
.equ MARK_OFF,        DNG_LEN - 16     /* trailing padding; the clip stays valid */
#else
.equ MARK_OFF,        SHORT_LEN - 16   /* the last word of the shortened file */
#endif

.equ S_COUNT,         0x00
.equ S_WRITER,        0x04
.equ S_FRAME,         0x08
.equ S_LR,            0x0C
.equ S_RESTORED,      0x10
.equ S_ERROR,         0x14
.equ S_STAGE,         0x18
.equ S_DONE,          0x1C             /* frames actually altered */
.equ S_NODE_BUF,      0x20
.equ S_NODE_LEN,      0x24             /* the length BEFORE we touched it */
.equ S_LEN_AFTER,     0x28             /* read back after the store */
.equ S_SHAPE_BAD,     0x2C
.equ S_MODE,          0x30             /* which experiment this image is */
.equ S_PROOF,         0x34             /* "MSL3", seeded; refuses without it */

.equ ERR_SHAPE,        2

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
    mov     r0, #MODE
    str     r0, [r4, #S_MODE]

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

    ldr     r0, [r4, #S_DONE]
    cmp     r0, #FRAMES
    bhs     call_real                  /* bounded, and the bound is the point */

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
    ldr     r5, [r2, #0x04]            /* node buffer: the CAMERA'S OWN */
    ldr     r3, [r2, #0x08]            /* node length */
    str     r5, [r4, #S_NODE_BUF]
    str     r3, [r4, #S_NODE_LEN]
    cmp     r5, #0
    beq     shape_bad
    tst     r5, #3
    bne     shape_bad
    LDA     r0, DNG_LEN
    cmp     r3, r0
    bne     shape_bad                  /* only the exact stock length */

    mov     r0, #3
    str     r0, [r4, #S_STAGE]

#if MODE != 1
    /* ---- write into the CAMERA'S OWN buffer -------------------------
     * Two words, through the uncached alias, exactly as G4a established CPU
     * writes reach the card's DMA: a barrier and no cache maintenance. The
     * offset is inside the length the node declares, so this cannot run past
     * the buffer the camera gave us. */
    add     r6, r5, #UNCACHED
    LDA     r0, MARK_OFF
    add     r6, r6, r0
    LDA     r0, MARK_MAGIC
    ldr     r1, [r4, #S_DONE]
    str     r0, [r6]
    str     r1, [r6, #4]
    dsb     sy
    mov     r0, #4
    str     r0, [r4, #S_STAGE]
#endif

#if MODE != 2
    /* ---- shorten the node ------------------------------------------- */
    ldr     r0, [r4, #S_WRITER]
    ldr     r2, [r0, #0x8C]            /* head, re-read after the write */
    LDA     r1, SHORT_LEN
    str     r1, [r2, #0x08]
    dsb     sy
    ldr     r1, [r2, #0x08]
    str     r1, [r4, #S_LEN_AFTER]     /* what the node reads back as */
    mov     r0, #5
    str     r0, [r4, #S_STAGE]
#endif

    ldr     r0, [r4, #S_DONE]
    add     r0, r0, #1
    str     r0, [r4, #S_DONE]
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
    ldr     r0, [r4, #S_DONE]
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
    pop     {r0, r1, r2, r3, r4, r5, r6, r7, r12, lr}
    ldr     pc, [pc, #-4]
    .word   REAL_FLUSH
