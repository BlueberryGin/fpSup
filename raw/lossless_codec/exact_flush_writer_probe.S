.syntax unified
.arm
.text

/*
 * One-shot, read-only FileSplitWriter flush probe for SIGMA fp V5.02.
 *
 * The call at C03A5490 is:
 *
 *     bl  C069ADE0
 *
 * r0 is the completed FileSplitWriter description and r1 is the second flush
 * argument.  This probe restores that call before dereferencing anything,
 * records the exact one-node shape used by a CinemaDNG frame, times the real
 * flush, and returns its uint64 result unchanged in r1:r0.
 *
 * Explicit stores are limited to HOOK_SITE, STATE, and this routine's stack.
 * The writer, its node, and the DNG buffer are read-only.  A stale, already
 * fetched entry restores the hook defensively and tail-calls the real flush
 * with the original registers and LR.
 *
 * STATE reserves 64 words (0x100 bytes); the defined result occupies 0x5C:
 *
 *   +00 hit count             +04 writer (entry r0)
 *   +08 entry r1              +0C entry lr
 *   +10 writer[0]             +14 writer[4]
 *   +18 writer[8]             +1C writer head (+0x8C)
 *   +20 writer node count     +24 expected node (writer+0x0C)
 *   +28 node next             +2C node buffer
 *   +30 node length           +34 node ordering kind
 *   +38 DNG first word        +3C cumulative shape flags (full = 0x1FF)
 *   +40 restored hook word    +44 timer before real flush
 *   +48 timer after flush     +4C real return low
 *   +50 real return high      +54 entered magic
 *   +58 done magic            +5C..+FC reserved
 *
 * Shape bits: writer non-null, writer[8].low-byte nonzero, count == 1,
 * exact head, terminal node, non-null node buffer, exact FHD length, kind 2,
 * and little-endian TIFF magic, respectively from bit 0 through bit 8.
 */

.equ HOOK_SITE,       0xC03A5490
.equ HOOK_ORIG,       0xEB0BD652
.equ REAL_FLUSH,      0xC069ADE0
/* Claimed at run time; the default is the historical fixed address, which
 * nothing declares ownership of. See probe_placement.py. */
#ifndef STATE
#define STATE 0xC0730600
#endif
.equ CLOCK,           0xC002B6E0

.equ ENTERED_MAGIC,   0x52544E45       /* "ENTR" */
.equ DONE_MAGIC,      0x454E4F44       /* "DONE" */

.equ FLAG_WRITER,       0x001
.equ FLAG_WRITER_MODE,  0x002
.equ FLAG_ONE_NODE,     0x004
.equ FLAG_EXACT_HEAD,   0x008
.equ FLAG_NODE_END,     0x010
.equ FLAG_NODE_BUF,     0x020
.equ FLAG_NODE_LEN,     0x040
.equ FLAG_NODE_KIND,    0x080
.equ FLAG_DNG_MAGIC,    0x100

.equ S_COUNT,         0x00
.equ S_WRITER,        0x04
.equ S_ARG1,          0x08
.equ S_LR,            0x0C
.equ S_WRITER_0,      0x10
.equ S_WRITER_4,      0x14
.equ S_WRITER_8,      0x18
.equ S_HEAD,          0x1C
.equ S_NODE_COUNT,    0x20
.equ S_EXPECTED_NODE, 0x24
.equ S_NODE_NEXT,     0x28
.equ S_NODE_BUF,      0x2C
.equ S_NODE_LEN,      0x30
.equ S_NODE_KIND,     0x34
.equ S_DNG_MAGIC,     0x38
.equ S_SHAPE_FLAGS,   0x3C
.equ S_RESTORED,      0x40
.equ S_T0,            0x44
.equ S_T1,            0x48
.equ S_RET_LO,        0x4C
.equ S_RET_HI,        0x50
.equ S_ENTERED,       0x54
.equ S_DONE,          0x58

/* The frame object the writer is called WITH (r1, captured in S_ARG1). The
 * copy-back design writes into frame[0x68], the buffer the writer hands us as
 * seg[0], so the one thing it has to know is how many holders that buffer has
 * at this moment. FUN_c0373dd0 copies the 0x68..0x128 block field for field
 * when a clone is made, so a clone SHARES this pointer rather than getting its
 * own allocation; FUN_c0374ee8 makes frame[0x1C0] of them and bumps
 * frame[0x1114]. Whether that happens on the movie path was not settled by
 * reading - two call sites, one gated on a mode and one not - so it is read
 * here instead. Read-only: every one of these is a load. */
.equ S_FR_KIND,       0x5C             /* frame[0x04]   capture kind         */
.equ S_FR_BUF,        0x60             /* frame[0x68]   the RAW buffer       */
.equ S_FR_HDR,        0x64             /* frame[0x128]  header bytes         */
.equ S_FR_ID,         0x68             /* frame[0x10FC] frame id             */
.equ S_FR_CLONES,     0x6C             /* frame[0x1110] clones made          */
.equ S_FR_REFS,       0x70             /* frame[0x1114] reference count      */
.equ S_FR_OWNER,      0x74             /* frame[0x1118] owner id, 0 = owner  */
.equ S_FR_DUPCNT,     0x78             /* frame[0x1C0]  clones REQUESTED     */
.equ S_FR_SEG_MATCH,  0x7C             /* 1 = frame[0x68] equals seg[0]      */

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

.global probe_entry
probe_entry:
    /* Fourteen saved words keep SP 8-byte aligned. */
    push    {r0, r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r11, r12, lr}

    LDA     r4, STATE
    ldr     r3, [r4, #S_COUNT]
    add     r3, r3, #1
    str     r3, [r4, #S_COUNT]
    cmp     r3, #1
    bne     stale_entry

    str     r0, [r4, #S_WRITER]
    str     r1, [r4, #S_ARG1]
    str     lr, [r4, #S_LR]
    LDA     r3, ENTERED_MAGIC
    str     r3, [r4, #S_ENTERED]
    dmb     sy

    /* Self-restore before reading the writer or calling firmware. */
    LDA     r5, HOOK_SITE
    LDA     r6, HOOK_ORIG
    str     r6, [r5]
    dmb     sy
    ldr     r7, [r5]
    str     r7, [r4, #S_RESTORED]
    cmp     r7, r6
    bne     call_real

    /* Read only the fixed writer fields after a minimal pointer guard. */
    ldr     r5, [r4, #S_WRITER]
    cmp     r5, #0
    beq     call_real
    mov     r3, #FLAG_WRITER
    str     r3, [r4, #S_SHAPE_FLAGS]
    tst     r5, #3
    bne     call_real
    ldr     r0, [r5, #0x00]
    ldr     r1, [r5, #0x04]
    ldr     r2, [r5, #0x08]
    ldr     r6, [r5, #0x8C]
    ldr     r7, [r5, #0x90]
    str     r0, [r4, #S_WRITER_0]
    str     r1, [r4, #S_WRITER_4]
    str     r2, [r4, #S_WRITER_8]
    str     r6, [r4, #S_HEAD]
    str     r7, [r4, #S_NODE_COUNT]
    add     r8, r5, #0x0C
    str     r8, [r4, #S_EXPECTED_NODE]
    tst     r2, #0xFF
    orrne   r3, r3, #FLAG_WRITER_MODE
    str     r3, [r4, #S_SHAPE_FLAGS]

    /* Do not touch a node unless both exact one-node predicates hold. */
    cmp     r7, #1
    bne     call_real
    orr     r3, r3, #FLAG_ONE_NODE
    str     r3, [r4, #S_SHAPE_FLAGS]
    cmp     r6, r8
    bne     call_real
    orr     r3, r3, #FLAG_EXACT_HEAD
    str     r3, [r4, #S_SHAPE_FLAGS]

    ldr     r0, [r6, #0x00]
    ldr     r1, [r6, #0x04]
    ldr     r2, [r6, #0x08]
    ldr     r7, [r6, #0x0C]
    str     r0, [r4, #S_NODE_NEXT]
    str     r1, [r4, #S_NODE_BUF]
    str     r2, [r4, #S_NODE_LEN]
    str     r7, [r4, #S_NODE_KIND]
    cmp     r0, #0
    orreq   r3, r3, #FLAG_NODE_END
    cmp     r1, #0
    orrne   r3, r3, #FLAG_NODE_BUF
    LDA     r0, 0x00318200
    cmp     r2, r0
    orreq   r3, r3, #FLAG_NODE_LEN
    cmp     r7, #2
    orreq   r3, r3, #FLAG_NODE_KIND
    str     r3, [r4, #S_SHAPE_FLAGS]

    /* A null or unaligned node buffer is recorded but never dereferenced. */
    cmp     r1, #0
    beq     call_real
    tst     r1, #3
    bne     call_real
    ldr     r0, [r1]
    str     r0, [r4, #S_DNG_MAGIC]
    LDA     r2, 0x002A4949
    cmp     r0, r2
    orreq   r3, r3, #FLAG_DNG_MAGIC
    str     r3, [r4, #S_SHAPE_FLAGS]

    /* Frame reference survey. Loads only; nothing here writes the frame. */
    ldr     r5, [r4, #S_ARG1]
    cmp     r5, #0
    beq     call_real
    ldr     r0, [r5, #4]
    str     r0, [r4, #S_FR_KIND]
    LDA     r0, 0x68
    ldr     r1, [r5, r0]
    str     r1, [r4, #S_FR_BUF]
    LDA     r0, 0x128
    ldr     r2, [r5, r0]
    str     r2, [r4, #S_FR_HDR]
    LDA     r0, 0x10FC
    ldr     r2, [r5, r0]
    str     r2, [r4, #S_FR_ID]
    LDA     r0, 0x1110
    ldr     r2, [r5, r0]
    str     r2, [r4, #S_FR_CLONES]
    LDA     r0, 0x1114
    ldr     r2, [r5, r0]
    str     r2, [r4, #S_FR_REFS]
    LDA     r0, 0x1118
    ldr     r2, [r5, r0]
    str     r2, [r4, #S_FR_OWNER]
    LDA     r0, 0x1C0
    ldr     r2, [r5, r0]
    str     r2, [r4, #S_FR_DUPCNT]
    /* Is the buffer the writer registered the same one the frame owns? If it
     * is not, copy-back would be writing into something else entirely. */
    ldr     r2, [r4, #S_NODE_BUF]
    cmp     r1, r2
    moveq   r0, #1
    movne   r0, #0
    str     r0, [r4, #S_FR_SEG_MATCH]

call_real:
    /* Measure only the real, synchronous flush call. */
    LDA     r12, CLOCK
    blx     r12
    str     r0, [r4, #S_T0]

    ldr     r0, [r4, #S_WRITER]
    ldr     r1, [r4, #S_ARG1]
    LDA     r12, REAL_FLUSH
    blx     r12
    str     r0, [r4, #S_RET_LO]
    str     r1, [r4, #S_RET_HI]

    LDA     r12, CLOCK
    blx     r12
    str     r0, [r4, #S_T1]
    LDA     r3, DONE_MAGIC
    str     r3, [r4, #S_DONE]
    dmb     sy

    /* Restore caller-visible registers; preserve the real uint64 r1:r0. */
    ldr     r0, [r4, #S_RET_LO]
    ldr     r1, [r4, #S_RET_HI]
    ldr     r2, [sp, #8]
    ldr     r3, [sp, #12]
    add     sp, sp, #16
    pop     {r4, r5, r6, r7, r8, r9, r10, r11, r12, lr}
    bx      lr

stale_entry:
    /* Close a stale-I-cache/re-arm window, then behave exactly like the BL. */
    LDA     r3, HOOK_SITE
    LDA     r12, HOOK_ORIG
    str     r12, [r3]
    dmb     sy
    pop     {r0, r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r11, r12, lr}
    ldr     pc, [pc, #-4]
    .word   REAL_FLUSH
