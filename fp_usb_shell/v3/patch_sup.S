/* Loader v3: a sup that is a list of firmware patches, optionally followed by
 * an inner entry that does the rest (the USB shell's `spawn`, later OG's).
 *
 *     [entry: this]  [table]  [data]  [inner code]
 *
 * entry(block, svc):
 *   1. claim every row; any refusal -> return SL_RELEASE having written nothing
 *   2. copy every row's new bytes over the firmware, publish
 *   3. if there is an inner entry, call it -- AFTER the writes, because it may
 *      do what cannot be undone (create a task, register with the firmware)
 *   4. return SL_KEEP
 *
 * table: count, then per row { address, bytes, kind, stock_off, new_off },
 *        offsets from `table`.  new_off 0: claim only, the inner entry writes
 *        it (a launcher that arms its own hooks).  bytes 0: not memory --
 *        claim_res(address as the resource id, kind).  The stock bytes come from the reference image
 *        at build time (build_v3.patch_sup), never from the camera.
 * INNER_OFF: the inner entry's offset from `entry`, or 0.  It is called the
 *        way stage2 called entries -- r0 = its own address -- plus r1 = svc.
 *
 * Position-independent: adr only. */
.syntax unified
.arm
.text

.equ SVC_CLAIM,   8
.equ SVC_RES,     12
.equ SVC_PUBLISH, 24

entry:
    push    {r4, r5, r6, r7, r8, r9, r10, lr}   @ even count
    mov     r4, r0                      @ the block
    mov     r5, r1                      @ the service table
    adr     r9, table
    ldr     r7, [r9]                    @ rows
    add     r6, r9, #4
1:  cmp     r7, #0
    beq     2f
    ldr     r1, [r6]                    @ address
    ldr     r2, [r6, #4]                @ bytes
    cmp     r2, #0
    bne     7f
    ldr     r2, [r6, #8]                @ a resource: claim_res(svc, id, kind)
    mov     r0, r5
    ldr     ip, [r5, #SVC_RES]
    blx     ip
    b       8f
7:  ldr     r3, [r6, #12]
    add     r3, r9, r3                  @ stock
    ldr     r0, [r6, #8]                @ kind: the fifth argument
    sub     sp, sp, #8
    str     r0, [sp]
    mov     r0, r5
    ldr     ip, [r5, #SVC_CLAIM]
    blx     ip
    add     sp, sp, #8
8:  cmp     r0, #0
    bne     9f                          @ refused: write nothing
    add     r6, r6, #20
    sub     r7, r7, #1
    b       1b

2:  ldr     r7, [r9]                    @ all granted: now write
    add     r6, r9, #4
3:  cmp     r7, #0
    beq     5f
    ldr     r0, [r6]                    @ destination
    ldr     r2, [r6, #4]
    ldr     r1, [r6, #16]
    cmp     r1, #0                      @ claim only / a resource: nothing to write
    moveq   r2, #0
    add     r1, r9, r1                  @ the new bytes
4:  subs    r2, r2, #4
    ldrge   r3, [r1], #4
    strge   r3, [r0], #4
    bgt     4b
    add     r6, r6, #20
    sub     r7, r7, #1
    b       3b
5:  mov     r0, r5
    ldr     ip, [r5, #SVC_PUBLISH]
    blx     ip

    movw    r0, #:lower16:INNER_OFF
    movt    r0, #:upper16:INNER_OFF
    cmp     r0, #0
    beq     6f
    adr     r1, entry
    add     r0, r1, r0                  @ the inner entry's own address
    mov     r1, r5
    blx     r0
6:  mov     r0, #0                      @ SL_KEEP
    pop     {r4, r5, r6, r7, r8, r9, r10, pc}

9:  mov     r0, #1                      @ SL_RELEASE
    pop     {r4, r5, r6, r7, r8, r9, r10, pc}

.align 2
table:
