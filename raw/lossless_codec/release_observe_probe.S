/* Observe when the camera decides a frame's buffer is finished with.
 *
 * WHY THIS EXISTS
 *
 * Compressed recording needs to know when our own output buffer is free again.
 * G5 proved one buffer is not enough; BUFFER_DESIGN's answer was a pool of
 * slots released by the per-frame completion callback C03A55D0. A scan on
 * 2026-09-28 found that callback has exactly ONE caller, 0xC03A54C4, and that
 * the call is skipped whenever the aggregation flag at manager+0xD is set -
 * which on SD is forced to 1 and cannot be turned off. So the named release
 * signal never fires on the target path.
 *
 * What the aggregated path does instead is write a state into frame[0x1104]:
 * 0xD at submit (success AND error alike), and later 0x12 from the card layer
 * after five teardown calls. The CinemaDNG manager at 0xC03A11F0 reclaims a
 * frame's buffer ONLY from frames in state 0x12, reading frame[0x68] - the
 * allocation base. So 0x12 is the transition that looks like release.
 *
 * "Looks like" is the reason for this probe. That reading is an inference from
 * who consumes the state, and static reading has already misled this project
 * once. This observes the transitions actually happening, per frame, with a
 * timestamp, and changes nothing.
 *
 * WHAT IT DOES AND DOES NOT DO
 *
 * It replaces ONE word - the first instruction of the state dispatcher
 * C0397038 - with a branch here, records four words per call, and continues
 * into the dispatcher with every register and the stack exactly as they were.
 * It does not redirect the writer, does not encode, does not allocate, does not
 * call anything, and the bytes the camera writes to the card are the bytes it
 * would have written anyway.
 *
 * The dispatcher has 22 callers and runs on every state transition of every
 * frame, so the handler is pure loads and stores with a bound on how many
 * records it will ever write. No F_CLOCK: that routine takes a lock and does a
 * 64-bit conversion through two calls. The raw counter it is built on is a
 * plain word at 0xC30781A4, so the timestamp here is one load in unknown
 * units, and the host calibrates the rate separately.
 */

#ifndef STATE
#define STATE 0xC072F700
#endif

/* The dispatcher, and the instruction this probe displaces. Both are checked by
 * the host before anything is written: a site that does not hold exactly this
 * word is not the site this probe was built for. */
.equ DISPATCH,        0xC0397038
.equ DISPATCH_ORIG,   0xE92D4DF0       /* push {r4,r5,r6,r7,r8,sl,fp,lr} */
.equ DISPATCH_BODY,   0xC039703C

.equ FRAME_STATE,     0x1104           /* frame[0x1104], the state word */
.equ TICK_VAR,        0xC30781A4       /* what F_CLOCK is built on, read raw */
.equ UNCACHED,        0x40000000

.equ S_HANDLE,        0x34             /* our 4 MiB block, seeded by the host */

/* The ring, in that block, clear of every other user of it: the guards start at
 * 0x3F0000, the size tables at 0x3F1000/0x3F1400, the IFD template at 0x3F2000,
 * the results at 0x3F2800, the trailer at 0x3F3000, the lifecycle records at
 * 0x3F4000 and the block's own mark at 0x3FC000. */
.equ RING_OFF,        0x3F5000
.equ RING_CAP,        1024             /* 16 KiB of records, then it stops */
.equ RING_MAGIC,      0x5642534F       /* "OBSV" */
.equ R_COUNT,         0x00
.equ R_CAP,           0x04
.equ R_MAGIC,         0x08
.equ R_DROPPED,       0x0C             /* transitions seen after the ring filled */
.equ R_FIRST,         0x10             /* records, 16 bytes each */

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

.global observe_entry
observe_entry:
    /* Only the caller-saved registers, and the arguments are among them, so
     * they are saved and restored rather than merely avoided. Six words keeps
     * the stack 8-byte aligned. */
    push    {r0, r1, r2, r3, r12, lr}

    LDA     r12, STATE
    ldr     r12, [r12, #S_HANDLE]
    /* A handle that is not in the quarter the allocator hands out is not ours,
     * and this handler must never store through a stale word. */
    mov     r1, r12, lsr #30
    cmp     r1, #1
    bne     observe_out
    cmp     r0, #0                     /* a frame pointer we would dereference */
    beq     observe_out

    add     r12, r12, #UNCACHED
    LDA     r1, RING_OFF
    add     r12, r12, r1               /* r12 = the ring */
    ldr     r1, [r12, #R_MAGIC]
    LDA     r3, RING_MAGIC
    cmp     r1, r3
    bne     observe_out                /* the host has not armed the ring */
    ldr     r1, [r12, #R_COUNT]
    LDA     r3, RING_CAP
    cmp     r1, r3
    bhs     observe_full

    /* One record: when, which frame, what state it is in on the way in, and
     * the reason code the caller passed. The state is read BEFORE the
     * dispatcher runs, so a transition shows up as the state it is leaving. */
    add     r3, r12, #R_FIRST
    add     r3, r3, r1, lsl #4
    LDA     r1, TICK_VAR
    ldr     r1, [r1]
    str     r1, [r3]
    str     r0, [r3, #4]
    LDA     r1, FRAME_STATE
    ldr     r1, [r0, r1]
    str     r1, [r3, #8]
    str     r2, [r3, #0xC]
    /* The count last, and only after the record is in memory: the host reads
     * this ring while the camera is still filling it, and a count that ran
     * ahead of the record would hand it a half-written entry. */
    dsb     sy
    ldr     r1, [r12, #R_COUNT]
    add     r1, r1, #1
    str     r1, [r12, #R_COUNT]
    dsb     sy

observe_out:
    pop     {r0, r1, r2, r3, r12, lr}
    /* The displaced instruction, then into the dispatcher's second one. The
     * load into pc touches no register, so the dispatcher begins with exactly
     * the state its caller set up. */
    push    {r4, r5, r6, r7, r8, sl, fp, lr}
    ldr     pc, [pc, #-4]
    .word   DISPATCH_BODY

observe_full:
    /* Full is recorded, not silently ignored: a log that stopped early and a
     * log that was never written look the same otherwise. */
    ldr     r1, [r12, #R_DROPPED]
    add     r1, r1, #1
    str     r1, [r12, #R_DROPPED]
    dsb     sy
    b       observe_out
