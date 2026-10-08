/* A sup for test_v3.py: claim every row of its table, and only if all of them
 * were granted write each row's new word.  Any refusal: write nothing and
 * return SL_RELEASE.  The table is appended by the test:
 *
 *     table: count, then { address, stock word, kind, new word } per row
 *     (new word 0: claim only, write nothing)
 *
 * It also leaves the service table's address in `svc_seen`, so the test can
 * check what it was handed.  Position-independent: adr only. */
.syntax unified
.arm
.text

.equ SVC_CLAIM,   8
.equ SVC_PUBLISH, 24

entry:
    push    {r4, r5, r6, r7, r8, lr}    @ even count
    mov     r4, r0                      @ the block
    mov     r5, r1                      @ the service table
    adr     r0, svc_seen
    str     r5, [r0]
    str     r4, [r0, #4]                @ and the block it was entered with
    str     r2, [r0, #8]                @ and its own path (service version 2)

    adr     r6, table
    ldr     r7, [r6], #4                @ rows
    mov     r8, r6
1:  cmp     r7, #0
    beq     2f
    ldr     r1, [r6]                    @ address
    mov     r2, #4
    add     r3, r6, #4                  @ the stock word, in this file
    ldr     r0, [r6, #8]                @ kind: the fifth argument
    sub     sp, sp, #8
    str     r0, [sp]
    mov     r0, r5
    ldr     ip, [r5, #SVC_CLAIM]
    blx     ip
    add     sp, sp, #8
    cmp     r0, #0
    bne     9f                          @ refused: write nothing
    add     r6, r6, #16
    sub     r7, r7, #1
    b       1b

2:  adr     r6, table                   @ all granted: now write
    ldr     r7, [r6], #4
3:  cmp     r7, #0
    beq     4f
    ldr     r0, [r6]
    ldr     r1, [r6, #12]
    cmp     r1, #0
    strne   r1, [r0]
    add     r6, r6, #16
    sub     r7, r7, #1
    b       3b
4:  mov     r0, r5
    ldr     ip, [r5, #SVC_PUBLISH]
    blx     ip
    mov     r0, #0                      @ SL_KEEP
    pop     {r4, r5, r6, r7, r8, pc}

9:  mov     r0, #1                      @ SL_RELEASE
    pop     {r4, r5, r6, r7, r8, pc}

.align 2
svc_seen: .word 0, 0, 0
table:
