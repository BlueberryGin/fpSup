.syntax unified
.arm
.text

/*
 * One-shot 4 MiB scratch reservation at the verified FHD writer call.
 *
 * The host only arms this after reading a complete PPWR power-preflight result.
 * It seeds S_POWER_PROOF with that magic, and this routine refuses to allocate
 * unless the seed is present.  The allocation is intentionally retained for
 * the immediately following one-frame encode-and-discard probe; that probe must
 * free the original allocator/handle pair on every terminal path.
 *
 * This phase never calls power, clock, or codec functions and never stores
 * through the DNG buffer or segment pointer.  It restores the hook before any
 * pointer dereference or firmware call, and always tail-calls the real writer
 * with the original argument registers and LR.
 */

.equ HOOK_SITE,       0xC0722AFC
.equ HOOK_ORIG,       0xEBFDE061
.equ REAL_WRITE,      0xC069AC88
/* Claimed at run time; the default is what this file assembles to on its
 * own. cave.py's rule: the caller passes the real address. */
#ifndef STATE
#define STATE 0xC072F700
#endif
.equ DONE_MAGIC,      0x454E4F44
.equ POWER_MAGIC,     0x52575050       /* "PPWR" */
.equ SCRATCH_MAGIC,   0x434F4C41       /* "ALOC" */
.equ GUARD_MAGIC,     0xA55AA55A

.equ EXPECTED_LR,     0xC0722B00
.equ EXPECTED_LEN,    0x00318200
.equ TIFF_MAGIC,      0x002A4949
.equ PIXEL_OFFSET,    0x00013400
.equ CODEC_MODE,      0xC37CF87C

.equ ALLOC_SIZE,      0x00400000
/* Must match single_frame_encode_discard_probe.S exactly: this phase writes
 * the guards the encode phase later checks, so a layout split between the two
 * files means the encode looks for guards where none were written. The arena
 * is sized from the worst-case output bound, not the packed raster; see
 * projects/lossless-sup/tools/output_bound.py and ROADMAP P0-3. */
.equ WORK_CAP,        0x003F0000
.equ TABLE_OFFSET,    0x003F1000
.equ TABLE_TMP_OFF,   0x003F1400

.equ F_CLOCK,         0xC002B6E0       /* free-running 1 MHz tick */
.equ F_ALLOC,         0xC001CF78
.equ F_GET,           0xC001D038
.equ F_FREE,          0xC001D2B8

/* First nine words retain exact_dng_writer_probe's arm transaction contract. */
.equ S_COUNT,         0x00
.equ S_R0,            0x04
.equ S_R1,            0x08
.equ S_R2,            0x0C
.equ S_LR,            0x10
.equ S_BUF,           0x14
.equ S_LEN,           0x18
.equ S_DONE,          0x1C
.equ S_RESTORED,      0x20
.equ S_PHASE,         0x24
.equ S_STAGE,         0x28
.equ S_ERROR,         0x2C
.equ S_ALLOCATOR,     0x30
.equ S_HANDLE,        0x34
.equ S_ALLOC_END,     0x38
.equ S_WORK_BASE,     0x3C
.equ S_WORK_END,      0x40
.equ S_WORK_GUARD,    0x44
.equ S_TABLE_BASE,    0x48
.equ S_TABLE_TMP,     0x4C
.equ S_SOURCE,        0x50
.equ S_CODEC_MODE,    0x54
.equ S_GUARD_WORK,    0x94
.equ S_GUARD_TABLE,   0x98
.equ S_GUARD_TMP,     0x9C
.equ S_POWER_PROOF,   0xD4
.equ S_SCRATCH_MAGIC, 0xD8
.equ S_END_GUARD,     0xDC
.equ S_GUARD_END,     0xE0
.equ S_TABLE_GUARD,   0xE4
.equ S_TMP_GUARD,     0xE8
.equ S_FREE_ERROR,    0xEC
.equ S_FREE_DONE,     0xF0

.equ PHASE_SCRATCH,   1
.equ FREE_MAGIC,      0x45455246       /* "FREE" */

.equ ERR_RESTORE,     1
.equ ERR_POWER_PROOF, 2
.equ ERR_LR,          3
.equ ERR_KIND,        4
.equ ERR_SEG_PTR,     5
.equ ERR_LENGTH,      6
.equ ERR_BUFFER,      7
.equ ERR_TIFF,        8
.equ ERR_SRC_ALIGN,   9
.equ ERR_MODE_BUSY,   10
.equ ERR_ALLOCATOR,   11
.equ ERR_ALLOC,       12
.equ ERR_ALIAS,       13
.equ ERR_ALIGN,       14
.equ ERR_RANGE,       15
.equ ERR_OVERLAP,     16
.equ ERR_GUARD,       17
.equ ERR_LIFE,        18               /* the block does not hold our mark */

