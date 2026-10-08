.syntax unified
.arm
.text
.align 2

/* SIGMA fp 5.02: OFFLINE finite raw-observation candidate, no installer.
 * Sites/ROM identity: native_hook_sites.json. No timer, MMIO, USB, wait,
 * allocation, native getter, C runtime, optical assertion or lens command.
 *
 * Required build definitions: STATE_AF/LENS/FACE/AAT, RECORDS_AF/LENS/FACE/AAT,
 * CAPACITY (1..1024). Addresses are supplied owned storage, NOT free-RAM claims.
 * Each source has its own 40-byte state and CAPACITY*256-byte record array.
 * The builder/owner must reject overlapping/wrapping regions and retain all
 * code/storage until every producer has quiesced. Never reset state while armed.
 *
 * State words: magic(0)=FNO1, version(4)=1, stride(8)=256, capacity(12),
 * nonce(16)!=0, enabled(20)=1, count(24), full(28), busy(32), loss(36).
 * Single LDREX/STREX attempt, no retry: nested/contention sets sticky loss=1.
 * A busy owner alone touches count/records. Full arrays never wrap/overwrite.
 * DMB publishes CPU records; it does not perform DMA cache maintenance.
 *
 * Record: magic(0)=FNR1, version(4)=1, source(8), commit seq(12), nonce(16),
 * original SP(20), observed r0(24), APSR(28), payload bytes(32), raw extras
 * (36,40,44), zero reserved(48..63), payload(64...), zero unused, trailer(252).
 * Body/trailer precede DMB then commit, DMB then count. Readers must validate
 * nonce, prefix, commit/trailer and loss; none of these prove optical validity.
 *
 * Saved frame is exactly 64 bytes below original SP:
 * +0 APSR, +4 original SP, +8 r0, +12 r1, ... +56 r12, +60 LR.
 * All r0-r12/SP and APSR NZCVQ/GE are restored. No VFP/FPSCR instructions.
 * The only BL is to this file's stackless private recorder, never to C.
 * Its private ABI requires no additional stack alignment and uses no LDRD.
 *
 * AF records BEFORE native, then tail-forwards with original LR and exact SP.
 * Other sites execute native once with exact original SP and changed incoming
 * LR=local after_native. Their native result/flags are recorded and restored;
 * LR is then set to the fixed callsite continuation. That differs from the
 * native post-call LR bit pattern, but these audited continuations do not read
 * current LR before another BL or their saved-frame POP-PC. Do not generalize.
 */

#if !defined(STATE_AF) || !defined(STATE_LENS) || !defined(STATE_FACE) || !defined(STATE_AAT)
#error Four STATE source addresses are required
#endif
#if !defined(RECORDS_AF) || !defined(RECORDS_LENS) || !defined(RECORDS_FACE) || !defined(RECORDS_AAT)
#error Four RECORDS source addresses are required
#endif
#ifndef CAPACITY
#error CAPACITY is required
#endif
#if CAPACITY < 1 || CAPACITY > 1024
#error CAPACITY must be in 1..1024
#endif
#if ((STATE_AF | STATE_LENS | STATE_FACE | STATE_AAT | RECORDS_AF | RECORDS_LENS | RECORDS_FACE | RECORDS_AAT) & 3)
#error State and record addresses must be word aligned
#endif

.macro ADDRESS reg, value
    movw \reg, #:lower16:\value
    movt \reg, #:upper16:\value
.endm

.macro SAVE_OBSERVED
    push {r0-r12, lr}
    mrs r12, apsr
    sub sp, sp, #8
    str r12, [sp]
    add r12, sp, #64
    str r12, [sp, #4]
    mov r4, sp
.endm

.macro RESTORE_OBSERVED
    ldr r12, [sp]
    msr APSR_nzcvqg, r12
    add sp, sp, #8
    pop {r0-r12, lr}
.endm

.global native_observer_af
.type native_observer_af, %function
native_observer_af:
    SAVE_OBSERVED
    ADDRESS r5, STATE_AF
    ADDRESS r6, RECORDS_AF
    mov r7, #1
    bl .Lrecord
    RESTORE_OBSERVED
    ldr pc, .Laf_native
.Laf_native:
    .word 0xC022B720
.size native_observer_af, .-native_observer_af

.global native_observer_lens
.type native_observer_lens, %function
native_observer_lens:
    adr lr, .Llens_after
    ldr pc, .Llens_native
.Llens_after:
    SAVE_OBSERVED
    ADDRESS r5, STATE_LENS
    ADDRESS r6, RECORDS_LENS
    mov r7, #2
    bl .Lrecord
    RESTORE_OBSERVED
    ldr lr, .Llens_continue
    ldr pc, .Llens_continue
.Llens_native:
    .word 0xC036D758
.Llens_continue:
    .word 0xC034045C
.size native_observer_lens, .-native_observer_lens

.global native_observer_face
.type native_observer_face, %function
native_observer_face:
    adr lr, .Lface_after
    ldr pc, .Lface_native
.Lface_after:
    SAVE_OBSERVED
    ADDRESS r5, STATE_FACE
    ADDRESS r6, RECORDS_FACE
    mov r7, #3
    bl .Lrecord
    RESTORE_OBSERVED
    ldr lr, .Lface_continue
    ldr pc, .Lface_continue
.Lface_native:
    .word 0xC05A9900
.Lface_continue:
    .word 0xC05AB34C
.size native_observer_face, .-native_observer_face

.global native_observer_aat
.type native_observer_aat, %function
native_observer_aat:
    adr lr, .Laat_after
    ldr pc, .Laat_native
.Laat_after:
    SAVE_OBSERVED
    ADDRESS r5, STATE_AAT
    ADDRESS r6, RECORDS_AAT
    mov r7, #4
    bl .Lrecord
    RESTORE_OBSERVED
    ldr lr, .Laat_continue
    ldr pc, .Laat_continue
.Laat_native:
    .word 0xC05B6CF8
.Laat_continue:
    .word 0xC05AB8CC
.size native_observer_aat, .-native_observer_aat

/* Private recorder: r4=saved frame, r5=state, r6=records, r7=source ID.
 * No SP writes, no calls, no exceptional exit. LR is the internal BL return.
 * The only backward branches are 64 zeroing iterations and 11/23/39/43 copy
 * iterations; there is no acquisition retry or wait for another producer.
 */
.Lrecord:
    ldr r0, [r5]
    ADDRESS r1, 0x314F4E46
    cmp r0, r1
    bne .Lreturn
    ldr r0, [r5, #4]
    cmp r0, #1
    bne .Lreturn
    ldr r0, [r5, #8]
    cmp r0, #256
    bne .Lreturn
    ldr r0, [r5, #12]
    movw r1, #CAPACITY
    cmp r0, r1
    bne .Lreturn
    ldr r0, [r5, #16]
    cmp r0, #0
    beq .Lreturn
    ldr r0, [r5, #20]
    cmp r0, #1
    bne .Lreturn
    ldr r0, [r5, #28]
    cmp r0, #0
    bne .Lreturn

    add r10, r5, #32
    ldrex r0, [r10]
    cmp r0, #0
    bne .Lbusy_conflict
    mov r0, #1
    strex r1, r0, [r10]
    cmp r1, #0
    bne .Lbusy_conflict
    dmb sy

    /* A disabled producer still releases only the lock it acquired. */
    ldr r0, [r5, #20]
    cmp r0, #1
    bne .Lrelease
    ldr r0, [r5, #28]
    cmp r0, #0
    bne .Lrelease
    ldr r8, [r5, #24]
    movw r0, #CAPACITY
    cmp r8, r0
    bhi .Lloss_locked
    beq .Lfull_locked

    cmp r7, #1
    beq .Lsource_af
    cmp r7, #2
    beq .Lsource_lens
    cmp r7, #3
    beq .Lsource_face
    /* Only the four fixed entry points can reach this private label. */
    ADDRESS r11, 0xC37CDFA8
    mov r12, #44
    b .Lsource_ready
.Lsource_af:
    ldr r11, [r4, #12]           /* saved incoming r1, not recorder r1 */
    ldr r0, [r4, #4]             /* original SP */
    movw r1, #0x6F4
    add r0, r0, r1
    cmp r11, r0
    bne .Lloss_locked
    mov r12, #172
    b .Lsource_ready
.Lsource_lens:
    ADDRESS r11, 0xC3476414
    mov r12, #92                 /* fixed F[0..0x5B], pointers remain raw */
    b .Lsource_ready
.Lsource_face:
    ADDRESS r11, 0xC37CCAAC
    mov r12, #156                /* group0; native slot is not identity */
.Lsource_ready:
    add r9, r6, r8, lsl #8
    add r8, r8, #1               /* local sequence 1..CAPACITY */
    mov r0, r9
    mov r1, #0
    mov r2, #64
.Lzero_record:
    str r1, [r0], #4
    subs r2, r2, #1
    bne .Lzero_record

    ADDRESS r0, 0x31524E46
    str r0, [r9]
    mov r0, #1
    str r0, [r9, #4]
    str r7, [r9, #8]
    /* +12 remains zero until after the complete body/trailer and DMB. */
    ldr r0, [r5, #16]
    str r0, [r9, #16]
    ldr r0, [r4, #4]
    str r0, [r9, #20]
    ldr r0, [r4, #8]
    str r0, [r9, #24]
    ldr r0, [r4]
    str r0, [r9, #28]
    str r12, [r9, #32]

    cmp r7, #1
    beq .Lextras_af
    cmp r7, #3
    beq .Lextras_face
    cmp r7, #4
    beq .Lextras_aat
    b .Lcopy_payload             /* lens extras remain zero */
.Lextras_af:
    ADDRESS r0, 0xC329160B
    ldrb r0, [r0]
    str r0, [r9, #36]
    ADDRESS r0, 0xC32914E0
    ldr r0, [r0]
    str r0, [r9, #40]
    ADDRESS r0, 0xC33D51FC
    ldr r0, [r0]
    str r0, [r9, #44]
    b .Lcopy_payload
.Lextras_face:
    ADDRESS r0, 0xC37CCAA0       /* M+4 publication gate */
    ldrb r0, [r0]
    str r0, [r9, #36]
    ADDRESS r0, 0xC37CCAA8       /* M+0x0C parsed found */
    ldrb r0, [r0]
    str r0, [r9, #40]
    ADDRESS r0, 0xC37CCAA9       /* M+0x0D parser state */
    ldrb r0, [r0]
    str r0, [r9, #44]
    b .Lcopy_payload
.Lextras_aat:
    ADDRESS r0, 0xC37CDFD4
    ldrb r0, [r0]
    str r0, [r9, #36]            /* preserve byte bits; no signed verdict */
    ADDRESS r0, 0xC37CDFD5
    ldrb r0, [r0]
    str r0, [r9, #40]
    ADDRESS r0, 0xC2F2D860
    ldrb r0, [r0]
    str r0, [r9, #44]
.Lcopy_payload:
    add r0, r9, #64
    lsr r2, r12, #2
.Lcopy_word:
    ldr r3, [r11], #4
    str r3, [r0], #4
    subs r2, r2, #1
    bne .Lcopy_word

    str r8, [r9, #252]
    dmb sy
    str r8, [r9, #12]
    dmb sy
    str r8, [r5, #24]
    movw r0, #CAPACITY
    cmp r8, r0
    beq .Lfull_locked
    b .Lrelease
.Lfull_locked:
    mov r0, #1
    str r0, [r5, #28]
    b .Lrelease
.Lloss_locked:
    mov r0, #1
    str r0, [r5, #36]
.Lrelease:
    dmb sy
    mov r0, #0
    str r0, [r10]
    dmb sy
.Lreturn:
    bx lr
.Lbusy_conflict:
    clrex
    mov r0, #1
    str r0, [r5, #36]            /* sticky loss; never clear someone else's busy */
    dmb sy
    bx lr
