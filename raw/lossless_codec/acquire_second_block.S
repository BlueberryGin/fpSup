/* Acquire the bound probe's SECOND block, on the camera, and record it.
 *
 * Why this is a separate routine and not part of an image that fires at the
 * writer hook:
 *
 *   * The handle used to arrive as a command-line argument -
 *     `--source-handle 0x5xxxxxxx`, carried forward by hand from an earlier
 *     run's log. Range, alignment and non-overlap were checked, on the host
 *     and again on the camera, and all three together prove the SHAPE of a
 *     handle and nothing about whether it is ours.
 *   * Acquiring it inside the hook image was not possible: the hook images run
 *     from a 1,792-byte claim and the one that needs two blocks has twenty
 *     bytes of headroom. Allocation, validation and a record do not fit.
 *   * Running the scratch preflight twice, which is how two blocks would
 *     otherwise be obtained, conflicts with the gates: after the first scratch
 *     the state block is no longer a fresh power-preflight, and re-preflighting
 *     is refused by the retained-state gate. Clearing the state to get around
 *     that would throw away the evidence the gate exists to protect.
 *
 * So this runs like release does: the host calls it in task context through
 * callfn, in the same boot, and it writes a record the camera itself produced.
 * Nothing here is armed, nothing hooks the writer, and no frame is in flight.
 *
 * It refuses rather than repairs. A record that is already held, a block that
 * comes back the wrong shape, memory that will not take our mark: each one
 * returns a code and leaves nothing allocated that is not recorded.
 *
 * AAPCS: no arguments, a status word in r0. Preserves r4-r11 and returns with
 * bx lr, so the host's image checks for a hook image's tail-call must not be
 * applied to it.
 */

#ifndef STATE
#define STATE 0xC072F700
#endif

.equ ALLOC_SIZE,      0x00400000
.equ UNCACHED,        0x40000000

.equ F_CLOCK,         0xC002B6E0
.equ F_ALLOC,         0xC001CF78
.equ F_GET,           0xC001D038
.equ F_FREE,          0xC001D2B8

.equ S_COUNT,         0x00
.equ S_HANDLE,        0x34
.equ S_ALLOC_END,     0x38
.equ S_SCRATCH_PROOF, 0xD8
.equ SCRATCH_MAGIC,   0x434F4C41       /* "ALOC" */

/* Must match single_frame_encode_discard_probe.S and
 * exact_writer_scratch_preflight.S: this routine writes the record that the
 * hook image reads. */
.equ LIFE_OFF,        0x003F4000
.equ LIFE_B,          0x40
.equ L_MAGIC,         0x00
.equ L_ROLE,          0x04
.equ L_ALLOC,         0x08
.equ L_HANDLE,        0x0C
.equ L_BYTES,         0x10
.equ L_END,           0x14
.equ L_TICK,          0x18
.equ L_SEQ,           0x1C
.equ L_PURPOSE,       0x20
.equ L_OWNMARK,       0x24
.equ L_HELD,          0x28
.equ L_STATE,         0x2C
.equ L_BOOT,          0x38             /* LOAD_DONE_US: which boot
                                        * wrote this record. A short
                                        * power cycle leaves DRAM
                                        * intact, so a record from
                                        * the PREVIOUS boot reads as
                                        * valid in every other field
                                        * -- it names itself, carries
                                        * the mark, has a role and
                                        * says held. On 2026-09-30
                                        * one such record let an arm
                                        * through on a block this
                                        * boot never owned. */
.equ LOAD_DONE_US,    0xC072F6F8       /* stage2 stamps it at boot */
.equ L_FREERET,       0x30
.equ L_FREETICK,      0x34
.equ LIFE_MAGIC,      0x4546494C       /* "LIFE" */
.equ OWN_MAGIC,       0x4E574F42       /* "BOWN" */
.equ PURPOSE_SRC,     0x45435253       /* "SRCE" */
.equ OWN_OFF,         0x003FC000
.equ LIFE_HELD,       1

/* Status words. Distinct from the probe's ERR_* numbering on purpose: these
 * are returned to the host by a call, not left in the state block. */
.equ ACQ_OK,          0
.equ ACQ_NO_SCRATCH,  1                /* no retained block A in state */
.equ ACQ_BAD_RECORD,  2                /* A's record is missing or not held */
.equ ACQ_BAD_MARK,    3                /* A does not carry our mark */
.equ ACQ_CLOCK_BACK,  4                /* the tick went backwards: other boot */
.equ ACQ_B_PRESENT,   5                /* a second block is already recorded */
.equ ACQ_NO_ALLOCATOR, 6
.equ ACQ_NO_BLOCK,    7
.equ ACQ_SHAPE,       8                /* wrong quarter, alignment, or wraps */
.equ ACQ_OVERLAP,     9                /* overlaps block A */
.equ ACQ_MARK_FAILED, 10               /* B would not hold the mark; freed */

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

.global acquire_entry
acquire_entry:
    push    {r4, r5, r6, r7, r8, r10, r11, lr}
    LDA     r4, STATE

    /* Block A has to be there, and has to be the block its record names. */
    ldr     r0, [r4, #S_SCRATCH_PROOF]
    LDA     r1, SCRATCH_MAGIC
    cmp     r0, r1
    movne   r0, #ACQ_NO_SCRATCH
    bne     acquire_return
    ldr     r8, [r4, #S_HANDLE]
    cmp     r8, #0
    moveq   r0, #ACQ_NO_SCRATCH
    beq     acquire_return

    add     r10, r8, #UNCACHED
    add     r10, r10, #LIFE_OFF        /* r10 = record A */
    ldr     r0, [r10, #L_MAGIC]
    LDA     r1, LIFE_MAGIC
    cmp     r0, r1
    ldreq   r0, [r10, #L_STATE]
    cmpeq   r0, #LIFE_HELD
    ldreq   r0, [r10, #L_HANDLE]
    cmpeq   r0, r8
    ldreq   r0, [r10, #L_ROLE]
    cmpeq   r0, #1
    movne   r0, #ACQ_BAD_RECORD
    bne     acquire_return

    add     r5, r8, #UNCACHED
    add     r5, r5, #OWN_OFF
    ldr     r0, [r5]
    LDA     r1, OWN_MAGIC
    cmp     r0, r1
    ldreq   r0, [r5, #4]
    cmpeq   r0, r8
    movne   r0, #ACQ_BAD_MARK
    bne     acquire_return

    /* The same-boot witness. The tick is free-running microseconds since
     * boot, so a reading BELOW the one recorded at block A's acquisition means
     * the counter restarted and the handle in the state block belongs to a
     * different boot. Two limits, stated rather than assumed: it is 32 bits at
     * 1 MHz and wraps about every 71.6 minutes, which shows up here as a
     * refusal - the safe direction; and a reboot whose recorded tick was very
     * small could slip through, which is why the mark above, not this, is the
     * evidence that the block is ours. */
    LDA     r12, F_CLOCK
    blx     r12
    mov     r7, r0                     /* tick, kept for both records */
    ldr     r1, [r10, #L_TICK]
    cmp     r7, r1
    movcc   r0, #ACQ_CLOCK_BACK
    bcc     acquire_return
    str     r7, [r10, #L_HELD]         /* A was still held, at this tick */

    /* A second block that is already recorded is not replaced. Overwriting the
     * record would lose the only handle by which the old one could be freed. */
    add     r11, r10, #LIFE_B          /* r11 = record B */
    LDA     r1, LIFE_MAGIC
    ldr     r0, [r11, #L_MAGIC]
    cmp     r0, r1
    ldreq   r0, [r11, #L_STATE]
    cmpeq   r0, #LIFE_HELD
    moveq   r0, #ACQ_B_PRESENT
    beq     acquire_return

    /* Acquire. The same allocator call the scratch phase makes, with the same
     * size and the same 1 KiB alignment. */
    mov     r0, #0
    LDA     r12, F_ALLOC
    blx     r12
    cmp     r0, #0
    moveq   r0, #ACQ_NO_ALLOCATOR
    beq     acquire_return
    mov     r6, r0                     /* allocator object */

    sub     sp, sp, #8
    mov     r0, r6
    LDA     r1, ALLOC_SIZE
    LDA     r2, 0x400
    mov     r3, #0
    str     r3, [sp]                   /* fifth argument */
    LDA     r12, F_GET
    blx     r12
    add     sp, sp, #8
    mov     r5, r0                     /* handle B */
    cmp     r5, #0
    moveq   r0, #ACQ_NO_BLOCK
    beq     acquire_return

    /* From here on a failure must free the block again: it is ours, nothing
     * else knows about it, and leaving it would be an orphan with no record.
     * Shape first, then disjointness from block A - the check the host used to
     * make alone, now made on the camera as well. */
    mov     r0, r5, lsr #30
    cmp     r0, #1                     /* 0x40000000..0x7FFFFFFF */
    movne   r0, #ACQ_SHAPE
    bne     acquire_free_and_return
    movs    r0, r5, lsl #22            /* the low ten bits: 1 KiB aligned */
    movne   r0, #ACQ_SHAPE
    bne     acquire_free_and_return
    adds    r7, r5, #ALLOC_SIZE
    movcs   r0, #ACQ_SHAPE
    bcs     acquire_free_and_return

    ldr     r0, [r4, #S_ALLOC_END]     /* end of block A */
    cmp     r7, r8
    bls     acquire_disjoint
    cmp     r0, r5
    bls     acquire_disjoint
    mov     r0, #ACQ_OVERLAP
    b       acquire_free_and_return

acquire_disjoint:
    /* The mark goes in first and is read back, so that memory we cannot write
     * is found here rather than as a bad encode later. */
    add     r0, r5, #UNCACHED
    add     r0, r0, #OWN_OFF
    LDA     r1, OWN_MAGIC
    str     r1, [r0]
    str     r5, [r0, #4]               /* the block names itself */
    dsb     sy
    ldr     r2, [r0]
    ldr     r3, [r0, #4]
    cmp     r2, r1
    cmpeq   r3, r5
    movne   r0, #ACQ_MARK_FAILED
    bne     acquire_free_and_return

    /* The record. Everything a later phase needs in order to decide whether
     * this block is still ours, and everything whoever reclaims it needs:
     * allocator, handle, the size asked for, the end, when it was acquired, on
     * which invocation, what for, and the mark that ties the record to the
     * memory. The magic goes in LAST, so an interrupted write leaves a record
     * that fails rather than one that passes. */
    mov     r0, #2                     /* L_ROLE: this is block B */
    mov     r2, r6                     /* allocator */
    mov     r3, r5                     /* handle */
    LDA     r12, ALLOC_SIZE
    add     r1, r11, #L_ROLE
    stm     r1, {r0, r2, r3, r12}      /* role, allocator, handle, bytes */
    str     r7, [r11, #L_END]
    LDA     r12, F_CLOCK
    blx     r12
    str     r0, [r11, #L_TICK]
    str     r0, [r11, #L_HELD]
    ldr     r0, [r4, #S_COUNT]
    str     r0, [r11, #L_SEQ]
    LDA     r0, PURPOSE_SRC
    str     r0, [r11, #L_PURPOSE]
    LDA     r0, OWN_MAGIC
    str     r0, [r11, #L_OWNMARK]
    mov     r0, #0
    str     r0, [r11, #L_FREERET]
    str     r0, [r11, #L_FREETICK]
    mov     r0, #LIFE_HELD
    str     r0, [r11, #L_STATE]
    dsb     sy
    LDA     r0, LIFE_MAGIC
    /* r12, not r0: r0 already holds the record magic that the very
     * next store writes, and clobbering it put the boot stamp where
     * the magic belongs -- the emulator caught it. */
    LDA     r12, LOAD_DONE_US
    ldr     r12, [r12]
    str     r12, [r11, #L_BOOT]
    str     r0, [r11, #L_MAGIC]
    dsb     sy
    mov     r0, #ACQ_OK
    b       acquire_return

acquire_free_and_return:
    /* Hand it straight back. The status word says why, and because the block
     * is gone before this returns there is nothing to reclaim later. */
    mov     r7, r0
    mov     r0, r6
    mov     r1, r5
    LDA     r12, F_FREE
    blx     r12
    mov     r0, r7

acquire_return:
    pop     {r4, r5, r6, r7, r8, r10, r11, lr}
    bx      lr