/* ---- the lifecycle record for the block this phase allocates -----------
 * Must match single_frame_encode_discard_probe.S, for the same reason the
 * arena layout above must: the phase that later uses the block reads what is
 * written here.
 *
 * Why the record exists at all. Until now the only evidence that a 4 MiB
 * block belonged to us was a magic in the STATE block plus three guard words
 * in the arena - which say "nothing has scribbled over this", not "this is
 * the block we were given". The distinction started to matter when a second
 * block was needed: its handle arrived as a command-line argument, and range,
 * alignment and non-overlap checks prove the SHAPE of a handle, not its
 * ownership. So acquisition now writes a record, and it writes it HERE,
 * because this is where the allocator returned the block; nowhere else can
 * say when it was acquired without being told.
 */
.equ LIFE_OFF,        0x003F4000
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
.equ PURPOSE_WORK,    0x4B524F57       /* "WORK" */
.equ OWN_OFF,         0x003FC000       /* the mark, inside the block itself */
.equ LIFE_HELD,       1
.equ UNCACHED,        0x40000000

.macro LDA reg, value
    movw    \reg, #:lower16:\value
    movt    \reg, #:upper16:\value
.endm

.macro FAIL code
    mov     r0, #\code
    str     r0, [r4, #S_ERROR]
    b       complete
.endm

.global probe_entry
probe_entry:
    /* Twelve words preserve every register used and keep SP 8-byte aligned. */
    push    {r0, r1, r2, r3, r4, r5, r6, r7, r8, r10, r12, lr}

    LDA     r4, STATE
    ldr     r3, [r4, #S_COUNT]
    add     r3, r3, #1
    str     r3, [r4, #S_COUNT]
    cmp     r3, #1
    bne     duplicate_entry

    str     r0, [r4, #S_R0]
    str     r1, [r4, #S_R1]
    str     r2, [r4, #S_R2]
    str     lr, [r4, #S_LR]
    mov     r0, #PHASE_SCRATCH
    str     r0, [r4, #S_PHASE]
    mov     r0, #1
    str     r0, [r4, #S_STAGE]
    /* arm_site relies on count becoming visible before the restored hook. */
    dmb     sy

    /* Restore first; verify readback before dereferencing or allocating. */
    LDA     r7, HOOK_SITE
    LDA     r8, HOOK_ORIG
    str     r8, [r7]
    dmb     sy
    ldr     r0, [r7]
    str     r0, [r4, #S_RESTORED]
    mov     r1, #2
    str     r1, [r4, #S_STAGE]
    cmp     r0, r8
    bne     bad_restore

    LDA     r0, POWER_MAGIC
    ldr     r1, [r4, #S_POWER_PROOF]
    cmp     r1, r0
    bne     bad_power_proof

    LDA     r0, EXPECTED_LR
    ldr     r1, [r4, #S_LR]
    cmp     r1, r0
    bne     bad_lr
    ldr     r0, [r4, #S_R2]
    cmp     r0, #2
    bne     bad_kind

    ldr     r5, [r4, #S_R1]
    cmp     r5, #0
    beq     bad_seg_ptr
    tst     r5, #3
    bne     bad_seg_ptr
    ldr     r6, [r5]
    str     r6, [r4, #S_BUF]
    cmp     r6, #0
    beq     bad_buffer
    ldr     r7, [r5, #4]
    str     r7, [r4, #S_LEN]
    LDA     r0, EXPECTED_LEN
    cmp     r7, r0
    bne     bad_length
    ldr     r0, [r6]
    LDA     r1, TIFF_MAGIC
    cmp     r0, r1
    bne     bad_tiff

    LDA     r0, PIXEL_OFFSET
    adds    r7, r6, r0
    bcs     bad_src_alignment
    str     r7, [r4, #S_SOURCE]
    movs    r0, r7, lsl #22
    bne     bad_src_alignment

    LDA     r0, CODEC_MODE
    ldr     r1, [r0]
    str     r1, [r4, #S_CODEC_MODE]
    /* 0xC37CF87C selects which client owns the engine; it is NOT a busy
       flag.  C06301F8 latches 1 (callback at +0x04) and C06303E4 latches 2
       (callback at +0x38); the mode-1 starter C0630208 and the mode-2
       starter C06303F8 each gate on their own value.  0 means CLOSED and is
       written only by the close routine at C062FF7A, so a non-zero read is
       the normal state whenever the engine is open.  Refuse only a value no
       setup path can produce.  [C 2026-09-29] */
    cmp     r1, #2
    bhi     codec_busy
    mov     r0, #3
    str     r0, [r4, #S_STAGE]

    /* F_GET(allocator, 4 MiB, 1 KiB alignment, NULL, caller-tag default). */
    mov     r0, #0
    LDA     r12, F_ALLOC
    blx     r12
    str     r0, [r4, #S_ALLOCATOR]
    cmp     r0, #0
    beq     bad_allocator
    mov     r8, r0

    sub     sp, sp, #8
    mov     r0, r8
    LDA     r1, ALLOC_SIZE
    LDA     r2, 0x400
    mov     r3, #0
    str     r3, [sp]
    LDA     r12, F_GET
    blx     r12
    add     sp, sp, #8
    mov     r8, r0
    str     r8, [r4, #S_HANDLE]
    cmp     r8, #0
    beq     bad_alloc
    mov     r0, #4
    str     r0, [r4, #S_STAGE]

    LDA     r0, 0x40000000
    cmp     r8, r0
    blo     bad_alias
    LDA     r0, 0x80000000
    cmp     r8, r0
    bhs     bad_alias
    movs    r0, r8, lsl #22
    bne     bad_alignment

    LDA     r0, ALLOC_SIZE
    adds    r1, r8, r0
    bcs     bad_range
    LDA     r0, 0x80000000
    cmp     r1, r0
    bhi     bad_range
    str     r1, [r4, #S_ALLOC_END]
    str     r8, [r4, #S_WORK_BASE]

    LDA     r0, WORK_CAP
    adds    r2, r8, r0
    bcs     bad_range
    str     r2, [r4, #S_WORK_END]
    str     r2, [r4, #S_WORK_GUARD]

    LDA     r0, TABLE_OFFSET
    adds    r3, r8, r0
    bcs     bad_range
    str     r3, [r4, #S_TABLE_BASE]
    add     r0, r3, #0x30
    str     r0, [r4, #S_TABLE_GUARD]

    LDA     r0, TABLE_TMP_OFF
    adds    r0, r8, r0
    bcs     bad_range
    str     r0, [r4, #S_TABLE_TMP]
    add     r12, r0, #0x30
    str     r12, [r4, #S_TMP_GUARD]

    sub     r0, r1, #4
    str     r0, [r4, #S_END_GUARD]
    ldr     r12, [r4, #S_TMP_GUARD]
    add     r12, r12, #4
    cmp     r12, r0
    bhi     bad_range
    cmp     r2, r3
    bhs     bad_range

    /* The entire reserved block must be disjoint from the live DNG segment. */
    ldr     r0, [r4, #S_LEN]
    adds    r0, r6, r0
    bcs     bad_range
    cmp     r1, r6
    bls     ranges_disjoint
    cmp     r0, r8
    bls     ranges_disjoint
    b       bad_overlap

ranges_disjoint:
    mov     r0, #5
    str     r0, [r4, #S_STAGE]
    LDA     r12, GUARD_MAGIC
    ldr     r0, [r4, #S_WORK_GUARD]
    str     r12, [r0]
    ldr     r0, [r4, #S_TABLE_GUARD]
    str     r12, [r0]
    ldr     r0, [r4, #S_TMP_GUARD]
    str     r12, [r0]
    ldr     r0, [r4, #S_END_GUARD]
    str     r12, [r0]
    dmb     sy

    ldr     r0, [r4, #S_WORK_GUARD]
    ldr     r0, [r0]
    str     r0, [r4, #S_GUARD_WORK]
    ldr     r1, [r4, #S_TABLE_GUARD]
    ldr     r1, [r1]
    str     r1, [r4, #S_GUARD_TABLE]
    ldr     r2, [r4, #S_TMP_GUARD]
    ldr     r2, [r2]
    str     r2, [r4, #S_GUARD_TMP]
    ldr     r3, [r4, #S_END_GUARD]
    ldr     r3, [r3]
    str     r3, [r4, #S_GUARD_END]
    LDA     r12, GUARD_MAGIC
    cmp     r0, r12
    cmpeq   r1, r12
    cmpeq   r2, r12
    cmpeq   r3, r12
    bne     bad_guard

    /* ---- the lifecycle record ----------------------------------------
     * Written through the uncached alias, which is the view every other
     * writer of these records uses; the host reads the cached address, as it
     * already does for the result words the encode phase leaves at
     * RESULT_OFF.
     *
     * The order matters twice. The mark goes into the block and is read back
     * BEFORE anything is recorded, because an allocator that handed us
     * memory we cannot write would otherwise show up later as a bad encode.
     * The record's magic goes in LAST, so a run interrupted halfway through
     * leaves a record that fails rather than one that passes.
     */
    LDA     r12, F_CLOCK
    blx     r12
    mov     r6, r0                     /* tick at acquisition */
    add     r10, r8, #UNCACHED
    add     r0, r10, #OWN_OFF
    LDA     r1, OWN_MAGIC
    str     r1, [r0]
    str     r8, [r0, #4]               /* the block names itself */
    dsb     sy
    ldr     r2, [r0]
    ldr     r3, [r0, #4]
    cmp     r2, r1
    cmpeq   r3, r8
    bne     bad_life

    add     r10, r10, #LIFE_OFF
    mov     r0, #1                     /* L_ROLE: this is block A */
    ldr     r2, [r4, #S_ALLOCATOR]
    mov     r3, r8
    LDA     r5, ALLOC_SIZE
    ldr     r7, [r4, #S_ALLOC_END]
    add     r12, r10, #L_ROLE
    stm     r12, {r0, r2, r3, r5, r7}  /* role, allocator, handle, bytes, end */
    str     r6, [r10, #L_TICK]
    ldr     r0, [r4, #S_COUNT]
    LDA     r2, PURPOSE_WORK
    mov     r3, r1                     /* L_OWNMARK, still OWN_MAGIC */
    mov     r5, r6                     /* L_HELD: checked again on each use */
    mov     r7, #LIFE_HELD
    add     r12, r10, #L_SEQ
    stm     r12, {r0, r2, r3, r5, r7}  /* seq, purpose, mark, held, state */
    mov     r0, #0
    str     r0, [r10, #L_FREERET]
    str     r0, [r10, #L_FREETICK]
    dsb     sy
    LDA     r0, LIFE_MAGIC
    /* r12, not r0: r0 already holds the record magic that the very
     * next store writes, and clobbering it put the boot stamp where
     * the magic belongs -- the emulator caught it. */
    LDA     r12, LOAD_DONE_US
    ldr     r12, [r12]
    str     r12, [r10, #L_BOOT]
    str     r0, [r10, #L_MAGIC]
    dsb     sy

    mov     r0, #7
    str     r0, [r4, #S_STAGE]
    LDA     r0, SCRATCH_MAGIC
    str     r0, [r4, #S_SCRATCH_MAGIC]
    b       complete

duplicate_entry:
    /* Close a host re-arm race or stale-I-cache entry defensively. */
    LDA     r0, HOOK_SITE
    LDA     r1, HOOK_ORIG
    str     r1, [r0]
    dmb     sy
    b       tail_call

bad_restore:        FAIL ERR_RESTORE
bad_power_proof:    FAIL ERR_POWER_PROOF
bad_lr:             FAIL ERR_LR
bad_kind:           FAIL ERR_KIND
bad_seg_ptr:        FAIL ERR_SEG_PTR
bad_length:         FAIL ERR_LENGTH
bad_buffer:         FAIL ERR_BUFFER
bad_tiff:           FAIL ERR_TIFF
bad_src_alignment:  FAIL ERR_SRC_ALIGN
codec_busy:         FAIL ERR_MODE_BUSY
bad_allocator:      FAIL ERR_ALLOCATOR
bad_alloc:          FAIL ERR_ALLOC
bad_alias:
    mov     r0, #ERR_ALIAS
    b       free_failed_allocation
bad_alignment:
    mov     r0, #ERR_ALIGN
    b       free_failed_allocation
bad_range:
    mov     r0, #ERR_RANGE
    b       free_failed_allocation
bad_overlap:
    mov     r0, #ERR_OVERLAP
    b       free_failed_allocation
bad_guard:
    mov     r0, #ERR_GUARD
    b       free_failed_allocation
bad_life:
    /* The block is ours - the allocator returned it - but it does not hold
     * our mark, so nothing may later treat it as recorded. Freed here, like
     * every other post-allocation failure, rather than left orphaned. */
    mov     r0, #ERR_LIFE

free_failed_allocation:
    str     r0, [r4, #S_ERROR]
    str     r0, [r4, #S_FREE_ERROR]
    ldr     r0, [r4, #S_ALLOCATOR]
    ldr     r1, [r4, #S_HANDLE]
    cmp     r0, #0
    beq     complete
    cmp     r1, #0
    beq     complete
    LDA     r12, F_FREE
    blx     r12
    LDA     r0, FREE_MAGIC
    str     r0, [r4, #S_FREE_DONE]
    b       complete

complete:
    dmb     sy
    LDA     r0, DONE_MAGIC
    str     r0, [r4, #S_DONE]

tail_call:
    pop     {r0, r1, r2, r3, r4, r5, r6, r7, r8, r10, r12, lr}
    ldr     pc, [pc, #-4]
    .word   REAL_WRITE
