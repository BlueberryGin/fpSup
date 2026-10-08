/* A sup for test_v3.py (service version 3): claim one site SHARED_UI, then
 * record what the version-3 services say while this entry runs:
 *
 *     seen +0   [SL_SVC_AT]
 *          +4   the table it was handed
 *          +8   svc->self(svc)
 *          +12  svc->done_at_start, +16 [LOAD_DONE_US]
 *          +20  svc->holder(svc, site, i) for i = 0..3
 *
 * The test appends: site, stock word.  Position-independent: adr only. */
.syntax unified
.arm
.text

.equ SVC_CLAIM,   8
.equ SVC_HOLDER,  32
.equ SVC_SELF,    36
.equ SVC_DONE,    40
.equ SL_SVC_AT,   0xC072F6FC
.equ LOAD_DONE,   0xC072F6F8

entry:
    push    {r4, r5, r6, r7, r8, lr}
    mov     r5, r1
    adr     r4, seen
    ldr     r0, =SL_SVC_AT
    ldr     r0, [r0]
    str     r0, [r4]
    str     r5, [r4, #4]
    adr     r6, site
    mov     r0, #3                      @ SL_SHARED_UI
    sub     sp, sp, #8
    str     r0, [sp]
    mov     r0, r5
    ldr     r1, [r6]
    mov     r2, #4
    add     r3, r6, #4
    ldr     ip, [r5, #SVC_CLAIM]
    blx     ip
    add     sp, sp, #8
    cmp     r0, #0
    movne   r0, #1
    popne   {r4, r5, r6, r7, r8, pc}
    mov     r0, r5
    ldr     ip, [r5, #SVC_SELF]
    blx     ip
    str     r0, [r4, #8]
    ldr     r0, [r5, #SVC_DONE]
    str     r0, [r4, #12]
    ldr     r0, =LOAD_DONE
    ldr     r0, [r0]
    str     r0, [r4, #16]
    mov     r7, #0
1:  mov     r0, r5
    ldr     r1, [r6]
    mov     r2, r7
    ldr     ip, [r5, #SVC_HOLDER]
    blx     ip
    add     r8, r4, #20
    str     r0, [r8, r7, lsl #2]
    add     r7, r7, #1
    cmp     r7, #4
    bne     1b
    mov     r0, #0
    pop     {r4, r5, r6, r7, r8, pc}

.ltorg
.align 2
seen: .word 0, 0, 0, 0, 0, 0, 0, 0, 0
site:
